from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .codegen import PythonArtifactService
from .modeling import ModelingService
from .workspace import WorkspaceStore


_COMPARISON_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".pdf"}


class ModelFeedbackService:
    """Freeze user comparison feedback and generate a new offline Python revision."""

    def __init__(
        self,
        store: WorkspaceStore,
        modeling: ModelingService | None = None,
    ) -> None:
        self.store = store
        self.modeling = modeling or ModelingService(store)

    def submit(
        self,
        job_id: str,
        feedback: str,
        comparison_images: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized = feedback.strip()
        if len(normalized) < 5:
            raise ValueError("feedback must contain at least 5 non-whitespace characters")
        if len(normalized) > 20000:
            raise ValueError("feedback must not exceed 20000 characters")
        state = self.store.load_state(job_id)
        if state.kind != "modeling":
            raise ValueError("a modeling job is required")

        revision = self._next_revision(state.artifacts)
        revision_tag = f"v{revision:03d}"
        attachments: list[dict[str, Any]] = []
        for index, raw in enumerate(comparison_images or [], start=1):
            source = Path(raw).expanduser().resolve()
            if not source.is_file():
                raise FileNotFoundError(raw)
            suffix = source.suffix.lower()
            if suffix not in _COMPARISON_SUFFIXES:
                raise ValueError(f"unsupported comparison image type: {source.name}")
            data = source.read_bytes()
            frozen = self.store.write_binary_artifact(
                job_id,
                f"model_feedback_{revision_tag}_input_{index}{suffix}",
                data,
            )
            attachments.append(
                {
                    "original_name": source.name,
                    "frozen_path": str(frozen),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )

        base_python = state.artifacts.get("python_model")
        base: dict[str, Any] | None = None
        if base_python:
            base_path = Path(base_python).expanduser().resolve()
            if base_path.parent != self.store.job_dir(job_id).resolve() or not base_path.is_file():
                raise PermissionError("latest Python model is missing or outside the modeling job")
            data = base_path.read_bytes()
            base = {
                "path": str(base_path),
                "sha256": hashlib.sha256(data).hexdigest(),
            }

        payload = {
            "job_id": job_id,
            "revision": revision,
            "revision_tag": revision_tag,
            "feedback": normalized,
            "comparison_attachments": attachments,
            "base_python": base,
            "instruction": (
                "Correct the next geometry-code revision while preserving approved source "
                "dimensions, materials, and derived relations."
            ),
        }
        path = self.store.write_artifact(
            job_id,
            f"model_feedback_{revision_tag}.json",
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts[f"model_feedback_{revision_tag}"] = str(path)
        state.status = "completed"
        state.current_stage = "feedback_recorded"
        state.error = None
        self.store.save_state(state)
        return {**payload, "artifact": str(path), "status": "awaiting_regeneration"}

    def regenerate(self, job_id: str) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        if state.kind != "modeling":
            raise ValueError("a modeling job is required")
        feedback_keys = sorted(
            key for key in state.artifacts if re.fullmatch(r"model_feedback_v\d{3}", key)
        )
        if not feedback_keys:
            raise ValueError("submit model feedback before requesting regeneration")
        generated = self.modeling.run(job_id, through_stage="boolean")
        if generated.status != "completed":
            raise RuntimeError(generated.error or "feedback regeneration failed")
        python_result = PythonArtifactService(
            self.store,
            modeling=self.modeling,
        ).export_existing(job_id, through_stage="boolean")
        return {
            "job_id": job_id,
            "status": "awaiting_user_comparison",
            "feedback_artifact": generated.artifacts[feedback_keys[-1]],
            "python": python_result,
        }

    @staticmethod
    def _next_revision(artifacts: dict[str, str]) -> int:
        revisions = [
            int(match.group(1))
            for key in artifacts
            if (match := re.fullmatch(r"model_feedback_v(\d{3})", key))
        ]
        return max(revisions, default=0) + 1
