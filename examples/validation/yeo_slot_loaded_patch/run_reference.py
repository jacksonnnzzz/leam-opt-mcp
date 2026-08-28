from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from antenna_mcp.aedt_runtime import (
    aedt_grpc_session_is_active,
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_multi_desktop,
    temporary_grpc_session_probe,
)
from antenna_mcp.discovery import preferred_aedt_version

from paper_targets import validate_paper_targets
from reference_model import DESIGN_NAMES, build_reference


def _frequency_to_ghz(value: object, unit: object | None = None) -> float:
    text = str(value).strip().lower()
    for suffix, factor in (("ghz", 1.0), ("mhz", 1e-3), ("khz", 1e-6), ("hz", 1e-9)):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    normalized_unit = str(unit or "").strip().lower()
    factors = {"ghz": 1.0, "mhz": 1e-3, "khz": 1e-6, "hz": 1e-9}
    if normalized_unit not in factors:
        raise ValueError(
            f"unable to convert numeric frequency {value!r} with unit {unit!r} to GHz"
        )
    return float(text) * factors[normalized_unit]


def _selected_cases(raw: str) -> list[str]:
    if raw == "all":
        return ["conventional", "scaled_slot_loaded"]
    return [raw]


def _release_desktop_only(desktop: Any, *, close_fallback: bool = False) -> None:
    try:
        desktop.release_desktop(
            close_projects=close_fallback,
            close_on_exit=close_fallback,
        )
    except Exception:
        pass


def _preflight_existing_desktop(
    *, version: str, port: int, active_project: str, target_designs: list[str]
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
        matching_projects = [
            name for name in desktop.project_list if name.casefold() == active_project.casefold()
        ]
        if not matching_projects:
            raise RuntimeError(
                f"project {active_project!r} is not open; available projects: "
                f"{desktop.project_list}"
            )
        actual_project = matching_projects[0]
        existing = set(desktop.design_list(actual_project))
        conflicts = sorted(existing.intersection(target_designs))
        if conflicts:
            raise RuntimeError(
                "refusing to modify existing reference design(s): " + ", ".join(conflicts)
            )
        if desktop.active_project(actual_project) is None:
            raise RuntimeError(f"unable to activate project {actual_project!r}")
        return desktop, actual_project
    except Exception:
        if desktop is not None:
            _release_desktop_only(desktop)
        raise


def _export_s11(hfss: Any, destination: Path) -> str:
    traces = hfss.get_traces_for_plot(
        get_self_terms=True,
        get_mutual_terms=False,
        category="dB(S",
    )
    self_reflections = [trace for trace in traces if _is_db_self_reflection(trace)]
    if len(self_reflections) != 1:
        raise RuntimeError(
            f"expected exactly one dB self-reflection trace for {hfss.design_name}; "
            f"got {traces!r}"
        )
    expression = self_reflections[0]
    data = hfss.post.get_solution_data(
        expressions=expression,
        setup_sweep_name="Setup1 : Sweep1",
        primary_sweep_variable="Freq",
    )
    if data is None:
        raise RuntimeError(f"HFSS returned no S11 solution data for {hfss.design_name}")
    frequencies, values = data.get_expression_data(expression, formula="real")
    if len(frequencies) != len(values) or len(frequencies) < 3:
        raise RuntimeError(f"HFSS returned an invalid S11 curve for {hfss.design_name}")
    primary_sweep = getattr(data, "primary_sweep", "Freq")
    frequency_unit = getattr(data, "units_sweeps", {}).get(primary_sweep)
    converted_frequencies = [
        _frequency_to_ghz(frequency, frequency_unit) for frequency in frequencies
    ]
    converted_values = [float(value) for value in values]
    if not all(math.isfinite(value) for value in [*converted_frequencies, *converted_values]):
        raise RuntimeError(f"HFSS returned non-finite S11 data for {hfss.design_name}")
    if any(
        right <= left
        for left, right in zip(converted_frequencies, converted_frequencies[1:])
    ):
        raise RuntimeError(f"HFSS returned non-increasing S11 frequencies for {hfss.design_name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(zip(converted_frequencies, converted_values))
    return expression


def _is_db_self_reflection(expression: object) -> bool:
    text = str(expression).strip()
    if not text.startswith("dB(S(") or not text.endswith("))"):
        return False
    terms = text[len("dB(S(") : -2].split(",")
    return len(terms) == 2 and terms[0].strip() == terms[1].strip() != ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the two frozen Yeo 2019 HFSS translation references."
    )
    default_dir = Path(__file__).resolve().parent / "local_results"
    parser.add_argument(
        "--case",
        choices=["all", *DESIGN_NAMES],
        default="all",
        help="Build both paper cases by default, or select one case.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=default_dir / "yeo_2019_patch_references.aedt",
    )
    parser.add_argument(
        "--conventional-s11",
        type=Path,
        default=default_dir / "reference_conventional_s11.csv",
    )
    parser.add_argument(
        "--scaled-s11",
        type=Path,
        default=default_dir / "reference_scaled_slot_loaded_s11.csv",
    )
    parser.add_argument(
        "--paper-report",
        type=Path,
        default=default_dir / "paper_target_report.json",
        help="Machine-readable comparison of solved curves with explicit paper targets.",
    )
    parser.add_argument("--version", default=None, help="AEDT version, for example 2025.1.")
    parser.add_argument(
        "--grpc-port",
        type=int,
        help="Attach strictly to an already open AEDT gRPC session instead of launching one.",
    )
    parser.add_argument(
        "--active-project",
        help="Use this already open project name; valid only with --grpc-port.",
    )
    parser.add_argument("--solve", action="store_true", help="Solve and export selected S11 curves.")
    parser.add_argument("--non-graphical", action="store_true")
    args = parser.parse_args(argv)

    if args.active_project and args.grpc_port is None:
        parser.error("--active-project requires --grpc-port")
    if args.grpc_port is not None and not 1 <= args.grpc_port <= 65535:
        parser.error("--grpc-port must be between 1 and 65535")

    cases = _selected_cases(args.case)
    target_designs = [DESIGN_NAMES[case] for case in cases]
    project = args.project.expanduser().resolve()
    s11_paths = {
        "conventional": args.conventional_s11.expanduser().resolve(),
        "scaled_slot_loaded": args.scaled_s11.expanduser().resolve(),
    }
    paper_report_path = args.paper_report.expanduser().resolve()
    if not args.active_project and project.exists():
        parser.error(f"refusing to overwrite existing reference project: {project}")
    if not args.active_project:
        project.parent.mkdir(parents=True, exist_ok=True)
    if args.solve:
        conflicts = [str(s11_paths[case]) for case in cases if s11_paths[case].exists()]
        if paper_report_path.exists():
            conflicts.append(str(paper_report_path))
        if conflicts:
            parser.error("refusing to overwrite existing result file(s): " + ", ".join(conflicts))

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
                target_designs=target_designs,
            )

        options = {
            "project": attached_project or str(project),
            "design": target_designs[0],
            "solution_type": "Modal",
            "version": version,
            "close_on_exit": False,
        }
        if args.grpc_port is not None:
            with temporary_grpc_session_probe(), temporary_multi_desktop():
                hfss = Hfss(
                    non_graphical=False,
                    new_desktop=False,
                    port=args.grpc_port,
                    **options,
                )
            ensure_strict_existing_attachment(hfss, args.grpc_port)
        else:
            hfss = Hfss(
                non_graphical=args.non_graphical,
                new_desktop=True,
                **options,
            )
        if getattr(hfss, "odesign", None) is None:
            raise RuntimeError("AEDT did not create an active HFSS design")
        if attached_project and str(hfss.project_name).casefold() != attached_project.casefold():
            raise RuntimeError(
                f"attached project is {hfss.project_name!r}, expected {attached_project!r}"
            )
        if str(hfss.design_name) != target_designs[0]:
            raise RuntimeError(
                f"AEDT activated design {hfss.design_name!r}, expected {target_designs[0]!r}"
            )

        built: dict[str, dict[str, Any]] = {}
        for index, case in enumerate(cases):
            design_name = DESIGN_NAMES[case]
            if index:
                if design_name in hfss.design_list:
                    raise RuntimeError(f"refusing to modify existing design: {design_name}")
                hfss.insert_design(design_name, solution_type="Modal")
                if str(hfss.design_name) != design_name:
                    raise RuntimeError(
                        f"AEDT inserted unexpected design {hfss.design_name!r}; "
                        f"expected {design_name!r}"
                    )
            build_reference(hfss, case)
            built[case] = {
                "design": design_name,
                "status": "built",
                "solved": False,
                "s11": None,
            }

        saved = hfss.save_project() if attached_project else hfss.save_project(str(project))
        if not saved:
            raise RuntimeError("HFSS failed to save the Yeo reference project")

        if args.solve:
            for case in cases:
                design_name = DESIGN_NAMES[case]
                activated = hfss.set_active_design(design_name)
                if not activated or str(hfss.design_name) != design_name:
                    raise RuntimeError(f"HFSS could not activate {design_name}")
                if not hfss.analyze_setup("Setup1"):
                    raise RuntimeError(f"HFSS failed to solve Setup1 in {design_name}")
                expression = _export_s11(hfss, s11_paths[case])
                built[case].update(
                    status="completed",
                    solved=True,
                    s11=str(s11_paths[case]),
                    expression=expression,
                )
            if not hfss.save_project():
                raise RuntimeError("HFSS failed to save solved reference results")

            paper_report = validate_paper_targets(
                {case: s11_paths[case] for case in cases}
            )
            paper_report_path.parent.mkdir(parents=True, exist_ok=True)
            with paper_report_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(paper_report, ensure_ascii=False, indent=2) + "\n")
            for case in cases:
                built[case]["paper_target_status"] = paper_report["cases"][case]["status"]
            paper_report_record = {
                "status": paper_report["status"],
                "path": str(paper_report_path),
            }
        else:
            paper_report_record = None

        result = {
            "status": "completed" if args.solve else "built",
            "project": str(getattr(hfss, "project_file", project)),
            "aedt_version": str(getattr(hfss, "aedt_version_id", "")),
            "cases": built,
            "paper_targets": paper_report_record,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        if hfss is not None:
            close = args.grpc_port is None
            hfss.release_desktop(close_projects=close, close_desktop=close)
        elif desktop is not None:
            _release_desktop_only(desktop)


if __name__ == "__main__":
    raise SystemExit(main())
