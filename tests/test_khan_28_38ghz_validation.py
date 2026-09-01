from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from antenna_mcp.validation import ValidationBenchmark


ROOT = Path(__file__).parents[1] / "examples" / "validation" / "khan_28_38ghz_monopole"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_khan_benchmark_freezes_all_table_parameters_and_single_element_scope():
    benchmark = ValidationBenchmark.model_validate_json((ROOT / "benchmark.json").read_text("utf-8"))
    module = _load("khan_reference_contract", "reference_model.py")

    assert benchmark.benchmark_id == "khan_2024_28_38ghz_monopole"
    assert set(module.paper_parameters()) == set(benchmark.reference["parameters"])
    assert benchmark.reference["parameters"]["L"]["value"] == 9.2
    assert benchmark.reference["parameters"]["W"]["value"] == 5.0
    assert benchmark.reference["parameters"]["Ri"]["value"] == 0.6
    assert "MIMO arrangement is excluded" in benchmark.source.notes[0]
    assert module.DESIGN_NAME == "Khan2024SingleElement28_38GHz"


def test_khan_figure_interpretation_is_bounded_and_uses_every_paper_parameter():
    module = _load("khan_geometry", "reference_model.py")
    geometry = module.geometry_coordinates()

    assert geometry["substrate_size"] == [5.0, 9.2, 0.787]
    assert geometry["ground_size"] == [5.0, 1.2]
    assert geometry["body_bounds"] == [1.1, 4.05, 3.9, 7.75]
    assert geometry["rod_top"] == pytest.approx(8.95)
    assert geometry["u_outer_radius"] == 0.6
    assert geometry["u_inner_radius"] == 0.3
    assert geometry["unused_paper_parameters"] == []
    for piece in [*geometry["radiator_pieces"], *geometry["feed_slot_tools"]]:
        x, y, z = piece["origin"]
        width, height = piece["size"]
        assert 0.0 <= x < 5.0
        assert 0.0 <= y < 9.2
        assert z == 0.787
        assert width > 0.0 and height > 0.0
        assert x + width <= 5.0 + 1e-12
        assert y + height <= 9.2 + 1e-12


def test_khan_missing_vertex_port_and_solver_details_remain_labelled_assumptions():
    payload = json.loads((ROOT / "assumptions.json").read_text("utf-8"))
    module = _load("khan_assumptions", "reference_model.py")

    assert "complete radiator polygon vertex coordinates and Boolean construction order" in payload["unresolved_from_paper"]
    assert payload["figure_interpretation_v1"]["evidence_class"].startswith("engineering interpretation")
    assert module.engineering_assumptions()["coordinate_interpretation"].endswith("_v1")
    assert module.engineering_assumptions()["conductor_model"] == "zero_thickness_pec_sheets"


def test_khan_v2_corrects_the_figure2_topology_without_changing_table_values():
    v1 = _load("khan_v1_for_v2_check", "reference_model.py")
    v2 = _load("khan_v2_geometry", "reference_model_v2.py")
    geometry = v2.geometry_coordinates()

    assert v2.paper_parameters() == v1.paper_parameters()
    assert v2.DESIGN_NAME.endswith("_V2")
    assert geometry["body_bounds"] == [1.1, 4.05, 3.9, 7.75]
    assert geometry["top_slot_circle_radius"] == 0.3
    assert geometry["top_slot_rectangle_size"][0] == 0.6
    assert geometry["unused_paper_parameters"] == ["Lc"]
    names = {piece["name"] for piece in geometry["radiator_pieces"]}
    assert {"OuterLeftLeg", "OuterRightLeg", "InnerLeftLeg", "InnerRightLeg"} <= names
    assert "UOuter" not in names


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
                _Face(1, [-2.0, 4.6, 0.0]),
                _Face(2, [7.0, 4.6, 0.0]),
                _Face(3, [2.5, 0.0, 0.0]),
                _Face(4, [2.5, 11.2, 0.0]),
                _Face(5, [2.5, 4.6, -2.0]),
                _Face(6, [2.5, 4.6, 2.787]),
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


def test_khan_build_uses_frozen_geometry_material_port_and_solver_contract():
    module = _load("khan_build", "reference_model.py")
    hfss = _Hfss()

    assert module.build_reference(hfss) is hfss
    assert hfss.materials.created.permittivity == 2.2
    assert hfss.materials.created.dielectric_loss_tangent == 0.0009
    rectangles = [call for call in hfss.modeler.calls if call[0] == "create_rectangle"]
    assert any(call[4]["name"] == "Ground" and call[3] == [5.0, 1.2] for call in rectangles)
    circles = [call for call in hfss.modeler.calls if call[0] == "create_circle"]
    assert [(call[4]["name"], call[3]) for call in circles] == [("UOuter", 0.6), ("UInnerTool", 0.3)]
    assert hfss.wave_port_call[0] == 3
    assert hfss.wave_port_call[1]["integration_line"] == [[2.5, 0.0, 0.0], [2.5, 0.0, 0.787]]
    assert hfss.radiation_call[0] == [1, 2, 4, 5, 6]
    assert hfss.setup_call["Frequency"] == "38GHz"
    assert hfss.setup.sweep_call["start_frequency"] == 22.0
    assert hfss.setup.sweep_call["stop_frequency"] == 43.0
    assert hfss.setup.sweep_call["num_of_freq_points"] == 1051


def _write_curve(path: Path, rows: list[tuple[float, float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(rows)


def test_khan_paper_gate_requires_both_complete_dual_bands(tmp_path):
    module = _load("khan_paper_targets", "paper_targets.py")
    rows = [
        (22.0, -2.0), (24.0, -3.0), (24.8, -8.0), (24.86, -10.0),
        (26.7, -22.0), (28.65, -10.0), (28.8, -7.0), (30.0, -3.0),
        (35.0, -3.0), (36.1, -7.0), (36.24, -10.0), (38.6, -30.0),
        (40.82, -10.0), (41.0, -7.0), (43.0, -2.0),
    ]
    passing = tmp_path / "passing.csv"
    _write_curve(passing, rows)
    report = module.validate_paper_targets(passing)
    assert report["passed"] is True
    assert report["observed"]["bands"][0]["minus_10db_band_ghz"] == pytest.approx([24.86, 28.65])
    assert report["observed"]["bands"][1]["minus_10db_band_ghz"] == pytest.approx([36.24, 40.82])

    missing_upper = tmp_path / "missing_upper.csv"
    _write_curve(missing_upper, [(frequency, -5.0 if frequency >= 35.0 else value) for frequency, value in rows])
    failed = module.validate_paper_targets(missing_upper)
    assert failed["passed"] is False
    assert next(check for check in failed["checks"] if check["path"] == "upper_38ghz_band.local_minimum_exists")["passed"] is False


def test_khan_assumption_space_and_frozen_hfss_record_are_complete_and_negative():
    from antenna_mcp.assumption_search import load_assumption_space, plan_assumption_trials

    space = load_assumption_space(ROOT / "assumption_space_v1.json")
    record = json.loads(
        (ROOT / "reference_data" / "hfss_reference_and_assumption_study_2026_08_31.json").read_text("utf-8")
    )

    assert len(plan_assumption_trials(space)) == 3
    assert record["status"] == "solved_fail_paper_gate_after_topology_correction_and_bounded_assumption_study"
    assert record["v1"]["convergence"]["converged"] is True
    assert record["v1"]["paper_gate_passed"] is False
    assert record["v2"]["convergence"]["converged"] is True
    assert record["v2"]["paper_dimensions_changed"] is False
    assert record["v2"]["paper_gate_passed"] is False
    study = record["bounded_assumption_study_v1"]
    assert study["trials_completed"] == 3
    assert study["trials_converged"] == 3
    assert study["trials_passing_paper_gate"] == 0
    assert all(len(trial["curve_sha256"]) == 64 for trial in study["trials"])
