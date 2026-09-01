"""Figure-2-corrected topology for the Khan 2024 single element.

V1 incorrectly interpreted the rounded top slot as a separate annulus. Figure
2 shows an outer inverted-U trace, an inner U trace, and two top rods separated
by a round-ended slot. This version changes only that coordinate/topology
interpretation. Every Table 1 value remains unchanged. ``Lc`` is retained as
unresolved because neither Figure 1 nor the prose uniquely anchors it.
"""

from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path
from typing import Any


_BASE_SPEC = importlib.util.spec_from_file_location(
    "_khan_v1_parameter_source", Path(__file__).resolve().with_name("reference_model.py")
)
if _BASE_SPEC is None or _BASE_SPEC.loader is None:
    raise RuntimeError("unable to load the frozen Khan Table 1 parameter source")
_BASE = importlib.util.module_from_spec(_BASE_SPEC)
_BASE_SPEC.loader.exec_module(_BASE)


def paper_parameters() -> dict[str, dict[str, Any]]:
    return _BASE.paper_parameters()


DESIGN_NAME = "Khan2024SingleElement28_38GHz_V2"

_ENGINEERING_ASSUMPTIONS = {
    "coordinate_interpretation": "figure1_and_figure2_nested_u_trace_v2",
    "conductor_model": "zero_thickness_pec_sheets",
    "feed_reference": "board_bottom_edge_centered",
    "unmapped_paper_parameter": "Lc",
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


def engineering_assumptions() -> dict[str, Any]:
    return deepcopy(_ENGINEERING_ASSUMPTIONS)


def geometry_coordinates(assumptions: dict[str, Any] | None = None) -> dict[str, Any]:
    del assumptions
    parameters = paper_parameters()
    value = lambda name: float(parameters[name]["value"])
    board_width = value("W")
    board_length = value("L")
    substrate_height = value("substrate_thickness")
    center_x = board_width / 2.0
    feed_width = value("FW")
    trace_width = value("GW")
    arm_width = value("AW")
    slot_width = value("Ri")
    slot_radius = value("R")

    feed_left = center_x - feed_width / 2.0
    body_bottom = value("FL") + value("SL")
    body_top = body_bottom + value("PL")
    outer_left = center_x - value("QW")
    outer_right = center_x + value("QW")
    inner_bottom = body_bottom + value("G")
    inner_top = inner_bottom + value("QL")
    inner_left = center_x - value("RW") / 2.0
    inner_right = center_x + value("RW") / 2.0
    rod_left = center_x - slot_width / 2.0 - arm_width
    slot_left = center_x - slot_width / 2.0
    slot_right = center_x + slot_width / 2.0
    rod_right = slot_right
    rod_top = body_top + value("GL")
    slot_bottom = rod_top - value("AL")
    stem_bottom = inner_top - value("RL")

    pieces = [
        {
            "name": "Radiator",
            "origin": [feed_left, 0.0, substrate_height],
            "size": [feed_width, inner_bottom + trace_width],
        },
        {
            "name": "FeedBranches",
            "origin": [feed_left - value("BL"), value("FL"), substrate_height],
            "size": [feed_width + 2.0 * value("BL"), value("BW")],
        },
        {
            "name": "OuterLeftLeg",
            "origin": [outer_left, body_bottom, substrate_height],
            "size": [trace_width, value("PL")],
        },
        {
            "name": "OuterRightLeg",
            "origin": [outer_right - trace_width, body_bottom, substrate_height],
            "size": [trace_width, value("PL")],
        },
        {
            "name": "OuterTopLeft",
            "origin": [outer_left, body_top - trace_width, substrate_height],
            "size": [rod_left + arm_width - outer_left, trace_width],
        },
        {
            "name": "OuterTopRight",
            "origin": [rod_right, body_top - trace_width, substrate_height],
            "size": [outer_right - rod_right, trace_width],
        },
        {
            "name": "OuterBottomLeft",
            "origin": [outer_left, body_bottom, substrate_height],
            "size": [feed_left - value("SW") - outer_left, trace_width],
        },
        {
            "name": "OuterBottomRight",
            "origin": [feed_left + feed_width + value("SW"), body_bottom, substrate_height],
            "size": [outer_right - feed_left - feed_width - value("SW"), trace_width],
        },
        {
            "name": "InnerLeftLeg",
            "origin": [inner_left, inner_bottom, substrate_height],
            "size": [trace_width, value("QL")],
        },
        {
            "name": "InnerRightLeg",
            "origin": [inner_right - trace_width, inner_bottom, substrate_height],
            "size": [trace_width, value("QL")],
        },
        {
            "name": "InnerBottom",
            "origin": [inner_left, inner_bottom, substrate_height],
            "size": [value("RW"), trace_width],
        },
        {
            "name": "InnerTopLeft",
            "origin": [rod_left - value("T"), inner_top - trace_width, substrate_height],
            "size": [value("T") + arm_width, trace_width],
        },
        {
            "name": "InnerTopRight",
            "origin": [rod_right, inner_top - trace_width, substrate_height],
            "size": [value("T") + arm_width, trace_width],
        },
    ]
    return {
        "substrate_origin": [0.0, 0.0, 0.0],
        "substrate_size": [board_width, board_length, substrate_height],
        "ground_origin": [0.0, 0.0, 0.0],
        "ground_size": [board_width, value("GL")],
        "radiator_pieces": pieces,
        "top_stem_origin": [rod_left, stem_bottom, substrate_height],
        "top_stem_size": [slot_width + 2.0 * arm_width, rod_top - stem_bottom],
        "top_slot_rectangle_origin": [slot_left, slot_bottom + slot_radius, substrate_height],
        "top_slot_rectangle_size": [slot_width, rod_top - slot_bottom - slot_radius + 0.01],
        "top_slot_circle_center": [center_x, slot_bottom + slot_radius, substrate_height],
        "top_slot_circle_radius": slot_radius,
        "port_integration_line": [[center_x, 0.0, 0.0], [center_x, 0.0, substrate_height]],
        "port_sheet_origin": [feed_left, 0.0, 0.0],
        "port_sheet_size": [substrate_height, feed_width],
        "body_bounds": [outer_left, body_bottom, outer_right, body_top],
        "rod_top": rod_top,
        "unused_paper_parameters": ["Lc"],
    }


def build_reference(hfss: Any, assumptions: dict[str, Any] | None = None) -> Any:
    if list(hfss.modeler.object_names):
        raise RuntimeError("Khan V2 reference design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("Khan V2 reference design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    resolved = _resolved_assumptions(assumptions)
    coordinates = geometry_coordinates(resolved)
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

    pieces = []
    for specification in coordinates["radiator_pieces"]:
        item = hfss.modeler.create_rectangle(
            "XY",
            specification["origin"],
            specification["size"],
            name=specification["name"],
            material="pec",
        )
        if not item:
            raise RuntimeError(f"HFSS failed to create {specification['name']}")
        pieces.append(item)

    stem = hfss.modeler.create_rectangle(
        "XY",
        coordinates["top_stem_origin"],
        coordinates["top_stem_size"],
        name="TopStem",
        material="pec",
    )
    slot_rectangle = hfss.modeler.create_rectangle(
        "XY",
        coordinates["top_slot_rectangle_origin"],
        coordinates["top_slot_rectangle_size"],
        name="TopSlotVerticalTool",
        material="vacuum",
    )
    slot_circle = hfss.modeler.create_circle(
        "XY",
        coordinates["top_slot_circle_center"],
        coordinates["top_slot_circle_radius"],
        name="TopSlotRoundTool",
        material="vacuum",
    )
    if not stem or not slot_rectangle or not slot_circle:
        raise RuntimeError("HFSS failed to create the round-ended top slot")
    if not hfss.modeler.subtract(stem, slot_rectangle, keep_originals=False):
        raise RuntimeError("HFSS failed to cut the vertical top slot")
    if not hfss.modeler.subtract(stem, slot_circle, keep_originals=False):
        raise RuntimeError("HFSS failed to round the top slot end")
    pieces.append(stem)
    if not hfss.modeler.unite(pieces):
        raise RuntimeError("HFSS failed to unite the nested-U radiator")
    radiator = pieces[0]

    if not hfss.assign_perfecte_to_sheets(ground, "GroundPEC"):
        raise RuntimeError("HFSS failed to assign GroundPEC")
    if not hfss.assign_perfecte_to_sheets(radiator, "RadiatorPEC"):
        raise RuntimeError("HFSS failed to assign RadiatorPEC")

    padding = float(resolved["radiation_padding_mm"])
    wave_port = resolved["excitation"] == "microstrip_wave_port_on_unpadded_negative_y_face"
    region = hfss.modeler.create_region(
        [padding, padding, padding, 0.0, padding, padding]
        if wave_port
        else [padding] * 6,
        pad_type="Absolute Offset",
        name="Region",
    )
    if not region:
        raise RuntimeError("HFSS failed to create the radiation region")
    if wave_port:
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
            impedance=float(resolved["port_impedance_ohm"]),
            name="WavePort1",
            renormalize=True,
        )
    else:
        if not hfss.assign_radiation_boundary_to_objects(region, name="Radiation"):
            raise RuntimeError("HFSS failed to assign the radiation region")
        port_sheet = hfss.modeler.create_rectangle(
            "XZ",
            coordinates["port_sheet_origin"],
            coordinates["port_sheet_size"],
            name="LumpedPortSheet",
            material="vacuum",
        )
        if not port_sheet:
            raise RuntimeError("HFSS failed to create the assumed lumped-port sheet")
        port = hfss.lumped_port(
            port_sheet,
            integration_line=coordinates["port_integration_line"],
            impedance=float(resolved["port_impedance_ohm"]),
            name="LumpedPort1",
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


def _resolved_assumptions(overrides: dict[str, Any] | None) -> dict[str, Any]:
    resolved = engineering_assumptions()
    if overrides:
        unknown = sorted(set(overrides) - set(resolved))
        if unknown:
            raise ValueError(f"unsupported Khan V2 assumptions: {unknown}")
        resolved.update(overrides)
    if resolved["excitation"] not in {
        "microstrip_wave_port_on_unpadded_negative_y_face",
        "internal_microstrip_lumped_port",
    }:
        raise ValueError("unsupported Khan V2 excitation")
    if float(resolved["radiation_padding_mm"]) <= 0.0:
        raise ValueError("radiation padding must be positive")
    return resolved


def _ensure_rt5880(hfss: Any) -> str:
    name = "RT5880_Khan_2024_er2p2_tand0p0009"
    material = hfss.materials.exists_material(name)
    if material:
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the RT5880 material")
    material.permittivity = 2.2
    material.dielectric_loss_tangent = 0.0009
    return name
