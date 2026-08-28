import json
from pathlib import Path

from antenna_mcp.codegen import PythonArtifactService
from antenna_mcp.feedback import ModelFeedbackService
from antenna_mcp.modeling import ModelingService
from antenna_mcp.models import ModelingRequest
from antenna_mcp.workspace import WorkspaceStore


class _FeedbackProvider:
    def __init__(self):
        self.prompts = []

    def generate(self, *, system, prompt, attachments):
        self.prompts.append(prompt)
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        corrected = "Move the feedline 1 mm right" in prompt
        if stage == "source_analysis":
            return json.dumps(
                {
                    "input_summary": "reviewed text intent",
                    "antenna_type": "feedline",
                    "coordinate_system": {
                        "plane": "XY",
                        "origin": [0, 0, 0],
                        "axes": ["x", "y", "z"],
                    },
                    "components": [],
                    "parameters": [
                        {
                            "symbol": "W",
                            "value": 10.0,
                            "unit": "mm",
                            "geometric_meaning": "feedline width",
                            "evidence_source": "operator-reviewed text",
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
                {"parameters": [{"name": "W", "value": 10.0, "unit": "mm"}]}
            )
        if stage == "model_3d":
            x = 1 if corrected else 0
            return f"hfss.modeler.create_box([{x}, 0, 0], ['W', '1mm', '1mm'], name='feedline')"
        if stage == "boolean":
            return "hfss.modeler.fit_all()"
        if stage == "model_2d":
            return "hfss.modeler.fit_all()"
        if stage == "dimensions":
            return json.dumps({"solids": []})
        return json.dumps({stage: []})


def test_feedback_creates_a_new_python_revision_without_aedt(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    provider = _FeedbackProvider()
    modeling = ModelingService(store, provider)
    state = modeling.create(ModelingRequest(description="Build a reviewed feedline geometry."))
    assert modeling.run(state.job_id, through_stage="boolean").status == "completed"

    first = PythonArtifactService(store, modeling).generate(state.job_id)
    feedback = ModelFeedbackService(store, modeling).submit(
        state.job_id,
        "Move the feedline 1 mm right after comparison with the source image.",
    )
    second = ModelFeedbackService(store, modeling).regenerate(state.job_id)

    assert first["revision"] == 1
    assert feedback["revision"] == 1
    assert second["python"]["revision"] == 2
    second_source = Path(second["python"]["python_file"]).read_text("utf-8")
    assert "create_box([1, 0, 0]" in second_source
    assert any("Move the feedline 1 mm right" in prompt for prompt in provider.prompts)
    assert "ansys.aedt" not in second_source


def test_feedback_freezes_comparison_image_in_job(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "Reviewed antenna geometry"})
    image = tmp_path / "comparison.png"
    image.write_bytes(b"comparison-image")

    result = ModelFeedbackService(store).submit(
        state.job_id,
        "The slot should be longer and remain centered.",
        [str(image)],
    )

    frozen = Path(result["comparison_attachments"][0]["frozen_path"])
    assert frozen.parent == store.job_dir(state.job_id)
    assert frozen.read_bytes() == b"comparison-image"
    assert store.load_state(state.job_id).current_stage == "feedback_recorded"
