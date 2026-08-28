"""Export solved S11 from one explicitly selected open AEDT design.

This helper is attach-only: it never launches AEDT, solves, saves, or closes the
user's project. It also refuses ambiguous or non-self-reflection traces.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from antenna_mcp.aedt_runtime import (
    aedt_grpc_session_is_active,
    ensure_strict_existing_attachment,
    is_aedt_app_released,
    prepare_pyaedt_environment,
    temporary_multi_desktop,
    temporary_grpc_session_probe,
)
from antenna_mcp.discovery import preferred_aedt_version
from antenna_mcp.s11_export import export_s11_curve


def _attach_existing_hfss(
    *, port: int, expected_project: str, expected_design: str
) -> Any:
    prepare_pyaedt_environment()
    from ansys.aedt.core import Hfss
    if not aedt_grpc_session_is_active(port, "127.0.0.1"):
        raise RuntimeError(
            f"no active AEDT gRPC session is available on port {port}; "
            "refusing to launch a fallback session"
        )
    options: dict[str, object] = {
        "project": expected_project,
        "design": expected_design,
        "non_graphical": False,
        "new_desktop": False,
        "close_on_exit": False,
        "port": port,
    }
    version = preferred_aedt_version()
    if version:
        options["version"] = version
    with temporary_grpc_session_probe(), temporary_multi_desktop():
        hfss = Hfss(**options)
    ensure_strict_existing_attachment(hfss, port)
    actual_project = str(getattr(hfss, "project_name", ""))
    actual_design = str(getattr(hfss, "design_name", ""))
    if actual_project.casefold() != expected_project.casefold():
        raise RuntimeError(
            f"attached project is {actual_project!r}, expected {expected_project!r}"
        )
    if actual_design.casefold() != expected_design.casefold():
        raise RuntimeError(
            f"attached design is {actual_design!r}, expected {expected_design!r}"
        )
    return hfss


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the unique solved dB(S(i,i)) trace from an open AEDT design."
    )
    parser.add_argument("--grpc-port", type=int, required=True)
    parser.add_argument("--expect-project", required=True)
    parser.add_argument("--expect-design", required=True)
    parser.add_argument("--setup-sweep", default="Setup1 : Sweep1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.grpc_port <= 65535:
        parser.error("--grpc-port must be between 1 and 65535")
    if args.output.expanduser().resolve().exists():
        parser.error(f"refusing to overwrite existing S11 curve: {args.output}")

    hfss = None
    try:
        hfss = _attach_existing_hfss(
            port=args.grpc_port,
            expected_project=args.expect_project,
            expected_design=args.expect_design,
        )
        result = export_s11_curve(
            hfss,
            args.output,
            setup_sweep=args.setup_sweep,
        )
        output = Path(str(result["output"]))
        result.update(
            status="exported_read_only",
            project=str(getattr(hfss, "project_name", "")),
            design=str(getattr(hfss, "design_name", "")),
            sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        if hfss is not None and not is_aedt_app_released(hfss):
            try:
                hfss.release_desktop(close_projects=False, close_desktop=False)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
