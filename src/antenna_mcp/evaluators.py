from __future__ import annotations

import math
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from .assumption_search import collect_convergence_evidence, wait_for_aedt_idle
from .models import MetricSpec, ParameterBound
from .discovery import preferred_aedt_version
from .aedt_runtime import (
    aedt_grpc_session_is_active,
    aedt_license_preflight,
    describe_aedt_exception,
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_grpc_session_probe,
    temporary_multi_desktop,
)
from .s11_export import select_unique_s11_expression


class Evaluator(Protocol):
    def verify_parameter_effects(
        self, parameters: list[ParameterBound]
    ) -> dict[str, Any]: ...
    def evaluate(self, parameters: dict[str, float]) -> dict[str, float]: ...
    def convergence_evidence(self) -> dict[str, Any]: ...
    def save_best(self, destination: Path) -> None: ...
    def close(self) -> None: ...


class HfssEvaluator:
    """A stateful PyAEDT evaluator for one copied HFSS project."""

    def __init__(
        self,
        project_path: Path,
        design_name: str | None,
        setup_sweep: str,
        metrics: list[MetricSpec],
        units: dict[str, str],
        session_mode: str = "new",
        grpc_port: int | None = None,
        max_delta_s: float = 0.02,
        maximum_adaptive_passes: int | None = None,
    ) -> None:
        if session_mode == "new":
            preflight = aedt_license_preflight([project_path.parent])
            if preflight:
                raise RuntimeError(preflight)
        elif not grpc_port or not aedt_grpc_session_is_active(grpc_port, "127.0.0.1"):
            raise RuntimeError(
                f"no active AEDT gRPC session is available on port {grpc_port}; "
                "refusing to launch a fallback session"
            )
        prepare_pyaedt_environment()
        try:
            from ansys.aedt.core import Hfss
        except ImportError as exc:
            raise RuntimeError("Install the hfss extra: pip install 'leam-opt-mcp[hfss]'") from exc
        self.metrics = metrics
        self.units = units
        self.setup_sweep = setup_sweep
        setup_parts = [item.strip() for item in setup_sweep.split(":")]
        if len(setup_parts) != 2 or not all(setup_parts):
            raise ValueError("setup_sweep must use 'SetupName : SweepName'")
        self.setup_name, self.sweep_name = setup_parts
        self.session_mode = session_mode
        self.max_delta_s = max_delta_s
        self.maximum_adaptive_passes = maximum_adaptive_passes
        self._last_convergence: dict[str, Any] | None = None
        options = dict(
            project=str(project_path),
            design=design_name,
            non_graphical=session_mode == "new",
            new_desktop=session_mode == "new",
            close_on_exit=False,
        )
        if session_mode == "existing":
            options["port"] = grpc_port or 0
        version = preferred_aedt_version()
        if version:
            options["version"] = version
        try:
            if session_mode == "existing":
                with temporary_grpc_session_probe(), temporary_multi_desktop():
                    self.hfss = Hfss(**options)
                ensure_strict_existing_attachment(self.hfss, int(grpc_port))
            else:
                self.hfss = Hfss(**options)
        except Exception as exc:
            raise RuntimeError(describe_aedt_exception(exc, [project_path.parent])) from exc
        self._apply_setup_overrides()

    def evaluate(self, parameters: dict[str, float]) -> dict[str, float]:
        self._last_convergence = None
        wait_for_aedt_idle(self.hfss)
        for name, value in parameters.items():
            self.hfss[name] = _format_parameter_value(value, self.units[name])
        if not self.hfss.analyze_setup(self.setup_name):
            wait_for_aedt_idle(self.hfss)
            raise RuntimeError(f"HFSS solve failed for {self.setup_name}")
        self._last_convergence = collect_convergence_evidence(
            self.hfss,
            setup_name=self.setup_name,
            sweep_name=self.sweep_name,
            max_delta_s=self.max_delta_s,
        )
        result: dict[str, float] = {}
        for metric in self.metrics:
            expression = self._resolve_expression(metric)
            data = self.hfss.post.get_solution_data(
                expressions=expression,
                setup_sweep_name=self.setup_sweep,
                primary_sweep_variable="Freq",
                report_category=metric.report_category,
                context=metric.context,
                variations=metric.variations,
            )
            if data is None:
                raise RuntimeError(f"HFSS returned no solution data for {metric.name}")
            frequencies = [_frequency_to_ghz(value) for value in data.primary_sweep_values]
            values = [float(v) for v in data.data_real(expression)]
            if not values:
                raise RuntimeError(f"no samples for {expression}")
            indices = list(range(min(len(values), len(frequencies))))
            if metric.frequency_min_ghz is not None:
                indices = [
                    index
                    for index in indices
                    if metric.frequency_min_ghz <= frequencies[index] <= metric.frequency_max_ghz
                ]
            if not indices:
                raise RuntimeError(f"no samples in requested frequency range for {metric.name}")
            selected_values = [values[index] for index in indices]
            if metric.reducer == "min":
                value = min(selected_values)
            elif metric.reducer == "max":
                value = max(selected_values)
            elif metric.reducer == "mean":
                value = sum(selected_values) / len(selected_values)
            else:
                index = min(indices, key=lambda i: abs(frequencies[i] - metric.frequency_ghz))
                value = values[index]
            if not math.isfinite(value):
                raise RuntimeError(f"non-finite metric {metric.name}")
            result[metric.name] = value
        return result

    def verify_parameter_effects(
        self, parameters: list[ParameterBound]
    ) -> dict[str, Any]:
        wait_for_aedt_idle(self.hfss)
        baseline = self._geometry_signature()
        if not baseline:
            raise RuntimeError("optimization design contains no geometry")
        original_values = {bound.name: str(self.hfss[bound.name]) for bound in parameters}
        evidence: list[dict[str, Any]] = []
        try:
            for bound in parameters:
                changed = False
                tried: list[float] = []
                for candidate in (bound.lower, bound.upper):
                    tried.append(candidate)
                    self.hfss[bound.name] = _format_parameter_value(candidate, bound.unit)
                    self._refresh_modeler()
                    if self._geometry_signature() != baseline:
                        changed = True
                        break
                self.hfss[bound.name] = original_values[bound.name]
                self._refresh_modeler()
                if self._geometry_signature() != baseline:
                    raise RuntimeError(
                        f"failed to restore baseline geometry after probing {bound.name}"
                    )
                evidence.append(
                    {
                        "name": bound.name,
                        "unit": bound.unit,
                        "tried_values": tried,
                        "geometry_changed": changed,
                    }
                )
        finally:
            for name, value in original_values.items():
                self.hfss[name] = value
            self._refresh_modeler()
        return {
            "schema_version": "1.0",
            "baseline_geometry_sha256": _json_sha256(baseline),
            "all_parameters_effective": all(
                item["geometry_changed"] for item in evidence
            ),
            "parameters": evidence,
        }

    def _geometry_signature(self) -> dict[str, Any]:
        signature: dict[str, Any] = {}
        for name in sorted(self.hfss.modeler.object_names):
            item = self.hfss.modeler[name]
            bounding_box = list(getattr(item, "bounding_box", []) or [])
            signature[name] = [round(float(value), 12) for value in bounding_box]
        return signature

    def _refresh_modeler(self) -> None:
        refresh = getattr(self.hfss.modeler, "refresh_all_ids", None)
        if callable(refresh):
            refresh()

    def _resolve_expression(self, metric: MetricSpec) -> str:
        if metric.expression != "auto_s11":
            return metric.expression
        traces = list(
            self.hfss.get_traces_for_plot(
                get_self_terms=True,
                get_mutual_terms=False,
                category="dB(S",
            )
        )
        return select_unique_s11_expression(traces)

    def _apply_setup_overrides(self) -> None:
        if self.maximum_adaptive_passes is None:
            return
        setup = self.hfss.get_setup(self.setup_name)
        if setup is None:
            raise RuntimeError(f"HFSS design contains no setup {self.setup_name!r}")
        setup.props["MaximumPasses"] = self.maximum_adaptive_passes
        if "MaxPass" in setup.props:
            setup.props["MaxPass"] = self.maximum_adaptive_passes
        if not setup.update():
            raise RuntimeError(
                f"HFSS failed to set MaximumPasses={self.maximum_adaptive_passes}"
            )

    def convergence_evidence(self) -> dict[str, Any]:
        if self._last_convergence is None:
            raise RuntimeError("no convergence evidence is available for the latest evaluation")
        return dict(self._last_convergence)

    def save_best(self, destination: Path) -> None:
        if not self.hfss.save_project(str(destination)):
            raise RuntimeError(f"HFSS failed to save best project to {destination}")

    def close(self) -> None:
        close = self.session_mode == "new"
        self.hfss.release_desktop(close_projects=close, close_desktop=close)


def _frequency_to_ghz(value: object) -> float:
    text = str(value).strip().lower()
    factors = {"ghz": 1.0, "mhz": 1e-3, "khz": 1e-6, "hz": 1e-9}
    for suffix, factor in factors.items():
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    return float(text)


def _format_parameter_value(value: float, unit: str) -> str:
    suffix = "" if unit.strip().casefold() in {"", "1", "ratio", "dimensionless"} else unit
    return f"{value:.12g}{suffix}"


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
