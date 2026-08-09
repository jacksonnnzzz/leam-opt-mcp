from pathlib import Path

import pytest
from pydantic import ValidationError

from antenna_mcp.models import MetricSpec, OptimizationRequest, ParameterBound
from antenna_mcp.optimizer import OptimizationService, latin_hypercube, score_metrics
from antenna_mcp.workspace import WorkspaceStore


class QuadraticEvaluator:
    def __init__(self):
        self.best_saved = False

    def evaluate(self, parameters):
        x = parameters["x"]
        return {"loss": (x - 2.0) ** 2}

    def save_best(self, destination: Path):
        destination.write_text("mock", encoding="utf-8")
        self.best_saved = True

    def close(self):
        pass


def test_score_directions():
    specs = [
        MetricSpec(name="a", goal="minimize", weight=2),
        MetricSpec(name="b", goal="maximize"),
        MetricSpec(name="c", goal="target", target=3),
        MetricSpec(name="d", goal="upper_bound", target=-10, weight=4),
        MetricSpec(name="e", goal="lower_bound", target=1, weight=5),
    ]
    assert score_metrics(specs, {"a": 2, "b": 4, "c": 5, "d": -8, "e": 0.5}) == 12.5


def test_optimization_keeps_source_and_saves_history(tmp_path, monkeypatch):
    project = tmp_path / "input.aedt"
    project.write_text("original", encoding="utf-8")
    store = WorkspaceStore(tmp_path / "jobs")
    evaluator = QuadraticEvaluator()
    service = OptimizationService(store, lambda path, request: evaluator)
    request = OptimizationRequest(
        project_path=str(project),
        parameters=[ParameterBound(name="x", lower=-5, upper=5, unit="mm")],
        metrics=[MetricSpec(name="loss")],
        max_trials=12,
    )
    state = service.create(request)
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")
    result = service.run(state.job_id)
    assert result.status == "completed"
    assert project.read_text("utf-8") == "original"
    assert Path(result.artifacts["trials"]).read_text("utf-8").count("\n") == 12
    assert Path(result.artifacts["optimized_project"]).is_file()


def test_optimized_filename_cannot_escape_job():
    with pytest.raises(ValidationError):
        OptimizationRequest(
            project_path="input.aedt",
            parameters=[ParameterBound(name="x", lower=0, upper=1)],
            metrics=[MetricSpec(name="loss")],
            save_best_as="../outside.aedt",
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_optimization_request_rejects_non_finite_numbers(value):
    with pytest.raises(ValidationError):
        ParameterBound(name="x", lower=value, upper=1)
    with pytest.raises(ValidationError):
        MetricSpec(name="loss", target=value)


def test_latin_hypercube_uses_each_stratum_once():
    import random

    request = OptimizationRequest(
        project_path="input.aedt",
        parameters=[ParameterBound(name="x", lower=0, upper=1)],
        metrics=[MetricSpec(name="loss")],
        max_trials=5,
    )
    points = latin_hypercube(random.Random(7), request, 5)
    strata = sorted(int(point["x"] * 5) for point in points)
    assert strata == [0, 1, 2, 3, 4]


def test_adaptive_surrogate_converges_on_quadratic(tmp_path, monkeypatch):
    project = tmp_path / "input.aedt"
    project.write_text("original", encoding="utf-8")
    store = WorkspaceStore(tmp_path / "jobs")
    service = OptimizationService(store, lambda path, request: QuadraticEvaluator())
    request = OptimizationRequest(
        project_path=str(project),
        parameters=[ParameterBound(name="x", lower=-5, upper=5, unit="mm")],
        metrics=[MetricSpec(name="loss")],
        max_trials=18,
        seed=3,
        initial_samples=4,
        candidate_pool_size=256,
    )
    state = service.create(request)
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")
    result = service.run(state.job_id)
    best = Path(result.artifacts["best"]).read_text("utf-8")

    assert result.status == "completed"
    assert '"score"' in best
    import json

    payload = json.loads(best)
    assert abs(payload["parameters"]["x"] - 2.0) < 0.5
