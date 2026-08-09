"""Offline PyAEDT geometry for LEAM Case 3, Fig. 7.

The geometry follows the reviewed Case 3 artifact.  Importing this file does
not start AEDT; ``build(hfss)`` only mutates an HFSS object supplied by the user.
"""

CASE_ID = "case3_monopole"
FIGURE = "Fig. 7"
SOURCE_JOB_ID = "mdl-c6cb8833a1e8"
GENERATION_REQUIRES_AEDT = False
EXECUTION_REQUIRES_AEDT = True

PARAMETERS = (
    ("DPR", "6.58mm"),
    ("SW", "13.43mm"),
    ("SLT", "1.0mm"),
    ("SLV", "7.9mm"),
    ("SLH", "7.9mm"),
    ("ML", "25.08mm"),
    ("RPL", "6.67mm"),
    ("MW", "1.2mm"),
    ("MG", "0.3mm"),
    ("SubT", "0.8mm"),
    ("eps_r", "4.4"),
    ("tan_delta", "0.02"),
    ("CuT", "0.035mm"),
    ("SL", "ML+DPR+0.2mm"),
    ("RPW", "(SW-MW-2*MG)/2"),
    ("ground_length", "ML-RPL"),
)

ASSUMPTIONS = (
    "CuT=0.035 mm because conductor thickness is not stated by the paper.",
)


def build(hfss):
    """Create the reviewed seven-solid topology in an existing HFSS design."""
    for name, value in PARAMETERS:
        hfss[name] = value

    fr4 = hfss.materials.add_material("LEAM_FR4")
    fr4.permittivity = 4.4
    fr4.dielectric_loss_tangent = 0.02

    hfss.modeler.create_box(
        ["0mm", "0mm", "0mm"],
        ["SW", "SL", "SubT"],
        name="substrate",
        material="LEAM_FR4",
    )
    hfss.modeler.create_cylinder(
        orientation="Z",
        origin=["SW/2", "ML", "SubT"],
        radius="DPR",
        height="CuT",
        name="radiator",
        material="copper",
    )
    hfss.modeler.create_box(
        ["(SW-MW)/2", "0mm", "SubT"],
        ["MW", "ML", "CuT"],
        name="feedline",
        material="copper",
    )
    hfss.modeler.create_box(
        ["0mm", "0mm", "SubT"],
        ["RPW", "ground_length", "CuT"],
        name="left_ground",
        material="copper",
    )
    hfss.modeler.create_box(
        ["SW-RPW", "0mm", "SubT"],
        ["RPW", "ground_length", "CuT"],
        name="right_ground",
        material="copper",
    )
    hfss.modeler.create_box(
        ["(SW-SLH)/2", "ML-SLT/2", "SubT"],
        ["SLH", "SLT", "CuT"],
        name="horizontal_slot",
        material="vacuum",
    )
    hfss.modeler.create_box(
        ["(SW-SLT)/2", "ML-SLV/2", "SubT"],
        ["SLT", "SLV", "CuT"],
        name="vertical_slot",
        material="vacuum",
    )
    hfss.modeler.unite(["radiator", "feedline"])
    hfss.modeler.unite(["horizontal_slot", "vertical_slot"])
    hfss.modeler.subtract("radiator", "horizontal_slot", keep_originals=False)
    hfss.modeler.fit_all()
    return hfss
