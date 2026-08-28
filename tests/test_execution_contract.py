from __future__ import annotations

import pytest

from antenna_mcp.execution_contract import (
    ExecutionContractError,
    validate_execution_fragment,
)


SOURCE = {
    "components": [
        {
            "name": "OuterCap",
            "role": "wave_port_cap",
            "geometric_evidence": {"source_face": "Outer.bottom_face_z"},
        }
    ],
    "operations": [
        {"operation": "assign_wave_port", "target": "Probe_Port", "reference": "Outer"}
    ],
}


def test_helper_generated_port_cap_must_not_be_created_as_primitive():
    with pytest.raises(ExecutionContractError, match="helper_object_created_directly"):
        validate_execution_fragment(
            "hfss.modeler.create_circle('XY', [0, 0, 0], 1, name='OuterCap')",
            "model_3d",
            source_analysis=SOURCE,
        )


def test_reviewed_helper_port_call_and_disabled_far_field_pass():
    source = """
hfss.wave_port(
    assignment=Outer.bottom_face_z,
    reference=Outer.name,
    create_pec_cap=True,
    name="Probe_Port",
)
"""
    assert (
        validate_execution_fragment(
            source,
            "simulation_setup",
            source_analysis=SOURCE,
            simulation_spec={"far_field": {"enabled": False}},
        )
        is None
    )


def test_wrong_cap_assignment_and_disabled_far_field_are_rejected_together():
    source = """
hfss.wave_port(
    assignment="OuterCap", reference_conductor="Outer", port_name="Probe_Port"
)
hfss.insert_infinite_sphere()
"""
    with pytest.raises(ExecutionContractError) as caught:
        validate_execution_fragment(
            source,
            "simulation_setup",
            source_analysis=SOURCE,
            simulation_spec={"far_field": {"enabled": False}},
        )

    codes = {item["code"] for item in caught.value.issues}
    assert {
        "disabled_far_field_created",
        "wrong_port_assignment_face",
        "wrong_port_reference",
        "missing_pec_cap_creation",
        "cap_used_as_port_assignment",
    } <= codes


def test_jobs_without_reviewed_execution_facts_are_unchanged():
    assert (
        validate_execution_fragment(
            "hfss.create_setup('Setup1')",
            "simulation_setup",
            source_analysis={"components": []},
            simulation_spec={},
        )
        is None
    )
