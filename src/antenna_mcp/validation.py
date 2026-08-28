from __future__ import annotations

import csv
import fnmatch
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workspace import WorkspaceStore


_FREQUENCY_TO_GHZ = {
    "Hz": 1e-9,
    "kHz": 1e-6,
    "MHz": 1e-3,
    "GHz": 1.0,
}


def _frequency_to_ghz(unit: str) -> float:
    return _FREQUENCY_TO_GHZ[unit]


def _frequency_unit_hint(column: str) -> str | None:
    aliases = {"hz": "Hz", "khz": "kHz", "mhz": "MHz", "ghz": "GHz"}
    hints = {
        aliases[token.lower()]
        for token in re.findall(
            r"(?i)(?<![A-Za-z])(GHz|MHz|kHz|Hz)(?![A-Za-z])", column
        )
    }
    if len(hints) > 1:
        raise ValueError(f"frequency_column contains conflicting unit hints: {column!r}")
    return next(iter(hints), None)


class BenchmarkSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    source_type: Literal["official_example", "tutorial", "paper", "measurement"]
    accessed_on: str | None = None
    notes: list[str] = Field(default_factory=list)


class _S11WindowTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    window_ghz: tuple[float, float]
    threshold_db: float | None = None
    minimum_points: int = Field(default=3, ge=3)

    @model_validator(mode="after")
    def valid_window(self) -> "_S11WindowTarget":
        low, high = self.window_ghz
        if not math.isfinite(low) or not math.isfinite(high) or low >= high:
            raise ValueError("window_ghz must contain two finite, increasing frequencies")
        if self.threshold_db is not None and not math.isfinite(self.threshold_db):
            raise ValueError("threshold_db must be finite")
        return self


class S11ResonanceTarget(_S11WindowTarget):
    """Compare one physical resonance inside a bounded frequency window."""

    kind: Literal["resonance"] = "resonance"
    resonance_relative_error_max: float | None = Field(default=None, ge=0.0)
    band_edge_relative_error_max: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def finite_limits(self) -> "S11ResonanceTarget":
        for name in ("resonance_relative_error_max", "band_edge_relative_error_max"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        return self


class S11BandTarget(_S11WindowTarget):
    """Require an entire passband or rejection/notch window to meet a threshold."""

    kind: Literal["passband", "stopband", "notch"]


S11Target = Annotated[S11ResonanceTarget | S11BandTarget, Field(discriminator="kind")]


class S11Criteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frequency_column: str = "frequency_ghz"
    frequency_unit: Literal["Hz", "kHz", "MHz", "GHz"] = "GHz"
    value_column: str = "s11_db"
    value_unit: Literal["dB"] = "dB"
    threshold_db: float = -10.0
    minimum_overlap_points: int = Field(default=20, ge=3)
    minimum_reference_coverage_fraction: float = Field(default=0.99, ge=0.0, le=1.0)
    resonance_relative_error_max: float = Field(default=0.01, ge=0.0)
    bandwidth_relative_error_max: float = Field(default=0.05, ge=0.0)
    curve_rmse_db_max: float = Field(default=1.0, ge=0.0)
    required: bool = True
    targets: list[S11Target] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_criteria(self) -> "S11Criteria":
        if not self.frequency_column.strip() or not self.value_column.strip():
            raise ValueError("S11 CSV column names cannot be empty")
        if self.frequency_column == self.value_column:
            raise ValueError("S11 frequency and value columns must be different")
        hinted_unit = _frequency_unit_hint(self.frequency_column)
        if hinted_unit is not None and hinted_unit != self.frequency_unit:
            raise ValueError(
                f"frequency_column implies {hinted_unit}, but frequency_unit is "
                f"{self.frequency_unit}"
            )
        for name in (
            "threshold_db",
            "minimum_reference_coverage_fraction",
            "resonance_relative_error_max",
            "bandwidth_relative_error_max",
            "curve_rmse_db_max",
        ):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        names = [target.name for target in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("S11 target names must be unique")
        return self


class ValidationBenchmark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str
    source: BenchmarkSource
    generation_evidence: dict[str, Any] = Field(default_factory=dict)
    reference: dict[str, Any]
    default_absolute_tolerance: float = Field(default=0.0, ge=0.0)
    tolerance_by_path: dict[str, float] = Field(default_factory=dict)
    s11: S11Criteria | None = None

    @model_validator(mode="after")
    def valid_tolerances(self) -> "ValidationBenchmark":
        if not self.reference:
            raise ValueError("reference model cannot be empty")
        if not math.isfinite(self.default_absolute_tolerance):
            raise ValueError("default_absolute_tolerance must be finite")
        if any(value < 0 or not math.isfinite(value) for value in self.tolerance_by_path.values()):
            raise ValueError("all path tolerances must be finite and non-negative")
        return self


class CandidateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    benchmark_id: str
    provenance: dict[str, Any] = Field(default_factory=dict)
    model: dict[str, Any]


class ValidationService:
    """Compare generated antenna contracts and S11 curves with a frozen benchmark."""

    def __init__(self, store: WorkspaceStore | None = None) -> None:
        self.store = store

    def validate_manifest(
        self,
        benchmark_path: str | Path,
        candidate_path: str | Path,
        *,
        reference_s11: str | Path | None = None,
        candidate_s11: str | Path | None = None,
        report_path: str | Path | None = None,
        contract_only: bool = False,
    ) -> dict[str, Any]:
        benchmark_file = _required_json_file(benchmark_path, "benchmark")
        candidate_file = _required_json_file(candidate_path, "candidate")
        benchmark = ValidationBenchmark.model_validate_json(benchmark_file.read_text("utf-8"))
        candidate = CandidateManifest.model_validate_json(candidate_file.read_text("utf-8"))
        if candidate.benchmark_id != benchmark.benchmark_id:
            raise ValueError(
                f"candidate benchmark_id {candidate.benchmark_id!r} does not match "
                f"{benchmark.benchmark_id!r}"
            )
        report = self._validate(
            benchmark,
            candidate.model,
            candidate.provenance,
            benchmark_file=benchmark_file,
            candidate_file=candidate_file,
            reference_s11=reference_s11,
            candidate_s11=candidate_s11,
            contract_only=contract_only,
        )
        destination = Path(report_path).expanduser().resolve() if report_path else None
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            report["report"] = str(destination)
        return report

    def validate_job(
        self,
        benchmark_path: str | Path,
        job_id: str,
        *,
        reference_s11: str | Path | None = None,
        candidate_s11: str | Path | None = None,
        contract_only: bool = False,
    ) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError("a WorkspaceStore is required for job validation")
        benchmark_file = _required_json_file(benchmark_path, "benchmark")
        benchmark = ValidationBenchmark.model_validate_json(benchmark_file.read_text("utf-8"))
        state = self.store.load_state(job_id)
        revisions = [
            int(match.group(1))
            for key in state.artifacts
            if (match := re.fullmatch(r"validation_report_v(\d{3})", key))
        ]
        revision = max(revisions, default=0) + 1
        revision_tag = f"v{revision:03d}"
        candidate_model = self.candidate_from_job(job_id)
        candidate_payload = {
            "schema_version": "1.0",
            "benchmark_id": benchmark.benchmark_id,
            "provenance": {
                "kind": "modeling_job",
                "job_id": job_id,
                "validation_revision": revision,
            },
            "model": candidate_model,
        }
        candidate_file = self.store.write_artifact(
            job_id,
            f"validation_candidate_{revision_tag}.json",
            json.dumps(candidate_payload, ensure_ascii=False, indent=2) + "\n",
        )
        latest_candidate_file = self.store.write_artifact(
            job_id,
            "validation_candidate.json",
            json.dumps(candidate_payload, ensure_ascii=False, indent=2) + "\n",
        )
        report = self._validate(
            benchmark,
            candidate_model,
            candidate_payload["provenance"],
            benchmark_file=benchmark_file,
            candidate_file=candidate_file,
            reference_s11=reference_s11,
            candidate_s11=candidate_s11,
            contract_only=contract_only,
        )
        report["revision"] = revision
        report["revision_tag"] = revision_tag
        report_file = self.store.write_artifact(
            job_id,
            f"validation_report_{revision_tag}.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        latest_report_file = self.store.write_artifact(
            job_id,
            "validation_report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts[f"validation_candidate_{revision_tag}"] = str(candidate_file)
        state.artifacts[f"validation_report_{revision_tag}"] = str(report_file)
        state.artifacts["validation_candidate"] = str(latest_candidate_file)
        state.artifacts["validation_report"] = str(latest_report_file)
        self.store.save_state(state)
        report["report"] = str(report_file)
        report["latest_report"] = str(latest_report_file)
        return report

    def candidate_from_job(self, job_id: str) -> dict[str, Any]:
        if self.store is None:
            raise RuntimeError("a WorkspaceStore is required for job validation")
        state = self.store.load_state(job_id)
        if state.kind != "modeling":
            raise ValueError("validation requires a modeling job")
        job_dir = self.store.job_dir(job_id).resolve()

        parameters_payload = _load_job_artifact(state.artifacts, job_dir, "parameters")
        materials_payload = _load_job_artifact(state.artifacts, job_dir, "materials")
        solids_payload = _load_job_artifact(state.artifacts, job_dir, "solids")
        dimensions_payload = _load_job_artifact(state.artifacts, job_dir, "dimensions")

        parameters = _named_mapping(parameters_payload.get("parameters"), "parameters")
        materials = _named_mapping(materials_payload.get("materials"), "materials")
        objects = _named_mapping(solids_payload.get("solids"), "solids")
        dimension_items, dimension_source = _job_dimensions(dimensions_payload)
        dimensions = _named_mapping(dimension_items, "dimensions")
        for name, dimension in dimensions.items():
            if name not in objects:
                objects[name] = {"name": name}
            objects[name]["dimensions"] = {
                key: value for key, value in dimension.items() if key != "name"
            }

        model: dict[str, Any] = {
            "parameters": parameters,
            "materials": materials,
            "objects": objects,
        }
        audit: dict[str, Any] = {
            "schema_version": "1.0",
            "job_id": job_id,
            "job_status": state.status,
            "current_stage": state.current_stage,
            "sources": {
                "parameters": "parameters.parameters",
                "materials": "materials.materials",
                "objects": "solids.solids",
                "dimensions": dimension_source,
            },
            "fallbacks": [],
            "missing_artifacts": [],
            "notes": [
                "Material definitions remain keyed by their generated names; role-keyed "
                "benchmark materials are not inferred from solid assignments.",
                "A missing candidate field is omitted so the contract comparator reports it; "
                "the validator never fills missing evidence with benchmark values.",
            ],
        }

        if state.artifacts.get("geometry_manifest"):
            geometry_payload = _load_job_artifact(
                state.artifacts, job_dir, "geometry_manifest"
            )
            operations = _optional_array_field(
                geometry_payload, "operations", "geometry_manifest"
            )
            audit["sources"]["operations"] = "geometry_manifest.operations"
        else:
            source_payload = _load_job_artifact(
                state.artifacts, job_dir, "source_analysis"
            )
            operations = _optional_array_field(
                source_payload, "operations", "source_analysis"
            )
            audit["sources"]["operations"] = "source_analysis.operations"
            audit["missing_artifacts"].append("geometry_manifest")
            audit["fallbacks"].append(
                {
                    "field": "operations",
                    "reason": "geometry_manifest artifact is absent",
                    "source": "source_analysis.operations",
                    "lossless": True,
                }
            )
        if operations is not None:
            model["operations"] = operations
        else:
            audit["notes"].append(
                f"{audit['sources']['operations']} is absent; model.operations was not created."
            )

        simulation_raw = state.artifacts.get("simulation_spec")
        if simulation_raw:
            model["solver"] = _load_job_artifact(
                state.artifacts, job_dir, "simulation_spec"
            )
            audit["sources"]["solver"] = "simulation_spec"
        else:
            audit["missing_artifacts"].append("simulation_spec")
            audit["notes"].append(
                "No solver field was created because simulation_spec is absent."
            )
        model["_assembly_audit"] = audit
        return model

    @staticmethod
    def _validate(
        benchmark: ValidationBenchmark,
        candidate_model: dict[str, Any],
        provenance: dict[str, Any],
        *,
        benchmark_file: Path,
        candidate_file: Path,
        reference_s11: str | Path | None,
        candidate_s11: str | Path | None,
        contract_only: bool,
    ) -> dict[str, Any]:
        comparator = _ContractComparator(benchmark)
        checks = comparator.compare(benchmark.reference, candidate_model)
        category_summary = _category_summary(checks)
        contract_passed = all(check["passed"] for check in checks)

        curve_report = _curve_report(
            benchmark.s11,
            reference_s11,
            candidate_s11,
            contract_only=contract_only,
        )
        simulation_passed = curve_report.get("passed")
        if not contract_passed or simulation_passed is False:
            status = "failed"
        elif curve_report["status"] == "incomplete":
            status = "incomplete"
        else:
            status = "passed"

        validation_level = (
            "electromagnetic"
            if curve_report["status"] in {"passed", "failed"}
            else "contract"
        )
        return {
            "schema_version": "1.0",
            "benchmark_id": benchmark.benchmark_id,
            "title": benchmark.title,
            "status": status,
            "quality_gate_passed": status == "passed",
            "validation_level": validation_level,
            "claims": {
                "geometry_and_solver_contract_validated": contract_passed,
                "electromagnetic_results_validated": simulation_passed is True,
            },
            "source": benchmark.source.model_dump(mode="json"),
            "provenance": provenance,
            "inputs": {
                "benchmark": _file_record(benchmark_file),
                "candidate": _file_record(candidate_file),
                "reference_s11": _optional_file_record(reference_s11),
                "candidate_s11": _optional_file_record(candidate_s11),
            },
            "contract": {
                "passed": contract_passed,
                "check_count": len(checks),
                "passed_count": sum(check["passed"] for check in checks),
                "failed_count": sum(not check["passed"] for check in checks),
                "categories": category_summary,
                "checks": checks,
            },
            "s11": curve_report,
        }


class _ContractComparator:
    def __init__(self, benchmark: ValidationBenchmark) -> None:
        self.default_tolerance = benchmark.default_absolute_tolerance
        self.path_tolerances = benchmark.tolerance_by_path
        self.checks: list[dict[str, Any]] = []

    def compare(self, expected: Any, actual: Any) -> list[dict[str, Any]]:
        self._compare(expected, actual, "")
        return self.checks

    def _compare(self, expected: Any, actual: Any, path: str) -> None:
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                self._record(path, expected, actual, False, "expected an object")
                return
            for key, expected_value in expected.items():
                child = f"{path}.{key}" if path else str(key)
                if key not in actual:
                    self._record(child, expected_value, None, False, "missing required field")
                else:
                    self._compare(expected_value, actual[key], child)
            if path in {"parameters", "materials", "objects"}:
                extras = sorted(set(actual) - set(expected))
                if extras:
                    self._record(
                        f"{path}.__extra__",
                        [],
                        extras,
                        False,
                        "unexpected collection items",
                    )
            return

        if isinstance(expected, list):
            if not isinstance(actual, list):
                self._record(path, expected, actual, False, "expected an array")
                return
            identity = _identity_key(expected)
            if identity:
                expected_map = {str(item[identity]): item for item in expected}
                actual_map = {
                    str(item[identity]): item
                    for item in actual
                    if isinstance(item, dict) and identity in item
                }
                self._record(
                    f"{path}.__count__",
                    len(expected),
                    len(actual),
                    len(expected) == len(actual),
                    "collection size",
                )
                for key, expected_item in expected_map.items():
                    child = f"{path}[{identity}={key}]"
                    if key not in actual_map:
                        self._record(child, expected_item, None, False, "missing required item")
                    else:
                        self._compare(expected_item, actual_map[key], child)
                extras = sorted(set(actual_map) - set(expected_map))
                if extras:
                    self._record(
                        f"{path}.__extra__",
                        [],
                        extras,
                        False,
                        "unexpected collection items",
                    )
                return
            self._record(
                f"{path}.__count__",
                len(expected),
                len(actual),
                len(expected) == len(actual),
                "array size",
            )
            for index, expected_value in enumerate(expected):
                child = f"{path}[{index}]"
                if index >= len(actual):
                    self._record(child, expected_value, None, False, "missing array item")
                else:
                    self._compare(expected_value, actual[index], child)
            return

        if _is_number(expected):
            if not _is_number(actual) or not math.isfinite(float(actual)):
                self._record(path, expected, actual, False, "expected a finite number")
                return
            tolerance = self._tolerance(path)
            difference = abs(float(expected) - float(actual))
            self._record(
                path,
                expected,
                actual,
                difference <= tolerance,
                "numeric comparison",
                tolerance=tolerance,
                absolute_error=difference,
            )
            return

        passed = expected == actual
        self._record(path, expected, actual, passed, "exact comparison")

    def _tolerance(self, path: str) -> float:
        matches = [
            (pattern, tolerance)
            for pattern, tolerance in self.path_tolerances.items()
            if fnmatch.fnmatchcase(path, pattern)
        ]
        if not matches:
            return self.default_tolerance
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        return matches[0][1]

    def _record(
        self,
        path: str,
        expected: Any,
        actual: Any,
        passed: bool,
        reason: str,
        **details: Any,
    ) -> None:
        self.checks.append(
            {
                "path": path or "$",
                "category": _category(path),
                "passed": bool(passed),
                "expected": expected,
                "actual": actual,
                "reason": reason,
                **details,
            }
        )


def _curve_report(
    criteria: S11Criteria | None,
    reference_path: str | Path | None,
    candidate_path: str | Path | None,
    *,
    contract_only: bool,
) -> dict[str, Any]:
    if criteria is None:
        return {"status": "not_applicable", "passed": None, "checks": []}
    if contract_only:
        return {
            "status": "skipped_contract_only",
            "passed": None,
            "required_for_full_validation": criteria.required,
            "checks": [],
        }
    if reference_path is None or candidate_path is None:
        missing = []
        if reference_path is None:
            missing.append("reference_s11")
        if candidate_path is None:
            missing.append("candidate_s11")
        partially_supplied = (reference_path is None) != (candidate_path is None)
        return {
            "status": (
                "incomplete"
                if criteria.required or partially_supplied
                else "optional_not_run"
            ),
            "passed": None,
            "required": criteria.required,
            "missing": missing,
            "checks": [],
        }

    reference_file = _required_csv_file(reference_path, "reference S11")
    candidate_file = _required_csv_file(candidate_path, "candidate S11")
    reference_frequency, reference_values = _read_curve(reference_file, criteria)
    candidate_frequency, candidate_values = _read_curve(candidate_file, criteria)

    overlap_low = max(float(reference_frequency[0]), float(candidate_frequency[0]))
    overlap_high = min(float(reference_frequency[-1]), float(candidate_frequency[-1]))
    reference_overlap_mask = (reference_frequency >= overlap_low) & (
        reference_frequency <= overlap_high
    )
    candidate_overlap_mask = (candidate_frequency >= overlap_low) & (
        candidate_frequency <= overlap_high
    )
    reference_overlap_frequency = reference_frequency[reference_overlap_mask]
    candidate_overlap_frequency = candidate_frequency[candidate_overlap_mask]
    overlap_point_counts = {
        "reference": len(reference_overlap_frequency),
        "candidate": len(candidate_overlap_frequency),
    }
    checks: list[dict[str, Any]] = []
    effective_overlap_points = min(overlap_point_counts.values())
    enough_points = effective_overlap_points >= criteria.minimum_overlap_points
    reference_span = float(reference_frequency[-1] - reference_frequency[0])
    overlap_span = max(0.0, overlap_high - overlap_low)
    reference_overlap_fraction = min(1.0, overlap_span / reference_span)
    enough_reference_coverage = (
        reference_overlap_fraction >= criteria.minimum_reference_coverage_fraction
    )
    checks.extend(
        [
            _metric_check(
                "s11.overlap_points",
                effective_overlap_points,
                criteria.minimum_overlap_points,
                enough_points,
                "minimum",
            ),
            _metric_check(
                "s11.reference_overlap_fraction",
                reference_overlap_fraction,
                criteria.minimum_reference_coverage_fraction,
                enough_reference_coverage,
                "minimum",
            ),
        ]
    )
    if not enough_points or not enough_reference_coverage:
        return {
            "status": "failed",
            "passed": False,
            "overlap_ghz": [overlap_low, overlap_high],
            "overlap_point_counts": overlap_point_counts,
            "checks": checks,
        }

    comparison_point_count = max(
        len(reference_overlap_frequency),
        len(candidate_overlap_frequency),
        criteria.minimum_overlap_points,
    )
    comparison_frequency = np.linspace(
        overlap_low, overlap_high, comparison_point_count, dtype=float
    )
    reference_overlap = np.interp(
        comparison_frequency, reference_frequency, reference_values
    )
    candidate_overlap = np.interp(
        comparison_frequency, candidate_frequency, candidate_values
    )
    rmse = float(np.sqrt(np.mean(np.square(reference_overlap - candidate_overlap))))
    if criteria.targets:
        checks.append(
            _metric_check(
                "s11.curve_rmse_db",
                rmse,
                criteria.curve_rmse_db_max,
                rmse <= criteria.curve_rmse_db_max,
                "maximum",
            )
        )
        target_reports = [
            _target_report(
                target,
                criteria,
                reference_frequency,
                reference_values,
                candidate_frequency,
                candidate_values,
            )
            for target in criteria.targets
        ]
        for target_report in target_reports:
            checks.extend(target_report["checks"])
        passed = all(check["passed"] for check in checks)
        return {
            "status": "passed" if passed else "failed",
            "passed": passed,
            "mode": "targets",
            "threshold_db": criteria.threshold_db,
            "overlap_ghz": [overlap_low, overlap_high],
            "overlap_point_counts": overlap_point_counts,
            "rmse_comparison_points": len(comparison_frequency),
            "curve_rmse_db": rmse,
            "targets": target_reports,
            "checks": checks,
        }

    reference_minimum = _local_minimum_in_window(
        reference_frequency,
        reference_values,
        (float(reference_frequency[0]), float(reference_frequency[-1])),
    )
    candidate_minimum = _local_minimum_in_window(
        candidate_frequency,
        candidate_values,
        (float(candidate_frequency[0]), float(candidate_frequency[-1])),
    )
    reference_resonance = reference_minimum[1] if reference_minimum else None
    candidate_resonance = candidate_minimum[1] if candidate_minimum else None
    if reference_resonance is None or candidate_resonance is None:
        resonance_error = None
    else:
        resonance_error = abs(candidate_resonance - reference_resonance) / max(
            abs(reference_resonance), np.finfo(float).eps
        )
    reference_bandwidth = (
        _bandwidth_at_index(
            reference_frequency,
            reference_values,
            criteria.threshold_db,
            reference_minimum[0],
        )
        if reference_minimum is not None
        else None
    )
    candidate_bandwidth = (
        _bandwidth_at_index(
            candidate_frequency,
            candidate_values,
            criteria.threshold_db,
            candidate_minimum[0],
        )
        if candidate_minimum is not None
        else None
    )
    if reference_bandwidth is None or candidate_bandwidth is None:
        bandwidth_error = None
        bandwidth_passed = False
    else:
        reference_width = reference_bandwidth[1] - reference_bandwidth[0]
        candidate_width = candidate_bandwidth[1] - candidate_bandwidth[0]
        bandwidth_error = abs(candidate_width - reference_width) / max(
            abs(reference_width), np.finfo(float).eps
        )
        bandwidth_passed = bandwidth_error <= criteria.bandwidth_relative_error_max

    checks.extend(
        [
            _metric_check(
                "s11.resonance_relative_error",
                resonance_error,
                criteria.resonance_relative_error_max,
                resonance_error is not None
                and resonance_error <= criteria.resonance_relative_error_max,
                "maximum",
            ),
            _metric_check(
                "s11.bandwidth_relative_error",
                bandwidth_error,
                criteria.bandwidth_relative_error_max,
                bandwidth_passed,
                "maximum",
            ),
            _metric_check(
                "s11.curve_rmse_db",
                rmse,
                criteria.curve_rmse_db_max,
                rmse <= criteria.curve_rmse_db_max,
                "maximum",
            ),
        ]
    )
    passed = all(check["passed"] for check in checks)
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "threshold_db": criteria.threshold_db,
        "overlap_ghz": [overlap_low, overlap_high],
        "overlap_point_counts": overlap_point_counts,
        "rmse_comparison_points": len(comparison_frequency),
        "reference": {
            "resonant_frequency_ghz": reference_resonance,
            "bandwidth_ghz": list(reference_bandwidth) if reference_bandwidth else None,
        },
        "candidate": {
            "resonant_frequency_ghz": candidate_resonance,
            "bandwidth_ghz": list(candidate_bandwidth) if candidate_bandwidth else None,
        },
        "checks": checks,
    }


def _target_report(
    target: S11Target,
    criteria: S11Criteria,
    reference_frequency: np.ndarray,
    reference_values: np.ndarray,
    candidate_frequency: np.ndarray,
    candidate_values: np.ndarray,
) -> dict[str, Any]:
    threshold = criteria.threshold_db if target.threshold_db is None else target.threshold_db
    window = target.window_ghz
    prefix = f"s11.targets[{target.name}]"
    reference_covered = _window_is_covered(reference_frequency, window)
    candidate_covered = _window_is_covered(candidate_frequency, window)
    reference_point_count = _window_point_count(reference_frequency, window)
    candidate_point_count = _window_point_count(candidate_frequency, window)
    checks = [
        _condition_check(
            f"{prefix}.reference_window_covered",
            [float(reference_frequency[0]), float(reference_frequency[-1])],
            list(window),
            reference_covered,
            "contains",
        ),
        _condition_check(
            f"{prefix}.candidate_window_covered",
            [float(candidate_frequency[0]), float(candidate_frequency[-1])],
            list(window),
            candidate_covered,
            "contains",
        ),
        _metric_check(
            f"{prefix}.reference_window_points",
            reference_point_count,
            target.minimum_points,
            reference_point_count >= target.minimum_points,
            "minimum",
        ),
        _metric_check(
            f"{prefix}.candidate_window_points",
            candidate_point_count,
            target.minimum_points,
            candidate_point_count >= target.minimum_points,
            "minimum",
        ),
    ]

    reference_usable = (
        reference_covered and reference_point_count >= target.minimum_points
    )
    candidate_usable = (
        candidate_covered and candidate_point_count >= target.minimum_points
    )
    if isinstance(target, S11ResonanceTarget):
        return _resonance_target_report(
            target,
            criteria,
            threshold,
            prefix,
            checks,
            reference_usable,
            candidate_usable,
            reference_frequency,
            reference_values,
            candidate_frequency,
            candidate_values,
        )
    return _band_target_report(
        target,
        threshold,
        prefix,
        checks,
        reference_usable,
        candidate_usable,
        reference_frequency,
        reference_values,
        candidate_frequency,
        candidate_values,
    )


def _resonance_target_report(
    target: S11ResonanceTarget,
    criteria: S11Criteria,
    threshold: float,
    prefix: str,
    checks: list[dict[str, Any]],
    reference_usable: bool,
    candidate_usable: bool,
    reference_frequency: np.ndarray,
    reference_values: np.ndarray,
    candidate_frequency: np.ndarray,
    candidate_values: np.ndarray,
) -> dict[str, Any]:
    reference_minimum = (
        _local_minimum_in_window(reference_frequency, reference_values, target.window_ghz)
        if reference_usable
        else None
    )
    candidate_minimum = (
        _local_minimum_in_window(candidate_frequency, candidate_values, target.window_ghz)
        if candidate_usable
        else None
    )
    checks.extend(
        [
            _condition_check(
                f"{prefix}.reference_local_minimum",
                _minimum_record(reference_minimum),
                "interior local minimum",
                reference_minimum is not None,
                "exists",
            ),
            _condition_check(
                f"{prefix}.candidate_local_minimum",
                _minimum_record(candidate_minimum),
                "interior local minimum",
                candidate_minimum is not None,
                "exists",
            ),
        ]
    )

    reference_below = reference_minimum is not None and reference_minimum[2] <= threshold
    candidate_below = candidate_minimum is not None and candidate_minimum[2] <= threshold
    checks.extend(
        [
            _metric_check(
                f"{prefix}.reference_minimum_s11_db",
                reference_minimum[2] if reference_minimum else None,
                threshold,
                reference_below,
                "maximum",
            ),
            _metric_check(
                f"{prefix}.candidate_minimum_s11_db",
                candidate_minimum[2] if candidate_minimum else None,
                threshold,
                candidate_below,
                "maximum",
            ),
        ]
    )

    resonance_limit = (
        criteria.resonance_relative_error_max
        if target.resonance_relative_error_max is None
        else target.resonance_relative_error_max
    )
    if reference_minimum is None or candidate_minimum is None:
        resonance_error = None
    else:
        resonance_error = abs(candidate_minimum[1] - reference_minimum[1]) / max(
            abs(reference_minimum[1]), np.finfo(float).eps
        )
    checks.append(
        _metric_check(
            f"{prefix}.resonance_relative_error",
            resonance_error,
            resonance_limit,
            resonance_error is not None and resonance_error <= resonance_limit,
            "maximum",
        )
    )

    reference_bandwidth = (
        _bandwidth_at_index(
            reference_frequency, reference_values, threshold, reference_minimum[0]
        )
        if reference_below and reference_minimum is not None
        else None
    )
    candidate_bandwidth = (
        _bandwidth_at_index(
            candidate_frequency, candidate_values, threshold, candidate_minimum[0]
        )
        if candidate_below and candidate_minimum is not None
        else None
    )
    checks.extend(
        [
            _condition_check(
                f"{prefix}.reference_band_edges",
                list(reference_bandwidth) if reference_bandwidth else None,
                "two threshold crossings inside the sweep",
                reference_bandwidth is not None,
                "available",
            ),
            _condition_check(
                f"{prefix}.candidate_band_edges",
                list(candidate_bandwidth) if candidate_bandwidth else None,
                "two threshold crossings inside the sweep",
                candidate_bandwidth is not None,
                "available",
            ),
        ]
    )

    edge_limit = (
        criteria.bandwidth_relative_error_max
        if target.band_edge_relative_error_max is None
        else target.band_edge_relative_error_max
    )
    lower_edge_error: float | None = None
    upper_edge_error: float | None = None
    if reference_bandwidth is not None and candidate_bandwidth is not None:
        reference_width = reference_bandwidth[1] - reference_bandwidth[0]
        denominator = max(abs(reference_width), np.finfo(float).eps)
        lower_edge_error = abs(candidate_bandwidth[0] - reference_bandwidth[0]) / denominator
        upper_edge_error = abs(candidate_bandwidth[1] - reference_bandwidth[1]) / denominator
    checks.extend(
        [
            _metric_check(
                f"{prefix}.lower_band_edge_relative_error",
                lower_edge_error,
                edge_limit,
                lower_edge_error is not None and lower_edge_error <= edge_limit,
                "maximum",
            ),
            _metric_check(
                f"{prefix}.upper_band_edge_relative_error",
                upper_edge_error,
                edge_limit,
                upper_edge_error is not None and upper_edge_error <= edge_limit,
                "maximum",
            ),
        ]
    )
    passed = all(check["passed"] for check in checks)
    return {
        "name": target.name,
        "kind": target.kind,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "window_ghz": list(target.window_ghz),
        "threshold_db": threshold,
        "reference": {
            "local_minimum": _minimum_record(reference_minimum),
            "band_edges_ghz": list(reference_bandwidth) if reference_bandwidth else None,
        },
        "candidate": {
            "local_minimum": _minimum_record(candidate_minimum),
            "band_edges_ghz": list(candidate_bandwidth) if candidate_bandwidth else None,
        },
        "checks": checks,
    }


def _band_target_report(
    target: S11BandTarget,
    threshold: float,
    prefix: str,
    checks: list[dict[str, Any]],
    reference_usable: bool,
    candidate_usable: bool,
    reference_frequency: np.ndarray,
    reference_values: np.ndarray,
    candidate_frequency: np.ndarray,
    candidate_values: np.ndarray,
) -> dict[str, Any]:
    reference_window = (
        _window_values(reference_frequency, reference_values, target.window_ghz)
        if reference_usable
        else None
    )
    candidate_window = (
        _window_values(candidate_frequency, candidate_values, target.window_ghz)
        if candidate_usable
        else None
    )
    if target.kind == "passband":
        criterion = "maximum"
        reference_extreme = (
            float(np.max(reference_window)) if reference_window is not None else None
        )
        candidate_extreme = (
            float(np.max(candidate_window)) if candidate_window is not None else None
        )
        reference_met = reference_extreme is not None and reference_extreme <= threshold
        candidate_met = candidate_extreme is not None and candidate_extreme <= threshold
        metric_name = "maximum_s11_db"
    else:
        criterion = "minimum"
        reference_extreme = (
            float(np.min(reference_window)) if reference_window is not None else None
        )
        candidate_extreme = (
            float(np.min(candidate_window)) if candidate_window is not None else None
        )
        reference_met = reference_extreme is not None and reference_extreme >= threshold
        candidate_met = candidate_extreme is not None and candidate_extreme >= threshold
        metric_name = "minimum_s11_db"
    checks.extend(
        [
            _metric_check(
                f"{prefix}.reference_{metric_name}",
                reference_extreme,
                threshold,
                reference_met,
                criterion,
            ),
            _metric_check(
                f"{prefix}.candidate_{metric_name}",
                candidate_extreme,
                threshold,
                candidate_met,
                criterion,
            ),
        ]
    )
    passed = all(check["passed"] for check in checks)
    return {
        "name": target.name,
        "kind": target.kind,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "window_ghz": list(target.window_ghz),
        "threshold_db": threshold,
        "reference": {metric_name: reference_extreme},
        "candidate": {metric_name: candidate_extreme},
        "checks": checks,
    }


def _window_is_covered(frequencies: np.ndarray, window: tuple[float, float]) -> bool:
    return bool(frequencies[0] <= window[0] and frequencies[-1] >= window[1])


def _window_point_count(frequencies: np.ndarray, window: tuple[float, float]) -> int:
    return int(np.count_nonzero((frequencies >= window[0]) & (frequencies <= window[1])))


def _window_values(
    frequencies: np.ndarray,
    values: np.ndarray,
    window: tuple[float, float],
) -> np.ndarray:
    low, high = window
    interior = (frequencies > low) & (frequencies < high)
    return np.concatenate(
        (
            np.asarray([np.interp(low, frequencies, values)]),
            values[interior],
            np.asarray([np.interp(high, frequencies, values)]),
        )
    )


def _local_minimum_in_window(
    frequencies: np.ndarray,
    values: np.ndarray,
    window: tuple[float, float],
) -> tuple[int, float, float] | None:
    low, high = window
    candidates = [
        index
        for index in range(1, len(values) - 1)
        if low < frequencies[index] < high
        and values[index] <= values[index - 1]
        and values[index] <= values[index + 1]
        and (values[index] < values[index - 1] or values[index] < values[index + 1])
    ]
    if not candidates:
        return None
    index = min(candidates, key=lambda item: float(values[item]))
    return index, float(frequencies[index]), float(values[index])


def _minimum_record(minimum: tuple[int, float, float] | None) -> dict[str, float] | None:
    if minimum is None:
        return None
    return {"frequency_ghz": minimum[1], "s11_db": minimum[2]}


def _bandwidth_at_index(
    frequencies: np.ndarray,
    values: np.ndarray,
    threshold: float,
    resonance_index: int,
) -> tuple[float, float] | None:
    below = values <= threshold
    if not below[resonance_index]:
        return None
    left = resonance_index
    right = resonance_index
    while left > 0 and below[left - 1]:
        left -= 1
    while right < len(values) - 1 and below[right + 1]:
        right += 1
    if left == 0 or right == len(values) - 1:
        return None
    low = _threshold_crossing(
        float(frequencies[left - 1]),
        float(values[left - 1]),
        float(frequencies[left]),
        float(values[left]),
        threshold,
    )
    high = _threshold_crossing(
        float(frequencies[right]),
        float(values[right]),
        float(frequencies[right + 1]),
        float(values[right + 1]),
        threshold,
    )
    if high <= low:
        return None
    return low, high


def _read_curve(path: Path, criteria: S11Criteria) -> tuple[np.ndarray, np.ndarray]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"S11 CSV has no header: {path}")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError(f"S11 CSV contains duplicate column names: {path}")
        required = {criteria.frequency_column, criteria.value_column}
        if not required.issubset(reader.fieldnames):
            raise ValueError(
                f"S11 CSV must contain columns {sorted(required)}; got {reader.fieldnames}"
            )
        points: list[tuple[float, float]] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                frequency = float(row[criteria.frequency_column])
                value = float(row[criteria.value_column])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid S11 number at {path}:{line_number}") from exc
            if not math.isfinite(frequency) or not math.isfinite(value):
                raise ValueError(f"non-finite S11 number at {path}:{line_number}")
            points.append((frequency * _frequency_to_ghz(criteria.frequency_unit), value))
    if len(points) < 3:
        raise ValueError(f"S11 CSV requires at least three data rows: {path}")
    frequencies = np.asarray([point[0] for point in points], dtype=float)
    values = np.asarray([point[1] for point in points], dtype=float)
    if np.any(np.diff(frequencies) <= 0):
        raise ValueError(f"S11 frequencies must be finite and strictly increasing: {path}")
    return frequencies, values


def _threshold_crossing(x1: float, y1: float, x2: float, y2: float, threshold: float) -> float:
    if y2 == y1:
        return x1
    fraction = (threshold - y1) / (y2 - y1)
    return x1 + fraction * (x2 - x1)


def _metric_check(
    path: str,
    actual: float | int | None,
    limit: float | int,
    passed: bool,
    criterion: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "category": "s11",
        "passed": bool(passed),
        "actual": actual,
        "limit": limit,
        "criterion": criterion,
    }


def _condition_check(
    path: str,
    actual: Any,
    expected: Any,
    passed: bool,
    criterion: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "category": "s11",
        "passed": bool(passed),
        "actual": actual,
        "expected": expected,
        "criterion": criterion,
    }


def _category_summary(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for check in checks:
        category = check["category"]
        item = summary.setdefault(category, {"passed": True, "check_count": 0, "failed_count": 0})
        item["check_count"] += 1
        if not check["passed"]:
            item["passed"] = False
            item["failed_count"] += 1
    return summary


def _category(path: str) -> str:
    root = path.split(".", 1)[0].split("[", 1)[0]
    return root or "root"


def _identity_key(items: list[Any]) -> str | None:
    if not items or not all(isinstance(item, dict) for item in items):
        return None
    for candidate in ("name", "id", "order"):
        if all(candidate in item for item in items):
            values = [str(item[candidate]) for item in items]
            if len(values) == len(set(values)):
                return candidate
    return None


def _named_mapping(raw: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"{label} artifact must contain an array")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError(f"{label}[{index}] must contain a string name")
        name = item["name"]
        if name in result:
            raise ValueError(f"duplicate {label} name: {name}")
        result[name] = dict(item)
    return result


def _job_dimensions(payload: dict[str, Any]) -> tuple[list[Any], str]:
    """Return generated dimensions without guessing between known job schemas."""

    if "dimensions" in payload:
        raw = payload["dimensions"]
        if not isinstance(raw, list):
            raise ValueError("dimensions artifact field dimensions must contain an array")
        return raw, "dimensions.dimensions"

    if "solids" in payload:
        raw = payload["solids"]
        if not isinstance(raw, list):
            raise ValueError("dimensions artifact field solids must contain an array")
        return raw, "dimensions.solids"

    output_contract = payload.get("output_contract")
    if isinstance(output_contract, dict) and "solids" in output_contract:
        raw = output_contract["solids"]
        if not isinstance(raw, list):
            raise ValueError(
                "dimensions artifact field output_contract.solids must contain an array"
            )
        return raw, "dimensions.output_contract.solids"

    raise ValueError(
        "dimensions artifact must contain dimensions, solids, or output_contract.solids"
    )


def _optional_array_field(
    payload: dict[str, Any],
    field: str,
    label: str,
) -> list[Any] | None:
    """Preserve an optional generated array, distinguishing absent from empty."""

    if field not in payload:
        return None
    raw = payload[field]
    if not isinstance(raw, list):
        raise ValueError(f"{label}.{field} must contain an array")
    return raw


def _load_job_artifact(
    artifacts: dict[str, str],
    job_dir: Path,
    name: str,
) -> dict[str, Any]:
    raw = artifacts.get(name)
    if not raw:
        raise ValueError(f"modeling job is missing required artifact: {name}")
    path = Path(raw).expanduser().resolve()
    if path.parent != job_dir or not path.is_file():
        raise PermissionError(f"{name} artifact is missing or outside the modeling job")
    payload = json.loads(path.read_text("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} artifact must contain a JSON object")
    return payload


def _required_json_file(raw: str | Path, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        raise FileNotFoundError(f"{label} JSON does not exist: {path}")
    return path


def _required_csv_file(raw: str | Path, label: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".csv":
        raise FileNotFoundError(f"{label} CSV does not exist: {path}")
    return path


def _file_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _optional_file_record(raw: str | Path | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        return {"path": str(path), "missing": True}
    return _file_record(path)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
