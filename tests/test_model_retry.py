from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from antenna_mcp.codegen import PythonArtifactService
from antenna_mcp.llm import DeepSeekChatProvider, OllamaVisionProvider
from antenna_mcp.model_retry import ModelRetryService
from antenna_mcp.modeling import ModelingService
from antenna_mcp.models import ModelingRequest
from antenna_mcp.source_refinement import _review_packet
from antenna_mcp.workflow_cli import main as workflow_main
from antenna_mcp.workspace import WorkspaceStore


class StageProvider:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    def generate(self, *, system, prompt, attachments):
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        self.calls.append(stage)
        if stage == "model_3d":
            return (
                "hfss.modeler.create_box(origin=[0, 0, 0], sizes=[1, 1, 1], "
                f"name={('part_' + self.label)!r})"
            )
        if stage == "model_2d":
            return (
                "hfss.modeler.create_rectangle(orientation='XY', origin=[0, 0, 0], "
                f"sizes=[1, 1], name={('sheet_' + self.label)!r})"
            )
        if stage == "boolean":
            return "hfss.modeler.subtract('part', 'tool', keep_originals=False)"
        if stage == "simulation_setup":
            return (
                "setup = hfss.create_setup('Setup1')\n"
                "hfss.create_linear_count_sweep(setup=setup.name, unit='GHz', "
                "start_frequency=1, stop_frequency=2, num_of_freq_points=3, "
                "name='Sweep1')"
            )
        if stage == "source_analysis":
            return json.dumps(
                {
                    "input_summary": "review fixture",
                    "antenna_type": "patch",
                    "coordinate_system": {
                        "plane": "XY",
                        "origin": "lower-left",
                        "axes": ["x", "y"],
                    },
                    "components": [],
                    "parameters": [
                        {
                            "symbol": "width",
                            "value": 10,
                            "unit": "mm",
                            "geometric_meaning": "patch width",
                            "evidence_source": "review fixture",
                            "confidence": 1.0,
                        }
                    ],
                    "operations": [],
                    "derived_relations": [],
                    "uncertainties": [],
                }
            )
        if stage == "parameters":
            return json.dumps(
                {
                    "parameters": [
                        {
                            "name": "width",
                            "value": 10,
                            "unit": "mm",
                            "description": "patch width",
                            "optimizable": True,
                        }
                    ]
                }
            )
        if stage in {"materials", "solids"}:
            return json.dumps({stage: []})
        if stage == "dimensions":
            return json.dumps({"solids": []})
        if stage == "simulation_spec":
            return json.dumps(
                {
                    "design_type": "HFSS",
                    "solution_type": "Modal",
                    "setup": {
                        "name": "Setup1",
                        "type": "HFSSDriven",
                        "adaptive_frequency": {"value": 1.5, "unit": "GHz"},
                    },
                    "sweep": {
                        "name": "Sweep1",
                        "type": "Interpolating",
                        "start": {"value": 1.0, "unit": "GHz"},
                        "stop": {"value": 2.0, "unit": "GHz"},
                    },
                    "s_parameter": "S11_dB",
                }
            )
        return json.dumps({stage: [{"revision": self.label}]})


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_retry_reuses_upstream_and_preserves_versioned_python(tmp_path):
    store = WorkspaceStore(tmp_path)
    initial_provider = StageProvider("old")
    initial_modeling = ModelingService(store, initial_provider)
    created = initial_modeling.create(
        ModelingRequest(
            description="Create a reviewable rectangular patch antenna model.",
            include_2d=False,
        )
    )
    completed = initial_modeling.run(created.job_id, through_stage="boolean")
    assert completed.status == "completed"
    exported_v1 = PythonArtifactService(store).export_existing(created.job_id)

    before = store.load_state(created.job_id)
    source_hash = _sha256(before.artifacts["source_analysis"])
    parameters_hash = _sha256(before.artifacts["parameters"])
    old_materials_hash = _sha256(before.artifacts["materials"])
    versioned_model = Path(exported_v1["python_file"])
    versioned_model_hash = _sha256(versioned_model)

    replacement_provider = StageProvider("new")
    retry = ModelRetryService(
        store,
        ModelingService(store, replacement_provider),
    ).retry(
        created.job_id,
        from_stage="materials",
        through_stage="boolean",
    )

    assert retry["status"] == "completed"
    assert replacement_provider.calls == [
        "materials",
        "solids",
        "dimensions",
        "model_3d",
    ]
    after = store.load_state(created.job_id)
    assert _sha256(after.artifacts["source_analysis"]) == source_hash
    assert _sha256(after.artifacts["parameters"]) == parameters_hash
    assert _sha256(versioned_model) == versioned_model_hash
    assert after.artifacts["python_model_v001"] == str(versioned_model)
    assert "python_model" not in after.artifacts
    assert "python_export_manifest" not in after.artifacts
    assert "aedt_runner" not in after.artifacts

    receipt = json.loads(Path(retry["receipt"]).read_text("utf-8"))
    invalidated = {item["key"]: item for item in receipt["invalidated_artifacts"]}
    assert invalidated["materials"]["sha256"] == old_materials_hash
    assert invalidated["materials"]["path"].endswith("materials.json")
    assert receipt["file_policy"]["deleted_files"] == []
    assert receipt["retained_versioned_artifacts"][0]["sha256"]

    exported_v2 = PythonArtifactService(store).export_existing(created.job_id)
    assert exported_v2["revision_tag"] == "v002"
    assert _sha256(versioned_model) == versioned_model_hash


def test_retry_refuses_incomplete_source_approval_without_mutating_state(tmp_path):
    store = WorkspaceStore(tmp_path)
    modeling = ModelingService(store, StageProvider("initial"))
    state = modeling.create(
        ModelingRequest(description="Create a reviewable rectangular patch antenna model.")
    )
    modeling.run(state.job_id, through_stage="parameters")
    current = store.load_state(state.job_id)
    approved = store.write_artifact(
        state.job_id,
        "source_analysis_approved.json",
        Path(current.artifacts["source_analysis"]).read_text("utf-8"),
    )
    current.artifacts["source_analysis_approved"] = str(approved)
    store.save_state(current)
    before = (store.job_dir(state.job_id) / "state.json").read_bytes()

    with pytest.raises(ValueError, match="approval chain is incomplete"):
        ModelRetryService(store, modeling).retry(
            state.job_id,
            from_stage="parameters",
            through_stage="parameters",
        )

    assert (store.job_dir(state.job_id) / "state.json").read_bytes() == before
    assert not list(store.job_dir(state.job_id).glob("model_retry_receipt_v*.json"))


def test_retry_verifies_and_retains_completed_source_approval(tmp_path):
    store = WorkspaceStore(tmp_path)
    initial = ModelingService(store, StageProvider("initial"))
    state = initial.create(
        ModelingRequest(
            description="Create a reviewable rectangular patch antenna model.",
            include_2d=False,
        )
    )
    initial.run(state.job_id, through_stage="parameters")
    current = store.load_state(state.job_id)
    source_text = Path(current.artifacts["source_analysis"]).read_text("utf-8")
    candidate = store.write_artifact(
        state.job_id,
        "source_analysis_candidate.json",
        source_text,
    )
    report = store.write_artifact(
        state.job_id,
        "source_refinement_report.json",
        json.dumps({"quality_gate_passed": True}) + "\n",
    )
    packet_payload = _review_packet(candidate, report)
    packet = store.write_artifact(
        state.job_id,
        "source_review_packet.json",
        json.dumps(packet_payload) + "\n",
    )
    approved = store.write_artifact(
        state.job_id,
        "source_analysis_approved.json",
        source_text,
    )
    current.artifacts.update(
        {
            "source_analysis_candidate": str(candidate),
            "source_refinement_report": str(report),
            "source_review_packet": str(packet),
            "source_analysis_approved": str(approved),
        }
    )
    store.save_state(current)

    replacement = StageProvider("approved")
    result = ModelRetryService(
        store,
        ModelingService(store, replacement),
    ).retry(
        state.job_id,
        from_stage="parameters",
        through_stage="materials",
    )

    receipt = json.loads(Path(result["receipt"]).read_text("utf-8"))
    assert receipt["source_approval"] == {
        "present": True,
        "approval_hash": packet_payload["approval_hash"],
        "policy": "verified and retained in place",
    }
    after = store.load_state(state.job_id)
    assert after.artifacts["source_analysis_approved"] == str(approved)
    assert after.artifacts["source_review_packet"] == str(packet)
    assert replacement.calls == ["parameters", "materials"]

    with pytest.raises(PermissionError, match="cannot retry source_analysis"):
        ModelRetryService(store, ModelingService(store, StageProvider("blocked"))).retry(
            state.job_id,
            from_stage="source_analysis",
            through_stage="materials",
        )


def test_retry_requires_all_upstream_artifacts_before_writing_receipt(tmp_path):
    store = WorkspaceStore(tmp_path)
    modeling = ModelingService(store, StageProvider("initial"))
    state = modeling.create(
        ModelingRequest(description="Create a reviewable rectangular patch antenna model.")
    )

    with pytest.raises(ValueError, match="required upstream artifact"):
        ModelRetryService(store, modeling).retry(
            state.job_id,
            from_stage="materials",
            through_stage="boolean",
        )

    assert not list(store.job_dir(state.job_id).glob("model_retry_receipt_v*.json"))


def test_workflow_cli_exposes_explicit_retry_bounds(tmp_path, monkeypatch, capsys):
    calls = []

    def fake_retry(self, job_id, *, from_stage, through_stage):
        calls.append((job_id, from_stage, through_stage))
        return {"job_id": job_id, "status": "completed"}

    monkeypatch.setattr(ModelRetryService, "retry", fake_retry)
    result = workflow_main(
        [
            "--workspace",
            str(tmp_path),
            "model-retry",
            "mdl-000000000000",
            "--from-stage",
            "materials",
            "--through-stage",
            "simulation_setup",
        ]
    )

    assert result == 0
    assert calls == [("mdl-000000000000", "materials", "simulation_setup")]
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_retry_fails_closed_when_fake_provider_returns_build_function(tmp_path):
    class WrappedFragmentProvider(StageProvider):
        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            self.calls.append(stage)
            if stage == "model_3d":
                return "def build(hfss):\n    hfss.modeler.fit_all()"
            if stage in {"model_2d", "boolean", "simulation_setup"}:
                return "hfss.modeler.fit_all()"
            if stage == "source_analysis":
                return super().generate(
                    system=system,
                    prompt=prompt,
                    attachments=attachments,
                )
            if stage == "parameters":
                return super().generate(
                    system=system,
                    prompt=prompt,
                    attachments=attachments,
                )
            if stage in {"materials", "solids"}:
                return json.dumps({stage: []})
            if stage == "dimensions":
                return json.dumps({"solids": []})
            return json.dumps({stage: [{"revision": self.label}]})

    store = WorkspaceStore(tmp_path)
    initial = ModelingService(store, StageProvider("initial"))
    state = initial.create(
        ModelingRequest(
            description="Create a reviewable rectangular patch antenna model.",
            include_2d=False,
        )
    )
    completed = initial.run(state.job_id, through_stage="boolean")
    assert completed.status == "completed"

    provider = WrappedFragmentProvider("wrapped")
    result = ModelRetryService(
        store,
        ModelingService(store, provider),
    ).retry(
        state.job_id,
        from_stage="materials",
        through_stage="boolean",
    )

    assert result["status"] == "failed"
    assert result["current_stage"] == "model_3d"
    assert result["error"] and "UnsafeGeneratedCode" in result["error"]
    assert provider.calls == ["materials", "solids", "dimensions", "model_3d"]
    assert "model_3d" not in result["artifacts"]


def test_source_retry_with_json_attachment_uses_split_text_provider(tmp_path, monkeypatch):
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        '{"generation_evidence":{"probe_span":"ground_top_to_signal_elevation"}}',
        "utf-8",
    )
    store = WorkspaceStore(tmp_path / "jobs")
    initial = ModelingService(store, StageProvider("initial"))
    state = initial.create(
        ModelingRequest(
            description="Reconstruct the frozen JSON benchmark.",
            attachments=[str(benchmark)],
            model="qwen3-vl:8b",
        )
    )
    assert initial.run(state.job_id, through_stage="source_analysis").status == "completed"

    monkeypatch.setenv("ANTENNA_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("ANTENNA_TEXT_MODEL", "deepseek-code-model")
    monkeypatch.setenv("ANTENNA_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")
    text_calls = []

    def fake_deepseek(self, *, system, prompt, attachments):
        text_calls.append((self.model, prompt, list(attachments)))
        return StageProvider("replacement").generate(
            system=system,
            prompt=prompt,
            attachments=attachments,
        )

    monkeypatch.setattr(DeepSeekChatProvider, "generate", fake_deepseek)
    monkeypatch.setattr(
        OllamaVisionProvider,
        "generate",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("a JSON-only source retry must not call Ollama vision")
        ),
    )

    result = ModelRetryService(store).retry(
        state.job_id,
        from_stage="source_analysis",
        through_stage="source_analysis",
    )

    assert result["status"] == "completed"
    assert len(text_calls) == 1
    model, prompt, attachments = text_calls[0]
    assert model == "deepseek-code-model"
    assert attachments == []
    assert "ground_top_to_signal_elevation" in prompt
