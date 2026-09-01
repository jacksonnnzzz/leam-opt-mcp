"""Adapter for a bounded search over Ibrahim paper-unresolved HFSS choices."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paper_targets import validate_paper_targets
from reference_model import build_reference, paper_parameters


SOLUTION_TYPE = "Modal"


def paper_parameters_contract() -> dict[str, dict[str, Any]]:
    return paper_parameters()


def design_name(trial: dict[str, Any]) -> str:
    return "IbrahimAst_" + trial["trial_id"].removeprefix("ast-")


def build_trial(hfss: Any, trial: dict[str, Any]) -> Any:
    return build_reference(hfss, assumptions=trial["assumptions"])


def structural_signature(hfss: Any, trial: dict[str, Any]) -> dict[str, Any]:
    assumptions = trial["assumptions"]
    actual_objects = set(hfss.modeler.object_names)
    required_objects = {"Substrate", "Ground", "Radiator", "Region"}
    allowed_objects = required_objects | {"LumpedPortSheet"}
    actual_boundaries = {str(boundary.name) for boundary in hfss.boundaries}
    required_boundaries = {"Radiation"}
    if assumptions["conductor_model"] == "zero_thickness_pec_sheets":
        required_boundaries.update({"GroundPEC", "RadiatorPEC"})
    if assumptions["excitation"] == "internal_microstrip_lumped_port":
        required_boundaries.add("LumpedPort1")
    else:
        required_boundaries.add("WavePort1")
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
        raise RuntimeError(
            "Ibrahim assumption trial violates its structural contract: " + "; ".join(issues)
        )
    return {
        "objects": sorted(actual_objects),
        "boundaries": sorted(actual_boundaries),
        "setups": sorted(hfss.setup_names),
        "analysis_sweeps": sorted(hfss.existing_analysis_sweeps),
    }


def evaluate_s11(path: str | Path, trial: dict[str, Any]) -> dict[str, float]:
    del trial
    report = validate_paper_targets(path)
    resonance = report["observed"]["resonance"]
    band = report["observed"]["minus_10db_band_ghz"]
    if resonance is None or band is None:
        return {
            "paper_gate_violation": 1000.0,
            "resonance_ghz": float(resonance["frequency_ghz"]) if resonance else 0.0,
            "minimum_s11_db": float(resonance["s11_db"]) if resonance else 0.0,
            "band_lower_ghz": float(band[0]) if band else 0.0,
            "band_upper_ghz": float(band[1]) if band else 0.0,
        }
    resonance_frequency = float(resonance["frequency_ghz"])
    minimum_s11 = float(resonance["s11_db"])
    lower = float(band[0])
    upper = float(band[1])
    resonance_violation = (abs(resonance_frequency - 38.0) / 38.0) / 0.01 - 1.0
    depth_violation = minimum_s11 + 25.0
    lower_violation = abs(lower - 36.5) / 0.3 - 1.0
    upper_violation = abs(upper - 39.5) / 0.3 - 1.0
    width_violation = (abs((upper - lower) - 3.0) / 3.0) / 0.1 - 1.0
    return {
        "paper_gate_violation": max(
            resonance_violation,
            depth_violation,
            lower_violation,
            upper_violation,
            width_violation,
        ),
        "resonance_ghz": resonance_frequency,
        "minimum_s11_db": minimum_s11,
        "band_lower_ghz": lower,
        "band_upper_ghz": upper,
    }
