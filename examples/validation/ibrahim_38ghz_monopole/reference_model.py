"""Frozen HFSS translation of Ibrahim et al.'s 38 GHz Antenna 3.

Only the single element in Figure 4 is in scope. Paper-explicit quantities and
the HFSS implementation assumptions are deliberately separate. Importing this
module neither imports PyAEDT nor starts AEDT.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


DESIGN_NAME = "Ibrahim2023Antenna3_38GHz"

_PAPER_PARAMETERS: dict[str, dict[str, Any]] = {
    "target_frequency": {"value": 38.0, "unit": "GHz", "evidence": "paper"},
    "band_start": {"value": 36.5, "unit": "GHz", "evidence": "paper"},
    "band_stop": {"value": 39.5, "unit": "GHz", "evidence": "paper"},
    "substrate_length_L": {"value": 12.0, "unit": "mm", "evidence": "paper"},
    "substrate_width_L": {"value": 12.0, "unit": "mm", "evidence": "paper"},
    "substrate_thickness_h": {"value": 0.203, "unit": "mm", "evidence": "paper"},
    "substrate_relative_permittivity": {"value": 3.55, "unit": "ratio", "evidence": "paper"},
    # Figure 1 draws R across the full circle; the prose calls R the diameter.
    "radiator_diameter_R": {"value": 4.94, "unit": "mm", "evidence": "paper"},
    "slot_width_W1": {"value": 2.2, "unit": "mm", "evidence": "paper"},
    "slot_left_length_L1": {"value": 2.45, "unit": "mm", "evidence": "paper"},
    "slot_right_length_L2": {"value": 2.35, "unit": "mm", "evidence": "paper"},
    "feed_width_Wf": {"value": 0.4, "unit": "mm", "evidence": "paper"},
    "feed_length_Lf": {"value": 7.0, "unit": "mm", "evidence": "paper"},
    "ground_length_Lg": {"value": 7.7, "unit": "mm", "evidence": "paper"},
}

_ENGINEERING_ASSUMPTIONS: dict[str, Any] = {
    "conductor_model": "zero_thickness_pec_sheets",
    "feed_boolean_overlap_mm": 0.0,
    "substrate_loss_tangent": 0.0027,
    "slot_coordinate_rule": "circle_chord_lengths_L1_L2_with_opening_W1",
    "excitation": "microstrip_wave_port_on_unpadded_negative_y_face",
    "port_impedance_ohm": 50.0,
    "radiation_padding_mm": 2.0,
    "solution_type": "Modal",
    "adaptive_frequency_ghz": 38.0,
    "maximum_adaptive_passes": 12,
    "minimum_adaptive_passes": 2,
    "max_delta_s": 0.02,
    "sweep_start_ghz": 34.0,
    "sweep_stop_ghz": 42.0,
    "sweep_points": 801,
    "sweep_type": "Interpolating",
}


def paper_parameters() -> dict[str, dict[str, Any]]:
    """Return only values explicitly supported by the selected paper scope."""
    return deepcopy(_PAPER_PARAMETERS)


def engineering_assumptions() -> dict[str, Any]:
    """Return the frozen choices needed because the paper omits HFSS details."""
    return deepcopy(_ENGINEERING_ASSUMPTIONS)


def geometry_coordinates(assumptions: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve Figure 4 into deterministic millimetre coordinates.

    The board lower-left corner is (0, 0). The feed rises along +Y. Figure 4
    gives unequal slot-side lengths L1 and L2 but no slot offset. The offset is
    therefore derived, not visually guessed: for slot edges x_left and
    x_right=x_left+W1 on a circle of radius r, the common slot floor satisfies
    sqrt(r^2-x_left^2)-L1 == sqrt(r^2-x_right^2)-L2.
    """
    resolved = _resolved_assumptions(assumptions)
    value = lambda name: float(_PAPER_PARAMETERS[name]["value"])
    board = value("substrate_length_L")
    height = value("substrate_thickness_h")
    diameter = value("radiator_diameter_R")
    radius = diameter / 2.0
    feed_length = value("feed_length_Lf")
    feed_width = value("feed_width_Wf")
    slot_width = value("slot_width_W1")
    left_length = value("slot_left_length_L1")
    right_length = value("slot_right_length_L2")
    center_x = board / 2.0
    center_y = feed_length + radius
    slot_left_relative = _solve_slot_left_edge(
        radius, slot_width, left_length - right_length
    )
    slot_right_relative = slot_left_relative + slot_width
    slot_floor_relative = (
        math.sqrt(radius * radius - slot_left_relative * slot_left_relative)
        - left_length
    )
    slot_left_outer_y = math.sqrt(
        radius * radius - slot_left_relative * slot_left_relative
    )
    slot_right_outer_y = math.sqrt(
        radius * radius - slot_right_relative * slot_right_relative
    )
    conductor_model = str(resolved["conductor_model"])
    conductor_thickness = {
        "zero_thickness_pec_sheets": 0.0,
        "finite_copper_0p017mm": 0.017,
        "finite_copper_0p035mm": 0.035,
    }[conductor_model]
    return {
        "substrate_origin": [0.0, 0.0, 0.0],
        "substrate_size": [board, board, height],
        "ground_origin": [0.0, 0.0, 0.0],
        "ground_size": [board, value("ground_length_Lg")],
        "feed_origin": [center_x - feed_width / 2.0, 0.0, height],
        # Lf remains the visible line length to the circle tangent. A positive
        # overlap extends only the internal Boolean tool under the circular
        # radiator and therefore does not change the Figure 4 outer contour.
        "feed_size": [feed_width, feed_length + float(resolved["feed_boolean_overlap_mm"])],
        "feed_visible_length": feed_length,
        "radiator_center": [center_x, center_y, height],
        "radiator_radius": radius,
        "slot_origin": [
            center_x + slot_left_relative,
            center_y + slot_floor_relative,
            height,
        ],
        # Extend beyond the circle so the top opening is guaranteed to be cut.
        "slot_size": [slot_width, radius - slot_floor_relative + 0.1],
        "slot_left_relative_x": slot_left_relative,
        "slot_right_relative_x": slot_right_relative,
        "slot_floor_relative_y": slot_floor_relative,
        "slot_left_reconstructed_length": slot_left_outer_y - slot_floor_relative,
        "slot_right_reconstructed_length": slot_right_outer_y - slot_floor_relative,
        "port_integration_line": [
            [center_x, 0.0, 0.0],
            [center_x, 0.0, height],
        ],
        "port_sheet_origin": [center_x - feed_width / 2.0, 0.0, 0.0],
        # PyAEDT 0.26.3 maps XZ rectangle dimensions as [Z span, X span].
        "port_sheet_size": [height, feed_width],
        "conductor_thickness": conductor_thickness,
    }


def build_reference(hfss: Any, assumptions: dict[str, Any] | None = None) -> Any:
    """Build the frozen single element in a deliberately empty Modal design."""
    if list(hfss.modeler.object_names):
        raise RuntimeError("Ibrahim reference design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("Ibrahim reference design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    resolved = _resolved_assumptions(assumptions)
    coordinates = geometry_coordinates(resolved)
    material = _ensure_ro4003(hfss, float(resolved["substrate_loss_tangent"]))
    substrate = hfss.modeler.create_box(
        coordinates["substrate_origin"],
        coordinates["substrate_size"],
        name="Substrate",
        material=material,
    )
    finite_copper = coordinates["conductor_thickness"] > 0.0
    if finite_copper:
        thickness = float(coordinates["conductor_thickness"])
        ground = hfss.modeler.create_box(
            [*coordinates["ground_origin"][:2], -thickness],
            [*coordinates["ground_size"], thickness],
            name="Ground",
            material="copper",
        )
        feed = hfss.modeler.create_box(
            coordinates["feed_origin"],
            [*coordinates["feed_size"], thickness],
            name="FeedLine",
            material="copper",
        )
        radiator = hfss.modeler.create_cylinder(
            "Z",
            coordinates["radiator_center"],
            coordinates["radiator_radius"],
            thickness,
            name="Radiator",
            material="copper",
        )
    else:
        ground = hfss.modeler.create_rectangle(
            "XY",
            coordinates["ground_origin"],
            coordinates["ground_size"],
            name="Ground",
            material="pec",
        )
        feed = hfss.modeler.create_rectangle(
            "XY",
            coordinates["feed_origin"],
            coordinates["feed_size"],
            name="FeedLine",
            material="pec",
        )
        radiator = hfss.modeler.create_circle(
            "XY",
            coordinates["radiator_center"],
            coordinates["radiator_radius"],
            name="Radiator",
            material="pec",
        )
    if not substrate or not ground or not feed or not radiator:
        raise RuntimeError("HFSS failed to create the paper-explicit geometry")
    if not hfss.modeler.unite([radiator, feed]):
        raise RuntimeError("HFSS failed to unite the radiator and feed line")
    if finite_copper:
        slot = hfss.modeler.create_box(
            coordinates["slot_origin"],
            [*coordinates["slot_size"], coordinates["conductor_thickness"]],
            name="SlotTool",
            material="vacuum",
        )
    else:
        slot = hfss.modeler.create_rectangle(
            "XY",
            coordinates["slot_origin"],
            coordinates["slot_size"],
            name="SlotTool",
            material="vacuum",
        )
    if not slot or not hfss.modeler.subtract(radiator, slot, keep_originals=False):
        raise RuntimeError("HFSS failed to cut the Figure 4 rectangular slot")
    if not finite_copper:
        if not hfss.assign_perfecte_to_sheets(ground, "GroundPEC"):
            raise RuntimeError("HFSS failed to assign GroundPEC")
        if not hfss.assign_perfecte_to_sheets(radiator, "RadiatorPEC"):
            raise RuntimeError("HFSS failed to assign RadiatorPEC")

    padding = float(resolved["radiation_padding_mm"])
    wave_port = resolved["excitation"] == "microstrip_wave_port_on_unpadded_negative_y_face"
    region_padding = (
        [padding, padding, padding, 0.0, padding, padding]
        if wave_port
        else [padding] * 6
    )
    region = hfss.modeler.create_region(
        region_padding, pad_type="Absolute Offset", name="Region"
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
        raise RuntimeError("HFSS failed to create the assumed microstrip wave port")

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
        start_frequency=34.0,
        stop_frequency=42.0,
        num_of_freq_points=801,
        save_fields=False,
        save_rad_fields=False,
        sweep_type="Interpolating",
        interpolation_tol=0.25,
        interpolation_max_solutions=450,
    )
    if not sweep:
        raise RuntimeError("HFSS failed to create Sweep1")
    return hfss


def _solve_slot_left_edge(radius: float, width: float, delta_length: float) -> float:
    """Solve the unique in-circle slot offset implied by L1-L2."""
    if radius <= 0.0 or width <= 0.0 or width >= 2.0 * radius:
        raise ValueError("slot width must be positive and smaller than the diameter")
    lower = -radius + 1e-12
    upper = radius - width - 1e-12

    def residual(left: float) -> float:
        right = left + width
        return (
            math.sqrt(max(0.0, radius * radius - left * left))
            - math.sqrt(max(0.0, radius * radius - right * right))
            - delta_length
        )

    if residual(lower) * residual(upper) >= 0.0:
        raise ValueError("paper slot dimensions do not define an in-circle solution")
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if residual(middle) > 0.0:
            upper = middle
        else:
            lower = middle
    return (lower + upper) / 2.0


def _ensure_ro4003(hfss: Any, loss_tangent: float) -> str:
    if loss_tangent <= 0.0:
        raise ValueError("substrate_loss_tangent must be positive")
    suffix = f"{loss_tangent:.6f}".rstrip("0").replace(".", "p")
    name = f"RO4003_Ibrahim_2023_er3p55_assumed_tand{suffix}"
    material = hfss.materials.exists_material(name)
    if material:
        actual_er = _material_value(material.permittivity)
        actual_tangent = _material_value(material.dielectric_loss_tangent)
        if abs(actual_er - 3.55) > 1e-12 or abs(actual_tangent - loss_tangent) > 1e-12:
            raise RuntimeError(f"project material {name!r} has incompatible properties")
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the paper substrate material")
    material.permittivity = 3.55
    material.dielectric_loss_tangent = loss_tangent
    return name


def _resolved_assumptions(overrides: dict[str, Any] | None) -> dict[str, Any]:
    resolved = deepcopy(_ENGINEERING_ASSUMPTIONS)
    if overrides is not None:
        legacy_keys = set(resolved) - {"feed_boolean_overlap_mm"}
        override_keys = set(overrides) if isinstance(overrides, dict) else set()
        if not isinstance(overrides, dict) or (
            override_keys != set(resolved) and override_keys != legacy_keys
        ):
            raise ValueError("assumption override must preserve the frozen assumption keys")
        resolved.update(deepcopy(overrides))
    if resolved["conductor_model"] not in {
        "zero_thickness_pec_sheets",
        "finite_copper_0p017mm",
        "finite_copper_0p035mm",
    }:
        raise ValueError("unsupported conductor_model assumption")
    if resolved["excitation"] not in {
        "microstrip_wave_port_on_unpadded_negative_y_face",
        "internal_microstrip_lumped_port",
    }:
        raise ValueError("unsupported excitation assumption")
    for name in ("substrate_loss_tangent", "port_impedance_ohm", "radiation_padding_mm"):
        if float(resolved[name]) <= 0.0:
            raise ValueError(f"{name} must be positive")
    if float(resolved["port_impedance_ohm"]) != 50.0:
        raise ValueError("the study keeps the paper's 50-ohm feed frozen")
    if float(resolved["feed_boolean_overlap_mm"]) < 0.0:
        raise ValueError("feed_boolean_overlap_mm cannot be negative")
    if float(resolved["feed_boolean_overlap_mm"]) >= float(
        _PAPER_PARAMETERS["radiator_diameter_R"]["value"]
    ) / 2.0:
        raise ValueError("feed_boolean_overlap_mm must remain inside the radiator")
    return resolved


def _material_value(property_object: Any) -> float:
    raw = getattr(property_object, "evaluated_value", None)
    if raw is None:
        raw = getattr(property_object, "value", property_object)
    return float(raw)
