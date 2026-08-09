import json

import pytest

from antenna_mcp.models import ModelingRequest
from antenna_mcp.source_refinement import SourceRefinementService, _audit_refinement
from antenna_mcp.workspace import WorkspaceStore


def source_payload(*, duplicate=False):
    parameters = [
        {
            "symbol": "W",
            "value": 10,
            "unit": "mm",
            "geometric_meaning": "width",
            "evidence_source": "figure",
            "confidence": 0.9,
        }
    ]
    if duplicate:
        parameters.append(dict(parameters[0], symbol="$W$", evidence_source="caption"))
    return {
        "input_summary": "one antenna",
        "antenna_type": "patch",
        "coordinate_system": {"plane": "XY", "origin": "lower-left", "axes": ["+x", "+y", "+z"]},
        "components": [
            {
                "name": "substrate",
                "role": "support",
                "primitive": "box",
                "material": "FR4_epoxy",
                "geometric_evidence": "outer rectangle",
                "confidence": 0.9,
            }
        ],
        "parameters": parameters,
        "operations": [],
        "uncertainties": ["port is unknown"],
    }


class Provider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.payload)


class SequenceProvider:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.payloads.pop(0))


def visual_audit_payload():
    return {
        "target_design": "patch",
        "source_scope": "single dimensioned figure",
        "components": [
            {
                "entity_id": "entity-substrate",
                "name": "substrate",
                "role": "support",
                "shape": "rectangle/box",
                "primitive_class": "box",
                "material_class": "dielectric",
                "layer": "volume",
                "layer_class": "volume",
                "geometric_relation": "contains the metal geometry",
                "visual_evidence": "outer rectangle",
                "source_locator": "source image",
                "confidence": 0.9,
            }
        ],
        "parameter_bindings": [
            {
                "claim_id": "claim-W",
                "evidence_mode": "visual",
                "symbol": "W",
                "value": 10,
                "unit": "mm",
                "entity_id": "entity-substrate",
                "quantity": "width",
                "axis": "x",
                "geometric_meaning": "substrate width",
                "dimension_evidence": "horizontal arrow across the outer rectangle",
                "source_locator": "source image",
                "confidence": 0.9,
            }
        ],
        "derived_relations": [],
        "required_operations": [],
        "rejected_hypotheses": [],
        "unresolved": [],
    }


def visually_bound_source_payload():
    payload = source_payload()
    payload["components"][0]["evidence_binding"] = {
        "mode": "visual",
        "entity_id": "entity-substrate",
        "primitive_class": "box",
        "material_class": "dielectric",
        "layer_class": "volume",
    }
    payload["parameters"][0]["semantic_binding"] = {
        "mode": "visual",
        "claim_id": "claim-W",
        "entity_id": "entity-substrate",
        "quantity": "width",
        "axis": "x",
    }
    return payload


def visual_verdict_payload(*, passed=True):
    findings = [] if passed else ["W is bound to the wrong feature"]
    return {
        "passed": passed,
        "critical_findings": findings,
        "component_count_correct": True,
        "topology_correct": True,
        "parameter_checks": [
            {
                "symbol": "W",
                "value_correct": True,
                "meaning_correct": passed,
                "visual_evidence": "horizontal arrow across the outer rectangle",
            }
        ],
        "cross_design_contamination": [],
    }


def test_refinement_requires_hash_approval_and_collapses_raw_duplicate(tmp_path):
    store = WorkspaceStore(tmp_path)
    request = ModelingRequest(description="A reviewed rectangular patch antenna description.")
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(
        state.job_id, "source_analysis.json", json.dumps(source_payload(duplicate=True))
    )
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    provider = Provider(source_payload())
    service = SourceRefinementService(store, provider)
    refined = service.refine(state.job_id)

    assert refined["status"] == "awaiting_review"
    assert refined["quality_gate_passed"] is True
    report = json.loads(open(refined["report"], encoding="utf-8").read())
    assert report["raw_duplicate_symbols"] == ["W"]
    assert provider.calls[0]["attachments"] == []

    with pytest.raises(PermissionError, match="hash"):
        service.approve(state.job_id, "0" * 64)

    approved = service.approve(state.job_id, refined["approval_hash"])
    assert approved["status"] == "completed"
    assert store.load_state(state.job_id).artifacts["source_analysis_approved"] == approved["approved"]


def test_refinement_quality_gate_blocks_missing_raw_parameter(tmp_path):
    store = WorkspaceStore(tmp_path)
    request = ModelingRequest(description="A reviewed rectangular patch antenna description.")
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw = source_payload()
    raw["parameters"].append(
        {
            "symbol": "L",
            "value": 20,
            "unit": "mm",
            "geometric_meaning": "length",
            "evidence_source": "figure",
            "confidence": 0.9,
        }
    )
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(raw))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    refined = SourceRefinementService(store, Provider(source_payload())).refine(state.job_id)

    assert refined["quality_gate_passed"] is False
    with pytest.raises(ValueError, match="quality gate"):
        SourceRefinementService(store).approve(state.job_id, refined["approval_hash"])


def test_refinement_uses_visual_audit_and_final_visual_verdict(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace")
    image = tmp_path / "source.png"
    image.write_bytes(b"not read by the fake provider")
    request = ModelingRequest(
        description="A reviewed rectangular patch antenna description.",
        attachments=[str(image)],
    )
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(source_payload()))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    provider = SequenceProvider(
        visual_audit_payload(),
        visually_bound_source_payload(),
        visual_verdict_payload(),
    )
    refined = SourceRefinementService(store, provider).refine(state.job_id)

    assert refined["quality_gate_passed"] is True
    assert len(provider.calls) == 3
    assert len(provider.calls[0]["attachments"]) == 1
    assert provider.calls[0]["attachments"][0].name.startswith("source_visual_input_1")
    assert provider.calls[1]["attachments"] == []
    assert provider.calls[2]["attachments"] == provider.calls[0]["attachments"]
    saved = store.load_state(state.job_id)
    assert "source_visual_audit" in saved.artifacts
    assert "source_visual_verdict" in saved.artifacts
    with open(saved.artifacts["source_visual_audit"], "a", encoding="utf-8") as stream:
        stream.write(" ")
    with pytest.raises(PermissionError, match="hash"):
        SourceRefinementService(store).approve(state.job_id, refined["approval_hash"])


def test_refinement_quality_gate_blocks_failed_visual_verdict(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace")
    image = tmp_path / "source.png"
    image.write_bytes(b"not read by the fake provider")
    request = ModelingRequest(description="A dimensioned patch antenna.", attachments=[str(image)])
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(source_payload()))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    provider = SequenceProvider(
        visual_audit_payload(),
        visually_bound_source_payload(),
        visual_verdict_payload(passed=False),
    )
    refined = SourceRefinementService(store, provider).refine(state.job_id)

    assert refined["quality_gate_passed"] is False
    report = json.loads(open(refined["report"], encoding="utf-8").read())
    assert report["visual_verdict_passed"] is False
    assert report["visual_failed_parameter_checks"] == ["W"]
    with pytest.raises(ValueError, match="quality gate"):
        SourceRefinementService(store).approve(state.job_id, refined["approval_hash"])


def test_refinement_quality_gate_blocks_component_count_change_and_low_confidence(tmp_path):
    store = WorkspaceStore(tmp_path)
    request = ModelingRequest(description="A reviewed rectangular patch antenna description.")
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(source_payload()))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    reviewed = source_payload()
    reviewed["components"].append(
        {
            "name": "unsupported_stub",
            "role": "unverified feed feature",
            "primitive": "cylinder",
            "material": "copper",
            "geometric_evidence": "uncertain",
            "confidence": 0.5,
        }
    )
    refined = SourceRefinementService(store, Provider(reviewed)).refine(state.job_id)

    assert refined["quality_gate_passed"] is False
    report = json.loads(open(refined["report"], encoding="utf-8").read())
    assert report["component_count_changed"] is True
    assert report["low_confidence_components"] == ["unsupported_stub"]


def test_semantic_gate_rejects_same_value_with_wrong_visual_binding(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace")
    image = tmp_path / "source.png"
    image.write_bytes(b"not read by the fake provider")
    request = ModelingRequest(description="A dimensioned patch antenna.", attachments=[str(image)])
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(source_payload()))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    reviewed = visually_bound_source_payload()
    reviewed["parameters"][0]["semantic_binding"]["quantity"] = "radius"
    provider = SequenceProvider(
        visual_audit_payload(),
        reviewed,
        visual_verdict_payload(),
    )
    refined = SourceRefinementService(store, provider).refine(state.job_id)

    assert refined["quality_gate_passed"] is False
    report = json.loads(open(refined["report"], encoding="utf-8").read())
    assert report["changed_numeric_values"] == []
    assert report["binding_conflicts"][0]["symbol"] == "W"


def test_operator_visual_audit_skips_model_audit_but_remains_hash_frozen(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace")
    image = tmp_path / "source.png"
    image.write_bytes(b"not read by the fake provider")
    audit_path = tmp_path / "reviewed-audit.json"
    audit_path.write_text(json.dumps(visual_audit_payload()), encoding="utf-8")
    request = ModelingRequest(description="A dimensioned patch antenna.", attachments=[str(image)])
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(source_payload()))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    provider = Provider(visually_bound_source_payload())
    refined = SourceRefinementService(store, provider).refine(
        state.job_id,
        visual_audit_path=str(audit_path),
    )

    assert refined["quality_gate_passed"] is True
    assert len(provider.calls) == 1
    assert provider.calls[0]["attachments"] == []
    report = json.loads(open(refined["report"], encoding="utf-8").read())
    assert report["visual_audit_provenance"] == "operator_supplied"
    packet = json.loads(open(refined["review_packet"], encoding="utf-8").read())
    assert {item["name"] for item in packet["artifacts"]} >= {
        "candidate",
        "report",
        "visual_audit",
        "visual_input_1",
    }


def test_semantic_gate_accepts_audited_text_evidence_mode():
    raw = source_payload()
    reviewed = visually_bound_source_payload()
    audit = visual_audit_payload()
    audit["parameter_bindings"][0]["evidence_mode"] = "text"
    reviewed["parameters"][0]["semantic_binding"]["mode"] = "text"

    report = _audit_refinement(raw, reviewed, audit, None, "document width is 10 mm")

    assert report["quality_gate_passed"] is True
    assert report["binding_conflicts"] == []


def test_semantic_gate_rejects_material_relation_and_operation_mismatches():
    raw = source_payload()
    reviewed = visually_bound_source_payload()
    audit = visual_audit_payload()
    audit["components"][0]["material_class"] = "void"
    reviewed["components"][0]["evidence_binding"]["material_class"] = "void"
    audit["derived_relations"] = [
        {
            "claim_id": "relation-W",
            "expression": "W = 10",
            "symbols": ["W"],
            "evidence": "explicit equation",
            "source_locator": "figure",
            "confidence": 0.9,
        }
    ]
    audit["required_operations"] = [
        {
            "operation": "unite",
            "target": "substrate",
            "operands": ["substrate"],
            "order": 1,
        }
    ]

    report = _audit_refinement(raw, reviewed, audit)

    assert report["quality_gate_passed"] is False
    assert report["component_material_conflicts"][0]["component"] == "substrate"
    assert report["missing_derived_relation_claims"] == ["relation-W"]
    assert report["missing_required_operations"][0]["operation"] == "unite"


def test_recheck_repairs_operator_audit_contract_without_another_model_call(tmp_path):
    store = WorkspaceStore(tmp_path / "workspace")
    image = tmp_path / "source.png"
    image.write_bytes(b"not read by the fake provider")
    audit_path = tmp_path / "reviewed-audit.json"
    audit_path.write_text(json.dumps(visual_audit_payload()), encoding="utf-8")
    request = ModelingRequest(description="A dimensioned patch antenna.", attachments=[str(image)])
    state = store.create_job("modeling", request.model_dump(mode="json"))
    raw_path = store.write_artifact(state.job_id, "source_analysis.json", json.dumps(source_payload()))
    state.artifacts["source_analysis"] = str(raw_path)
    state.status = "completed"
    store.save_state(state)

    candidate = visually_bound_source_payload()
    candidate["components"][0]["evidence_binding"] = {
        "mode": "visual",
        "entity_id": "entity-substrate",
    }
    refined = SourceRefinementService(store, Provider(candidate)).refine(
        state.job_id,
        visual_audit_path=str(audit_path),
    )
    assert refined["quality_gate_passed"] is False

    rechecked = SourceRefinementService(store).recheck(state.job_id)

    assert rechecked["quality_gate_passed"] is True
    assert rechecked["canonical_repair_count"] == 2
    with pytest.raises(PermissionError, match="hash"):
        SourceRefinementService(store).approve(state.job_id, refined["approval_hash"])
    approved = SourceRefinementService(store).approve(state.job_id, rechecked["approval_hash"])
    assert approved["status"] == "completed"
