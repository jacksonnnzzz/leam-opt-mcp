from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import antenna_mcp.iterative_reconstruction as iterative_reconstruction
import antenna_mcp.workflow_cli as workflow_cli
from antenna_mcp.assumption_search import AssumptionStudyLedger
from antenna_mcp.iterative_reconstruction import (
    diagnose_reconstruction_curve,
    load_reconstruction_target,
    propose_next_iteration,
    run_iterative_reconstruction,
)
from antenna_mcp.workflow_cli import main as workflow_main


def _write_curve(path: Path, *, minimum_db: float = -3.0) -> Path:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "frequency_ghz",
                "s11_db",
                "zin_50_real_ohm",
                "zin_50_imag_ohm",
            ]
        )
        writer.writerows(
            [
                (2.3, -0.5, 30.0, -80.0),
                (2.42, -0.8, 75.0, -280.0),
                (2.55, minimum_db, 110.0, -130.0),
                (2.7, -1.0, 20.0, 60.0),
            ]
        )
    return path


def _target() -> dict:
    return {
        "schema_version": "1.0",
        "case_id": "mukai",
        "max_iterations": 3,
        "large_reactance_ohm": 50.0,
        "target": {
            "resonant_frequency_ghz": 2.42,
            "maximum_resonant_frequency_error_ghz": 0.05,
            "maximum_minimum_s11_db": -10.0,
        },
        "previously_tested_assumptions": [],
    }


def _space() -> dict:
    return {
        "schema_version": "1.0",
        "study_id": "mukai_iterations",
        "case_id": "mukai",
        "strategy": "one_at_a_time",
        "include_baseline": True,
        "paper_parameters": {
            "patch_length": {"value": 36.0, "unit": "mm", "evidence": "paper"}
        },
        "baseline_assumptions": {
            "patch_longitudinal_offset_mm": 0.0,
            "radiation_padding_mm": 50.0,
        },
        "search_space": {
            "patch_longitudinal_offset_mm": {
                "values": [-4.25, 4.25],
                "source_status": "unresolved_from_source",
            },
            "radiation_padding_mm": {
                "values": [75.0],
                "source_status": "unresolved_from_source",
            },
        },
        "solver_gate": {"max_delta_s": 0.02},
        "acceptance": {
            "metric": "minimum_s11_db",
            "operator": "<=",
            "threshold": -10.0,
            "require_converged": True,
        },
    }


def test_diagnosis_distinguishes_convergence_from_paper_curve_gate(tmp_path):
    curve = _write_curve(tmp_path / "curve.csv")
    diagnosis = diagnose_reconstruction_curve(curve, _target())
    assert diagnosis["paper_gate_passed"] is False
    assert diagnosis["metrics"]["minimum_frequency_ghz"] == 2.55
    assert diagnosis["metrics"]["input_reactance_at_target_ohm"] == -280.0
    assert set(diagnosis["signals"]) >= {
        "resonance_above_target",
        "weak_matching",
        "large_negative_reactance_at_target",
    }


def test_iteration_proposes_only_unresolved_assumption_and_skips_tested_values(tmp_path):
    curve = _write_curve(tmp_path / "curve.csv")
    target = _target()
    target["previously_tested_assumptions"] = [
        {"patch_longitudinal_offset_mm": -4.25}
    ]
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(target), encoding="utf-8")
    space_path = tmp_path / "space.json"
    space_path.write_text(json.dumps(_space()), encoding="utf-8")

    result = propose_next_iteration(
        curve_path=curve,
        target_path=target_path,
        assumption_space_path=space_path,
        output_dir=tmp_path / "iterations",
    )

    assert result["status"] == "proposed"
    assert result["proposal"]["prior_id"] == "feed_geometry_interpretation"
    assert result["proposal"]["changed_assumptions"] == {
        "patch_longitudinal_offset_mm": 4.25
    }
    assert "patch_length" not in result["proposal"]["changed_assumptions"]
    assert Path(result["diagnosis_path"]).is_file()
    assert Path(result["iteration_path"]).is_file()


def test_repeated_planning_versions_records_and_never_repeats_proposal(tmp_path):
    curve = _write_curve(tmp_path / "curve.csv")
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(_target()), encoding="utf-8")
    space_path = tmp_path / "space.json"
    space_path.write_text(json.dumps(_space()), encoding="utf-8")
    output = tmp_path / "iterations"

    first = propose_next_iteration(
        curve_path=curve,
        target_path=target_path,
        assumption_space_path=space_path,
        output_dir=output,
    )
    second = propose_next_iteration(
        curve_path=curve,
        target_path=target_path,
        assumption_space_path=space_path,
        output_dir=output,
    )
    assert first["revision"] == 1
    assert second["revision"] == 2
    assert first["proposal"]["changed_assumptions"] != second["proposal"]["changed_assumptions"]
    assert sorted(path.name for path in output.glob("iteration_*.json")) == [
        "iteration_v001.json",
        "iteration_v002.json",
    ]


def test_cli_exposes_iteration_proposal(tmp_path, capsys):
    curve = _write_curve(tmp_path / "curve.csv")
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(_target()), encoding="utf-8")
    space_path = tmp_path / "space.json"
    space_path.write_text(json.dumps(_space()), encoding="utf-8")
    assert workflow_main(
        [
            "iteration-propose",
            "--curve",
            str(curve),
            "--target",
            str(target_path),
            "--space",
            str(space_path),
            "--output-dir",
            str(tmp_path / "iterations"),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "proposed"


def test_invalid_iteration_budget_fails_closed(tmp_path):
    target = _target()
    target["max_iterations"] = 0
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    with pytest.raises(ValueError, match="positive integer"):
        load_reconstruction_target(path)


def test_iteration_series_rejects_changed_target_snapshot(tmp_path):
    curve = _write_curve(tmp_path / "curve.csv")
    target = _target()
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(target), encoding="utf-8")
    space_path = tmp_path / "space.json"
    space_path.write_text(json.dumps(_space()), encoding="utf-8")
    output = tmp_path / "iterations"
    propose_next_iteration(
        curve_path=curve,
        target_path=target_path,
        assumption_space_path=space_path,
        output_dir=output,
    )
    target["target"]["maximum_minimum_s11_db"] = -6.0
    target_path.write_text(json.dumps(target), encoding="utf-8")
    with pytest.raises(ValueError, match="different target snapshot"):
        propose_next_iteration(
            curve_path=curve,
            target_path=target_path,
            assumption_space_path=space_path,
            output_dir=output,
        )


def test_iteration_run_closes_loop_writes_report_and_is_resumable(tmp_path, monkeypatch):
    baseline = _write_curve(tmp_path / "baseline.csv")
    target_path = tmp_path / "target.json"
    target_path.write_text(json.dumps(_target()), encoding="utf-8")
    space_path = tmp_path / "space.json"
    space_path.write_text(json.dumps(_space()), encoding="utf-8")
    adapter = tmp_path / "adapter.py"
    adapter.write_text("# fake adapter for controller test\n", encoding="utf-8")
    iterations = tmp_path / "iterations"
    study = tmp_path / "study"
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs["trial_ids"][0])
        ledger = AssumptionStudyLedger(kwargs["space_path"], kwargs["output_dir"])
        ledger.initialize()
        trial = next(item for item in ledger.trials() if item["trial_id"] == calls[-1])
        directory = ledger.trial_dir(trial)
        directory.mkdir(parents=True, exist_ok=True)
        curve = directory / "s11_v001.csv"
        if len(calls) == 1:
            _write_curve(curve, minimum_db=-4.0)
            frequency = 2.55
            minimum = -4.0
        else:
            with curve.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["frequency_ghz", "s11_db", "zin_50_real_ohm", "zin_50_imag_ohm"]
                )
                writer.writerows(
                    [
                        (2.3, -1.0, 50.0, -20.0),
                        (2.42, -12.0, 50.0, 0.0),
                        (2.55, -3.0, 70.0, 30.0),
                        (2.7, -1.0, 30.0, 80.0),
                    ]
                )
            frequency = 2.42
            minimum = -12.0
        ledger.record_result(
            trial,
            {
                "status": "completed",
                "design": f"Design_{len(calls)}",
                "converged": True,
                "metrics": {
                    "minimum_s11_db": minimum,
                    "resonant_frequency_ghz": frequency,
                },
            },
            curve_path=curve,
        )
        return ledger.summary()

    monkeypatch.setattr(iterative_reconstruction, "run_aedt_assumption_search", fake_run)
    result = run_iterative_reconstruction(
        baseline_curve_path=baseline,
        target_path=target_path,
        assumption_space_path=space_path,
        adapter_path=adapter,
        iteration_output_dir=iterations,
        study_output_dir=study,
        grpc_port=50051,
        active_project="Project9",
    )
    assert result["status"] == "stopped_paper_gate_passed"
    assert result["paper_gate_passed_count"] == 1
    assert result["executed_trial_ids"] == calls
    assert len(calls) == 2
    assert Path(result["report_json"]).is_file()
    assert Path(result["report_markdown"]).is_file()
    assert "Interpretation boundary" in Path(result["report_markdown"]).read_text(
        encoding="utf-8"
    )

    resumed = run_iterative_reconstruction(
        baseline_curve_path=baseline,
        target_path=target_path,
        assumption_space_path=space_path,
        adapter_path=adapter,
        iteration_output_dir=iterations,
        study_output_dir=study,
        grpc_port=50051,
        active_project="Project9",
    )
    assert resumed["report_reused"] is True
    assert resumed["executed_trial_ids"] == []
    assert len(calls) == 2

    changed_baseline = _write_curve(tmp_path / "changed-baseline.csv", minimum_db=-5.0)
    with pytest.raises(ValueError, match="baseline curve differs"):
        run_iterative_reconstruction(
            baseline_curve_path=changed_baseline,
            target_path=target_path,
            assumption_space_path=space_path,
            adapter_path=adapter,
            iteration_output_dir=iterations,
            study_output_dir=study,
            grpc_port=50051,
            active_project="Project9",
        )


def test_cli_exposes_complete_iteration_runner(tmp_path, capsys, monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return {"status": "stopped_hypotheses_exhausted"}

    monkeypatch.setattr(workflow_cli, "run_iterative_reconstruction", fake_run)
    assert workflow_cli.main(
        [
            "iteration-run",
            "--curve",
            str(tmp_path / "baseline.csv"),
            "--target",
            str(tmp_path / "target.json"),
            "--space",
            str(tmp_path / "space.json"),
            "--adapter",
            str(tmp_path / "adapter.py"),
            "--output-dir",
            str(tmp_path / "iterations"),
            "--study-output-dir",
            str(tmp_path / "study"),
            "--grpc-port",
            "50051",
            "--active-project",
            "Project9",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "stopped_hypotheses_exhausted"
    assert captured["grpc_port"] == 50051
    assert captured["active_project"] == "Project9"
