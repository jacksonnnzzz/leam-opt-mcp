"""Controlled HFSS variants for unresolved Yeo (2019) implementation choices.

Every paper-explicit dimension and material value is held fixed.  Only the
conductor representation and excitation type, both unresolved by the paper,
are varied.  Importing this module does not import PyAEDT or start AEDT.
"""

from __future__ import annotations

from typing import Any

from reference_model import engineering_assumptions, geometry_coordinates, paper_parameters


def _variant_set(design_prefix: str) -> dict[str, dict[str, str]]:
    return {
        "solid_lumped": {
            "design": f"{design_prefix}_SolidLumped",
            "conductor_model": "solid_copper_0p035mm",
            "port_model": "internal_lumped_port",
        },
        "pec_wave": {
            "design": f"{design_prefix}_PECWave",
            "conductor_model": "zero_thickness_pec",
            "port_model": "full_region_face_wave_port",
        },
        "pec_lumped": {
            "design": f"{design_prefix}_PECLumped",
            "conductor_model": "zero_thickness_pec",
            "port_model": "internal_lumped_port",
        },
    }


VARIANTS_BY_CASE = {
    "conventional": _variant_set("YeoConventionalPatch"),
    "scaled_slot_loaded": _variant_set("YeoScaledSlotLoadedPatch"),
}
# Backwards-compatible conventional alias used by existing audit code.
VARIANTS = VARIANTS_BY_CASE["conventional"]


def build_variant(hfss: Any, variant: str, case: str = "conventional") -> Any:
    if case not in VARIANTS_BY_CASE:
        raise ValueError(f"unknown Yeo assumption-study case: {case}")
    variants = VARIANTS_BY_CASE[case]
    if variant not in variants:
        raise ValueError(f"unknown Yeo assumption variant: {variant}")
    if list(hfss.modeler.object_names):
        raise RuntimeError("Yeo assumption-study design must be empty before construction")
    if list(getattr(hfss, "setup_names", [])):
        raise RuntimeError("Yeo assumption-study design must not contain analysis setups")

    hfss.modeler.model_units = "mm"
    coordinates = geometry_coordinates(case)
    parameters = paper_parameters(case)
    assumptions = engineering_assumptions()
    material_name = _ensure_rf35(hfss)
    substrate = hfss.modeler.create_box(
        coordinates["substrate_origin"],
        coordinates["substrate_size"],
        name="RF35_Substrate",
        material=material_name,
    )
    if not substrate:
        raise RuntimeError("HFSS failed to create the RF-35 substrate")

    configuration = variants[variant]
    if configuration["conductor_model"] == "zero_thickness_pec":
        ground, patch = _build_sheet_conductors(hfss, coordinates)
    else:
        ground, patch = _build_solid_conductors(hfss, coordinates)

    substrate_height = float(parameters["substrate_thickness"]["value"])
    if configuration["port_model"] == "internal_lumped_port":
        _assign_lumped_port(hfss, coordinates, substrate_height)
        padding = float(assumptions["radiation_padding"]["value"])
        region = hfss.modeler.create_region(
            [padding] * 6, pad_type="Absolute Offset", name="Region"
        )
        if not region or not hfss.assign_radiation_boundary_to_objects(
            region, name="Radiation"
        ):
            raise RuntimeError("HFSS failed to create the lumped-port radiation region")
    else:
        _assign_wave_port_and_region(hfss, substrate_height, assumptions)

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


def _build_solid_conductors(hfss: Any, coordinates: dict[str, list[float]]) -> tuple[Any, Any]:
    ground = hfss.modeler.create_box(
        coordinates["ground_origin"], coordinates["ground_size"],
        name="Ground", material="copper",
    )
    patch = hfss.modeler.create_box(
        coordinates["patch_origin"], coordinates["patch_size"],
        name="PatchFeed", material="copper",
    )
    inset = hfss.modeler.create_box(
        coordinates["inset_origin"], coordinates["inset_size"],
        name="InsetCutTool", material="vacuum",
    )
    if not ground or not patch or not inset:
        raise RuntimeError("HFSS failed to create solid conductors")
    if not hfss.modeler.subtract(patch, inset, keep_originals=False):
        raise RuntimeError("HFSS failed to subtract the solid inset")
    if "slot_origin" in coordinates:
        slot = hfss.modeler.create_box(
            coordinates["slot_origin"], coordinates["slot_size"],
            name="SlotCutTool", material="vacuum",
        )
        if not slot or not hfss.modeler.subtract(patch, slot, keep_originals=False):
            raise RuntimeError("HFSS failed to subtract the solid radiating-edge slot")
    feed = hfss.modeler.create_box(
        coordinates["feed_origin"], coordinates["feed_size"],
        name="FeedLine", material="copper",
    )
    if not feed or not hfss.modeler.unite([patch, feed]):
        raise RuntimeError("HFSS failed to unite the solid patch and feed")
    return ground, patch


def _build_sheet_conductors(hfss: Any, coordinates: dict[str, list[float]]) -> tuple[Any, Any]:
    ground = hfss.modeler.create_rectangle(
        "XY", [*coordinates["substrate_origin"][:2], 0.0],
        coordinates["substrate_size"][:2], name="Ground", material="pec",
    )
    patch = hfss.modeler.create_rectangle(
        "XY", coordinates["patch_origin"], coordinates["patch_size"][:2],
        name="PatchFeed", material="pec",
    )
    inset = hfss.modeler.create_rectangle(
        "XY", coordinates["inset_origin"], coordinates["inset_size"][:2],
        name="InsetCutTool", material="vacuum",
    )
    if not ground or not patch or not inset:
        raise RuntimeError("HFSS failed to create PEC sheet conductors")
    if not hfss.modeler.subtract(patch, inset, keep_originals=False):
        raise RuntimeError("HFSS failed to subtract the sheet inset")
    if "slot_origin" in coordinates:
        slot = hfss.modeler.create_rectangle(
            "XY", coordinates["slot_origin"], coordinates["slot_size"][:2],
            name="SlotCutTool", material="vacuum",
        )
        if not slot or not hfss.modeler.subtract(patch, slot, keep_originals=False):
            raise RuntimeError("HFSS failed to subtract the sheet radiating-edge slot")
    feed = hfss.modeler.create_rectangle(
        "XY", coordinates["feed_origin"], coordinates["feed_size"][:2],
        name="FeedLine", material="pec",
    )
    if not feed or not hfss.modeler.unite([patch, feed]):
        raise RuntimeError("HFSS failed to unite the sheet patch and feed")
    if not hfss.assign_perfecte_to_sheets(ground, "GroundPEC"):
        raise RuntimeError("HFSS failed to assign GroundPEC")
    if not hfss.assign_perfecte_to_sheets(patch, "PatchFeedPEC"):
        raise RuntimeError("HFSS failed to assign PatchFeedPEC")
    return ground, patch


def _assign_lumped_port(
    hfss: Any, coordinates: dict[str, list[float]], substrate_height: float
) -> None:
    feed_width = float(coordinates["feed_size"][0])
    port_sheet = hfss.modeler.create_rectangle(
        "XZ",
        [-feed_width / 2.0, 0.0, 0.0],
        # PyAEDT 0.26.3 maps XZ rectangle dimensions as [Z span, X span].
        [substrate_height, feed_width],
        name="LumpedPortSheet",
        material="vacuum",
    )
    if not port_sheet:
        raise RuntimeError("HFSS failed to create the local microstrip port sheet")
    port = hfss.lumped_port(
        port_sheet,
        integration_line=[[0.0, 0.0, 0.0], [0.0, 0.0, substrate_height]],
        impedance=50,
        name="LumpedPort1",
        renormalize=True,
    )
    if not port:
        raise RuntimeError("HFSS failed to assign the local microstrip lumped port")


def _assign_wave_port_and_region(
    hfss: Any, substrate_height: float, assumptions: dict[str, Any]
) -> None:
    padding = float(assumptions["radiation_padding"]["value"])
    region = hfss.modeler.create_region(
        [padding, padding, padding, 0.0, padding, padding],
        pad_type="Absolute Offset",
        name="Region",
    )
    if not region:
        raise RuntimeError("HFSS failed to create the wave-port radiation region")
    port_face = min(region.faces, key=lambda face: float(face.center[1]))
    radiation_faces = [face.id for face in region.faces if face.id != port_face.id]
    if len(radiation_faces) != 5 or not hfss.assign_radiation_boundary_to_faces(
        radiation_faces, name="Radiation"
    ):
        raise RuntimeError("HFSS failed to assign the five wave-port radiation faces")
    port = hfss.wave_port(
        port_face.id,
        integration_line=[[0.0, 0.0, 0.0], [0.0, 0.0, substrate_height]],
        modes=1,
        impedance=50,
        name="WavePort1",
        renormalize=True,
    )
    if not port:
        raise RuntimeError("HFSS failed to assign the full-region-face wave port")


def _ensure_rf35(hfss: Any) -> str:
    name = "Yeo2019_RF35_er3p5_tand0p0018"
    material = hfss.materials.exists_material(name)
    if material:
        return name
    material = hfss.materials.add_material(name)
    if not material:
        raise RuntimeError("HFSS failed to create the RF-35 material")
    material.permittivity = 3.5
    material.dielectric_loss_tangent = 0.0018
    return name
