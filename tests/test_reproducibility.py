from __future__ import annotations

import json
from pathlib import Path

import pytest

from antenna_mcp.modeling import ModelingService
from antenna_mcp.models import ModelingRequest
from antenna_mcp.reproducibility import (
    CRITERIA,
    ReproducibilityAuditService,
    criterion_weight_total,
    validate_reproducibility_evidence,
)
from antenna_mcp.workflow_cli import main as workflow_main
from antenna_mcp.workspace import WorkspaceStore


def _criteria(status: str = "explicit") -> list[dict[str, object]]:
    return [
        {
            "id": criterion.id,
            "status": status,
            "evidence_source": "Table 1" if status != "missing" else None,
            "detail": "Frozen test evidence.",
        }
        for criterion in CRITERIA
    ]


def _source(criteria: list[dict[str, object]] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "input_summary": "A fully specified rectangular patch source.",
        "antenna_type": "rectangular_patch",
        "coordinate_system": {
            "plane": "XY",
            "origin": [0, 0, 0],
            "axes": ["X", "Y", "Z"],
        },
        "components": [
            {
                "name": "Patch",
                "role": "radiator",
                "primitive": "rectangle",
                "material": "copper",
                "geometric_evidence": {
                    "origin_mm": [0, 0, 1.6],
                    "size_mm": [30, 40],
                },
                "confidence": 1.0,
            }
        ],
        "parameters": [
            {
                "symbol": "W",
                "value": 40,
                "unit": "mm",
                "geometric_meaning": "patch width",
                "evidence_source": "Table 1",
                "confidence": 1.0,
            }
        ],
        "operations": [{"order": 1, "operation": "create", "target": "Patch"}],
        "derived_relations": [],
        "uncertainties": [],
    }
    if criteria is not None:
        payload["reproducibility_evidence"] = {
            "score": 1000,
            "criteria": criteria,
        }
    return payload


def test_fixed_criteria_total_one_hundred_and_ignore_llm_score():
    assert criterion_weight_total() == 100.0
    report = ReproducibilityAuditService().audit_payload(_source(_criteria()))

    assert report["score"] == 100.0
    assert report["grade"] == "A"
    assert report["decision"] == "direct_reconstruction"
    assert report["workflow_policy"]["electromagnetic_reproduction_claim_allowed"] is False
    assert report["extraction_mode"] == "structured_source_evidence"


def test_assumed_evidence_is_candidate_only_and_requires_approval():
    report = ReproducibilityAuditService().audit_payload(_source(_criteria("assumed")))

    assert report["score"] == 25.0
    assert report["grade"] == "C"
    assert report["decision"] == "candidate_only"
    assert len(report["engineering_assumptions"]) == len(CRITERIA)
    assert report["workflow_policy"]["candidate_codegen_allowed"] is True
    assert report["workflow_policy"]["assumption_approval_required"] is True


def test_missing_structured_criteria_are_scored_missing():
    evidence = {"criteria": _criteria()[:-1]}
    records = validate_reproducibility_evidence(evidence)
    assert records[CRITERIA[-1].id]["status"] == "missing"


def test_unknown_or_duplicate_criterion_is_rejected():
    with pytest.raises(ValueError, match="unknown"):
        validate_reproducibility_evidence(
            {
                "criteria": [
                    {
                        "id": "invented_metric",
                        "status": "explicit",
                        "evidence_source": "paper",
                        "detail": "not allowed",
                    }
                ]
            }
        )
    duplicated = _criteria() + [_criteria()[0]]
    with pytest.raises(ValueError, match="duplicated"):
        validate_reproducibility_evidence({"criteria": duplicated})


def test_job_audit_is_saved_without_modifying_source(tmp_path):
    store = WorkspaceStore(tmp_path)
    state = store.create_job(
        "modeling",
        ModelingRequest(description="Create a reviewable rectangular patch.").model_dump(
            mode="json"
        ),
    )
    source_text = json.dumps(_source(_criteria()), ensure_ascii=False, indent=2)
    source_path = store.write_artifact(state.job_id, "source_analysis.json", source_text)
    state.artifacts["source_analysis"] = str(source_path)
    store.save_state(state)

    result = ReproducibilityAuditService(store).audit_job(state.job_id)

    assert result["grade"] == "A"
    assert Path(result["assessment"]).is_file()
    assert Path(result["report"]).is_file()
    assert source_path.read_text("utf-8") == source_text
    updated = store.load_state(state.job_id)
    assert updated.artifacts["reproducibility_assessment"] == result["assessment"]
    assert updated.artifacts["reproducibility_report"] == result["report"]


class _SourceProvider:
    def generate(self, *, system, prompt, attachments):
        assert "Do not calculate a score or grade" in prompt
        return json.dumps(_source(_criteria()), ensure_ascii=False)


def test_modeling_source_stage_automatically_writes_assessment(tmp_path):
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, _SourceProvider())
    state = service.create(
        ModelingRequest(description="Create a reviewable rectangular patch from Table 1.")
    )

    result = service.run(state.job_id, through_stage="source_analysis")

    assert result.status == "completed"
    report_path = Path(result.artifacts["reproducibility_assessment"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text("utf-8"))
    assert report["score"] == 100.0
    assert report["source_artifact"] == result.artifacts["source_analysis"]
    assert Path(result.artifacts["reproducibility_report"]).is_file()


def test_cli_audits_a_source_file(tmp_path, capsys):
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_source(_criteria())), "utf-8")

    exit_code = workflow_main(
        ["reproducibility-audit", "--source-analysis", str(source_path)]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["grade"] == "A"
    assert output["score"] == 100.0
