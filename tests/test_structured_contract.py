from __future__ import annotations

import copy

import pytest

from antenna_mcp.structured_contract import (
    StructuredContractError,
    validate_dimensions_against_solids,
    validate_simulation_spec,
    validate_source_component_topology,
)


SOLIDS = {
    "solids": [
        {
            "name": "RadiatingElement",
            "role": "rectangular_patch",
            "primitive": "rectangular_patch",
            "material": "copper",
            "parent_layer": "TopMetal",
        },
        {
            "name": "AirEnvelope",
            "role": "open_region",
            "primitive": "open_region",
            "material": "air",
            "boundary": "radiation",
        },
    ]
}


SIMULATION_SPEC = {
    "design_type": "HFSS",
    "solution_type": "Terminal",
    "setup": {
        "name": "Setup1",
        "type": "HFSSDriven",
        "adaptive_frequency": {"value": 10.0, "unit": "GHz"},
    },
    "sweep": {
        "name": "Sweep1",
        "type": "Interpolating",
        "start": {"value": 8.0, "unit": "GHz"},
        "stop": {"value": 12.0, "unit": "GHz"},
    },
    "s_parameter": "S11_dB",
}


STACKUP_SOLIDS = {
    "operations": [
        {"order": 1, "operation": "add_ground_layer", "target": "Ground"},
        {"order": 2, "operation": "add_dielectric_layer", "target": "Substrate"},
        {"order": 3, "operation": "add_signal_layer", "target": "Top"},
    ],
    "solids": [
        {
            "name": "Ground",
            "role": "stackup_ground_layer",
            "primitive": "stackup_ground_layer",
            "material": "copper",
            "geometry": {
                "shape": "box",
                "position": [0.0, 0.0, 7.0],
                "size": [20.0, 20.0, 0.035],
                "unit": "mm",
            },
        },
        {
            "name": "Substrate",
            "role": "stackup_dielectric_layer",
            "primitive": "stackup_dielectric_layer",
            "material": "Duroid",
            "geometry": {
                "shape": "box",
                "position": [0.0, 0.0, 7.035],
                "size": [20.0, 20.0, 0.5],
                "unit": "mm",
            },
        },
        {
            "name": "Top",
            "role": "stackup_signal_layer",
            "primitive": "stackup_signal_layer",
            "material": "copper",
            "fill_material": "air",
            "body_material": "air",
            "geometry": {
                "shape": "box",
                "position": [0.0, 0.0, 7.535],
                "size": [20.0, 20.0, 0.035],
                "unit": "mm",
            },
        },
        {
            "name": "Element",
            "role": "rectangular_patch",
            "primitive": "rectangular_patch",
            "material": "copper",
            "parent_layer": "Top",
            "geometry": {
                "shape": "box",
                "position": [2.0, 3.0, 7.535],
                "size": [9.0, 8.0, 0.035],
                "unit": "mm",
            },
        },
        {
            "name": "FeedProbe",
            "role": "probe_inner",
            "primitive": "cylinder",
            "material": "copper",
            "dependencies": {"objects": ["Element"]},
            "geometry": {
                "shape": "cylinder",
                "position": [5.0, 5.0, 7.035],
                "radius": 0.01,
                "height": 0.5,
                "unit": "mm",
            },
        },
    ],
}


def test_dimensions_preserve_solid_identity_and_explicit_relationships():
    dimensions = copy.deepcopy(SOLIDS)
    dimensions["solids"][0]["vertices"] = [[0, 0, 0], [1, 0, 0]]
    dimensions["solids"][1]["padding"] = {"value": 3, "unit": "mm"}

    validate_dimensions_against_solids(dimensions, SOLIDS)


def test_dimensions_reject_missing_extra_and_changed_solid_records():
    dimensions = {
        "solids": [
            {
                "name": "RadiatingElement",
                "role": "radiator",
                "primitive": "rectangle",
                "material": "pec",
                "parent_layer": "BottomMetal",
            },
            {
                "name": "InventedObject",
                "role": "tool",
                "primitive": "box",
                "material": "vacuum",
            },
        ]
    }

    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(dimensions, SOLIDS)

    assert [(issue["code"], issue["path"]) for issue in caught.value.issues] == [
        ("missing_record", "dimensions.solids"),
        ("unexpected_record", "dimensions.solids"),
        ("value_mismatch", "dimensions.solids['RadiatingElement'].role"),
        ("value_mismatch", "dimensions.solids['RadiatingElement'].primitive"),
        ("value_mismatch", "dimensions.solids['RadiatingElement'].material"),
        ("value_mismatch", "dimensions.solids['RadiatingElement'].parent_layer"),
    ]


def test_patch_and_open_region_relations_must_be_explicit_not_guessed():
    solids = {
        "solids": [
            {
                "name": "ElementA",
                "role": "rectangular_patch",
                "primitive": "rectangular_patch",
                "material": "copper",
            },
            {
                "name": "EnvelopeB",
                "role": "open_region",
                "primitive": "open_region",
                "material": "air",
            },
        ]
    }

    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(copy.deepcopy(solids), solids)

    assert caught.value.issues == [
        {
            "code": "missing_relationship",
            "path": "solids.solids['ElementA'].parent_layer",
            "expected": "explicit non-empty relationship",
            "actual": None,
        },
        {
            "code": "missing_relationship",
            "path": "solids.solids['EnvelopeB'].boundary",
            "expected": "explicit non-empty relationship",
            "actual": None,
        },
    ]


def test_object_name_alone_does_not_infer_a_relationship():
    solids = {
        "solids": [
            {
                "name": "Patch",
                "role": "radiator",
                "primitive": "rectangle",
                "material": "copper",
            },
            {
                "name": "Region",
                "role": "air_volume",
                "primitive": "box",
                "material": "air",
            },
        ]
    }

    validate_dimensions_against_solids(copy.deepcopy(solids), solids)


def test_nullable_material_and_nested_dimensions_are_preserved():
    solids = {
        "solids": [
            {
                "name": "Envelope",
                "role": "open_region",
                "primitive": "open_region",
                "material": None,
                "boundary": "radiation",
                "geometric_evidence": {
                    "x_range_mm": [-1.0, 2.0],
                    "y_range_mm": [-2.0, 3.0],
                    "z_range_mm": [-3.0, 4.0],
                },
            }
        ]
    }
    dimensions = {
        "solids": [
            {
                "name": "Envelope",
                "role": "open_region",
                "primitive": "open_region",
                "material": None,
                "boundary": "radiation",
                "dimensions": {
                    "x_range_mm": [-1.0, 2.0],
                    "y_range_mm": [-2.0, 3.0],
                    "z_range_mm": [-3.0, 4.0],
                },
            }
        ]
    }

    validate_dimensions_against_solids(dimensions, solids)

    dimensions["solids"][0]["dimensions"]["x_range_mm"] = [-1.0, 2.5]
    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(dimensions, solids)
    assert {
        "code": "explicit_geometry_mismatch",
        "path": "dimensions.solids['Envelope'].geometry.x_extent",
        "expected": [-1.0, 2.0],
        "actual": [-1.0, 2.5],
    } in caught.value.issues


def test_stackup_topology_accepts_arbitrary_origin_and_official_relative_spans():
    validate_dimensions_against_solids(copy.deepcopy(STACKUP_SOLIDS), STACKUP_SOLIDS)


def test_stackup_topology_rejects_patch_on_signal_top_and_probe_through_conductor():
    solids = copy.deepcopy(STACKUP_SOLIDS)
    patch = solids["solids"][3]
    patch["geometry"] = {
        "shape": "sheet",
        "position": [2.0, 3.0, 7.57],
        "size": [9.0, 8.0],
        "unit": "mm",
    }
    solids["solids"][4]["geometry"]["height"] = 0.535

    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(copy.deepcopy(solids), solids)

    solid_issues = [
        issue for issue in caught.value.issues if str(issue["path"]).startswith("solids.solids")
    ]
    assert [issue["code"] for issue in solid_issues] == [
        "patch_parent_elevation_mismatch",
        "patch_parent_thickness_mismatch",
        "probe_stackup_span_mismatch",
    ]
    assert solid_issues[0]["expected"] == pytest.approx(7.535)
    assert solid_issues[2]["expected"] == pytest.approx([7.035, 7.535])


def test_stackup_topology_rejects_layer_gap_and_explicit_order_disagreement():
    solids = copy.deepcopy(STACKUP_SOLIDS)
    solids["solids"][1]["geometry"]["position"][2] = 7.045
    solids["solids"][1]["geometry"]["size"][2] = 0.49
    solids["operations"][1]["order"] = 3
    solids["operations"][2]["order"] = 2

    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(copy.deepcopy(solids), solids)

    codes = {issue["code"] for issue in caught.value.issues}
    assert "stackup_gap" in codes
    assert "stackup_order_mismatch" in codes


def test_stackup_signal_fill_is_checked_only_when_explicit():
    invalid = copy.deepcopy(STACKUP_SOLIDS)
    invalid["solids"][2]["fill_material"] = "copper"
    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(copy.deepcopy(invalid), invalid)
    assert "invalid_signal_fill_material" in {
        issue["code"] for issue in caught.value.issues
    }

    expected = copy.deepcopy(STACKUP_SOLIDS)
    dimensions = copy.deepcopy(expected)
    dimensions["solids"][2].pop("fill_material")
    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(dimensions, expected)
    assert {
        "code": "missing_field",
        "path": "dimensions.solids['Top'].fill_material",
        "expected": "air",
        "actual": None,
    } in caught.value.issues
    invalid_body = copy.deepcopy(STACKUP_SOLIDS)
    invalid_body["solids"][2]["body_material"] = "copper"
    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(copy.deepcopy(invalid_body), invalid_body)
    assert {issue["code"] for issue in caught.value.issues} >= {
        "signal_body_fill_mismatch",
        "invalid_signal_body_material",
    }

    dimensions = copy.deepcopy(STACKUP_SOLIDS)
    dimensions["solids"][2].pop("body_material")
    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(dimensions, STACKUP_SOLIDS)
    assert {
        "code": "missing_field",
        "path": "dimensions.solids['Top'].body_material",
        "expected": "air",
        "actual": None,
    } in caught.value.issues


def test_source_topology_rejects_copper_body_for_air_filled_signal_layer():
    source = {
        "components": copy.deepcopy(STACKUP_SOLIDS["solids"]),
        "operations": copy.deepcopy(STACKUP_SOLIDS["operations"]),
    }
    source["components"][2]["body_material"] = "copper"

    with pytest.raises(StructuredContractError) as caught:
        validate_source_component_topology(source)

    assert caught.value.contract == "source_component_topology"
    assert {issue["code"] for issue in caught.value.issues} >= {
        "signal_body_fill_mismatch",
        "invalid_signal_body_material",
    }


def test_stackup_dimensions_cannot_silently_replace_explicit_upstream_z_geometry():
    dimensions = copy.deepcopy(STACKUP_SOLIDS)
    dimensions["solids"][3]["geometry"]["position"][2] = 7.57

    with pytest.raises(StructuredContractError) as caught:
        validate_dimensions_against_solids(dimensions, STACKUP_SOLIDS)

    assert any(
        issue["code"] == "explicit_geometry_mismatch"
        and issue["path"] == "dimensions.solids['Element'].geometry.z_extent"
        for issue in caught.value.issues
    )


def test_non_stackup_geometry_is_not_subject_to_stackup_physics():
    solids = {
        "solids": [
            {
                "name": "OrdinaryBox",
                "role": "housing",
                "primitive": "box",
                "material": "plastic",
                "geometry": {
                    "shape": "box",
                    "position": [0.0, 0.0, 0.0],
                    "size": [1.0, 1.0, 1.0],
                },
            }
        ]
    }
    dimensions = copy.deepcopy(solids)
    dimensions["solids"][0]["geometry"]["position"][2] = 100.0

    validate_dimensions_against_solids(dimensions, solids)


def test_valid_simulation_spec_accepts_supported_optional_sections():
    payload = copy.deepcopy(SIMULATION_SPEC)
    payload.update(
        {
            "excitation": {"name": "P1", "type": "WavePort"},
            "open_region": {"object": "AirEnvelope", "type": "RadiationBoundary"},
            "far_field": {"enabled": True},
            "uncertainties": ["Integration line was not evidenced."],
        }
    )

    validate_simulation_spec(payload)


def test_real_v002_shape_reports_exact_portable_schema_failures():
    """The old payload used aliases that G4 could not consume losslessly."""

    payload = {
        "solution_type": "Terminal",
        "excitation": {"name": "Probe_Port", "type": "WavePort"},
        "open_region": {"type": "RadiationBoundary", "object": "Region"},
        "far_field": {"enabled": True},
        "setup": {
            "name": "Setup1",
            "solution_type": "Terminal",
            "adaptive_frequency": {
                "expression": "patch_frequency",
                "value": 10.0,
                "unit": "GHz",
            },
        },
        "sweep": {
            "name": "Sweep",
            "type": "Interpolating",
            "start_frequency": 8.0,
            "stop_frequency": 12.0,
            "unit": "GHz",
        },
        "uncertainties": [],
    }

    with pytest.raises(StructuredContractError) as caught:
        validate_simulation_spec(payload)

    assert caught.value.contract == "simulation_spec"
    assert caught.value.issues == [
        {
            "code": "missing_field",
            "path": "simulation_spec.design_type",
            "expected": "HFSS",
            "actual": None,
        },
        {
            "code": "missing_field",
            "path": "simulation_spec.setup.type",
            "expected": "non-empty string",
            "actual": None,
        },
        {
            "code": "missing_field",
            "path": "simulation_spec.sweep.start",
            "expected": "object",
            "actual": None,
        },
        {
            "code": "missing_field",
            "path": "simulation_spec.sweep.stop",
            "expected": "object",
            "actual": None,
        },
        {
            "code": "missing_field",
            "path": "simulation_spec.s_parameter",
            "expected": "non-empty string",
            "actual": None,
        },
    ]


def test_simulation_spec_rejects_malformed_frequency_and_optional_sections():
    payload = copy.deepcopy(SIMULATION_SPEC)
    payload["setup"]["adaptive_frequency"] = {"value": "10 GHz", "unit": ""}
    payload["excitation"] = []
    payload["uncertainties"] = "none"

    with pytest.raises(StructuredContractError) as caught:
        validate_simulation_spec(payload)

    assert [(issue["code"], issue["path"]) for issue in caught.value.issues] == [
        ("invalid_value", "simulation_spec.setup.adaptive_frequency.value"),
        ("invalid_value", "simulation_spec.setup.adaptive_frequency.unit"),
        ("invalid_type", "simulation_spec.excitation"),
        ("invalid_type", "simulation_spec.uncertainties"),
    ]
