from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from .models import MetricSpec
from .discovery import preferred_aedt_version
from .aedt_runtime import aedt_license_preflight, describe_aedt_exception, prepare_pyaedt_environment


class Evaluator(Protocol):
    def evaluate(self, parameters: dict[str, float]) -> dict[str, float]: ...
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
    ) -> None:
        if session_mode == "new":
            preflight = aedt_license_preflight([project_path.parent])
            if preflight:
                raise RuntimeError(preflight)
        prepare_pyaedt_environment()
        try:
            from ansys.aedt.core import Hfss
        except ImportError as exc:
            raise RuntimeError("Install the hfss extra: pip install 'leam-opt-mcp[hfss]'") from exc
        self.metrics = metrics
        self.units = units
        self.setup_sweep = setup_sweep
        self.session_mode = session_mode
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
            self.hfss = Hfss(**options)
        except Exception as exc:
            raise RuntimeError(describe_aedt_exception(exc, [project_path.parent])) from exc

    def evaluate(self, parameters: dict[str, float]) -> dict[str, float]:
        for name, value in parameters.items():
            self.hfss[name] = f"{value:.12g}{self.units[name]}"
        setup_name = self.setup_sweep.split(":", 1)[0].strip()
        if not self.hfss.analyze_setup(setup_name):
            raise RuntimeError(f"HFSS solve failed for {setup_name}")
        result: dict[str, float] = {}
        for metric in self.metrics:
            data = self.hfss.post.get_solution_data(
                expressions=metric.expression,
                setup_sweep_name=self.setup_sweep,
                primary_sweep_variable="Freq",
                report_category=metric.report_category,
                context=metric.context,
                variations=metric.variations,
            )
            if data is None:
                raise RuntimeError(f"HFSS returned no solution data for {metric.name}")
            frequencies = [_frequency_to_ghz(value) for value in data.primary_sweep_values]
            values = [float(v) for v in data.data_real(metric.expression)]
            if not values:
                raise RuntimeError(f"no samples for {metric.expression}")
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

    def save_best(self, destination: Path) -> None:
        self.hfss.save_project(str(destination))

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
