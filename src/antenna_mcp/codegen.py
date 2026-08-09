from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import textwrap
from pathlib import Path
from typing import Any

from .modeling import ModelingService, validate_generated_python
from .workspace import WorkspaceStore


_PYTHON_EXPORT_STAGES = {"boolean", "simulation_setup"}
_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PythonArtifactService:
    """Create a complete, import-safe Python model artifact without starting AEDT."""

    def __init__(
        self,
        store: WorkspaceStore,
        modeling: ModelingService | None = None,
    ) -> None:
        self.store = store
        self.modeling = modeling or ModelingService(store)

    def generate(self, job_id: str, through_stage: str = "boolean") -> dict[str, Any]:
        if through_stage not in _PYTHON_EXPORT_STAGES:
            raise ValueError(
                "through_stage must be 'boolean' for geometry-only code or "
                "'simulation_setup' for reviewed simulation-setup code"
            )
        state = self.store.load_state(job_id)
        if state.kind != "modeling":
            raise ValueError("a modeling job is required")

        required = {"model_3d", "boolean"}
        if through_stage == "simulation_setup":
            required.add("simulation_setup")
        # Reviewed/deterministic compilers already have these source fragments. Reuse them
        # so exporting code never retries a failed AEDT launch or requires a license.
        if not required.issubset(state.artifacts):
            state = self.modeling.run(job_id, through_stage=through_stage)
            if state.status != "completed" or not required.issubset(state.artifacts):
                raise RuntimeError(state.error or "modeling did not produce the requested code stages")

        return self.export_existing(job_id, through_stage=through_stage)

    def export_existing(
        self,
        job_id: str,
        through_stage: str = "boolean",
    ) -> dict[str, Any]:
        if through_stage not in _PYTHON_EXPORT_STAGES:
            raise ValueError("unsupported Python export stage")
        state = self.store.load_state(job_id)
        if state.kind != "modeling":
            raise ValueError("a modeling job is required")
        job_dir = self.store.job_dir(job_id)
        parameters_path = self._artifact_path(
            job_dir,
            state.artifacts.get("parameters"),
            "parameters",
        )
        source_artifacts = self._source_artifacts(state.artifacts, job_dir, through_stage)

        fragment = "\n".join(
            f"# --- {stage} ---\n{path.read_text('utf-8').strip()}"
            for stage, path in source_artifacts
        ).strip()
        validate_generated_python(fragment)
        parameters = self._parameter_assignments(parameters_path)
        source_names = [path.name for _, path in source_artifacts]
        source = self._render(job_id, source_names, fragment, parameters)
        ast.parse(source)
        validate_generated_python(source)

        fingerprint = self._input_fingerprint(
            state.artifacts,
            job_dir,
            parameters_path,
            source_artifacts,
            through_stage,
        )
        existing = self._existing_revision(state.artifacts, job_dir, fingerprint)
        if existing:
            return existing

        revision = self._next_revision(state.artifacts)
        revision_tag = f"v{revision:03d}"
        python_path = self.store.write_artifact(
            job_id,
            f"generated_model_{revision_tag}.py",
            source,
        )
        latest_path = self.store.write_artifact(job_id, "generated_model.py", source)
        python_bytes = python_path.read_bytes()
        manifest = {
            "job_id": job_id,
            "status": "completed",
            "revision": revision,
            "revision_tag": revision_tag,
            "input_fingerprint": fingerprint,
            "through_stage": through_stage,
            "prior_job_status": state.status,
            "prior_job_stage": state.current_stage,
            "prior_job_error": state.error,
            "python_file": str(python_path),
            "latest_python_file": str(latest_path),
            "python_sha256": hashlib.sha256(python_bytes).hexdigest(),
            "source_artifacts": [
                {
                    "stage": stage,
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for stage, path in source_artifacts
            ],
            "parameters": str(parameters_path),
            "parameters_sha256": hashlib.sha256(parameters_path.read_bytes()).hexdigest(),
            "parameter_count": len(parameters),
            "generation_requires_aedt": False,
            "generation_requires_hfss_license": False,
            "execution_requires_aedt": True,
            "execution_contract": "Import generated_model.py and call build(existing_hfss_object).",
        }
        manifest_path = self.store.write_artifact(
            job_id,
            f"python_export_manifest_{revision_tag}.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        latest_manifest_path = self.store.write_artifact(
            job_id,
            "python_export_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        state.artifacts[f"python_model_{revision_tag}"] = str(python_path)
        state.artifacts[f"python_export_manifest_{revision_tag}"] = str(manifest_path)
        state.artifacts["python_model"] = str(latest_path)
        state.artifacts["python_export_manifest"] = str(latest_manifest_path)
        state.status = "completed"
        state.current_stage = "python_export"
        state.error = None
        self.store.save_state(state)
        manifest["manifest"] = str(manifest_path)
        return manifest

    @classmethod
    def _source_artifacts(
        cls,
        artifacts: dict[str, str],
        job_dir: Path,
        through_stage: str,
    ) -> list[tuple[str, Path]]:
        stages = ["model_3d", "model_2d", "boolean"]
        if through_stage == "simulation_setup":
            stages.append("simulation_setup")
        result: list[tuple[str, Path]] = []
        for stage in stages:
            raw = artifacts.get(stage)
            if raw:
                result.append((stage, cls._artifact_path(job_dir, raw, stage)))
            elif stage in {"model_3d", "boolean", "simulation_setup"}:
                raise ValueError(f"{stage} artifact is required before Python export")
        return result

    @staticmethod
    def _input_fingerprint(
        artifacts: dict[str, str],
        job_dir: Path,
        parameters_path: Path,
        source_artifacts: list[tuple[str, Path]],
        through_stage: str,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(through_stage.encode("utf-8"))
        digest.update(parameters_path.read_bytes())
        for stage, path in source_artifacts:
            digest.update(stage.encode("utf-8"))
            digest.update(path.read_bytes())
        for key, raw in sorted(artifacts.items()):
            if re.fullmatch(r"model_feedback_v\d{3}", key):
                path = PythonArtifactService._artifact_path(job_dir, raw, key)
                digest.update(key.encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _next_revision(artifacts: dict[str, str]) -> int:
        revisions = [
            int(match.group(1))
            for key in artifacts
            if (match := re.fullmatch(r"python_model_v(\d{3})", key))
        ]
        return max(revisions, default=0) + 1

    @classmethod
    def _existing_revision(
        cls,
        artifacts: dict[str, str],
        job_dir: Path,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        for key, raw in sorted(artifacts.items(), reverse=True):
            if not re.fullmatch(r"python_export_manifest_v\d{3}", key):
                continue
            path = cls._artifact_path(job_dir, raw, key)
            payload = json.loads(path.read_text("utf-8"))
            python_path = Path(str(payload.get("python_file") or "")).expanduser().resolve()
            if (
                payload.get("input_fingerprint") == fingerprint
                and python_path.parent == job_dir.resolve()
                and python_path.is_file()
            ):
                payload["manifest"] = str(path)
                payload["reused"] = True
                return payload
        return None

    @staticmethod
    def _artifact_path(job_dir: Path, raw: str | None, label: str) -> Path:
        if not raw:
            raise ValueError(f"{label} artifact is required before Python export")
        path = Path(raw).expanduser().resolve()
        if path.parent != job_dir.resolve() or not path.is_file():
            raise PermissionError(f"{label} artifact is missing or outside the modeling job")
        return path

    @staticmethod
    def _parameter_assignments(path: Path) -> list[tuple[str, str]]:
        payload = json.loads(path.read_text("utf-8"))
        items = payload.get("parameters") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("parameters artifact must contain a parameters array")
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"parameters[{index}] must be an object")
            name = str(item.get("name") or "")
            if not _PARAMETER_NAME.fullmatch(name) or name in seen:
                raise ValueError(f"invalid or duplicate parameter name: {name!r}")
            seen.add(name)
            expression = item.get("expression")
            if expression is not None:
                value = str(expression).strip()
                if not value:
                    raise ValueError(f"parameter {name} has an empty expression")
            else:
                raw_value = item.get("value")
                if (
                    isinstance(raw_value, bool)
                    or not isinstance(raw_value, (int, float))
                    or not math.isfinite(raw_value)
                ):
                    raise ValueError(f"parameter {name} has no finite numeric value")
                unit = str(item.get("unit") or "").strip()
                value = f"{raw_value}{unit}"
            result.append((name, value))
        return result

    @staticmethod
    def _render(
        job_id: str,
        source_artifacts: list[str],
        fragment: str,
        parameters: list[tuple[str, str]],
    ) -> str:
        assignments = "(\n" + "".join(
            f"    ({name!r}, {value!r}),\n" for name, value in parameters
        ) + ")"
        indented_fragment = textwrap.indent(fragment, "    ") if fragment else "    pass"
        return (
            '"""Generated antenna model. Importing this file does not start AEDT.\n\n'
            "Call ``build(hfss)`` only after explicitly connecting to a licensed AEDT/HFSS "
            "session.\n"
            '"""\n\n'
            f"SOURCE_JOB_ID = {job_id!r}\n"
            f"SOURCE_ARTIFACTS = {tuple(source_artifacts)!r}\n"
            "GENERATION_REQUIRES_AEDT = False\n"
            "EXECUTION_REQUIRES_AEDT = True\n"
            f"PARAMETERS = {assignments}\n\n\n"
            "def build(hfss):\n"
            '    """Apply parameters and construct the reviewed model in an existing HFSS object."""\n'
            "    for name, value in PARAMETERS:\n"
            "        hfss[name] = value\n\n"
            f"{indented_fragment}\n"
            "    return hfss\n"
        )
