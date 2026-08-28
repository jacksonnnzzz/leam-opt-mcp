from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from antenna_mcp.validation import ValidationBenchmark, ValidationService
from antenna_mcp.workflow_cli import main as workflow_main
from antenna_mcp.workspace import WorkspaceStore


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _benchmark(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "benchmark.json",
        {
            "schema_version": "1.0",
            "benchmark_id": "patch_test",
            "title": "Patch test",
            "source": {
                "title": "Official test source",
                "url": "https://example.com/official",
                "source_type": "official_example",
            },
            "reference": {
                "parameters": {
                    "W": {"value": 10.0, "unit": "mm"},
                    "L": {"value": 9.0, "unit": "mm"},
                },
                "objects": {
                    "patch": {"primitive": "box", "material": "copper"},
                },
                "operations": [
                    {"order": 1, "operation": "create", "target": "patch"},
                ],
                "solver": {
                    "solution_type": "Terminal",
                    "sweep": {"start": 8.0, "stop": 12.0},
                },
            },
            "default_absolute_tolerance": 0.0,
            "tolerance_by_path": {"parameters.*.value": 0.01},
            "s11": {
                "minimum_overlap_points": 20,
                "resonance_relative_error_max": 0.01,
                "bandwidth_relative_error_max": 0.05,
                "curve_rmse_db_max": 1.0,
            },
        },
    )


def _candidate(tmp_path: Path, *, width: float = 10.0) -> Path:
    return _write_json(
        tmp_path / "candidate.json",
        {
            "schema_version": "1.0",
            "benchmark_id": "patch_test",
            "provenance": {"kind": "test"},
            "model": {
                "parameters": {
                    "W": {"value": width, "unit": "mm"},
                    "L": {"value": 9.0, "unit": "mm"},
                },
                "objects": {
                    "patch": {"primitive": "box", "material": "copper"},
                },
                "operations": [
                    {"order": 1, "operation": "create", "target": "patch"},
                ],
                "solver": {
                    "solution_type": "Terminal",
                    "sweep": {"start": 8.0, "stop": 12.0},
                },
            },
        },
    )


def _target_benchmark(tmp_path: Path, targets: list[dict], *, rmse_max: float = 1.0) -> Path:
    path = _benchmark(tmp_path)
    payload = json.loads(path.read_text("utf-8"))
    payload["s11"]["targets"] = targets
    payload["s11"]["curve_rmse_db_max"] = rmse_max
    return _write_json(path, payload)


def _curve(path: Path, frequencies: np.ndarray, values: np.ndarray) -> Path:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frequency_ghz", "s11_db"])
        writer.writerows(zip(frequencies, values))
    return path


def test_contract_only_passes_without_claiming_electromagnetic_validity(tmp_path):
    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path),
        contract_only=True,
    )

    assert report["status"] == "passed"
    assert report["quality_gate_passed"] is True
    assert report["claims"] == {
        "geometry_and_solver_contract_validated": True,
        "electromagnetic_results_validated": False,
    }
    assert report["s11"]["status"] == "skipped_contract_only"


def test_full_validation_is_incomplete_without_both_curves(tmp_path):
    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path),
    )

    assert report["status"] == "incomplete"
    assert report["quality_gate_passed"] is False
    assert report["s11"]["missing"] == ["reference_s11", "candidate_s11"]


def test_contract_reports_numeric_failure_with_path_and_tolerance(tmp_path):
    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path, width=10.02),
        contract_only=True,
    )

    assert report["status"] == "failed"
    failed = [check for check in report["contract"]["checks"] if not check["passed"]]
    assert [check["path"] for check in failed] == ["parameters.W.value"]
    assert failed[0]["tolerance"] == 0.01


def test_full_s11_validation_compares_resonance_bandwidth_and_curve(tmp_path):
    frequencies = np.linspace(8.0, 12.0, 81)
    reference_values = -2.0 - 18.0 * np.exp(-((frequencies - 10.0) / 0.35) ** 2)
    candidate_values = reference_values + 0.1
    reference = _curve(tmp_path / "reference.csv", frequencies, reference_values)
    candidate_curve = _curve(tmp_path / "candidate.csv", frequencies, candidate_values)

    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path),
        reference_s11=reference,
        candidate_s11=candidate_curve,
    )

    assert report["status"] == "passed"
    assert report["claims"]["electromagnetic_results_validated"] is True
    assert "mode" not in report["s11"]
    assert "targets" not in report["s11"]
    assert {check["path"] for check in report["s11"]["checks"]} == {
        "s11.overlap_points",
        "s11.reference_overlap_fraction",
        "s11.resonance_relative_error",
        "s11.bandwidth_relative_error",
        "s11.curve_rmse_db",
    }


def test_multiband_targets_validate_each_resonance_passband_and_notch(tmp_path):
    frequencies = np.linspace(1.0, 8.0, 701)
    values = (
        -2.0
        - 18.0 * np.exp(-((frequencies - 2.0) / 0.18) ** 2)
        - 16.0 * np.exp(-((frequencies - 5.0) / 0.24) ** 2)
    )
    benchmark = _target_benchmark(
        tmp_path,
        [
            {"name": "low", "kind": "resonance", "window_ghz": [1.7, 2.3]},
            {"name": "high", "kind": "resonance", "window_ghz": [4.6, 5.4]},
            {
                "name": "matched_band",
                "kind": "passband",
                "window_ghz": [1.9, 2.1],
                "threshold_db": -10.0,
            },
            {
                "name": "rejection_notch",
                "kind": "notch",
                "window_ghz": [3.0, 3.8],
                "threshold_db": -6.0,
            },
        ],
    )

    report = ValidationService().validate_manifest(
        benchmark,
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, values),
        candidate_s11=_curve(tmp_path / "candidate.csv", frequencies, values),
    )

    assert report["status"] == "passed"
    assert report["s11"]["mode"] == "targets"
    assert [item["name"] for item in report["s11"]["targets"]] == [
        "low",
        "high",
        "matched_band",
        "rejection_notch",
    ]
    assert all(item["passed"] for item in report["s11"]["targets"])
    low = report["s11"]["targets"][0]
    assert low["reference"]["local_minimum"]["frequency_ghz"] == pytest.approx(2.0)
    assert low["reference"]["band_edges_ghz"] is not None


def test_resonance_target_fails_individual_band_edge_errors(tmp_path):
    frequencies = np.linspace(1.0, 3.0, 401)
    reference_values = -2.0 - 18.0 * np.exp(-((frequencies - 2.0) / 0.16) ** 2)
    candidate_values = -2.0 - 18.0 * np.exp(-((frequencies - 2.0) / 0.28) ** 2)
    benchmark = _target_benchmark(
        tmp_path,
        [
            {
                "name": "fundamental",
                "kind": "resonance",
                "window_ghz": [1.6, 2.4],
                "band_edge_relative_error_max": 0.05,
            }
        ],
        rmse_max=100.0,
    )

    report = ValidationService().validate_manifest(
        benchmark,
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, reference_values),
        candidate_s11=_curve(tmp_path / "candidate.csv", frequencies, candidate_values),
    )

    assert report["status"] == "failed"
    target = report["s11"]["targets"][0]
    failed_paths = {check["path"] for check in target["checks"] if not check["passed"]}
    assert failed_paths == {
        "s11.targets[fundamental].lower_band_edge_relative_error",
        "s11.targets[fundamental].upper_band_edge_relative_error",
    }


@pytest.mark.parametrize("kind", ["stopband", "notch"])
def test_rejection_window_fails_when_any_s11_value_is_below_threshold(tmp_path, kind):
    frequencies = np.linspace(1.0, 5.0, 401)
    reference_values = np.full_like(frequencies, -2.0)
    candidate_values = -2.0 - 8.0 * np.exp(-((frequencies - 3.0) / 0.08) ** 2)
    benchmark = _target_benchmark(
        tmp_path,
        [
            {
                "name": "rejection",
                "kind": kind,
                "window_ghz": [2.8, 3.2],
                "threshold_db": -6.0,
            }
        ],
        rmse_max=100.0,
    )

    report = ValidationService().validate_manifest(
        benchmark,
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, reference_values),
        candidate_s11=_curve(tmp_path / "candidate.csv", frequencies, candidate_values),
    )

    assert report["status"] == "failed"
    target = report["s11"]["targets"][0]
    failed = [check for check in target["checks"] if not check["passed"]]
    assert [check["path"] for check in failed] == [
        "s11.targets[rejection].candidate_minimum_s11_db"
    ]


def test_passband_fails_when_any_s11_value_is_above_threshold(tmp_path):
    frequencies = np.linspace(1.0, 5.0, 401)
    reference_values = np.full_like(frequencies, -12.0)
    candidate_values = -12.0 + 5.0 * np.exp(-((frequencies - 3.0) / 0.08) ** 2)
    benchmark = _target_benchmark(
        tmp_path,
        [
            {
                "name": "service_band",
                "kind": "passband",
                "window_ghz": [2.8, 3.2],
                "threshold_db": -10.0,
            }
        ],
        rmse_max=100.0,
    )

    report = ValidationService().validate_manifest(
        benchmark,
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, reference_values),
        candidate_s11=_curve(tmp_path / "candidate.csv", frequencies, candidate_values),
    )

    assert report["status"] == "failed"
    target = report["s11"]["targets"][0]
    failed = [check for check in target["checks"] if not check["passed"]]
    assert [check["path"] for check in failed] == [
        "s11.targets[service_band].candidate_maximum_s11_db"
    ]


def test_resonance_target_requires_minimum_below_threshold(tmp_path):
    frequencies = np.linspace(1.0, 3.0, 401)
    reference_values = -2.0 - 18.0 * np.exp(-((frequencies - 2.0) / 0.16) ** 2)
    candidate_values = -2.0 - 6.0 * np.exp(-((frequencies - 2.0) / 0.16) ** 2)
    benchmark = _target_benchmark(
        tmp_path,
        [{"name": "weak", "kind": "resonance", "window_ghz": [1.6, 2.4]}],
        rmse_max=100.0,
    )

    report = ValidationService().validate_manifest(
        benchmark,
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, reference_values),
        candidate_s11=_curve(tmp_path / "candidate.csv", frequencies, candidate_values),
    )

    assert report["status"] == "failed"
    failed_paths = {
        check["path"]
        for check in report["s11"]["targets"][0]["checks"]
        if not check["passed"]
    }
    assert "s11.targets[weak].candidate_minimum_s11_db" in failed_paths


def test_s11_targets_reject_duplicate_names_and_invalid_windows(tmp_path):
    payload = json.loads(_benchmark(tmp_path).read_text("utf-8"))
    payload["s11"]["targets"] = [
        {"name": "band", "kind": "passband", "window_ghz": [2.0, 3.0]},
        {"name": "band", "kind": "resonance", "window_ghz": [4.0, 3.0]},
    ]

    with pytest.raises(ValidationError):
        ValidationBenchmark.model_validate(payload)


@pytest.mark.parametrize(
    ("section", "unknown_key"),
    [("source", "source_typo"), ("s11", "curve_rmse_db_typo")],
)
def test_benchmark_schema_rejects_unknown_nested_keys(tmp_path, section, unknown_key):
    payload = json.loads(_benchmark(tmp_path).read_text("utf-8"))
    payload[section][unknown_key] = "silently accepting this would weaken the gate"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ValidationBenchmark.model_validate(payload)


def test_generation_evidence_is_optional_metadata_not_a_contract_check(tmp_path):
    benchmark_path = _benchmark(tmp_path)
    payload = json.loads(benchmark_path.read_text("utf-8"))
    parsed_without_evidence = ValidationBenchmark.model_validate(payload)
    assert parsed_without_evidence.generation_evidence == {}

    baseline = ValidationService().validate_manifest(
        benchmark_path, _candidate(tmp_path), contract_only=True
    )
    payload["generation_evidence"] = {
        "producer": "PyAEDT 0.26.3",
        "formulas": {"x": "producer-side evidence only"},
    }
    _write_json(benchmark_path, payload)

    parsed_with_evidence = ValidationBenchmark.model_validate(payload)
    assert parsed_with_evidence.generation_evidence["producer"] == "PyAEDT 0.26.3"
    with_evidence = ValidationService().validate_manifest(
        benchmark_path, _candidate(tmp_path), contract_only=True
    )
    assert with_evidence["contract"] == baseline["contract"]
    assert all(
        not check["path"].startswith("generation_evidence")
        for check in with_evidence["contract"]["checks"]
    )


def test_official_probe_patch_records_pyaedt_generation_formulas():
    benchmark_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "validation"
        / "ansys_pyaedt_probe_patch"
        / "benchmark.json"
    )
    benchmark = ValidationBenchmark.model_validate_json(
        benchmark_path.read_text("utf-8")
    )
    evidence = benchmark.generation_evidence

    assert evidence["implementation"]["version"] == "0.26.3"
    assert evidence["stackup_layers"]["evaluated_z_ranges_mm"] == {
        "ground": [0.0, 0.035],
        "dielectric": [0.035, 0.535],
        "signal": [0.535, 0.57],
    }
    assert evidence["resize_around_patch"]["percentage_offset_argument"] == 0.25
    assert evidence["resize_around_patch"]["evaluated_mm"]["x_range"] == [
        -2.3925,
        11.9625,
    ]
    assert evidence["patch"]["evaluated_bounding_box_mm"]["z_range"] == [
        0.535,
        0.57,
    ]
    assert evidence["probe"]["evaluated_mm"]["origin"] == [7.105725, 0.0, 0.035]
    assert evidence["feed"]["outer"]["z_range_mm"] == [-0.112, 0.035]
    assert evidence["region"]["evaluated_bounding_box_mm"]["z_range"] == [
        -3.0,
        3.57,
    ]
    assert evidence["face_boundary"]["selected_topology"] == "lateral cylindrical face"
    assert evidence["port_cap"]["port_plane_z_mm"] == -0.112


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("default_absolute_tolerance",), float("inf")),
        (("s11", "curve_rmse_db_max"), float("inf")),
        (("s11", "threshold_db"), float("nan")),
    ],
)
def test_benchmark_schema_rejects_non_finite_acceptance_limits(tmp_path, path, value):
    payload = json.loads(_benchmark(tmp_path).read_text("utf-8"))
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValidationError, match="must be finite"):
        ValidationBenchmark.model_validate(payload)


def test_contract_rejects_extra_semantic_collection_members_but_allows_metadata(tmp_path):
    candidate_path = _candidate(tmp_path)
    candidate = json.loads(candidate_path.read_text("utf-8"))
    candidate["model"]["objects"]["patch"]["review_note"] = "metadata is allowed"
    _write_json(candidate_path, candidate)

    metadata_report = ValidationService().validate_manifest(
        _benchmark(tmp_path), candidate_path, contract_only=True
    )
    assert metadata_report["status"] == "passed"

    candidate["model"]["objects"]["unexpected_parasitic"] = {
        "primitive": "box",
        "material": "copper",
    }
    _write_json(candidate_path, candidate)
    extra_object_report = ValidationService().validate_manifest(
        _benchmark(tmp_path), candidate_path, contract_only=True
    )

    assert extra_object_report["status"] == "failed"
    assert [
        check["path"]
        for check in extra_object_report["contract"]["checks"]
        if not check["passed"]
    ] == ["objects.__extra__"]


def test_overlap_minimum_applies_to_both_curves(tmp_path):
    reference_frequency = np.linspace(8.0, 12.0, 81)
    reference_values = -2.0 - 18.0 * np.exp(
        -((reference_frequency - 10.0) / 0.35) ** 2
    )
    candidate_frequency = np.asarray([8.0, 10.0, 12.0])
    candidate_values = np.asarray([-2.0, -20.0, -2.0])

    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path),
        reference_s11=_curve(
            tmp_path / "reference.csv", reference_frequency, reference_values
        ),
        candidate_s11=_curve(
            tmp_path / "candidate.csv", candidate_frequency, candidate_values
        ),
    )

    assert report["status"] == "failed"
    assert report["s11"]["overlap_point_counts"] == {"reference": 81, "candidate": 3}
    assert report["s11"]["checks"][0]["path"] == "s11.overlap_points"
    assert report["s11"]["checks"][0]["actual"] == 3


def test_candidate_must_cover_the_reference_sweep(tmp_path):
    reference_frequency = np.linspace(8.0, 12.0, 81)
    candidate_frequency = np.linspace(9.0, 11.0, 41)
    reference_values = -2.0 - 18.0 * np.exp(
        -((reference_frequency - 10.0) / 0.35) ** 2
    )
    candidate_values = -2.0 - 18.0 * np.exp(
        -((candidate_frequency - 10.0) / 0.35) ** 2
    )

    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path),
        reference_s11=_curve(
            tmp_path / "reference.csv", reference_frequency, reference_values
        ),
        candidate_s11=_curve(
            tmp_path / "candidate.csv", candidate_frequency, candidate_values
        ),
    )

    assert report["status"] == "failed"
    coverage = next(
        check
        for check in report["s11"]["checks"]
        if check["path"] == "s11.reference_overlap_fraction"
    )
    assert coverage["actual"] == pytest.approx(0.5)
    assert coverage["passed"] is False


def test_s11_csv_rejects_non_monotonic_frequency_order(tmp_path):
    reference_frequency = np.linspace(8.0, 12.0, 81)
    values = -2.0 - 18.0 * np.exp(-((reference_frequency - 10.0) / 0.35) ** 2)
    candidate_frequency = reference_frequency.copy()
    candidate_frequency[[20, 21]] = candidate_frequency[[21, 20]]

    with pytest.raises(ValueError, match="strictly increasing"):
        ValidationService().validate_manifest(
            _benchmark(tmp_path),
            _candidate(tmp_path),
            reference_s11=_curve(tmp_path / "reference.csv", reference_frequency, values),
            candidate_s11=_curve(tmp_path / "candidate.csv", candidate_frequency, values),
        )


def test_rmse_uses_sampling_points_from_both_curves(tmp_path):
    benchmark_path = _benchmark(tmp_path)
    benchmark = json.loads(benchmark_path.read_text("utf-8"))
    benchmark["s11"]["curve_rmse_db_max"] = 0.1
    _write_json(benchmark_path, benchmark)

    reference_frequency = np.linspace(8.0, 12.0, 21)
    reference_values = -2.0 - 18.0 * np.exp(
        -((reference_frequency - 10.0) / 0.35) ** 2
    )
    candidate_frequency = np.linspace(8.0, 12.0, 401)
    candidate_values = np.interp(
        candidate_frequency, reference_frequency, reference_values
    )
    candidate_values[np.argmin(abs(candidate_frequency - 8.1))] += 8.0

    report = ValidationService().validate_manifest(
        benchmark_path,
        _candidate(tmp_path),
        reference_s11=_curve(
            tmp_path / "reference.csv", reference_frequency, reference_values
        ),
        candidate_s11=_curve(
            tmp_path / "candidate.csv", candidate_frequency, candidate_values
        ),
    )

    assert report["status"] == "failed"
    assert report["s11"]["rmse_comparison_points"] == 401
    rmse_check = next(
        check
        for check in report["s11"]["checks"]
        if check["path"] == "s11.curve_rmse_db"
    )
    assert rmse_check["passed"] is False


def test_legacy_mode_rejects_sweep_edge_as_a_resonance(tmp_path):
    frequencies = np.linspace(8.0, 12.0, 81)
    monotonic = np.linspace(-2.0, -20.0, len(frequencies))

    report = ValidationService().validate_manifest(
        _benchmark(tmp_path),
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, monotonic),
        candidate_s11=_curve(tmp_path / "candidate.csv", frequencies, monotonic),
    )

    assert report["status"] == "failed"
    assert report["s11"]["reference"] == {
        "resonant_frequency_ghz": None,
        "bandwidth_ghz": None,
    }


def test_optional_s11_without_curves_is_contract_level_but_partial_pair_is_incomplete(
    tmp_path,
):
    benchmark_path = _benchmark(tmp_path)
    benchmark = json.loads(benchmark_path.read_text("utf-8"))
    benchmark["s11"]["required"] = False
    _write_json(benchmark_path, benchmark)

    not_run = ValidationService().validate_manifest(
        benchmark_path, _candidate(tmp_path)
    )
    assert not_run["status"] == "passed"
    assert not_run["validation_level"] == "contract"
    assert not_run["claims"]["electromagnetic_results_validated"] is False

    frequencies = np.linspace(8.0, 12.0, 81)
    values = -2.0 - 18.0 * np.exp(-((frequencies - 10.0) / 0.35) ** 2)
    partial = ValidationService().validate_manifest(
        benchmark_path,
        _candidate(tmp_path),
        reference_s11=_curve(tmp_path / "reference.csv", frequencies, values),
    )
    assert partial["status"] == "incomplete"
    assert partial["quality_gate_passed"] is False


def test_frequency_unit_is_explicit_and_curves_are_normalized_to_ghz(tmp_path):
    benchmark_path = _benchmark(tmp_path)
    benchmark = json.loads(benchmark_path.read_text("utf-8"))
    benchmark["s11"]["frequency_column"] = "frequency_mhz"
    with pytest.raises(ValidationError, match="frequency_column implies MHz"):
        ValidationBenchmark.model_validate(benchmark)

    benchmark["s11"]["frequency_unit"] = "MHz"
    _write_json(benchmark_path, benchmark)
    frequencies_mhz = np.linspace(8000.0, 12000.0, 81)
    values = -2.0 - 18.0 * np.exp(-((frequencies_mhz - 10000.0) / 350.0) ** 2)

    def mhz_curve(path: Path, curve_values: np.ndarray) -> Path:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["frequency_mhz", "s11_db"])
            writer.writerows(zip(frequencies_mhz, curve_values))
        return path

    report = ValidationService().validate_manifest(
        benchmark_path,
        _candidate(tmp_path),
        reference_s11=mhz_curve(tmp_path / "reference_mhz.csv", values),
        candidate_s11=mhz_curve(tmp_path / "candidate_mhz.csv", values + 0.1),
    )
    assert report["status"] == "passed"
    assert report["s11"]["reference"]["resonant_frequency_ghz"] == pytest.approx(10.0)


def test_s11_csv_rejects_ambiguous_duplicate_headers(tmp_path):
    duplicate_header = tmp_path / "duplicate_header.csv"
    duplicate_header.write_text(
        "frequency_ghz,s11_db,s11_db\n8,-2,-20\n10,-20,-2\n12,-2,-20\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate column names"):
        ValidationService().validate_manifest(
            _benchmark(tmp_path),
            _candidate(tmp_path),
            reference_s11=duplicate_header,
            candidate_s11=duplicate_header,
        )


def test_workflow_validation_writes_report(tmp_path, capsys):
    report_path = tmp_path / "report.json"
    exit_code = workflow_main(
        [
            "--workspace",
            str(tmp_path / "jobs"),
            "validate",
            "--benchmark",
            str(_benchmark(tmp_path)),
            "--candidate",
            str(_candidate(tmp_path)),
            "--contract-only",
            "--report",
            str(report_path),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "passed"
    assert output["report"] == str(report_path.resolve())
    saved = json.loads(report_path.read_text("utf-8"))
    assert saved["status"] == "passed"


def test_job_validation_normalizes_artifacts_and_versions_reports(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "A complete benchmark patch model."})
    artifacts = {
        "parameters": {"parameters": [
            {"name": "W", "value": 10.0, "unit": "mm"},
            {"name": "L", "value": 9.0, "unit": "mm"},
        ]},
        "materials": {"materials": []},
        "solids": {"solids": [
            {"name": "patch", "primitive": "box", "material": "copper"},
        ]},
        "dimensions": {"dimensions": [
            {"name": "patch", "origin": [0, 0, 0], "size": ["W", "L", 0.035]},
        ]},
        "geometry_manifest": {"operations": [
            {"order": 1, "operation": "create", "target": "patch"},
        ]},
        "simulation_spec": {
            "solution_type": "Terminal",
            "sweep": {"start": 8.0, "stop": 12.0},
        },
    }
    for name, payload in artifacts.items():
        path = store.write_artifact(
            state.job_id,
            f"{name}.json",
            json.dumps(payload),
        )
        state.artifacts[name] = str(path)
    store.save_state(state)

    service = ValidationService(store)
    first = service.validate_job(_benchmark(tmp_path), state.job_id, contract_only=True)
    second = service.validate_job(_benchmark(tmp_path), state.job_id, contract_only=True)

    assert first["status"] == "passed"
    assert first["revision_tag"] == "v001"
    assert second["revision_tag"] == "v002"
    saved = store.load_state(state.job_id)
    assert Path(saved.artifacts["validation_report_v001"]).is_file()
    assert Path(saved.artifacts["validation_report_v002"]).is_file()
    assert Path(saved.artifacts["validation_report"]).name == "validation_report.json"


def test_job_validation_uses_audited_generic_fallback_without_geometry_manifest(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "Generic ModelingService job."})
    artifacts = {
        "source_analysis": {"operations": [
            {"order": 1, "operation": "create", "target": "patch"},
        ]},
        "parameters": {"parameters": [
            {"name": "W", "value": 10.0, "unit": "mm"},
            {"name": "L", "value": 9.0, "unit": "mm"},
        ]},
        "materials": {"materials": []},
        "solids": {"solids": [
            {"name": "patch", "primitive": "box", "material": "copper"},
        ]},
        "dimensions": {"output_contract": {"solids": [
            {"name": "patch", "origin": [0, 0, 0], "size": ["W", "L", 0.035]},
        ]}},
        "simulation_spec": {
            "solution_type": "Terminal",
            "sweep": {"start": 8.0, "stop": 12.0},
        },
    }
    for name, payload in artifacts.items():
        path = store.write_artifact(state.job_id, f"{name}.json", json.dumps(payload))
        state.artifacts[name] = str(path)
    store.save_state(state)

    service = ValidationService(store)
    candidate = service.candidate_from_job(state.job_id)

    assert candidate["operations"] == artifacts["source_analysis"]["operations"]
    assert candidate["objects"]["patch"]["dimensions"]["size"] == ["W", "L", 0.035]
    audit = candidate["_assembly_audit"]
    assert audit["sources"]["dimensions"] == "dimensions.output_contract.solids"
    assert audit["sources"]["operations"] == "source_analysis.operations"
    assert audit["missing_artifacts"] == ["geometry_manifest"]
    assert audit["fallbacks"] == [
        {
            "field": "operations",
            "reason": "geometry_manifest artifact is absent",
            "source": "source_analysis.operations",
            "lossless": True,
        }
    ]

    report = service.validate_job(_benchmark(tmp_path), state.job_id, contract_only=True)
    assert report["status"] == "passed"


def test_job_validation_uses_audited_top_level_dimension_solids_without_inference(
    tmp_path,
):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "DeepSeek dimensions schema."})
    generated_dimensions = {
        "name": "patch",
        "box_x_range_mm": [0.0, 10.0],
        "box_y_range_mm": [0.0, 9.0],
        "box_z_range_mm": [0.5, 0.535],
    }
    artifacts = {
        "source_analysis": {},
        "parameters": {"parameters": [
            {"name": "W", "value": 10.0, "unit": "mm"},
            {"name": "L", "value": 9.0, "unit": "mm"},
        ]},
        "materials": {"materials": []},
        "solids": {"solids": [
            {"name": "patch", "primitive": "box", "material": "copper"},
        ]},
        "dimensions": {"solids": [generated_dimensions]},
    }
    for name, payload in artifacts.items():
        path = store.write_artifact(state.job_id, f"{name}.json", json.dumps(payload))
        state.artifacts[name] = str(path)
    store.save_state(state)

    candidate = ValidationService(store).candidate_from_job(state.job_id)

    assert candidate["objects"]["patch"]["dimensions"] == {
        key: value for key, value in generated_dimensions.items() if key != "name"
    }
    assert "origin" not in candidate["objects"]["patch"]["dimensions"]
    assert "size" not in candidate["objects"]["patch"]["dimensions"]
    assert (
        candidate["_assembly_audit"]["sources"]["dimensions"]
        == "dimensions.solids"
    )


def test_job_dimension_solids_requires_an_explicit_array(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "Invalid dimensions schema."})
    artifacts = {
        "parameters": {"parameters": []},
        "materials": {"materials": []},
        "solids": {"solids": []},
        "dimensions": {"solids": {"patch": {"size": [10.0, 9.0, 0.035]}}},
    }
    for name, payload in artifacts.items():
        path = store.write_artifact(state.job_id, f"{name}.json", json.dumps(payload))
        state.artifacts[name] = str(path)
    store.save_state(state)

    with pytest.raises(
        ValueError,
        match="dimensions artifact field solids must contain an array",
    ):
        ValidationService(store).candidate_from_job(state.job_id)


def test_job_fallback_does_not_fabricate_absent_operations_or_solver(tmp_path):
    store = WorkspaceStore(tmp_path / "jobs")
    state = store.create_job("modeling", {"description": "Incomplete generic job."})
    artifacts = {
        "source_analysis": {"input_summary": "No operation evidence."},
        "parameters": {"parameters": [
            {"name": "W", "value": 10.0, "unit": "mm"},
            {"name": "L", "value": 9.0, "unit": "mm"},
        ]},
        "materials": {"materials": []},
        "solids": {"solids": [
            {"name": "patch", "primitive": "box", "material": "copper"},
        ]},
        "dimensions": {"dimensions": [
            {"name": "patch", "origin": [0, 0, 0], "size": ["W", "L", 0.035]},
        ]},
    }
    for name, payload in artifacts.items():
        path = store.write_artifact(state.job_id, f"{name}.json", json.dumps(payload))
        state.artifacts[name] = str(path)
    store.save_state(state)

    service = ValidationService(store)
    candidate = service.candidate_from_job(state.job_id)

    assert "operations" not in candidate
    assert "solver" not in candidate
    assert candidate["_assembly_audit"]["missing_artifacts"] == [
        "geometry_manifest",
        "simulation_spec",
    ]
    report = service.validate_job(_benchmark(tmp_path), state.job_id, contract_only=True)
    failed_paths = {
        check["path"] for check in report["contract"]["checks"] if not check["passed"]
    }
    assert "operations" in failed_paths
    assert "solver" in failed_paths


def test_job_material_definitions_are_not_coerced_to_role_keyed_benchmark(tmp_path):
    benchmark = json.loads(_benchmark(tmp_path).read_text("utf-8"))
    benchmark["reference"] = {
        "materials": {
            "ground": {"material": "copper"},
        }
    }
    benchmark_path = _write_json(tmp_path / "role-material-benchmark.json", benchmark)
    candidate_path = _write_json(
        tmp_path / "material-definitions-candidate.json",
        {
            "schema_version": "1.0",
            "benchmark_id": "patch_test",
            "model": {
                "materials": {
                    "copper": {"conductivity": 5.8e7},
                    "vacuum": {"permittivity": 1.0, "permeability": 0.0},
                }
            },
        },
    )

    report = ValidationService().validate_manifest(
        benchmark_path, candidate_path, contract_only=True
    )
    failed_paths = {
        check["path"] for check in report["contract"]["checks"] if not check["passed"]
    }
    assert report["status"] == "failed"
    assert failed_paths == {"materials.ground", "materials.__extra__"}


def test_aligned_material_definition_contract_rejects_wrong_permeability(tmp_path):
    benchmark = json.loads(_benchmark(tmp_path).read_text("utf-8"))
    benchmark["reference"] = {
        "materials": {
            "vacuum": {"permittivity": 1.0, "permeability": 1.0},
        }
    }
    benchmark_path = _write_json(tmp_path / "definition-material-benchmark.json", benchmark)
    candidate_path = _write_json(
        tmp_path / "wrong-material-candidate.json",
        {
            "schema_version": "1.0",
            "benchmark_id": "patch_test",
            "model": {
                "materials": {
                    "vacuum": {"permittivity": 1.0, "permeability": 0.0},
                }
            },
        },
    )

    report = ValidationService().validate_manifest(
        benchmark_path, candidate_path, contract_only=True
    )
    failed = [check for check in report["contract"]["checks"] if not check["passed"]]
    assert report["status"] == "failed"
    assert [check["path"] for check in failed] == ["materials.vacuum.permeability"]
