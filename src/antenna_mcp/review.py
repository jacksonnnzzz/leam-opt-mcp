from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from .models import JobState
from .workspace import WorkspaceStore


class ArtifactReviewService:
    """Freeze generated artifacts behind a content-addressed approval token."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def prepare(self, job_id: str) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        if state.kind != "modeling" or state.status != "completed":
            raise ValueError("a completed modeling job is required for review")
        packet = self._packet(state)
        path = self.store.write_artifact(
            job_id,
            "review_packet.json",
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts["review_packet"] = str(path)
        self.store.save_state(state)
        return packet

    def verify(self, job_id: str, approval_hash: str) -> dict[str, Any]:
        state = self.store.load_state(job_id)
        packet = self._packet(state)
        if not hmac.compare_digest(packet["approval_hash"], approval_hash):
            raise PermissionError(
                "approval hash does not match the current generated artifacts; prepare and review them again"
            )
        return packet

    def _packet(self, state: JobState) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for stage, raw_path in sorted(state.artifacts.items()):
            if stage == "review_packet":
                continue
            path = Path(raw_path).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"artifact missing for review: {stage}: {path}")
            data = path.read_bytes()
            entries.append(
                {
                    "stage": stage,
                    "path": str(path),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "executable": path.suffix.lower() == ".py",
                }
            )
        canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "job_id": state.job_id,
            "artifacts": entries,
            "approval_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "approval_instruction": (
                "Inspect every artifact, especially executable Python, then pass this exact approval_hash "
                "to the build operation. Any edit invalidates the hash."
            ),
        }
