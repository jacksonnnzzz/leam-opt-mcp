"""Small import-safe launcher used by the one-case validation folders."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Sequence


def run_shared(
    shared_runner: Path,
    default_arguments: Sequence[str],
    argv: Sequence[str] | None = None,
    *,
    protected_options: Sequence[str] = (),
) -> int:
    """Load one shared runner and prepend case-specific safe defaults."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    for option in protected_options:
        if any(item == option or item.startswith(f"{option}=") for item in arguments):
            raise SystemExit(f"{option} is fixed by this one-case launcher")

    runner = shared_runner.resolve()
    if not runner.is_file():
        raise FileNotFoundError(f"shared case runner does not exist: {runner}")
    module_name = f"_validation_runner_{runner.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, runner)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load shared case runner: {runner}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(runner.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(runner.parent))
    return int(module.main([*default_arguments, *arguments]))
