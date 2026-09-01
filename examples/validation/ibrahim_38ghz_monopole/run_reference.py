from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

from antenna_mcp.aedt_runtime import (
    aedt_grpc_session_is_active,
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_grpc_session_probe,
    temporary_multi_desktop,
)
from antenna_mcp.assumption_search import collect_convergence_evidence
from antenna_mcp.discovery import preferred_aedt_version

from paper_targets import validate_paper_targets
from reference_model import DESIGN_NAME, build_reference


def _frequency_to_ghz(value: object, unit: object | None = None) -> float:
    text = str(value).strip().lower()
    for suffix, factor in (("ghz", 1.0), ("mhz", 1e-3), ("khz", 1e-6), ("hz", 1e-9)):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    normalized_unit = str(unit or "").strip().lower()
    factors = {"ghz": 1.0, "mhz": 1e-3, "khz": 1e-6, "hz": 1e-9}
    if normalized_unit not in factors:
        raise ValueError(f"unable to convert numeric frequency {value!r} with unit {unit!r} to GHz")
    return float(text) * factors[normalized_unit]


def _release_desktop_only(desktop: Any, *, close_fallback: bool = False) -> None:
    try:
        desktop.release_desktop(close_projects=close_fallback, close_on_exit=close_fallback)
    except Exception:
        pass


def _preflight_existing_desktop(
    *, version: str, port: int, active_project: str, reuse_existing_design: bool = False
) -> tuple[Any, str]:
    from ansys.aedt.core import Desktop

    if not aedt_grpc_session_is_active(port, "127.0.0.1"):
        raise RuntimeError(
            f"no active AEDT gRPC session is available on port {port}; "
            "refusing to launch a fallback session"
        )
    desktop = None
    try:
        with temporary_grpc_session_probe(), temporary_multi_desktop():
            desktop = Desktop(
                version=version,
                non_graphical=False,
                new_desktop=False,
                close_on_exit=False,
                port=port,
            )
        launched = getattr(desktop, "launched_by_pyaedt", None)
        actual_port = getattr(desktop, "port", None)
        if launched is not False or int(actual_port or 0) != port:
            _release_desktop_only(desktop, close_fallback=launched is True)
            desktop = None
            raise RuntimeError(
                "strict AEDT attachment failed or PyAEDT launched a fallback session; "
                f"expected existing port {port}, launched_by_pyaedt={launched!r}, "
                f"actual_port={actual_port!r}"
            )
        matching = [name for name in desktop.project_list if name.casefold() == active_project.casefold()]
        if not matching:
            raise RuntimeError(
                f"project {active_project!r} is not open; available projects: {desktop.project_list}"
            )
        actual_project = matching[0]
        existing_designs = desktop.design_list(actual_project)
        design_exists = DESIGN_NAME.casefold() in {
            name.casefold() for name in existing_designs
        }
        if reuse_existing_design and not design_exists:
            raise RuntimeError(
                f"cannot resume because solved design {DESIGN_NAME!r} does not exist"
            )
        if not reuse_existing_design and design_exists:
            raise RuntimeError(f"refusing to modify existing reference design: {DESIGN_NAME}")
        if desktop.active_project(actual_project) is None:
            raise RuntimeError(f"unable to activate project {actual_project!r}")
        return desktop, actual_project
    except Exception:
        if desktop is not None:
            _release_desktop_only(desktop)
        raise


def _is_db_self_reflection(expression: object) -> bool:
    match = re.fullmatch(
        r"\s*dB\s*\(\s*S\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)\s*\)\s*",
        str(expression),
        flags=re.IGNORECASE,
    )
    return bool(match and match.group(1).strip() == match.group(2).strip())


def _export_s11(hfss: Any, destination: Path) -> dict[str, Any]:
    traces = hfss.get_traces_for_plot(
        get_self_terms=True,
        get_mutual_terms=False,
        category="dB(S",
    )
    self_reflections = [trace for trace in traces if _is_db_self_reflection(trace)]
    if len(self_reflections) != 1:
        raise RuntimeError(f"expected exactly one dB self-reflection trace; got {traces!r}")
    expression = self_reflections[0]
    data = hfss.post.get_solution_data(
        expressions=expression,
        setup_sweep_name="Setup1 : Sweep1",
        primary_sweep_variable="Freq",
    )
    if data is None:
        raise RuntimeError("HFSS returned no S11 solution data")
    raw_frequencies, raw_values = data.get_expression_data(expression, formula="real")
    if len(raw_frequencies) != len(raw_values) or len(raw_frequencies) < 3:
        raise RuntimeError("HFSS returned an invalid S11 curve")
    primary_sweep = getattr(data, "primary_sweep", "Freq") or "Freq"
    sweep_units = getattr(data, "units_sweeps", {}) or {}
    frequency_unit = sweep_units.get(primary_sweep)
    points = [
        (_frequency_to_ghz(frequency, frequency_unit), float(value))
        for frequency, value in zip(raw_frequencies, raw_values)
    ]
    if not all(math.isfinite(item) for point in points for item in point):
        raise RuntimeError("HFSS returned non-finite S11 data")
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise RuntimeError("HFSS returned non-increasing S11 frequencies")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(points)
    return {
        "expression": expression,
        "point_count": len(points),
        "frequency_range_ghz": [points[0][0], points[-1][0]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and optionally solve one frozen paper-reference antenna."
    )
    default_dir = Path(__file__).resolve().parent / "local_results"
    parser.add_argument("--project", type=Path, default=default_dir / "ibrahim_38ghz_reference.aedt")
    parser.add_argument("--s11", type=Path, default=default_dir / "reference_s11.csv")
    parser.add_argument("--paper-report", type=Path, default=default_dir / "paper_target_report.json")
    parser.add_argument("--version", default=None, help="AEDT version, for example 2025.1.")
    parser.add_argument("--grpc-port", type=int, help="Attach strictly to an already open AEDT gRPC session.")
    parser.add_argument("--active-project", help="Use this already open project; valid only with --grpc-port.")
    parser.add_argument("--solve", action="store_true", help="Solve Setup1 and export/evaluate S11.")
    parser.add_argument(
        "--reuse-existing-solved-design",
        action="store_true",
        help=(
            "Attach to the existing same-name design and only export/evaluate its "
            "already solved Setup1 : Sweep1. Used to resume after a postprocessing failure."
        ),
    )
    parser.add_argument("--non-graphical", action="store_true")
    args = parser.parse_args(argv)

    if args.active_project and args.grpc_port is None:
        parser.error("--active-project requires --grpc-port")
    if args.reuse_existing_solved_design and (
        args.grpc_port is None or not args.active_project
    ):
        parser.error("--reuse-existing-solved-design requires --grpc-port and --active-project")
    if args.reuse_existing_solved_design and args.solve:
        parser.error("--reuse-existing-solved-design cannot be combined with --solve")
    if args.grpc_port is not None and not 1 <= args.grpc_port <= 65535:
        parser.error("--grpc-port must be between 1 and 65535")
    project = args.project.expanduser().resolve()
    s11_file = args.s11.expanduser().resolve()
    paper_report_file = args.paper_report.expanduser().resolve()
    if not args.active_project and project.exists():
        parser.error(f"refusing to overwrite existing reference project: {project}")
    if not args.active_project:
        project.parent.mkdir(parents=True, exist_ok=True)
    should_export = args.solve or args.reuse_existing_solved_design
    if should_export and (s11_file.exists() or paper_report_file.exists()):
        parser.error("refusing to overwrite an existing S11 curve or paper-target report")

    prepare_pyaedt_environment()
    from ansys.aedt.core import Hfss

    version = args.version or preferred_aedt_version()
    desktop = None
    hfss = None
    attached_project = args.active_project
    try:
        if args.grpc_port is not None:
            desktop, attached_project = _preflight_existing_desktop(
                version=version,
                port=args.grpc_port,
                active_project=args.active_project,
                reuse_existing_design=args.reuse_existing_solved_design,
            )
            _release_desktop_only(desktop)
            desktop = None
        options = {
            "project": attached_project or str(project),
            "design": DESIGN_NAME,
            "solution_type": "Modal",
            "version": version,
            "close_on_exit": False,
        }
        if args.grpc_port is not None:
            with temporary_grpc_session_probe(), temporary_multi_desktop():
                hfss = Hfss(non_graphical=False, new_desktop=False, port=args.grpc_port, **options)
            ensure_strict_existing_attachment(hfss, args.grpc_port)
        else:
            hfss = Hfss(non_graphical=args.non_graphical, new_desktop=True, **options)
        if getattr(hfss, "odesign", None) is None:
            raise RuntimeError("AEDT did not create an active HFSS design")
        if attached_project and str(hfss.project_name).casefold() != attached_project.casefold():
            raise RuntimeError(f"attached project is {hfss.project_name!r}, expected {attached_project!r}")
        if str(hfss.design_name) != DESIGN_NAME:
            raise RuntimeError(f"AEDT activated design {hfss.design_name!r}, expected {DESIGN_NAME!r}")

        if not args.reuse_existing_solved_design:
            build_reference(hfss)
            saved = hfss.save_project() if attached_project else hfss.save_project(str(project))
            if not saved:
                raise RuntimeError("HFSS failed to save the paper-reference project")
        else:
            sweeps = list(getattr(hfss, "existing_analysis_sweeps", []))
            if "Setup1 : Sweep1" not in sweeps:
                raise RuntimeError(
                    "existing design does not contain the solved Setup1 : Sweep1; "
                    f"available sweeps: {sweeps!r}"
                )
        result: dict[str, Any] = {
            "status": "built",
            "project": str(getattr(hfss, "project_file", project)),
            "design": DESIGN_NAME,
            "aedt_version": str(getattr(hfss, "aedt_version_id", "")),
            "solved": False,
            "s11": None,
            "paper_target_report": None,
            "paper_target_passed": None,
            "reused_existing_solved_design": args.reuse_existing_solved_design,
        }
        exit_code = 0
        if should_export:
            if args.solve:
                if not hfss.analyze_setup("Setup1"):
                    raise RuntimeError("HFSS failed to solve Setup1")
            convergence = collect_convergence_evidence(
                hfss, setup_name="Setup1", sweep_name="Sweep1", max_delta_s=0.02
            )
            curve_metrics = _export_s11(hfss, s11_file)
            paper_report = validate_paper_targets(s11_file)
            paper_metrics_passed = bool(paper_report["passed"])
            paper_report["paper_metrics_passed"] = paper_metrics_passed
            paper_report["solver_convergence"] = convergence
            paper_report["passed"] = paper_metrics_passed and convergence["converged"]
            paper_report["status"] = "passed" if paper_report["passed"] else "failed"
            paper_report_file.parent.mkdir(parents=True, exist_ok=True)
            with paper_report_file.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(paper_report, ensure_ascii=False, indent=2) + "\n")
            result.update(
                status="completed" if paper_report["passed"] else "failed_paper_target",
                solved=True,
                s11=str(s11_file),
                paper_target_report=str(paper_report_file),
                paper_target_passed=paper_report["passed"],
                paper_observed=paper_report["observed"],
                convergence=convergence,
                **curve_metrics,
            )
            if not args.reuse_existing_solved_design and not hfss.save_project():
                raise RuntimeError("HFSS failed to save the solved reference results")
            exit_code = 0 if paper_report["passed"] else 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code
    finally:
        if hfss is not None:
            close = args.grpc_port is None
            hfss.release_desktop(close_projects=close, close_desktop=close)
        elif desktop is not None:
            _release_desktop_only(desktop)


if __name__ == "__main__":
    raise SystemExit(main())
