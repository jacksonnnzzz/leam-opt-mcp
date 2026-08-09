import json

import pytest

from antenna_mcp.modeling import (
    ModelingService,
    UnsafeGeneratedCode,
    _validate_source_analysis,
    validate_generated_python,
)
from antenna_mcp.models import ModelingRequest
from antenna_mcp.workspace import WorkspaceStore


class FakeProvider:
    def generate(self, *, system, prompt, attachments):
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        if stage in {"model_3d", "model_2d", "boolean", "simulation_setup"}:
            return "```python\nhfss.modeler.create_box([0, 0, 0], [1, 1, 1], name='part')\n```"
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


def test_text_only_source_analysis_is_deterministic(tmp_path):
    class NeverCalled:
        def generate(self, **kwargs):
            raise AssertionError("provider should not be called for source_analysis without attachments")

    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, NeverCalled())
    state = service.create(ModelingRequest(description="A rectangular patch antenna at 2.45 GHz."))
    result = service.run(state.job_id, through_stage="source_analysis")

    assert result.status == "completed"
    payload = json.loads((store.job_dir(state.job_id) / "source_analysis.json").read_text("utf-8"))
    assert payload["components"] == []
    assert payload["uncertainties"]


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
