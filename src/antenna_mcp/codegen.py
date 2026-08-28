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
_NATIVE_RUNNER_NAME = "run_generated_model_in_aedt.py"


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
            existing.update(self._native_execution_metadata(through_stage))
            if through_stage == "boolean":
                entrypoints = self._write_aedt_entrypoints(
                    job_id,
                    str(existing["revision_tag"]),
                    Path(str(existing["python_file"])).name,
                    through_stage=through_stage,
                )
                existing.update(entrypoints)
                self._record_aedt_entrypoints(
                    state,
                    str(existing["revision_tag"]),
                    entrypoints,
                )
                self.store.save_state(state)
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
        native_execution = self._native_execution_metadata(through_stage)
        entrypoints = (
            self._write_aedt_entrypoints(
                job_id,
                revision_tag,
                python_path.name,
                through_stage=through_stage,
            )
            if through_stage == "boolean"
            else {}
        )
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
            "execution_contract": (
                "Import generated_model.py in external CPython/PyAEDT and call "
                "build(existing_hfss_object)."
                if through_stage == "simulation_setup"
                else "Import generated_model.py and call build(existing_hfss_object)."
            ),
            **native_execution,
            **entrypoints,
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
        if entrypoints:
            self._record_aedt_entrypoints(state, revision_tag, entrypoints)
        state.status = "completed"
        state.current_stage = "python_export"
        state.error = None
        self.store.save_state(state)
        manifest["manifest"] = str(manifest_path)
        return manifest

    @staticmethod
    def _native_execution_metadata(through_stage: str) -> dict[str, Any]:
        if through_stage == "boolean":
            return {
                "native_aedt_execution_available": True,
                "native_aedt_execution_scope": "geometry_only",
            }
        return {
            "native_aedt_execution_available": False,
            "native_aedt_execution_scope": "unsupported_for_simulation_setup",
            "native_aedt_execution_reason": (
                "The native AEDT adapter intentionally supports reviewed geometry only. "
                "Run simulation_setup exports with external CPython/PyAEDT so setup, port, "
                "and boundary APIs are available."
            ),
        }

    def _write_aedt_entrypoints(
        self,
        job_id: str,
        revision_tag: str,
        versioned_model_name: str,
        *,
        through_stage: str,
    ) -> dict[str, Any]:
        """Write thin IronPython wrappers around the single reviewed native adapter."""
        if through_stage != "boolean":
            raise ValueError("native AEDT entrypoints are restricted to geometry-only exports")
        native_runner = self._native_runner_path()
        native_runner_sha256 = hashlib.sha256(native_runner.read_bytes()).hexdigest()
        versioned_source = self._render_aedt_wrapper(
            versioned_model_name,
            native_runner,
            native_runner_sha256,
        )
        latest_source = self._render_aedt_wrapper(
            versioned_model_name,
            native_runner,
            native_runner_sha256,
        )
        versioned_path = self.store.write_artifact(
            job_id,
            f"run_in_aedt_{revision_tag}.py",
            versioned_source,
        )
        latest_path = self.store.write_artifact(job_id, "run_in_aedt.py", latest_source)
        return {
            "versioned_aedt_runner": str(versioned_path),
            "aedt_runner": str(latest_path),
            "versioned_aedt_runner_sha256": hashlib.sha256(
                versioned_path.read_bytes()
            ).hexdigest(),
            "aedt_runner_sha256": hashlib.sha256(latest_path.read_bytes()).hexdigest(),
            "native_aedt_adapter": str(native_runner),
            "native_aedt_adapter_sha256": native_runner_sha256,
            "aedt_runner_requires_source_or_installed_adapter": True,
            "aedt_runner_contract": (
                "Run this wrapper with AEDT Tools > Run Script. It creates a new HFSS "
                "design, builds geometry, and never saves or solves."
            ),
        }

    @staticmethod
    def _record_aedt_entrypoints(
        state: Any,
        revision_tag: str,
        entrypoints: dict[str, Any],
    ) -> None:
        state.artifacts[f"aedt_runner_{revision_tag}"] = str(
            entrypoints["versioned_aedt_runner"]
        )
        state.artifacts["aedt_runner"] = str(entrypoints["aedt_runner"])

    @staticmethod
    def _native_runner_path() -> Path:
        # Wheels include the exact tools/ source at this package-relative path via
        # Hatch's force-include setting. A source checkout uses tools/ directly.
        candidates = (
            Path(__file__).resolve().with_name("_aedt_native_runner.py"),
            Path(__file__).resolve().parents[2] / "tools" / _NATIVE_RUNNER_NAME,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(
            "the native AEDT adapter is missing; reinstall leam-opt-mcp or use a complete "
            "GitHub source checkout"
        )

    @staticmethod
    def _render_aedt_wrapper(
        model_name: str,
        native_runner: Path,
        native_runner_sha256: str,
    ) -> str:
        # Keep this source parseable by the IronPython 2.7 interpreter shipped with
        # AEDT 2025 R1: no annotations, f-strings, pathlib, or future imports.
        return (
            '"""AEDT entrypoint generated by leam-opt-mcp; builds but never saves or solves."""\n\n'
            "import hashlib\n"
            "import io\n"
            "import os\n"
            "import runpy\n\n"
            "HERE = os.path.dirname(os.path.abspath(__file__))\n"
            f"MODEL = os.path.join(HERE, {model_name!r})\n"
            f"RUNNER_HINT = {str(native_runner)!r}\n"
            f"EXPECTED_RUNNER_SHA256 = {native_runner_sha256!r}\n\n"
            "def _find_runner():\n"
            "    candidates = [RUNNER_HINT]\n"
            "    directory = HERE\n"
            "    while True:\n"
            f"        candidates.append(os.path.join(directory, 'tools', {_NATIVE_RUNNER_NAME!r}))\n"
            "        parent = os.path.dirname(directory)\n"
            "        if parent == directory:\n"
            "            break\n"
            "        directory = parent\n"
            "    for candidate in candidates:\n"
            "        if candidate and os.path.isfile(candidate):\n"
            "            with io.open(candidate, 'rb') as stream:\n"
            "                digest = hashlib.sha256(stream.read()).hexdigest()\n"
            "            if digest == EXPECTED_RUNNER_SHA256:\n"
            "                return candidate\n"
            "    raise RuntimeError(\n"
            "        'The reviewed AEDT adapter is missing or has changed. Re-run antenna-workflow codegen '\n"
            "        'from the installed package or the complete GitHub checkout.'\n"
            "    )\n\n"
            "if not os.path.isfile(MODEL):\n"
            "    raise RuntimeError('Generated model file does not exist: ' + MODEL)\n"
            "RUNNER = _find_runner()\n"
            "runpy.run_path(RUNNER)['run_model'](MODEL, create_new_design=True)\n"
        )

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
