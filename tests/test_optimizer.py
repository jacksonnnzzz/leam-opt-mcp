import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from antenna_mcp.evaluators import HfssEvaluator, _format_parameter_value
from antenna_mcp.models import MetricSpec, OptimizationRequest, ParameterBound
from antenna_mcp.optimizer import OptimizationService, latin_hypercube, score_metrics
from antenna_mcp.workspace import WorkspaceStore


class QuadraticEvaluator:
    def __init__(self):
        self.best_saved = False

    def evaluate(self, parameters):
        x = parameters["x"]
        return {"loss": (x - 2.0) ** 2}

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
    assert Path(result.artifacts["optimization_snapshot"]).is_file()
    assert Path(result.artifacts["working_project"]).name == f"{state.job_id}_working.aedt"


def test_optimized_filename_cannot_escape_job():
    with pytest.raises(ValidationError):
        OptimizationRequest(
            project_path="input.aedt",
            parameters=[ParameterBound(name="x", lower=0, upper=1)],
            metrics=[MetricSpec(name="loss")],
            save_best_as="../outside.aedt",
        )


def test_optimization_request_rejects_malformed_setup_sweep():
    with pytest.raises(ValidationError, match="SetupName : SweepName"):
        OptimizationRequest(
            project_path="input.aedt",
            setup_sweep="Setup1",
            parameters=[ParameterBound(name="x", lower=0, upper=1)],
            metrics=[MetricSpec(name="loss")],
        )


def test_dimensionless_parameter_values_do_not_gain_a_fake_ratio_unit():
    assert _format_parameter_value(0.485, "ratio") == "0.485"
    assert _format_parameter_value(0.485, "dimensionless") == "0.485"
    assert _format_parameter_value(1.25, "mm") == "1.25mm"


def test_auto_s11_expression_requires_one_self_reflection():
    evaluator = object.__new__(HfssEvaluator)
    evaluator.hfss = type(
        "FakeHfss",
        (),
        {
            "get_traces_for_plot": lambda self, **kwargs: [
                "dB(S(Probe_Port,Probe_Port))",
                "dB(S(Probe_Port,Other))",
            ]
        },
    )()
    assert evaluator._resolve_expression(MetricSpec(name="s11", expression="auto_s11")) == (
        "dB(S(Probe_Port,Probe_Port))"
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
    payload = json.loads(best)
    assert abs(payload["parameters"]["x"] - 2.0) < 0.5


class SelectiveConvergenceEvaluator(QuadraticEvaluator):
    def __init__(self):
        super().__init__()
        self.last_x = 0.0

    def evaluate(self, parameters):
        self.last_x = parameters["x"]
        return super().evaluate(parameters)

    def convergence_evidence(self):
        converged = self.last_x <= 1.0
        return {
            "converged": converged,
            "final_max_magnitude_delta_s": 0.01 if converged else 0.08,
            "sweep_converged": True,
        }


def test_optimizer_rejects_unconverged_trial_even_when_score_is_better(
    tmp_path, monkeypatch
):
    project = tmp_path / "input.aedt"
    project.write_text("original", encoding="utf-8")
    store = WorkspaceStore(tmp_path / "jobs")
    evaluator = SelectiveConvergenceEvaluator()
    service = OptimizationService(store, lambda path, request: evaluator)
    request = OptimizationRequest(
        project_path=str(project),
        parameters=[ParameterBound(name="x", lower=0, upper=3, unit="mm")],
        metrics=[MetricSpec(name="loss")],
        max_trials=2,
        initial_points=[{"x": 2.0}, {"x": 1.0}],
    )
    state = service.create(request)
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")
    result = service.run(state.job_id)
    trials = [
        json.loads(line)
        for line in Path(result.artifacts["trials"]).read_text("utf-8").splitlines()
    ]
    best_payload = json.loads(Path(result.artifacts["best"]).read_text("utf-8"))

    assert result.status == "completed"
    assert trials[0]["status"] == "rejected"
    assert trials[0]["score"] is None
    assert trials[1]["status"] == "ok"
    assert best_payload["parameters"] == {"x": 1.0}


def test_optimizer_resumes_without_repeating_completed_trials(tmp_path, monkeypatch):
    project = tmp_path / "input.aedt"
    project.write_text("original", encoding="utf-8")
    store = WorkspaceStore(tmp_path / "jobs")
    evaluator = QuadraticEvaluator()
    service = OptimizationService(store, lambda path, request: evaluator)
    request = OptimizationRequest(
        project_path=str(project),
        parameters=[ParameterBound(name="x", lower=0, upper=3, unit="mm")],
        metrics=[MetricSpec(name="loss")],
        max_trials=2,
        initial_points=[{"x": 0.0}, {"x": 2.0}],
    )
    state = service.create(request)
    store.append_jsonl(
        state.job_id,
        "trials.jsonl",
        {
            "trial": 1,
            "parameters": {"x": 0.0},
            "metrics": {"loss": 4.0},
            "score": 4.0,
            "status": "ok",
            "convergence": {
                "converged": True,
                "final_max_magnitude_delta_s": 0.01,
            },
            "error": None,
        },
    )
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")
    result = service.run(state.job_id)

    assert result.status == "completed"
    assert Path(result.artifacts["trials"]).read_text("utf-8").count("\n") == 2
    best_payload = json.loads(Path(result.artifacts["best"]).read_text("utf-8"))
    assert best_payload["trial"] == 2

    service.run(state.job_id)
    assert Path(result.artifacts["trials"]).read_text("utf-8").count("\n") == 2


class IneffectiveParameterEvaluator(QuadraticEvaluator):
    def verify_parameter_effects(self, parameters):
        return {
            "schema_version": "1.0",
            "all_parameters_effective": False,
            "parameters": [
                {"name": item.name, "geometry_changed": False} for item in parameters
            ],
        }


def test_optimizer_fails_before_solving_when_variable_does_not_change_geometry(
    tmp_path, monkeypatch
):
    project = tmp_path / "input.aedt"
    project.write_text("original", encoding="utf-8")
    store = WorkspaceStore(tmp_path / "jobs")
    service = OptimizationService(
        store, lambda path, request: IneffectiveParameterEvaluator()
    )
    request = OptimizationRequest(
        project_path=str(project),
        parameters=[ParameterBound(name="x", lower=0, upper=3, unit="mm")],
        metrics=[MetricSpec(name="loss")],
        max_trials=2,
    )
    state = service.create(request)
    monkeypatch.setenv("ANTENNA_MCP_ALLOW_SIMULATION", "1")
    result = service.run(state.job_id)

    assert result.status == "failed"
    assert "do not change geometry: x" in result.error
    assert not (store.job_dir(state.job_id) / "trials.jsonl").exists()


def test_optimization_preflight_records_effects_without_solving(tmp_path):
    project = tmp_path / "input.aedt"
    project.write_text("original", encoding="utf-8")
    store = WorkspaceStore(tmp_path / "jobs")
    evaluator = QuadraticEvaluator()
    service = OptimizationService(store, lambda path, request: evaluator)
    request = OptimizationRequest(
        project_path=str(project),
        parameters=[ParameterBound(name="x", lower=0, upper=3, unit="mm")],
        metrics=[MetricSpec(name="loss")],
        max_trials=2,
    )
    state = service.create(request)
    result = service.preflight(state.job_id)

    assert result.status == "created"
    assert result.current_stage == "preflight_complete"
    assert Path(result.artifacts["parameter_effects"]).is_file()
    assert not (store.job_dir(state.job_id) / "trials.jsonl").exists()
    assert evaluator.best_saved is False


def test_official_probe_patch_optimization_example_is_convergence_gated():
    root = Path(__file__).parents[1]
    request = OptimizationRequest.model_validate_json(
        (
            root
            / "examples"
            / "validation"
            / "ansys_pyaedt_probe_patch"
            / "optimization_request.example.json"
        ).read_text(encoding="utf-8")
    )

    assert request.design_name == "OfficialProbeFedPatch"
    assert request.require_convergence is True
    assert request.verify_parameter_effects is True
    assert request.max_delta_s == 0.02
    assert request.maximum_adaptive_passes == 12
    assert request.initial_points[0] == {
        "Patch_length": 9.57,
        "Patch_width": 9.25,
        "probe_x_rel": 0.485,
    }
    assert {metric.expression for metric in request.metrics} == {"auto_s11"}


def test_recorded_official_probe_patch_optimization_is_auditable():
    root = Path(__file__).parents[1]
    record = json.loads(
        (
            root
            / "examples"
            / "validation"
            / "ansys_pyaedt_probe_patch"
            / "reference_data"
            / "optimization_study_2026_08_28.json"
        ).read_text(encoding="utf-8")
    )

    assert record["benchmark_id"] == "ansys_pyaedt_probe_patch"
    assert record["provenance"]["source_project_unchanged"] is True
    assert (
        record["provenance"]["source_project_sha256_before"]
        == record["provenance"]["source_project_sha256_after"]
    )
    assert record["parameter_effect_preflight"]["passed"] is True
    assert all(
        item["geometry_changed"]
        for item in record["parameter_effect_preflight"]["parameters"]
    )

    trials = record["trials"]
    gate = record["study_design"]["acceptance_gates"]["adaptive_delta_s_limit"]
    assert len(trials) == 12
    assert [trial["trial"] for trial in trials] == list(range(1, 13))
    assert all(trial["status"] == "ok" for trial in trials)
    assert all(trial["final_max_magnitude_delta_s"] <= gate for trial in trials)
    assert all(
        trial["score"]
        == pytest.approx(
            trial["worst_s11_9p9_to_10p1_db"]
            + 0.25 * trial["s11_at_10ghz_db"]
        )
        for trial in trials
    )

    best = min(trials, key=lambda trial: trial["score"])
    outcome = record["outcome"]
    assert best["trial"] == outcome["best_trial"] == 9
    assert outcome["successful_trials"] == 12
    assert outcome["rejected_trials"] == 0
    assert outcome["failed_trials"] == 0
    assert outcome["all_trials_converged"] is True
    assert outcome["improvement"][
        "best_meets_full_9p9_to_10p1_below_minus_10_db"
    ] is True
    assert outcome["best"]["worst_s11_9p9_to_10p1_db"] < -10.0
