from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .modeling import ModelingService
from .models import JobState, ModelingRequest
from .prompts import STAGES
from .reviewed_model import EngineeringAssumptionService
from .workspace import WorkspaceStore


_SOURCE_REVIEW_KEYS = {
    "source_analysis_candidate",
    "source_refinement_report",
    "source_review_packet",
    "source_analysis_approved",
    "source_visual_audit",
    "source_visual_verdict",
}

_DERIVED_LATEST_KEYS = {
    "aedt_runner",
    "builder",
    "geometry_manifest",
    "geometry_validation",
    "hfss_build_report",
    "hfss_project",
    "python_export_manifest",
    "python_model",
    "review_packet",
    "validation_candidate",
    "validation_report",
}

_VERSIONED_OUTPUT_KEY = re.compile(
    r"(?:python_model|python_export_manifest|aedt_runner|validation_candidate|validation_report)_v\d{3}"
)
_RETRY_RECEIPT_KEY = re.compile(r"model_retry_receipt_v(\d{3})")


class ModelRetryService:
    """Invalidate selected generated stages without losing their audit history.

    A retry remains in the same modeling job.  This is deliberate: source-review
    packets bind absolute, job-local paths, so moving approved evidence into a fork
    would silently invalidate the user's approval.  The service freezes the old
    downstream paths and digests in a versioned receipt, removes only their mutable
    state aliases, and asks :class:`ModelingService` to use its normal failed-stage
    resume path.
    """

    def __init__(
        self,
        store: WorkspaceStore,
        modeling: ModelingService | None = None,
    ) -> None:
        self.store = store
        self.modeling = modeling or ModelingService(store)

    def retry(
        self,
        job_id: str,
        *,
        from_stage: str,
        through_stage: str = "boolean",
    ) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        request = self._preflight(state, from_stage, through_stage)
        job_dir = self.store.job_dir(job_id).resolve()

        source_approval_hash: str | None = None
        if state.artifacts.get("source_analysis_approved"):
            if from_stage == "source_analysis":
                raise PermissionError(
                    "cannot retry source_analysis after source approval; create a new modeling "
                    "job and review its source candidate instead"
                )
            _, _, source_approval_hash = EngineeringAssumptionService(
                self.store
            )._verify_source_approval(state)
        elif _SOURCE_REVIEW_KEYS.intersection(state.artifacts) or any(
            key.startswith("source_visual_input_") for key in state.artifacts
        ):
            raise PermissionError(
                "the source review chain is incomplete or awaiting approval; finish or abandon "
                "that review explicitly before retrying modeling stages"
            )

        upstream_keys = self._required_upstream_keys(state, request, from_stage)
        upstream = [self._artifact_record(state, key, job_dir) for key in upstream_keys]

        invalidated_keys = self._invalidated_keys(state, from_stage)
        invalidated = [
            self._artifact_record(state, key, job_dir) for key in invalidated_keys
        ]
        versioned_keys = sorted(
            key for key in state.artifacts if _VERSIONED_OUTPUT_KEY.fullmatch(key)
        )
        retained_versioned = [
            self._artifact_record(state, key, job_dir) for key in versioned_keys
        ]
        versioned_hashes = {item["key"]: item["sha256"] for item in retained_versioned}

        receipt_key, receipt_name = self._next_receipt(job_dir, state)
        state_path = job_dir / "state.json"
        prior_state_sha256 = hashlib.sha256(state_path.read_bytes()).hexdigest()
        receipt = {
            "schema_version": 1,
            "action": "model_retry",
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "from_stage": from_stage,
            "through_stage": through_stage,
            "prior_state": {
                "status": state.status,
                "current_stage": state.current_stage,
                "error": state.error,
                "sha256": prior_state_sha256,
            },
            "request_sha256": hashlib.sha256(
                json.dumps(
                    state.request,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "source_approval": {
                "present": source_approval_hash is not None,
                "approval_hash": source_approval_hash,
                "policy": (
                    "verified and retained in place"
                    if source_approval_hash is not None
                    else "no approved source chain was present"
                ),
            },
            "reused_upstream_artifacts": upstream,
            "invalidated_artifacts": invalidated,
            "retained_versioned_artifacts": retained_versioned,
            "file_policy": {
                "deleted_files": [],
                "versioned_outputs_are_immutable": True,
                "note": (
                    "Files are not deleted. Mutable stage files can be superseded by the retry; "
                    "their pre-retry paths, sizes, and SHA-256 digests remain frozen here."
                ),
            },
        }
        receipt_path = self.store.write_artifact(
            job_id,
            receipt_name,
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        )
        receipt_sha256 = hashlib.sha256(receipt_path.read_bytes()).hexdigest()

        for key in invalidated_keys:
            state.artifacts.pop(key, None)
        state.artifacts[receipt_key] = str(receipt_path)
        # ModelingService intentionally resumes artifacts strictly before the failed
        # stage.  Marking this planned retry as failed reuses that existing, tested path.
        state.status = "failed"
        state.current_stage = from_stage
        state.error = f"planned model retry recorded in {receipt_name}"
        self.store.save_state(state)

        result = self.modeling.run(job_id, through_stage=through_stage)
        self._verify_versioned_outputs_unchanged(result, versioned_hashes, job_dir)
        return {
            "job_id": job_id,
            "status": result.status,
            "current_stage": result.current_stage,
            "error": result.error,
            "from_stage": from_stage,
            "through_stage": through_stage,
            "receipt": str(receipt_path),
            "receipt_sha256": receipt_sha256,
            "invalidated_artifact_keys": invalidated_keys,
            "reused_upstream_artifact_keys": upstream_keys,
            "retained_versioned_artifact_keys": versioned_keys,
            "artifacts": result.artifacts,
        }

    @staticmethod
    def _preflight(
        state: JobState,
        from_stage: str,
        through_stage: str,
    ) -> ModelingRequest:
        if state.kind != "modeling":
            raise ValueError("a modeling job is required")
        if state.status == "running":
            raise ValueError("cannot retry a running modeling job")
        if state.status == "awaiting_review":
            raise ValueError("finish the pending source review before retrying the job")
        if from_stage not in STAGES:
            raise ValueError(f"unknown retry stage: {from_stage}")
        if through_stage not in STAGES:
            raise ValueError(f"unknown stage: {through_stage}")
        if STAGES.index(from_stage) > STAGES.index(through_stage):
            raise ValueError("from_stage must not come after through_stage")

        request = ModelingRequest.model_validate(state.request)
        for stage in (from_stage, through_stage):
            if stage == "model_2d" and not request.include_2d:
                raise ValueError("model_2d is disabled for this job")
            if stage in {"simulation_spec", "simulation_setup"} and not request.include_simulation:
                raise ValueError("set include_simulation=true to retry simulation stages")
            if stage == "optimization_spec" and not request.include_optimization:
                raise ValueError("set include_optimization=true to retry optimization_spec")
        return request

    def _required_upstream_keys(
        self,
        state: JobState,
        request: ModelingRequest,
        from_stage: str,
    ) -> list[str]:
        result: list[str] = []
        cutoff = STAGES.index(from_stage)
        for stage in STAGES[:cutoff]:
            if not self._stage_enabled(stage, request):
                continue
            if stage == "source_analysis" and state.artifacts.get("source_analysis_approved"):
                result.append("source_analysis_approved")
                continue
            if stage not in state.artifacts:
                raise ValueError(
                    f"cannot retry from {from_stage}: required upstream artifact {stage} is missing"
                )
            result.append(stage)
        return result

    @staticmethod
    def _stage_enabled(stage: str, request: ModelingRequest) -> bool:
        if stage == "model_2d":
            return request.include_2d
        if stage in {"simulation_spec", "simulation_setup"}:
            return request.include_simulation
        if stage == "optimization_spec":
            return request.include_optimization
        return True

    @staticmethod
    def _invalidated_keys(state: JobState, from_stage: str) -> list[str]:
        cutoff = STAGES.index(from_stage)
        stage_keys = set(STAGES[cutoff:])
        keys = stage_keys.union(_DERIVED_LATEST_KEYS)
        # Historical versioned Python, runner, manifest, validation, feedback, and
        # retry receipt keys are deliberately absent from this set.
        return sorted(key for key in keys if key in state.artifacts)

    @staticmethod
    def _artifact_record(state: JobState, key: str, job_dir: Path) -> dict[str, Any]:
        raw = state.artifacts.get(key)
        if not raw:
            raise ValueError(f"artifact {key} has no path")
        path = Path(raw).expanduser().resolve()
        try:
            path.relative_to(job_dir)
        except ValueError as exc:
            raise PermissionError(f"artifact {key} is outside the modeling job: {path}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"artifact {key} is missing: {path}")
        data = path.read_bytes()
        return {
            "key": key,
            "path": str(path),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    @staticmethod
    def _next_receipt(job_dir: Path, state: JobState) -> tuple[str, str]:
        revisions = {
            int(match.group(1))
            for key in state.artifacts
            if (match := _RETRY_RECEIPT_KEY.fullmatch(key))
        }
        for path in job_dir.glob("model_retry_receipt_v*.json"):
            match = re.fullmatch(r"model_retry_receipt_v(\d{3})\.json", path.name)
            if match:
                revisions.add(int(match.group(1)))
        revision = max(revisions, default=0) + 1
        key = f"model_retry_receipt_v{revision:03d}"
        return key, f"{key}.json"

    @staticmethod
    def _verify_versioned_outputs_unchanged(
        state: JobState,
        expected: dict[str, str],
        job_dir: Path,
    ) -> None:
        for key, digest in expected.items():
            record = ModelRetryService._artifact_record(state, key, job_dir)
            if record["sha256"] != digest:
                raise PermissionError(f"retry modified immutable versioned artifact {key}")
