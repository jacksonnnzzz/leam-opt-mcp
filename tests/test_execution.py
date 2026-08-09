import json
from pathlib import Path

import pytest

from antenna_mcp.execution import HfssBuildService, _exclusive_hfss_build
from antenna_mcp.models import JobState
from antenna_mcp.review import ArtifactReviewService
from antenna_mcp.workspace import WorkspaceStore


class FakeHfss:
    def __init__(self, **kwargs):
        self.variables = {}
        self.modeler = FakeModeler()
        self.odesign = object()

    def __setitem__(self, name, value):
        self.variables[name] = value

    def save_project(self, path):
        Path(path).write_text("aedt", encoding="utf-8")
        return True

    def release_desktop(self, **kwargs):
        pass


class FakeModeler:
    def __init__(self):
        self.boxes = []

    def create_box(self, position, dimensions, name=None):
        self.boxes.append((position, dimensions, name))

    @property
    def object_names(self):
        return [item[2] for item in self.boxes if item[2]]

    @property
    def model_consistency_report(self):
        return {"Missing Objects": [], "Non-Existent Objects": []}


def test_hfss_build_lock_rejects_concurrent_same_or_other_job():
    with _exclusive_hfss_build("mdl-000000000001"):
        with pytest.raises(RuntimeError, match="already running for job"):
            with _exclusive_hfss_build("mdl-000000000001"):
                pass
        with pytest.raises(RuntimeError, match="another HFSS build"):
            with _exclusive_hfss_build("mdl-000000000002"):
                pass


def test_builder_applies_parameters_and_fragments(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    state = store.create_job("modeling", {"description": "long enough antenna description"})
    parameters = store.write_artifact(
        state.job_id,
        "parameters.json",
        json.dumps({"parameters": [{"name": "W", "value": 10, "unit": "mm"}]}),
    )
    model = store.write_artifact(
        state.job_id,
        "model_3d.py",
        "hfss.modeler.create_box([0, 0, 0], [1, 1, 1], name='patch')\n",
    )
    boolean = store.write_artifact(state.job_id, "boolean.py", "value = len([1])\n")
    state.status = "completed"
    state.artifacts = {"parameters": str(parameters), "model_3d": str(model), "boolean": str(boolean)}
    store.save_state(state)
    approval = ArtifactReviewService(store).prepare(state.job_id)["approval_hash"]
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")
    fake = FakeHfss()
    result = HfssBuildService(store, lambda **kwargs: fake).build(state.job_id, approval_hash=approval)
    assert result.status == "completed"
    assert fake.variables["W"] == "10mm"
    assert fake.modeler.boxes[0][2] == "patch"
    assert Path(result.artifacts["hfss_project"]).is_file()


def test_builder_records_hfss_constructor_failure(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    state = store.create_job("modeling", {"description": "long enough antenna description"})
    parameters = store.write_artifact(
        state.job_id,
        "parameters.json",
        json.dumps({"parameters": [{"name": "W", "value": 10, "unit": "mm"}]}),
    )
    model = store.write_artifact(state.job_id, "model_3d.py", "value = len([1])\n")
    boolean = store.write_artifact(state.job_id, "boolean.py", "value = len([1])\n")
    state.status = "completed"
    state.artifacts = {"parameters": str(parameters), "model_3d": str(model), "boolean": str(boolean)}
    store.save_state(state)
    approval = ArtifactReviewService(store).prepare(state.job_id)["approval_hash"]
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")

    def fail_factory(**kwargs):
        raise RuntimeError("HFSS unavailable")

    result = HfssBuildService(store, fail_factory).build(state.job_id, approval_hash=approval)

    assert result.status == "failed"
    assert result.current_stage == "hfss_build"
    assert "HFSS unavailable" in result.error


def test_builder_rejects_artifact_changed_after_review(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path)
    state = store.create_job("modeling", {"description": "long enough antenna description"})
    parameters = store.write_artifact(
        state.job_id,
        "parameters.json",
        json.dumps({"parameters": [{"name": "W", "value": 10, "unit": "mm"}]}),
    )
    model = store.write_artifact(state.job_id, "model_3d.py", "value = len([1])\n")
    boolean = store.write_artifact(state.job_id, "boolean.py", "value = len([1])\n")
    state.status = "completed"
    state.artifacts = {"parameters": str(parameters), "model_3d": str(model), "boolean": str(boolean)}
    store.save_state(state)
    approval = ArtifactReviewService(store).prepare(state.job_id)["approval_hash"]
    model.write_text("value = len([1, 2])\n", encoding="utf-8")
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")

    with pytest.raises(PermissionError, match="does not match"):
        HfssBuildService(store, lambda **kwargs: FakeHfss()).build(
            state.job_id,
            approval_hash=approval,
        )


def test_immutable_snapshot_detects_change_after_review_packet_was_computed(tmp_path):
    store = WorkspaceStore(tmp_path)
    state = store.create_job("modeling", {"description": "long enough antenna description"})
    parameters = store.write_artifact(
        state.job_id,
        "parameters.json",
        json.dumps({"parameters": [{"name": "W", "value": 10, "unit": "mm"}]}),
    )
    model = store.write_artifact(state.job_id, "model_3d.py", "value = len([1])\n")
    boolean = store.write_artifact(state.job_id, "boolean.py", "value = len([1])\n")
    state.status = "completed"
    state.artifacts = {"parameters": str(parameters), "model_3d": str(model), "boolean": str(boolean)}
    store.save_state(state)
    packet = ArtifactReviewService(store).prepare(state.job_id)
    model.write_text("value = len([1, 2])\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="immutable build snapshot"):
        HfssBuildService._snapshot_reviewed_artifacts(packet, store.load_state(state.job_id))


def test_empty_geometry_manifest_cannot_approve_an_empty_model():
    report = HfssBuildService._verify_geometry(FakeHfss(), {"geometry_manifest": b"{}"})
    assert report["passed"] is False
