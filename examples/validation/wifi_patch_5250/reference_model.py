"""Frozen HFSS translation of the El-Gendy 5.25 GHz single patch element.

Paper-explicit values and cross-solver engineering assumptions are intentionally
separate. Importing this module does not import PyAEDT or start AEDT.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DESIGN_NAME = "ElGendySinglePatch5250_EdgeReferencedXp"

_PAPER_PARAMETERS: dict[str, dict[str, Any]] = {
    "center_frequency": {"value": 5.25, "unit": "GHz", "evidence": "paper"},
    "wifi_band_start": {"value": 5.15, "unit": "GHz", "evidence": "paper"},
    "wifi_band_stop": {"value": 5.35, "unit": "GHz", "evidence": "paper"},
    "substrate_relative_permittivity": {"value": 4.5, "unit": "ratio", "evidence": "paper"},
    "substrate_loss_tangent": {"value": 0.025, "unit": "ratio", "evidence": "paper"},
    "substrate_thickness_h": {"value": 1.5, "unit": "mm", "evidence": "paper"},
    "substrate_length_Lg": {"value": 25.92, "unit": "mm", "evidence": "paper"},
    "substrate_width_Wg": {"value": 34.44, "unit": "mm", "evidence": "paper"},
    "reflector_width_WR": {"value": 34.44, "unit": "mm", "evidence": "paper"},
    "reflector_extension_LR": {"value": 20.0, "unit": "mm", "evidence": "paper"},
    "patch_length_Lp": {"value": 12.55, "unit": "mm", "evidence": "paper"},
    "patch_width_Wp": {"value": 17.22, "unit": "mm", "evidence": "paper"},
    "feed_offset_Xp": {"value": 2.89, "unit": "mm", "evidence": "paper"},
}

_ENGINEERING_ASSUMPTIONS: dict[str, Any] = {
    "conductor_model": "zero_thickness_pec_sheets",
    "probe_inner_radius_mm": 0.5,
    "probe_outer_radius_mm": 1.225,
    "feed_length_mm": 7.35,
    "coax_dielectric": "vacuum",
    "port_impedance_ohm": 50.0,
    "radiation_padding_mm": 15.0,
    "solution_type": "Terminal",
    "sweep_start_ghz": 5.0,
    "sweep_stop_ghz": 5.5,
    "sweep_points": 501,
}


def paper_parameters() -> dict[str, dict[str, Any]]:
    """Return only quantities explicitly stated in the paper."""
    return deepcopy(_PAPER_PARAMETERS)


def engineering_assumptions() -> dict[str, Any]:
    """Return the frozen assumptions required to translate CST to HFSS."""
    return deepcopy(_ENGINEERING_ASSUMPTIONS)


def geometry_coordinates(assumptions: dict[str, Any] | None = None) -> dict[str, list[float]]:
    """Resolve Figure 2 and the documented coordinate convention in millimetres."""
    resolved = _resolved_assumptions(assumptions)
    value = lambda name: float(_PAPER_PARAMETERS[name]["value"])
    lg = value("substrate_length_Lg")
    wg = value("substrate_width_Wg")
    wr = value("reflector_width_WR")
    lr = value("reflector_extension_LR")
    lp = value("patch_length_Lp")
    wp = value("patch_width_Wp")
    h = value("substrate_thickness_h")
    xp = value("feed_offset_Xp")
    feed_length = float(resolved["feed_length_mm"])
    conductor_thickness = (
        0.035 if resolved["conductor_model"] == "finite_copper_0p035mm" else 0.0
    )
    return {
        "substrate_origin": [-lg / 2.0, -wg / 2.0, 0.0],
        "substrate_size": [lg, wg, h],
        "reflector_origin": [-lg / 2.0 - lr, -wr / 2.0, 0.0],
        "reflector_size": [lg + 2.0 * lr, wr],
        "patch_origin": [-lp / 2.0, -wp / 2.0, h],
        "patch_size": [lp, wp],
        # Figure 2 and Equation (3) define Xp from a radiating patch edge.
        # The lower/negative-X edge is used as the deterministic convention.
        "probe_origin": [-lp / 2.0 + xp, 0.0, 0.0],
        "probe_size": [float(resolved["probe_inner_radius_mm"]), h + conductor_thickness],
        "feed_origin": [-lp / 2.0 + xp, 0.0, 0.0],
        "feed_size": [float(resolved["probe_inner_radius_mm"]), -feed_length],
        "outer_origin": [-lp / 2.0 + xp, 0.0, 0.0],
        "outer_size": [float(resolved["probe_outer_radius_mm"]), -feed_length],
    }


def build_reference(
    hfss: Any, assumptions: dict[str, Any] | None = None
) -> Any:
    """Build the paper's single element in a deliberately empty Terminal design."""
    if list(hfss.modeler.object_names):
        raise RuntimeError("El-Gendy reference design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("El-Gendy reference design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    resolved = _resolved_assumptions(assumptions)
    coords = geometry_coordinates(resolved)
    material_name = _ensure_fr4(hfss)
    substrate = hfss.modeler.create_box(
        coords["substrate_origin"], coords["substrate_size"],
        name="Substrate", material=material_name,
    )
    finite_copper = resolved["conductor_model"] == "finite_copper_0p035mm"
    if finite_copper:
        thickness = 0.035
        reflector = hfss.modeler.create_box(
            [*coords["reflector_origin"][:2], -thickness],
            [*coords["reflector_size"], thickness],
            name="Reflector",
            material="copper",
        )
        patch = hfss.modeler.create_box(
            coords["patch_origin"],
            [*coords["patch_size"], thickness],
            name="Patch",
            material="copper",
        )
    else:
        reflector = hfss.modeler.create_rectangle(
            "XY", coords["reflector_origin"], coords["reflector_size"],
            name="Reflector", material="pec",
        )
        patch = hfss.modeler.create_rectangle(
            "XY", coords["patch_origin"], coords["patch_size"],
            name="Patch", material="pec",
        )
    if not substrate or not reflector or not patch:
        raise RuntimeError("HFSS failed to create the paper-explicit geometry")

    # Both the reflector and tool are XY sheets. This avoids relying on AEDT's
    # cross-dimensional sheet-minus-solid Boolean behavior for a zero-thickness
    # conductor; the coax dielectric solid is created separately below.
    if finite_copper:
        aperture = hfss.modeler.create_cylinder(
            "Z",
            [*coords["outer_origin"][:2], -0.035],
            coords["outer_size"][0],
            0.035,
            name="FeedApertureTool",
            material="vacuum",
        )
    else:
        aperture = hfss.modeler.create_circle(
            "XY",
            coords["outer_origin"],
            coords["outer_size"][0],
            name="FeedApertureTool",
            material="vacuum",
        )
    if not aperture or not hfss.modeler.subtract(reflector, aperture, keep_originals=False):
        raise RuntimeError("HFSS failed to cut the feed aperture in the reflector")

    probe = hfss.modeler.create_cylinder(
        "Z", coords["probe_origin"], coords["probe_size"][0], coords["probe_size"][1],
        name="Probe", material="copper",
    )
    feed_wire = hfss.modeler.create_cylinder(
        "Z", coords["feed_origin"], coords["feed_size"][0], coords["feed_size"][1],
        name="ProbeFeedWire", material="copper",
    )
    coax_material = _ensure_coax_dielectric(hfss, resolved["coax_dielectric"])
    feed_outer = hfss.modeler.create_cylinder(
        "Z", coords["outer_origin"], coords["outer_size"][0], coords["outer_size"][1],
        name="ProbeFeedOuter", material=coax_material,
    )
    if not probe or not feed_wire or not feed_outer:
        raise RuntimeError("HFSS failed to create the assumed coaxial feed")

    # The paper specifies a probe-fed topology but not the CAD overlap policy.
    # Remove the probe volume from the dielectric so HFSS never has two solved
    # volumes occupying the same cells.  With finite copper, merge the volume
    # shared by the probe and patch into one electrically continuous conductor.
    if not hfss.modeler.subtract(substrate, probe, keep_originals=True):
        raise RuntimeError("HFSS failed to cut the probe bore through the substrate")
    if finite_copper and not hfss.modeler.unite([patch, probe]):
        raise RuntimeError("HFSS failed to unite the finite patch and probe conductor")

    if not finite_copper:
        if not hfss.assign_perfecte_to_sheets(reflector, "ReflectorPEC"):
            raise RuntimeError("HFSS failed to assign the reflector Perfect E boundary")
        if not hfss.assign_perfecte_to_sheets(patch, "PatchPEC"):
            raise RuntimeError("HFSS failed to assign the patch Perfect E boundary")
    largest_outer_face = max(feed_outer.faces, key=lambda face: float(face.area))
    if not hfss.assign_perfecte_to_sheets(largest_outer_face.id, "ProbePEC"):
        raise RuntimeError("HFSS failed to assign the coax outer Perfect E boundary")
    port = hfss.wave_port(
        feed_outer.bottom_face_z,
        reference=feed_outer.name,
        create_pec_cap=True,
        impedance=float(resolved["port_impedance_ohm"]),
        name="ProbePort",
    )
    if not port:
        raise RuntimeError("HFSS failed to create the terminal wave port")

    padding = float(resolved["radiation_padding_mm"])
    region = hfss.modeler.create_region(
        [padding] * 6, pad_type="Absolute Offset", name="Region"
    )
    if not region or not hfss.assign_radiation_boundary_to_objects(region, name="Radiation"):
        raise RuntimeError("HFSS failed to create the open radiation region")

    setup = hfss.create_setup(
        name="Setup1", setup_type="HFSSDriven", Frequency="5.25GHz",
        MaximumPasses=12, MinimumPasses=2, MaxDeltaS=0.02,
    )
    if not setup:
        raise RuntimeError("HFSS failed to create Setup1")
    sweep = setup.create_frequency_sweep(
        unit="GHz", name="Sweep1", start_frequency=5.0, stop_frequency=5.5,
        num_of_freq_points=501, save_fields=False, save_rad_fields=False,
        sweep_type="Interpolating", interpolation_tol=0.25,
        interpolation_max_solutions=250,
    )
    if not sweep:
        raise RuntimeError("HFSS failed to create Sweep1")
    return hfss


def _ensure_fr4(hfss: Any) -> str:
    name = "FR4_ElGendy_2022"
    material = hfss.materials.exists_material(name)
    if material:
        actual_er = _material_value(material.permittivity)
        actual_tangent = _material_value(material.dielectric_loss_tangent)
        if abs(actual_er - 4.5) > 1e-12 or abs(actual_tangent - 0.025) > 1e-12:
            raise RuntimeError(f"project material {name!r} has incompatible properties")
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the paper FR-4 material")
    material.permittivity = 4.5
    material.dielectric_loss_tangent = 0.025
    return name


def _ensure_coax_dielectric(hfss: Any, model: str) -> str:
    if model == "vacuum":
        return "vacuum"
    if model != "ptfe_er2p1":
        raise ValueError(f"unsupported coax dielectric model: {model}")
    name = "PTFE_Assumption_er2p1"
    material = hfss.materials.exists_material(name)
    if not material:
        material = hfss.materials.add_material(name)
        if not material:
            raise RuntimeError("HFSS failed to create the assumed PTFE material")
        material.permittivity = 2.1
        material.dielectric_loss_tangent = 0.0002
    return name


def _resolved_assumptions(overrides: dict[str, Any] | None) -> dict[str, Any]:
    resolved = deepcopy(_ENGINEERING_ASSUMPTIONS)
    if overrides is not None:
        if not isinstance(overrides, dict) or set(overrides) != set(resolved):
            raise ValueError("assumption override must preserve the exact frozen assumption keys")
        resolved.update(deepcopy(overrides))
    if resolved["conductor_model"] not in {
        "zero_thickness_pec_sheets",
        "finite_copper_0p035mm",
    }:
        raise ValueError("unsupported conductor_model assumption")
    if resolved["coax_dielectric"] not in {"vacuum", "ptfe_er2p1"}:
        raise ValueError("unsupported coax_dielectric assumption")
    inner = float(resolved["probe_inner_radius_mm"])
    outer = float(resolved["probe_outer_radius_mm"])
    if not 0.0 < inner < outer:
        raise ValueError("probe radii must satisfy 0 < inner < outer")
    if float(resolved["feed_length_mm"]) <= 0.0:
        raise ValueError("feed_length_mm must be positive")
    if float(resolved["radiation_padding_mm"]) <= 0.0:
        raise ValueError("radiation_padding_mm must be positive")
    if float(resolved["port_impedance_ohm"]) != 50.0:
        raise ValueError("the current study keeps port_impedance_ohm frozen at 50")
    return resolved


def _material_value(property_object: Any) -> float:
    raw = getattr(property_object, "evaluated_value", None)
    if raw is None:
        raw = getattr(property_object, "value", property_object)
    return float(raw)
