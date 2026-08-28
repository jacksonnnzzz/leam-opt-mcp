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
    versioned_runner = Path(result["versioned_aedt_runner"])
    latest_runner = Path(result["aedt_runner"])
    assert versioned_runner.name == "run_in_aedt_v001.py"
    assert latest_runner.name == "run_in_aedt.py"
    assert versioned_runner.is_file()
    assert latest_runner.is_file()
    versioned_runner_source = versioned_runner.read_text("utf-8")
    latest_runner_source = latest_runner.read_text("utf-8")
    compile(versioned_runner_source, str(versioned_runner), "exec")
    assert "from __future__" not in versioned_runner_source
    assert "generated_model_v001.py" in versioned_runner_source
    assert "generated_model_v001.py" in latest_runner_source
    assert "create_new_design=True" in versioned_runner_source
    assert "SaveProject" not in versioned_runner_source
    assert "Analyze" not in versioned_runner_source
    assert result["aedt_runner_contract"].endswith("never saves or solves.")
    assert result["native_aedt_adapter_sha256"] == hashlib.sha256(
        Path(result["native_aedt_adapter"]).read_bytes()
    ).hexdigest()
    assert exported_state.artifacts["aedt_runner_v001"] == str(versioned_runner)
    assert exported_state.artifacts["aedt_runner"] == str(latest_runner)

    versioned_runner.unlink()
    latest_runner.unlink()
    repeated = PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)
    assert repeated["revision"] == 1
    assert repeated["reused"] is True
    assert Path(repeated["versioned_aedt_runner"]).is_file()
    assert Path(repeated["aedt_runner"]).is_file()


def test_export_rejects_unresolved_parameter(tmp_path):
    store, state = _job_with_builder(tmp_path)
    Path(state.artifacts["parameters"]).write_text(
        json.dumps({"parameters": [{"name": "W", "value": None, "unit": "mm"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="finite numeric value"):
        PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)


def test_simulation_export_does_not_create_or_replace_native_geometry_runner(tmp_path):
    store, state = _job_with_builder(tmp_path)
    simulation = store.write_artifact(
        state.job_id,
        "simulation_setup.py",
        "hfss.create_setup(name='Setup1')\n",
    )
    state.artifacts["simulation_setup"] = str(simulation)
    store.save_state(state)
    service = PythonArtifactService(store, _NeverRunModeling())

    geometry = service.generate(state.job_id, through_stage="boolean")
    stable_runner = Path(geometry["aedt_runner"])
    runner_before = stable_runner.read_bytes()
    assert b"generated_model_v001.py" in runner_before

    simulation_export = service.generate(state.job_id, through_stage="simulation_setup")

    assert simulation_export["revision_tag"] == "v002"
    assert simulation_export["native_aedt_execution_available"] is False
    assert simulation_export["native_aedt_execution_scope"] == "unsupported_for_simulation_setup"
    assert "external CPython/PyAEDT" in simulation_export["native_aedt_execution_reason"]
    assert "versioned_aedt_runner" not in simulation_export
    assert "aedt_runner" not in simulation_export
    assert stable_runner.read_bytes() == runner_before
    assert b"generated_model_v001.py" in stable_runner.read_bytes()
    assert b"generated_model.py" not in stable_runner.read_bytes()
    saved = store.load_state(state.job_id)
    assert saved.artifacts["aedt_runner_v001"] == str(Path(geometry["versioned_aedt_runner"]))
    assert saved.artifacts["aedt_runner"] == str(stable_runner)
    assert "aedt_runner_v002" not in saved.artifacts
    manifest = json.loads(
        Path(simulation_export["manifest"]).read_text(encoding="utf-8")
    )
    assert manifest["native_aedt_execution_available"] is False
    assert "aedt_runner" not in manifest


def test_first_simulation_export_has_no_native_aedt_wrapper(tmp_path):
    store, state = _job_with_builder(tmp_path)
    simulation = store.write_artifact(
        state.job_id,
        "simulation_setup.py",
        "hfss.create_setup(name='Setup1')\n",
    )
    state.artifacts["simulation_setup"] = str(simulation)
    store.save_state(state)

    result = PythonArtifactService(store, _NeverRunModeling()).generate(
        state.job_id,
        through_stage="simulation_setup",
    )

    assert result["revision_tag"] == "v001"
    assert result["native_aedt_execution_available"] is False
    job_dir = store.job_dir(state.job_id)
    assert not (job_dir / "run_in_aedt.py").exists()
    assert not (job_dir / "run_in_aedt_v001.py").exists()
    saved = store.load_state(state.job_id)
    assert "aedt_runner" not in saved.artifacts
    assert "aedt_runner_v001" not in saved.artifacts


def test_export_rejects_source_stage_outside_job(tmp_path):
    store, state = _job_with_builder(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    state.artifacts["model_3d"] = str(outside)
    store.save_state(state)
    with pytest.raises(PermissionError, match="outside"):
        PythonArtifactService(store, _NeverRunModeling()).generate(state.job_id)
