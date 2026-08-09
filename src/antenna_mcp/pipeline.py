from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codegen import PythonArtifactService
from .execution import HfssBuildService
from .modeling import ModelingService
from .models import JobState, ModelingRequest, OptimizationPlan, PipelineRequest
from .optimizer import OptimizationService
from .review import ArtifactReviewService
from .workspace import WorkspaceStore


class PipelineService:
    """Orchestrate source understanding, HFSS build, and optimization as one job."""

    def __init__(
        self,
        store: WorkspaceStore,
        modeling: ModelingService | None = None,
        builder: HfssBuildService | None = None,
        optimizer: OptimizationService | None = None,
        review: ArtifactReviewService | None = None,
    ) -> None:
        self.store = store
        self.modeling = modeling or ModelingService(store)
        self.builder = builder or HfssBuildService(store)
        self.optimizer = optimizer or OptimizationService(store)
        self.review = review or ArtifactReviewService(store)

    def create(self, request: PipelineRequest) -> JobState:
        modeling_state = self.modeling.create(
            ModelingRequest(
                description=request.description,
                backend=request.backend,
                template=request.template,
                attachments=request.attachments,
                include_2d=request.include_2d,
                include_simulation=True,
                include_optimization=True,
                model=request.model,
            )
        )
        state = self.store.create_job("pipeline", request.model_dump(mode="json"))
        state.artifacts["modeling_job_id"] = modeling_state.job_id
        state.current_stage = "created"
        self.store.save_state(state)
        return state

    def generate(self, job_id: str) -> dict[str, Any]:
        state = self._load(job_id)
        if state.status not in {"created", "failed"}:
            raise ValueError(f"pipeline cannot generate from status {state.status}")
        state.status = "running"
        state.current_stage = "source_to_optimization_spec"
        state.error = None
        self.store.save_state(state)

        modeling_job_id = state.artifacts["modeling_job_id"]
        generated = self.modeling.run(modeling_job_id, through_stage="optimization_spec")
        if generated.status != "completed":
            state.status = "failed"
            state.error = generated.error or "modeling pipeline failed"
            self.store.save_state(state)
            return {"pipeline": state.model_dump(mode="json"), "modeling": generated.model_dump(mode="json")}

        python_artifact = PythonArtifactService(
            self.store,
            modeling=self.modeling,
        ).export_existing(modeling_job_id, through_stage="boolean")
        packet = self.review.prepare(modeling_job_id)
        packet_path = self.store.write_artifact(
            job_id,
            "review_reference.json",
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts["review_packet"] = str(packet_path)
        state.status = "awaiting_review"
        state.current_stage = "user_hfss_comparison"
        self.store.save_state(state)
        return {
            "pipeline": state.model_dump(mode="json"),
            "modeling": generated.model_dump(mode="json"),
            "python": python_artifact,
            "review": packet,
        }

    def build(self, job_id: str, approval_hash: str) -> dict[str, Any]:
        state = self._load(job_id)
        if state.status != "awaiting_review" and not (
            state.status == "failed" and state.current_stage == "hfss_build"
        ):
            raise ValueError("pipeline must be awaiting_review before HFSS build")
        request = PipelineRequest.model_validate(state.request)
        state.status = "running"
        state.current_stage = "hfss_build"
        self.store.save_state(state)

        built = self.builder.build(
            state.artifacts["modeling_job_id"],
            project_name=request.project_name,
            approval_hash=approval_hash,
            session_mode=request.session_mode,
            grpc_port=request.grpc_port,
        )
        if built.status != "completed":
            state.status = "failed"
            state.error = built.error or "HFSS build failed"
        else:
            state.artifacts["hfss_project"] = built.artifacts["hfss_project"]
            state.status = "ready_to_optimize"
            state.current_stage = "optimization_ready"
        self.store.save_state(state)
        return {
            "pipeline": state.model_dump(mode="json"),
            "modeling": built.model_dump(mode="json"),
        }

    def optimize(self, job_id: str) -> dict[str, Any]:
        state = self._load(job_id)
        if state.status != "ready_to_optimize" and not (
            state.status == "failed" and state.current_stage == "hfss_optimization"
        ):
            raise ValueError("pipeline must be ready_to_optimize")
        modeling = self.store.load_state(state.artifacts["modeling_job_id"])
        pipeline_request = PipelineRequest.model_validate(state.request)
        plan_path = Path(modeling.artifacts["optimization_spec"])
        plan = OptimizationPlan.model_validate_json(plan_path.read_text("utf-8"))

        state.status = "running"
        state.current_stage = "hfss_optimization"
        self.store.save_state(state)
        optimization = self.optimizer.create(
            plan.to_request(
                state.artifacts["hfss_project"],
                session_mode=pipeline_request.session_mode,
                grpc_port=pipeline_request.grpc_port,
            )
        )
        state.artifacts["optimization_job_id"] = optimization.job_id
        self.store.save_state(state)
        result = self.optimizer.run(optimization.job_id)
        if result.status != "completed":
            state.status = "failed"
            state.error = result.error or "HFSS optimization failed"
        else:
            for name in ("trials", "best", "optimized_project"):
                state.artifacts[name] = result.artifacts[name]
            summary = {
                "pipeline_job_id": state.job_id,
                "modeling_job_id": modeling.job_id,
                "optimization_job_id": result.job_id,
                "baseline_project": state.artifacts["hfss_project"],
                "optimized_project": result.artifacts["optimized_project"],
                "best": json.loads(Path(result.artifacts["best"]).read_text("utf-8")),
                "trials": result.artifacts["trials"],
            }
            summary_path = self.store.write_artifact(
                job_id,
                "pipeline_result.json",
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            )
            state.artifacts["result"] = str(summary_path)
            state.status = "completed"
            state.current_stage = "complete"
        self.store.save_state(state)
        return {
            "pipeline": state.model_dump(mode="json"),
            "optimization": result.model_dump(mode="json"),
        }

    def _load(self, job_id: str) -> JobState:
        state = self.store.load_state(job_id)
        if state.kind != "pipeline":
            raise ValueError("a pipeline job is required")
        return state
