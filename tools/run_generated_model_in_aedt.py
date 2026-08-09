"""Native AEDT adapter for offline ``generated_model_vNNN.py`` artifacts.

This file is designed for AEDT's ``Tools > Run Script`` environment, including
the IronPython 2.7 interpreter used by AEDT 2025 R1.
It translates the small PyAEDT-style geometry interface used by the generated
artifacts into native ``oDesign``/``oEditor`` calls.  It does not save or solve.
"""

import __main__
import ast
import io
import os
import runpy


def _is_docstring_node(node):
    if not isinstance(node, ast.Expr):
        return False
    constant_type = getattr(ast, "Constant", None)
    if constant_type is not None:
        return isinstance(node.value, constant_type) and isinstance(node.value.value, str)
    return isinstance(node.value, ast.Str)


def _safe_model_namespace(model_file):
    model_file = os.path.abspath(model_file)
    if not os.path.isfile(model_file) or not model_file.lower().endswith(".py"):
        raise RuntimeError("Generated model file does not exist: " + model_file)
    with io.open(model_file, "r", encoding="utf-8") as stream:
        source = stream.read()
    tree = ast.parse(source, filename=model_file)
    banned_calls = {
        "eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
        "getattr", "setattr", "delattr", "globals", "locals", "vars",
    }
    allowed_top_level = [ast.Assign, ast.FunctionDef]
    annotation_assignment = getattr(ast, "AnnAssign", None)
    if annotation_assignment is not None:
        allowed_top_level.append(annotation_assignment)
    allowed_top_level = tuple(allowed_top_level)
    for node in tree.body:
        if _is_docstring_node(node):
            continue
        if not isinstance(node, allowed_top_level):
            raise RuntimeError("Generated model contains executable top-level code: " + type(node).__name__)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RuntimeError("Imports are not allowed in generated model files")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in banned_calls:
            raise RuntimeError("Unsafe call in generated model: " + node.func.id)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise RuntimeError("Private attribute access is not allowed in generated model files")
    namespace = runpy.run_path(model_file)
    if not callable(namespace.get("build")):
        raise RuntimeError("Generated model must define build(hfss)")
    return namespace


def _active_desktop():
    desktop = getattr(__main__, "oDesktop", None)
    if desktop is not None:
        return desktop
    import ScriptEnv

    ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
    desktop = getattr(__main__, "oDesktop", None)
    if desktop is None:
        raise RuntimeError("Unable to obtain AEDT oDesktop")
    return desktop


class _Material:
    def __init__(self, name):
        self.name = name
        self.permittivity = 1.0
        self.dielectric_loss_tangent = 0.0


class _Materials:
    def __init__(self, definition_manager):
        self._definition_manager = definition_manager
        self._materials = {}

    def add_material(self, name):
        material = _Material(name)
        self._materials[name.lower()] = material
        return material

    def ensure(self, name):
        material = self._materials.get(str(name).lower())
        if material is None:
            return
        args = [
            "NAME:" + material.name,
            "CoordinateSystemType:=", "Cartesian",
            "BulkOrSurfaceType:=", 1,
            ["NAME:PhysicsTypes", "set:=", ["Electromagnetic"]],
            "permittivity:=", str(material.permittivity),
            "dielectric_loss_tangent:=", str(material.dielectric_loss_tangent),
        ]
        existing = [str(item).lower() for item in self._definition_manager.GetProjectMaterialNames()]
        if material.name.lower() in existing:
            self._definition_manager.EditMaterial(material.name, args)
        else:
            self._definition_manager.AddMaterial(args)


class _Modeler:
    def __init__(self, editor, materials):
        self._editor = editor
        self._materials = materials

    def _attributes(self, name, material):
        self._materials.ensure(material)
        dielectric = str(material).lower() not in {"copper", "pec", "aluminum", "silver", "gold"}
        transparency = 0.8 if str(material).lower() in {"vacuum", "air"} else 0.2
        return [
            "NAME:Attributes",
            "Name:=", name,
            "Flags:=", "",
            "Color:=", "(132 132 193)",
            "Transparency:=", transparency,
            "PartCoordinateSystem:=", "Global",
            "SolveInside:=", dielectric,
            "MaterialValue:=", '"' + str(material) + '"',
            "UDMId:=", "",
            "SurfaceMaterialValue:=", '"Steel-oxidised-surface"',
            "ShellElement:=", False,
            "ShellElementThickness:=", "0mm",
            "IsMaterialEditable:=", True,
            "UseMaterialAppearance:=", False,
            "IsLightweight:=", False,
        ]

    def _require_new_name(self, name):
        existing = []
        for group in ("Solids", "Sheets", "Lines", "Unclassified"):
            try:
                existing.extend(str(item).lower() for item in self._editor.GetObjectsInGroup(group))
            except Exception:
                continue
        if str(name).lower() in existing:
            raise RuntimeError("Object already exists; use an empty design or rename it: " + str(name))

    def create_box(self, origin, sizes, name=None, material=None, **kwargs):
        self._require_new_name(name)
        args = [
            "NAME:BoxParameters",
            "XPosition:=", origin[0], "YPosition:=", origin[1], "ZPosition:=", origin[2],
            "XSize:=", sizes[0], "YSize:=", sizes[1], "ZSize:=", sizes[2],
        ]
        return self._editor.CreateBox(args, self._attributes(name, material))

    def create_cylinder(self, orientation, origin, radius, height, num_sides=0, name=None, material=None, **kwargs):
        self._require_new_name(name)
        args = [
            "NAME:CylinderParameters",
            "XCenter:=", origin[0], "YCenter:=", origin[1], "ZCenter:=", origin[2],
            "Radius:=", radius, "Height:=", height,
            "WhichAxis:=", str(orientation), "NumSides:=", str(num_sides),
        ]
        return self._editor.CreateCylinder(args, self._attributes(name, material))

    def create_polyline(self, points, cover_surface=False, close_surface=False, name=None, material=None, **kwargs):
        self._require_new_name(name)
        native_points = [list(point) for point in points]
        if close_surface and native_points[0] != native_points[-1]:
            native_points.append(list(native_points[0]))
        point_args = ["NAME:PolylinePoints"]
        for point in native_points:
            point_args.append(["NAME:PLPoint", "X:=", point[0], "Y:=", point[1], "Z:=", point[2]])
        segment_args = ["NAME:PolylineSegments"]
        for index in range(len(native_points) - 1):
            segment_args.append(
                ["NAME:PLSegment", "SegmentType:=", "Line", "StartIndex:=", index, "NoOfPoints:=", 2]
            )
        xsection = [
            "NAME:PolylineXSection",
            "XSectionType:=", "None", "XSectionOrient:=", "Auto",
            "XSectionWidth:=", "0mm", "XSectionTopWidth:=", "0mm",
            "XSectionHeight:=", "0mm", "XSectionNumSegments:=", "0",
            "XSectionBendType:=", "Corner",
        ]
        args = [
            "NAME:PolylineParameters",
            "IsPolylineCovered:=", bool(cover_surface),
            "IsPolylineClosed:=", bool(close_surface),
            point_args,
            segment_args,
            xsection,
        ]
        return self._editor.CreatePolyline(args, self._attributes(name, material))

    def thicken_sheet(self, assignment, thickness, both_sides=False):
        self._editor.ThickenSheet(
            ["NAME:Selections", "Selections:=", assignment, "NewPartsModelFlag:=", "Model"],
            ["NAME:SheetThickenParameters", "Thickness:=", thickness, "BothSides:=", bool(both_sides)],
        )
        return assignment

    def unite(self, assignments, **kwargs):
        selections = ",".join(str(item) for item in assignments)
        self._editor.Unite(
            ["NAME:Selections", "Selections:=", selections],
            ["NAME:UniteParameters", "KeepOriginals:=", False],
        )
        return assignments[0]

    def subtract(self, blank, tool, keep_originals=False, **kwargs):
        self._editor.Subtract(
            ["NAME:Selections", "Blank Parts:=", blank, "Tool Parts:=", tool],
            ["NAME:SubtractParameters", "KeepOriginals:=", bool(keep_originals)],
        )
        return blank

    def fit_all(self):
        return self._editor.FitAll()


class _NativeHfss:
    def __init__(self, project, design, editor):
        self._design = design
        self.project_name = str(project.GetName())
        self.design_name = str(design.GetName())
        self.materials = _Materials(project.GetDefinitionManager())
        self.modeler = _Modeler(editor, self.materials)

    def __setitem__(self, name, value):
        variables = [str(item).lower() for item in self._design.GetVariables()]
        if str(name).lower() in variables:
            props = ["NAME:ChangedProps", ["NAME:" + str(name), "Value:=", str(value)]]
        else:
            props = [
                "NAME:NewProps",
                [
                    "NAME:" + str(name),
                    "PropType:=", "VariableProp",
                    "UserDef:=", True,
                    "Value:=", str(value),
                    "Description:=", "LEAM generated parameter",
                    "ReadOnly:=", False,
                    "Hidden:=", False,
                    "Sweep:=", True,
                ],
            ]
        self._design.ChangeProperty(
            [
                "NAME:AllTabs",
                ["NAME:LocalVariableTab", ["NAME:PropServers", "LocalVariables"], props],
            ]
        )


def _resolve_project(desktop):
    try:
        project_names = [str(item) for item in desktop.GetProjectList()]
    except Exception:
        project_names = []
    if len(project_names) == 1:
        project = desktop.SetActiveProject(project_names[0])
        if project is not None:
            return project
    if not project_names:
        project = desktop.NewProject()
        if project is not None:
            return project
    try:
        project = desktop.GetActiveProject()
    except Exception:
        project = None
    if project is not None:
        return project
    raise RuntimeError(
        "No AEDT project is active. Keep only the target project open, or click its project node before Run Script. "
        "Open projects: " + ", ".join(project_names)
    )


def _new_hfss_design(project, case_id):
    base = "LEAM_" + "".join(character if character.isalnum() or character == "_" else "_" for character in str(case_id))
    try:
        existing = [str(item).split(";")[-1] for item in project.GetTopDesignList()]
    except Exception:
        existing = []
    name = base
    suffix = 2
    while name.lower() in [item.lower() for item in existing]:
        name = base + "_" + str(suffix)
        suffix += 1
    design = project.InsertDesign("HFSS", name, "DrivenModal", "")
    if design is None:
        design = project.SetActiveDesign(name)
    return design


def _resolve_design(project, case_id, create_new_design):
    if create_new_design:
        return _new_hfss_design(project, case_id)
    try:
        design = project.GetActiveDesign()
    except Exception:
        design = None
    if design is not None:
        return design
    try:
        design_objects = list(project.GetDesigns())
    except Exception:
        design_objects = []
    hfss_designs = []
    for candidate in design_objects:
        try:
            if "HFSS" in str(candidate.GetDesignType()).upper():
                hfss_designs.append(candidate)
        except Exception:
            continue
    if len(hfss_designs) == 1:
        return project.SetActiveDesign(hfss_designs[0].GetName())
    return _new_hfss_design(project, case_id)


def run_model(model_file, desktop=None, create_new_design=False):
    """Build a generated geometry in an active or newly created HFSS design."""
    namespace = _safe_model_namespace(model_file)
    desktop = desktop or _active_desktop()
    project = _resolve_project(desktop)
    design = _resolve_design(project, namespace.get("CASE_ID") or "antenna", create_new_design)
    if design is None:
        raise RuntimeError("AEDT could not create or activate an HFSS design")
    design_type = str(design.GetDesignType())
    if "HFSS" not in design_type.upper():
        raise RuntimeError("The active design is not HFSS: " + design_type)
    editor = design.SetActiveEditor("3D Modeler")
    native_hfss = _NativeHfss(project, design, editor)
    try:
        namespace["build"](native_hfss)
    except Exception as exc:
        desktop.AddMessage(native_hfss.project_name, native_hfss.design_name, 2, "LEAM build failed: " + str(exc))
        raise
    message = "LEAM geometry built but not saved: " + str(namespace.get("CASE_ID") or model_file)
    desktop.AddMessage(native_hfss.project_name, native_hfss.design_name, 0, message)
    return {
        "status": "built_unsaved",
        "case_id": namespace.get("CASE_ID"),
        "project": native_hfss.project_name,
        "design": native_hfss.design_name,
    }


if __name__ == "__main__":
    raw = globals().get("ScriptArgument", "")
    model_path = str(raw).strip().strip('"')
    if not model_path:
        raise RuntimeError("Pass the generated model path in AEDT's Script Arguments field")
    run_model(model_path)
