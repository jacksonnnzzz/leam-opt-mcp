import json
from pathlib import Path

from antenna_mcp.execution import HfssBuildService
from antenna_mcp.modeling import ModelingService
from antenna_mcp.models import PipelineRequest
from antenna_mcp.optimizer import OptimizationService
from antenna_mcp.pipeline import PipelineService
from antenna_mcp.workspace import WorkspaceStore


class PipelineProvider:
    def generate(self, *, system, prompt, attachments):
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        if stage in {"model_3d", "model_2d", "boolean", "simulation_setup"}:
            return "hfss.modeler.create_box([0, 0, 0], [1, 1, 1], name='part')"
        if stage == "parameters":
            return json.dumps(
                {"parameters": [{"name": "x", "value": 0, "unit": "mm", "optimizable": True}]}
            )
        if stage == "optimization_spec":
            return json.dumps(
                {
                    "design_name": "HFSSDesign1",
                    "setup_sweep": "Setup1 : Sweep1",
                    "parameters": [{"name": "x", "lower": -5, "upper": 5, "unit": "mm"}],
                    "metrics": [{"name": "loss", "expression": "loss", "goal": "minimize"}],
                    "max_trials": 6,
                    "seed": 3,
                    "strategy": "adaptive_surrogate",
                    "initial_samples": 2,
                    "candidate_pool_size": 64,
                    "exploration_weight": 1.5,
                    "initial_points": [{"x": 0}],
                    "save_best_as": "optimized.aedt",
                }
            )
        return json.dumps({stage: []})


class FakeModeler:
    def __init__(self):
        self.object_names = []

    def create_box(self, *args, **kwargs):
        self.object_names.append(kwargs.get("name", "part"))
        return True

    @property
    def model_consistency_report(self):
        return {"Missing Objects": [], "Non-Existent Objects": []}


class FakeHfss:
    def __init__(self):
        self.modeler = FakeModeler()
        self.variables = {}
        self.odesign = object()

    def __setitem__(self, name, value):
        self.variables[name] = value

    def save_project(self, path):
        Path(path).write_text("aedt", encoding="utf-8")
        return True

    def release_desktop(self, **kwargs):
        pass


class QuadraticEvaluator:
    def evaluate(self, parameters):
        return {"loss": (parameters["x"] - 2.0) ** 2}

    def save_best(self, destination):
        Path(destination).write_text("best", encoding="utf-8")

    def close(self):
        pass


def test_end_to_end_pipeline_state_machine(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "jobs")
    modeling = ModelingService(store, PipelineProvider())
    builder = HfssBuildService(store, lambda **kwargs: FakeHfss())
    optimizer = OptimizationService(store, lambda path, request: QuadraticEvaluator())
    service = PipelineService(store, modeling=modeling, builder=builder, optimizer=optimizer)
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")

    pipeline = service.create(
        PipelineRequest(description="Build and optimize a generic parametric antenna model.")
    )
    generated = service.generate(pipeline.job_id)
    approval_hash = generated["review"]["approval_hash"]
    built = service.build(pipeline.job_id, approval_hash)
    optimized = service.optimize(pipeline.job_id)

    assert generated["pipeline"]["status"] == "awaiting_review"
    assert generated["pipeline"]["current_stage"] == "user_hfss_comparison"
    assert generated["python"]["generation_requires_hfss_license"] is False
    assert Path(generated["python"]["python_file"]).is_file()
    assert built["pipeline"]["status"] == "ready_to_optimize"
    assert optimized["pipeline"]["status"] == "completed"
    final = store.load_state(pipeline.job_id)
    assert Path(final.artifacts["hfss_project"]).is_file()
    assert Path(final.artifacts["optimized_project"]).is_file()
    assert Path(final.artifacts["result"]).is_file()
