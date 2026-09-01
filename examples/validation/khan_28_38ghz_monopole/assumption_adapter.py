"""Bounded adapter for Khan V2 paper-unresolved port/boundary choices."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from paper_targets import validate_paper_targets
from reference_model_v2 import build_reference, paper_parameters


SOLUTION_TYPE = "Modal"


def paper_parameters_contract() -> dict[str, dict[str, Any]]:
    contract = paper_parameters()
    for specification in contract.values():
        specification["evidence"] = "paper"
    return contract


def design_name(trial: dict[str, Any]) -> str:
    return "KhanAst_" + trial["trial_id"].removeprefix("ast-")


def build_trial(hfss: Any, trial: dict[str, Any]) -> Any:
    return build_reference(hfss, assumptions=trial["assumptions"])


def structural_signature(hfss: Any, trial: dict[str, Any]) -> dict[str, Any]:
    assumptions = trial["assumptions"]
    actual_objects = set(hfss.modeler.object_names)
    required_objects = {"Substrate", "Ground", "Radiator", "Region"}
    allowed_objects = required_objects | {"LumpedPortSheet"}
    actual_boundaries = {str(boundary.name) for boundary in hfss.boundaries}
    required_boundaries = {"GroundPEC", "RadiatorPEC", "Radiation"}
    required_boundaries.add(
        "LumpedPort1"
        if assumptions["excitation"] == "internal_microstrip_lumped_port"
        else "WavePort1"
    )
    issues = []
    if not required_objects.issubset(actual_objects) or not actual_objects.issubset(allowed_objects):
        issues.append(f"objects={sorted(actual_objects)!r}")
    if not required_boundaries.issubset(actual_boundaries):
        issues.append(f"boundaries={sorted(actual_boundaries)!r}")
    if set(hfss.setup_names) != {"Setup1"}:
        issues.append(f"setups={sorted(hfss.setup_names)!r}")
    if "Setup1 : Sweep1" not in set(hfss.existing_analysis_sweeps):
        issues.append(f"analysis_sweeps={sorted(hfss.existing_analysis_sweeps)!r}")
    if issues:
        raise RuntimeError("Khan assumption trial violates its structural contract: " + "; ".join(issues))
    return {
        "objects": sorted(actual_objects),
        "boundaries": sorted(actual_boundaries),
        "setups": sorted(hfss.setup_names),
        "analysis_sweeps": sorted(hfss.existing_analysis_sweeps),
    }


def evaluate_s11(path: str | Path, trial: dict[str, Any]) -> dict[str, float]:
    del trial
    report = validate_paper_targets(path)
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        rows = [(float(row["frequency_ghz"]), float(row["s11_db"])) for row in csv.DictReader(stream)]

    def window(start: float, stop: float) -> tuple[float, float, float]:
        points = [(frequency, value) for frequency, value in rows if start <= frequency <= stop]
        resonance_frequency, minimum = min(points, key=lambda point: point[1])
        return resonance_frequency, minimum, max(value for _, value in points)

    lower_frequency, lower_minimum, lower_worst = window(24.86, 28.65)
    upper_frequency, upper_minimum, upper_worst = window(36.24, 40.82)
    continuous_violation = max(
        lower_worst + 10.0,
        upper_worst + 10.0,
        abs(lower_frequency - 26.7) / 0.6 - 1.0,
        abs(upper_frequency - 38.6) / 0.6 - 1.0,
        lower_minimum + 15.0,
        upper_minimum + 20.0,
    )
    violation = continuous_violation if report["passed"] else max(continuous_violation, 1e-9)
    return {
        "paper_gate_violation": violation,
        "lower_resonance_ghz": lower_frequency,
        "lower_minimum_s11_db": lower_minimum,
        "lower_band_worst_s11_db": lower_worst,
        "upper_resonance_ghz": upper_frequency,
        "upper_minimum_s11_db": upper_minimum,
        "upper_band_worst_s11_db": upper_worst,
    }
