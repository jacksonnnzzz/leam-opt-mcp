from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


CASES_ROOT = Path(__file__).resolve().parents[1] / "examples" / "validation" / "cases"
CASE_IDS = {
    "ansys_probe_patch",
    "yeo_conventional_patch",
    "yeo_scaled_slot_loaded_patch",
    "wifi_patch_5250",
    "ibrahim_38ghz_monopole",
    "khan_28_38ghz_monopole",
    "kaur_baseline_uwb",
    "kaur_wlan_notch",
    "kaur_xband_notch",
}

VALIDATION_ROOT = CASES_ROOT.parent


def test_each_validation_case_has_an_independent_python_launcher_and_valid_manifest():
    discovered = {path.parent.name for path in CASES_ROOT.glob("*/case.json")}
    assert discovered == CASE_IDS
    design_names = set()
    for case_id in sorted(CASE_IDS):
        case_dir = CASES_ROOT / case_id
        runner = case_dir / "run_case.py"
        payload = json.loads((case_dir / "case.json").read_text("utf-8"))
        assert runner.is_file()
        assert payload["case_id"] == case_id
        assert payload["runner"] == "run_case.py"
        assert payload["design_name"] not in design_names
        design_names.add(payload["design_name"])
        for key in ("shared_model", "benchmark"):
            assert (case_dir / payload[key]).resolve().is_file()


def test_all_one_case_launchers_expose_help_without_starting_aedt():
    for case_id in sorted(CASE_IDS):
        result = subprocess.run(
            [sys.executable, str(CASES_ROOT / case_id / "run_case.py"), "--help"],
            cwd=CASES_ROOT.parents[2],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, (case_id, result.stdout, result.stderr)
        assert "--solve" in result.stdout


def test_campaign_summary_matches_the_solved_and_pending_reference_designs():
    payload = json.loads((VALIDATION_ROOT / "campaign.json").read_text("utf-8"))
    summary = payload["summary"]
    assert summary["implemented_reference_designs"] == 9
    assert summary["reference_designs_solved"] == 9
    assert summary["reference_designs_passing_reference_gate"] == 1
    assert summary["reference_designs_failing_reference_gate"] == 8
    assert summary["static_tested_blocked"] == 0
    assert summary["static_tested_pending_solve"] == 0

    statuses = {case["case_id"]: case["status"] for case in payload["cases"]}
    assert statuses == {
        "ansys_pyaedt_probe_patch": "candidate-solved/pass",
        "yeo_2019_conventional_inset_patch": "solved/fail-paper-gate",
        "yeo_2019_scaled_slot_loaded_patch": "solved/fail-paper-gate",
        "wifi_patch_5250": "solved/fail-paper-gate",
        "kaur_2021_split_ring_monopole": "solved/fail-paper-gate",
        "ibrahim_2023_38ghz_monopole": "solved/fail-paper-gate",
        "khan_2024_28_38ghz_monopole": "solved/fail-paper-gate",
    }
    for case in payload["cases"]:
        for artifact_name, artifact in case["artifacts"].items():
            paths = [artifact] if isinstance(artifact, str) else artifact
            assert isinstance(paths, list)
            for relative_path in paths:
                if artifact_name == "source_pdf":
                    source_path = Path(relative_path)
                    assert source_path.suffix.lower() == ".pdf"
                    assert "references" in source_path.parts
                    continue
                assert (VALIDATION_ROOT / relative_path).is_file(), (
                    case["case_id"],
                    relative_path,
                )
