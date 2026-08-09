from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping


def discover_aedt_installations(environment: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Discover AEDT roots, including custom installs advertised by ANSYSEM_ROOTxxx."""
    env = environment or os.environ
    candidates: dict[Path, str | None] = {}
    explicit_executable = env.get("ANTENNA_MCP_AEDT_EXECUTABLE")
    if explicit_executable:
        executable = Path(explicit_executable).expanduser()
        candidates[executable.parent] = _version_code_from_path(executable.parent)
    explicit_root = env.get("ANTENNA_MCP_AEDT_ROOT")
    if explicit_root:
        root = Path(explicit_root).expanduser()
        candidates[root] = _version_code_from_path(root)
    for key, raw in env.items():
        match = re.fullmatch(r"ANSYSEM_ROOT(\d{3})", key.upper())
        if match and raw:
            candidates[Path(raw).expanduser()] = match.group(1)

    for base in (
        Path("C:/Program Files/AnsysEM"),
        Path("C:/Program Files/ANSYS Inc"),
        Path("C:/Program Files (x86)/AnsysEM"),
    ):
        if base.is_dir():
            for executable in base.glob("**/ansysedt.exe"):
                candidates[executable.parent] = _version_code_from_path(executable.parent)

    found: list[dict[str, Any]] = []
    for unresolved, version_code in candidates.items():
        root = unresolved.resolve()
        executable = root / "ansysedt.exe"
        syslib = root / "syslib"
        if executable.is_file() and syslib.is_dir():
            found.append(
                {
                    "root": str(root),
                    "executable": str(executable),
                    "version_code": version_code,
                    "version": _display_version(version_code),
                }
            )
    return sorted(found, key=lambda item: item["version_code"] or "", reverse=True)


def preferred_aedt_version(environment: Mapping[str, str] | None = None) -> str | None:
    env = environment or os.environ
    explicit = env.get("ANTENNA_MCP_AEDT_VERSION")
    if explicit:
        return explicit
    installations = discover_aedt_installations(env)
    return installations[0]["version"] if installations else None


def _version_code_from_path(path: Path) -> str | None:
    match = re.search(r"v(\d{3})", str(path), flags=re.IGNORECASE)
    return match.group(1) if match else None


def _display_version(code: str | None) -> str | None:
    if not code or len(code) != 3:
        return None
    return f"20{code[:2]}.{code[2]}"
