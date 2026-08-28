from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import __version__
from .assumption_search import AssumptionStudyLedger, run_aedt_assumption_search
from .codegen import PythonArtifactService
from .execution import HfssBuildService
from .feedback import ModelFeedbackService
from .modeling import ModelingService
from .model_retry import ModelRetryService
from .models import ModelingRequest, OptimizationRequest, PipelineRequest
from .optimizer import OptimizationService
from .pipeline import PipelineService
from .review import ArtifactReviewService
from .reviewed_model import EngineeringAssumptionService, ReviewedModelCompiler
from .source_refinement import SourceRefinementService
from .validation import ValidationService
from .workspace import WorkspaceStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="antenna-workflow",
        description="Run the reviewable antenna reconstruction workflow without an MCP client.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--workspace",
        default=None,
        help="Job directory. Defaults to ANTENNA_MCP_WORKSPACE or .antenna-mcp.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("model-create", help="Create an offline modeling job.")
    create.add_argument("--description", required=True)
    create.add_argument("--attachment", action="append", default=[])
    create.add_argument(
        "--template",
        choices=("strong_description", "weak_description", "paper_reconstruction"),
        default="strong_description",
    )
    create.add_argument("--backend", choices=("hfss", "cst"), default="hfss")
    create.add_argument("--no-2d", action="store_true")
    create.add_argument("--include-simulation", action="store_true")
    create.add_argument("--include-optimization", action="store_true")
    create.add_argument("--model", default=None)

    run = commands.add_parser("model-run", help="Run a modeling job through one stage.")
    run.add_argument("job_id")
    run.add_argument(
        "--through-stage",
        default="boolean",
        choices=(
            "source_analysis",
            "parameters",
            "materials",
            "solids",
            "dimensions",
            "model_3d",
            "model_2d",
            "boolean",
            "simulation_spec",
            "simulation_setup",
            "optimization_spec",
        ),
    )

    retry = commands.add_parser(
        "model-retry",
        help="Audit old downstream artifacts and regenerate from an earlier stage.",
    )
    retry.add_argument("job_id")
    retry.add_argument(
        "--from-stage",
        required=True,
        choices=(
            "source_analysis",
            "parameters",
            "materials",
            "solids",
            "dimensions",
            "model_3d",
            "model_2d",
            "boolean",
            "simulation_spec",
            "simulation_setup",
            "optimization_spec",
        ),
    )
    retry.add_argument(
        "--through-stage",
        default="boolean",
        choices=(
            "source_analysis",
            "parameters",
            "materials",
            "solids",
            "dimensions",
            "model_3d",
            "model_2d",
            "boolean",
            "simulation_spec",
            "simulation_setup",
            "optimization_spec",
        ),
    )

    status = commands.add_parser("status", help="Read a job state and artifact paths.")
    status.add_argument("job_id")

    refine = commands.add_parser("source-refine", help="Create a reviewed source candidate.")
    refine.add_argument("job_id")
    refine.add_argument("--description", default=None)
    refine.add_argument("--visual-audit", default=None)

    recheck = commands.add_parser("source-recheck", help="Reconcile a candidate to a visual audit.")
    recheck.add_argument("job_id")
    recheck.add_argument("--visual-audit", default=None)

    approve = commands.add_parser("source-approve", help="Approve a hash-frozen source candidate.")
    approve.add_argument("job_id")
    approve.add_argument("approval_hash")

    assumption = commands.add_parser(
        "assumption-propose", help="Propose one value for an unresolved parameter."
    )
    assumption.add_argument("job_id")
    assumption.add_argument("symbol")
    assumption.add_argument("value", type=float)
    assumption.add_argument("--unit", required=True)
    assumption.add_argument("--rationale", required=True)

    assumption_approve = commands.add_parser(
        "assumption-approve", help="Approve a hash-frozen engineering assumption."
    )
    assumption_approve.add_argument("job_id")
    assumption_approve.add_argument("approval_hash")

    assumption_plan = commands.add_parser(
        "assumption-plan",
        help="Freeze an engineering-assumption space and deterministically plan trials.",
    )
    assumption_plan.add_argument("--space", required=True, type=Path)
    assumption_plan.add_argument("--output-dir", required=True, type=Path)
    assumption_plan.add_argument("--limit", type=int)

    assumption_report = commands.add_parser(
        "assumption-report", help="Rank immutable results from an assumption study."
    )
    assumption_report.add_argument("--space", required=True, type=Path)
    assumption_report.add_argument("--output-dir", required=True, type=Path)

    assumption_run = commands.add_parser(
        "assumption-run",
        help="Run a bounded, resumable engineering-assumption study in an existing AEDT project.",
    )
    assumption_run.add_argument("--space", required=True, type=Path)
    assumption_run.add_argument("--adapter", required=True, type=Path)
    assumption_run.add_argument("--output-dir", required=True, type=Path)
    assumption_run.add_argument("--grpc-port", required=True, type=int)
    assumption_run.add_argument("--active-project", required=True)
    assumption_run.add_argument("--aedt-version", default=None)
    assumption_run.add_argument("--limit", type=int)
    assumption_run.add_argument("--resume", action="store_true")
    assumption_run.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --resume, append a new immutable attempt for failed trials.",
    )
    assumption_run.add_argument(
        "--postprocess-existing",
        action="store_true",
        help="Do not solve; extract evidence only from already solved, receipt-matched designs.",
    )

    compile_model = commands.add_parser(
        "model-compile", help="Compile approved evidence into deterministic model artifacts."
    )
    compile_model.add_argument("job_id")
    compile_model.add_argument("--profile", choices=("auto", "leam_case3"), default="auto")
    compile_model.add_argument("--assumption-approval-hash", required=True)

    codegen = commands.add_parser("codegen", help="Export versioned import-safe Python code.")
    codegen.add_argument("job_id")
    codegen.add_argument(
        "--through-stage", choices=("boolean", "simulation_setup"), default="boolean"
    )

    feedback = commands.add_parser("feedback", help="Freeze user comparison feedback.")
    feedback.add_argument("job_id")
    feedback.add_argument("feedback")
    feedback.add_argument("--comparison-image", action="append", default=[])

    regenerate = commands.add_parser("regenerate", help="Generate the next Python revision.")
    regenerate.add_argument("job_id")

    review = commands.add_parser("artifact-review", help="Hash all executable build artifacts.")
    review.add_argument("job_id")

    build = commands.add_parser("hfss-build", help="Apply reviewed artifacts to HFSS.")
    build.add_argument("job_id")
    build.add_argument("approval_hash")
    build.add_argument("--project-name", default="antenna.aedt")
    build.add_argument("--session-mode", choices=("new", "existing"), default="new")
    build.add_argument("--grpc-port", type=int)

    pipeline_create = commands.add_parser(
        "pipeline-create", help="Create a complete modeling/build/optimization pipeline."
    )
    pipeline_create.add_argument("--description", required=True)
    pipeline_create.add_argument("--attachment", action="append", default=[])
    pipeline_create.add_argument(
        "--template",
        choices=("strong_description", "weak_description", "paper_reconstruction"),
        default="strong_description",
    )
    pipeline_create.add_argument("--no-2d", action="store_true")
    pipeline_create.add_argument("--model", default=None)
    pipeline_create.add_argument("--project-name", default="antenna_pipeline.aedt")
    pipeline_create.add_argument("--session-mode", choices=("new", "existing"), default="new")
    pipeline_create.add_argument("--grpc-port", type=int)

    for name, help_text in (
        ("pipeline-generate", "Generate the pipeline and stop at artifact review."),
        ("pipeline-optimize", "Run the approved pipeline optimization."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("job_id")

    pipeline_build = commands.add_parser(
        "pipeline-build", help="Build a pipeline after explicit artifact approval."
    )
    pipeline_build.add_argument("job_id")
    pipeline_build.add_argument("approval_hash")

    optimization_create = commands.add_parser(
        "optimization-create", help="Create an optimization job from a JSON request."
    )
    optimization_create.add_argument("request_json", type=Path)

    optimization_preflight = commands.add_parser(
        "optimization-preflight",
        help="Verify that every optimization variable changes copied-project geometry.",
    )
    optimization_preflight.add_argument("job_id")

    optimization_run = commands.add_parser(
        "optimization-run", help="Run a prepared optimization job."
    )
    optimization_run.add_argument("job_id")

    validate = commands.add_parser(
        "validate", help="Compare a generated model contract and optional S11 data with a benchmark."
    )
    validate.add_argument("--benchmark", required=True, type=Path)
    source = validate.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate", type=Path)
    source.add_argument("--job-id")
    validate.add_argument("--reference-s11", type=Path)
    validate.add_argument("--candidate-s11", type=Path)
    validate.add_argument("--report", type=Path)
    validate.add_argument(
        "--contract-only",
        action="store_true",
        help="Validate geometry/material/solver declarations without claiming EM-result validity.",
    )
    return parser


def run_command(args: argparse.Namespace) -> dict[str, Any]:
    store = WorkspaceStore(args.workspace)
    command = args.command

    if command == "model-create":
        request = ModelingRequest(
            description=args.description,
            backend=args.backend,
            template=args.template,
            attachments=args.attachment,
            include_2d=not args.no_2d,
            include_simulation=args.include_simulation,
            include_optimization=args.include_optimization,
            model=args.model,
        )
        return ModelingService(store).create(request).model_dump(mode="json")
    if command == "model-run":
        return ModelingService(store).run(args.job_id, args.through_stage).model_dump(mode="json")
    if command == "model-retry":
        return ModelRetryService(store).retry(
            args.job_id,
            from_stage=args.from_stage,
            through_stage=args.through_stage,
        )
    if command == "status":
        return store.load_state(args.job_id).model_dump(mode="json")
    if command == "source-refine":
        return SourceRefinementService(store).refine(
            args.job_id, args.description, args.visual_audit
        )
    if command == "source-recheck":
        return SourceRefinementService(store).recheck(args.job_id, args.visual_audit)
    if command == "source-approve":
        return SourceRefinementService(store).approve(args.job_id, args.approval_hash)
    if command == "assumption-propose":
        return EngineeringAssumptionService(store).prepare(
            args.job_id, args.symbol, args.value, args.unit, args.rationale
        )
    if command == "assumption-approve":
        return EngineeringAssumptionService(store).approve(args.job_id, args.approval_hash)
    if command == "assumption-plan":
        return AssumptionStudyLedger(args.space, args.output_dir).prepare(limit=args.limit)
    if command == "assumption-report":
        ledger = AssumptionStudyLedger(args.space, args.output_dir)
        ledger.initialize()
        path = ledger.write_summary()
        return {**ledger.summary(), "summary": str(path)}
    if command == "assumption-run":
        return run_aedt_assumption_search(
            space_path=args.space,
            adapter_path=args.adapter,
            output_dir=args.output_dir,
            grpc_port=args.grpc_port,
            active_project=args.active_project,
            version=args.aedt_version,
            limit=args.limit,
            resume=args.resume,
            retry_failed=args.retry_failed,
            postprocess_existing=args.postprocess_existing,
        )
    if command == "model-compile":
        return ReviewedModelCompiler(store).compile(
            args.job_id, args.profile, args.assumption_approval_hash
        )
    if command == "codegen":
        return PythonArtifactService(store).generate(args.job_id, args.through_stage)
    if command == "feedback":
        return ModelFeedbackService(store).submit(
            args.job_id, args.feedback, args.comparison_image
        )
    if command == "regenerate":
        return ModelFeedbackService(store).regenerate(args.job_id)
    if command == "artifact-review":
        return ArtifactReviewService(store).prepare(args.job_id)
    if command == "hfss-build":
        return HfssBuildService(store).build(
            args.job_id,
            args.project_name,
            approval_hash=args.approval_hash,
            session_mode=args.session_mode,
            grpc_port=args.grpc_port,
        ).model_dump(mode="json")
    if command == "pipeline-create":
        request = PipelineRequest(
            description=args.description,
            attachments=args.attachment,
            template=args.template,
            include_2d=not args.no_2d,
            model=args.model,
            project_name=args.project_name,
            session_mode=args.session_mode,
            grpc_port=args.grpc_port,
        )
        return PipelineService(store).create(request).model_dump(mode="json")
    if command == "pipeline-generate":
        return PipelineService(store).generate(args.job_id)
    if command == "pipeline-build":
        return PipelineService(store).build(args.job_id, args.approval_hash)
    if command == "pipeline-optimize":
        return PipelineService(store).optimize(args.job_id)
    if command == "optimization-create":
        request = OptimizationRequest.model_validate_json(args.request_json.read_text("utf-8"))
        return OptimizationService(store).create(request).model_dump(mode="json")
    if command == "optimization-preflight":
        return OptimizationService(store).preflight(args.job_id).model_dump(mode="json")
    if command == "optimization-run":
        return OptimizationService(store).run(args.job_id).model_dump(mode="json")
    if command == "validate":
        service = ValidationService(store)
        if args.job_id:
            if args.report is not None:
                raise ValueError("--report is only valid with --candidate; job reports are stored in the job")
            return service.validate_job(
                args.benchmark,
                args.job_id,
                reference_s11=args.reference_s11,
                candidate_s11=args.candidate_s11,
                contract_only=args.contract_only,
            )
        return service.validate_manifest(
            args.benchmark,
            args.candidate,
            reference_s11=args.reference_s11,
            candidate_s11=args.candidate_s11,
            report_path=args.report,
            contract_only=args.contract_only,
        )
    raise AssertionError(f"unhandled command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run_command(args)
    except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command != "status" and _result_failed(result):
        return 1
    return 0


def _result_failed(result: dict[str, Any]) -> bool:
    if result.get("status") in {"failed", "incomplete"}:
        return True
    for key in ("pipeline", "modeling", "optimization"):
        nested = result.get(key)
        if isinstance(nested, dict) and nested.get("status") == "failed":
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
