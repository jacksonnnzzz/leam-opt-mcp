import sys

if sys.version_info[:2] < (3, 10) or "ansysedt" in str(getattr(sys, "executable", "")).lower():
    raise RuntimeError(
        "This run_case.py is an external CPython/PyAEDT launcher. "
        "Run it from PowerShell with .\\.venv\\Scripts\\python.exe; "
        "do not use AEDT Tools > Run Script."
    )

from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent
VALIDATION_ROOT = CASE_DIR.parents[1]
sys.path.insert(0, str(CASE_DIR.parent))
from _shared_launcher import run_shared  # noqa: E402

SHARED_RUNNER = VALIDATION_ROOT / "kaur_split_ring_monopole" / "run_reference.py"
DEFAULT_ARGUMENTS = [
    "--case", "baseline",
    "--project", str(CASE_DIR / "local_results" / "kaur_baseline_uwb.aedt"),
    "--results-dir", str(CASE_DIR / "local_results"),
]


def main(argv=None):
    return run_shared(SHARED_RUNNER, DEFAULT_ARGUMENTS, argv, protected_options=("--case",))


if __name__ == "__main__":
    raise SystemExit(main())
