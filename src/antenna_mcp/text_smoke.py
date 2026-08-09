from __future__ import annotations

import argparse
import os

from .modeling import ModelingService
from .models import ModelingRequest, Template
from .prompts import STAGES
from .workspace import WorkspaceStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run text-only antenna generation without vision or HFSS.")
    parser.add_argument("description", help="Detailed antenna topology, dimensions, materials, and intent")
    parser.add_argument("--through-stage", choices=STAGES, default="parameters")
    parser.add_argument("--workspace", default=".antenna-mcp")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    provider = os.getenv("ANTENNA_TEXT_PROVIDER", "openai").strip().lower()
    if provider == "deepseek" and not os.getenv("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is not configured in this process")
    if provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not configured in this process")
    if provider not in {"deepseek", "openai"}:
        raise SystemExit("ANTENNA_TEXT_PROVIDER must be 'deepseek' or 'openai'")

    store = WorkspaceStore(args.workspace)
    service = ModelingService(store)
    state = service.create(
        ModelingRequest(
            description=args.description,
            template=Template.STRONG,
            attachments=[],
            model=args.model,
        )
    )
    result = service.run(state.job_id, through_stage=args.through_stage)
    if result.status != "completed":
        raise SystemExit(result.error or "text smoke test failed")
    print(f"job_id={result.job_id}")
    for name, path in result.artifacts.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
