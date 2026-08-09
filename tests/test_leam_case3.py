import json
import runpy
from pathlib import Path
from types import SimpleNamespace

from antenna_mcp.modeling import _validate_source_analysis
from antenna_mcp.models import OptimizationRequest
from antenna_mcp.source_refinement import _validate_visual_audit


FIXTURE = Path(__file__).parents[1] / "examples" / "leam_case3"


class FakeModeler:
    def __init__(self):
        self.objects = []
        self.operations = []

    def create_box(self, origin, sizes, name, material):
        result = SimpleNamespace(name=name)
        self.objects.append(("box", name, origin, sizes, material))
        return result

    def create_cylinder(self, orientation, origin, radius, height, name, material):
        result = SimpleNamespace(name=name)
        self.objects.append(("cylinder", name, origin, [radius, height], material))
        return result

    def unite(self, objects):
        self.operations.append(("unite", [item.name for item in objects]))

    def subtract(self, blank, tool, keep_originals=False):
        self.operations.append(("subtract", blank.name, tool.name, keep_originals))

    def fit_all(self):
        pass


class FakeHfss:
    def __init__(self):
        self.variables = {}
        self.modeler = FakeModeler()
        self.materials = SimpleNamespace(
            items={},
            add_material=lambda name: self.materials.items.setdefault(
                name,
                SimpleNamespace(permittivity=None, dielectric_loss_tangent=None),
            ),
        )

    def __setitem__(self, name, value):
        self.variables[name] = value


def test_case3_recognition_and_optimization_specs_validate():
    source = json.loads((FIXTURE / "recognized_source.json").read_text("utf-8"))
    _validate_source_analysis(source)
    optimization = OptimizationRequest.model_validate_json(
        (FIXTURE / "optimization_request.json").read_text("utf-8")
    )

    assert source["antenna_type"].startswith("coplanar")
    assert len(optimization.parameters) == 9
    assert {metric.goal for metric in optimization.metrics} == {"upper_bound", "lower_bound"}


def test_case3_builder_keeps_derived_dimensions_parametric():
    module = runpy.run_path(str(FIXTURE / "build_model.py"))
    hfss = FakeHfss()
    result = module["build"](hfss)

    assert hfss.variables["SL"] == "ML+DPR+0.2mm"
    assert hfss.variables["RPW"] == "(SW-MW-2*MG)/2"
    assert hfss.variables["ground_length"] == "ML-RPL"
    assert hfss.materials.items["LEAM_FR4"].permittivity == 4.4
    assert hfss.materials.items["LEAM_FR4"].dielectric_loss_tangent == 0.02
    assert {name for _, name, *_ in hfss.modeler.objects} == {
        "substrate",
        "radiator",
        "feedline",
        "left_ground",
        "right_ground",
        "horizontal_slot",
        "vertical_slot",
    }
    assert hfss.modeler.operations[-1] == ("subtract", "radiator", "horizontal_slot", False)
    assert set(result) == {"substrate", "radiator", "left_ground", "right_ground"}


def test_case3_operator_audit_encodes_materials_evidence_modes_and_topology():
    audit = json.loads((FIXTURE / "visual_audit.json").read_text("utf-8"))
    _validate_visual_audit(audit)

    components = {item["name"]: item for item in audit["components"]}
    parameters = {item["symbol"]: item for item in audit["parameter_bindings"]}

    assert len(components) == 7
    assert components["horizontal_slot"]["material_class"] == "void"
    assert components["feedline"]["layer_class"] == "top_coplanar"
    assert parameters["DPR"]["quantity"] == "radius"
    assert parameters["SubT"]["evidence_mode"] == "text"
    assert parameters["CuT"]["evidence_mode"] == "unresolved"
    assert parameters["CuT"]["value"] is None
    assert [(item["operation"], item["target"]) for item in audit["required_operations"]] == [
        ("unite", "radiator"),
        ("unite", "horizontal_slot"),
        ("subtract", "radiator"),
    ]
