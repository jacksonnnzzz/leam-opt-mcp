from __future__ import annotations

import copy
import json

import pytest

from antenna_mcp.modeling import (
    CrossStageConsistencyError,
    ModelingService,
    _validate_cross_stage_artifact,
)
from antenna_mcp.models import ModelingRequest
from antenna_mcp.prompts import STAGE_INSTRUCTIONS, SYSTEM_PROMPT
from antenna_mcp.workspace import WorkspaceStore


SOURCE = {
    "input_summary": "dimensioned patch drawing",
    "antenna_type": "rectangular patch",
    "coordinate_system": {
        "plane": "XY",
        "origin": [0, 0, 0],
        "axes": ["x", "y", "z"],
    },
    "components": [
        {
            "name": "Patch",
            "role": "radiator",
            "primitive": "rectangle",
            "material": "copper",
            "geometric_evidence": "dimensioned outline",
            "confidence": 1.0,
        }
    ],
    "parameters": [
        {
            "symbol": "W",
            "value": 10,
            "unit": "mm",
            "geometric_meaning": "patch width",
            "evidence_source": "figure label W",
            "confidence": 1.0,
        }
    ],
    "operations": [],
    "derived_relations": [],
    "uncertainties": [],
}


class ContractProvider:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}

    def generate(self, *, system, prompt, attachments):
        stage = prompt.split("Stage: ", 1)[1].splitlines()[0]
        if stage in self.overrides:
            return json.dumps(self.overrides[stage])
        if stage == "source_analysis":
            return json.dumps(SOURCE)
        if stage == "parameters":
            return json.dumps(
                {
                    "parameters": [
                        {
                            "name": "W",
                            "value": 10.0,
                            "unit": "mm",
                            "description": "allowed downstream description",
                            "optimizable": True,
                        }
                    ]
                }
            )
        if stage == "materials":
            return json.dumps(
                {
                    "materials": [
                        {
                            "name": "copper",
                            "conductivity": 5.8e7,
                        }
                    ]
                }
            )
        if stage == "solids":
            return json.dumps(
                {
                    "solids": [
                        {
                            "name": "Patch",
                            "role": "radiator",
                            "primitive": "rectangle",
                            "material": "copper",
                            "coordinate_system": "global",
                            "dependencies": [],
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected stage {stage}")


def _run(tmp_path, through_stage, overrides=None):
    store = WorkspaceStore(tmp_path)
    service = ModelingService(store, ContractProvider(overrides))
    state = service.create(
        ModelingRequest(description="Reconstruct this evidence-backed rectangular patch.")
    )
    return service.run(state.job_id, through_stage=through_stage)


def test_exact_source_contract_passes_and_allows_descriptive_fields(tmp_path):
    result = _run(tmp_path, "solids")

    assert result.status == "completed"
    assert result.current_stage == "solids"
    assert {"source_analysis", "parameters", "materials", "solids"} <= result.artifacts.keys()


@pytest.mark.parametrize(
    ("parameters", "error_code"),
    [
        ({"parameters": []}, "identity_set_mismatch"),
        (
            {
                "parameters": [
                    {"name": "W", "value": 11, "unit": "mm"},
                ]
            },
            "source_value_changed",
        ),
        (
            {
                "parameters": [
                    {"name": "W", "value": 10, "unit": "cm"},
                ]
            },
            "source_value_changed",
        ),
        (
            {
                "parameters": [
                    {"name": "W", "value": 10, "unit": "mm"},
                    {"name": "invented", "value": 1, "unit": "mm"},
                ]
            },
            "identity_set_mismatch",
        ),
    ],
)
def test_parameter_drift_fails_at_parameters_stage(tmp_path, parameters, error_code):
    result = _run(tmp_path, "parameters", {"parameters": parameters})

    assert result.status == "failed"
    assert result.current_stage == "parameters"
    assert result.error.startswith("CrossStageConsistencyError:")
    assert error_code in result.error
    assert "parameters" not in result.artifacts


@pytest.mark.parametrize(
    "materials",
    [
        {"materials": []},
        {
            "materials": [
                {"name": "copper"},
                {"name": "vacuum"},
            ]
        },
    ],
)
def test_missing_or_unevidenced_material_fails_at_materials_stage(tmp_path, materials):
    result = _run(tmp_path, "materials", {"materials": materials})

    assert result.status == "failed"
    assert result.current_stage == "materials"
    assert "material_set_mismatch" in result.error
    assert "materials" not in result.artifacts


@pytest.mark.parametrize(
    "solids",
    [
        {"solids": []},
        {
            "solids": [
                {
                    "name": "Patch",
                    "role": "radiator",
                    "primitive": "box",
                    "material": "copper",
                }
            ]
        },
        {
            "solids": [
                {
                    "name": "Patch",
                    "role": "radiator",
                    "primitive": "rectangle",
                    "material": "copper",
                },
                {
                    "name": "InventedFeed",
                    "role": "feed",
                    "primitive": "box",
                    "material": "copper",
                },
            ]
        },
    ],
)
def test_solid_set_or_evidence_field_drift_fails_at_solids_stage(tmp_path, solids):
    result = _run(tmp_path, "solids", {"solids": solids})

    assert result.status == "failed"
    assert result.current_stage == "solids"
    assert "CrossStageConsistencyError" in result.error
    assert "solids" not in result.artifacts


def test_solids_gate_rechecks_upstream_material_coverage():
    solids = ContractProvider().generate(
        system="",
        prompt="Stage: solids\n",
        attachments=[],
    )

    with pytest.raises(CrossStageConsistencyError) as caught:
        _validate_cross_stage_artifact(
            "solids",
            json.loads(solids),
            copy.deepcopy(SOURCE),
            materials_contract={"materials": []},
        )

    assert caught.value.stage == "solids"
    assert any(issue["code"] == "material_set_mismatch" for issue in caught.value.issues)


def test_solids_preserve_explicit_layer_fill_and_body_material_contract():
    source = copy.deepcopy(SOURCE)
    source["components"][0].update(
        {
            "parent_layer": "signal",
            "fill_material": "air",
            "body_material": "copper",
        }
    )
    drifted = {
        "solids": [
            {
                "name": "Patch",
                "role": "radiator",
                "primitive": "rectangle",
                "material": "copper",
                "parent_layer": "signal",
                "body_material": "copper",
            }
        ]
    }

    with pytest.raises(CrossStageConsistencyError) as caught:
        _validate_cross_stage_artifact(
            "solids",
            drifted,
            source,
            materials_contract={"materials": [{"name": "copper"}, {"name": "air"}]},
        )

    assert any(
        issue["path"] == "solids.solids['Patch'].fill_material"
        for issue in caught.value.issues
    )


def test_materials_include_explicit_fill_and_body_materials():
    source = copy.deepcopy(SOURCE)
    source["components"][0].update(
        {"fill_material": "air", "body_material": "copper"}
    )

    with pytest.raises(CrossStageConsistencyError) as caught:
        _validate_cross_stage_artifact(
            "materials",
            {"materials": [{"name": "copper"}]},
            source,
        )

    assert any(
        issue["code"] == "material_set_mismatch" and issue["missing"] == ["air"]
        for issue in caught.value.issues
    )


def test_solids_must_preserve_structured_geometric_evidence():
    source = copy.deepcopy(SOURCE)
    source["components"][0]["geometric_evidence"] = {
        "x_range_mm": [0.0, 10.0],
        "z_range_mm": [0.535, 0.57],
    }
    solid = json.loads(
        ContractProvider().generate(
            system="",
            prompt="Stage: solids\n",
            attachments=[],
        )
    )

    with pytest.raises(CrossStageConsistencyError) as caught:
        _validate_cross_stage_artifact(
            "solids",
            solid,
            source,
            materials_contract={"materials": [{"name": "copper"}]},
        )

    assert any(
        issue["path"] == "solids.solids['Patch'].geometric_evidence"
        for issue in caught.value.issues
    )


def test_prompts_define_immutable_cross_stage_contract():
    assert "immutable evidence contract" in SYSTEM_PROMPT
    assert "exactly one record for every" in STAGE_INSTRUCTIONS["parameters"]
    assert "no others" in STAGE_INSTRUCTIONS["materials"]
    assert "one-to-one correspondence" in STAGE_INSTRUCTIONS["solids"]
    assert "copy every explicit parent_layer" in STAGE_INSTRUCTIONS["dimensions"]
    assert "must never be dropped" in STAGE_INSTRUCTIONS["dimensions"]
    assert "face-contiguous" in STAGE_INSTRUCTIONS["dimensions"]
    assert "reference-ground top face" in STAGE_INSTRUCTIONS["dimensions"]
    assert "fill_material" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "body_material" in STAGE_INSTRUCTIONS["solids"]
    assert '"axes":["X","Y","Z"]' in STAGE_INSTRUCTIONS["source_analysis"]
    assert "never return axes as an object" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "generation_evidence.source_contract" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "The key is evidence_source, never" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "never put prose" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "never add\nnull placeholders" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "component_geometric_evidence" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "structured JSON object" in STAGE_INSTRUCTIONS["solids"]
    assert "structured `dimensions` object" in STAGE_INSTRUCTIONS["dimensions"]
    assert "parameter set is exactly reference.parameters" in STAGE_INSTRUCTIONS["source_analysis"]
    assert "implementation constants" in STAGE_INSTRUCTIONS["source_analysis"]
