"""Offline PyAEDT geometry for the L-slot patch shown in LEAM Fig. 3.

Importing this file does not connect to or start AEDT.  Call ``build(hfss)``
only after the user has deliberately connected an HFSS object.
"""

CASE_ID = "demo_l_slot"
FIGURE = "Fig. 3"
GENERATION_REQUIRES_AEDT = False
EXECUTION_REQUIRES_AEDT = True

PARAMETERS = (
    ("PatchW", "10mm"),
    ("PatchL", "8mm"),
    ("Slot1L", "4mm"),
    ("Slot1W", "1mm"),
    ("Slot2L", "6mm"),
    ("Slot2W", "1mm"),
    ("SlotOffset", "2mm"),
    ("CuT", "0.035mm"),
)

ASSUMPTIONS = ()


def build(hfss):
    """Create only the conductor and L-shaped subtraction in an existing HFSS design."""
    for name, value in PARAMETERS:
        hfss[name] = value

    hfss.modeler.create_box(
        ["0mm", "0mm", "0mm"],
        ["PatchW", "PatchL", "CuT"],
        name="patch",
        material="copper",
    )
    hfss.modeler.create_box(
        ["SlotOffset", "SlotOffset", "0mm"],
        ["Slot2L", "Slot2W", "CuT"],
        name="slot_horizontal",
        material="vacuum",
    )
    hfss.modeler.create_box(
        ["SlotOffset", "SlotOffset", "0mm"],
        ["Slot1W", "Slot1L", "CuT"],
        name="slot_vertical",
        material="vacuum",
    )
    hfss.modeler.unite(["slot_horizontal", "slot_vertical"])
    hfss.modeler.subtract("patch", "slot_horizontal", keep_originals=False)
    hfss.modeler.fit_all()
    return hfss
