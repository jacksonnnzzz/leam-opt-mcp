"""Strict existing-AEDT runner for the Figure-2-corrected V2 topology."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_MODEL_SPEC = importlib.util.spec_from_file_location(
    "reference_model", _HERE / "reference_model_v2.py"
)
if _MODEL_SPEC is None or _MODEL_SPEC.loader is None:
    raise RuntimeError("unable to load Khan V2 reference model")
_MODEL = importlib.util.module_from_spec(_MODEL_SPEC)
sys.modules["reference_model"] = _MODEL
_MODEL_SPEC.loader.exec_module(_MODEL)

_SHARED = _HERE.parent / "ibrahim_38ghz_monopole" / "run_reference.py"
_RUNNER_SPEC = importlib.util.spec_from_file_location("_khan_v2_reference_runner", _SHARED)
if _RUNNER_SPEC is None or _RUNNER_SPEC.loader is None:
    raise RuntimeError(f"unable to load strict reference runner: {_SHARED}")
_RUNNER = importlib.util.module_from_spec(_RUNNER_SPEC)
_RUNNER_SPEC.loader.exec_module(_RUNNER)


def main(argv=None):
    return int(_RUNNER.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
