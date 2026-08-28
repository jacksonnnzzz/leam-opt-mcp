from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from antenna_mcp.validation import ValidationBenchmark


CASE_DIR = Path(__file__).resolve().parents[1] / "examples" / "validation" / "kaur_split_ring_monopole"


def _module(filename: str, name: str):
    path = CASE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    local_dependencies = ("paper_targets", "reference_model")
    previous_modules = {
        dependency: sys.modules.pop(dependency, None)
        for dependency in local_dependencies
    }
    sys.path.insert(0, str(CASE_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CASE_DIR))
        for dependency, previous in previous_modules.items():
            sys.modules.pop(dependency, None)
            if previous is not None:
                sys.modules[dependency] = previous
    return module


def test_kaur_three_benchmarks_validate_and_never_mix_srs_parameters():
    model = _module("reference_model.py", "kaur_model_contract")
    names = {
        "baseline": "benchmark_baseline.json",
        "wlan_notch": "benchmark_wlan_notch.json",
        "xband_notch": "benchmark_xband_notch.json",
    }
    contracts = {
        case: ValidationBenchmark.model_validate_json((CASE_DIR / filename).read_text("utf-8"))
        for case, filename in names.items()
    }
    for case, contract in contracts.items():
        assert contract.reference["case"] == case
        assert contract.reference["parameters"] == {
            name: {"value": item["value"], "unit": item["unit"]}
            for name, item in model.paper_parameters(case).items()
        }
        assert contract.reference["objects"]
        assert contract.reference["operations"]
        assert contract.reference["solver"]["port"]["type"] == "lumped_port"
    assert not any("srs_" in name for name in contracts["baseline"].reference["parameters"])
    assert "srs_outer_radius_R1" in contracts["wlan_notch"].reference["parameters"]
    assert "srs_outer_radius_R1_prime" not in contracts["wlan_notch"].reference["parameters"]
    assert "srs_outer_radius_R1_prime" in contracts["xband_notch"].reference["parameters"]
    assert "srs_outer_radius_R1" not in contracts["xband_notch"].reference["parameters"]


def test_kaur_coordinates_enforce_connectivity_and_physical_cpw_port():
    model = _module("reference_model.py", "kaur_model_coordinates")
    coords = model.geometry_coordinates("wlan_notch")
    assert coords["feed_origin"][1] + coords["feed_size"][1] == pytest.approx(5.934)
    assert coords["stub_origin"][1] == pytest.approx(5.934)
    assert coords["stub_origin"][1] + coords["stub_size"][1] == pytest.approx(7.434)
    assert coords["patch_origin"][1] == pytest.approx(7.434)
    assert coords["patch_top_margin"] == pytest.approx(1.566)
    assert coords["port_origin"] == pytest.approx([-1.05, 0.0, 0.0])
    assert coords["port_size"] == pytest.approx([2.1, 1.6])
    assert np.asarray(coords["port_integration_line"]) == pytest.approx(
        np.asarray([[0.6, 0.0, 1.6], [1.05, 0.0, 1.6]])
    )
    assert coords["srs_center"] == pytest.approx([0.0, 7.434, 1.6])


class _Material:
    permittivity = None
    dielectric_loss_tangent = None


class _Materials:
    def exists_material(self, _name):
        return False

    def add_material(self, _name):
        return _Material()


class _Object:
    def __init__(self, name):
        self.name = name


class _Modeler:
    def __init__(self):
        self.object_names = []
        self.calls = []
        self.model_units = None

    def _create(self, operation, name, *args):
        self.calls.append((operation, name, *args))
        self.object_names.append(name)
        return _Object(name)

    def create_box(self, origin, size, **kwargs):
        return self._create("box", kwargs["name"], origin, size, kwargs["material"])

    def create_rectangle(self, orientation, origin, size, **kwargs):
        return self._create("rectangle", kwargs["name"], orientation, origin, size, kwargs["material"])

    def create_circle(self, orientation, center, radius, **kwargs):
        return self._create("circle", kwargs["name"], orientation, center, radius, kwargs["material"])

    def unite(self, objects):
        names = [item.name for item in objects]
        self.calls.append(("unite", names))
        return _Object(names[0])

    def subtract(self, blank, tool, **kwargs):
        blank_name = getattr(blank, "name", blank)
        tool_name = getattr(tool, "name", tool)
        self.calls.append(("subtract", blank_name, tool_name, kwargs))
        return True

    def create_region(self, padding, **kwargs):
        return self._create("region", kwargs["name"], padding, kwargs)


class _Setup:
    def __init__(self):
        self.sweep = None

    def create_frequency_sweep(self, **kwargs):
        self.sweep = kwargs
        return object()


class _Hfss:
    def __init__(self):
        self.modeler = _Modeler()
        self.materials = _Materials()
        self.setup_names = []
        self.perfect = []
        self.port = None
        self.setup = _Setup()

    def assign_perfecte_to_sheets(self, assignment, name):
        self.perfect.append((assignment, name))
        return object()

    def lumped_port(self, assignment, **kwargs):
        self.port = (assignment, kwargs)
        return object()

    def assign_radiation_boundary_to_objects(self, assignment, **kwargs):
        return object()

    def create_setup(self, **kwargs):
        self.setup_kwargs = kwargs
        return self.setup


@pytest.mark.parametrize("case", ["baseline", "wlan_notch", "xband_notch"])
def test_kaur_builder_preserves_united_names_and_builds_each_case(case):
    model = _module("reference_model.py", f"kaur_model_build_{case}")
    hfss = _Hfss()
    assert model.build_reference(hfss, case) is hfss
    unite_calls = [call for call in hfss.modeler.calls if call[0] == "unite"]
    assert unite_calls == [
        ("unite", ["Patch", "MatchingStub", "FeedLine"]),
        ("unite", ["LeftGround", "RightGround"]),
    ]
    assert ("Patch", "RadiatorPEC") in hfss.perfect
    assert ("LeftGround", "GroundPEC") in hfss.perfect
    port_sheet_call = next(
        call
        for call in hfss.modeler.calls
        if call[0] == "rectangle" and call[1] == "LumpedPortSheet"
    )
    assert port_sheet_call[2] == "XZ"
    assert port_sheet_call[3] == pytest.approx([-1.05, 0.0, 0.0])
    assert port_sheet_call[4] == pytest.approx([1.6, 2.1])
    _, port = hfss.port
    assert np.asarray(port["integration_line"]) == pytest.approx(
        np.asarray([[0.6, 0.0, 1.6], [1.05, 0.0, 1.6]])
    )
    subtracts = [call for call in hfss.modeler.calls if call[0] == "subtract"]
    if case == "baseline":
        assert subtracts == []
    else:
        assert subtracts[-1][1:] == ("Patch", "SRSOuterTool", {"keep_originals": False})


def _write_curve(path: Path, case: str, *, bad_notch: bool = False) -> Path:
    target = _module("paper_targets.py", f"kaur_targets_curve_{case}")
    frequency = np.linspace(3.0, 12.0, 1801)
    values = np.full_like(frequency, -12.0)
    if case != "baseline":
        center = 5.3 if case == "wlan_notch" else 7.4
        band = target.TARGETS[case]["notch_band_ghz"]
        frequency = np.unique(np.concatenate((frequency, np.asarray([*band, center]))))
        values = np.full_like(frequency, -12.0)
        if not bad_notch:
            peak_vswr = target.TARGETS[case]["simulated_peak_vswr"]
            peak_gamma = (peak_vswr - 1.0) / (peak_vswr + 1.0)
            peak_db = 20.0 * math.log10(peak_gamma)
            mask = (frequency >= band[0]) & (frequency <= band[1])
            notch_frequency = frequency[mask]
            normalized = np.where(
                notch_frequency <= center,
                (notch_frequency - center) / (center - band[0]),
                (notch_frequency - center) / (band[1] - center),
            )
            values[mask] = target.VSWR_TWO_S11_DB + (
                peak_db - target.VSWR_TWO_S11_DB
            ) * (1.0 - normalized**2)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(zip(frequency, values))
    return path


@pytest.mark.parametrize("case", ["baseline", "wlan_notch", "xband_notch"])
def test_kaur_paper_evaluator_passes_physical_curves_and_shared_edges(tmp_path, case):
    targets = _module("paper_targets.py", f"kaur_targets_pass_{case}")
    curve = _write_curve(tmp_path / f"{case}.csv", case)
    report = targets.evaluate_paper_targets(curve, case)
    assert report["passed"] is True
    assert report["quantity"] == "dB(S11) self-reflection"
    if case != "baseline":
        assert report["observed"]["notch"]["peak_vswr"] == pytest.approx(
            targets.TARGETS[case]["simulated_peak_vswr"]
        )


def test_kaur_paper_evaluator_rejects_low_reflection_in_notch(tmp_path):
    targets = _module("paper_targets.py", "kaur_targets_fail")
    curve = _write_curve(tmp_path / "bad.csv", "wlan_notch", bad_notch=True)
    report = targets.evaluate_paper_targets(curve, "wlan_notch")
    assert report["passed"] is False
    assert any(
        check["path"] == "notch.entire_reported_band_at_or_above_vswr_2"
        and not check["passed"]
        for check in report["checks"]
    )


def test_kaur_paper_evaluator_rejects_nonfinite_and_nonmonotonic_csv(tmp_path):
    targets = _module("paper_targets.py", "kaur_targets_numeric")
    for filename, rows, message in [
        ("nan.csv", [(3, -12), (4, math.nan), (12, -12)], "non-finite"),
        ("order.csv", [(3, -12), (5, -12), (4, -12), (12, -12)], "strictly increasing"),
    ]:
        path = tmp_path / filename
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["frequency_ghz", "s11_db"])
            writer.writerows(rows)
        with pytest.raises(ValueError, match=message):
            targets.evaluate_paper_targets(path, "baseline")


def test_kaur_runner_export_is_db_self_reflection_and_unit_safe(tmp_path):
    runner = _module("run_reference.py", "kaur_runner_export")

    class Data:
        primary_sweep = "Freq"
        units_sweeps = {"Freq": "MHz"}

        def get_expression_data(self, expression, formula):
            assert formula == "real"
            return [3000, 6000, 12000], [-12, -20, -12]

    class Post:
        def get_solution_data(self, **kwargs):
            return Data()

    class Hfss:
        post = Post()

        def get_traces_for_plot(self, **kwargs):
            assert kwargs["category"] == "dB(S"
            return ["dB(S(LumpedPort1,LumpedPort1))"]

    output = tmp_path / "curve.csv"
    result = runner._export_s11(Hfss(), output)
    assert result["expression"] == "dB(S(LumpedPort1,LumpedPort1))"
    assert output.read_text("utf-8").splitlines()[-1].startswith("12.0,")
    assert runner._is_db_self_reflection("S(P1,P1)") is False


def test_kaur_assumptions_explicitly_separate_unresolved_values():
    payload = json.loads((CASE_DIR / "assumptions.json").read_text("utf-8"))
    assert "conductor material, conductivity, and thickness" in payload["unresolved_from_paper"]
    assert len(payload["case_isolation"]) == 3


def test_kaur_frozen_hfss_outcomes_preserve_all_three_negative_results():
    path = CASE_DIR / "reference_data" / "hfss_reference_outcomes_2026_08_26.json"
    payload = json.loads(path.read_text("utf-8"))
    assert payload["environment"] == {"aedt": "2025.1", "pyaedt": "0.26.3"}
    assert payload["execution_correction"]["corrected_api_dimensions"] == [1.6, 2.1]
    assert payload["status"] == "all_three_solved_fail_paper_gate"
    assert payload["paper_gate_passed_cases"] == []
    assert set(payload["results"]) == {"baseline", "wlan_notch", "xband_notch"}
    assert all(not result["paper_gate_passed"] for result in payload["results"].values())
    assert {
        case: result["curve_sha256"]
        for case, result in payload["results"].items()
    } == {
        "baseline": "24ac959f7c666c970758ec7ab63f6903e191804c5e63bf3ceb58e0beba2b1fe4",
        "wlan_notch": "2fbcf876f09f8895d43632a4e77440fc559912252825d76d6528159e7e21c597",
        "xband_notch": "9792e67800dcffa1a9395fc3dc3df13c2b93b74b35585edede457a911d2210a7",
    }
