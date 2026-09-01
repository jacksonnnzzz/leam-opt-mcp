"""Deterministic HFSS translation of Khan et al.'s 28/38 GHz single element.

Only the single radiator in Figure 1 is in scope. Table 1 values are frozen as
paper evidence. Coordinates that Figure 1 does not uniquely anchor are kept in
the engineering-assumption record instead of being presented as paper facts.
Importing this module neither imports PyAEDT nor starts AEDT.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DESIGN_NAME = "Khan2024SingleElement28_38GHz"

_PAPER_PARAMETERS: dict[str, dict[str, Any]] = {
    "L": {"value": 9.2, "unit": "mm", "evidence": "Table 1"},
    "W": {"value": 5.0, "unit": "mm", "evidence": "Table 1"},
    "PL": {"value": 3.7, "unit": "mm", "evidence": "Table 1"},
    "QW": {"value": 1.4, "unit": "mm", "evidence": "Table 1"},
    "QL": {"value": 3.1, "unit": "mm", "evidence": "Table 1"},
    "T": {"value": 0.8, "unit": "mm", "evidence": "Table 1"},
    "RL": {"value": 1.3, "unit": "mm", "evidence": "Table 1"},
    "RW": {"value": 2.0, "unit": "mm", "evidence": "Table 1"},
    "SW": {"value": 0.2, "unit": "mm", "evidence": "Table 1"},
    "SL": {"value": 0.75, "unit": "mm", "evidence": "Table 1"},
    "BW": {"value": 0.3, "unit": "mm", "evidence": "Table 1"},
    "BL": {"value": 0.65, "unit": "mm", "evidence": "Table 1"},
    "FL": {"value": 3.3, "unit": "mm", "evidence": "Table 1"},
    "FW": {"value": 0.9, "unit": "mm", "evidence": "Table 1"},
    "AL": {"value": 2.4, "unit": "mm", "evidence": "Table 1"},
    "GL": {"value": 1.2, "unit": "mm", "evidence": "Table 1 and Figure 1(b)"},
    "Lc": {"value": 1.5, "unit": "mm", "evidence": "Table 1"},
    "AW": {"value": 0.2, "unit": "mm", "evidence": "Table 1"},
    "Ri": {"value": 0.6, "unit": "mm", "evidence": "Table 1"},
    "R": {"value": 0.3, "unit": "mm", "evidence": "Table 1"},
    "G": {"value": 0.3, "unit": "mm", "evidence": "Table 1"},
    "GW": {"value": 0.2, "unit": "mm", "evidence": "Table 1"},
    "substrate_thickness": {
        "value": 0.787,
        "unit": "mm",
        "evidence": "Dual-band design evaluation",
    },
    "substrate_relative_permittivity": {
        "value": 2.2,
        "unit": "ratio",
        "evidence": "Dual-band design evaluation",
    },
    "substrate_loss_tangent": {
        "value": 0.0009,
        "unit": "ratio",
        "evidence": "Dual-band design evaluation",
    },
}

_ENGINEERING_ASSUMPTIONS: dict[str, Any] = {
    "coordinate_interpretation": "figure1_centered_piecewise_trace_v1",
    "conductor_model": "zero_thickness_pec_sheets",
    "hidden_boolean_overlap_mm": 0.01,
    "feed_reference": "board_bottom_edge_centered",
    "ground_reference": "board_bottom_edge_with_GL_height",
    "Lc_interpretation": "right_inner_connector_vertical_length",
    "excitation": "microstrip_wave_port_on_unpadded_negative_y_face",
    "port_impedance_ohm": 50.0,
    "radiation_padding_mm": 2.0,
    "solution_type": "Modal",
    "adaptive_frequency_ghz": 38.0,
    "maximum_adaptive_passes": 12,
    "minimum_adaptive_passes": 2,
    "max_delta_s": 0.02,
    "sweep_start_ghz": 22.0,
    "sweep_stop_ghz": 43.0,
    "sweep_points": 1051,
    "sweep_type": "Interpolating",
}


def paper_parameters() -> dict[str, dict[str, Any]]:
    return deepcopy(_PAPER_PARAMETERS)


def engineering_assumptions() -> dict[str, Any]:
    return deepcopy(_ENGINEERING_ASSUMPTIONS)


def geometry_coordinates() -> dict[str, Any]:
    """Resolve the Figure 1 topology into explicit millimetre primitives.

    Figure 1 fixes dimensions but does not publish a complete vertex list. The
    translation centers the feed/radiator on the board, uses FL+SL as the lower
    frame elevation, and maps Lc to the right inner connector. Those three
    coordinate choices are assumptions and are intentionally reviewable here.
    """
    value = lambda name: float(_PAPER_PARAMETERS[name]["value"])
    width = value("W")
    length = value("L")
    height = value("substrate_thickness")
    center_x = width / 2.0
    feed_width = value("FW")
    feed_length = value("FL")
    branch_width = value("BW")
    branch_length = value("BL")
    slot_width = value("SW")
    slot_length = value("SL")
    trace_width = value("GW")
    outer_half_width = value("QW")
    patch_height = value("PL")
    clearance = value("G")
    overlap = float(_ENGINEERING_ASSUMPTIONS["hidden_boolean_overlap_mm"])

    feed_left = center_x - feed_width / 2.0
    body_bottom = feed_length + slot_length
    body_top = body_bottom + patch_height
    outer_left = center_x - outer_half_width
    outer_right = center_x + outer_half_width
    inner_left = outer_left + branch_width + clearance
    inner_bottom = body_bottom + branch_width + clearance
    inner_right = inner_left + value("RW")

    outer_radius = value("Ri")
    inner_radius = value("R")
    u_bottom = body_top + value("GL") - value("AL")
    u_center_y = u_bottom + outer_radius
    rod_top = u_bottom + value("AL")
    left_rod_x = center_x - outer_radius
    right_rod_x = center_x + outer_radius - value("AW")

    pieces = [
        {"name": "Radiator", "origin": [feed_left, 0.0, height], "size": [feed_width, feed_length + overlap]},
        {"name": "FeedCrossbar", "origin": [feed_left - branch_length, feed_length - overlap, height], "size": [feed_width + 2.0 * branch_length, branch_width + overlap]},
        {"name": "FeedNeck", "origin": [feed_left, feed_length - overlap, height], "size": [feed_width, slot_length + overlap]},
        {"name": "OuterLeft", "origin": [outer_left, body_bottom, height], "size": [branch_width, patch_height]},
        {"name": "OuterRight", "origin": [outer_right - branch_width, body_bottom, height], "size": [branch_width, patch_height]},
        {"name": "OuterBottom", "origin": [outer_left, body_bottom, height], "size": [2.0 * outer_half_width, branch_width]},
        {"name": "OuterTopLeft", "origin": [outer_left, body_top - branch_width, height], "size": [left_rod_x + value("AW") - outer_left, branch_width]},
        {"name": "OuterTopRight", "origin": [right_rod_x, body_top - branch_width, height], "size": [outer_right - right_rod_x, branch_width]},
        {"name": "InnerLeft", "origin": [inner_left, inner_bottom, height], "size": [trace_width, value("QL")]},
        {"name": "InnerLeftTop", "origin": [inner_left, inner_bottom + value("QL") - trace_width, height], "size": [left_rod_x + value("AW") - inner_left, trace_width]},
        {"name": "InnerBottom", "origin": [inner_left, inner_bottom, height], "size": [value("RW"), trace_width]},
        {"name": "InnerRight", "origin": [inner_right - trace_width, inner_bottom, height], "size": [trace_width, value("RL")]},
        {"name": "InnerRightTop", "origin": [inner_right - value("T"), inner_bottom + value("RL") - trace_width, height], "size": [value("T"), trace_width]},
        {"name": "InnerConnector", "origin": [right_rod_x, inner_bottom + value("RL") - overlap, height], "size": [value("AW"), value("Lc") + overlap]},
        {"name": "LeftTopRod", "origin": [left_rod_x, u_center_y - overlap, height], "size": [value("AW"), rod_top - u_center_y + overlap]},
        {"name": "RightTopRod", "origin": [right_rod_x, u_center_y - overlap, height], "size": [value("AW"), rod_top - u_center_y + overlap]},
    ]
    slot_tools = [
        {"name": "LeftFeedSlot", "origin": [feed_left - slot_width, body_bottom, height], "size": [slot_width, slot_length]},
        {"name": "RightFeedSlot", "origin": [feed_left + feed_width, body_bottom, height], "size": [slot_width, slot_length]},
    ]
    return {
        "substrate_origin": [0.0, 0.0, 0.0],
        "substrate_size": [width, length, height],
        "ground_origin": [0.0, 0.0, 0.0],
        "ground_size": [width, value("GL")],
        "radiator_pieces": pieces,
        "feed_slot_tools": slot_tools,
        "u_outer_center": [center_x, u_center_y, height],
        "u_outer_radius": outer_radius,
        "u_inner_radius": inner_radius,
        "u_top_cutter_origin": [center_x - outer_radius - overlap, u_center_y, height],
        "u_top_cutter_size": [2.0 * outer_radius + 2.0 * overlap, outer_radius + overlap],
        "port_integration_line": [[center_x, 0.0, 0.0], [center_x, 0.0, height]],
        "body_bounds": [outer_left, body_bottom, outer_right, body_top],
        "rod_top": rod_top,
        "unused_paper_parameters": [],
    }


def build_reference(hfss: Any) -> Any:
    """Build the frozen single element in an empty Driven Modal design."""
    if list(hfss.modeler.object_names):
        raise RuntimeError("Khan reference design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("Khan reference design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    coordinates = geometry_coordinates()
    material = _ensure_rt5880(hfss)
    substrate = hfss.modeler.create_box(
        coordinates["substrate_origin"],
        coordinates["substrate_size"],
        name="Substrate",
        material=material,
    )
    ground = hfss.modeler.create_rectangle(
        "XY",
        coordinates["ground_origin"],
        coordinates["ground_size"],
        name="Ground",
        material="pec",
    )
    if not substrate or not ground:
        raise RuntimeError("HFSS failed to create the substrate or partial ground")

    radiator_pieces = []
    for piece in coordinates["radiator_pieces"]:
        item = hfss.modeler.create_rectangle(
            "XY", piece["origin"], piece["size"], name=piece["name"], material="pec"
        )
        if not item:
            raise RuntimeError(f"HFSS failed to create radiator piece {piece['name']}")
        radiator_pieces.append(item)

    u_outer = hfss.modeler.create_circle(
        "XY",
        coordinates["u_outer_center"],
        coordinates["u_outer_radius"],
        name="UOuter",
        material="pec",
    )
    u_inner = hfss.modeler.create_circle(
        "XY",
        coordinates["u_outer_center"],
        coordinates["u_inner_radius"],
        name="UInnerTool",
        material="vacuum",
    )
    u_top = hfss.modeler.create_rectangle(
        "XY",
        coordinates["u_top_cutter_origin"],
        coordinates["u_top_cutter_size"],
        name="UTopTool",
        material="vacuum",
    )
    if not u_outer or not u_inner or not u_top:
        raise RuntimeError("HFSS failed to create the U-shaped resonator primitives")
    if not hfss.modeler.subtract(u_outer, u_inner, keep_originals=False):
        raise RuntimeError("HFSS failed to hollow the U-shaped resonator")
    if not hfss.modeler.subtract(u_outer, u_top, keep_originals=False):
        raise RuntimeError("HFSS failed to open the U-shaped resonator")
    radiator_pieces.append(u_outer)

    if not hfss.modeler.unite(radiator_pieces):
        raise RuntimeError("HFSS failed to unite the Figure 1 radiator pieces")
    radiator = radiator_pieces[0]
    for tool_spec in coordinates["feed_slot_tools"]:
        tool = hfss.modeler.create_rectangle(
            "XY",
            tool_spec["origin"],
            tool_spec["size"],
            name=tool_spec["name"],
            material="vacuum",
        )
        if not tool or not hfss.modeler.subtract(radiator, tool, keep_originals=False):
            raise RuntimeError(f"HFSS failed to cut {tool_spec['name']}")

    if not hfss.assign_perfecte_to_sheets(ground, "GroundPEC"):
        raise RuntimeError("HFSS failed to assign GroundPEC")
    if not hfss.assign_perfecte_to_sheets(radiator, "RadiatorPEC"):
        raise RuntimeError("HFSS failed to assign RadiatorPEC")

    padding = float(_ENGINEERING_ASSUMPTIONS["radiation_padding_mm"])
    region = hfss.modeler.create_region(
        [padding, padding, padding, 0.0, padding, padding],
        pad_type="Absolute Offset",
        name="Region",
    )
    if not region:
        raise RuntimeError("HFSS failed to create the radiation region")
    port_face = min(region.faces, key=lambda face: float(face.center[1]))
    radiation_faces = [face.id for face in region.faces if face.id != port_face.id]
    if len(radiation_faces) != 5:
        raise RuntimeError("HFSS radiation region does not have six faces")
    if not hfss.assign_radiation_boundary_to_faces(radiation_faces, name="Radiation"):
        raise RuntimeError("HFSS failed to assign the five radiation faces")
    port = hfss.wave_port(
        port_face.id,
        integration_line=coordinates["port_integration_line"],
        modes=1,
        impedance=float(_ENGINEERING_ASSUMPTIONS["port_impedance_ohm"]),
        name="WavePort1",
        renormalize=True,
    )
    if not port:
        raise RuntimeError("HFSS failed to create WavePort1")

    setup = hfss.create_setup(
        name="Setup1",
        setup_type="HFSSDriven",
        Frequency="38GHz",
        MaximumPasses=12,
        MinimumPasses=2,
        MaxDeltaS=0.02,
    )
    if not setup:
        raise RuntimeError("HFSS failed to create Setup1")
    sweep = setup.create_frequency_sweep(
        unit="GHz",
        name="Sweep1",
        start_frequency=22.0,
        stop_frequency=43.0,
        num_of_freq_points=1051,
        save_fields=False,
        save_rad_fields=False,
        sweep_type="Interpolating",
        interpolation_tol=0.25,
        interpolation_max_solutions=500,
    )
    if not sweep:
        raise RuntimeError("HFSS failed to create Sweep1")
    return hfss


def _ensure_rt5880(hfss: Any) -> str:
    name = "RT5880_Khan_2024_er2p2_tand0p0009"
    material = hfss.materials.exists_material(name)
    if material:
        actual_er = _material_value(material.permittivity)
        actual_tangent = _material_value(material.dielectric_loss_tangent)
        if abs(actual_er - 2.2) > 1e-12 or abs(actual_tangent - 0.0009) > 1e-12:
            raise RuntimeError(f"project material {name!r} has incompatible properties")
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the RT5880 material")
    material.permittivity = 2.2
    material.dielectric_loss_tangent = 0.0009
    return name


def _material_value(property_object: Any) -> float:
    raw = getattr(property_object, "evaluated_value", None)
    if raw is None:
        raw = getattr(property_object, "value", property_object)
    return float(raw)
