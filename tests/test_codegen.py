import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from antenna_mcp.codegen import PythonArtifactService
from antenna_mcp.workspace import WorkspaceStore


class _NeverRunModeling:
    def run(self, *_args, **_kwargs):
        raise AssertionError("existing code stages must be exported without rerunning modeling")


class _FakeModeler:
    def __init__(self):
        self.created = []

    def create_box(self, origin, size, name):
        self.created.append((origin, size, name))
        return name

    def fit_all(self):
        return True


class _FakeHfss:
    def __init__(self):
        self.variables = {}
        self.modeler = _FakeModeler()

    def __setitem__(self, name, value):
        self.variables[name] = value


def _job_with_builder(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "A reviewed antenna geometry."})
    parameters = store.write_artifact(
        state.job_id,
        "parameters.json",
        json.dumps(
            {
                "parameters": [
                    {"name": "W", "value": 10.0, "unit": "mm"},
                    {
                        "name": "W2",
                        "value": 20.0,
                        "unit": "mm",
                        "expression": "2*W",
                    },
                ]
            }
        ),
    )
    model_3d = store.write_artifact(
        state.job_id,
        "model_3d.py",
        "hfss.modeler.create_box([0, 0, 0], ['W', 'W2', '1mm'], name='part')\n",
    )
    boolean = store.write_artifact(state.job_id, "boolean.py", "hfss.modeler.fit_all()\n")
    state.artifacts.update(
        parameters=str(parameters),
        model_3d=str(model_3d),
        boolean=str(boolean),
    )
    state.status = "failed"
    state.error = "an earlier AEDT launch failed"
    store.save_state(state)
    return store, state


def test_existing_builder_exports_without_aedt_or_modeling_retry(tmp_path):
    store, state = _job_with_builder(tmp_path)
    result = PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)

    exported = Path(result["python_file"])
    source = exported.read_text("utf-8")
    ast.parse(source)
    assert "ansys.aedt" not in source
    assert result["generation_requires_hfss_license"] is False
    assert result["execution_requires_aedt"] is True
    assert result["python_sha256"] == hashlib.sha256(exported.read_bytes()).hexdigest()
    exported_state = store.load_state(state.job_id)
    assert exported_state.status == "completed"
    assert exported_state.current_stage == "python_export"
    assert exported_state.error is None
    assert result["prior_job_status"] == "failed"

    spec = importlib.util.spec_from_file_location("generated_test_model", exported)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    fake = _FakeHfss()
    returned = module.build(fake)
    assert returned is fake
    assert fake.variables == {"W": "10.0mm", "W2": "2*W"}
    assert fake.modeler.created == [([0, 0, 0], ["W", "W2", "1mm"], "part")]
    assert result["revision"] == 1
    assert Path(result["latest_python_file"]).name == "generated_model.py"

    repeated = PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)
    assert repeated["revision"] == 1
    assert repeated["reused"] is True


def test_export_rejects_unresolved_parameter(tmp_path):
    store, state = _job_with_builder(tmp_path)
    Path(state.artifacts["parameters"]).write_text(
        json.dumps({"parameters": [{"name": "W", "value": None, "unit": "mm"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite numeric value"):
        PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)


def test_export_rejects_source_stage_outside_job(tmp_path):
    store, state = _job_with_builder(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    state.artifacts["model_3d"] = str(outside)
    store.save_state(state)
    with pytest.raises(PermissionError, match="outside"):
        PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)
