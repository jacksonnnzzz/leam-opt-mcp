import json
from pathlib import Path

import pytest

from antenna_mcp.llm import DeepSeekChatProvider, OllamaVisionProvider
from antenna_mcp.modeling import (
    ModelingService,
    UnsafeGeneratedCode,
    _validate_source_against_attachment_contract,
    _validate_source_analysis,
    validate_generated_fragment,
    validate_generated_python,
)
from antenna_mcp.models import ModelingRequest
from antenna_mcp.workspace import WorkspaceStore


class FakeProvider:
    def generate(self, *, system, prompt, attachments):
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        if stage == "model_3d":
            return "```python\nhfss.modeler.create_box(origin=[0, 0, 0], sizes=[1, 1, 1], name='part')\n```"
        if stage == "model_2d":
            return "```python\nhfss.modeler.create_rectangle(orientation='XY', origin=[0, 0, 0], sizes=[1, 1], name='sheet')\n```"
        if stage == "boolean":
            return "```python\nhfss.modeler.subtract('part', 'tool', keep_originals=False)\n```"
        if stage == "simulation_setup":
            return "```python\nsetup = hfss.create_setup('Setup1')\nhfss.create_linear_count_sweep(setup=setup.name, unit='GHz', start_frequency=1, stop_frequency=2, num_of_freq_points=3, name='Sweep1')\n```"
        if stage == "source_analysis":
            return json.dumps(
                {
                    "input_summary": "one dimension drawing",
                    "antenna_type": "patch",
                    "coordinate_system": {"plane": "XY", "origin": "lower-left", "axes": ["x", "y"]},
                    "components": [],
                    "parameters": [],
                    "operations": [],
                    "uncertainties": [],
                }
            )
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
                    "required_reports": ["S11_dB"],
                    "uncertainties": [],
                }
            )
        return json.dumps({stage: []})


def test_pipeline_writes_all_artifacts(tmp_path):
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, FakeProvider())
    state = service.create(ModelingRequest(description="A rectangular patch antenna at 2.45 GHz."))
    result = service.run(state.job_id)
    assert result.status == "completed"
    assert "builder" in result.artifacts
    assert (store.job_dir(state.job_id) / "build_model.py").is_file()


def test_image_is_only_sent_to_source_analysis(tmp_path):
    image = tmp_path / "drawing.png"
    image.write_bytes(b"not decoded by fake provider")

    class RecordingProvider(FakeProvider):
        def __init__(self):
            self.calls = []

        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            self.calls.append((stage, [path.name for path in attachments]))
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    provider = RecordingProvider()
    store = WorkspaceStore(tmp_path / "jobs")
    service = ModelingService(store, provider)
    state = service.create(
        ModelingRequest(
            description="Reconstruct the antenna from this dimension drawing.",
            attachments=[str(image)],
        )
    )
    result = service.run(state.job_id, through_stage="dimensions")

    assert result.status == "completed"
    assert provider.calls[0] == ("source_analysis", ["drawing.png"])
    assert all(not attachments for _, attachments in provider.calls[1:])
    assert "source_analysis" in result.artifacts


def test_failed_job_resumes_at_failed_stage_without_repeating_prior_llm_calls(tmp_path):
    class FailModel3DOnce(FakeProvider):
        def __init__(self):
            self.calls = []
            self.prompts = []
            self.failed = False

        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            self.calls.append(stage)
            self.prompts.append(prompt)
            if stage == "model_3d" and not self.failed:
                self.failed = True
                raise RuntimeError("transient local-model failure")
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    provider = FailModel3DOnce()
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    state = service.create(
        ModelingRequest(
            description="A rectangular patch antenna at 2.45 GHz.",
            include_2d=False,
        )
    )

    failed = service.run(state.job_id, through_stage="boolean")
    assert failed.status == "failed"
    assert failed.current_stage == "model_3d"
    calls_before_retry = list(provider.calls)

    completed = service.run(state.job_id, through_stage="boolean")

    assert completed.status == "completed"
    assert completed.error is None
    assert calls_before_retry == [
        "source_analysis",
        "parameters",
        "materials",
        "solids",
        "dimensions",
        "model_3d",
    ]
    assert provider.calls[len(calls_before_retry) :] == ["model_3d"]
    assert "transient local-model failure" in provider.prompts[-1]
    assert "Previous fail-closed diagnostic for this same stage" in provider.prompts[-1]


def test_failed_job_can_resume_with_text_only_env_override(tmp_path, monkeypatch):
    class FailAtModel3D(FakeProvider):
        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            if stage == "model_3d":
                raise RuntimeError("local vision model timed out")
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    store = WorkspaceStore(tmp_path)
    initial = ModelingService(store, FailAtModel3D())
    state = initial.create(
        ModelingRequest(
            description="A rectangular patch antenna at 2.45 GHz.",
            include_2d=False,
            model="qwen3-vl:8b",
        )
    )
    failed = initial.run(state.job_id, through_stage="boolean")
    assert failed.status == "failed"
    assert failed.current_stage == "model_3d"

    monkeypatch.setenv("ANTENNA_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("ANTENNA_TEXT_MODEL", "deepseek-code-model")
    monkeypatch.setenv("ANTENNA_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")
    text_calls = []

    def fake_deepseek(self, *, system, prompt, attachments):
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        text_calls.append((self.model, stage, list(attachments)))
        return FakeProvider().generate(system=system, prompt=prompt, attachments=attachments)

    monkeypatch.setattr(DeepSeekChatProvider, "generate", fake_deepseek)
    monkeypatch.setattr(
        OllamaVisionProvider,
        "generate",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("resume must not send text stages to Ollama vision")
        ),
    )

    completed = ModelingService(store).run(state.job_id, through_stage="boolean")

    assert completed.status == "completed"
    assert text_calls == [
        ("deepseek-code-model", "model_3d", []),
    ]
    assert store.load_state(state.job_id).request["model"] == "qwen3-vl:8b"


def test_text_only_source_analysis_uses_provider_to_create_evidence_contract(tmp_path):
    class RecordingProvider(FakeProvider):
        def __init__(self):
            self.calls = []

        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            self.calls.append((stage, list(attachments)))
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    store = WorkspaceStore(tmp_path)
    provider = RecordingProvider()
    service = ModelingService(store, provider)
    state = service.create(ModelingRequest(description="A rectangular patch antenna at 2.45 GHz."))
    result = service.run(state.job_id, through_stage="source_analysis")

    assert result.status == "completed"
    assert provider.calls == [("source_analysis", [])]
    payload = json.loads((store.job_dir(state.job_id) / "source_analysis.json").read_text("utf-8"))
    assert payload["components"] == []
    assert payload["input_summary"] == "one dimension drawing"


def test_json_source_attachment_uses_deepseek_text_not_ollama_vision(tmp_path, monkeypatch):
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        '{"generation_evidence":{"probe_span":"ground_top_to_signal_elevation"}}',
        "utf-8",
    )
    monkeypatch.setenv("ANTENNA_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("ANTENNA_TEXT_MODEL", "deepseek-code-model")
    monkeypatch.setenv("ANTENNA_VISION_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")
    text_calls = []

    def fake_deepseek(self, *, system, prompt, attachments):
        text_calls.append((self.model, prompt, list(attachments)))
        return FakeProvider().generate(
            system=system,
            prompt=prompt,
            attachments=attachments,
        )

    monkeypatch.setattr(DeepSeekChatProvider, "generate", fake_deepseek)
    monkeypatch.setattr(
        OllamaVisionProvider,
        "generate",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("a JSON-only source must not be sent to Ollama vision")
        ),
    )

    store = WorkspaceStore(tmp_path / "jobs")
    state = ModelingService(store).create(
        ModelingRequest(
            description="Reconstruct the frozen JSON benchmark.",
            attachments=[str(benchmark)],
            model="qwen3-vl:8b",
        )
    )
    result = ModelingService(store).run(state.job_id, through_stage="source_analysis")

    assert result.status == "completed"
    assert len(text_calls) == 1
    model, prompt, attachments = text_calls[0]
    assert model == "deepseek-code-model"
    assert attachments == []
    assert "ground_top_to_signal_elevation" in prompt


def test_source_analysis_rejects_duplicate_parameter_symbols():
    payload = {
        "input_summary": "drawing",
        "antenna_type": "patch",
        "coordinate_system": {"plane": "XY", "origin": "lower-left", "axes": ["x", "y"]},
        "components": [],
        "parameters": [
            {
                "symbol": "W",
                "value": 1,
                "unit": "mm",
                "geometric_meaning": "width",
                "evidence_source": "figure",
                "confidence": 0.9,
            },
            {
                "symbol": "$W$",
                "value": 1,
                "unit": "mm",
                "geometric_meaning": "width",
                "evidence_source": "caption",
                "confidence": 0.9,
            },
        ],
        "operations": [],
        "uncertainties": [],
    }

    with pytest.raises(ValueError, match="duplicate parameter"):
        _validate_source_analysis(payload)


def test_source_analysis_rejects_axes_object_with_explicit_type_error():
    payload = {
        "input_summary": "drawing",
        "antenna_type": "patch",
        "coordinate_system": {
            "plane": "XY",
            "origin": [0, 0, 0],
            "axes": {"x": "right", "y": "up", "z": "normal"},
        },
        "components": [],
        "parameters": [],
        "operations": [],
        "uncertainties": [],
    }

    with pytest.raises(ValueError, match="axes must be null or an array; got dict"):
        _validate_source_analysis(payload)


def test_source_analysis_reports_evidence_source_alias_precisely():
    payload = {
        "input_summary": "drawing",
        "antenna_type": "patch",
        "coordinate_system": {"plane": "XY", "origin": [0, 0, 0], "axes": ["X", "Y", "Z"]},
        "components": [],
        "parameters": [
            {
                "symbol": "W",
                "value": 10,
                "unit": "mm",
                "geometric_meaning": "patch width",
                "evidence": "figure label",
                "confidence": 1.0,
            }
        ],
        "operations": [],
        "uncertainties": [],
    }

    with pytest.raises(
        ValueError,
        match="use 'evidence_source', not the unsupported alias 'evidence'",
    ):
        _validate_source_analysis(payload)


def test_source_analysis_rejects_object_summary_and_prose_relationships():
    payload = {
        "input_summary": {"summary": "not a string"},
        "antenna_type": "patch",
        "coordinate_system": {"plane": "XY", "origin": [0, 0, 0], "axes": ["X", "Y", "Z"]},
        "components": [],
        "parameters": [],
        "operations": [],
        "uncertainties": [],
    }
    with pytest.raises(ValueError, match="input_summary must be a non-empty string"):
        _validate_source_analysis(payload)

    payload["input_summary"] = "probe-fed patch"
    payload["components"] = [
        {
            "name": "Patch",
            "role": "radiator",
            "primitive": "rectangular_patch",
            "material": "copper",
            "geometric_evidence": {"z_range_mm": [0.535, 0.57]},
            "required_relationships": ["Patch is attached to signal"],
            "confidence": 1.0,
        }
    ]
    with pytest.raises(ValueError, match="must be a field name, not prose"):
        _validate_source_analysis(payload)

    payload["components"][0]["required_relationships"] = []
    payload["components"][0]["parent_layer"] = None
    with pytest.raises(ValueError, match="parent_layer is an empty optional field; omit it"):
        _validate_source_analysis(payload)


def test_frozen_attachment_contract_rejects_semantic_drift(tmp_path):
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps(
            {
                "generation_evidence": {
                    "source_contract": {
                        "component_roles": {"signal": "stackup_signal_layer"},
                        "component_material_semantics": {
                            "signal": {
                                "material": "copper",
                                "fill_material": "air",
                                "body_material": "air",
                            }
                        },
                        "component_geometric_evidence": {
                            "signal": {"z_range_mm": [0.535, 0.57]}
                        },
                        "required_relationships": {
                            "signal": ["fill_material", "body_material"]
                        },
                    }
                },
                "reference": {
                    "parameters": {"W": {"value": 10, "unit": "mm"}},
                    "objects": {
                        "signal": {
                            "role": "stackup_signal_layer",
                            "primitive": "stackup_signal_layer",
                            "material": "copper",
                        }
                    },
                    "operations": [
                        {"order": 1, "operation": "add_signal_layer", "target": "signal"}
                    ],
                },
            }
        ),
        "utf-8",
    )
    payload = {
        "components": [
            {
                "name": "signal",
                "role": "top copper plane",
                "primitive": "stackup_signal_layer",
                "material": "copper",
                "fill_material": "air",
                "body_material": "copper",
                "required_relationships": ["fill_material", "body_material"],
            }
        ],
        "parameters": [
            {"symbol": "W", "value": 11, "unit": "mm"},
            {"symbol": "resize_ratio", "value": 0.25, "unit": "ratio"},
        ],
        "operations": [],
    }

    with pytest.raises(ValueError, match="attached frozen source contract") as caught:
        _validate_source_against_attachment_contract(payload, [benchmark])

    message = str(caught.value)
    assert "stackup_signal_layer" in message
    assert "body_material" in message
    assert "geometric_evidence" in message
    assert "operation_count_mismatch" in message
    assert "parameter_set_mismatch" in message
    assert "resize_ratio" in message
    assert "derived_relations or geometric evidence" in message


def test_rejected_source_response_is_versioned_but_never_accepted(tmp_path):
    class InvalidAxesProvider(FakeProvider):
        def generate(self, *, system, prompt, attachments):
            payload = json.loads(
                super().generate(
                    system=system,
                    prompt=prompt,
                    attachments=attachments,
                )
            )
            payload["coordinate_system"]["axes"] = {
                "x": "right",
                "y": "up",
                "z": "normal",
            }
            return json.dumps(payload)

    store = WorkspaceStore(tmp_path)
    state = ModelingService(store, InvalidAxesProvider()).create(
        ModelingRequest(description="Reconstruct a reviewable patch.")
    )
    result = ModelingService(store, InvalidAxesProvider()).run(
        state.job_id,
        through_stage="source_analysis",
    )

    assert result.status == "failed"
    assert "source_analysis" not in result.artifacts
    rejected = result.artifacts["rejected_source_analysis_v001"]
    report_path = result.artifacts["rejected_source_analysis_report_v001"]
    assert '"x": "right"' in Path(rejected).read_text("utf-8")
    report = json.loads(Path(report_path).read_text("utf-8"))
    assert report["stage"] == "source_analysis"
    assert report["error_type"] == "ValueError"
    assert report["status"] == "rejected"
    assert report["candidate_sha256"]

    retried = ModelingService(store, InvalidAxesProvider()).run(
        state.job_id,
        through_stage="source_analysis",
    )
    assert retried.status == "failed"
    assert Path(retried.artifacts["rejected_source_analysis_v001"]).is_file()
    assert Path(retried.artifacts["rejected_source_analysis_v002"]).is_file()
    assert Path(retried.artifacts["rejected_source_analysis_report_v002"]).is_file()


def test_simulation_stages_are_opt_in(tmp_path):
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, FakeProvider())
    state = service.create(
        ModelingRequest(
            description="A rectangular patch antenna at 2.45 GHz with a reviewed coax feed.",
            include_simulation=True,
        )
    )
    result = service.run(state.job_id, through_stage="simulation_setup")

    assert result.status == "completed"
    assert "simulation_spec" in result.artifacts
    assert "simulation_setup" in result.artifacts
    builder = (store.job_dir(state.job_id) / "build_model.py").read_text("utf-8")
    assert "# --- simulation_setup ---" in builder


def test_simulation_stage_requires_opt_in(tmp_path):
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, FakeProvider())
    state = service.create(ModelingRequest(description="A rectangular patch antenna at 2.45 GHz."))

    with pytest.raises(ValueError, match="include_simulation"):
        service.run(state.job_id, through_stage="simulation_spec")


def test_generated_code_rejects_imports():
    with pytest.raises(UnsafeGeneratedCode):
        validate_generated_python("import os\nos.system('whoami')")


def test_generated_code_rejects_dunder_escape():
    with pytest.raises(UnsafeGeneratedCode):
        validate_generated_python("classes = ().__class__.__base__.__subclasses__()")


@pytest.mark.parametrize(
    "source",
    [
        "hfss.analyze()",
        "hfss.analyze_setup('Setup1')",
        "hfss.save_project('unreviewed.aedt')",
        "hfss.oproject.Save()",
        "hfss.post.export_report_to_file('unreviewed.csv')",
        "hfss.release_desktop()",
        "hfss.close_desktop()",
        "hfss.odesktop.QuitApplication()",
        "run_later = hfss.analyze\nrun_later()",
    ],
)
def test_generated_code_rejects_solver_file_and_lifecycle_methods(source):
    with pytest.raises(UnsafeGeneratedCode, match="dangerous HFSS/AEDT method"):
        validate_generated_python(source)


def test_generated_code_allows_reviewable_geometry_operations():
    validate_generated_python(
        "\n".join(
            [
                "box = hfss.modeler.create_box([0, 0, 0], [1, 1, 1], name='box')",
                "hfss.modeler.unite(['box', 'feed'])",
                "hfss.modeler.fit_all()",
            ]
        )
    )


@pytest.mark.parametrize(
    "source",
    [
        "def build(hfss):\n    hfss.modeler.fit_all()",
        "def helper():\n    return 1\nhfss.modeler.fit_all()",
        "async def helper():\n    return None\nhfss.modeler.fit_all()",
        "class Builder:\n    pass\nhfss.modeler.fit_all()",
        "callback = lambda: hfss.modeler.fit_all()",
    ],
)
def test_generated_fragment_rejects_function_and_class_definitions(source):
    with pytest.raises(UnsafeGeneratedCode, match="not allowed in generated fragments"):
        validate_generated_fragment(source)


def test_generated_fragment_reports_exact_forbidden_construct_and_line():
    with pytest.raises(UnsafeGeneratedCode, match=r"lambda expression at line 2"):
        validate_generated_fragment(
            "faces = hfss.modeler.get_object_faces('outer')\n"
            "selected = max(faces, key=lambda face: face)"
        )


def test_generated_fragment_requires_and_preserves_existing_hfss():
    with pytest.raises(UnsafeGeneratedCode, match="execute against the existing hfss"):
        validate_generated_fragment("size = [1, 1, 1]")
    with pytest.raises(UnsafeGeneratedCode, match="cannot rebind"):
        validate_generated_fragment("hfss = object()\nhfss.modeler.fit_all()")

    validate_generated_fragment(
        "box = hfss.modeler.create_box([0, 0, 0], [1, 1, 1], name='box')"
    )


def test_modeling_fails_closed_when_fake_provider_wraps_fragment_in_build(tmp_path):
    class WrappedFragmentProvider(FakeProvider):
        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            if stage == "model_3d":
                return "def build(hfss):\n    hfss.modeler.fit_all()"
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, WrappedFragmentProvider())
    state = service.create(
        ModelingRequest(
            description="A rectangular patch antenna at 2.45 GHz.",
            include_2d=False,
        )
    )

    result = service.run(state.job_id, through_stage="boolean")

    assert result.status == "failed"
    assert result.current_stage == "model_3d"
    assert result.error and "UnsafeGeneratedCode" in result.error
    assert "model_3d" not in result.artifacts


def test_modeling_fails_closed_when_model_stage_leaks_solver_work(tmp_path):
    class LeakingProvider(FakeProvider):
        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            if stage == "model_3d":
                return (
                    "hfss.modeler.create_box(origin=[0, 0, 0], sizes=[1, 1, 1], "
                    "name='part')\nhfss.create_setup('Setup1')"
                )
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    store = WorkspaceStore(tmp_path)
    state = ModelingService(store, LeakingProvider()).create(
        ModelingRequest(description="Create one evidence-backed box.", include_2d=False)
    )
    result = ModelingService(store, LeakingProvider()).run(
        state.job_id, through_stage="boolean"
    )

    assert result.status == "failed"
    assert result.current_stage == "model_3d"
    assert result.error and "StageOwnershipError" in result.error
    assert "model_3d" not in result.artifacts


def test_modeling_fails_closed_on_incompatible_installed_pyaedt_call(tmp_path):
    class WrongApiProvider(FakeProvider):
        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            if stage == "model_3d":
                return (
                    "hfss.modeler.create_rectangle(origin=[0, 0, 0], "
                    "sizes=[1, 1], name='Patch')"
                )
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    store = WorkspaceStore(tmp_path)
    provider = WrongApiProvider()
    state = ModelingService(store, provider).create(
        ModelingRequest(description="Create one evidence-backed sheet.", include_2d=False)
    )
    result = ModelingService(store, provider).run(state.job_id, through_stage="boolean")

    assert result.status == "failed"
    assert result.current_stage == "model_3d"
    assert result.error and "PyAedtApiContractError" in result.error
    assert "requires orientation" in result.error
    assert "model_3d" not in result.artifacts


def test_modeling_fails_closed_on_nonportable_simulation_spec(tmp_path):
    class OldSimulationSchemaProvider(FakeProvider):
        def generate(self, *, system, prompt, attachments):
            stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
            if stage == "simulation_spec":
                return json.dumps(
                    {
                        "solution_type": "Terminal",
                        "setup": {
                            "name": "Setup1",
                            "adaptive_frequency": "10GHz",
                        },
                        "sweep": {
                            "name": "Sweep",
                            "start_frequency": 8,
                            "stop_frequency": 12,
                        },
                    }
                )
            return super().generate(system=system, prompt=prompt, attachments=attachments)

    store = WorkspaceStore(tmp_path)
    provider = OldSimulationSchemaProvider()
    state = ModelingService(store, provider).create(
        ModelingRequest(
            description="Create a reviewed antenna and simulation.",
            include_2d=False,
            include_simulation=True,
        )
    )
    result = ModelingService(store, provider).run(
        state.job_id, through_stage="simulation_setup"
    )

    assert result.status == "failed"
    assert result.current_stage == "simulation_spec"
    assert result.error and "StructuredContractError" in result.error
    assert "simulation_spec.design_type" in result.error
    assert "simulation_spec" not in result.artifacts


def test_no_reviewed_boolean_operations_create_deterministic_empty_stage(tmp_path):
    store = WorkspaceStore(tmp_path)
    provider = FakeProvider()
    service = ModelingService(store, provider)
    state = service.create(
        ModelingRequest(description="Create a box without boolean operations.", include_2d=False)
    )

    result = service.run(state.job_id, through_stage="boolean")

    assert result.status == "completed"
    boolean_source = (store.job_dir(state.job_id) / "boolean.py").read_text("utf-8")
    assert boolean_source == "# No reviewed boolean operations; stage intentionally empty.\n"
