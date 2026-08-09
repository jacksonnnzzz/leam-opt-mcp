from __future__ import annotations

import argparse
import json
import os

from .source_refinement import SourceRefinementService
from .workspace import WorkspaceStore


def refine_main() -> None:
    parser = argparse.ArgumentParser(description="Refine raw visual antenna evidence with the text LLM.")
    parser.add_argument("job_id")
    parser.add_argument("--description", default=None)
    parser.add_argument(
        "--visual-audit",
        default=None,
        help="Optional operator-reviewed visual-audit JSON; skips model-generated visual audit.",
    )
    parser.add_argument("--workspace", default=".antenna-mcp")
    args = parser.parse_args()
    if os.getenv("ANTENNA_TEXT_PROVIDER", "openai").lower() == "deepseek" and not os.getenv(
        "DEEPSEEK_API_KEY"
    ):
        raise SystemExit("DEEPSEEK_API_KEY is not configured in this process")
    result = SourceRefinementService(WorkspaceStore(args.workspace)).refine(
        args.job_id, args.description, args.visual_audit
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def approve_main() -> None:
    parser = argparse.ArgumentParser(description="Approve a hash-frozen source refinement.")
    parser.add_argument("job_id")
    parser.add_argument("approval_hash")
    parser.add_argument("--workspace", default=".antenna-mcp")
    args = parser.parse_args()
    result = SourceRefinementService(WorkspaceStore(args.workspace)).approve(
        args.job_id, args.approval_hash
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def recheck_main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministically reconcile a candidate to its operator-reviewed source audit."
    )
    parser.add_argument("job_id")
    parser.add_argument("--visual-audit", default=None)
    parser.add_argument("--workspace", default=".antenna-mcp")
    args = parser.parse_args()
    result = SourceRefinementService(WorkspaceStore(args.workspace)).recheck(
        args.job_id,
        args.visual_audit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
