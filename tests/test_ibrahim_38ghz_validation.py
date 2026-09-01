from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from antenna_mcp.validation import ValidationBenchmark


ROOT = Path(__file__).parents[1] / "examples" / "validation" / "ibrahim_38ghz_monopole"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ibrahim_benchmark_matches_reference_and_keeps_scope_narrow():
    benchmark = ValidationBenchmark.model_validate_json((ROOT / "benchmark.json").read_text("utf-8"))
    module = _load("ibrahim_reference", "reference_model.py")

    assert benchmark.benchmark_id == "ibrahim_2023_38ghz_monopole"
    assert set(module.paper_parameters()) == set(benchmark.reference["parameters"])
    assert benchmark.reference["parameters"]["radiator_diameter_R"]["value"] == 4.94
    assert "four-port MIMO" in benchmark.source.notes[0]
    assert module.DESIGN_NAME == "Ibrahim2023Antenna3_38GHz"


def test_ibrahim_slot_coordinates_reconstruct_all_three_printed_dimensions():
    module = _load("ibrahim_slot_geometry", "reference_model.py")
    coordinates = module.geometry_coordinates()

    assert coordinates["substrate_size"] == [12.0, 12.0, 0.203]
    assert coordinates["radiator_center"] == [6.0, 9.47, 0.203]
    assert coordinates["radiator_radius"] == 2.47
    assert coordinates["feed_origin"] == [5.8, 0.0, 0.203]
    assert coordinates["ground_size"] == [12.0, 7.7]
    assert coordinates["slot_size"][0] == 2.2
    assert coordinates["slot_left_reconstructed_length"] == pytest.approx(2.45, abs=1e-12)
    assert coordinates["slot_right_reconstructed_length"] == pytest.approx(2.35, abs=1e-12)
    assert coordinates["slot_left_relative_x"] == pytest.approx(-0.999606, abs=1e-5)
    assert coordinates["slot_right_relative_x"] == pytest.approx(1.200394, abs=1e-5)


def test_ibrahim_assumptions_are_explicit_and_not_promoted_to_paper_facts():
    payload = json.loads((ROOT / "assumptions.json").read_text("utf-8"))
    module = _load("ibrahim_assumptions", "reference_model.py")

    assert payload["benchmark_id"] == "ibrahim_2023_38ghz_monopole"
    assert "dielectric loss tangent" in payload["unresolved_from_paper"]
    assert "substrate_loss_tangent" not in module.paper_parameters()
    assert module.engineering_assumptions()["substrate_loss_tangent"] == 0.0027
    assert "slot_offset_rule" in payload["derived_from_paper_geometry"]


class _Face:
    def __init__(self, face_id: int, center: list[float]):
        self.id = face_id
        self.center = center


class _Object:
    def __init__(self, name: str, faces=None):
        self.name = name
        self.faces = faces or []


class _Material:
    def __init__(self):
        self.permittivity = None
        self.dielectric_loss_tangent = None


class _Materials:
    def __init__(self):
        self.created = None

    def exists_material(self, name):
        return False

    def add_material(self, name):
        self.created = _Material()
        return self.created


class _Modeler:
    def __init__(self):
        self.object_names = []
        self.calls = []
        self.model_units = None

    def create_box(self, origin, size, **kwargs):
        self.calls.append(("create_box", origin, size, kwargs))
        return _Object(kwargs["name"])

    def create_rectangle(self, orientation, origin, size, **kwargs):
        self.calls.append(("create_rectangle", orientation, origin, size, kwargs))
        return _Object(kwargs["name"])

    def create_circle(self, orientation, origin, radius, **kwargs):
        self.calls.append(("create_circle", orientation, origin, radius, kwargs))
        return _Object(kwargs["name"])

    def unite(self, assignments):
        self.calls.append(("unite", assignments))
        return True

    def subtract(self, blank, tool, **kwargs):
        self.calls.append(("subtract", blank, tool, kwargs))
        return True

    def create_region(self, padding, **kwargs):
        self.calls.append(("create_region", padding, kwargs))
        return _Object(
            kwargs["name"],
            faces=[
                _Face(1, [-2.0, 6.0, 0.0]),
                _Face(2, [14.0, 6.0, 0.0]),
                _Face(3, [6.0, 0.0, 0.0]),
                _Face(4, [6.0, 14.0, 0.0]),
                _Face(5, [6.0, 6.0, -2.0]),
                _Face(6, [6.0, 6.0, 2.203]),
            ],
        )


class _Setup:
    def __init__(self):
        self.sweep_call = None

    def create_frequency_sweep(self, **kwargs):
        self.sweep_call = kwargs
        return SimpleNamespace(name=kwargs["name"])


class _Hfss:
    def __init__(self):
        self.modeler = _Modeler()
        self.materials = _Materials()
        self.setup_names = []
        self.perfect_e_calls = []
        self.radiation_call = None
        self.wave_port_call = None
        self.setup_call = None
        self.setup = _Setup()

    def assign_perfecte_to_sheets(self, assignment, name):
        self.perfect_e_calls.append((assignment, name))
        return SimpleNamespace(name=name)

    def assign_radiation_boundary_to_faces(self, assignment, **kwargs):
        self.radiation_call = (assignment, kwargs)
        return SimpleNamespace(name=kwargs["name"])

    def wave_port(self, assignment, **kwargs):
        self.wave_port_call = (assignment, kwargs)
        return SimpleNamespace(name=kwargs["name"])

    def create_setup(self, **kwargs):
        self.setup_call = kwargs
        return self.setup


def test_ibrahim_build_uses_the_frozen_geometry_port_and_solver_contract():
    module = _load("ibrahim_build", "reference_model.py")
    hfss = _Hfss()

    assert module.build_reference(hfss) is hfss
    assert hfss.materials.created.permittivity == 3.55
    assert hfss.materials.created.dielectric_loss_tangent == 0.0027
    circle = next(call for call in hfss.modeler.calls if call[0] == "create_circle")
    assert circle[2] == [6.0, 9.47, 0.203]
    assert circle[3] == 2.47
    subtraction = next(call for call in hfss.modeler.calls if call[0] == "subtract")
    assert subtraction[1].name == "Radiator"
    assert subtraction[2].name == "SlotTool"
    assert subtraction[3] == {"keep_originals": False}
    region = next(call for call in hfss.modeler.calls if call[0] == "create_region")
    assert region[1] == [2.0, 2.0, 2.0, 0.0, 2.0, 2.0]
    assert hfss.wave_port_call[0] == 3
    assert hfss.wave_port_call[1]["integration_line"] == [[6.0, 0.0, 0.0], [6.0, 0.0, 0.203]]
    assert hfss.radiation_call[0] == [1, 2, 4, 5, 6]
    assert hfss.setup_call["Frequency"] == "38GHz"
    assert hfss.setup.sweep_call["start_frequency"] == 34.0
    assert hfss.setup.sweep_call["stop_frequency"] == 42.0
    assert hfss.setup.sweep_call["num_of_freq_points"] == 801


def _write_curve(path: Path, rows: list[tuple[float, float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(rows)


def test_ibrahim_paper_gate_accepts_only_complete_resonance_depth_and_band(tmp_path):
    module = _load("ibrahim_paper_targets", "paper_targets.py")
    passing = tmp_path / "passing.csv"
    _write_curve(
        passing,
        [(34.0, -2.0), (36.0, -5.0), (36.5, -10.0), (37.0, -18.0),
         (38.0, -30.0), (39.0, -18.0), (39.5, -10.0), (40.0, -5.0), (42.0, -2.0)],
    )
    report = module.validate_paper_targets(passing)
    assert report["passed"] is True
    assert report["observed"]["minus_10db_band_ghz"] == [36.5, 39.5]

    shallow = tmp_path / "shallow.csv"
    _write_curve(
        shallow,
        [(34.0, -2.0), (36.0, -5.0), (36.5, -10.0), (38.0, -20.0),
         (39.5, -10.0), (40.0, -5.0), (42.0, -2.0)],
    )
    failed = module.validate_paper_targets(shallow)
    assert failed["passed"] is False
    depth_check = next(check for check in failed["checks"] if check["path"] == "resonance.minimum_s11_db")
    assert depth_check["passed"] is False


def test_ibrahim_assumption_spaces_and_frozen_hfss_record_are_complete_and_negative():
    from antenna_mcp.assumption_search import (
        json_sha256,
        load_assumption_space,
        plan_assumption_trials,
    )

    v1 = load_assumption_space(ROOT / "assumption_space.json")
    v2 = load_assumption_space(ROOT / "assumption_space_v2.json")
    record = json.loads(
        (
            ROOT
            / "reference_data"
            / "hfss_reference_and_assumption_studies_2026_08_30.json"
        ).read_text(encoding="utf-8")
    )

    assert v1["paper_parameters"] == v2["paper_parameters"]
    assert json_sha256(v1["paper_parameters"]) == record["paper_parameters_sha256"]
    assert len(plan_assumption_trials(v1)) == 5
    assert len(plan_assumption_trials(v2)) == 5
    assert record["status"] == "solved_fail_paper_gate_after_two_bounded_assumption_studies"
    assert record["baseline"]["convergence"]["converged"] is True
    assert record["baseline"]["paper_gate_passed"] is False
    assert record["baseline"]["minus_10db_band_ghz"] is None
    assert record["assumption_study_v1"]["trials_planned"] == 5
    assert record["assumption_study_v1"]["trials_converged"] == 3
    assert record["assumption_study_v1"]["trials_passing_paper_gate"] == 0
    assert record["assumption_study_v2"]["trials_planned"] == 5
    assert record["assumption_study_v2"]["trials_converged"] == 5
    assert record["assumption_study_v2"]["trials_passing_paper_gate"] == 0
    assert all(
        len(value) == 64
        for value in record["assumption_study_v2"]["trial_curve_sha256"].values()
    )
