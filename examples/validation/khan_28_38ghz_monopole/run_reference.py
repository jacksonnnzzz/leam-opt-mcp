"""Reuse the strict existing-AEDT reference runner with this case's modules.

The generic execution behaviour is shared with the Ibrahim benchmark. Because
this wrapper lives beside Khan's ``reference_model`` and ``paper_targets``, the
shared runner imports the Khan-specific model and gate in a fresh process.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent / "ibrahim_38ghz_monopole" / "run_reference.py"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_SPEC = importlib.util.spec_from_file_location("_khan_strict_reference_runner", _SHARED)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"unable to load strict reference runner: {_SHARED}")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def main(argv=None):
    return int(_MODULE.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
