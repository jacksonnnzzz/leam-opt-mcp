"""HFSS translation of two antennas from Yeo and Lee (2019).

The paper dimensions and the engineering assumptions used to translate the CST
models to HFSS are deliberately kept in separate dictionaries. Importing this
module does not import PyAEDT or start AEDT.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DESIGN_NAMES = {
    "conventional": "YeoConventionalPatch",
    "scaled_slot_loaded": "YeoScaledSlotLoadedPatch",
}


_SHARED_PAPER_PARAMETERS: dict[str, dict[str, Any]] = {
    "ground_width": {"value": 80.0, "unit": "mm", "evidence": "paper"},
    "ground_length": {"value": 80.0, "unit": "mm", "evidence": "paper"},
    "substrate_thickness": {"value": 0.76, "unit": "mm", "evidence": "paper"},
    "substrate_relative_permittivity": {
        "value": 3.5,
        "unit": "ratio",
        "evidence": "paper",
    },
    "substrate_loss_tangent": {
        "value": 0.0018,
        "unit": "ratio",
        "evidence": "paper",
    },
    "feed_width": {"value": 1.66, "unit": "mm", "evidence": "paper"},
}


_CASE_PAPER_PARAMETERS: dict[str, dict[str, dict[str, Any]]] = {
    "conventional": {
        "patch_width": {"value": 40.0, "unit": "mm", "evidence": "paper"},
        "patch_length": {"value": 31.9, "unit": "mm", "evidence": "paper"},
        "feed_length": {"value": 24.5, "unit": "mm", "evidence": "paper"},
        "inset_width": {"value": 2.8, "unit": "mm", "evidence": "paper"},
        "inset_length": {"value": 9.0, "unit": "mm", "evidence": "paper"},
        "first_resonance": {"value": 2.5, "unit": "GHz", "evidence": "paper"},
        "first_minus_10db_lower": {
            "value": 2.490,
            "unit": "GHz",
            "evidence": "paper",
        },
        "first_minus_10db_upper": {
            "value": 2.510,
            "unit": "GHz",
            "evidence": "paper",
        },
    },
    "scaled_slot_loaded": {
        "patch_width": {"value": 31.8, "unit": "mm", "evidence": "paper"},
        "patch_length": {"value": 25.4, "unit": "mm", "evidence": "paper"},
        "feed_length": {"value": 27.3, "unit": "mm", "evidence": "paper"},
        "inset_width": {"value": 2.3, "unit": "mm", "evidence": "paper"},
        "inset_length": {"value": 12.0, "unit": "mm", "evidence": "paper"},
        "slot_width": {"value": 1.0, "unit": "mm", "evidence": "paper"},
        "slot_length": {"value": 29.8, "unit": "mm", "evidence": "paper"},
        "slot_to_radiating_edge": {
            "value": 1.0,
            "unit": "mm",
            "evidence": "paper",
        },
        "first_resonance": {"value": 2.5, "unit": "GHz", "evidence": "paper"},
        "second_resonance": {"value": 3.465, "unit": "GHz", "evidence": "paper"},
        "first_minus_10db_lower": {
            "value": 2.496,
            "unit": "GHz",
            "evidence": "paper",
        },
        "first_minus_10db_upper": {
            "value": 2.503,
            "unit": "GHz",
            "evidence": "paper",
        },
    },
}


_ENGINEERING_ASSUMPTIONS: dict[str, Any] = {
    "coordinate_convention": {
        "value": "ground lower-left is (-40, 0); feed enters from y=0; z=0 is the substrate bottom",
        "evidence": "implementation_assumption_from_figure_1",
    },
    "patch_lower_edge_y": {
        "value": "feed_length",
        "evidence": "implementation_assumption_from_figure_1",
    },
    "inset_definition": {
        "value": "centered opening of total width inset_width and depth inset_length",
        "evidence": "implementation_assumption_from_figure_1",
    },
    "conductor_material": {
        "value": "copper",
        "evidence": "implementation_assumption_unresolved_by_paper",
    },
    "conductor_thickness": {
        "value": 0.035,
        "unit": "mm",
        "evidence": "implementation_assumption_unresolved_by_paper",
    },
    "mut_present": {
        "value": False,
        "evidence": "paper_unloaded_condition",
    },
    "hfss_solution_type": {
        "value": "Modal",
        "evidence": "implementation_assumption_cross_solver_translation",
    },
    "radiation_padding": {
        "value": 30.0,
        "unit": "mm",
        "evidence": "implementation_assumption_approximately_quarter_wavelength",
    },
    "sweep": {
        "type": "Interpolating",
        "start_ghz": 1.5,
        "stop_ghz": 3.7,
        "points": 1101,
        "evidence": "implementation_assumption",
    },
}


def paper_parameters(case: str) -> dict[str, dict[str, Any]]:
    """Return only values explicitly stated in the paper for ``case``."""
    _validate_case(case)
    return deepcopy({**_SHARED_PAPER_PARAMETERS, **_CASE_PAPER_PARAMETERS[case]})


def engineering_assumptions() -> dict[str, Any]:
    """Return the assumptions required for a reproducible HFSS translation."""
    return deepcopy(_ENGINEERING_ASSUMPTIONS)


def geometry_coordinates(case: str) -> dict[str, list[float]]:
    """Resolve the documented coordinate convention into numeric boxes in mm."""
    parameters = paper_parameters(case)
    value = lambda name: float(parameters[name]["value"])
    patch_width = value("patch_width")
    patch_length = value("patch_length")
    feed_width = value("feed_width")
    feed_length = value("feed_length")
    inset_width = value("inset_width")
    inset_length = value("inset_length")
    substrate_height = value("substrate_thickness")
    conductor_thickness = float(_ENGINEERING_ASSUMPTIONS["conductor_thickness"]["value"])

    coordinates = {
        "substrate_origin": [-40.0, 0.0, 0.0],
        "substrate_size": [80.0, 80.0, substrate_height],
        "ground_origin": [-40.0, 0.0, -conductor_thickness],
        "ground_size": [80.0, 80.0, conductor_thickness],
        "patch_origin": [-patch_width / 2.0, feed_length, substrate_height],
        "patch_size": [patch_width, patch_length, conductor_thickness],
        "inset_origin": [-inset_width / 2.0, feed_length, substrate_height],
        "inset_size": [inset_width, inset_length, conductor_thickness],
        "feed_origin": [-feed_width / 2.0, 0.0, substrate_height],
        "feed_size": [feed_width, feed_length + inset_length, conductor_thickness],
    }
    if case == "scaled_slot_loaded":
        slot_width = value("slot_width")
        slot_length = value("slot_length")
        edge_offset = value("slot_to_radiating_edge")
        patch_top = feed_length + patch_length
        coordinates.update(
            slot_origin=[
                -slot_length / 2.0,
                patch_top - edge_offset - slot_width,
                substrate_height,
            ],
            slot_size=[slot_length, slot_width, conductor_thickness],
        )
    return coordinates


def build_reference(hfss: Any, case: str) -> Any:
    """Build one case in the current, deliberately empty HFSS Modal design."""
    _validate_case(case)
    if list(hfss.modeler.object_names):
        raise RuntimeError("Yeo reference design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("Yeo reference design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    parameters = paper_parameters(case)
    coordinates = geometry_coordinates(case)
    substrate_material = _ensure_rf35(hfss)

    substrate = hfss.modeler.create_box(
        coordinates["substrate_origin"],
        coordinates["substrate_size"],
        name="RF35_Substrate",
        material=substrate_material,
    )
    ground = hfss.modeler.create_box(
        coordinates["ground_origin"],
        coordinates["ground_size"],
        name="Ground",
        material="copper",
    )
    patch = hfss.modeler.create_box(
        coordinates["patch_origin"],
        coordinates["patch_size"],
        name="PatchFeed",
        material="copper",
    )
    inset = hfss.modeler.create_box(
        coordinates["inset_origin"],
        coordinates["inset_size"],
        name="InsetCutTool",
        material="vacuum",
    )
    if not hfss.modeler.subtract(patch, inset, keep_originals=False):
        raise RuntimeError("HFSS failed to subtract the inset from the patch")
    feed = hfss.modeler.create_box(
        coordinates["feed_origin"],
        coordinates["feed_size"],
        name="FeedLine",
        material="copper",
    )
    united = hfss.modeler.unite([patch, feed])
    if not united:
        raise RuntimeError("HFSS failed to unite the patch and feed line")

    if case == "scaled_slot_loaded":
        slot = hfss.modeler.create_box(
            coordinates["slot_origin"],
            coordinates["slot_size"],
            name="RadiatingEdgeSlotTool",
            material="vacuum",
        )
        if not hfss.modeler.subtract("PatchFeed", slot, keep_originals=False):
            raise RuntimeError("HFSS failed to subtract the radiating-edge slot")

    padding = float(_ENGINEERING_ASSUMPTIONS["radiation_padding"]["value"])
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
        raise RuntimeError("HFSS radiation region does not have the expected six faces")
    if not hfss.assign_radiation_boundary_to_faces(radiation_faces, name="Radiation"):
        raise RuntimeError("HFSS failed to assign the radiation boundary")

    substrate_height = float(parameters["substrate_thickness"]["value"])
    conductor_thickness = float(_ENGINEERING_ASSUMPTIONS["conductor_thickness"]["value"])
    port = hfss.wave_port(
        port_face.id,
        integration_line=[
            [0.0, 0.0, 0.0],
            [0.0, 0.0, substrate_height + conductor_thickness],
        ],
        modes=1,
        impedance=50,
        name="WavePort1",
        renormalize=True,
    )
    if not port:
        raise RuntimeError("HFSS failed to create the microstrip wave port")

    setup = hfss.create_setup(
        name="Setup1",
        setup_type="HFSSDriven",
        Frequency="2.5GHz",
        MaximumPasses=12,
        MinimumPasses=2,
        MaxDeltaS=0.02,
    )
    if not setup:
        raise RuntimeError("HFSS failed to create Setup1")
    sweep = setup.create_frequency_sweep(
        unit="GHz",
        name="Sweep1",
        start_frequency=1.5,
        stop_frequency=3.7,
        num_of_freq_points=1101,
        save_fields=False,
        save_rad_fields=False,
        sweep_type="Interpolating",
        interpolation_tol=0.25,
        interpolation_max_solutions=350,
    )
    if not sweep:
        raise RuntimeError("HFSS failed to create Sweep1")
    return hfss


def _ensure_rf35(hfss: Any) -> str:
    name = "Yeo2019_RF35_er3p5_tand0p0018"
    material = hfss.materials.exists_material(name)
    if material:
        actual_er = _material_value(material.permittivity)
        actual_tangent = _material_value(material.dielectric_loss_tangent)
        if abs(actual_er - 3.5) > 1e-12 or abs(actual_tangent - 0.0018) > 1e-12:
            raise RuntimeError(
                f"project material {name!r} exists with incompatible dielectric properties"
            )
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the RF-35 material")
    material.permittivity = 3.5
    material.dielectric_loss_tangent = 0.0018
    return name


def _material_value(property_object: Any) -> float:
    raw = getattr(property_object, "evaluated_value", None)
    if raw is None:
        raw = getattr(property_object, "value", property_object)
    return float(raw)


def _validate_case(case: str) -> None:
    if case not in DESIGN_NAMES:
        choices = ", ".join(sorted(DESIGN_NAMES))
        raise ValueError(f"unknown Yeo case {case!r}; choose one of: {choices}")
