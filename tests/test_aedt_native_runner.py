from __future__ import annotations

import runpy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tools" / "run_generated_model_in_aedt.py"
CASES = (
    "demo_l_slot",
    "case1_vivaldi",
    "case2_slotted_patch",
    "case3_monopole",
)


def _value_after(items, marker):
    return items[items.index(marker) + 1]


class _DefinitionManager:
    def __init__(self):
        self.materials = {}

    def GetProjectMaterialNames(self):
        return tuple(self.materials)

    def AddMaterial(self, args):
        self.materials[args[0].removeprefix("NAME:")] = args

    def EditMaterial(self, name, args):
        self.materials[name] = args


class _Editor:
    def __init__(self):
        self.objects = {}
        self.calls = []

    def GetObjectsInGroup(self, group):
        return tuple(self.objects)

    def _create(self, kind, args, attributes):
        name = _value_after(attributes, "Name:=")
        self.objects[name] = kind
        self.calls.append((kind, args, attributes))
        return name

    def CreateBox(self, args, attributes):
        return self._create("box", args, attributes)

    def CreateCylinder(self, args, attributes):
        return self._create("cylinder", args, attributes)

    def CreatePolyline(self, args, attributes):
        return self._create("polyline", args, attributes)

    def ThickenSheet(self, selections, parameters):
        self.calls.append(("thicken", selections, parameters))

    def Unite(self, selections, parameters):
        names = _value_after(selections, "Selections:=").split(",")
        for name in names[1:]:
            self.objects.pop(name, None)
        self.calls.append(("unite", selections, parameters))

    def Subtract(self, selections, parameters):
        self.objects.pop(_value_after(selections, "Tool Parts:="), None)
        self.calls.append(("subtract", selections, parameters))

    def FitAll(self):
        self.calls.append(("fit_all", (), {}))
        return True


class _Design:
    def __init__(self, editor, name="HFSSDesign1"):
        self.editor = editor
        self.name = name
        self.variables = {}
        self.changes = []

    def GetName(self):
        return self.name

    def GetDesignType(self):
        return "HFSS"

    def GetVariables(self):
        return tuple(self.variables)

    def ChangeProperty(self, command):
        self.changes.append(command)
        props = command[1][2]
        definition = props[1]
        name = definition[0].removeprefix("NAME:")
        self.variables[name] = _value_after(definition, "Value:=")

    def SetActiveEditor(self, name):
        assert name == "3D Modeler"
        return self.editor


class _Project:
    def __init__(self, design, definitions):
        self.design = design
        self.designs = [design]
        self.definitions = definitions

    def GetName(self):
        return "Project5"

    def GetActiveDesign(self):
        return self.design

    def GetDesigns(self):
        return tuple(self.designs)

    def GetTopDesignList(self):
        return tuple("HFSS;" + item.GetName() for item in self.designs)

    def SetActiveDesign(self, name):
        for design in self.designs:
            if design.GetName() == name:
                self.design = design
                return design
        return None

    def InsertDesign(self, design_type, name, solution_type, parent):
        assert design_type == "HFSS"
        design = _Design(_Editor(), name=name)
        self.designs.append(design)
        self.design = design
        return design

    def GetDefinitionManager(self):
        return self.definitions


class _Desktop:
    def __init__(self):
        self.editor = _Editor()
        self.design = _Design(self.editor)
        self.definitions = _DefinitionManager()
        self.project = _Project(self.design, self.definitions)
        self.messages = []

    def GetActiveProject(self):
        return self.project

    def GetProjectList(self):
        return (self.project.GetName(),)

    def SetActiveProject(self, name):
        return self.project if name == self.project.GetName() else None

    def NewProject(self):
        return self.project

    def AddMessage(self, project, design, severity, message):
        self.messages.append((project, design, severity, message))


@pytest.mark.parametrize("case", CASES)
def test_native_runner_builds_each_generated_case_without_pyaedt(case):
    namespace = runpy.run_path(str(RUNNER))
    desktop = _Desktop()
    model = ROOT / "examples" / "leam_paper_cases" / case / "generated_model_v001.py"
    result = namespace["run_model"](str(model), desktop=desktop)
    assert result["status"] == "built_unsaved"
    assert result["case_id"] == case
    assert desktop.design.variables
    assert desktop.editor.calls[-1][0] == "fit_all"
    assert desktop.messages[-1][2] == 0


@pytest.mark.parametrize("case", CASES)
def test_aedt_wrapper_exists_and_is_valid_python(case):
    wrapper = ROOT / "examples" / "leam_paper_cases" / case / "run_in_aedt.py"
    compile(wrapper.read_text(encoding="utf-8"), str(wrapper), "exec")


def test_native_runner_rejects_non_hfss_design():
    namespace = runpy.run_path(str(RUNNER))
    desktop = _Desktop()
    desktop.design.GetDesignType = lambda: "Maxwell 3D"
    model = ROOT / "examples" / "leam_paper_cases" / "demo_l_slot" / "generated_model_v001.py"
    with pytest.raises(RuntimeError, match="not HFSS"):
        namespace["run_model"](str(model), desktop=desktop)


def test_native_runner_activates_sole_project_and_creates_unique_hfss_design():
    namespace = runpy.run_path(str(RUNNER))
    desktop = _Desktop()
    desktop.GetActiveProject = lambda: (_ for _ in ()).throw(RuntimeError("project is not activated"))
    model = ROOT / "examples" / "leam_paper_cases" / "case1_vivaldi" / "generated_model_v001.py"
    result = namespace["run_model"](str(model), desktop=desktop, create_new_design=True)
    assert result["project"] == "Project5"
    assert result["design"] == "LEAM_case1_vivaldi"
    assert desktop.project.design.GetDesignType() == "HFSS"
    assert desktop.project.design.editor.calls[-1][0] == "fit_all"
