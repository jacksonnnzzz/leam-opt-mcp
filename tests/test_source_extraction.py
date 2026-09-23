import json

import pytest

from antenna_mcp.models import ModelingRequest
from antenna_mcp.modeling import ModelingService
from antenna_mcp.workspace import WorkspaceStore
from antenna_mcp.source_extraction import criterion_ids, merge_parts
from antenna_mcp.reproducibility import ReproducibilityAuditService
from antenna_mcp.evidence_consistency import EvidenceConsistencyError


def parts():
    result = {}
    for part in ("geometry", "materials", "solver", "validation"):
        result[part] = {"parameters": [], "uncertainties": [], "reproducibility_evidence": {
            "criteria": [{"id": cid, "status": "missing", "evidence_source": None,
                          "detail": "Not supplied in test source."} for cid in criterion_ids(part)]}}
    result["geometry"].update({"input_summary": "A test antenna source with unresolved inputs.",
        "antenna_type": "patch", "coordinate_system": {"plane": None, "origin": None, "axes": None},
        "components": [{"name": "Radiator", "role": "radiator", "primitive": "box", "material": None,
                        "confidence": 0.5, "geometric_evidence": "Figure 1"}],
        "operations": [], "derived_relations": []})
    result["materials"]["component_materials"] = [{"name": "Radiator", "material": "metal_x", "evidence_source": "Table 2"}]
    return result


class Provider:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def generate(self, **kwargs):
        part = next(p for p in self.payloads if f"Source part: {p}\n" in kwargs["prompt"])
        self.calls.append(part)
        return json.dumps(self.payloads[part])


def test_split_pipeline_merges_four_passes_and_records_history(tmp_path):
    store = WorkspaceStore(tmp_path)
    provider = Provider(parts())
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "completed", state.error
    assert provider.calls == ["geometry", "materials", "solver", "validation"]
    source = json.loads(open(state.artifacts["source_analysis"], encoding="utf-8").read())
    assert source["components"][0]["material"] == "metal_x"
    assert len(source["reproducibility_evidence"]["criteria"]) == 28
    assert "source_split_v001_report" in state.artifacts
    # A second extraction must retain first-pass raw output, not silently reuse it.
    service.run(job.job_id, "source_analysis")
    assert "source_split_v002_report" in store.load_state(job.job_id).artifacts


def test_invalid_part_stops_before_later_calls_and_never_publishes_source(tmp_path):
    payloads = parts()
    payloads["geometry"]["components"] *= 2
    store = WorkspaceStore(tmp_path)
    provider = Provider(payloads)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed"
    assert provider.calls == ["geometry"] * 3
    assert "source_analysis" not in state.artifacts
    assert "reproducibility_assessment" not in state.artifacts
    assert "source_split_v001_geometry" in state.artifacts
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    attempts = report["parts"]["geometry"]["attempts"]
    assert len(attempts) == 3
    assert all(a["status"] == "rejected" for a in attempts)
    assert len({a["raw_artifact"] for a in attempts}) == 3


def test_material_conflict_not_silently_overwritten():
    payloads = parts()
    payloads["geometry"]["components"][0]["material"] = "different_metal"
    with pytest.raises(ValueError, match="material conflict"):
        merge_parts(payloads)


@pytest.mark.parametrize("detail", ["Mesh controls not specified.", "未提供网格控制参数。", "Not reported in the source."])
def test_contradictory_positive_status_cannot_score(detail):
    source = {"reproducibility_evidence": {"criteria": [
        {"id": "mesh_controls", "status": "explicit", "evidence_source": "Table 1", "detail": detail}]}}
    with pytest.raises(EvidenceConsistencyError, match="status_detail_contradiction"):
        ReproducibilityAuditService().audit_payload(source)


def test_positive_fact_with_different_variant_caveat_is_allowed():
    source = {"reproducibility_evidence": {"criteria": [
        {"id": "substrate_loss_tangent", "status": "explicit", "evidence_source": "Table 1",
         "detail": "Nominal tan(delta)=0.003. Actual fabricated losses are not specified."}]}}
    assert ReproducibilityAuditService().audit_payload(source)["score"] == 3


def test_missing_parameter_claim_cannot_coexist_with_value():
    source = {"parameters": [{"symbol": "Width", "value": 17}],
              "uncertainties": [{"parameter_symbol": "Width", "status": "missing"}]}
    with pytest.raises(EvidenceConsistencyError, match="parameter_declared_missing"):
        ReproducibilityAuditService().audit_payload(source)


def test_duplicate_entities_rejected_even_by_direct_audit():
    with pytest.raises(EvidenceConsistencyError, match="duplicate_entity"):
        ReproducibilityAuditService().audit_payload({"components": [{"name": "Plate"}, {"name": " plate "}]})


def test_parameter_conflict_during_merge():
    payloads = parts()
    p = {"symbol": "h", "value": 2, "unit": "mm", "geometric_meaning": "height", "evidence_source": "Fig 1", "confidence": 1}
    payloads["geometry"]["parameters"] = [p]
    payloads["materials"]["parameters"] = [{**p, "value": 3}]
    with pytest.raises(ValueError, match="parameter conflict"):
        merge_parts(payloads)


def test_foreign_criterion_stops_part(tmp_path):
    payloads = parts()
    payloads["geometry"]["reproducibility_evidence"]["criteria"][0]["id"] = "mesh_controls"
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, Provider(payloads))
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed"
    assert "exactly these evidence IDs" in state.error


def test_uncertainty_contradiction_blocks_score():
    with pytest.raises(EvidenceConsistencyError, match="uncertainty_evidence_contradiction"):
        ReproducibilityAuditService().audit_payload({
            "reproducibility_evidence": {"criteria": [{"id": "mesh_controls", "status": "explicit",
                "evidence_source": "Table 1", "detail": "Mesh size is 0.2 mm."}]},
            "uncertainties": [{"id": "missing_mesh_controls", "status": "missing"}]})


def test_cli_persists_split_mode(tmp_path, capsys):
    from antenna_mcp.workflow_cli import main
    main(["--workspace", str(tmp_path), "model-create", "--description", "Extract a generic antenna",
          "--source-extraction-mode", "split"])
    result = json.loads(capsys.readouterr().out)
    assert result["request"]["source_extraction_mode"] == "split"


@pytest.mark.parametrize("failure", ["missing_confidence", "invalid_json", "contradiction"])
def test_correction_rechecks_source_without_program_filling_fields(tmp_path, failure):
    class CorrectingProvider(Provider):
        def __init__(self):
            super().__init__(parts())
            self.prompts = []

        def generate(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            raw = super().generate(**kwargs)
            if len(self.calls) != 1:
                return raw
            if failure == "invalid_json":
                return '{"broken":'
            data = json.loads(raw)
            if failure == "missing_confidence":
                del data["components"][0]["confidence"]
            else:
                data["reproducibility_evidence"]["criteria"][0].update(
                    status="explicit", evidence_source="Table 1", detail="Not specified.")
            return json.dumps(data)

    store = WorkspaceStore(tmp_path)
    provider = CorrectingProvider()
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "completed", state.error
    assert provider.calls == ["geometry", "geometry", "materials", "solver", "validation"]
    assert "Correction request" in provider.prompts[1]
    assert "validation_error" in provider.prompts[1]
    assert "untrusted" in provider.prompts[1]
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    geometry = report["parts"]["geometry"]
    assert geometry["accepted_attempt"] == 2
    assert [a["status"] for a in geometry["attempts"]] == ["rejected", "validated"]
    first_raw = open(geometry["attempts"][0]["raw_artifact"], encoding="utf-8").read()
    if failure == "missing_confidence":
        assert "confidence" not in json.loads(first_raw)["components"][0]
    assert open(geometry["attempts"][1]["prompt_artifact"], encoding="utf-8").read() == provider.prompts[1]


def test_provider_failure_is_recorded_and_not_retried(tmp_path):
    class OfflineProvider(Provider):
        def generate(self, **kwargs):
            self.calls.append("geometry")
            raise RuntimeError("provider unavailable")

    provider = OfflineProvider(parts())
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed"
    assert provider.calls == ["geometry"]
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    assert report["parts"]["geometry"]["attempts"][0]["status"] == "provider_failed"
    assert "source_analysis" not in state.artifacts


def test_merge_conflicts_do_not_trigger_unbounded_corrections(tmp_path):
    payloads = parts()
    payloads["geometry"]["components"][0]["material"] = "different_metal"
    provider = Provider(payloads)
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed"
    assert provider.calls == ["geometry", "materials", "solver", "validation"]
    assert "source_analysis" not in state.artifacts
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    assert report["failed_part"] == "merge"


def test_explicit_geometry_attachments_and_native_schema_are_isolated(tmp_path):
    class StructuredProvider(Provider):
        def __init__(self):
            super().__init__(parts())
            self.inputs = []
            self.schemas = []

        def generate(self, **kwargs):
            self.inputs.append(kwargs["attachments"])
            return super().generate(**kwargs)

        def generate_structured(self, *, schema, **kwargs):
            self.schemas.append(schema)
            return self.generate(**kwargs)

    full = tmp_path / "full.txt"
    full.write_text("full source", "utf-8")
    geometry = tmp_path / "geometry.txt"
    geometry.write_text("selected pages", "utf-8")
    provider = StructuredProvider()
    store = WorkspaceStore(tmp_path / "jobs")
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split",
        attachments=[str(full)], geometry_attachments=[str(geometry)]))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "completed", state.error
    assert provider.inputs == [[geometry], [full], [full], [full]]
    assert len(provider.schemas) == 1
    schema = provider.schemas[0]
    assert "confidence" in schema["$defs"]["Component"]["required"]
    assert schema["$defs"]["Criterion"]["properties"]["id"]["enum"] == criterion_ids("geometry")
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    assert report["parts"]["geometry"]["input_selection"] == "explicit_geometry_attachments"
    assert report["parts"]["geometry"]["attachments"][0]["path"] == str(geometry)


@pytest.mark.parametrize("invalid", ["string_confidence", "bool_confidence", "coordinates", "extra_parameter"])
def test_geometry_schema_rejects_shape_errors_without_coercion(invalid):
    from antenna_mcp.source_geometry import GeometryOutput
    payload = parts()["geometry"]
    if invalid == "string_confidence":
        payload["components"][0]["confidence"] = "0.5"
    elif invalid == "bool_confidence":
        payload["components"][0]["confidence"] = True
    elif invalid == "coordinates":
        payload["coordinate_system"]["origin"] = [0, 0]
    else:
        payload["parameters"] = [{"symbol": "w", "value": 1, "unit": "mm", "confidence": 0.5,
                                   "geometric_meaning": "width", "evidence_source": "Fig 1", "invented": 1}]
    with pytest.raises(ValueError):
        GeometryOutput.model_validate(payload, strict=True)


def test_geometry_schema_preserves_structured_evidence_and_relationships():
    from antenna_mcp.source_geometry import GeometryOutput
    payload = parts()["geometry"]
    payload["components"][0].update(parent_layer="signal", required_relationships=["parent_layer"],
                                    geometric_evidence={"z_extent": [0, 1], "formula": "h"})
    result = GeometryOutput.model_validate(payload, strict=True)
    assert result.components[0].model_dump()["parent_layer"] == "signal"
    assert result.components[0].geometric_evidence == {"z_extent": [0, 1], "formula": "h"}


def test_geometry_attachments_require_split_and_existing_file(tmp_path):
    with pytest.raises(ValueError, match="requires"):
        ModelingRequest(description="Extract a generic antenna", geometry_attachments=["any.txt"])
    service = ModelingService(WorkspaceStore(tmp_path))
    with pytest.raises(FileNotFoundError):
        service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split",
                                       geometry_attachments=[str(tmp_path / "absent.txt")]))


@pytest.mark.parametrize("split", [True, False])
def test_truncated_response_never_publishes_or_retries_and_preserves_metrics(tmp_path, split):
    from antenna_mcp.llm_result import LlmOutputTruncatedError

    class TruncatedProvider:
        calls = 0
        def generate(self, **kwargs):
            self.calls += 1
            raise LlmOutputTruncatedError("output limit reached", {"status": "truncated", "eval_count": 128})

    provider = TruncatedProvider()
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split" if split else "single"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed" and provider.calls == 1
    assert "source_analysis" not in state.artifacts and "reproducibility_assessment" not in state.artifacts
    if split:
        report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
        attempt = report["parts"]["geometry"]["attempts"][0]
        assert attempt["status"] == "truncated"
        assert attempt["request_metadata"]["eval_count"] == 128
    else:
        report = json.loads(open(state.artifacts["llm_source_analysis_v001"], encoding="utf-8").read())
        assert report["eval_count"] == 128


def test_success_metadata_is_preserved_without_changing_source(tmp_path):
    from antenna_mcp.llm_result import LlmText

    class ReportedProvider(Provider):
        def generate(self, **kwargs):
            return LlmText(super().generate(**kwargs), {"status": "returned", "eval_count": 42})

    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, ReportedProvider(parts()))
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "completed", state.error
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    assert report["parts"]["geometry"]["attempts"][0]["request_metadata"]["eval_count"] == 42


def subparts():
    from antenna_mcp.source_geometry import GEOMETRY_SUBPARTS
    payloads = parts()
    geometry = payloads.pop("geometry")
    split = {name: {key: geometry[key] for key in model.model_fields} for name, model in GEOMETRY_SUBPARTS.items()}
    return {**split, **payloads}


def test_three_geometry_passes_merge_and_preserve_raw_history(tmp_path):
    from copy import deepcopy
    payloads = subparts()
    before = deepcopy(payloads)
    provider = Provider(payloads)
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split",
                                       geometry_extraction_mode="staged"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "completed", state.error
    assert provider.calls == list(payloads)
    assert payloads == before
    merged = json.loads(open(state.artifacts["source_split_v001_geometry_merged"], encoding="utf-8").read())
    assert merged == parts()["geometry"]
    source = json.loads(open(state.artifacts["source_analysis"], encoding="utf-8").read())
    assert len(source["reproducibility_evidence"]["criteria"]) == 28
    assert source["components"][0]["material"] == "metal_x"
    prior = open(state.artifacts["source_split_v001_geometry_entities"], encoding="utf-8").read()
    second = service.run(job.job_id, "source_analysis")
    assert "source_split_v002_geometry_entities" in second.artifacts
    assert open(second.artifacts["source_split_v001_geometry_entities"], encoding="utf-8").read() == prior


@pytest.mark.parametrize("failure", ["duplicate_entity", "foreign_field", "missing_field", "foreign_criterion"])
def test_invalid_subpass_stops_without_merge_or_score(tmp_path, failure):
    payloads = subparts()
    failed_part = "geometry_entities"
    if failure == "duplicate_entity":
        payloads[failed_part]["components"] *= 2
    elif failure == "missing_field":
        del payloads[failed_part]["components"][0]["confidence"]
    elif failure == "foreign_field":
        failed_part = "geometry_parameters"
        payloads[failed_part]["components"] = []
    else:
        failed_part = "geometry_evidence"
        payloads[failed_part]["reproducibility_evidence"]["criteria"][0]["id"] = "mesh_controls"
    provider = Provider(payloads)
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split",
                                       geometry_extraction_mode="staged"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed"
    assert provider.calls.count(failed_part) == 3
    assert "materials" not in provider.calls
    assert "source_analysis" not in state.artifacts and "reproducibility_assessment" not in state.artifacts
    assert "source_split_v001_geometry_merged" not in state.artifacts


def test_cross_subpass_missing_parameter_conflict_blocks_merge(tmp_path):
    payloads = subparts()
    payloads["geometry_entities"]["uncertainties"] = [{"parameter_symbol": "W", "status": "missing"}]
    payloads["geometry_parameters"]["parameters"] = [{"symbol": "W", "value": 7, "unit": "mm",
        "geometric_meaning": "width", "evidence_source": "Fig 1", "confidence": 0.8}]
    provider = Provider(payloads)
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split",
                                       geometry_extraction_mode="staged"))
    state = service.run(job.job_id, "source_analysis")
    assert state.status == "failed" and "parameter_declared_missing" in state.error
    assert provider.calls == ["geometry_entities", "geometry_parameters", "geometry_evidence"]
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    assert report["failed_part"] == "geometry_merge"
    assert "source_analysis" not in state.artifacts


def test_subpass_truncation_stops_without_correction(tmp_path):
    from antenna_mcp.llm_result import LlmOutputTruncatedError
    class Truncated(Provider):
        def generate(self, **kwargs):
            raw = super().generate(**kwargs)
            if self.calls[-1] == "geometry_parameters":
                raise LlmOutputTruncatedError("truncated", {"status": "truncated", "eval_count": 16})
            return raw
    provider = Truncated(subparts())
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, provider)
    job = service.create(ModelingRequest(description="Extract a generic antenna", source_extraction_mode="split",
                                       geometry_extraction_mode="staged"))
    state = service.run(job.job_id, "source_analysis")
    assert provider.calls == ["geometry_entities", "geometry_parameters"]
    assert state.status == "failed"
    assert "source_analysis" not in state.artifacts
    report = json.loads(open(state.artifacts["source_split_v001_report"], encoding="utf-8").read())
    assert report["parts"]["geometry_parameters"]["attempts"][0]["status"] == "truncated"


def test_staged_geometry_requires_split_and_cli_persists_mode(tmp_path, capsys):
    from antenna_mcp.workflow_cli import main
    with pytest.raises(ValueError, match="requires"):
        ModelingRequest(description="Extract a generic antenna", geometry_extraction_mode="staged")
    main(["--workspace", str(tmp_path), "model-create", "--description", "Extract a generic antenna",
          "--source-extraction-mode", "split", "--geometry-extraction-mode", "staged"])
    result = json.loads(capsys.readouterr().out)
    assert result["request"]["geometry_extraction_mode"] == "staged"
