"""Build the LEAM paper Case 3 geometry in an existing PyAEDT ``hfss`` object.

This is a transparent regression fixture for the multimodal pipeline. It intentionally
builds geometry only; excitation, radiation boundary, setup, and sweep require a separate
review because the LEAM paper does not specify them for this case.
"""


PARAMETERS = {
    "DPR": "6.58mm",
    "SW": "13.43mm",
    "SLT": "1mm",
    "SLV": "7.9mm",
    "SLH": "7.9mm",
    "ML": "25.08mm",
    "RPL": "6.67mm",
    "MW": "1.2mm",
    "MG": "0.3mm",
    "SubT": "0.8mm",
    "CuT": "0.035mm",
}

DERIVED_PARAMETERS = {
    "SL": "ML+DPR+0.2mm",
    "RPW": "(SW-MW-2*MG)/2",
    "ground_length": "ML-RPL",
}


def build(hfss):
    for name, value in PARAMETERS.items():
        hfss[name] = value
    for name, expression in DERIVED_PARAMETERS.items():
        hfss[name] = expression

    leam_fr4 = hfss.materials.add_material("LEAM_FR4")
    leam_fr4.permittivity = 4.4
    leam_fr4.dielectric_loss_tangent = 0.02

    substrate = hfss.modeler.create_box(
        ["0mm", "0mm", "0mm"],
        ["SW", "SL", "SubT"],
        name="substrate",
        material="LEAM_FR4",
    )
    radiator = hfss.modeler.create_cylinder(
        orientation="Z",
        origin=["SW/2", "ML", "SubT"],
        radius="DPR",
        height="CuT",
        name="radiator",
        material="copper",
    )
    feedline = hfss.modeler.create_box(
        ["(SW-MW)/2", "0mm", "SubT"],
        ["MW", "ML", "CuT"],
        name="feedline",
        material="copper",
    )
    left_ground = hfss.modeler.create_box(
        ["0mm", "0mm", "SubT"],
        ["RPW", "ground_length", "CuT"],
        name="left_ground",
        material="copper",
    )
    right_ground = hfss.modeler.create_box(
        ["SW-RPW", "0mm", "SubT"],
        ["RPW", "ground_length", "CuT"],
        name="right_ground",
        material="copper",
    )
    horizontal_slot = hfss.modeler.create_box(
        ["(SW-SLH)/2", "ML-SLT/2", "SubT"],
        ["SLH", "SLT", "CuT"],
        name="horizontal_slot",
        material="vacuum",
    )
    vertical_slot = hfss.modeler.create_box(
        ["(SW-SLT)/2", "ML-SLV/2", "SubT"],
        ["SLT", "SLV", "CuT"],
        name="vertical_slot",
        material="vacuum",
    )

    hfss.modeler.unite([radiator, feedline])
    hfss.modeler.unite([horizontal_slot, vertical_slot])
    hfss.modeler.subtract(radiator, horizontal_slot, keep_originals=False)
    hfss.modeler.fit_all()

    return {
        "substrate": substrate,
        "radiator": radiator,
        "left_ground": left_ground,
        "right_ground": right_ground,
    }
