"""Apply an offline generated antenna model to the active AEDT/HFSS design.

This helper never saves or solves the project.  It refuses to start when no
``ansysedt`` process exists and can verify the active project/design before the
generated ``build(hfss)`` function is called.
"""

from __future__ import annotations

import argparse
import ast
import json
import runpy
from pathlib import Path
from typing import Any

from antenna_mcp.aedt_runtime import prepare_pyaedt_environment
from antenna_mcp.discovery import preferred_aedt_version
from antenna_mcp.modeling import validate_generated_python


def load_generated_model(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.is_file() or path.suffix.casefold() != ".py":
        raise ValueError(f"generated model must be an existing .py file: {path}")
    source = path.read_text(encoding="utf-8")
    validate_generated_python(source)
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.FunctionDef)):
            continue
        raise ValueError(f"generated model contains executable top-level code: {type(node).__name__}")
    namespace = runpy.run_path(str(path))
    if not callable(namespace.get("build")):
        raise ValueError("generated model must define build(hfss)")
    return namespace


def apply_generated_model(path: Path, hfss: Any) -> dict[str, Any]:
    namespace = load_generated_model(path)
    namespace["build"](hfss)
    return {
        "case_id": namespace.get("CASE_ID"),
        "figure": namespace.get("FIGURE"),
        "project": getattr(hfss, "project_name", None),
        "design": getattr(hfss, "design_name", None),
        "status": "built_unsaved",
    }


def _existing_aedt_pids() -> list[int]:
    try:
        import psutil
    except ImportError as exc:
        raise RuntimeError("psutil is required to verify that AEDT is already open") from exc
    pids = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if "ansysedt" in str(process.info["name"]).casefold():
                pids.append(int(process.info["pid"]))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return sorted(pids)


def _attach_active_hfss() -> Any:
    if not _existing_aedt_pids():
        raise RuntimeError("AEDT is not open. Open the target HFSS project and design first.")
    prepare_pyaedt_environment()
    try:
        from ansys.aedt.core import Hfss
    except ImportError as exc:
        raise RuntimeError("PyAEDT is not installed in this Python environment") from exc
    options = {
        "project": None,
        "design": None,
        "non_graphical": False,
        "new_desktop": False,
        "close_on_exit": False,
    }
    version = preferred_aedt_version()
    if version:
        options["version"] = version
    return Hfss(**options)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply generated_model_vNNN.py to the active HFSS design without saving or solving."
    )
    parser.add_argument("model_file", type=Path)
    parser.add_argument("--expect-project", help="Abort unless this project is active, for example Project5.")
    parser.add_argument("--expect-design", help="Abort unless this HFSS design is active.")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the Python artifact without connecting to AEDT.",
    )
    args = parser.parse_args()

    namespace = load_generated_model(args.model_file)
    if args.validate_only:
        print(
            json.dumps(
                {
                    "status": "validated_offline",
                    "case_id": namespace.get("CASE_ID"),
                    "figure": namespace.get("FIGURE"),
                    "model_file": str(args.model_file.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    hfss = _attach_active_hfss()
    project = str(getattr(hfss, "project_name", ""))
    design = str(getattr(hfss, "design_name", ""))
    if args.expect_project and project.casefold() != args.expect_project.casefold():
        raise RuntimeError(f"active project is {project!r}, expected {args.expect_project!r}; no geometry was created")
    if args.expect_design and design.casefold() != args.expect_design.casefold():
        raise RuntimeError(f"active design is {design!r}, expected {args.expect_design!r}; no geometry was created")

    result = apply_generated_model(args.model_file, hfss)
    result["instruction"] = "Inspect the geometry in AEDT, then use Save As manually if it is acceptable."
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
