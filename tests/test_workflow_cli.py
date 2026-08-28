from __future__ import annotations

import json

from antenna_mcp.doctor import build_report, main as doctor_main
from antenna_mcp.workflow_cli import _result_failed, main as workflow_main


def test_workflow_cli_creates_and_reads_job(tmp_path, capsys):
    result = workflow_main(
        [
            "--workspace",
            str(tmp_path),
            "model-create",
            "--description",
            "Create a parameterized rectangular patch antenna for offline review.",
        ]
    )
    assert result == 0
    created = json.loads(capsys.readouterr().out)
    assert created["kind"] == "modeling"
    assert created["status"] == "created"

    result = workflow_main(
        ["--workspace", str(tmp_path), "status", created["job_id"]]
    )
    assert result == 0
    status = json.loads(capsys.readouterr().out)
    assert status == created


def test_doctor_never_exposes_credentials(tmp_path, monkeypatch, capsys):
    secret = "credential-value-that-must-not-appear"
    environment = {
        "ANTENNA_TEXT_PROVIDER": "deepseek",
        "ANTENNA_VISION_PROVIDER": "ollama",
        "ANTENNA_TEXT_MODEL": "deepseek-v4-pro",
        "DEEPSEEK_API_KEY": secret,
        "OLLAMA_VISION_MODEL": "qwen3-vl:8b",
    }
    report = build_report(workspace=tmp_path, environment=environment)
    serialized = json.dumps(report)
    assert secret not in serialized
    assert report["providers"]["deepseek_key_configured"] is True
    assert report["providers"]["text_model_override"] == "deepseek-v4-pro"
    blank_override = build_report(
        workspace=tmp_path,
        environment={**environment, "ANTENNA_TEXT_MODEL": "   "},
    )
    assert blank_override["providers"]["text_model_override"] is None

    for key in (
        "ANTENNA_TEXT_PROVIDER",
        "ANTENNA_VISION_PROVIDER",
        "ANTENNA_TEXT_MODEL",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANSYSEM_ROOT251",
    ):
        monkeypatch.delenv(key, raising=False)
    assert doctor_main(["--workspace", str(tmp_path)]) == 0
    assert secret not in capsys.readouterr().out


def test_workflow_failure_detection_handles_nested_pipeline_results():
    assert _result_failed({"status": "failed"}) is True
    assert _result_failed({"pipeline": {"status": "failed"}}) is True
    assert _result_failed({"status": "completed"}) is False
