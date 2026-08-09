from __future__ import annotations

import argparse
import json

from .execution import HfssBuildService
from .reviewed_model import EngineeringAssumptionService, ReviewedModelCompiler
from .workspace import WorkspaceStore


def assume_propose_main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Propose an engineering assumption separately from source evidence and return "
            "the content hash required for explicit approval."
        )
    )
    parser.add_argument("job_id")
    parser.add_argument("symbol")
    parser.add_argument("value", type=float)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--rationale", required=True)
    args = parser.parse_args()
    result = EngineeringAssumptionService(WorkspaceStore()).prepare(
        args.job_id,
        args.symbol,
        args.value,
        args.unit,
        args.rationale,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def assume_approve_main() -> None:
    parser = argparse.ArgumentParser(
        description="Approve an engineering assumption candidate using its exact review hash."
    )
    parser.add_argument("job_id")
    parser.add_argument("approval_hash")
    args = parser.parse_args()
    result = EngineeringAssumptionService(WorkspaceStore()).approve(
        args.job_id,
        args.approval_hash,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def compile_main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile approved source evidence and assumptions into deterministic HFSS artifacts."
    )
    parser.add_argument("job_id")
    parser.add_argument("--profile", choices=("auto", "leam_case3"), default="auto")
    parser.add_argument("--assumption-approval-hash", required=True)
    args = parser.parse_args()
    result = ReviewedModelCompiler(WorkspaceStore()).compile(
        args.job_id,
        args.profile,
        args.assumption_approval_hash,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_main() -> None:
    parser = argparse.ArgumentParser(
        description="Build hash-approved HFSS artifacts after the explicit simulator execution gate is enabled."
    )
    parser.add_argument("job_id")
    parser.add_argument("approval_hash")
    parser.add_argument("--project-name", default="antenna.aedt")
    parser.add_argument("--session-mode", choices=("new", "existing"), default="new")
    parser.add_argument("--grpc-port", type=int)
    args = parser.parse_args()
    result = HfssBuildService(WorkspaceStore()).build(
        args.job_id,
        project_name=args.project_name,
        approval_hash=args.approval_hash,
        session_mode=args.session_mode,
        grpc_port=args.grpc_port,
    )
    print(result.model_dump_json(indent=2))
