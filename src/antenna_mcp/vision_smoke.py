from __future__ import annotations

import argparse
import os
from pathlib import Path

from .modeling import ModelingService
from .models import ModelingRequest, Template
from .workspace import WorkspaceStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one source-analysis vision call without HFSS.")
    parser.add_argument("attachment", nargs="+", help="One or more PNG/JPEG/PDF paths")
    parser.add_argument(
        "--description",
        default=(
            "Identify this antenna's topology, labeled dimensions, materials, and uncertainties. "
            "If the source contains multiple antenna designs, do not merge them; report that a "
            "specific target design must be selected."
        ),
    )
    parser.add_argument("--workspace", default=".antenna-mcp")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    vision_provider = os.getenv("ANTENNA_VISION_PROVIDER", "openai").strip().lower()
    if vision_provider == "deepseek":
        raise SystemExit(
            "DeepSeek's current API model is text-only. Set ANTENNA_VISION_PROVIDER=openai "
            "with a separate API key, or ANTENNA_VISION_PROVIDER=ollama for local analysis."
        )
    if vision_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not configured for the vision provider in this process")
    attachments = [str(Path(item).expanduser().resolve()) for item in args.attachment]
    store = WorkspaceStore(args.workspace)
    service = ModelingService(store)
    state = service.create(
        ModelingRequest(
            description=args.description,
            template=Template.PAPER,
            attachments=attachments,
            model=args.model,
        )
    )
    result = service.run(state.job_id, through_stage="source_analysis")
    if result.status != "completed":
        raise SystemExit(result.error or "vision smoke test failed")
    print(f"job_id={result.job_id}")
    print(f"source_analysis={result.artifacts['source_analysis']}")


if __name__ == "__main__":
    main()
