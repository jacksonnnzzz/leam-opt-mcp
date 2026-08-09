"""Offline PyAEDT geometry draft for LEAM Case 1, Fig. 4 (Vivaldi).

The paper does not publish the 20 updated spline X coordinates.  The X1..X20
defaults below are therefore an explicit visual-fit seed and are meant to be
edited after comparison.  No AEDT session is started when this file is imported.
"""

CASE_ID = "case1_vivaldi"
FIGURE = "Fig. 4"
GENERATION_REQUIRES_AEDT = False
EXECUTION_REQUIRES_AEDT = True

PARAMETERS = (
    ("SubT", "0.813mm"),
    ("CuT", "0.035mm"),
    ("L1", "7mm"),
    ("W1", "2mm"),
    ("L2", "8mm"),
    ("W2", "1mm"),
    ("PF", "5mm"),
    ("Gap1", "1mm"),
    ("R1", "2mm"),
    ("R2", "2mm"),
    ("X1", "0.250mm"),
    ("X2", "2.278mm"),
    ("X3", "3.433mm"),
    ("X4", "4.392mm"),
    ("X5", "5.244mm"),
    ("X6", "6.024mm"),
    ("X7", "6.750mm"),
    ("X8", "7.435mm"),
    ("X9", "8.086mm"),
    ("X10", "8.710mm"),
    ("X11", "9.310mm"),
    ("X12", "9.889mm"),
    ("X13", "10.450mm"),
    ("X14", "10.994mm"),
    ("X15", "11.524mm"),
    ("X16", "12.042mm"),
    ("X17", "12.547mm"),
    ("X18", "13.041mm"),
    ("X19", "13.525mm"),
    ("X20", "14.000mm"),
)

ASSUMPTIONS = (
    "X1..X20 are a monotonic visual fit because their updated values are absent from the paper.",
    "PF maps to the figure's gap2=5 mm.",
    "RO4003C dielectric values use nominal epsilon_r=3.55 and tan_delta=0.0027.",
    "The 20-point taper is represented by short straight segments for robust PyAEDT portability.",
)


def build(hfss):
    """Create the six solids and front slot tool in an existing HFSS design."""
    for name, value in PARAMETERS:
        hfss[name] = value

    ro4003c = hfss.materials.add_material("LEAM_RO4003C")
    ro4003c.permittivity = 3.55
    ro4003c.dielectric_loss_tangent = 0.0027

    hfss.modeler.create_box(
        ["0mm", "0mm", "0mm"],
        ["30mm", "20mm", "SubT"],
        name="substrate",
        material="LEAM_RO4003C",
    )
    hfss.modeler.create_box(
        ["0mm", "0mm", "SubT"],
        ["30mm", "20mm", "CuT"],
        name="front_patch",
        material="copper",
    )

    left_points = [
        ["X1", "20mm", "SubT"], ["X2", "19mm", "SubT"],
        ["X3", "18mm", "SubT"], ["X4", "17mm", "SubT"],
        ["X5", "16mm", "SubT"], ["X6", "15mm", "SubT"],
        ["X7", "14mm", "SubT"], ["X8", "13mm", "SubT"],
        ["X9", "12mm", "SubT"], ["X10", "11mm", "SubT"],
        ["X11", "10mm", "SubT"], ["X12", "9mm", "SubT"],
        ["X13", "8mm", "SubT"], ["X14", "7mm", "SubT"],
        ["X15", "6mm", "SubT"], ["X16", "5mm", "SubT"],
        ["X17", "4mm", "SubT"], ["X18", "3mm", "SubT"],
        ["X19", "2mm", "SubT"], ["X20", "1mm", "SubT"],
    ]
    right_points_bottom_to_top = [
        ["30mm-X20", "1mm", "SubT"], ["30mm-X19", "2mm", "SubT"],
        ["30mm-X18", "3mm", "SubT"], ["30mm-X17", "4mm", "SubT"],
        ["30mm-X16", "5mm", "SubT"], ["30mm-X15", "6mm", "SubT"],
        ["30mm-X14", "7mm", "SubT"], ["30mm-X13", "8mm", "SubT"],
        ["30mm-X12", "9mm", "SubT"], ["30mm-X11", "10mm", "SubT"],
        ["30mm-X10", "11mm", "SubT"], ["30mm-X9", "12mm", "SubT"],
        ["30mm-X8", "13mm", "SubT"], ["30mm-X7", "14mm", "SubT"],
        ["30mm-X6", "15mm", "SubT"], ["30mm-X5", "16mm", "SubT"],
        ["30mm-X4", "17mm", "SubT"], ["30mm-X3", "18mm", "SubT"],
        ["30mm-X2", "19mm", "SubT"], ["30mm-X1", "20mm", "SubT"],
    ]
    taper_points = left_points + right_points_bottom_to_top
    hfss.modeler.create_polyline(
        taper_points,
        cover_surface=True,
        close_surface=True,
        name="taper_slot_tool",
        material="vacuum",
    )
    hfss.modeler.thicken_sheet("taper_slot_tool", "CuT")
    hfss.modeler.create_cylinder(
        orientation="Z",
        origin=["15mm", "Gap1+R1", "SubT"],
        radius="R1",
        height="CuT",
        name="front_circle_tool",
        material="vacuum",
    )
    hfss.modeler.subtract("front_patch", "taper_slot_tool", keep_originals=False)
    hfss.modeler.subtract("front_patch", "front_circle_tool", keep_originals=False)

    hfss.modeler.create_box(
        ["30mm-L1", "PF", "-CuT"],
        ["L1", "W1", "CuT"],
        name="back_rectangle_1",
        material="copper",
    )
    hfss.modeler.create_box(
        ["30mm-L1-L2", "PF+0.5*(W1-W2)", "-CuT"],
        ["L2", "W2", "CuT"],
        name="back_rectangle_2",
        material="copper",
    )
    hfss.modeler.create_cylinder(
        orientation="Z",
        origin=["30mm-L1-L2", "PF+0.5*W1", "-CuT"],
        radius="R2",
        height="CuT",
        name="back_cylinder",
        material="copper",
    )
    hfss.modeler.fit_all()
    return hfss
