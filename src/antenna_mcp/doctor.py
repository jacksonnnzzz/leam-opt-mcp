from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .aedt_runtime import planned_transport
from .discovery import discover_aedt_installations
from .workspace import WorkspaceStore


def build_report(
    *,
    workspace: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a credential-safe installation and configuration report."""

    env = dict(os.environ if environment is None else environment)
    store = WorkspaceStore(workspace or env.get("ANTENNA_MCP_WORKSPACE"))
    text_provider = env.get("ANTENNA_TEXT_PROVIDER", "openai").strip().lower()
    vision_provider = env.get("ANTENNA_VISION_PROVIDER", "openai").strip().lower()
    issues: list[dict[str, str]] = []

    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        issues.append(
            {
                "level": "error",
                "message": "Python 3.10 through 3.13 is required.",
            }
        )

    if text_provider not in {"openai", "deepseek", "ollama"}:
        issues.append({"level": "error", "message": f"Unsupported text provider: {text_provider}"})
    if vision_provider not in {"openai", "ollama"}:
        issues.append(
            {
                "level": "error",
                "message": "Vision provider must be openai or ollama for image/PDF input.",
            }
        )
    required_key = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}.get(
        text_provider
    )
    if required_key and not env.get(required_key):
        issues.append(
            {
                "level": "warning",
                "message": f"{required_key} is not set; cloud text generation is unavailable.",
            }
        )
    if vision_provider == "openai" and not env.get("OPENAI_API_KEY"):
        issues.append(
            {
                "level": "warning",
                "message": "OPENAI_API_KEY is not set; choose Ollama or configure cloud vision.",
            }
        )
    if vision_provider == "ollama" and importlib.util.find_spec("fitz") is None:
        issues.append(
            {
                "level": "warning",
                "message": "PyMuPDF is not installed; PDF input needs the local-vision extra.",
            }
        )

    installations = discover_aedt_installations(env)
    if not installations:
        issues.append(
            {
                "level": "info",
                "message": "AEDT was not discovered; offline Python generation still works.",
            }
        )

    transport: dict[str, Any] | None
    try:
        transport = planned_transport(env)
    except ValueError as exc:
        transport = None
        issues.append({"level": "error", "message": str(exc)})

    errors = [issue for issue in issues if issue["level"] == "error"]
    warnings = [issue for issue in issues if issue["level"] == "warning"]
    return {
        "status": "error" if errors else ("needs_configuration" if warnings else "ready"),
        "package_version": __version__,
        "python": {
            "version": platform_python_version(),
            "supported": (3, 10) <= sys.version_info[:2] < (3, 14),
        },
        "workspace": str(store.root),
        "providers": {
            "text": text_provider,
            "vision": vision_provider,
            "deepseek_key_configured": bool(env.get("DEEPSEEK_API_KEY")),
            "openai_key_configured": bool(env.get("OPENAI_API_KEY")),
            "ollama_base_url": env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            "ollama_vision_model": env.get("OLLAMA_VISION_MODEL", "qwen3-vl:8b"),
        },
        "optional_dependencies": {
            "pyaedt": _module_available("ansys.aedt.core"),
            "pymupdf": _module_available("fitz"),
        },
        "aedt": {
            "installations": installations,
            "transport": transport,
            "execution_enabled": env.get("ANTENNA_MCP_ALLOW_SIMULATION") == "1",
        },
        "issues": issues,
    }


def platform_python_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect LEAM Opt MCP configuration without printing credential values."
    )
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit code for errors or missing provider configuration.",
    )
    args = parser.parse_args(argv)
    report = build_report(workspace=args.workspace)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and report["status"] != "ready":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
