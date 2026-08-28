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
        if stage == "model_3d":
            return "hfss.modeler.create_box(origin=[0, 0, 0], sizes=[1, 1, 1], name='part')"
        if stage == "model_2d":
            return "hfss.modeler.create_rectangle(orientation='XY', origin=[0, 0, 0], sizes=[1, 1], name='sheet')"
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
                    "input_summary": "generic parametric antenna text intent",
                    "antenna_type": "generic",
                    "coordinate_system": {
                        "plane": "XY",
                        "origin": [0, 0, 0],
                        "axes": ["x", "y", "z"],
                    },
                    "components": [],
                    "parameters": [
                        {
                            "symbol": "x",
                            "value": 0,
                            "unit": "mm",
                            "geometric_meaning": "generic optimization coordinate",
                            "evidence_source": "test intent",
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
        return json.dumps({stage: []})


class FakeModeler:
    def __init__(self):
        self.object_names = []

    def create_box(self, *args, **kwargs):
        self.object_names.append(kwargs.get("name", "part"))
        return True

    def create_rectangle(self, *args, **kwargs):
        self.object_names.append(kwargs.get("name", "sheet"))
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

    def create_setup(self, name):
        return FakeSetup(name)

    def create_linear_count_sweep(self, **kwargs):
        return True

    def save_project(self, path):
        Path(path).write_text("aedt", encoding="utf-8")
        return True

    def release_desktop(self, **kwargs):
        pass


class FakeSetup:
    def __init__(self, name):
        self.name = name
        self.props = {}

    def update(self):
        return True


class QuadraticEvaluator:
    def evaluate(self, parameters):
        return {"loss": (parameters["x"] - 2.0) ** 2}

    def convergence_evidence(self):
        return {
            "converged": True,
            "final_max_magnitude_delta_s": 0.01,
            "sweep_converged": True,
        }

    def verify_parameter_effects(self, parameters):
        return {
            "schema_version": "1.0",
            "all_parameters_effective": True,
            "parameters": [
                {"name": item.name, "geometry_changed": True} for item in parameters
            ],
        }

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
