from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from antenna_mcp.aedt_runtime import (
    aedt_grpc_session_is_active,
    ensure_strict_existing_attachment,
    prepare_pyaedt_environment,
    temporary_multi_desktop,
    temporary_grpc_session_probe,
)
from antenna_mcp.discovery import preferred_aedt_version

from reference_model import build_reference


def _frequency_to_ghz(value: object) -> float:
    text = str(value).strip().lower()
    for suffix, factor in (("ghz", 1.0), ("mhz", 1e-3), ("khz", 1e-6), ("hz", 1e-9)):
        if text.endswith(suffix):
            return float(text[: -len(suffix)]) * factor
    return float(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the frozen official probe-patch reference.")
    default_dir = Path(__file__).resolve().parent / "local_results"
    parser.add_argument("--project", type=Path, default=default_dir / "official_probe_patch.aedt")
    parser.add_argument("--s11", type=Path, default=default_dir / "reference_s11.csv")
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
    parser.add_argument("--solve", action="store_true", help="Solve Setup1 and export S11.")
    parser.add_argument("--non-graphical", action="store_true")
    args = parser.parse_args(argv)

    project = args.project.expanduser().resolve()
    s11_file = args.s11.expanduser().resolve()
    if args.active_project and args.grpc_port is None:
        parser.error("--active-project requires --grpc-port")
    if not args.active_project and project.exists():
        parser.error(f"refusing to overwrite existing reference project: {project}")
    if not args.active_project:
        project.parent.mkdir(parents=True, exist_ok=True)
    if args.solve:
        s11_file.parent.mkdir(parents=True, exist_ok=True)
        if s11_file.exists():
            parser.error(f"refusing to overwrite existing reference curve: {s11_file}")

    prepare_pyaedt_environment()
    from ansys.aedt.core import Hfss

    hfss = None
    try:
        if args.grpc_port is not None and not 1 <= args.grpc_port <= 65535:
            parser.error("--grpc-port must be between 1 and 65535")
        options = {
            "project": args.active_project or str(project),
            "design": "OfficialProbeFedPatch",
            "solution_type": "Terminal",
            "version": args.version or preferred_aedt_version(),
            "close_on_exit": False,
        }
        if args.grpc_port is not None:
            if not aedt_grpc_session_is_active(args.grpc_port, "127.0.0.1"):
                raise RuntimeError(
                    f"no active AEDT gRPC session is available on port {args.grpc_port}; "
                    "refusing to launch a fallback session"
                )
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
        if args.active_project and str(hfss.project_name).casefold() != args.active_project.casefold():
            raise RuntimeError(
                f"attached project is {hfss.project_name!r}, expected {args.active_project!r}"
            )
        if list(hfss.modeler.object_names):
            raise RuntimeError("reference design must be empty before construction")
        build_reference(hfss)
        saved = hfss.save_project() if args.active_project else hfss.save_project(str(project))
        if not saved:
            raise RuntimeError("HFSS failed to save the reference project")

        result = {
            "status": "built",
            "project": str(getattr(hfss, "project_file", project)),
            "aedt_version": str(getattr(hfss, "aedt_version_id", "")),
            "solved": False,
            "s11": None,
        }
        if args.solve:
            if not hfss.analyze_setup("Setup1"):
                raise RuntimeError("HFSS failed to solve Setup1")
            traces = hfss.get_traces_for_plot()
            if not traces:
                raise RuntimeError("HFSS returned no S-parameter traces")
            expression = traces[0]
            data = hfss.post.get_solution_data(
                expressions=expression,
                setup_sweep_name="Setup1 : Sweep1",
                primary_sweep_variable="Freq",
            )
            if data is None:
                raise RuntimeError("HFSS returned no S11 solution data")
            frequencies, values = data.get_expression_data(expression, formula="real")
            if len(frequencies) != len(values) or len(frequencies) < 3:
                raise RuntimeError("HFSS returned an invalid S11 curve")
            with s11_file.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["frequency_ghz", "s11_db"])
                writer.writerows(
                    (_frequency_to_ghz(frequency), float(value))
                    for frequency, value in zip(frequencies, values)
                )
            result.update(status="completed", solved=True, s11=str(s11_file))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        if hfss is not None:
            close = args.grpc_port is None
            hfss.release_desktop(close_projects=close, close_desktop=close)


if __name__ == "__main__":
    raise SystemExit(main())
