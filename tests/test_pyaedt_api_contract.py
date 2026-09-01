from __future__ import annotations

from pathlib import Path

import pytest

from antenna_mcp.pyaedt_api_contract import (
    PyAedtApiContractError,
    validate_pyaedt_api_fragment,
)


ROOT = Path(__file__).resolve().parents[1]


def test_supported_pyaedt_0263_calls_pass_without_importing_or_starting_aedt():
    source = """
rectangle = hfss.modeler.create_rectangle(
    orientation="XY", origin=[0, 0, 0], sizes=[1, 2], name="R"
)
circle = hfss.modeler.create_circle("XY", [0, 0, 0], 1, name="C")
cylinder = hfss.modeler.create_cylinder(
    orientation="Z", origin=[0, 0, 0], radius=1, height=2,
    num_sides=0, name="CY", material="copper"
)
face_id = hfss.modeler.get_faceid_from_position(
    position=[1, 0, 0], assignment="CY"
)
port = hfss.wave_port(assignment=face_id, name="P1")
sweep = hfss.create_linear_count_sweep(
    setup="Setup1", unit="GHz", start_frequency=1,
    stop_frequency=2, num_of_freq_points=11
)
"""

    assert validate_pyaedt_api_fragment(source, "model_3d") is None


@pytest.mark.parametrize("method", ["create_rectangle", "create_circle"])
def test_sheet_primitives_require_orientation(method):
    source = f"hfss.modeler.{method}(origin=[0, 0, 0], radius=1)"

    with pytest.raises(PyAedtApiContractError, match="requires orientation"):
        validate_pyaedt_api_fragment(source, "model_3d")


def test_create_cylinder_rejects_axis_and_non_signature_keywords():
    source = """
hfss.modeler.create_cylinder(
    axis="Z", origin=[0, 0, 0], radius=1, height=2, numSides=0
)
"""

    with pytest.raises(PyAedtApiContractError) as caught:
        validate_pyaedt_api_fragment(source, "model_3d")

    message = str(caught.value)
    assert "unsupported axis=" in message
    assert "orientation=" in message
    assert "unsupported keyword numSides=" in message


def test_obsolete_face_lookup_names_exact_replacement_and_argument_order():
    source = 'face = hfss.modeler.get_face_by_position("Probe", [1, 0, 0])'

    with pytest.raises(PyAedtApiContractError) as caught:
        validate_pyaedt_api_fragment(source, "simulation_setup")

    assert "get_faceid_from_position(position=..., assignment=...)" in str(caught.value)


def test_assign_wave_port_names_supported_hfss_method():
    with pytest.raises(PyAedtApiContractError, match=r"hfss\.wave_port"):
        validate_pyaedt_api_fragment(
            "hfss.assign_wave_port(port_face, name='P1')", "simulation_setup"
        )


def test_nonexistent_assign_perfecte_names_supported_replacements():
    with pytest.raises(PyAedtApiContractError) as caught:
        validate_pyaedt_api_fragment(
            "hfss.assign_perfecte(face_id, name='Probe_PEC')",
            "simulation_setup",
        )

    message = str(caught.value)
    assert "assign_perfecte_to_sheets" in message
    assert "assign_perfect_e" in message


def test_linear_count_sweep_requires_singular_unit_keyword():
    source = """
hfss.create_linear_count_sweep(
    setup="Setup1", units="GHz", start_frequency=1, stop_frequency=2
)
"""

    with pytest.raises(PyAedtApiContractError) as caught:
        validate_pyaedt_api_fragment(source, "simulation_setup")

    message = str(caught.value)
    assert "unsupported units=" in message
    assert "requires unit" in message


def test_linear_count_sweep_rejects_unit_bearing_strings_when_unit_is_separate():
    source = """
hfss.create_linear_count_sweep(
    setup="Setup1", unit="GHz", start_frequency="8GHz",
    stop_frequency="12GHz", name="Sweep1"
)
"""

    with pytest.raises(PyAedtApiContractError) as caught:
        validate_pyaedt_api_fragment(source, "simulation_setup")

    message = str(caught.value)
    assert "start_frequency=" in message
    assert "stop_frequency=" in message
    assert "numeric frequency value" in message


def test_solver_boundary_and_port_keywords_match_installed_signatures():
    source = """
hfss.assign_radiation_boundary_to_objects(["Region"], name="Radiation")
hfss.assign_perfecte_to_sheets([1], name="Probe_PEC")
hfss.wave_port(
    assignment=outer.bottom_face_z, reference=outer.name,
    create_pec_cap=True, name="Probe_Port"
)
hfss.create_setup(name="Setup1", setup_type="HFSSDriven", Frequency="10GHz")
hfss.create_linear_count_sweep(
    setup="Setup1", unit="GHz", start_frequency=8, stop_frequency=12,
    name="Sweep1", sweep_type="Interpolating"
)
"""

    assert validate_pyaedt_api_fragment(source, "simulation_setup") is None


def test_invented_solver_keywords_and_lowercase_frequency_are_rejected():
    source = """
hfss.assign_radiation_boundary_to_objects(["Region"], boundary_name="Radiation")
hfss.assign_perfecte_to_sheets([1], boundary_name="Probe_PEC")
hfss.wave_port(assignment="cap", port_name="P1", reference_conductor=[1])
hfss.create_setup(name="Setup1", frequency="10GHz")
hfss.create_linear_count_sweep(
    setup="Setup1", unit="GHz", start_frequency=8, stop_frequency=12,
    sweep_name="Sweep1"
)
"""

    with pytest.raises(PyAedtApiContractError) as caught:
        validate_pyaedt_api_fragment(source, "simulation_setup")

    message = str(caught.value)
    for keyword in (
        "boundary_name=",
        "port_name=",
        "reference_conductor=",
        "frequency=",
        "sweep_name=",
    ):
        assert keyword in message


def test_unknown_contract_version_is_rejected_explicitly():
    with pytest.raises(ValueError, match="unsupported PyAEDT API contract version"):
        validate_pyaedt_api_fragment("hfss.modeler.object_names", "model_3d", "0.27.0")


@pytest.mark.parametrize(
    "relative_path",
    [
        "examples/validation/wifi_patch_5250/reference_model.py",
        "examples/validation/ibrahim_38ghz_monopole/reference_model.py",
        "examples/validation/khan_28_38ghz_monopole/reference_model.py",
        "examples/validation/khan_28_38ghz_monopole/reference_model_v2.py",
        "examples/validation/kaur_split_ring_monopole/reference_model.py",
        "examples/validation/yeo_slot_loaded_patch/reference_model.py",
        "examples/leam_case3/build_model.py",
        "examples/leam_paper_cases/case1_vivaldi/generated_model_v001.py",
        "examples/leam_paper_cases/case3_monopole/generated_model_v001.py",
    ],
)
def test_repository_reference_usage_matches_pyaedt_contract(relative_path):
    source = (ROOT / relative_path).read_text("utf-8")

    assert validate_pyaedt_api_fragment(source, relative_path) is None
