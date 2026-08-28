from __future__ import annotations

import csv
import hashlib
import importlib.util
import itertools
import json
import math
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from .aedt_runtime import (
    aedt_grpc_session_is_active,
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_grpc_session_probe,
    temporary_multi_desktop,
)
from .discovery import preferred_aedt_version
from .s11_export import export_s11_curve


class AssumptionSearchError(ValueError):
    """Raised when a study would lose evidence separation or auditability."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_assumption_space(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    validate_assumption_space(payload)
    return payload


def validate_assumption_space(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "1.0":
        raise AssumptionSearchError("assumption space schema_version must be '1.0'")
    for key in ("study_id", "case_id"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise AssumptionSearchError(f"assumption space {key} must be a non-empty string")
    paper = payload.get("paper_parameters")
    baseline = payload.get("baseline_assumptions")
    search = payload.get("search_space")
    if not isinstance(paper, dict) or not paper:
        raise AssumptionSearchError("paper_parameters must be a non-empty object")
    if not isinstance(baseline, dict) or not baseline:
        raise AssumptionSearchError("baseline_assumptions must be a non-empty object")
    if not isinstance(search, dict) or not search:
        raise AssumptionSearchError("search_space must be a non-empty object")
    overlap = sorted(set(paper) & set(search))
    if overlap:
        raise AssumptionSearchError(
            "paper-explicit parameters cannot appear in search_space: " + ", ".join(overlap)
        )
    for name, item in paper.items():
        if not isinstance(item, dict) or item.get("evidence") != "paper":
            raise AssumptionSearchError(
                f"paper_parameters.{name} must be an object with evidence='paper'"
            )
        if "value" not in item or "unit" not in item:
            raise AssumptionSearchError(
                f"paper_parameters.{name} must contain value and unit"
            )
    for name, item in search.items():
        if name not in baseline:
            raise AssumptionSearchError(
                f"search_space.{name} is not present in baseline_assumptions"
            )
        if not isinstance(item, dict):
            raise AssumptionSearchError(f"search_space.{name} must be an object")
        if item.get("source_status") != "unresolved_from_source":
            raise AssumptionSearchError(
                f"search_space.{name}.source_status must be 'unresolved_from_source'"
            )
        values = item.get("values")
        if not isinstance(values, list) or not values:
            raise AssumptionSearchError(f"search_space.{name}.values must be non-empty")
        for value in values:
            _validate_json_scalar(value, f"search_space.{name}.values")
    strategy = payload.get("strategy", "one_at_a_time")
    if strategy not in {"one_at_a_time", "cartesian"}:
        raise AssumptionSearchError("strategy must be one_at_a_time or cartesian")
    minimum_changed = payload.get("minimum_changed_assumptions", 0)
    maximum_changed = payload.get("maximum_changed_assumptions", len(search))
    if not isinstance(minimum_changed, int) or isinstance(minimum_changed, bool):
        raise AssumptionSearchError("minimum_changed_assumptions must be an integer")
    if not isinstance(maximum_changed, int) or isinstance(maximum_changed, bool):
        raise AssumptionSearchError("maximum_changed_assumptions must be an integer")
    if minimum_changed < 0 or maximum_changed < minimum_changed:
        raise AssumptionSearchError(
            "changed-assumption bounds must satisfy 0 <= minimum <= maximum"
        )
    if maximum_changed > len(search):
        raise AssumptionSearchError(
            "maximum_changed_assumptions cannot exceed the search-space field count"
        )
    acceptance = payload.get("acceptance")
    if not isinstance(acceptance, dict):
        raise AssumptionSearchError("acceptance must be an object")
    if not isinstance(acceptance.get("metric"), str) or not acceptance["metric"]:
        raise AssumptionSearchError("acceptance.metric must be a non-empty string")
    if acceptance.get("operator") not in {"<=", ">=", "<", ">"}:
        raise AssumptionSearchError("acceptance.operator must be <=, >=, <, or >")
    threshold = acceptance.get("threshold")
    if not isinstance(threshold, (int, float)) or not math.isfinite(float(threshold)):
        raise AssumptionSearchError("acceptance.threshold must be finite")
    for index, constraint in enumerate(payload.get("constraints", [])):
        if not isinstance(constraint, dict):
            raise AssumptionSearchError(f"constraints[{index}] must be an object")
        if constraint.get("left") not in baseline:
            raise AssumptionSearchError(f"constraints[{index}].left is not an assumption")
        right = constraint.get("right")
        if isinstance(right, str) and right not in baseline:
            raise AssumptionSearchError(f"constraints[{index}].right is not an assumption")
        if constraint.get("operator") not in {"<", "<=", ">", ">=", "==", "!="}:
            raise AssumptionSearchError(f"constraints[{index}].operator is unsupported")


def _validate_json_scalar(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return
    raise AssumptionSearchError(f"{path} must contain only finite JSON scalar values")


def plan_assumption_trials(
    payload: dict[str, Any], *, limit: int | None = None
) -> list[dict[str, Any]]:
    validate_assumption_space(payload)
    if limit is not None and limit < 1:
        raise AssumptionSearchError("trial limit must be positive")
    baseline = dict(payload["baseline_assumptions"])
    candidates: list[dict[str, Any]] = []
    if payload.get("include_baseline", True):
        candidates.append(baseline)
    if payload.get("strategy", "one_at_a_time") == "one_at_a_time":
        for name, item in payload["search_space"].items():
            for value in item["values"]:
                if value == baseline[name]:
                    continue
                assumptions = dict(baseline)
                assumptions[name] = value
                candidates.append(assumptions)
    else:
        names = list(payload["search_space"])
        axes = []
        for name in names:
            values = [baseline[name], *payload["search_space"][name]["values"]]
            axes.append(list(dict.fromkeys(canonical_json(value) for value in values)))
        if math.prod(len(axis) for axis in axes) > 10_000:
            raise AssumptionSearchError("cartesian search exceeds the 10000-trial safety limit")
        for encoded_values in itertools.product(*axes):
            assumptions = dict(baseline)
            assumptions.update(
                {name: json.loads(value) for name, value in zip(names, encoded_values)}
            )
            candidates.append(assumptions)

    trials: list[dict[str, Any]] = []
    seen: set[str] = set()
    paper_hash = json_sha256(payload["paper_parameters"])
    for assumptions in candidates:
        if not _constraints_pass(payload, assumptions):
            continue
        assumptions_hash = json_sha256(assumptions)
        if assumptions_hash in seen:
            continue
        seen.add(assumptions_hash)
        changed = {
            name: value
            for name, value in assumptions.items()
            if value != baseline.get(name)
        }
        changed_count = len(changed)
        minimum_changed = int(payload.get("minimum_changed_assumptions", 0))
        maximum_changed = int(
            payload.get("maximum_changed_assumptions", len(payload["search_space"]))
        )
        if not minimum_changed <= changed_count <= maximum_changed:
            continue
        trials.append(
            {
                "schema_version": "1.0",
                "study_id": payload["study_id"],
                "case_id": payload["case_id"],
                "trial_id": f"ast-{assumptions_hash[:12]}",
                "paper_parameters_sha256": paper_hash,
                "assumptions_sha256": assumptions_hash,
                "changed_assumptions": changed,
                "assumptions": assumptions,
            }
        )
        if limit is not None and len(trials) >= limit:
            break
    if not trials:
        raise AssumptionSearchError("assumption space produced no valid trials")
    return trials


def _constraints_pass(payload: dict[str, Any], assumptions: dict[str, Any]) -> bool:
    for constraint in payload.get("constraints", []):
        left = assumptions[constraint["left"]]
        right_spec = constraint["right"]
        right = assumptions[right_spec] if isinstance(right_spec, str) else right_spec
        operator = constraint["operator"]
        passed = {
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
            "==": left == right,
            "!=": left != right,
        }[operator]
        if not passed:
            return False
    return True


class AssumptionStudyLedger:
    """Immutable local ledger for an engineering-assumption study."""

    def __init__(self, space_path: str | Path, output_dir: str | Path):
        self.space_path = Path(space_path).expanduser().resolve()
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.space = load_assumption_space(self.space_path)
        self.space_hash = json_sha256(self.space)
        self.paper_hash = json_sha256(self.space["paper_parameters"])

    @property
    def snapshot_path(self) -> Path:
        return self.output_dir / "study_snapshot.json"

    def initialize(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "schema_version": "1.0",
            "study_id": self.space["study_id"],
            "case_id": self.space["case_id"],
            "space_sha256": self.space_hash,
            "paper_parameters_sha256": self.paper_hash,
            "space": self.space,
        }
        if self.snapshot_path.exists():
            actual = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if actual != snapshot:
                raise AssumptionSearchError(
                    "study output belongs to a different assumption-space snapshot"
                )
            return
        with self.snapshot_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")

    def trials(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        return plan_assumption_trials(self.space, limit=limit)

    def trial_dir(self, trial: dict[str, Any]) -> Path:
        self._validate_trial(trial)
        return self.output_dir / "trials" / trial["trial_id"]

    def result_paths(self, trial: dict[str, Any]) -> list[Path]:
        return sorted(self.trial_dir(trial).glob("result_v[0-9][0-9][0-9].json"))

    def latest_result_path(self, trial: dict[str, Any]) -> Path | None:
        paths = self.result_paths(trial)
        return paths[-1] if paths else None

    def pending_trials(
        self,
        *,
        limit: int | None = None,
        resume: bool = False,
        retry_failed: bool = False,
    ) -> list[dict[str, Any]]:
        planned = self.trials(limit=limit)
        results = {item["trial"]["trial_id"]: item for item in self.load_results()}
        if results and not resume:
            raise AssumptionSearchError(
                "study already contains results; use --resume to continue without overwriting"
            )
        if retry_failed and not resume:
            raise AssumptionSearchError("--retry-failed requires --resume")
        pending = []
        for trial in planned:
            result = results.get(trial["trial_id"])
            if result is None or (retry_failed and result["status"] == "failed"):
                pending.append(trial)
        return pending

    def write_build_receipt(
        self, trial: dict[str, Any], *, design: str, signature: dict[str, Any]
    ) -> Path:
        directory = self.trial_dir(trial)
        directory.mkdir(parents=True, exist_ok=True)
        receipt = {
            "schema_version": "1.0",
            "trial_id": trial["trial_id"],
            "assumptions_sha256": trial["assumptions_sha256"],
            "paper_parameters_sha256": trial["paper_parameters_sha256"],
            "design": design,
            "signature": signature,
        }
        paths = self.build_receipt_paths(trial)
        for path in paths:
            actual = json.loads(path.read_text(encoding="utf-8"))
            if actual == receipt:
                return path
            if actual.get("design") == design:
                raise AssumptionSearchError(
                    f"build receipt mismatch for design {design!r}"
                )
        path = directory / (
            "build_receipt.json" if not paths else f"build_receipt_v{len(paths) + 1:03d}.json"
        )
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        return path

    def build_receipt_paths(self, trial: dict[str, Any]) -> list[Path]:
        directory = self.trial_dir(trial)
        paths = []
        legacy = directory / "build_receipt.json"
        if legacy.is_file():
            paths.append(legacy)
        paths.extend(sorted(directory.glob("build_receipt_v[0-9][0-9][0-9].json")))
        return paths

    def verify_build_receipt(self, trial: dict[str, Any], *, design: str) -> dict[str, Any]:
        paths = self.build_receipt_paths(trial)
        if not paths:
            raise AssumptionSearchError(
                f"existing design {design!r} has no immutable build receipt; refusing resume"
            )
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
        matching = [receipt for receipt in receipts if receipt.get("design") == design]
        if len(matching) != 1:
            raise AssumptionSearchError(
                f"existing design {design!r} has no unique immutable build receipt; refusing resume"
            )
        receipt = matching[0]
        expected = {
            "trial_id": trial["trial_id"],
            "assumptions_sha256": trial["assumptions_sha256"],
            "paper_parameters_sha256": trial["paper_parameters_sha256"],
            "design": design,
        }
        for key, value in expected.items():
            if receipt.get(key) != value:
                raise AssumptionSearchError(
                    f"existing design receipt has wrong {key}: {receipt.get(key)!r}"
                )
        if not isinstance(receipt.get("signature"), dict):
            raise AssumptionSearchError("existing design receipt has no structural signature")
        return receipt

    def record_result(
        self,
        trial: dict[str, Any],
        result: dict[str, Any],
        *,
        curve_path: str | Path | None = None,
        allow_retry: bool = False,
    ) -> dict[str, Any]:
        self._validate_trial(trial)
        directory = self.trial_dir(trial)
        directory.mkdir(parents=True, exist_ok=True)
        normalized = _normalize_trial_result(self.space, result)
        if curve_path is not None:
            curve = Path(curve_path).expanduser().resolve()
            if not curve.is_file() or curve.parent != directory.resolve():
                raise AssumptionSearchError(
                    "trial curve must exist directly inside its immutable trial directory"
                )
            normalized["s11"] = {
                "file": curve.name,
                "sha256": hashlib.sha256(curve.read_bytes()).hexdigest(),
            }
        elif normalized["status"] == "completed":
            raise AssumptionSearchError("completed trial requires an S11 curve")
        payload = {
            "schema_version": "1.0",
            "trial": trial,
            **normalized,
        }
        existing = self.result_paths(trial)
        if existing:
            actual = json.loads(existing[-1].read_text(encoding="utf-8"))
            if actual != payload:
                if not allow_retry or actual.get("status") != "failed":
                    raise AssumptionSearchError(
                        f"refusing to overwrite different result for {trial['trial_id']}"
                    )
            else:
                return actual
        index = len(existing) + 1
        path = directory / f"result_v{index:03d}.json"
        if path.exists():
            raise AssumptionSearchError(f"result version collision at {path}")
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self.write_summary()
        return payload

    def load_results(self) -> list[dict[str, Any]]:
        root = self.output_dir / "trials"
        if not root.is_dir():
            return []
        results = []
        for directory in sorted(root.glob("ast-*")):
            paths = sorted(directory.glob("result_v[0-9][0-9][0-9].json"))
            if not paths:
                continue
            payload = json.loads(paths[-1].read_text(encoding="utf-8"))
            self._validate_trial(payload.get("trial"))
            results.append(payload)
        return results

    def summary(self) -> dict[str, Any]:
        results = self.load_results()
        ranked = sorted(results, key=lambda item: _result_rank_key(self.space, item))
        return {
            "schema_version": "1.0",
            "study_id": self.space["study_id"],
            "case_id": self.space["case_id"],
            "space_sha256": self.space_hash,
            "paper_parameters_sha256": self.paper_hash,
            "result_count": len(results),
            "completed_count": sum(item["status"] == "completed" for item in results),
            "failed_count": sum(item["status"] == "failed" for item in results),
            "paper_gate_passed_count": sum(item["paper_gate_passed"] for item in results),
            "status": _study_status(results),
            "ranking": [
                {
                    "rank": index,
                    "trial_id": item["trial"]["trial_id"],
                    "changed_assumptions": item["trial"]["changed_assumptions"],
                    "status": item["status"],
                    "converged": item["converged"],
                    "paper_gate_passed": item["paper_gate_passed"],
                    "metrics": item.get("metrics", {}),
                }
                for index, item in enumerate(ranked, start=1)
            ],
        }

    def write_summary(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        index = 1
        while (self.output_dir / f"summary_v{index:03d}.json").exists():
            index += 1
        path = self.output_dir / f"summary_v{index:03d}.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(self.summary(), ensure_ascii=False, indent=2) + "\n")
        return path

    def prepare(self, *, limit: int | None = None) -> dict[str, Any]:
        self.initialize()
        trials = self.trials(limit=limit)
        return {
            "status": "planned",
            "study_id": self.space["study_id"],
            "case_id": self.space["case_id"],
            "space_sha256": self.space_hash,
            "paper_parameters_sha256": self.paper_hash,
            "trial_count": len(trials),
            "trials": trials,
            "output_dir": str(self.output_dir),
        }

    def _validate_trial(self, trial: Any) -> None:
        if not isinstance(trial, dict):
            raise AssumptionSearchError("trial must be an object")
        if trial.get("study_id") != self.space["study_id"]:
            raise AssumptionSearchError("trial belongs to a different study")
        if trial.get("paper_parameters_sha256") != self.paper_hash:
            raise AssumptionSearchError("trial paper parameters are not the frozen snapshot")
        assumptions = trial.get("assumptions")
        if not isinstance(assumptions, dict) or json_sha256(assumptions) != trial.get(
            "assumptions_sha256"
        ):
            raise AssumptionSearchError("trial assumption hash is invalid")
        if trial.get("trial_id") != f"ast-{trial['assumptions_sha256'][:12]}":
            raise AssumptionSearchError("trial_id does not match its assumption hash")


def _normalize_trial_result(
    space: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("status") not in {"completed", "failed"}:
        raise AssumptionSearchError("trial result status must be completed or failed")
    status = result["status"]
    converged = result.get("converged") is True
    metrics = result.get("metrics", {})
    if status == "completed" and not isinstance(metrics, dict):
        raise AssumptionSearchError("completed trial metrics must be an object")
    metric_name = space["acceptance"]["metric"]
    metric_value = metrics.get(metric_name) if isinstance(metrics, dict) else None
    if status == "completed":
        if not isinstance(metric_value, (int, float)) or not math.isfinite(float(metric_value)):
            raise AssumptionSearchError(
                f"completed trial requires finite metric {metric_name!r}"
            )
        for name, value in metrics.items():
            if not isinstance(name, str) or not isinstance(value, (int, float)):
                raise AssumptionSearchError("trial metrics must be numeric")
            if not math.isfinite(float(value)):
                raise AssumptionSearchError("trial metrics must be finite")
    metric_passed = status == "completed" and _compare(
        float(metric_value),
        space["acceptance"]["operator"],
        float(space["acceptance"]["threshold"]),
    )
    convergence_required = space["acceptance"].get("require_converged", True)
    paper_gate_passed = metric_passed and (converged or not convergence_required)
    normalized = {
        "status": status,
        "converged": converged,
        "paper_gate_passed": paper_gate_passed,
        "metrics": metrics if isinstance(metrics, dict) else {},
    }
    if "design" in result:
        normalized["design"] = str(result["design"])
    if "convergence" in result:
        if not isinstance(result["convergence"], dict):
            raise AssumptionSearchError("convergence evidence must be an object")
        normalized["convergence"] = result["convergence"]
    if status == "failed":
        error = str(result.get("error", "unspecified trial failure")).strip()
        normalized["error"] = error[:4000]
        failure_kind = result.get("failure_kind")
        if failure_kind is not None:
            if failure_kind not in {
                "license_unavailable",
                "geometry_validation",
                "solver_failure",
                "evidence_export",
                "client_interrupted",
            }:
                raise AssumptionSearchError("failed trial has unsupported failure_kind")
            normalized["failure_kind"] = failure_kind
    return normalized


def _compare(actual: float, operator: str, expected: float) -> bool:
    return {
        "<=": actual <= expected,
        ">=": actual >= expected,
        "<": actual < expected,
        ">": actual > expected,
    }[operator]


def _result_rank_key(space: dict[str, Any], result: dict[str, Any]) -> tuple[Any, ...]:
    metric_name = space["acceptance"]["metric"]
    metric = result.get("metrics", {}).get(metric_name)
    numeric = float(metric) if isinstance(metric, (int, float)) else math.inf
    if space["acceptance"]["operator"] in {">", ">="}:
        numeric = -numeric
    return (
        not result.get("paper_gate_passed", False),
        result.get("status") != "completed",
        not result.get("converged", False),
        numeric,
        result["trial"]["trial_id"],
    )


def _study_status(results: list[dict[str, Any]]) -> str:
    if any(item["paper_gate_passed"] for item in results):
        return "passed_variant_found"
    if results and all(item["status"] == "failed" for item in results):
        return "failed"
    if any(item["status"] == "failed" for item in results):
        return "completed_with_failures"
    return "no_passing_variant_yet"


def evaluate_passband_curve(
    path: str | Path, *, start_ghz: float, stop_ghz: float
) -> dict[str, float]:
    points: list[tuple[float, float]] = []
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["frequency_ghz", "s11_db"]:
            raise AssumptionSearchError(
                "S11 CSV must have exactly frequency_ghz,s11_db columns"
            )
        for row in reader:
            point = (float(row["frequency_ghz"]), float(row["s11_db"]))
            if not all(math.isfinite(value) for value in point):
                raise AssumptionSearchError("S11 CSV contains non-finite data")
            points.append(point)
    if len(points) < 3 or any(b[0] <= a[0] for a, b in zip(points, points[1:])):
        raise AssumptionSearchError("S11 CSV frequencies must be strictly increasing")
    if points[0][0] > start_ghz or points[-1][0] < stop_ghz:
        raise AssumptionSearchError("S11 CSV does not cover the target band")
    band = [
        (start_ghz, _interpolate(points, start_ghz)),
        *((frequency, value) for frequency, value in points if start_ghz < frequency < stop_ghz),
        (stop_ghz, _interpolate(points, stop_ghz)),
    ]
    resonance_frequency, minimum_s11 = min(band, key=lambda item: item[1])
    return {
        "maximum_s11_in_target_band_db": max(value for _, value in band),
        "minimum_s11_in_target_band_db": minimum_s11,
        "resonant_frequency_ghz": resonance_frequency,
    }


def _interpolate(points: list[tuple[float, float]], target: float) -> float:
    for index, (frequency, value) in enumerate(points):
        if frequency == target:
            return value
        if frequency > target:
            previous_frequency, previous_value = points[index - 1]
            fraction = (target - previous_frequency) / (frequency - previous_frequency)
            return previous_value + fraction * (value - previous_value)
    raise AssumptionSearchError("interpolation target is outside the S11 curve")


def collect_convergence_evidence(
    hfss: Any, *, setup_name: str = "Setup1", sweep_name: str = "Sweep1", max_delta_s: float
) -> dict[str, Any]:
    profiles = hfss.get_profile(setup_name)
    if not profiles:
        raise RuntimeError("HFSS returned no solver profile for convergence verification")
    keys = list(profiles.keys())
    if not keys:
        raise RuntimeError("HFSS solver profile is empty")
    profile = profiles[keys[-1]]
    adaptive = getattr(profile, "adaptive_pass", None)
    steps = getattr(adaptive, "steps", {}) if adaptive is not None else {}
    pass_items = [(name, item) for name, item in steps.items() if "pass" in name.casefold()]
    deltas = [
        float(item.delta_s_max)
        for _, item in pass_items
        if getattr(item, "delta_s_max", None) is not None
    ]
    if not deltas:
        raise RuntimeError("HFSS profile contains no adaptive Delta S evidence")
    sweeps = getattr(profile, "frequency_sweeps", {}) or {}
    sweep = sweeps.get(sweep_name)
    if sweep is None:
        matching = [value for name, value in sweeps.items() if sweep_name.casefold() in name.casefold()]
        sweep = matching[-1] if len(matching) == 1 else None
    if sweep is None:
        raise RuntimeError(f"HFSS profile contains no {sweep_name!r} evidence")
    sweep_converged = getattr(sweep, "converged", None) is True
    final_delta = deltas[-1]
    return {
        "adaptive_passes_completed": len(pass_items),
        "final_max_magnitude_delta_s": final_delta,
        "adaptive_delta_s_limit": float(max_delta_s),
        "adaptive_converged": final_delta <= float(max_delta_s),
        "sweep_converged": sweep_converged,
        "converged": final_delta <= float(max_delta_s) and sweep_converged,
    }


def run_aedt_assumption_search(
    *,
    space_path: str | Path,
    adapter_path: str | Path,
    output_dir: str | Path,
    grpc_port: int,
    active_project: str,
    version: str | None = None,
    limit: int | None = None,
    resume: bool = False,
    retry_failed: bool = False,
    postprocess_existing: bool = False,
) -> dict[str, Any]:
    if not 1 <= int(grpc_port) <= 65535:
        raise AssumptionSearchError("gRPC port must be between 1 and 65535")
    ledger = AssumptionStudyLedger(space_path, output_dir)
    ledger.initialize()
    adapter = _load_adapter(adapter_path)
    adapter_paper = adapter.paper_parameters_contract()
    if canonical_json(adapter_paper) != canonical_json(ledger.space["paper_parameters"]):
        raise AssumptionSearchError(
            "adapter paper parameters do not match the frozen assumption space"
        )
    planned = ledger.trials(limit=limit)
    pending = ledger.pending_trials(
        limit=limit, resume=resume, retry_failed=retry_failed
    )
    if postprocess_existing and not resume:
        raise AssumptionSearchError("--postprocess-existing requires --resume")
    if not pending:
        return {**ledger.summary(), "status": "already_complete", "pending_count": 0}
    design_by_trial = {trial["trial_id"]: _adapter_design_name(adapter, trial) for trial in planned}

    prepare_pyaedt_environment()
    from ansys.aedt.core import Desktop, Hfss

    selected_version = version or preferred_aedt_version()
    if not aedt_grpc_session_is_active(grpc_port, "127.0.0.1"):
        raise RuntimeError(
            f"no active AEDT gRPC session is available on port {grpc_port}; "
            "refusing to launch a fallback session"
        )
    desktop = None
    hfss = None
    try:
        with temporary_grpc_session_probe(), temporary_multi_desktop():
            desktop = Desktop(
                version=selected_version,
                non_graphical=False,
                new_desktop=False,
                close_on_exit=False,
                port=grpc_port,
            )
        if getattr(desktop, "launched_by_pyaedt", None) is not False:
            raise RuntimeError("strict AEDT attachment unexpectedly launched a new Desktop")
        projects = list(desktop.project_list)
        matching = [name for name in projects if name.casefold() == active_project.casefold()]
        if not matching:
            raise RuntimeError(
                f"project {active_project!r} is not open; available projects: {projects}"
            )
        project = matching[0]
        existing_designs = set(desktop.design_list(project))
        pending_designs = {design_by_trial[item["trial_id"]] for item in pending}
        conflicts = sorted(existing_designs & pending_designs)
        if conflicts and not resume:
            raise RuntimeError(
                "assumption-study designs already exist; inspect them and use --resume: "
                + ", ".join(conflicts)
            )
        first = pending[0]
        first_design = design_by_trial[first["trial_id"]]
        with temporary_grpc_session_probe(), temporary_multi_desktop():
            hfss = Hfss(
                project=project,
                design=first_design,
                solution_type=str(adapter.SOLUTION_TYPE),
                version=selected_version,
                non_graphical=False,
                new_desktop=False,
                close_on_exit=False,
                port=grpc_port,
            )
        ensure_strict_existing_attachment(hfss, grpc_port)

        for trial in pending:
            # A previous client can disappear while AEDT keeps solving.  Never
            # change the active design until the shared Desktop is idle: doing
            # so aborts the orphaned solve and can cascade false failures into
            # every later trial in the batch.
            wait_for_aedt_idle(hfss)
            design = design_by_trial[trial["trial_id"]]
            directory = ledger.trial_dir(trial)
            directory.mkdir(parents=True, exist_ok=True)
            curve_index = 1
            while (directory / f"s11_v{curve_index:03d}.csv").exists():
                curve_index += 1
            curve = directory / f"s11_v{curve_index:03d}.csv"
            try:
                was_preexisting = design in existing_designs
                if str(hfss.design_name) != design:
                    if was_preexisting:
                        if not hfss.set_active_design(design):
                            raise RuntimeError(f"unable to activate existing design {design!r}")
                    else:
                        hfss.insert_design(design, solution_type=str(adapter.SOLUTION_TYPE))
                if str(hfss.design_name) != design:
                    raise RuntimeError(
                        f"active design is {hfss.design_name!r}, expected {design!r}"
                    )
                if was_preexisting:
                    if _is_empty_design(hfss):
                        adapter.build_trial(hfss, trial)
                        signature = adapter.structural_signature(hfss, trial)
                        ledger.write_build_receipt(trial, design=design, signature=signature)
                    else:
                        try:
                            receipt = ledger.verify_build_receipt(trial, design=design)
                        except AssumptionSearchError:
                            latest = ledger.latest_result_path(trial)
                            if not (
                                retry_failed
                                and latest is not None
                                and _validator_rejected_build_can_be_adopted(latest)
                            ):
                                raise
                            signature = adapter.structural_signature(hfss, trial)
                            receipt_path = ledger.write_build_receipt(
                                trial, design=design, signature=signature
                            )
                            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                        actual = adapter.structural_signature(hfss, trial)
                        if actual != receipt["signature"]:
                            raise RuntimeError(
                                f"existing design {design!r} no longer matches its build receipt"
                            )
                else:
                    if not _is_empty_design(hfss):
                        raise RuntimeError(f"new design {design!r} is not empty")
                    adapter.build_trial(hfss, trial)
                    signature = adapter.structural_signature(hfss, trial)
                    ledger.write_build_receipt(trial, design=design, signature=signature)
                if postprocess_existing:
                    if not was_preexisting:
                        raise RuntimeError(
                            f"postprocess-only trial has no existing design {design!r}"
                        )
                else:
                    if not hfss.save_project():
                        raise RuntimeError(f"HFSS failed to save {design!r}")
                    errors_before = set(_aedt_error_messages(hfss))
                    if not hfss.analyze_setup("Setup1"):
                        # Analyze can return early when AEDT reports a stale or
                        # concurrent solve.  Drain that solve before the next
                        # iteration is allowed to activate another design.
                        wait_for_aedt_idle(hfss)
                        errors_after = _aedt_error_messages(hfss)
                        new_errors = [item for item in errors_after if item not in errors_before]
                        details = new_errors or errors_after[-8:]
                        suffix = " | ".join(details[-8:])
                        raise RuntimeError(
                            f"HFSS failed to solve Setup1 in {design!r}"
                            + (f"; AEDT errors: {suffix}" if suffix else "")
                        )
                convergence = collect_convergence_evidence(
                    hfss,
                    setup_name="Setup1",
                    sweep_name="Sweep1",
                    max_delta_s=float(ledger.space["solver_gate"]["max_delta_s"]),
                )
                export_s11_curve(hfss, curve, setup_sweep="Setup1 : Sweep1")
                metrics = adapter.evaluate_s11(curve, trial)
                ledger.record_result(
                    trial,
                    {
                        "status": "completed",
                        "design": design,
                        "converged": convergence["converged"],
                        "convergence": convergence,
                        "metrics": metrics,
                    },
                    curve_path=curve,
                    allow_retry=retry_failed,
                )
                existing_designs.add(design)
            except Exception as exc:
                latest = ledger.latest_result_path(trial)
                if latest is None or retry_failed:
                    error = f"{type(exc).__name__}: {exc}"
                    ledger.record_result(
                        trial,
                        {
                            "status": "failed",
                            "design": design,
                            "converged": False,
                            "metrics": {},
                            "error": error,
                            "failure_kind": classify_assumption_failure(error),
                        },
                        curve_path=curve if curve.exists() else None,
                        allow_retry=retry_failed,
                    )
                existing_designs.add(design)
        if not postprocess_existing and not hfss.save_project():
            raise RuntimeError("HFSS failed to save the completed assumption study")
        summary_path = ledger.write_summary()
        return {
            **ledger.summary(),
            "planned_count": len(planned),
            "pending_count": 0,
            "summary": str(summary_path),
        }
    finally:
        if hfss is not None:
            hfss.release_desktop(close_projects=False, close_desktop=False)
        elif desktop is not None:
            try:
                desktop.release_desktop(close_projects=False, close_on_exit=False)
            except Exception:
                pass


def _load_adapter(path: str | Path) -> ModuleType:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".py":
        raise AssumptionSearchError("assumption adapter must be an existing Python file")
    name = f"antenna_assumption_adapter_{hashlib.sha256(str(source).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise AssumptionSearchError(f"unable to load assumption adapter {source}")
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous_reference = sys.modules.pop("reference_model", sentinel)
    sys.path.insert(0, str(source.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(source.parent))
        sys.modules.pop("reference_model", None)
        if previous_reference is not sentinel:
            sys.modules["reference_model"] = previous_reference
    for attribute in (
        "SOLUTION_TYPE",
        "paper_parameters_contract",
        "design_name",
        "build_trial",
        "structural_signature",
        "evaluate_s11",
    ):
        if not hasattr(module, attribute):
            raise AssumptionSearchError(f"assumption adapter is missing {attribute}")
    return module


def _adapter_design_name(adapter: ModuleType, trial: dict[str, Any]) -> str:
    name = str(adapter.design_name(trial))
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
        raise AssumptionSearchError(
            f"adapter produced unsafe AEDT design name {name!r}"
        )
    return name


def _is_empty_design(hfss: Any) -> bool:
    return not (
        list(hfss.modeler.object_names)
        or list(getattr(hfss, "boundaries", []))
        or list(getattr(hfss, "setup_names", []))
    )


def _validator_rejected_build_can_be_adopted(path: str | Path) -> bool:
    """Allow one fail-closed migration after a structural validator is corrected.

    The new adapter must still validate the live design before a receipt is written.
    Solver, license, manual, or otherwise incomplete failures are never eligible.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        payload.get("status") == "failed"
        and payload.get("converged") is False
        and "violates its structural contract" in str(payload.get("error", ""))
        and "s11" not in payload
    )


def _aedt_error_messages(hfss: Any) -> list[str]:
    try:
        messages = hfss.logger.get_messages(
            str(hfss.project_name),
            str(hfss.design_name),
            level=2,
            aedt_messages=True,
        )
        return [str(item) for item in messages.error_level]
    except Exception:
        return []


def wait_for_aedt_idle(
    hfss: Any,
    *,
    timeout_seconds: float = 7200.0,
    poll_seconds: float = 2.0,
) -> None:
    """Wait until the attached shared AEDT Desktop has no active simulation.

    The check is deliberately global because changing the active design while
    any design is solving can stop that solve in AEDT.  A timeout fails closed
    instead of silently contaminating subsequent assumption trials.
    """
    deadline = time.monotonic() + timeout_seconds
    while bool(hfss.are_there_simulations_running):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"AEDT is still running a simulation after {timeout_seconds:g} seconds"
            )
        time.sleep(poll_seconds)


def classify_assumption_failure(error: str) -> str:
    value = error.casefold()
    if any(
        marker in value
        for marker in (
            "license error",
            "license found",
            "vendor daemon",
            "license checkout",
            "licensed feature",
        )
    ):
        return "license_unavailable"
    if any(marker in value for marker in ("intersect", "model validation", "geometry validation")):
        return "geometry_validation"
    if any(
        marker in value
        for marker in (
            "already running",
            "aborted by user",
            "stopped on user request",
            "still running a simulation",
        )
    ):
        return "client_interrupted"
    if any(marker in value for marker in ("s11", "profile", "convergence", "evidence")):
        return "evidence_export"
    return "solver_failure"
