"""Offline PyAEDT geometry draft for LEAM Case 2, Fig. 5.

Only PatchW, PatchL, SlotW, and SlotL are stated by the paper.  Every other
initial value is isolated below as an explicit engineering assumption so that
comparison feedback can replace it without contaminating paper evidence.
"""

CASE_ID = "case2_slotted_patch"
FIGURE = "Fig. 5"
GENERATION_REQUIRES_AEDT = False
EXECUTION_REQUIRES_AEDT = True

PARAMETERS = (
    ("PatchW", "38mm"),
    ("PatchL", "28mm"),
    ("SlotW", "2mm"),
    ("SlotL", "10mm"),
    ("SubW", "50mm"),
    ("SubL", "50mm"),
    ("SubT", "1.6mm"),
    ("CuT", "0.035mm"),
    ("FeedW", "3mm"),
    ("FeedL", "11mm"),
    ("SlotYOffset", "14mm"),
)

ASSUMPTIONS = (
    "SubW=50 mm, SubL=50 mm, and SubT=1.6 mm are initial reconstruction values.",
    "The substrate is FR-4 with epsilon_r=4.4 and tan_delta=0.02.",
    "CuT=0.035 mm, FeedW=3 mm, and FeedL=11 mm are initial values.",
    "The slot is centered laterally and its lower edge is 14 mm above the patch bottom.",
    "The ground is a full conductor sheet on the back of the substrate.",
)


def build(hfss):
    """Create the five paper solids in an existing HFSS design."""
    for name, value in PARAMETERS:
        hfss[name] = value

    fr4 = hfss.materials.add_material("LEAM_FR4")
    fr4.permittivity = 4.4
    fr4.dielectric_loss_tangent = 0.02

    hfss.modeler.create_box(
        ["0mm", "0mm", "0mm"],
        ["SubW", "SubL", "SubT"],
        name="substrate",
        material="LEAM_FR4",
    )
    hfss.modeler.create_box(
        ["(SubW-PatchW)/2", "FeedL", "SubT"],
        ["PatchW", "PatchL", "CuT"],
        name="patch",
        material="copper",
    )
    hfss.modeler.create_box(
        ["(SubW-SlotW)/2", "FeedL+SlotYOffset", "SubT"],
        ["SlotW", "SlotL", "CuT"],
        name="slot",
        material="vacuum",
    )
    hfss.modeler.create_box(
        ["(SubW-FeedW)/2", "0mm", "SubT"],
        ["FeedW", "FeedL", "CuT"],
        name="feedline",
        material="copper",
    )
    hfss.modeler.create_box(
        ["0mm", "0mm", "-CuT"],
        ["SubW", "SubL", "CuT"],
        name="ground",
        material="copper",
    )
    hfss.modeler.unite(["patch", "feedline"])
    hfss.modeler.subtract("patch", "slot", keep_originals=False)
    hfss.modeler.fit_all()
    return hfss
