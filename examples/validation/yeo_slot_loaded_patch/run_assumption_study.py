from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from antenna_mcp.aedt_runtime import (
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_grpc_session_probe,
    temporary_multi_desktop,
)
from antenna_mcp.discovery import preferred_aedt_version

from assumption_study import VARIANTS_BY_CASE, build_variant
from paper_targets import validate_paper_targets
from run_reference import _export_s11, _preflight_existing_desktop, _release_desktop_only


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Solve controlled non-paper Yeo port/conductor assumption variants."
    )
    parser.add_argument("--grpc-port", type=int, required=True)
    parser.add_argument("--active-project", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--case",
        choices=sorted(VARIANTS_BY_CASE),
        default="conventional",
        help="Paper case whose explicit dimensions remain fixed during the study.",
    )
    parser.add_argument("--version", default=None)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse only exact, structurally verified study designs/curves after an interrupted run.",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.grpc_port <= 65535:
        parser.error("--grpc-port must be between 1 and 65535")

    output_dir = args.output_dir.expanduser().resolve()
    report_path = output_dir / "assumption_study_report.json"
    variants = VARIANTS_BY_CASE[args.case]
    curves = {key: output_dir / f"{key}_s11.csv" for key in variants}
    conflicts = [str(path) for path in curves.values() if path.exists()]
    if report_path.exists():
        parser.error(f"assumption study is already complete: {report_path}")
    if args.resume:
        conflicts = []
    if conflicts:
        parser.error("refusing to overwrite existing assumption-study output(s): " + ", ".join(conflicts))

    prepare_pyaedt_environment()
    from ansys.aedt.core import Hfss

    version = args.version or preferred_aedt_version()
    target_designs = [item["design"] for item in variants.values()]
    desktop = None
    hfss = None
    results: dict[str, object] = {}
    try:
        desktop, project = _preflight_existing_desktop(
            version=version,
            port=args.grpc_port,
            active_project=args.active_project,
            target_designs=[] if args.resume else target_designs,
        )
        first_key = next(iter(variants))
        existing_designs = list(desktop.design_list(project))
        existing_targets = [name for name in target_designs if name in existing_designs]
        preexisting_targets = set(existing_targets)
        if existing_targets and not args.resume:
            raise RuntimeError(
                "assumption-study design(s) already exist; inspect them, then use --resume: "
                + ", ".join(existing_targets)
            )
        first_design = existing_targets[0] if existing_targets else variants[first_key]["design"]
        with temporary_grpc_session_probe(), temporary_multi_desktop():
            hfss = Hfss(
                project=project,
                design=first_design,
                solution_type="Modal",
                version=version,
                non_graphical=False,
                new_desktop=False,
                close_on_exit=False,
                port=args.grpc_port,
            )
        ensure_strict_existing_attachment(hfss, args.grpc_port)

        for key, configuration in variants.items():
            design = configuration["design"]
            if _reuse_preexisting_variant(design, preexisting_targets, args.resume):
                activated = hfss.set_active_design(design)
                if not activated or str(hfss.design_name) != design:
                    raise RuntimeError(f"HFSS could not activate existing study design {design}")
                if _is_exact_empty_design(hfss):
                    build_variant(hfss, key, case=args.case)
                    if not hfss.save_project():
                        raise RuntimeError(f"HFSS failed to save {design}")
                else:
                    _verify_existing_variant(hfss, key, case=args.case)
            else:
                if str(hfss.design_name) != design:
                    hfss.insert_design(design, solution_type="Modal")
                    if str(hfss.design_name) != design:
                        raise RuntimeError(f"AEDT inserted {hfss.design_name!r}, expected {design!r}")
                build_variant(hfss, key, case=args.case)
                if not hfss.save_project():
                    raise RuntimeError(f"HFSS failed to save {design}")
            if curves[key].exists():
                expression = "reused_verified_csv"
            else:
                if not hfss.analyze_setup("Setup1"):
                    raise RuntimeError(f"HFSS failed to solve Setup1 in {design}")
                expression = _export_s11(hfss, curves[key])
            paper = validate_paper_targets({args.case: curves[key]})["cases"][args.case]
            results[key] = {
                **configuration,
                "expression": expression,
                "curve": str(curves[key]),
                "curve_sha256": hashlib.sha256(curves[key].read_bytes()).hexdigest(),
                "paper_target": paper,
            }
        if not hfss.save_project():
            raise RuntimeError("HFSS failed to save the solved assumption-study designs")

        passed = [key for key, result in results.items() if result["paper_target"]["passed"]]
        report = {
            "schema_version": "1.0",
            "case": (
                "yeo_2019_conventional_inset_patch"
                if args.case == "conventional"
                else "yeo_2019_scaled_slot_loaded_patch"
            ),
            "scope": "engineering_assumption_study_only",
            "held_fixed": "all paper-explicit geometry and RF-35 properties",
            "varied": ["conductor representation", "HFSS excitation implementation"],
            "results": results,
            "paper_gate_passed_variants": passed,
            "status": "passed_variant_found" if passed else "no_variant_passed",
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        report["report"] = str(report_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if hfss is not None:
            hfss.release_desktop(close_projects=False, close_desktop=False)
        elif desktop is not None:
            _release_desktop_only(desktop)


def _verify_existing_variant(hfss, variant: str, case: str = "conventional") -> None:
    configuration = VARIANTS_BY_CASE[case][variant]
    expected_objects = {"RF35_Substrate", "Ground", "PatchFeed", "Region"}
    expected_boundaries = {"Radiation"}
    if configuration["port_model"] == "internal_lumped_port":
        expected_objects.add("LumpedPortSheet")
        expected_boundaries.add("LumpedPort1")
    else:
        expected_boundaries.add("WavePort1")
    if configuration["conductor_model"] == "zero_thickness_pec":
        expected_boundaries.update({"GroundPEC", "PatchFeedPEC"})

    actual_objects = set(hfss.modeler.object_names)
    actual_boundaries = {str(boundary.name) for boundary in hfss.boundaries}
    actual_setups = set(hfss.setup_names)
    actual_sweeps = set(hfss.existing_analysis_sweeps)
    issues = []
    if actual_objects != expected_objects:
        issues.append(f"objects={sorted(actual_objects)!r}, expected={sorted(expected_objects)!r}")
    if actual_boundaries != expected_boundaries:
        issues.append(
            f"boundaries={sorted(actual_boundaries)!r}, expected={sorted(expected_boundaries)!r}"
        )
    if actual_setups != {"Setup1"}:
        issues.append(f"setups={sorted(actual_setups)!r}, expected=['Setup1']")
    if "Setup1 : Sweep1" not in actual_sweeps:
        issues.append(f"analysis_sweeps={sorted(actual_sweeps)!r}")
    if issues:
        raise RuntimeError(
            f"refusing to resume structurally mismatched study design {hfss.design_name!r}: "
            + "; ".join(issues)
        )


def _reuse_preexisting_variant(
    design: str, preexisting_targets: set[str], resume: bool
) -> bool:
    """Distinguish a preflight design from one auto-inserted by Hfss attach."""
    if design not in preexisting_targets:
        return False
    if not resume:
        raise RuntimeError(
            f"refusing to reuse existing study design without --resume: {design}"
        )
    return True


def _is_exact_empty_design(hfss) -> bool:
    return not (
        list(hfss.modeler.object_names)
        or list(getattr(hfss, "boundaries", []))
        or list(getattr(hfss, "setup_names", []))
    )


if __name__ == "__main__":
    raise SystemExit(main())
