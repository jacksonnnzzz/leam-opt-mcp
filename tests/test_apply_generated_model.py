from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "apply_generated_model.py"
MODEL = ROOT / "examples" / "leam_paper_cases" / "demo_l_slot" / "generated_model_v001.py"


def _module():
    spec = importlib.util.spec_from_file_location("apply_generated_model", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Modeler:
    def create_box(self, *args, **kwargs):
        return kwargs.get("name")

    def unite(self, *args, **kwargs):
        return True

    def subtract(self, *args, **kwargs):
        return True

    def fit_all(self):
        return True


class _Hfss:
    project_name = "Project5"
    design_name = "HFSSDesign1"

    def __init__(self):
        self.parameters = {}
        self.modeler = _Modeler()

    def __setitem__(self, name, value):
        self.parameters[name] = value


def test_load_and_apply_generated_model_without_aedt():
    module = _module()
    namespace = module.load_generated_model(MODEL)
    assert namespace["CASE_ID"] == "demo_l_slot"
    hfss = _Hfss()
    result = module.apply_generated_model(MODEL, hfss)
    assert result["status"] == "built_unsaved"
    assert result["project"] == "Project5"
    assert hfss.parameters["PatchW"] == "10mm"


def test_loader_rejects_executable_top_level_code(tmp_path):
    module = _module()
    path = tmp_path / "bad.py"
    path.write_text("def build(hfss):\n    return hfss\nbuild(None)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="executable top-level code"):
        module.load_generated_model(path)
