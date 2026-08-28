"""Safely build/solve selected Kaur 2021 HFSS reference cases."""

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
    temporary_multi_desktop,
    temporary_grpc_session_probe,
)
from antenna_mcp.discovery import preferred_aedt_version

from paper_targets import evaluate_paper_targets
from reference_model import DESIGN_NAMES, build_reference


def _selected_cases(raw: str) -> list[str]:
    return list(DESIGN_NAMES) if raw == "all" else [raw]


def _frequency_to_ghz(value: object, unit: object | None = None) -> float:
    text = str(value).strip().lower()
    for suffix, factor in (("ghz", 1.0), ("mhz", 1e-3), ("khz", 1e-6), ("hz", 1e-9)):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    factor = {"ghz": 1.0, "mhz": 1e-3, "khz": 1e-6, "hz": 1e-9}.get(
        str(unit or "").strip().lower()
    )
    if factor is None:
        raise ValueError(f"unable to convert numeric frequency {value!r} with unit {unit!r} to GHz")
    return float(text) * factor


def _is_db_self_reflection(expression: object) -> bool:
    match = re.fullmatch(
        r"\s*dB\s*\(\s*S\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*\)\s*\)\s*",
        str(expression), flags=re.IGNORECASE,
    )
    return bool(match and match.group(1).strip() == match.group(2).strip())


def _export_s11(hfss: Any, destination: Path) -> dict[str, Any]:
    traces = hfss.get_traces_for_plot(
        get_self_terms=True, get_mutual_terms=False, category="dB(S"
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
    raw_frequency, raw_values = data.get_expression_data(expression, formula="real")
    if len(raw_frequency) != len(raw_values) or len(raw_frequency) < 3:
        raise RuntimeError("HFSS returned an invalid S11 curve")
    primary = getattr(data, "primary_sweep", "Freq") or "Freq"
    unit = (getattr(data, "units_sweeps", {}) or {}).get(primary)
    points = [
        (_frequency_to_ghz(frequency, unit), float(value))
        for frequency, value in zip(raw_frequency, raw_values)
    ]
    if not all(math.isfinite(item) for point in points for item in point):
        raise RuntimeError("HFSS returned non-finite S11 data")
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise RuntimeError("HFSS returned non-increasing S11 frequencies")
    if points[0][0] > 3.0 or points[-1][0] < 12.0:
        raise RuntimeError("HFSS S11 curve does not cover the paper's 3-12 GHz range")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(points)
    return {"expression": expression, "point_count": len(points), "path": str(destination)}


def _release_desktop_only(desktop: Any, *, close_fallback: bool = False) -> None:
    try:
        desktop.release_desktop(close_projects=close_fallback, close_on_exit=close_fallback)
    except Exception:
        pass


def _preflight_existing_desktop(
    *, version: str, port: int, active_project: str, target_designs: list[str]
) -> tuple[Any, str]:
    from ansys.aedt.core import Desktop
    if not aedt_grpc_session_is_active(port, "127.0.0.1"):
        raise RuntimeError(f"no active AEDT gRPC session on port {port}; refusing fallback launch")
    desktop = None
    try:
        with temporary_grpc_session_probe(), temporary_multi_desktop():
            desktop = Desktop(
                version=version, non_graphical=False, new_desktop=False,
                close_on_exit=False, port=port,
            )
        launched = getattr(desktop, "launched_by_pyaedt", None)
        actual_port = int(getattr(desktop, "port", 0) or 0)
        if launched is not False or actual_port != port:
            _release_desktop_only(desktop, close_fallback=launched is True)
            desktop = None
            raise RuntimeError("strict AEDT attachment failed or launched a fallback session")
        matching = [name for name in desktop.project_list if name.casefold() == active_project.casefold()]
        if not matching:
            raise RuntimeError(f"project {active_project!r} is not open; available: {desktop.project_list}")
        actual_project = matching[0]
        existing = {name.casefold() for name in desktop.design_list(actual_project)}
        conflicts = [name for name in target_designs if name.casefold() in existing]
        if conflicts:
            raise RuntimeError("refusing to modify existing design(s): " + ", ".join(conflicts))
        if desktop.active_project(actual_project) is None:
            raise RuntimeError(f"unable to activate project {actual_project!r}")
        return desktop, actual_project
    except Exception:
        if desktop is not None:
            _release_desktop_only(desktop)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the frozen Kaur 2021 HFSS cases.")
    default_dir = Path(__file__).resolve().parent / "local_results"
    parser.add_argument("--case", choices=["all", *DESIGN_NAMES], default="all")
    parser.add_argument("--project", type=Path, default=default_dir / "kaur_2021_uwb.aedt")
    parser.add_argument("--results-dir", type=Path, default=default_dir)
    parser.add_argument("--version", default=None)
    parser.add_argument("--grpc-port", type=int)
    parser.add_argument("--active-project")
    parser.add_argument("--solve", action="store_true")
    parser.add_argument("--non-graphical", action="store_true")
    args = parser.parse_args(argv)
    if args.active_project and args.grpc_port is None:
        parser.error("--active-project requires --grpc-port")
    if args.grpc_port is not None and not 1 <= args.grpc_port <= 65535:
        parser.error("--grpc-port must be between 1 and 65535")

    cases = _selected_cases(args.case)
    target_designs = [DESIGN_NAMES[case] for case in cases]
    project = args.project.expanduser().resolve()
    results_dir = args.results_dir.expanduser().resolve()
    s11_paths = {case: results_dir / f"reference_{case}_s11.csv" for case in cases}
    report_paths = {case: results_dir / f"paper_target_{case}.json" for case in cases}
    if not args.active_project and project.exists():
        parser.error(f"refusing to overwrite existing reference project: {project}")
    conflicts = [str(path) for path in [*s11_paths.values(), *report_paths.values()] if args.solve and path.exists()]
    if conflicts:
        parser.error("refusing to overwrite existing result file(s): " + ", ".join(conflicts))
    if not args.active_project:
        project.parent.mkdir(parents=True, exist_ok=True)

    prepare_pyaedt_environment()
    from ansys.aedt.core import Hfss

    version = args.version or preferred_aedt_version()
    desktop = hfss = None
    attached_project = args.active_project
    try:
        if args.grpc_port is not None:
            desktop, attached_project = _preflight_existing_desktop(
                version=version, port=args.grpc_port,
                active_project=args.active_project, target_designs=target_designs,
            )
        options = dict(
            project=attached_project or str(project), design=target_designs[0],
            solution_type="Modal", version=version, close_on_exit=False,
        )
        if args.grpc_port is not None:
            with temporary_grpc_session_probe(), temporary_multi_desktop():
                hfss = Hfss(non_graphical=False, new_desktop=False, port=args.grpc_port, **options)
            ensure_strict_existing_attachment(hfss, args.grpc_port)
        else:
            hfss = Hfss(non_graphical=args.non_graphical, new_desktop=True, **options)
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

        records: dict[str, Any] = {}
        for index, case in enumerate(cases):
            name = DESIGN_NAMES[case]
            if index:
                if name.casefold() in {item.casefold() for item in hfss.design_list}:
                    raise RuntimeError(f"refusing to modify existing design: {name}")
                hfss.insert_design(name, solution_type="Modal")
                if str(hfss.design_name) != name:
                    raise RuntimeError(f"HFSS inserted {hfss.design_name!r}, expected {name!r}")
            build_reference(hfss, case)
            records[case] = {"design": name, "status": "built", "solved": False}
        saved = hfss.save_project() if attached_project else hfss.save_project(str(project))
        if not saved:
            raise RuntimeError("HFSS failed to save the Kaur reference project")

        exit_code = 0
        if args.solve:
            for case in cases:
                name = DESIGN_NAMES[case]
                if not hfss.set_active_design(name) or str(hfss.design_name) != name:
                    raise RuntimeError(f"HFSS could not activate {name}")
                if not hfss.analyze_setup("Setup1"):
                    raise RuntimeError(f"HFSS failed to solve Setup1 in {name}")
                export = _export_s11(hfss, s11_paths[case])
                report = evaluate_paper_targets(s11_paths[case], case)
                report_paths[case].parent.mkdir(parents=True, exist_ok=True)
                with report_paths[case].open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                records[case].update(
                    status="completed" if report["passed"] else "failed_paper_target",
                    solved=True, s11=export, paper_report=str(report_paths[case]),
                )
                if not report["passed"]:
                    exit_code = 2
            if not hfss.save_project():
                raise RuntimeError("HFSS failed to save solved reference results")
        print(json.dumps({"status": "completed" if args.solve else "built", "project": str(getattr(hfss, "project_file", project)), "cases": records}, ensure_ascii=False, indent=2))
        return exit_code
    finally:
        if hfss is not None:
            close = args.grpc_port is None
            hfss.release_desktop(close_projects=close, close_desktop=close)
        elif desktop is not None:
            _release_desktop_only(desktop)


if __name__ == "__main__":
    raise SystemExit(main())
