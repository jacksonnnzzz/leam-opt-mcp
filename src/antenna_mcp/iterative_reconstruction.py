from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .assumption_search import (
    AssumptionStudyLedger,
    canonical_json,
    load_assumption_space,
    plan_assumption_trials,
    run_aedt_assumption_search,
)


class IterativeReconstructionError(ValueError):
    """Raised when an iterative diagnosis would lose evidence separation."""


ITERATION_REPORT_FORMAT_VERSION = "1.1"


def load_reconstruction_target(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise IterativeReconstructionError("target schema_version must be '1.0'")
    for key in ("case_id", "target"):
        if key not in payload:
            raise IterativeReconstructionError(f"target is missing {key}")
    target = payload["target"]
    if not isinstance(target, dict):
        raise IterativeReconstructionError("target.target must be an object")
    for key in (
        "resonant_frequency_ghz",
        "maximum_resonant_frequency_error_ghz",
        "maximum_minimum_s11_db",
    ):
        value = target.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise IterativeReconstructionError(f"target.target.{key} must be finite")
    budget = payload.get("max_iterations", 10)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise IterativeReconstructionError("target.max_iterations must be a positive integer")
    tested = payload.get("previously_tested_assumptions", [])
    if not isinstance(tested, list) or not all(isinstance(item, dict) for item in tested):
        raise IterativeReconstructionError("previously_tested_assumptions must be an array of objects")
    return payload


def load_prior_knowledge(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = (
        Path(path).expanduser().resolve()
        if path is not None
        else Path(__file__).with_name("reconstruction_priors.json")
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0" or not isinstance(payload.get("priors"), list):
        raise IterativeReconstructionError("prior knowledge must use schema_version 1.0")
    identifiers: set[str] = set()
    for index, item in enumerate(payload["priors"]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise IterativeReconstructionError(f"priors[{index}] must have an id")
        if item["id"] in identifiers:
            raise IterativeReconstructionError(f"duplicate prior id: {item['id']}")
        identifiers.add(item["id"])
        if not isinstance(item.get("signals_any"), list) or not item["signals_any"]:
            raise IterativeReconstructionError(f"prior {item['id']} must declare signals_any")
        if not isinstance(item.get("modifiable_fields"), list) or not item["modifiable_fields"]:
            raise IterativeReconstructionError(f"prior {item['id']} must declare modifiable_fields")
    return payload["priors"]


def diagnose_reconstruction_curve(
    curve_path: str | Path, target_payload: dict[str, Any]
) -> dict[str, Any]:
    curve = Path(curve_path).expanduser().resolve()
    rows = _load_curve(curve)
    target = target_payload["target"]
    target_frequency = float(target["resonant_frequency_ghz"])
    target_point = _interpolate(rows, target_frequency)
    minimum = min(rows, key=lambda item: item["s11_db"])
    frequency_error = minimum["frequency_ghz"] - target_frequency
    frequency_limit = float(target["maximum_resonant_frequency_error_ghz"])
    s11_limit = float(target["maximum_minimum_s11_db"])
    signals: list[str] = []
    if frequency_error > frequency_limit:
        signals.append("resonance_above_target")
    elif frequency_error < -frequency_limit:
        signals.append("resonance_below_target")
    else:
        signals.append("resonance_frequency_within_gate")
    if minimum["s11_db"] > s11_limit:
        signals.append("weak_matching")
    else:
        signals.append("matching_depth_within_gate")
    reactance = target_point.get("zin_imag_ohm")
    reactance_limit = float(target_payload.get("large_reactance_ohm", 50.0))
    if reactance is not None and reactance < -reactance_limit:
        signals.append("large_negative_reactance_at_target")
    elif reactance is not None and reactance > reactance_limit:
        signals.append("large_positive_reactance_at_target")
    passed = abs(frequency_error) <= frequency_limit and minimum["s11_db"] <= s11_limit
    return {
        "schema_version": "1.0",
        "case_id": target_payload["case_id"],
        "curve": str(curve),
        "curve_sha256": hashlib.sha256(curve.read_bytes()).hexdigest(),
        "target": target,
        "metrics": {
            "minimum_frequency_ghz": minimum["frequency_ghz"],
            "minimum_s11_db": minimum["s11_db"],
            "resonant_frequency_error_ghz": frequency_error,
            "s11_at_target_frequency_db": target_point["s11_db"],
            "input_resistance_at_target_ohm": target_point.get("zin_real_ohm"),
            "input_reactance_at_target_ohm": reactance,
        },
        "signals": signals,
        "paper_gate_passed": passed,
    }


def propose_next_iteration(
    *,
    curve_path: str | Path,
    target_path: str | Path,
    assumption_space_path: str | Path,
    output_dir: str | Path,
    prior_knowledge_path: str | Path | None = None,
) -> dict[str, Any]:
    target_source = Path(target_path).expanduser().resolve()
    space_source = Path(assumption_space_path).expanduser().resolve()
    target = load_reconstruction_target(target_source)
    space = load_assumption_space(space_source)
    if target["case_id"] != space["case_id"]:
        raise IterativeReconstructionError("target and assumption space case_id differ")
    diagnosis = diagnose_reconstruction_curve(curve_path, target)
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    target_sha256 = hashlib.sha256(target_source.read_bytes()).hexdigest()
    space_sha256 = hashlib.sha256(space_source.read_bytes()).hexdigest()
    _verify_series_inputs(output, target_sha256=target_sha256, space_sha256=space_sha256)
    revision = _next_revision(output)
    if revision > int(target.get("max_iterations", 10)):
        status = "stopped_budget_exhausted"
        proposal = None
    elif diagnosis["paper_gate_passed"]:
        status = "stopped_paper_gate_passed"
        proposal = None
    else:
        priors = load_prior_knowledge(prior_knowledge_path)
        proposal = _select_trial(space, target, diagnosis, priors, output)
        status = "proposed" if proposal else "stopped_hypotheses_exhausted"
    diagnosis_path = output / f"diagnosis_v{revision:03d}.json"
    iteration_path = output / f"iteration_v{revision:03d}.json"
    diagnosis_record = {
        **diagnosis,
        "revision": revision,
        "target_file": str(target_source),
        "target_sha256": target_sha256,
        "assumption_space_file": str(space_source),
        "assumption_space_sha256": space_sha256,
    }
    with diagnosis_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(diagnosis_record, ensure_ascii=False, indent=2) + "\n")
    record = {
        "schema_version": "1.0",
        "case_id": target["case_id"],
        "revision": revision,
        "status": status,
        "diagnosis": diagnosis_path.name,
        "diagnosis_sha256": hashlib.sha256(diagnosis_path.read_bytes()).hexdigest(),
        "proposal": proposal,
    }
    with iteration_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return {**record, "diagnosis_path": str(diagnosis_path), "iteration_path": str(iteration_path)}


def run_iterative_reconstruction(
    *,
    baseline_curve_path: str | Path,
    target_path: str | Path,
    assumption_space_path: str | Path,
    adapter_path: str | Path,
    iteration_output_dir: str | Path,
    study_output_dir: str | Path,
    grpc_port: int,
    active_project: str,
    version: str | None = None,
    prior_knowledge_path: str | Path | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Run or resume the deterministic diagnose-build-solve-evaluate loop."""
    baseline_curve = Path(baseline_curve_path).expanduser().resolve()
    adapter = Path(adapter_path).expanduser().resolve()
    if not baseline_curve.is_file():
        raise FileNotFoundError(baseline_curve)
    if not adapter.is_file() or adapter.suffix.casefold() != ".py":
        raise IterativeReconstructionError("iteration adapter must be an existing Python file")

    target_source = Path(target_path).expanduser().resolve()
    space_source = Path(assumption_space_path).expanduser().resolve()
    target = load_reconstruction_target(target_source)
    space = load_assumption_space(space_source)
    if target["case_id"] != space["case_id"]:
        raise IterativeReconstructionError("target and assumption space case_id differ")

    iteration_output = Path(iteration_output_dir).expanduser().resolve()
    study_output = Path(study_output_dir).expanduser().resolve()
    iteration_output.mkdir(parents=True, exist_ok=True)
    ledger = AssumptionStudyLedger(space_source, study_output)
    ledger.initialize()
    _verify_series_inputs(
        iteration_output,
        target_sha256=hashlib.sha256(target_source.read_bytes()).hexdigest(),
        space_sha256=hashlib.sha256(space_source.read_bytes()).hexdigest(),
    )
    existing_diagnoses = sorted(
        iteration_output.glob("diagnosis_v[0-9][0-9][0-9].json")
    )
    if existing_diagnoses:
        frozen_baseline = json.loads(existing_diagnoses[0].read_text(encoding="utf-8"))
        if frozen_baseline.get("curve_sha256") != hashlib.sha256(
            baseline_curve.read_bytes()
        ).hexdigest():
            raise IterativeReconstructionError(
                "baseline curve differs from the frozen first diagnosis"
            )

    current = _latest_iteration(iteration_output)
    if current is None:
        current = propose_next_iteration(
            curve_path=baseline_curve,
            target_path=target_source,
            assumption_space_path=space_source,
            output_dir=iteration_output,
            prior_knowledge_path=prior_knowledge_path,
        )

    executed_trial_ids: list[str] = []
    execution_error: str | None = None
    while current["status"] == "proposed":
        proposal = current.get("proposal")
        if not isinstance(proposal, dict) or not isinstance(proposal.get("trial_id"), str):
            raise IterativeReconstructionError("proposed iteration has no valid trial_id")
        trial_id = proposal["trial_id"]
        trials = {item["trial_id"]: item for item in ledger.trials()}
        if trial_id not in trials:
            raise IterativeReconstructionError(
                f"iteration proposed trial {trial_id!r} outside the frozen assumption space"
            )
        trial = trials[trial_id]
        latest_path = ledger.latest_result_path(trial)
        result = (
            json.loads(latest_path.read_text(encoding="utf-8")) if latest_path is not None else None
        )
        should_run = result is None or (retry_failed and result.get("status") == "failed")
        if should_run:
            run_aedt_assumption_search(
                space_path=space_source,
                adapter_path=adapter,
                output_dir=study_output,
                grpc_port=grpc_port,
                active_project=active_project,
                version=version,
                resume=True,
                retry_failed=retry_failed,
                trial_ids=[trial_id],
            )
            executed_trial_ids.append(trial_id)
            latest_path = ledger.latest_result_path(trial)
            result = (
                json.loads(latest_path.read_text(encoding="utf-8"))
                if latest_path is not None
                else None
            )
        if result is None:
            execution_error = f"trial {trial_id} produced no immutable result"
            break
        if result.get("status") != "completed":
            execution_error = str(result.get("error", f"trial {trial_id} failed"))
            break
        curve_info = result.get("s11")
        if not isinstance(curve_info, dict) or not isinstance(curve_info.get("file"), str):
            execution_error = f"completed trial {trial_id} has no S11 artifact"
            break
        curve = ledger.trial_dir(trial) / curve_info["file"]
        if not curve.is_file() or hashlib.sha256(curve.read_bytes()).hexdigest() != curve_info.get(
            "sha256"
        ):
            execution_error = f"completed trial {trial_id} has an invalid S11 artifact"
            break
        current = propose_next_iteration(
            curve_path=curve,
            target_path=target_source,
            assumption_space_path=space_source,
            output_dir=iteration_output,
            prior_knowledge_path=prior_knowledge_path,
        )

    controller_status = "failed" if execution_error else current["status"]
    report = write_iterative_reconstruction_report(
        target_path=target_source,
        assumption_space_path=space_source,
        iteration_output_dir=iteration_output,
        study_output_dir=study_output,
        controller_status=controller_status,
        execution_error=execution_error,
    )
    return {
        **report,
        "executed_trial_ids": executed_trial_ids,
        "latest_iteration": current,
    }


def write_iterative_reconstruction_report(
    *,
    target_path: str | Path,
    assumption_space_path: str | Path,
    iteration_output_dir: str | Path,
    study_output_dir: str | Path,
    controller_status: str | None = None,
    execution_error: str | None = None,
) -> dict[str, Any]:
    """Write an immutable, reviewable report for the current loop state."""
    target_source = Path(target_path).expanduser().resolve()
    space_source = Path(assumption_space_path).expanduser().resolve()
    target = load_reconstruction_target(target_source)
    space = load_assumption_space(space_source)
    if target["case_id"] != space["case_id"]:
        raise IterativeReconstructionError("target and assumption space case_id differ")
    output = Path(iteration_output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger = AssumptionStudyLedger(space_source, study_output_dir)
    ledger.initialize()

    iteration_paths = sorted(output.glob("iteration_v[0-9][0-9][0-9].json"))
    diagnosis_paths = sorted(output.glob("diagnosis_v[0-9][0-9][0-9].json"))
    if not iteration_paths or not diagnosis_paths:
        raise IterativeReconstructionError("iteration report requires at least one diagnosis")
    if len(iteration_paths) != len(diagnosis_paths):
        raise IterativeReconstructionError("iteration and diagnosis version counts differ")
    iterations = [json.loads(path.read_text(encoding="utf-8")) for path in iteration_paths]
    diagnoses = [json.loads(path.read_text(encoding="utf-8")) for path in diagnosis_paths]
    for iteration, iteration_path, diagnosis, diagnosis_path in zip(
        iterations, iteration_paths, diagnoses, diagnosis_paths
    ):
        if (
            iteration.get("revision") != diagnosis.get("revision")
            or iteration.get("diagnosis") != diagnosis_path.name
            or iteration.get("diagnosis_sha256")
            != hashlib.sha256(diagnosis_path.read_bytes()).hexdigest()
        ):
            raise IterativeReconstructionError(
                f"iteration series integrity check failed at {iteration_path.name}"
            )
    latest_iteration = iterations[-1]
    status = controller_status or str(latest_iteration.get("status", "incomplete"))
    summary = ledger.summary()
    if summary["paper_gate_passed_count"]:
        status = "stopped_paper_gate_passed"
    elif execution_error:
        status = "failed"

    results: list[dict[str, Any]] = []
    result_paths: list[Path] = []
    for trial_result in ledger.load_results():
        trial = trial_result["trial"]
        result_path = ledger.latest_result_path(trial)
        if result_path is None:
            continue
        result_paths.append(result_path)
        curve_record = trial_result.get("s11")
        curve_path = None
        if isinstance(curve_record, dict) and isinstance(curve_record.get("file"), str):
            curve_path = ledger.trial_dir(trial) / curve_record["file"]
        item = {
            "trial_id": trial["trial_id"],
            "changed_assumptions": trial["changed_assumptions"],
            "status": trial_result["status"],
            "design": trial_result.get("design"),
            "converged": trial_result.get("converged") is True,
            "paper_gate_passed": trial_result.get("paper_gate_passed") is True,
            "metrics": trial_result.get("metrics", {}),
            "result": str(result_path),
            "decision": (
                "paper_gate_passed"
                if trial_result.get("paper_gate_passed") is True
                else "not_supported_by_paper_gate"
                if trial_result.get("status") == "completed"
                else "excluded_execution_failure"
            ),
        }
        if curve_path is not None:
            item["s11"] = str(curve_path)
            item["s11_sha256"] = curve_record.get("sha256")
        if trial_result.get("status") == "failed":
            item["failure_kind"] = trial_result.get("failure_kind")
            item["error"] = trial_result.get("error")
        results.append(item)

    state = {
        "target_sha256": hashlib.sha256(target_source.read_bytes()).hexdigest(),
        "assumption_space_sha256": hashlib.sha256(space_source.read_bytes()).hexdigest(),
        "iteration_files": [
            {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in iteration_paths
        ],
        "diagnosis_files": [
            {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in diagnosis_paths
        ],
        "result_files": [
            {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in result_paths
        ],
        "controller_status": status,
        "execution_error": execution_error,
        "report_format_version": ITERATION_REPORT_FORMAT_VERSION,
    }
    state_sha256 = hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()
    existing_reports = sorted(output.glob("iteration_report_v[0-9][0-9][0-9].json"))
    if existing_reports:
        previous = json.loads(existing_reports[-1].read_text(encoding="utf-8"))
        previous_markdown = existing_reports[-1].with_suffix(".md")
        if previous.get("state_sha256") == state_sha256 and previous_markdown.is_file():
            return {
                **previous,
                "report_json": str(existing_reports[-1]),
                "report_markdown": str(previous_markdown),
                "report_reused": True,
            }

    baseline = diagnoses[0]
    report = {
        "schema_version": "1.0",
        "report_format_version": ITERATION_REPORT_FORMAT_VERSION,
        "case_id": target["case_id"],
        "status": status,
        "stop_reason": "execution_failure" if execution_error else status,
        "paper_target": target["target"],
        "target_file": str(target_source),
        "target_sha256": state["target_sha256"],
        "assumption_space_file": str(space_source),
        "assumption_space_sha256": state["assumption_space_sha256"],
        "paper_parameters_sha256": ledger.paper_hash,
        "baseline": {
            "curve": baseline.get("curve"),
            "curve_sha256": baseline.get("curve_sha256"),
            "metrics": baseline.get("metrics", {}),
            "paper_gate_passed": baseline.get("paper_gate_passed") is True,
        },
        "iteration_count": len(iterations),
        "trial_count": len(results),
        "completed_trial_count": sum(item["status"] == "completed" for item in results),
        "failed_trial_count": sum(item["status"] == "failed" for item in results),
        "paper_gate_passed_count": sum(item["paper_gate_passed"] for item in results),
        "ranking": summary["ranking"],
        "trials": results,
        "iterations": [
            {
                "revision": item.get("revision"),
                "status": item.get("status"),
                "proposal": item.get("proposal"),
                "file": str(path),
            }
            for item, path in zip(iterations, iteration_paths)
        ],
        "execution_error": execution_error,
        "evidence_boundary": (
            "A stopped or non-passing result applies only to the frozen paper target and "
            "bounded assumption space. It does not prove the paper wrong and does not "
            "authorize changing paper-explicit parameters."
        ),
        "state_sha256": state_sha256,
    }
    revision = len(existing_reports) + 1
    json_path = output / f"iteration_report_v{revision:03d}.json"
    markdown_path = output / f"iteration_report_v{revision:03d}.md"
    with json_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    with markdown_path.open("x", encoding="utf-8") as stream:
        stream.write(_render_iteration_report(report))
    return {
        **report,
        "report_json": str(json_path),
        "report_markdown": str(markdown_path),
        "report_reused": False,
    }


def _latest_iteration(output: Path) -> dict[str, Any] | None:
    paths = sorted(output.glob("iteration_v[0-9][0-9][0-9].json"))
    if not paths:
        return None
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    return {**payload, "iteration_path": str(paths[-1])}


def _render_iteration_report(report: dict[str, Any]) -> str:
    target = report["paper_target"]
    lines = [
        f"# Iterative reconstruction report: {report['case_id']}",
        "",
        f"- Status: `{report['status']}`",
        f"- Target resonance: `{target['resonant_frequency_ghz']}` GHz",
        f"- Maximum resonance error: `{target['maximum_resonant_frequency_error_ghz']}` GHz",
        f"- Required minimum S11: `{target['maximum_minimum_s11_db']}` dB",
        f"- Completed trials: `{report['completed_trial_count']}`",
        f"- Failed trials: `{report['failed_trial_count']}`",
        f"- Paper-gate passes: `{report['paper_gate_passed_count']}`",
        "",
        "## Baseline",
        "",
        f"- Resonance: `{_format_report_number(report['baseline']['metrics'].get('minimum_frequency_ghz'))}` GHz",
        f"- Minimum S11: `{_format_report_number(report['baseline']['metrics'].get('minimum_s11_db'))}` dB",
        f"- S11 at target frequency: `{_format_report_number(report['baseline']['metrics'].get('s11_at_target_frequency_db'))}` dB",
        "",
        "## Results",
        "",
        "| Rank | Trial | Changed assumption | Converged | Resonance (GHz) | Minimum S11 (dB) | Paper gate | Decision |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    by_trial = {item["trial_id"]: item for item in report["trials"]}
    for ranked in report["ranking"]:
        item = by_trial[ranked["trial_id"]]
        metrics = item.get("metrics", {})
        change = ", ".join(
            f"{name}={value}" for name, value in item["changed_assumptions"].items()
        ) or "baseline"
        lines.append(
            "| {rank} | `{trial}` | {change} | {converged} | {frequency} | {s11} | {gate} | `{decision}` |".format(
                rank=ranked["rank"],
                trial=item["trial_id"],
                change=change,
                converged="yes" if item["converged"] else "no",
                frequency=_format_report_number(metrics.get("resonant_frequency_ghz")),
                s11=_format_report_number(metrics.get("minimum_s11_db")),
                gate="pass" if item["paper_gate_passed"] else "fail",
                decision=item["decision"],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            report["evidence_boundary"],
            "",
        ]
    )
    if report.get("execution_error"):
        lines.extend(["## Execution failure", "", str(report["execution_error"]), ""])
    return "\n".join(lines)


def _format_report_number(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.6g}"
    return "—"


def _select_trial(
    space: dict[str, Any],
    target: dict[str, Any],
    diagnosis: dict[str, Any],
    priors: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any] | None:
    paper_names = set(space["paper_parameters"])
    search_names = set(space["search_space"])
    tested = {
        canonical_json(item) for item in target.get("previously_tested_assumptions", [])
    }
    for path in sorted(output.glob("iteration_v[0-9][0-9][0-9].json")):
        item = json.loads(path.read_text(encoding="utf-8")).get("proposal")
        if item and isinstance(item.get("changed_assumptions"), dict):
            tested.add(canonical_json(item["changed_assumptions"]))
    signals = set(diagnosis["signals"])
    trials = plan_assumption_trials(space)
    candidates: list[tuple[int, int, dict[str, Any], dict[str, Any]]] = []
    for prior_order, prior in enumerate(priors):
        if not signals.intersection(prior["signals_any"]):
            continue
        fields = set(prior["modifiable_fields"])
        if fields & paper_names:
            raise IterativeReconstructionError(
                f"prior {prior['id']} attempts to modify paper parameters"
            )
        applicable_fields = fields & search_names
        for trial_order, trial in enumerate(trials):
            changed = trial["changed_assumptions"]
            if not changed or not set(changed).issubset(applicable_fields):
                continue
            if canonical_json(changed) in tested:
                continue
            candidates.append((prior_order, trial_order, prior, trial))
    if not candidates:
        return None
    _, _, prior, trial = min(candidates, key=lambda item: (item[0], item[1]))
    return {
        "prior_id": prior["id"],
        "reason": prior["reason"],
        "signals": sorted(set(prior["signals_any"]) & signals),
        "trial_id": trial["trial_id"],
        "changed_assumptions": trial["changed_assumptions"],
        "assumptions_sha256": trial["assumptions_sha256"],
        "paper_parameters_sha256": trial["paper_parameters_sha256"],
        "expected_observation": prior["expected_observation"],
    }


def _load_curve(path: Path) -> list[dict[str, float]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"frequency_ghz", "s11_db"}.issubset(reader.fieldnames):
            raise IterativeReconstructionError("curve must contain frequency_ghz and s11_db")
        rows = []
        for raw in reader:
            item = {
                "frequency_ghz": float(raw["frequency_ghz"]),
                "s11_db": float(raw["s11_db"]),
            }
            if raw.get("zin_50_real_ohm") not in (None, ""):
                item["zin_real_ohm"] = float(raw["zin_50_real_ohm"])
            if raw.get("zin_50_imag_ohm") not in (None, ""):
                item["zin_imag_ohm"] = float(raw["zin_50_imag_ohm"])
            if not all(math.isfinite(value) for value in item.values()):
                raise IterativeReconstructionError("curve contains non-finite values")
            rows.append(item)
    if len(rows) < 3 or any(b["frequency_ghz"] <= a["frequency_ghz"] for a, b in zip(rows, rows[1:])):
        raise IterativeReconstructionError("curve must contain at least three increasing points")
    return rows


def _interpolate(rows: list[dict[str, float]], frequency: float) -> dict[str, float]:
    if frequency < rows[0]["frequency_ghz"] or frequency > rows[-1]["frequency_ghz"]:
        raise IterativeReconstructionError("target frequency is outside the curve")
    for left, right in zip(rows, rows[1:]):
        if left["frequency_ghz"] <= frequency <= right["frequency_ghz"]:
            if frequency == left["frequency_ghz"]:
                return dict(left)
            ratio = (frequency - left["frequency_ghz"]) / (
                right["frequency_ghz"] - left["frequency_ghz"]
            )
            shared = set(left) & set(right)
            return {
                key: left[key] + ratio * (right[key] - left[key]) for key in shared
            }
    return dict(rows[-1])


def _next_revision(output: Path) -> int:
    revisions = []
    for path in output.glob("iteration_v[0-9][0-9][0-9].json"):
        revisions.append(int(path.stem.rsplit("v", 1)[1]))
    return max(revisions, default=0) + 1


def _verify_series_inputs(output: Path, *, target_sha256: str, space_sha256: str) -> None:
    existing = sorted(output.glob("diagnosis_v[0-9][0-9][0-9].json"))
    if not existing:
        return
    previous = json.loads(existing[-1].read_text(encoding="utf-8"))
    if previous.get("target_sha256") != target_sha256:
        raise IterativeReconstructionError(
            "iteration output belongs to a different target snapshot"
        )
    if previous.get("assumption_space_sha256") != space_sha256:
        raise IterativeReconstructionError(
            "iteration output belongs to a different assumption-space snapshot"
        )
