"""Deterministic HFSS translation of the three Kaur et al. (2021) cases.

The paper values and the engineering choices needed to turn its drawings into
an executable HFSS model are intentionally exposed through different APIs.
Importing this module neither imports PyAEDT nor starts AEDT.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DESIGN_NAMES = {
    "baseline": "Kaur2021BaselineUWB",
    "wlan_notch": "Kaur2021WLANNotch",
    "xband_notch": "Kaur2021XBandNotch",
}


_COMMON_PAPER_PARAMETERS: dict[str, dict[str, Any]] = {
    "substrate_width_WS": {"value": 18.0, "unit": "mm", "evidence": "Table 1"},
    "substrate_length_LS": {"value": 18.0, "unit": "mm", "evidence": "Table 1"},
    "substrate_thickness_h": {"value": 1.6, "unit": "mm", "evidence": "Table 1"},
    "substrate_relative_permittivity": {"value": 4.4, "unit": "ratio", "evidence": "Section 2.1"},
    "substrate_loss_tangent": {"value": 0.02, "unit": "ratio", "evidence": "Section 2.1"},
    "patch_width_WP": {"value": 13.5, "unit": "mm", "evidence": "Table 1"},
    "patch_length_LP": {"value": 9.0, "unit": "mm", "evidence": "Table 1"},
    "ground_width_WG": {"value": 7.95, "unit": "mm", "evidence": "Table 1"},
    "ground_length_LG": {"value": 5.4, "unit": "mm", "evidence": "Table 1"},
    "feed_width_WF": {"value": 1.2, "unit": "mm", "evidence": "Table 1"},
    "feed_length_LF": {"value": 5.934, "unit": "mm", "evidence": "Table 1"},
    "stub_width_X1": {"value": 6.0, "unit": "mm", "evidence": "Table 1"},
    "stub_length_Y1": {"value": 1.5, "unit": "mm", "evidence": "Table 1"},
}

_CASE_PAPER_PARAMETERS: dict[str, dict[str, dict[str, Any]]] = {
    "baseline": {},
    "wlan_notch": {
        "srs_outer_radius_R1": {"value": 2.4, "unit": "mm", "evidence": "Table 2"},
        "srs_inner_radius_R2": {"value": 2.1, "unit": "mm", "evidence": "Table 2"},
        "srs_split_gap_S1": {"value": 0.4, "unit": "mm", "evidence": "Table 2"},
    },
    "xband_notch": {
        "srs_outer_radius_R1_prime": {"value": 2.1, "unit": "mm", "evidence": "Table 2"},
        "srs_inner_radius_R2_prime": {"value": 1.6, "unit": "mm", "evidence": "Table 2"},
        "srs_split_gap_S1_prime": {"value": 0.4, "unit": "mm", "evidence": "Table 2"},
    },
}

_ENGINEERING_ASSUMPTIONS: dict[str, Any] = {
    "coordinate_convention": {
        "value": "substrate lower-left=(-9,0,0), feed runs in +y, metal is on z=h",
        "evidence": "implementation assumption from Figures 5 and 7",
    },
    "patch_vertical_placement": {
        "value": "feed, stub and patch are exactly contiguous: patch_bottom=LF+Y1",
        "derived_patch_top_margin_mm": 1.566,
        "evidence": "connectivity constraint plus Figure 5; vertical placement not dimensioned",
    },
    "srs_location": {
        "value": "ring centre lies on x=0 and y equals the patch bottom edge",
        "evidence": "implementation assumption inferred from Figure 7; not dimensioned",
    },
    "srs_split_orientation": {
        "value": "gap centred at the bottom of the ring",
        "evidence": "implementation assumption inferred from Figure 7",
    },
    "conductor_model": {
        "value": "zero-thickness PEC sheets",
        "evidence": "implementation assumption; material/thickness unresolved by paper",
    },
    "feed_excitation": {
        "value": "XZ lumped-port sheet spans the 2.1 mm CPW centre opening at y=0; integration line crosses from feed right edge to right-ground inner edge at z=h",
        "evidence": "implementation assumption; connector/port unresolved by paper",
    },
    "radiation_padding": {
        "value": 25.0,
        "unit": "mm",
        "evidence": "implementation assumption",
    },
    "solution_type": {"value": "Modal", "evidence": "implementation assumption"},
    "setup": {
        "adaptive_frequency_ghz": 7.5,
        "maximum_passes": 14,
        "minimum_passes": 2,
        "max_delta_s": 0.02,
        "evidence": "implementation assumption",
    },
    "sweep": {
        "type": "Interpolating",
        "start_ghz": 3.0,
        "stop_ghz": 12.0,
        "points": 901,
        "evidence": "implementation assumption",
    },
}


def paper_parameters(case: str) -> dict[str, dict[str, Any]]:
    """Return only dimensions/material properties explicitly stated by the paper."""
    _validate_case(case)
    return deepcopy({**_COMMON_PAPER_PARAMETERS, **_CASE_PAPER_PARAMETERS[case]})


def engineering_assumptions() -> dict[str, Any]:
    """Return the labelled assumptions required for an executable HFSS model."""
    return deepcopy(_ENGINEERING_ASSUMPTIONS)


def geometry_coordinates(case: str) -> dict[str, Any]:
    """Resolve Figures 5/7 and the frozen coordinate convention in millimetres."""
    p = paper_parameters(case)
    value = lambda name: float(p[name]["value"])
    ws, ls = value("substrate_width_WS"), value("substrate_length_LS")
    h = value("substrate_thickness_h")
    wp, lp = value("patch_width_WP"), value("patch_length_LP")
    wg, lg = value("ground_width_WG"), value("ground_length_LG")
    wf, lf = value("feed_width_WF"), value("feed_length_LF")
    x1, y1 = value("stub_width_X1"), value("stub_length_Y1")

    stub_bottom = lf
    patch_bottom = stub_bottom + y1
    patch_top = patch_bottom + lp
    cpw_opening = ws - 2.0 * wg
    coordinates: dict[str, Any] = {
        "substrate_origin": [-ws / 2.0, 0.0, 0.0],
        "substrate_size": [ws, ls, h],
        "left_ground_origin": [-ws / 2.0, 0.0, h],
        "left_ground_size": [wg, lg],
        "right_ground_origin": [ws / 2.0 - wg, 0.0, h],
        "right_ground_size": [wg, lg],
        "patch_origin": [-wp / 2.0, patch_bottom, h],
        "patch_size": [wp, lp],
        "stub_origin": [-x1 / 2.0, stub_bottom, h],
        "stub_size": [x1, y1],
        "feed_origin": [-wf / 2.0, 0.0, h],
        "feed_size": [wf, lf],
        "port_origin": [-cpw_opening / 2.0, 0.0, 0.0],
        "port_size": [cpw_opening, h],
        "port_integration_line": [[wf / 2.0, 0.0, h], [cpw_opening / 2.0, 0.0, h]],
        "patch_bottom_y": patch_bottom,
        "patch_top_margin": ls - patch_top,
    }
    if case != "baseline":
        suffix = "" if case == "wlan_notch" else "_prime"
        coordinates["srs_center"] = [0.0, patch_bottom, h]
        coordinates["srs_outer_radius"] = value(f"srs_outer_radius_R1{suffix}")
        coordinates["srs_inner_radius"] = value(f"srs_inner_radius_R2{suffix}")
        coordinates["srs_split_gap"] = value(f"srs_split_gap_S1{suffix}")
    return coordinates


def build_reference(hfss: Any, case: str) -> Any:
    """Build one frozen Kaur case in a deliberately empty Driven Modal design."""
    _validate_case(case)
    if list(hfss.modeler.object_names):
        raise RuntimeError("Kaur reference design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("Kaur reference design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    coordinates = geometry_coordinates(case)
    substrate = hfss.modeler.create_box(
        coordinates["substrate_origin"], coordinates["substrate_size"],
        name="FR4_Substrate", material=_ensure_fr4(hfss),
    )
    left_ground = _rectangle(hfss, "LeftGround", coordinates["left_ground_origin"], coordinates["left_ground_size"])
    right_ground = _rectangle(hfss, "RightGround", coordinates["right_ground_origin"], coordinates["right_ground_size"])
    patch = _rectangle(hfss, "Patch", coordinates["patch_origin"], coordinates["patch_size"])
    stub = _rectangle(hfss, "MatchingStub", coordinates["stub_origin"], coordinates["stub_size"])
    feed = _rectangle(hfss, "FeedLine", coordinates["feed_origin"], coordinates["feed_size"])
    if not all([substrate, left_ground, right_ground, patch, stub, feed]):
        raise RuntimeError("HFSS failed to create the Kaur paper geometry")

    radiator = hfss.modeler.unite([patch, stub, feed])
    if not radiator:
        raise RuntimeError("HFSS failed to unite the patch, stub, and feed")
    if str(getattr(radiator, "name", radiator)) != "Patch":
        raise RuntimeError("HFSS did not preserve Patch as the united radiator name")
    ground = hfss.modeler.unite([left_ground, right_ground])
    if not ground:
        raise RuntimeError("HFSS failed to unite the CPW ground planes")
    if str(getattr(ground, "name", ground)) != "LeftGround":
        raise RuntimeError("HFSS did not preserve LeftGround as the united ground name")

    if case != "baseline":
        outer = hfss.modeler.create_circle(
            "XY", coordinates["srs_center"], coordinates["srs_outer_radius"],
            name="SRSOuterTool", material="vacuum",
        )
        inner = hfss.modeler.create_circle(
            "XY", coordinates["srs_center"], coordinates["srs_inner_radius"],
            name="SRSInnerKeepTool", material="vacuum",
        )
        if not outer or not inner or not hfss.modeler.subtract(outer, inner, keep_originals=False):
            raise RuntimeError("HFSS failed to create the annular SRS tool")
        gap = float(coordinates["srs_split_gap"])
        radius = float(coordinates["srs_outer_radius"])
        split = _rectangle(
            hfss,
            "SRSSplitKeepTool",
            [-gap / 2.0, float(coordinates["patch_bottom_y"]) - radius, coordinates["srs_center"][2]],
            [gap, radius],
            material="vacuum",
        )
        if not split or not hfss.modeler.subtract(outer, split, keep_originals=False):
            raise RuntimeError("HFSS failed to open the SRS split")
        if not hfss.modeler.subtract("Patch", outer, keep_originals=False):
            raise RuntimeError("HFSS failed to etch the SRS from the radiator")

    for conductor, boundary_name in (("Patch", "RadiatorPEC"), ("LeftGround", "GroundPEC")):
        if not hfss.assign_perfecte_to_sheets(conductor, boundary_name):
            raise RuntimeError(f"HFSS failed to assign {boundary_name}")

    port_sheet = hfss.modeler.create_rectangle(
        "XZ",
        coordinates["port_origin"],
        # PyAEDT 0.26.3 maps XZ rectangle dimensions as [Z span, X span].
        [coordinates["port_size"][1], coordinates["port_size"][0]],
        name="LumpedPortSheet", material="vacuum",
    )
    if not port_sheet:
        raise RuntimeError("HFSS failed to create the assumed CPW port sheet")
    port = hfss.lumped_port(
        port_sheet,
        integration_line=coordinates["port_integration_line"],
        impedance=50,
        name="LumpedPort1",
        renormalize=True,
    )
    if not port:
        raise RuntimeError("HFSS failed to assign the assumed CPW lumped port")

    padding = float(_ENGINEERING_ASSUMPTIONS["radiation_padding"]["value"])
    region = hfss.modeler.create_region([padding] * 6, pad_type="Absolute Offset", name="Region")
    if not region or not hfss.assign_radiation_boundary_to_objects(region, name="Radiation"):
        raise RuntimeError("HFSS failed to create the open radiation region")

    setup_values = _ENGINEERING_ASSUMPTIONS["setup"]
    setup = hfss.create_setup(
        name="Setup1", setup_type="HFSSDriven",
        Frequency=f"{setup_values['adaptive_frequency_ghz']}GHz",
        MaximumPasses=setup_values["maximum_passes"],
        MinimumPasses=setup_values["minimum_passes"],
        MaxDeltaS=setup_values["max_delta_s"],
    )
    if not setup:
        raise RuntimeError("HFSS failed to create Setup1")
    sweep_values = _ENGINEERING_ASSUMPTIONS["sweep"]
    sweep = setup.create_frequency_sweep(
        unit="GHz", name="Sweep1",
        start_frequency=sweep_values["start_ghz"],
        stop_frequency=sweep_values["stop_ghz"],
        num_of_freq_points=sweep_values["points"],
        save_fields=False, save_rad_fields=False,
        sweep_type=sweep_values["type"],
        interpolation_tol=0.25, interpolation_max_solutions=450,
    )
    if not sweep:
        raise RuntimeError("HFSS failed to create Sweep1")
    return hfss


def _rectangle(
    hfss: Any, name: str, origin: list[float], size: list[float], material: str = "pec"
) -> Any:
    return hfss.modeler.create_rectangle("XY", origin, size, name=name, material=material)


def _ensure_fr4(hfss: Any) -> str:
    name = "FR4_Kaur_2021_er4p4_tand0p02"
    material = hfss.materials.exists_material(name)
    if material:
        actual_er = _material_value(material.permittivity)
        actual_tangent = _material_value(material.dielectric_loss_tangent)
        if abs(actual_er - 4.4) > 1e-12 or abs(actual_tangent - 0.02) > 1e-12:
            raise RuntimeError(f"project material {name!r} has incompatible properties")
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the paper FR-4 material")
    material.permittivity = 4.4
    material.dielectric_loss_tangent = 0.02
    return name


def _material_value(property_object: Any) -> float:
    raw = getattr(property_object, "evaluated_value", None)
    if raw is None:
        raw = getattr(property_object, "value", property_object)
    return float(raw)


def _validate_case(case: str) -> None:
    if case not in DESIGN_NAMES:
        choices = ", ".join(sorted(DESIGN_NAMES))
        raise ValueError(f"unknown Kaur case {case!r}; choose one of: {choices}")
