from __future__ import annotations

import csv
import importlib.util
import inspect
import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest

from antenna_mcp.validation import ValidationBenchmark


ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "examples" / "validation" / "yeo_slot_loaded_patch"


def _reference_model():
    path = CASE_DIR / "reference_model.py"
    spec = importlib.util.spec_from_file_location("yeo_reference_model", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _local_module(filename: str, module_name: str):
    path = CASE_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def test_yeo_paper_parameters_are_separate_from_engineering_assumptions():
    model = _reference_model()
    conventional = model.paper_parameters("conventional")
    scaled = model.paper_parameters("scaled_slot_loaded")
    assumptions = model.engineering_assumptions()

    assert conventional["patch_width"]["value"] == 40.0
    assert conventional["patch_length"]["value"] == 31.9
    assert scaled["patch_width"]["value"] == 31.8
    assert scaled["patch_length"]["value"] == 25.4
    assert scaled["slot_length"]["value"] == 29.8
    assert all(item["evidence"] == "paper" for item in conventional.values())
    assert assumptions["conductor_thickness"]["value"] == 0.035
    assert "conductor_thickness" not in conventional
    assert "radiation_padding" not in scaled


def test_yeo_coordinate_resolution_matches_documented_figure_convention():
    model = _reference_model()
    conventional = model.geometry_coordinates("conventional")
    scaled = model.geometry_coordinates("scaled_slot_loaded")

    assert conventional["patch_origin"] == [-20.0, 24.5, 0.76]
    assert conventional["feed_size"] == [1.66, 33.5, 0.035]
    assert scaled["patch_origin"] == [-15.9, 27.3, 0.76]
    assert scaled["feed_size"] == [1.66, 39.3, 0.035]
    assert scaled["slot_origin"] == [-14.9, 50.7, 0.76]
    assert scaled["slot_size"] == [29.8, 1.0, 0.035]


def test_yeo_benchmark_contracts_validate_and_do_not_mix_cases():
    conventional = ValidationBenchmark.model_validate_json(
        (CASE_DIR / "benchmark_conventional.json").read_text("utf-8")
    )
    scaled = ValidationBenchmark.model_validate_json(
        (CASE_DIR / "benchmark_scaled_slot_loaded.json").read_text("utf-8")
    )

    assert conventional.benchmark_id == "yeo_2019_conventional_inset_patch"
    assert scaled.benchmark_id == "yeo_2019_scaled_slot_loaded_patch"
    assert "slot_length" not in conventional.reference["parameters"]
    assert scaled.reference["parameters"]["slot_length"]["value"] == 29.8
    assert [target.name for target in conventional.s11.targets] == ["fundamental"]
    assert [target.name for target in scaled.s11.targets] == ["fundamental", "upper_mode"]


def test_yeo_literature_targets_preserve_explicit_text_values():
    targets = json.loads(
        (CASE_DIR / "reference_data" / "literature_targets.json").read_text("utf-8")
    )
    conventional = targets["cases"]["conventional_inset_fed_patch"]["s11_targets"]
    scaled = targets["cases"]["scaled_slot_loaded_patch"]["s11_targets"]

    assert conventional["resonances_ghz"] == [2.5]
    assert conventional["first_minus_10db_band_ghz"] == [2.49, 2.51]
    assert scaled["resonances_ghz"] == [2.5, 3.465]
    assert scaled["first_minus_10db_band_ghz"] == [2.496, 2.503]
    assert targets["digitization"]["use_restrictions"]


def test_yeo_builder_uses_disjoint_port_and_radiation_faces():
    model = _reference_model()

    class Property:
        def __init__(self, value):
            self.value = value

        @property
        def evaluated_value(self):
            return self.value

    class Material:
        def __init__(self):
            self.permittivity = Property(1.0)
            self.dielectric_loss_tangent = Property(0.0)

    class Materials:
        def __init__(self):
            self.rf35 = None

        def exists_material(self, _name):
            return self.rf35 or False

        def add_material(self, _name):
            self.rf35 = Material()
            return self.rf35

    class Face:
        def __init__(self, identifier, center):
            self.id = identifier
            self.center = center

    class Object:
        def __init__(self, name, faces=None):
            self.name = name
            self.faces = faces or []

    class Modeler:
        def __init__(self):
            self.object_names = []
            self.model_units = None
            self.calls = []

        def create_box(self, origin, size, name, material):
            self.calls.append(("create_box", name, origin, size, material))
            self.object_names.append(name)
            return Object(name)

        def subtract(self, blank, tool, keep_originals):
            self.calls.append(("subtract", getattr(blank, "name", blank), tool.name, keep_originals))
            return True

        def unite(self, objects):
            self.calls.append(("unite", [item.name for item in objects]))
            return objects[0].name

        def create_region(self, padding, pad_type, name):
            self.calls.append(("create_region", padding, pad_type, name))
            return Object(
                name,
                [
                    Face(1, [-70.0, 40.0, 0.0]),
                    Face(2, [70.0, 40.0, 0.0]),
                    Face(3, [0.0, 110.0, 0.0]),
                    Face(4, [0.0, 0.0, 0.0]),
                    Face(5, [0.0, 40.0, 30.795]),
                    Face(6, [0.0, 40.0, -30.035]),
                ],
            )

    class Setup:
        def __init__(self):
            self.sweep_kwargs = None

        def create_frequency_sweep(self, **kwargs):
            self.sweep_kwargs = kwargs
            return object()

    class Hfss:
        def __init__(self):
            self.modeler = Modeler()
            self.materials = Materials()
            self.setup_names = []
            self.radiation_faces = None
            self.port_kwargs = None
            self.setup_kwargs = None

        def assign_radiation_boundary_to_faces(self, faces, name):
            self.radiation_faces = (faces, name)
            return object()

        def wave_port(self, face, **kwargs):
            self.port_kwargs = (face, kwargs)
            return object()

        def create_setup(self, **kwargs):
            self.setup_kwargs = kwargs
            return Setup()

    hfss = Hfss()
    model.build_reference(hfss, "conventional")

    assert hfss.radiation_faces == ([1, 2, 3, 5, 6], "Radiation")
    assert hfss.port_kwargs == (
        4,
        {
            "integration_line": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.795]],
            "modes": 1,
            "impedance": 50,
            "name": "WavePort1",
            "renormalize": True,
        },
    )
    region_call = next(call for call in hfss.modeler.calls if call[0] == "create_region")
    assert region_call == (
        "create_region",
        [30.0, 30.0, 30.0, 0.0, 30.0, 30.0],
        "Absolute Offset",
        "Region",
    )


def test_yeo_calls_match_installed_pyaedt_0263_signatures():
    from ansys.aedt.core import Hfss
    from ansys.aedt.core.application.design import Design
    from ansys.aedt.core.modeler.cad.primitives import GeometryModeler

    wave_port = inspect.signature(Hfss.wave_port).parameters
    assert {"assignment", "integration_line", "modes", "impedance", "name", "renormalize"} <= set(
        wave_port
    )
    assert "assignment" in inspect.signature(Hfss.assign_radiation_boundary_to_faces).parameters
    assert "pad_value" in inspect.signature(GeometryModeler.create_region).parameters
    assert "pad_type" in inspect.signature(GeometryModeler.create_region).parameters
    assert "name" in inspect.signature(Design.set_active_design).parameters


def test_yeo_export_requires_db_self_reflection_and_converts_frequency_units(tmp_path):
    runner = _local_module("run_reference.py", "yeo_run_reference")

    class Data:
        primary_sweep = "Freq"
        units_sweeps = {"Freq": "MHz"}

        def get_expression_data(self, expression, formula):
            assert expression == "dB(S(WavePort1:1,WavePort1:1))"
            assert formula == "real"
            return [1500.0, 2500.0, 3700.0], [-1.0, -20.0, -2.0]

    class Post:
        def get_solution_data(self, **kwargs):
            assert kwargs["expressions"].startswith("dB(S(")
            return Data()

    class Hfss:
        design_name = "Design"
        post = Post()

        def get_traces_for_plot(self, **kwargs):
            assert kwargs["category"] == "dB(S"
            assert kwargs["get_mutual_terms"] is False
            return ["dB(S(WavePort1:1,WavePort1:1))"]

    destination = tmp_path / "s11.csv"
    expression = runner._export_s11(Hfss(), destination)
    rows = list(csv.DictReader(destination.open("r", encoding="utf-8")))

    assert expression == "dB(S(WavePort1:1,WavePort1:1))"
    assert [float(row["frequency_ghz"]) for row in rows] == [1.5, 2.5, 3.7]
    assert [float(row["s11_db"]) for row in rows] == [-1.0, -20.0, -2.0]
    assert runner._is_db_self_reflection("mag(S(P1,P1))") is False
    assert runner._is_db_self_reflection("dB(S(P1,P2))") is False


def _write_synthetic_curve(path: Path, case: str, *, shift_ghz: float = 0.0) -> Path:
    frequency = np.linspace(1.5, 3.7, 4401)
    if case == "conventional":
        centers_and_widths = [(2.5 + shift_ghz, 0.020)]
    else:
        centers_and_widths = [(2.4995 + shift_ghz, 0.007), (3.465 + shift_ghz, 0.024)]
    values = np.zeros_like(frequency)
    for center, width in centers_and_widths:
        sigma = (width / 2.0) / math.sqrt(math.log(2.0))
        dip = -20.0 * np.exp(-((frequency - center) / sigma) ** 2)
        values = np.minimum(values, dip)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(zip(frequency, values))
    return path


def test_yeo_paper_target_report_is_machine_readable(tmp_path):
    targets = _local_module("paper_targets.py", "yeo_paper_targets")
    conventional = _write_synthetic_curve(tmp_path / "conventional.csv", "conventional")
    scaled = _write_synthetic_curve(tmp_path / "scaled.csv", "scaled_slot_loaded")

    report = targets.validate_paper_targets(
        {"conventional": conventional, "scaled_slot_loaded": scaled}
    )

    assert report["status"] == "passed"
    assert report["cases"]["conventional"]["passed"] is True
    assert len(report["cases"]["scaled_slot_loaded"]["observed"]["resonances"]) == 2
    assert report["criteria"]["resonance_relative_error_max"] == 0.01


def test_yeo_paper_target_report_detects_shifted_reference(tmp_path):
    targets = _local_module("paper_targets.py", "yeo_paper_targets_shifted")
    shifted = _write_synthetic_curve(
        tmp_path / "shifted.csv", "conventional", shift_ghz=0.08
    )

    report = targets.validate_paper_targets({"conventional": shifted})

    assert report["status"] == "failed"
    failed = [
        check
        for check in report["cases"]["conventional"]["checks"]
        if not check["passed"]
    ]
    assert any(check["path"] == "resonance_1.relative_error" for check in failed)


def test_yeo_paper_target_cli_writes_failed_report_and_refuses_overwrite(tmp_path):
    targets = _local_module("paper_targets.py", "yeo_paper_targets_cli")
    shifted = _write_synthetic_curve(
        tmp_path / "shifted.csv", "conventional", shift_ghz=0.08
    )
    output = tmp_path / "report.json"
    assert targets.main(
        ["--case", "conventional", "--curve", str(shifted), "--output", str(output)]
    ) == 1
    assert json.loads(output.read_text("utf-8"))["status"] == "failed"
    with pytest.raises(SystemExit):
        targets.main(
            ["--case", "conventional", "--curve", str(shifted), "--output", str(output)]
        )


def test_yeo_assumption_study_resume_requires_exact_structure():
    runner = _local_module("run_assumption_study.py", "yeo_assumption_runner")

    class Boundary:
        def __init__(self, name):
            self.name = name

    class Modeler:
        object_names = [
            "RF35_Substrate",
            "Ground",
            "PatchFeed",
            "Region",
            "LumpedPortSheet",
        ]

    class Hfss:
        design_name = "YeoConventionalPatch_SolidLumped"
        modeler = Modeler()
        boundaries = [Boundary("Radiation"), Boundary("LumpedPort1")]
        setup_names = ["Setup1"]
        existing_analysis_sweeps = ["Setup1 : LastAdaptive", "Setup1 : Sweep1"]

    runner._verify_existing_variant(Hfss(), "solid_lumped")
    Hfss.modeler.object_names = Hfss.modeler.object_names[:-1]
    with pytest.raises(RuntimeError, match="structurally mismatched"):
        runner._verify_existing_variant(Hfss(), "solid_lumped")


def test_yeo_assumption_study_distinguishes_auto_inserted_first_design():
    runner = _local_module("run_assumption_study.py", "yeo_assumption_runner_preflight")

    design = "YeoScaledSlotLoadedPatch_SolidLumped"
    assert runner._reuse_preexisting_variant(design, set(), resume=False) is False
    assert runner._reuse_preexisting_variant(design, {design}, resume=True) is True
    with pytest.raises(RuntimeError, match="without --resume"):
        runner._reuse_preexisting_variant(design, {design}, resume=False)

    class EmptyHfss:
        class Modeler:
            object_names = []

        modeler = Modeler()
        boundaries = []
        setup_names = []

    assert runner._is_exact_empty_design(EmptyHfss()) is True
    EmptyHfss.modeler.object_names = ["unexpected"]
    assert runner._is_exact_empty_design(EmptyHfss()) is False


def test_yeo_assumption_study_has_isolated_case_specific_designs():
    study = _local_module("assumption_study.py", "yeo_assumption_study_cases")

    conventional = {
        item["design"] for item in study.VARIANTS_BY_CASE["conventional"].values()
    }
    scaled = {
        item["design"]
        for item in study.VARIANTS_BY_CASE["scaled_slot_loaded"].values()
    }
    assert conventional.isdisjoint(scaled)
    assert all(name.startswith("YeoConventionalPatch_") for name in conventional)
    assert all(name.startswith("YeoScaledSlotLoadedPatch_") for name in scaled)


def test_scaled_assumption_solid_builder_subtracts_inset_and_slot():
    study = _local_module("assumption_study.py", "yeo_assumption_study_slot")

    class Modeler:
        def __init__(self):
            self.created = []
            self.subtractions = []

        def create_box(self, origin, size, *, name, material):
            obj = {"origin": origin, "size": size, "name": name, "material": material}
            self.created.append(obj)
            return obj

        def subtract(self, blank, tool, *, keep_originals):
            self.subtractions.append((blank["name"], tool["name"], keep_originals))
            return True

        @staticmethod
        def unite(objects):
            return bool(objects)

    class Hfss:
        modeler = Modeler()

    coordinates = _reference_model().geometry_coordinates("scaled_slot_loaded")
    study._build_solid_conductors(Hfss(), coordinates)

    assert [item["name"] for item in Hfss.modeler.created] == [
        "Ground",
        "PatchFeed",
        "InsetCutTool",
        "SlotCutTool",
        "FeedLine",
    ]
    assert Hfss.modeler.subtractions == [
        ("PatchFeed", "InsetCutTool", False),
        ("PatchFeed", "SlotCutTool", False),
    ]


def test_recorded_conventional_assumption_study_is_fail_closed_and_hash_frozen():
    record = json.loads(
        (
            CASE_DIR
            / "reference_data"
            / "conventional_hfss_assumption_study_2026_08_26.json"
        ).read_text("utf-8")
    )

    assert record["status"] == "solved_fail_paper_gate"
    assert record["paper_gate_passed_variants"] == []
    assert set(record["results"]) == {
        "baseline_solid_wave",
        "solid_lumped",
        "pec_wave",
        "pec_lumped",
    }
    assert all(
        result["paper_gate_passed"] is False
        and len(result["curve_sha256"]) == 64
        for result in record["results"].values()
    )
    assert record["results"]["solid_lumped"]["observed_resonance_ghz"] == 2.47
    assert record["results"]["solid_lumped"][
        "observed_first_minus_10db_band_ghz"
    ] == pytest.approx([2.469030984348128, 2.4721133095595813])
    assert "Do not generate or accept" in record["policy"]


def test_recorded_scaled_assumption_study_is_fail_closed_and_hash_frozen():
    record = json.loads(
        (
            CASE_DIR
            / "reference_data"
            / "scaled_hfss_assumption_study_2026_08_26.json"
        ).read_text("utf-8")
    )

    assert record["status"] == "solved_fail_paper_gate"
    assert record["paper_gate_passed_variants"] == []
    assert set(record["results"]) == {
        "baseline_solid_wave",
        "solid_lumped",
        "pec_wave",
        "pec_lumped",
    }
    assert all(
        result["paper_gate_passed"] is False
        and len(result["curve_sha256"]) == 64
        for result in record["results"].values()
    )
    baseline_modes = record["results"]["baseline_solid_wave"][
        "observed_resonances"
    ]
    assert baseline_modes[0]["frequency_ghz"] == 2.482
    assert baseline_modes[1] is None
    assert record["results"]["baseline_solid_wave"][
        "observed_first_minus_10db_band_ghz"
    ] == pytest.approx([2.478674324580235, 2.486260436389462])
    assert "complete paper gate" in record["policy"]
