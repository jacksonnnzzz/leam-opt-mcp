from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from antenna_mcp.assumption_search import (
    AssumptionSearchError,
    AssumptionStudyLedger,
    classify_assumption_failure,
    collect_convergence_evidence,
    evaluate_passband_curve,
    plan_assumption_trials,
    validate_assumption_space,
    wait_for_aedt_idle,
    _validator_rejected_build_can_be_adopted,
)
from antenna_mcp.workflow_cli import main as workflow_main


def _space() -> dict:
    return {
        "schema_version": "1.0",
        "study_id": "study",
        "case_id": "case",
        "strategy": "one_at_a_time",
        "include_baseline": True,
        "paper_parameters": {
            "patch_width": {"value": 10.0, "unit": "mm", "evidence": "paper"}
        },
        "baseline_assumptions": {"padding": 10.0, "inner": 0.5, "outer": 1.0},
        "search_space": {
            "padding": {
                "values": [5.0, 10.0, 20.0],
                "source_status": "unresolved_from_source",
            },
            "inner": {
                "values": [0.4, 1.2],
                "source_status": "unresolved_from_source",
            },
        },
        "constraints": [{"left": "inner", "operator": "<", "right": "outer"}],
        "solver_gate": {"max_delta_s": 0.02},
        "acceptance": {
            "metric": "maximum_s11_in_target_band_db",
            "operator": "<=",
            "threshold": -10.0,
            "require_converged": True,
        },
    }


def _write_space(path: Path, payload: dict | None = None) -> Path:
    path.write_text(json.dumps(payload or _space()), encoding="utf-8")
    return path


def _write_curve(path: Path, maximum: float = -11.0) -> Path:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows([(5.0, -2.0), (5.15, maximum), (5.25, -20.0), (5.35, maximum), (5.5, -2.0)])
    return path


def test_assumption_planner_is_deterministic_and_never_changes_paper_parameters():
    payload = _space()
    first = plan_assumption_trials(payload)
    second = plan_assumption_trials(payload)
    assert first == second
    assert len(first) == 4
    assert first[0]["changed_assumptions"] == {}
    assert {trial["paper_parameters_sha256"] for trial in first} == {
        first[0]["paper_parameters_sha256"]
    }
    assert all(trial["assumptions"]["inner"] < trial["assumptions"]["outer"] for trial in first)
    assert all(trial["trial_id"].startswith("ast-") for trial in first)


def test_assumption_space_rejects_paper_parameter_search_and_unlabelled_values():
    payload = _space()
    payload["search_space"]["patch_width"] = {
        "values": [9.5],
        "source_status": "unresolved_from_source",
    }
    payload["baseline_assumptions"]["patch_width"] = 10.0
    with pytest.raises(AssumptionSearchError, match="paper-explicit"):
        validate_assumption_space(payload)

    payload = _space()
    payload["search_space"]["padding"].pop("source_status")
    with pytest.raises(AssumptionSearchError, match="unresolved_from_source"):
        validate_assumption_space(payload)


def test_cartesian_planner_can_select_only_interaction_trials():
    payload = _space()
    payload["strategy"] = "cartesian"
    payload["include_baseline"] = False
    payload["minimum_changed_assumptions"] = 2
    payload["maximum_changed_assumptions"] = 2

    trials = plan_assumption_trials(payload)

    assert len(trials) == 2
    assert all(len(trial["changed_assumptions"]) == 2 for trial in trials)
    assert {tuple(trial["changed_assumptions"]) for trial in trials} == {
        ("padding", "inner")
    }


@pytest.mark.parametrize(
    ("minimum", "maximum", "message"),
    [
        (-1, 1, "0 <= minimum"),
        (2, 1, "0 <= minimum"),
        (0, 3, "cannot exceed"),
        (True, 1, "must be an integer"),
    ],
)
def test_assumption_space_rejects_invalid_changed_assumption_bounds(
    minimum, maximum, message
):
    payload = _space()
    payload["minimum_changed_assumptions"] = minimum
    payload["maximum_changed_assumptions"] = maximum
    with pytest.raises(AssumptionSearchError, match=message):
        validate_assumption_space(payload)


def test_ledger_is_immutable_resumable_and_ranks_converged_gate_passes_first(tmp_path):
    space = _write_space(tmp_path / "space.json")
    ledger = AssumptionStudyLedger(space, tmp_path / "study")
    ledger.initialize()
    trials = ledger.trials()

    passing_dir = ledger.trial_dir(trials[0])
    passing_dir.mkdir(parents=True)
    passing_curve = _write_curve(passing_dir / "s11.csv", -11.0)
    passing = ledger.record_result(
        trials[0],
        {
            "status": "completed",
            "converged": True,
            "metrics": {"maximum_s11_in_target_band_db": -11.0},
        },
        curve_path=passing_curve,
    )
    assert passing["paper_gate_passed"] is True

    nonconverged_dir = ledger.trial_dir(trials[1])
    nonconverged_dir.mkdir(parents=True)
    nonconverged_curve = _write_curve(nonconverged_dir / "s11.csv", -12.0)
    rejected = ledger.record_result(
        trials[1],
        {
            "status": "completed",
            "converged": False,
            "metrics": {"maximum_s11_in_target_band_db": -12.0},
        },
        curve_path=nonconverged_curve,
    )
    assert rejected["paper_gate_passed"] is False
    assert ledger.summary()["ranking"][0]["trial_id"] == trials[0]["trial_id"]
    assert ledger.pending_trials(resume=True) == trials[2:]
    with pytest.raises(AssumptionSearchError, match="--resume"):
        ledger.pending_trials(resume=False)

    changed = dict(passing)
    changed["metrics"] = {"maximum_s11_in_target_band_db": -13.0}
    with pytest.raises(AssumptionSearchError, match="overwrite"):
        ledger.record_result(
            trials[0],
            {
                "status": "completed",
                "converged": True,
                "metrics": changed["metrics"],
            },
            curve_path=passing_curve,
        )


def test_failed_trial_retry_appends_a_new_immutable_result_version(tmp_path):
    space = _write_space(tmp_path / "space.json")
    ledger = AssumptionStudyLedger(space, tmp_path / "study")
    ledger.initialize()
    trial = ledger.trials(limit=1)[0]
    ledger.record_result(
        trial,
        {"status": "failed", "converged": False, "metrics": {}, "error": "license"},
    )
    assert ledger.pending_trials(limit=1, resume=True) == []
    assert ledger.pending_trials(limit=1, resume=True, retry_failed=True) == [trial]
    directory = ledger.trial_dir(trial)
    curve = _write_curve(directory / "s11_v002.csv")
    ledger.record_result(
        trial,
        {
            "status": "completed",
            "converged": True,
            "metrics": {"maximum_s11_in_target_band_db": -11.0},
        },
        curve_path=curve,
        allow_retry=True,
    )
    assert [path.name for path in ledger.result_paths(trial)] == [
        "result_v001.json",
        "result_v002.json",
    ]
    assert ledger.load_results()[0]["paper_gate_passed"] is True


def test_only_unsolved_structural_validator_failure_is_eligible_for_receipt_adoption(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "status": "failed",
                "converged": False,
                "error": "RuntimeError: case violates its structural contract: objects=[]",
            }
        ),
        encoding="utf-8",
    )
    assert _validator_rejected_build_can_be_adopted(path) is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["s11"] = {"file": "s11.csv", "sha256": "x"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _validator_rejected_build_can_be_adopted(path) is False


def test_build_receipts_are_immutable_and_versioned_by_design_revision(tmp_path):
    space = _write_space(tmp_path / "space.json")
    ledger = AssumptionStudyLedger(space, tmp_path / "study")
    ledger.initialize()
    trial = ledger.trials(limit=1)[0]

    first = ledger.write_build_receipt(trial, design="Design_r1", signature={"objects": ["A"]})
    second = ledger.write_build_receipt(trial, design="Design_r2", signature={"objects": ["A", "B"]})

    assert first.name == "build_receipt.json"
    assert second.name == "build_receipt_v002.json"
    assert ledger.verify_build_receipt(trial, design="Design_r1")["signature"] == {"objects": ["A"]}
    assert ledger.verify_build_receipt(trial, design="Design_r2")["signature"] == {"objects": ["A", "B"]}
    with pytest.raises(AssumptionSearchError, match="mismatch"):
        ledger.write_build_receipt(trial, design="Design_r2", signature={"objects": []})


def test_assumption_failure_classification_separates_license_from_model_failure():
    assert classify_assumption_failure("Simulation was terminated by license error") == "license_unavailable"
    assert classify_assumption_failure("The desired vendor daemon is down") == "license_unavailable"
    assert classify_assumption_failure("Parts Patch and Probe intersect") == "geometry_validation"
    assert classify_assumption_failure("Simulation for Setup1 is already running") == "client_interrupted"
    assert classify_assumption_failure("HFSS failed to solve Setup1") == "solver_failure"


def test_wait_for_aedt_idle_drains_an_orphaned_shared_desktop_solve():
    class FakeHfss:
        def __init__(self):
            self.states = iter((True, True, False))

        @property
        def are_there_simulations_running(self):
            return next(self.states)

    wait_for_aedt_idle(FakeHfss(), timeout_seconds=1.0, poll_seconds=0.0)


def test_wait_for_aedt_idle_fails_closed_on_timeout():
    hfss = SimpleNamespace(are_there_simulations_running=True)
    with pytest.raises(RuntimeError, match="still running a simulation"):
        wait_for_aedt_idle(hfss, timeout_seconds=0.0, poll_seconds=0.0)


def test_passband_evaluator_interpolates_both_exact_paper_edges(tmp_path):
    path = tmp_path / "curve.csv"
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows([(5.0, -2.0), (5.1, -8.0), (5.2, -12.0), (5.25, -20.0), (5.3, -12.0), (5.4, -8.0), (5.5, -2.0)])
    metrics = evaluate_passband_curve(path, start_ghz=5.15, stop_ghz=5.35)
    assert metrics == pytest.approx(
        {
            "maximum_s11_in_target_band_db": -10.0,
            "minimum_s11_in_target_band_db": -20.0,
            "resonant_frequency_ghz": 5.25,
        }
    )


def test_convergence_evidence_requires_both_adaptive_and_sweep_convergence():
    passes = {
        "Adaptive Pass 1": SimpleNamespace(delta_s_max=None),
        "Adaptive Pass 2": SimpleNamespace(delta_s_max=0.08),
        "Adaptive Pass 3": SimpleNamespace(delta_s_max=0.015),
    }
    profile = SimpleNamespace(
        adaptive_pass=SimpleNamespace(steps=passes),
        frequency_sweeps={"Sweep1": SimpleNamespace(converged=True)},
    )
    hfss = SimpleNamespace(get_profile=lambda _name: {"Setup1": profile})
    result = collect_convergence_evidence(hfss, max_delta_s=0.02)
    assert result["adaptive_passes_completed"] == 3
    assert result["final_max_magnitude_delta_s"] == 0.015
    assert result["converged"] is True

    profile.frequency_sweeps["Sweep1"].converged = False
    assert collect_convergence_evidence(hfss, max_delta_s=0.02)["converged"] is False


def test_workflow_cli_plans_and_reports_assumption_study(tmp_path, capsys):
    space = _write_space(tmp_path / "space.json")
    output = tmp_path / "study"
    assert workflow_main(
        [
            "assumption-plan",
            "--space",
            str(space),
            "--output-dir",
            str(output),
            "--limit",
            "2",
        ]
    ) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "planned"
    assert planned["trial_count"] == 2
    assert workflow_main(
        ["assumption-report", "--space", str(space), "--output-dir", str(output)]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["result_count"] == 0
    assert Path(report["summary"]).is_file()
