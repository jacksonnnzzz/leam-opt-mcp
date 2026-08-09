from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from antenna_mcp.modeling import validate_generated_python


ROOT = Path(__file__).resolve().parents[1] / "examples" / "leam_paper_cases"
CASES = (
    "demo_l_slot",
    "case1_vivaldi",
    "case2_slotted_patch",
    "case3_monopole",
)


class _Property:
    def __init__(self) -> None:
        self.value = None


class _Material:
    def __init__(self) -> None:
        self.permittivity = _Property()
        self.dielectric_loss_tangent = _Property()


class _Materials:
    def __init__(self) -> None:
        self.names = []

    def add_material(self, name):
        self.names.append(name)
        return _Material()


class _Modeler:
    def __init__(self) -> None:
        self.calls = []

    def create_box(self, *args, **kwargs):
        self.calls.append(("create_box", args, kwargs))
        return kwargs.get("name")

    def create_cylinder(self, *args, **kwargs):
        self.calls.append(("create_cylinder", args, kwargs))
        return kwargs.get("name")

    def create_polyline(self, *args, **kwargs):
        self.calls.append(("create_polyline", args, kwargs))
        return kwargs.get("name")

    def thicken_sheet(self, *args, **kwargs):
        self.calls.append(("thicken_sheet", args, kwargs))
        return args[0]

    def unite(self, *args, **kwargs):
        self.calls.append(("unite", args, kwargs))
        return True

    def subtract(self, *args, **kwargs):
        self.calls.append(("subtract", args, kwargs))
        return True

    def fit_all(self, *args, **kwargs):
        self.calls.append(("fit_all", args, kwargs))
        return True


class _Hfss:
    def __init__(self) -> None:
        self.parameters = {}
        self.materials = _Materials()
        self.modeler = _Modeler()

    def __setitem__(self, name, value):
        self.parameters[name] = value


@pytest.mark.parametrize("case", CASES)
def test_generated_paper_case_is_safe_and_buildable_offline(case):
    path = ROOT / case / "generated_model_v001.py"
    source = path.read_text(encoding="utf-8")
    validate_generated_python(source)
    namespace = runpy.run_path(str(path))

    hfss = _Hfss()
    assert namespace["build"](hfss) is hfss
    assert hfss.parameters
    assert hfss.modeler.calls[-1][0] == "fit_all"
    assert not any(name in {"analyze", "save_project"} for name, _, _ in hfss.modeler.calls)


@pytest.mark.parametrize("case", CASES)
def test_each_paper_case_has_machine_readable_evidence(case):
    payload = json.loads((ROOT / case / "evidence_and_assumptions.json").read_text(encoding="utf-8"))
    assert payload["case_id"] == case
    assert payload["evidence"]
    assert "assumptions" in payload


def test_vivaldi_has_twenty_monotonic_taper_parameters():
    namespace = runpy.run_path(str(ROOT / "case1_vivaldi" / "generated_model_v001.py"))
    values = {
        name: float(value.removesuffix("mm"))
        for name, value in namespace["PARAMETERS"]
        if name.startswith("X")
    }
    ordered = [values[f"X{index}"] for index in range(1, 21)]
    assert ordered == sorted(ordered)
    assert 0 < ordered[0] < ordered[-1] < 15


def test_case3_keeps_reviewed_derived_relations():
    namespace = runpy.run_path(str(ROOT / "case3_monopole" / "generated_model_v001.py"))
    parameters = dict(namespace["PARAMETERS"])
    assert parameters["SL"] == "ML+DPR+0.2mm"
    assert parameters["ground_length"] == "ML-RPL"
    assert parameters["RPW"] == "(SW-MW-2*MG)/2"


def test_reconstruction_requests_pair_each_case_with_visual_and_language_input():
    payload = json.loads((ROOT / "reconstruction_requests.json").read_text(encoding="utf-8"))
    requests = {item["case_id"]: item for item in payload["requests"]}
    assert set(requests) == set(CASES)
    assert all(item["visual_target"] and item["language_input"] for item in requests.values())
