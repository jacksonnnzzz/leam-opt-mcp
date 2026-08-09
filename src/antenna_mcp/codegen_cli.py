from __future__ import annotations

import argparse
import json

from .codegen import PythonArtifactService
from .workspace import WorkspaceStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an import-safe antenna Python file without starting AEDT or "
            "checking out an HFSS license."
        )
    )
    parser.add_argument("job_id")
    parser.add_argument(
        "--through-stage",
        choices=("boolean", "simulation_setup"),
        default="boolean",
    )
    args = parser.parse_args()
    result = PythonArtifactService(WorkspaceStore()).generate(
        args.job_id,
        through_stage=args.through_stage,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
