from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .models import JobState


class WorkspaceStore:
    """Owns all server-written files and prevents path traversal."""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.getenv("ANTENNA_MCP_WORKSPACE", ".antenna-mcp")
        self.root = Path(configured).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create_job(self, kind: str, request: dict[str, Any]) -> JobState:
        prefixes = {"modeling": "mdl", "optimization": "opt", "pipeline": "pipe"}
        try:
            prefix = prefixes[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported job kind: {kind}") from exc
        job_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
        path = self.root / job_id
        path.mkdir(parents=False)
        state = JobState(job_id=job_id, kind=kind, request=request)
        self.save_state(state)
        return state

    def job_dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"(?:mdl|opt|pipe)-[a-f0-9]{12}", job_id):
            raise ValueError("invalid job id")
        path = (self.root / job_id).resolve()
        if path.parent != self.root:
            raise ValueError("job path escapes workspace")
        if not path.is_dir():
            raise FileNotFoundError(job_id)
        return path

    def load_state(self, job_id: str) -> JobState:
        return JobState.model_validate_json((self.job_dir(job_id) / "state.json").read_text("utf-8"))

    def save_state(self, state: JobState) -> None:
        path = self.root / state.job_id / "state.json"
        self._atomic_write(path, state.model_dump_json(indent=2))

    def write_artifact(self, job_id: str, name: str, content: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("invalid artifact name")
        path = self.job_dir(job_id) / name
        self._atomic_write(path, content)
        return path

    def write_binary_artifact(self, job_id: str, name: str, content: bytes) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("invalid artifact name")
        path = self.job_dir(job_id) / name
        self._atomic_write_bytes(path, content)
        return path

    def append_jsonl(self, job_id: str, name: str, value: dict[str, Any]) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("invalid artifact name")
        path = self.job_dir(job_id) / name
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False) + "\n")
        return path

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
