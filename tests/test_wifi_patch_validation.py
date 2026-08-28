from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from antenna_mcp.validation import ValidationBenchmark


ROOT = Path(__file__).parents[1] / "examples" / "validation" / "wifi_patch_5250"


def _reference_module():
    spec = importlib.util.spec_from_file_location("wifi_patch_5250_reference", ROOT / "reference_model.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_module():
    spec = importlib.util.spec_from_file_location("wifi_patch_5250_runner", ROOT / "run_reference.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous_reference = sys.modules.pop("reference_model", sentinel)
    sys.path.insert(0, str(ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT))
        sys.modules.pop("reference_model", None)
        if previous_reference is not sentinel:
            sys.modules["reference_model"] = previous_reference
    return module


def test_wifi_patch_benchmark_matches_offline_reference_contract():
    benchmark = ValidationBenchmark.model_validate_json(
        (ROOT / "benchmark.json").read_text(encoding="utf-8")
    )
    module = _reference_module()
    model_parameters = module.paper_parameters()
    benchmark_parameters = benchmark.reference["parameters"]

    assert benchmark.benchmark_id == "wifi_patch_5250"
    assert set(model_parameters) == set(benchmark_parameters)
    for name, expected in benchmark_parameters.items():
        assert model_parameters[name]["value"] == expected["value"]
        assert model_parameters[name]["unit"] == expected["unit"]

    coordinates = module.geometry_coordinates()
    assert coordinates["substrate_size"] == [25.92, 34.44, 1.5]
    assert coordinates["reflector_size"] == [65.92, 34.44]
    assert coordinates["patch_size"] == [12.55, 17.22]
    assert coordinates["probe_origin"] == pytest.approx([-3.385, 0.0, 0.0])
    assert benchmark.reference["engineering_assumptions"]["coordinate_mapping"][
        "feed_location"
    ] == ["-Lp/2+Xp", "0mm", "0mm"]


def test_wifi_patch_assumptions_are_explicit_and_separate():
    assumptions = json.loads((ROOT / "assumptions.json").read_text(encoding="utf-8"))
    module = _reference_module()

    assert assumptions["benchmark_id"] == "wifi_patch_5250"
    assert "conductor material, conductivity, and thickness" in assumptions["unresolved_from_paper"]
    assert module.engineering_assumptions()["conductor_model"] == "zero_thickness_pec_sheets"
    assert "probe_inner_radius_mm" not in module.paper_parameters()


def test_wifi_recorded_source_correction_is_hash_frozen_and_fail_closed():
    record = json.loads(
        (
            ROOT
            / "reference_data"
            / "hfss_reference_outcome_2026_08_26.json"
        ).read_text("utf-8")
    )

    assert record["status"] == "solved_fail_paper_gate_after_evidence_correction"
    rejected = record["rejected_center_referenced_xp_translation"]
    corrected = record["edge_referenced_xp_translation"]
    assert rejected["paper_gate_passed"] is False
    assert corrected["paper_gate_passed"] is False
    assert len(rejected["curve_sha256"]) == len(corrected["curve_sha256"]) == 64
    assert corrected["feed_coordinate_expression"] == "x=-Lp/2+Xp"
    assert corrected["evaluated_feed_x_mm"] == pytest.approx(-3.385)
    assert corrected["maximum_s11_in_5p15_to_5p35_db"] == pytest.approx(
        -7.106688591506792
    )
    assert "Do not use either curve" in record["policy"]


def test_wifi_patch_reference_import_does_not_require_pyaedt():
    module = _reference_module()
    assert callable(module.build_reference)
    assert module.DESIGN_NAME == "ElGendySinglePatch5250_EdgeReferencedXp"


class _FakeFace:
    def __init__(self, face_id, area):
        self.id = face_id
        self.area = area


class _FakeObject:
    def __init__(self, name, *, faces=None, bottom_face=None):
        self.name = name
        self.faces = faces or [_FakeFace(1, 1.0)]
        self.bottom_face_z = bottom_face or _FakeFace(2, 1.0)


class _FakeMaterial:
    def __init__(self):
        self.permittivity = None
        self.dielectric_loss_tangent = None


class _FakeMaterials:
    def __init__(self):
        self.created = None

    def exists_material(self, name):
        return False

    def add_material(self, name):
        self.created = _FakeMaterial()
        return self.created


class _FakeModeler:
    def __init__(self):
        self.object_names = []
        self.calls = []
        self.model_units = None
        self.outer = None

    def create_box(self, origin, size, **kwargs):
        self.calls.append(("create_box", origin, size, kwargs))
        return _FakeObject(kwargs["name"])

    def create_rectangle(self, orientation, origin, size, **kwargs):
        self.calls.append(("create_rectangle", orientation, origin, size, kwargs))
        return _FakeObject(kwargs["name"])

    def create_circle(self, orientation, origin, radius, **kwargs):
        self.calls.append(("create_circle", orientation, origin, radius, kwargs))
        return _FakeObject(kwargs["name"])

    def subtract(self, blank, tool, **kwargs):
        self.calls.append(("subtract", blank, tool, kwargs))
        return True

    def unite(self, assignments):
        self.calls.append(("unite", assignments))
        return True

    def create_cylinder(self, orientation, origin, radius, height, **kwargs):
        self.calls.append(("create_cylinder", orientation, origin, radius, height, kwargs))
        if kwargs["name"] == "ProbeFeedOuter":
            self.outer = _FakeObject(
                kwargs["name"],
                faces=[_FakeFace(801, 3.0), _FakeFace(802, 90.0), _FakeFace(803, 3.0)],
                bottom_face=_FakeFace(804, 3.0),
            )
            return self.outer
        return _FakeObject(kwargs["name"])

    def create_region(self, padding, **kwargs):
        self.calls.append(("create_region", padding, kwargs))
        return _FakeObject(kwargs["name"])


class _FakeSetup:
    def __init__(self):
        self.sweep_call = None

    def create_frequency_sweep(self, **kwargs):
        self.sweep_call = kwargs
        return SimpleNamespace(name=kwargs["name"])


class _FakeHfssBuild:
    def __init__(self):
        self.modeler = _FakeModeler()
        self.materials = _FakeMaterials()
        self.setup_names = []
        self.perfect_e_calls = []
        self.wave_port_call = None
        self.radiation_call = None
        self.setup_call = None
        self.setup = _FakeSetup()

    def assign_perfecte_to_sheets(self, assignment, name):
        self.perfect_e_calls.append((assignment, name))
        return SimpleNamespace(name=name)

    def wave_port(self, assignment, **kwargs):
        self.wave_port_call = (assignment, kwargs)
        return SimpleNamespace(name=kwargs["name"])

    def assign_radiation_boundary_to_objects(self, assignment, **kwargs):
        self.radiation_call = (assignment, kwargs)
        return SimpleNamespace(name=kwargs["name"])

    def create_setup(self, **kwargs):
        self.setup_call = kwargs
        return self.setup


def test_wifi_patch_build_uses_pyaedt_026_compatible_feed_and_region_contract():
    module = _reference_module()
    hfss = _FakeHfssBuild()

    assert module.build_reference(hfss) is hfss

    aperture = next(call for call in hfss.modeler.calls if call[0] == "create_circle")
    assert aperture[1] == "XY"
    assert aperture[2] == pytest.approx([-3.385, 0.0, 0.0])
    assert aperture[3] == 1.225
    subtraction = next(
        call
        for call in hfss.modeler.calls
        if call[0] == "subtract" and call[1].name == "Reflector"
    )
    assert subtraction[1].name == "Reflector"
    assert subtraction[2].name == "FeedApertureTool"
    assert subtraction[3] == {"keep_originals": False}
    probe_bore = next(
        call
        for call in hfss.modeler.calls
        if call[0] == "subtract" and call[1].name == "Substrate"
    )
    assert probe_bore[2].name == "Probe"
    assert probe_bore[3] == {"keep_originals": True}

    assert (802, "ProbePEC") in hfss.perfect_e_calls
    port_face, port_options = hfss.wave_port_call
    assert port_face is hfss.modeler.outer.bottom_face_z
    assert port_options == {
        "reference": "ProbeFeedOuter",
        "create_pec_cap": True,
        "impedance": 50.0,
        "name": "ProbePort",
    }
    region_call = next(call for call in hfss.modeler.calls if call[0] == "create_region")
    assert region_call[1] == [15.0] * 6
    assert region_call[2] == {"pad_type": "Absolute Offset", "name": "Region"}
    assert hfss.radiation_call[1] == {"name": "Radiation"}
    assert hfss.setup_call["Frequency"] == "5.25GHz"
    assert hfss.setup.sweep_call["start_frequency"] == 5.0
    assert hfss.setup.sweep_call["stop_frequency"] == 5.5
    assert hfss.setup.sweep_call["num_of_freq_points"] == 501


def test_wifi_assumption_space_locks_paper_parameters_and_plans_only_unresolved_fields():
    from antenna_mcp.assumption_search import load_assumption_space, plan_assumption_trials

    module = _reference_module()
    payload = load_assumption_space(ROOT / "assumption_space.json")
    assert payload["paper_parameters"] == module.paper_parameters()
    assert payload["baseline_assumptions"] == module.engineering_assumptions()
    assert set(payload["search_space"]).isdisjoint(payload["paper_parameters"])
    trials = plan_assumption_trials(payload)
    assert len(trials) == 10
    assert all(len(trial["changed_assumptions"]) == 1 for trial in trials)


def test_wifi_frozen_assumption_search_has_ten_converged_nonpassing_trials():
    record = json.loads(
        (
            ROOT
            / "reference_data"
            / "engineering_assumption_search_2026_08_27.json"
        ).read_text(encoding="utf-8")
    )
    summary = record["summary"]
    trials = record["completed_trials"]

    assert summary == {
        "trials_planned": 10,
        "trials_completed_with_em_evidence": 10,
        "trials_failed": 0,
        "completed_trials_passing_paper_gate": 0,
        "status": "completed_no_passing_variant",
        "best_completed_trial_id": "ast-cec96cb39f11",
    }
    assert len(trials) == 10
    assert [trial["rank"] for trial in trials] == list(range(1, 11))
    assert all(trial["convergence"]["sweep_converged"] for trial in trials)
    assert all(
        trial["convergence"]["final_max_magnitude_delta_s"] <= 0.02
        for trial in trials
    )
    assert all(trial["paper_gate_passed"] is False for trial in trials)
    assert all(len(trial["s11_sha256"]) == 64 for trial in trials)
    assert trials[0]["metrics"]["maximum_s11_in_target_band_db"] == pytest.approx(
        -8.02029313315898
    )


def test_wifi_v2_interaction_space_freezes_paper_values_and_omits_v1_singletons():
    from antenna_mcp.assumption_search import (
        json_sha256,
        load_assumption_space,
        plan_assumption_trials,
    )

    v1 = load_assumption_space(ROOT / "assumption_space.json")
    v2 = load_assumption_space(ROOT / "assumption_space_v2.json")
    trials = plan_assumption_trials(v2)

    assert v2["paper_parameters"] == v1["paper_parameters"]
    assert json_sha256(v2["paper_parameters"]) == (
        "7cafc6ee10fb4b08e00818febeb9192d59399942b28eda5ca8050ec02b5def13"
    )
    assert len(trials) == 11
    assert all(2 <= len(trial["changed_assumptions"]) <= 4 for trial in trials)
    assert all(
        set(trial["changed_assumptions"])
        <= {
            "radiation_padding_mm",
            "probe_outer_radius_mm",
            "feed_length_mm",
            "probe_inner_radius_mm",
        }
        for trial in trials
    )


def test_recorded_wifi_v2_interaction_results_are_complete_converged_and_negative():
    record = json.loads(
        (ROOT / "reference_data" / "engineering_assumption_interactions_2026_08_28.json")
        .read_text(encoding="utf-8")
    )

    assert record["integrity"]["paper_parameters_sha256"] == (
        "7cafc6ee10fb4b08e00818febeb9192d59399942b28eda5ca8050ec02b5def13"
    )
    assert record["integrity"]["paper_parameters_modified"] is False
    assert record["summary"] == {
        "trials_planned": 11,
        "trials_completed_with_em_evidence": 11,
        "trials_converged": 11,
        "trials_failed": 0,
        "completed_trials_passing_paper_gate": 0,
        "status": "completed_no_passing_variant",
        "best_completed_trial_id": "ast-d851d471eb78",
    }
    trials = record["completed_trials"]
    assert [trial["rank"] for trial in trials] == list(range(1, 12))
    assert all(trial["convergence"]["sweep_converged"] for trial in trials)
    assert all(
        trial["convergence"]["final_max_magnitude_delta_s"] <= 0.02
        for trial in trials
    )
    assert all(trial["paper_gate_passed"] is False for trial in trials)
    assert all(len(trial["s11_sha256"]) == 64 for trial in trials)
    assert trials[0]["metrics"]["maximum_s11_in_target_band_db"] == pytest.approx(
        -8.492023097516151
    )
    assert record["comparison_to_v1"]["improvement_db"] == pytest.approx(
        0.471729964357171
    )


def test_wifi_finite_copper_and_ptfe_assumptions_change_only_labelled_implementation_details():
    module = _reference_module()
    finite = module.engineering_assumptions()
    finite["conductor_model"] = "finite_copper_0p035mm"
    hfss = _FakeHfssBuild()
    module.build_reference(hfss, assumptions=finite)
    conductor_boxes = {
        call[3]["name"]: call
        for call in hfss.modeler.calls
        if call[0] == "create_box" and call[3]["name"] in {"Reflector", "Patch"}
    }
    assert conductor_boxes["Reflector"][2][2] == pytest.approx(0.035)
    assert conductor_boxes["Patch"][2][2] == pytest.approx(0.035)
    finite_unite = next(call for call in hfss.modeler.calls if call[0] == "unite")
    assert [item.name for item in finite_unite[1]] == ["Patch", "Probe"]
    assert not any(name in {"ReflectorPEC", "PatchPEC"} for _, name in hfss.perfect_e_calls)
    assert module.paper_parameters()["patch_length_Lp"]["value"] == 12.55

    ptfe = module.engineering_assumptions()
    ptfe["coax_dielectric"] = "ptfe_er2p1"
    hfss = _FakeHfssBuild()
    module.build_reference(hfss, assumptions=ptfe)
    assert hfss.materials.created.permittivity == 2.1
    outer = next(
        call for call in hfss.modeler.calls
        if call[0] == "create_cylinder" and call[5]["name"] == "ProbeFeedOuter"
    )
    assert outer[5]["material"] == "PTFE_Assumption_er2p1"


class _FakeSolutionData:
    def __init__(self, frequencies, values, *, unit="GHz", primary_sweep="Freq"):
        self.frequencies = frequencies
        self.values = values
        self.primary_sweep = primary_sweep
        self.units_sweeps = {primary_sweep: unit} if unit is not None else {}
        self.call = None

    def get_expression_data(self, expression, formula):
        self.call = (expression, formula)
        return self.frequencies, self.values


class _FakePost:
    def __init__(self, data):
        self.data = data
        self.call = None

    def get_solution_data(self, **kwargs):
        self.call = kwargs
        return self.data


class _FakeHfssExport:
    design_name = "ElGendySinglePatch5250"

    def __init__(self, traces, data):
        self.traces = traces if isinstance(traces, list) else [traces]
        self.trace_call = None
        self.post = _FakePost(data)

    def get_traces_for_plot(self, **kwargs):
        self.trace_call = kwargs
        return self.traces


def test_wifi_patch_s11_export_explicitly_requests_db_and_checks_band_edges(tmp_path):
    module = _run_module()
    expression = "dB(S(ProbePort_T1,ProbePort_T1))"
    data = _FakeSolutionData(
        [5.0, 5.14, 5.16, 5.25, 5.34, 5.36, 5.5],
        [-2.0, -9.2, -11.2, -20.0, -11.2, -9.2, -2.0],
    )
    hfss = _FakeHfssExport(expression, data)
    output = tmp_path / "s11.csv"

    result = module._export_s11(hfss, output)

    assert hfss.trace_call == {
        "get_self_terms": True,
        "get_mutual_terms": False,
        "category": "dB(S",
    }
    assert data.call == (expression, "real")
    assert result["maximum_s11_in_5p15_to_5p35_db"] == pytest.approx(-10.2)
    assert result["paper_band_target_passed"] is True
    assert result["resonance_search_window_ghz"] == [5.15, 5.35]
    assert result["resonant_frequency_ghz"] == 5.25
    assert output.read_text(encoding="utf-8").splitlines()[0] == "frequency_ghz,s11_db"


def test_wifi_patch_s11_export_rejects_non_db_expression(tmp_path):
    module = _run_module()
    data = _FakeSolutionData([5.0, 5.25, 5.5], [-2.0, -20.0, -2.0])
    hfss = _FakeHfssExport("S(ProbePort,ProbePort)", data)

    with pytest.raises(RuntimeError, match="exactly one dB self-reflection"):
        module._export_s11(hfss, tmp_path / "invalid.csv")


def test_wifi_patch_s11_export_uses_solution_units_and_ignores_out_of_band_minimum(tmp_path):
    module = _run_module()
    expression = "dB(S(ProbePort_T1,ProbePort_T1))"
    data = _FakeSolutionData(
        [5000, 5140, 5160, 5250, 5340, 5360, 5500],
        [-40.0, -9.2, -11.2, -20.0, -11.2, -9.2, -2.0],
        unit="MHz",
    )
    output = tmp_path / "mhz_s11.csv"

    result = module._export_s11(_FakeHfssExport(expression, data), output)

    assert result["resonant_frequency_ghz"] == 5.25
    assert result["minimum_s11_db"] == -20.0
    rows = output.read_text(encoding="utf-8").splitlines()
    assert rows[1].startswith("5.0,")
    assert rows[-1].startswith("5.5,")


def test_wifi_patch_s11_export_requires_exactly_one_self_reflection(tmp_path):
    module = _run_module()
    data = _FakeSolutionData([5.0, 5.25, 5.5], [-2.0, -20.0, -2.0])
    traces = ["dB(S(P1,P1))", "dB(S(P2,P2))", "dB(S(P1,P2))"]

    with pytest.raises(RuntimeError, match="exactly one dB self-reflection"):
        module._export_s11(_FakeHfssExport(traces, data), tmp_path / "ambiguous.csv")


@pytest.mark.parametrize(
    ("frequencies", "values", "message"),
    [
        ([5.0, 5.25, 5.25, 5.5], [-2.0, -20.0, -18.0, -2.0], "non-increasing"),
        ([5.0, 5.25, 5.5], [-2.0, float("nan"), -2.0], "non-finite"),
        ([5.0, float("inf"), 5.5], [-2.0, -20.0, -2.0], "non-finite"),
    ],
)
def test_wifi_patch_s11_export_rejects_invalid_numeric_curves(
    tmp_path, frequencies, values, message
):
    module = _run_module()
    data = _FakeSolutionData(frequencies, values)
    output = tmp_path / "invalid_numbers.csv"

    with pytest.raises(RuntimeError, match=message):
        module._export_s11(
            _FakeHfssExport("dB(S(ProbePort_T1,ProbePort_T1))", data), output
        )
    assert not output.exists()


def test_wifi_patch_numeric_frequency_requires_solution_unit(tmp_path):
    module = _run_module()
    data = _FakeSolutionData([5.0, 5.25, 5.5], [-2.0, -20.0, -2.0], unit=None)

    with pytest.raises(ValueError, match="unable to convert numeric frequency"):
        module._export_s11(
            _FakeHfssExport("dB(S(ProbePort_T1,ProbePort_T1))", data),
            tmp_path / "missing_unit.csv",
        )


def test_wifi_patch_db_self_reflection_parser_is_strict():
    module = _run_module()

    assert module._is_db_self_reflection(" dB( S( Port_T1 , Port_T1 ) ) ") is True
    assert module._is_db_self_reflection("dB(S(P1,P2))") is False
    assert module._is_db_self_reflection("mag(S(P1,P1))") is False


def test_wifi_patch_attached_flow_requests_new_design_and_never_closes_desktop(
    monkeypatch, capsys
):
    module = _run_module()
    calls = {}

    class FakePreflightDesktop:
        pass

    class FakeHfss:
        odesign = object()
        project_name = "Project8"
        design_name = module.DESIGN_NAME
        project_file = r"D:\projects\Project8.aedt"
        aedt_version_id = "2025.1"

        def save_project(self, *args):
            return True

        def release_desktop(self, **kwargs):
            calls["release"] = kwargs

    def fake_hfss(**kwargs):
        calls["hfss_options"] = kwargs
        return FakeHfss()

    fake_preflight = FakePreflightDesktop()
    monkeypatch.setattr(module, "prepare_pyaedt_environment", lambda: {})
    monkeypatch.setattr(module, "temporary_multi_desktop", nullcontext)
    monkeypatch.setattr(
        module,
        "_preflight_existing_desktop",
        lambda **kwargs: (fake_preflight, "Project8"),
    )
    monkeypatch.setattr(module, "temporary_grpc_session_probe", nullcontext)
    monkeypatch.setattr(
        module,
        "_release_desktop_only",
        lambda desktop, **kwargs: calls.setdefault("preflight_released", desktop),
    )
    monkeypatch.setattr(module, "ensure_strict_existing_attachment", lambda app, port: None)
    monkeypatch.setattr(module, "build_reference", lambda app: app)

    fake_ansys = ModuleType("ansys")
    fake_aedt = ModuleType("ansys.aedt")
    fake_core = ModuleType("ansys.aedt.core")
    fake_core.Hfss = fake_hfss
    fake_ansys.aedt = fake_aedt
    fake_aedt.core = fake_core
    monkeypatch.setitem(sys.modules, "ansys", fake_ansys)
    monkeypatch.setitem(sys.modules, "ansys.aedt", fake_aedt)
    monkeypatch.setitem(sys.modules, "ansys.aedt.core", fake_core)
    assert module.main(
        ["--version", "2025.1", "--grpc-port", "50051", "--active-project", "Project8"]
    ) == 0

    assert calls["preflight_released"] is fake_preflight
    assert calls["hfss_options"]["project"] == "Project8"
    assert calls["hfss_options"]["design"] == module.DESIGN_NAME
    assert calls["hfss_options"]["solution_type"] == "Terminal"
    assert calls["hfss_options"]["new_desktop"] is False
    assert calls["hfss_options"]["port"] == 50051
    assert calls["release"] == {"close_projects": False, "close_desktop": False}
    assert json.loads(capsys.readouterr().out)["design"] == module.DESIGN_NAME
