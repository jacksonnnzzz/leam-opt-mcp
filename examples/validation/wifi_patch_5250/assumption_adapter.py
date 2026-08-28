"""Wi-Fi patch adapter for the generic engineering-assumption search engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from antenna_mcp.assumption_search import evaluate_passband_curve

from reference_model import build_reference, paper_parameters


SOLUTION_TYPE = "Terminal"


def paper_parameters_contract() -> dict[str, dict[str, Any]]:
    return paper_parameters()


def design_name(trial: dict[str, Any]) -> str:
    # r2 records the non-overlapping feed CAD topology.  A new name preserves
    # the rejected r1 designs and their immutable receipts for auditability.
    return "ElGendyAst_" + trial["trial_id"].removeprefix("ast-") + "_r2"


def build_trial(hfss: Any, trial: dict[str, Any]) -> Any:
    return build_reference(hfss, assumptions=trial["assumptions"])


def structural_signature(hfss: Any, trial: dict[str, Any]) -> dict[str, Any]:
    assumptions = trial["assumptions"]
    expected_objects = {
        "Substrate",
        "Reflector",
        "Patch",
        "ProbeFeedWire",
        "ProbeFeedOuter",
        "ProbeFeedOuter_ObjectFromFace1",
        "Region",
    }
    if assumptions["conductor_model"] == "zero_thickness_pec_sheets":
        expected_objects.add("Probe")
    expected_boundaries = {"ProbePEC", "ProbePort", "ProbePort_T1", "Radiation"}
    if assumptions["conductor_model"] == "zero_thickness_pec_sheets":
        expected_boundaries.update({"ReflectorPEC", "PatchPEC"})
    actual_objects = set(hfss.modeler.object_names)
    actual_boundaries = {str(boundary.name) for boundary in hfss.boundaries}
    actual_setups = set(hfss.setup_names)
    actual_sweeps = set(hfss.existing_analysis_sweeps)
    issues = []
    if actual_objects != expected_objects:
        issues.append(f"objects={sorted(actual_objects)!r}")
    if actual_boundaries != expected_boundaries:
        issues.append(f"boundaries={sorted(actual_boundaries)!r}")
    if actual_setups != {"Setup1"}:
        issues.append(f"setups={sorted(actual_setups)!r}")
    if "Setup1 : Sweep1" not in actual_sweeps:
        issues.append(f"analysis_sweeps={sorted(actual_sweeps)!r}")
    if issues:
        raise RuntimeError(
            "Wi-Fi assumption trial violates its structural contract: " + "; ".join(issues)
        )
    return {
        "objects": sorted(actual_objects),
        "boundaries": sorted(actual_boundaries),
        "setups": sorted(actual_setups),
        "analysis_sweeps": sorted(actual_sweeps),
    }


def evaluate_s11(path: str | Path, trial: dict[str, Any]) -> dict[str, float]:
    del trial
    return evaluate_passband_curve(path, start_ghz=5.15, stop_ghz=5.35)
