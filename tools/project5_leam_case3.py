import ScriptEnv


ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
oDesktop.RestoreWindow()
oProject = oDesktop.GetActiveProject()
if oProject is None:
    raise Exception("Open the target AEDT project before running this script")

existing_designs = [str(name).split(";")[-1] for name in oProject.GetTopDesignList()]
base_design_name = "LEAM_Case3"
design_name = base_design_name
suffix = 2
while design_name in existing_designs:
    design_name = base_design_name + "_" + str(suffix)
    suffix += 1

oProject.InsertDesign("HFSS", design_name, "DrivenModal", "")
oDesign = oProject.SetActiveDesign(design_name)
oDesign.ChangeProperty(
    [
        "NAME:AllTabs",
        [
            "NAME:LocalVariableTab",
            ["NAME:PropServers", "LocalVariables"],
            [
                "NAME:NewProps",
                ["NAME:DPR", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "6.58mm"],
                ["NAME:SW", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "13.43mm"],
                ["NAME:SLT", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "1mm"],
                ["NAME:SLV", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "7.9mm"],
                ["NAME:SLH", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "7.9mm"],
                ["NAME:ML", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "25.08mm"],
                ["NAME:RPL", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "6.67mm"],
                ["NAME:MW", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "1.2mm"],
                ["NAME:MG", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "0.3mm"],
                ["NAME:SubT", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "0.8mm"],
                ["NAME:CuT", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "0.035mm"],
                ["NAME:eps_r", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "4.4"],
                ["NAME:tan_delta", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "0.02"],
                ["NAME:SL", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "ML+DPR+0.2mm"],
                ["NAME:RPW", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "(SW-MW-2*MG)/2"],
                ["NAME:ground_length", "PropType:=", "VariableProp", "UserDef:=", True, "Value:=", "ML-RPL"],
            ],
        ],
    ]
)

oDefinitionManager = oProject.GetDefinitionManager()
oDefinitionManager.AddMaterial(
    [
        "NAME:LEAM_FR4",
        "CoordinateSystemType:=", "Cartesian",
        "BulkOrSurfaceType:=", 1,
        ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
        "permittivity:=", "4.4",
        "dielectric_loss_tangent:=", "0.02",
    ]
)

oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.SetModelUnits(["NAME:Units Parameter", "Units:=", "mm", "Rescale:=", False])


def attributes(name, material, color, transparency, solve_inside):
    return [
        "NAME:Attributes",
        "Name:=", name,
        "Flags:=", "",
        "Color:=", color,
        "Transparency:=", transparency,
        "PartCoordinateSystem:=", "Global",
        "UDMId:=", "",
        "MaterialValue:=", '"' + material + '"',
        "SurfaceMaterialValue:=", '""',
        "SolveInside:=", solve_inside,
        "ShellElement:=", False,
        "ShellElementThickness:=", "0mm",
        "ReferenceTemperature:=", "20cel",
        "IsMaterialEditable:=", True,
        "UseMaterialAppearance:=", False,
        "IsLightweight:=", False,
    ]


def create_box(name, x, y, z, dx, dy, dz, material, color, transparency, solve_inside):
    oEditor.CreateBox(
        [
            "NAME:BoxParameters",
            "XPosition:=", x,
            "YPosition:=", y,
            "ZPosition:=", z,
            "XSize:=", dx,
            "YSize:=", dy,
            "ZSize:=", dz,
        ],
        attributes(name, material, color, transparency, solve_inside),
    )


create_box(
    "substrate", "0mm", "0mm", "0mm", "SW", "SL", "SubT",
    "LEAM_FR4", "(143 175 143)", 0.65, True,
)
oEditor.CreateCylinder(
    [
        "NAME:CylinderParameters",
        "XCenter:=", "SW/2",
        "YCenter:=", "ML",
        "ZCenter:=", "SubT",
        "Radius:=", "DPR",
        "Height:=", "CuT",
        "WhichAxis:=", "Z",
        "NumSides:=", "0",
    ],
    attributes("radiator", "copper", "(255 128 0)", 0, False),
)
create_box(
    "feedline", "(SW-MW)/2", "0mm", "SubT", "MW", "ML", "CuT",
    "copper", "(255 128 0)", 0, False,
)
create_box(
    "left_ground", "0mm", "0mm", "SubT", "RPW", "ground_length", "CuT",
    "copper", "(255 128 0)", 0, False,
)
create_box(
    "right_ground", "SW-RPW", "0mm", "SubT", "RPW", "ground_length", "CuT",
    "copper", "(255 128 0)", 0, False,
)
create_box(
    "horizontal_slot", "(SW-SLH)/2", "ML-SLT/2", "SubT", "SLH", "SLT", "CuT",
    "vacuum", "(128 128 255)", 0.8, True,
)
create_box(
    "vertical_slot", "(SW-SLT)/2", "ML-SLV/2", "SubT", "SLT", "SLV", "CuT",
    "vacuum", "(128 128 255)", 0.8, True,
)

oEditor.Unite(
    ["NAME:Selections", "Selections:=", "radiator,feedline"],
    ["NAME:UniteParameters", "KeepOriginals:=", False],
)
oEditor.Unite(
    ["NAME:Selections", "Selections:=", "horizontal_slot,vertical_slot"],
    ["NAME:UniteParameters", "KeepOriginals:=", False],
)
oEditor.Subtract(
    ["NAME:Selections", "Blank Parts:=", "radiator", "Tool Parts:=", "horizontal_slot"],
    ["NAME:SubtractParameters", "KeepOriginals:=", False],
)

final_objects = sorted([str(name) for name in oEditor.GetObjectsInGroup("Solids")])
expected_objects = ["left_ground", "radiator", "right_ground", "substrate"]
if final_objects != expected_objects:
    raise Exception("Unexpected final objects: " + ", ".join(final_objects))

oEditor.FitAll()
oDesktop.AddMessage(
    oProject.GetName(),
    design_name,
    0,
    "LEAM Case 3 geometry created; no setup or solve was added.",
)
