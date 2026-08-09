from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .llm import LlmProvider, provider_from_env
from .models import JobState, ModelingRequest, OptimizationPlan
from .prompts import STAGES, STAGE_INSTRUCTIONS, SYSTEM_PROMPT
from .workspace import WorkspaceStore


class UnsafeGeneratedCode(ValueError):
    pass


class ModelingService:
    def __init__(self, store: WorkspaceStore, provider: LlmProvider | None = None) -> None:
        self.store = store
        self.provider = provider

    def create(self, request: ModelingRequest) -> JobState:
        if request.backend.value != "hfss":
            raise NotImplementedError("0.1 implements HFSS; CST is reserved by the backend interface")
        for raw in request.attachments:
            path = Path(raw).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(raw)
        return self.store.create_job("modeling", request.model_dump(mode="json"))

    def run(self, job_id: str, through_stage: str = "boolean") -> JobState:
        state = self.store.load_state(job_id)
        request = ModelingRequest.model_validate(state.request)
        provider = self.provider or provider_from_env(request.model)
        approved_source = state.artifacts.get("source_analysis_approved")
        if state.status == "awaiting_review" and state.artifacts.get("source_analysis_candidate"):
            raise ValueError("approve the source refinement before generating downstream artifacts")
        if through_stage not in STAGES:
            raise ValueError(f"unknown stage: {through_stage}")
        if through_stage in {"simulation_spec", "simulation_setup"} and not request.include_simulation:
            raise ValueError("set include_simulation=true to generate simulation stages")
        if through_stage == "optimization_spec" and not request.include_optimization:
            raise ValueError("set include_optimization=true to generate an optimization specification")
        state.status = "running"
        self.store.save_state(state)
        try:
            context: list[str] = []
            feedback_context = self._feedback_context(state)
            attachments = [Path(p).expanduser().resolve() for p in request.attachments]
            for stage in STAGES:
                if stage == "model_2d" and not request.include_2d:
                    continue
                if stage in {"simulation_spec", "simulation_setup"} and not request.include_simulation:
                    continue
                if stage == "optimization_spec" and not request.include_optimization:
                    continue
                state.current_stage = stage
                self.store.save_state(state)
                prompt = self._prompt(request, stage, context, feedback_context)
                if stage == "source_analysis" and approved_source:
                    cleaned = Path(approved_source).read_text("utf-8").strip()
                    payload = json.loads(cleaned)
                    _validate_source_analysis(payload)
                    context.append(f"[source_analysis]\n{cleaned}")
                    if through_stage == "source_analysis":
                        break
                    continue
                if stage == "source_analysis" and not attachments:
                    result = json.dumps(
                        {
                            "input_summary": "Text-only antenna description; no image or PDF evidence supplied.",
                            "antenna_type": None,
                            "coordinate_system": {"plane": None, "origin": None, "axes": None},
                            "components": [],
                            "parameters": [],
                            "operations": [],
                            "uncertainties": ["Geometry must be inferred from the textual description."],
                        },
                        ensure_ascii=False,
                    )
                else:
                    # Images/PDFs are interpreted once. The validated visual artifact is then
                    # passed to every later stage, avoiding repeated and potentially inconsistent
                    # readings of the same dimension drawing.
                    stage_attachments = attachments if stage == "source_analysis" else []
                    result = provider.generate(
                        system=SYSTEM_PROMPT,
                        prompt=prompt,
                        attachments=stage_attachments,
                    )
                suffix = ".py" if stage in {"model_3d", "model_2d", "boolean", "simulation_setup"} else ".json"
                cleaned = _strip_fence(result)
                if suffix == ".json":
                    payload = json.loads(cleaned)
                    if stage == "source_analysis":
                        _validate_source_analysis(payload)
                    elif stage == "optimization_spec":
                        OptimizationPlan.model_validate(payload)
                else:
                    validate_generated_python(cleaned)
                path = self.store.write_artifact(job_id, stage + suffix, cleaned + "\n")
                state.artifacts[stage] = str(path)
                context.append(f"[{stage}]\n{cleaned}")
                self.store.save_state(state)
                if stage == through_stage:
                    break
            if through_stage in {"boolean", "simulation_setup", "optimization_spec"}:
                builder = self._assemble_builder(job_id, state)
                state.artifacts["builder"] = str(builder)
            state.status = "completed"
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.store.save_state(state)
        return state

    @staticmethod
    def _prompt(
        request: ModelingRequest,
        stage: str,
        context: list[str],
        feedback_context: str = "",
    ) -> str:
        prior = "\n\n".join(context) or "No prior artifacts."
        attachment_names = ", ".join(Path(path).name for path in request.attachments) or "none"
        feedback = feedback_context or "No operator HFSS comparison feedback has been submitted."
        return (
            f"Template: {request.template.value}\nStage: {stage}\n"
            f"Attachments: {attachment_names}\n"
            f"Antenna intent:\n{request.description}\n\nPrior validated artifacts:\n{prior}\n\n"
            f"Operator HFSS comparison feedback:\n{feedback}\n\n"
            "Apply operator feedback only when it is consistent with the approved source evidence. "
            "Do not silently change reviewed dimensions, materials, or derived relations; report "
            "conflicts as uncertainties in JSON stages.\n\n"
            f"Output contract:\n{STAGE_INSTRUCTIONS[stage]}"
        )

    def _feedback_context(self, state: JobState) -> str:
        job_dir = self.store.job_dir(state.job_id).resolve()
        payloads: list[dict[str, object]] = []
        for key, raw in sorted(state.artifacts.items()):
            if not re.fullmatch(r"model_feedback_v\d{3}", key):
                continue
            path = Path(raw).expanduser().resolve()
            if path.parent != job_dir or not path.is_file():
                raise PermissionError(f"model feedback artifact is missing or outside the job: {path}")
            payload = json.loads(path.read_text("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"{path.name} must contain a JSON object")
            payloads.append(payload)
        if not payloads:
            return ""
        text = json.dumps(payloads, ensure_ascii=False, indent=2)
        if len(text) > 60000:
            raise ValueError("model feedback context exceeds 60000 characters")
        return text

    def _assemble_builder(self, job_id: str, state: JobState) -> Path:
        sections = ["# Generated by leam-opt-mcp. Review before execution."]
        for stage in ("model_3d", "model_2d", "boolean", "simulation_setup"):
            raw = state.artifacts.get(stage)
            if raw:
                sections.append(f"\n# --- {stage} ---\n" + Path(raw).read_text("utf-8"))
        return self.store.write_artifact(job_id, "build_model.py", "\n".join(sections))


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


def _validate_source_analysis(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("source_analysis must be a JSON object")
    required = {
        "input_summary",
        "antenna_type",
        "coordinate_system",
        "components",
        "parameters",
        "operations",
        "uncertainties",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"source_analysis is missing keys: {sorted(missing)}")
    for key in ("components", "parameters", "operations", "uncertainties"):
        if not isinstance(payload[key], list):
            raise ValueError(f"source_analysis.{key} must be an array")
    coordinate_system = payload["coordinate_system"]
    if not isinstance(coordinate_system, dict):
        raise ValueError("source_analysis.coordinate_system must be an object")
    missing_coordinates = {"plane", "origin", "axes"} - coordinate_system.keys()
    if missing_coordinates:
        raise ValueError(
            "source_analysis.coordinate_system is missing keys: "
            f"{sorted(missing_coordinates)}"
        )
    if coordinate_system["axes"] is not None and not isinstance(coordinate_system["axes"], list):
        raise ValueError("source_analysis.coordinate_system.axes must be null or an array")
    component_fields = {"name", "role", "primitive", "material", "geometric_evidence", "confidence"}
    component_names: set[str] = set()
    for index, component in enumerate(payload["components"]):
        if not isinstance(component, dict) or component_fields - component.keys():
            raise ValueError(f"source_analysis.components[{index}] does not match the component contract")
        normalized_name = str(component["name"]).strip().casefold()
        if not normalized_name or normalized_name in component_names:
            raise ValueError(f"source_analysis contains a missing or duplicate component name: {component['name']!r}")
        component_names.add(normalized_name)
        _validate_confidence(component["confidence"], f"source_analysis.components[{index}].confidence")
    parameter_fields = {
        "symbol", "value", "unit", "geometric_meaning", "evidence_source", "confidence"
    }
    parameter_symbols: set[str] = set()
    for index, parameter in enumerate(payload["parameters"]):
        if not isinstance(parameter, dict) or parameter_fields - parameter.keys():
            raise ValueError(f"source_analysis.parameters[{index}] does not match the parameter contract")
        normalized_symbol = str(parameter["symbol"]).strip().strip("$").casefold()
        if not normalized_symbol or normalized_symbol in parameter_symbols:
            raise ValueError(
                f"source_analysis contains a missing or duplicate parameter symbol: {parameter['symbol']!r}"
            )
        parameter_symbols.add(normalized_symbol)
        _validate_confidence(parameter["confidence"], f"source_analysis.parameters[{index}].confidence")


def _validate_confidence(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"{field} must be a number between 0 and 1")


def validate_generated_python(source: str) -> None:
    tree = ast.parse(source)
    banned_names = {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "input",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
    }
    banned_roots = {"os", "sys", "subprocess", "socket", "shutil", "pathlib", "requests", "httpx"}
    dangerous_method_prefixes = (
        "analyze",
        "solve",
        "save",
        "export",
        "release",
        "quit",
        "shutdown",
        "terminate",
    )
    dangerous_close_methods = {
        "close",
        "close_desktop",
        "close_project",
        "closedesktop",
        "closeproject",
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise UnsafeGeneratedCode("imports are not allowed in generated fragments")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned_names:
            raise UnsafeGeneratedCode(f"call to {node.func.id} is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise UnsafeGeneratedCode("private and dunder attributes are not allowed")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in banned_roots:
            raise UnsafeGeneratedCode(f"access to {node.value.id} is not allowed")
        if isinstance(node, ast.Attribute):
            normalized = node.attr.casefold()
            if normalized in dangerous_close_methods or normalized.startswith(
                dangerous_method_prefixes
            ):
                raise UnsafeGeneratedCode(
                    f"access to dangerous HFSS/AEDT method {node.attr} is not allowed"
                )
