from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .codegen import PythonArtifactService
from .discovery import discover_aedt_installations
from .aedt_runtime import aedt_failure_diagnostic, planned_transport
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


mcp = FastMCP(
    "LEAM Opt MCP",
    instructions=(
        "Generate inspectable antenna-modeling artifacts and optimize copied HFSS projects. "
        "Every case must produce a versioned Python model first and stop for user HFSS comparison. "
        "Generated code must be reviewed. Never enable simulator execution without explicit user approval."
    ),
)


def _services() -> tuple[WorkspaceStore, ModelingService, HfssBuildService, OptimizationService]:
    store = WorkspaceStore()
    return store, ModelingService(store), HfssBuildService(store), OptimizationService(store)


@mcp.tool()
def antenna_server_health() -> dict[str, Any]:
    """Report configured workspace, optional backend availability, and execution gate."""
    store = WorkspaceStore()
    return {
        "status": "ok",
        "workspace": str(store.root),
        "aedt_installations": discover_aedt_installations(),
        "pyaedt_transport": planned_transport(),
        "aedt_last_failure": aedt_failure_diagnostic(),
        "text_provider": os.getenv("ANTENNA_TEXT_PROVIDER", "openai"),
        "vision_provider": os.getenv("ANTENNA_VISION_PROVIDER", "openai"),
        "ollama_vision_model": os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b"),
        "openai_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "deepseek_key_configured": bool(os.getenv("DEEPSEEK_API_KEY")),
        "hfss_python_available": importlib.util.find_spec("ansys.aedt.core") is not None
        if importlib.util.find_spec("ansys")
        else False,
        "simulation_execution_enabled": os.getenv("ANTENNA_MCP_ALLOW_SIMULATION") == "1",
    }


@mcp.tool()
def create_antenna_modeling_job(
    description: str,
    template: str = "strong_description",
    backend: str = "hfss",
    attachments: list[str] | None = None,
    include_2d: bool = True,
    include_simulation: bool = False,
    include_optimization: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    """Create a staged prompt-driven modeling job without running the LLM or simulator."""
    _, modeling, _, _ = _services()
    request = ModelingRequest(
        description=description,
        template=template,
        backend=backend,
        attachments=attachments or [],
        include_2d=include_2d,
        include_simulation=include_simulation,
        include_optimization=include_optimization,
        model=model,
    )
    return modeling.create(request).model_dump(mode="json")


@mcp.tool()
def run_antenna_modeling_job(job_id: str, through_stage: str = "boolean") -> dict[str, Any]:
    """Run modeling stages through the requested stage and save all intermediate artifacts."""
    _, modeling, _, _ = _services()
    return modeling.run(job_id, through_stage).model_dump(mode="json")


@mcp.tool()
def retry_antenna_modeling_job(
    job_id: str,
    from_stage: str,
    through_stage: str = "boolean",
) -> dict[str, Any]:
    """Regenerate from one stage after freezing old downstream paths and hashes.

    The operation retains approved source evidence and immutable versioned Python
    exports. It never starts AEDT; model-provider calls are limited to the requested
    modeling stages.
    """
    store = WorkspaceStore()
    return ModelRetryService(store).retry(
        job_id,
        from_stage=from_stage,
        through_stage=through_stage,
    )


@mcp.tool()
def generate_antenna_python(
    job_id: str,
    through_stage: str = "boolean",
) -> dict[str, Any]:
    """Generate a complete Python model artifact without starting AEDT or using a license.

    The returned file is safe to import offline. It exposes ``build(hfss)``; calling that
    function later requires an explicitly supplied, licensed PyAEDT HFSS object.
    """
    store = WorkspaceStore()
    return PythonArtifactService(store).generate(job_id, through_stage=through_stage)


@mcp.tool()
def submit_antenna_model_feedback(
    job_id: str,
    feedback: str,
    comparison_images: list[str] | None = None,
) -> dict[str, Any]:
    """Record the user's HFSS/image comparison notes without running or changing AEDT."""
    store = WorkspaceStore()
    return ModelFeedbackService(store).submit(job_id, feedback, comparison_images or [])


@mcp.tool()
def regenerate_antenna_python_from_feedback(job_id: str) -> dict[str, Any]:
    """Use recorded feedback to produce the next versioned Python model, still without AEDT."""
    store = WorkspaceStore()
    return ModelFeedbackService(store).regenerate(job_id)


@mcp.tool()
def analyze_antenna_source(
    description: str,
    attachments: list[str],
    template: str = "paper_reconstruction",
    model: str | None = None,
) -> dict[str, Any]:
    """Recognize topology, labels, dimensions, materials, and uncertainties in antenna images/PDFs."""
    _, modeling, _, _ = _services()
    state = modeling.create(
        ModelingRequest(
            description=description,
            template=template,
            attachments=attachments,
            model=model,
        )
    )
    return modeling.run(state.job_id, "source_analysis").model_dump(mode="json")


@mcp.tool()
def refine_antenna_source(
    job_id: str,
    target_description: str | None = None,
    visual_audit_path: str | None = None,
) -> dict[str, Any]:
    """Reconcile raw visual evidence with extracted text; produces a hash-frozen review candidate."""
    store = WorkspaceStore()
    return SourceRefinementService(store).refine(job_id, target_description, visual_audit_path)


@mcp.tool()
def approve_antenna_source(job_id: str, approval_hash: str) -> dict[str, Any]:
    """Approve the reviewed source candidate before downstream model/code generation."""
    store = WorkspaceStore()
    return SourceRefinementService(store).approve(job_id, approval_hash)


@mcp.tool()
def recheck_antenna_source(
    job_id: str,
    visual_audit_path: str | None = None,
) -> dict[str, Any]:
    """Apply a reviewed source audit deterministically and issue a new hash-frozen candidate."""
    store = WorkspaceStore()
    return SourceRefinementService(store).recheck(job_id, visual_audit_path)


@mcp.tool()
def propose_antenna_engineering_assumption(
    job_id: str,
    symbol: str,
    value: float,
    unit: str,
    rationale: str,
) -> dict[str, Any]:
    """Propose a value for a null/unresolved source parameter and return its review hash."""
    store = WorkspaceStore()
    return EngineeringAssumptionService(store).prepare(
        job_id,
        symbol,
        value,
        unit,
        rationale,
    )


@mcp.tool()
def approve_antenna_engineering_assumption(
    job_id: str,
    approval_hash: str,
) -> dict[str, Any]:
    """Approve the exact hash-frozen engineering assumption candidate after user review."""
    store = WorkspaceStore()
    return EngineeringAssumptionService(store).approve(job_id, approval_hash)


@mcp.tool()
def compile_reviewed_antenna_model(
    job_id: str,
    assumption_approval_hash: str,
    profile: str = "auto",
) -> dict[str, Any]:
    """Deterministically compile approved evidence and assumptions into reviewed HFSS artifacts."""
    store = WorkspaceStore()
    return ReviewedModelCompiler(store).compile(job_id, profile, assumption_approval_hash)


@mcp.tool()
def prepare_antenna_artifact_review(job_id: str) -> dict[str, Any]:
    """Hash every generated artifact and return the approval token required for HFSS execution."""
    store = WorkspaceStore()
    return ArtifactReviewService(store).prepare(job_id)


@mcp.tool()
def build_hfss_project(
    job_id: str,
    approval_hash: str,
    project_name: str = "antenna.aedt",
    session_mode: str = "new",
    grpc_port: int | None = None,
) -> dict[str, Any]:
    """Build reviewed artifacts; any edit after review invalidates the supplied approval hash."""
    _, _, builder, _ = _services()
    return builder.build(
        job_id,
        project_name,
        approval_hash=approval_hash,
        session_mode=session_mode,
        grpc_port=grpc_port,
    ).model_dump(mode="json")


@mcp.tool()
def create_hfss_optimization_job(request: dict[str, Any]) -> dict[str, Any]:
    """Copy an existing HFSS project into an isolated job and prepare black-box optimization."""
    _, _, _, optimizer = _services()
    return optimizer.create(OptimizationRequest.model_validate(request)).model_dump(mode="json")


@mcp.tool()
def preflight_hfss_optimization_job(job_id: str) -> dict[str, Any]:
    """Verify parameter-to-geometry effects on the isolated project without solving HFSS."""
    _, _, _, optimizer = _services()
    return optimizer.preflight(job_id).model_dump(mode="json")


@mcp.tool()
def run_hfss_optimization_job(job_id: str) -> dict[str, Any]:
    """Run HFSS trials. Requires ANTENNA_MCP_ALLOW_SIMULATION=1 and never overwrites the source project."""
    _, _, _, optimizer = _services()
    return optimizer.run(job_id).model_dump(mode="json")


@mcp.tool()
def get_antenna_job(job_id: str) -> dict[str, Any]:
    """Read current job state and artifact paths."""
    store = WorkspaceStore()
    return store.load_state(job_id).model_dump(mode="json")


@mcp.tool()
def create_antenna_pipeline(
    description: str,
    attachments: list[str] | None = None,
    template: str = "strong_description",
    backend: str = "hfss",
    include_2d: bool = True,
    model: str | None = None,
    project_name: str = "antenna_pipeline.aedt",
    session_mode: str = "new",
    grpc_port: int | None = None,
) -> dict[str, Any]:
    """Create one end-to-end job spanning multimodal understanding, HFSS build, and optimization."""
    store = WorkspaceStore()
    service = PipelineService(store)
    request = PipelineRequest(
        description=description,
        attachments=attachments or [],
        template=template,
        backend=backend,
        include_2d=include_2d,
        model=model,
        project_name=project_name,
        session_mode=session_mode,
        grpc_port=grpc_port,
    )
    return service.create(request).model_dump(mode="json")


@mcp.tool()
def generate_antenna_pipeline(job_id: str) -> dict[str, Any]:
    """Run source understanding through optimization planning, then stop at the artifact review gate."""
    store = WorkspaceStore()
    return PipelineService(store).generate(job_id)


@mcp.tool()
def build_approved_antenna_pipeline(job_id: str, approval_hash: str) -> dict[str, Any]:
    """Build the approved pipeline model and stop before expensive optimization."""
    store = WorkspaceStore()
    return PipelineService(store).build(job_id, approval_hash)


@mcp.tool()
def optimize_antenna_pipeline(job_id: str) -> dict[str, Any]:
    """Run the planned HFSS optimization and produce the best project plus complete trial history."""
    store = WorkspaceStore()
    return PipelineService(store).optimize(job_id)


@mcp.tool()
def validate_antenna_model(
    benchmark_path: str,
    candidate_path: str | None = None,
    job_id: str | None = None,
    reference_s11_path: str | None = None,
    candidate_s11_path: str | None = None,
    contract_only: bool = False,
) -> dict[str, Any]:
    """Compare a generated model with a frozen benchmark and optional S11 curves.

    Contract-only validation checks declared geometry, materials, Boolean operations, and
    solver setup without claiming electromagnetic correctness. Full validation requires
    both reference and candidate S11 CSV files.
    """
    if (candidate_path is None) == (job_id is None):
        raise ValueError("provide exactly one of candidate_path or job_id")
    store = WorkspaceStore()
    service = ValidationService(store)
    if job_id:
        return service.validate_job(
            benchmark_path,
            job_id,
            reference_s11=reference_s11_path,
            candidate_s11=candidate_s11_path,
            contract_only=contract_only,
        )
    return service.validate_manifest(
        benchmark_path,
        candidate_path,
        reference_s11=reference_s11_path,
        candidate_s11=candidate_s11_path,
        contract_only=contract_only,
    )


def main() -> None:
    transport = os.getenv("ANTENNA_MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "streamable-http", "sse"}:
        raise ValueError("ANTENNA_MCP_TRANSPORT must be stdio, streamable-http, or sse")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
