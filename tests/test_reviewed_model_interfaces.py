import inspect
import json
import sys

from antenna_mcp import reviewed_model_cli, server


class _FakeCodegen:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, job_id, through_stage="boolean"):
        self.calls.append((job_id, through_stage))
        return {"status": "completed", "python_file": "generated_model.py"}


class _FakeFeedback:
    def __init__(self) -> None:
        self.calls = []

    def submit(self, job_id, feedback, comparison_images):
        self.calls.append(("submit", job_id, feedback, comparison_images))
        return {"status": "awaiting_regeneration"}

    def regenerate(self, job_id):
        self.calls.append(("regenerate", job_id))
        return {"status": "awaiting_user_comparison"}


class _FakeAssumptionService:
    def __init__(self) -> None:
        self.calls = []

    def prepare(self, job_id, symbol, value, unit, rationale):
        self.calls.append(("prepare", job_id, symbol, value, unit, rationale))
        return {"status": "awaiting_review", "approval_hash": "proposal-hash"}

    def approve(self, job_id, approval_hash):
        self.calls.append(("approve", job_id, approval_hash))
        return {"status": "completed", "approval_hash": approval_hash}


class _FakeCompiler:
    def __init__(self) -> None:
        self.calls = []

    def compile(self, job_id, profile, assumption_approval_hash):
        self.calls.append((job_id, profile, assumption_approval_hash))
        return {"status": "awaiting_artifact_review"}


def _install_cli_service(monkeypatch, service):
    monkeypatch.setattr(reviewed_model_cli, "WorkspaceStore", lambda: object())
    monkeypatch.setattr(
        reviewed_model_cli,
        "EngineeringAssumptionService",
        lambda _store: service,
    )


def test_assumption_propose_cli_has_no_confirmation_self_attestation(monkeypatch, capsys):
    service = _FakeAssumptionService()
    _install_cli_service(monkeypatch, service)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "model-assume-propose",
            "mdl-0123456789ab",
            "CuT",
            "0.035",
            "--unit",
            "mm",
            "--rationale",
            "The paper leaves copper thickness unresolved.",
        ],
    )

    reviewed_model_cli.assume_propose_main()

    assert service.calls == [
        (
            "prepare",
            "mdl-0123456789ab",
            "CuT",
            0.035,
            "mm",
            "The paper leaves copper thickness unresolved.",
        )
    ]
    assert json.loads(capsys.readouterr().out)["approval_hash"] == "proposal-hash"


def test_assumption_approve_cli_forwards_exact_hash(monkeypatch, capsys):
    service = _FakeAssumptionService()
    _install_cli_service(monkeypatch, service)
    monkeypatch.setattr(
        sys,
        "argv",
        ["model-assume-approve", "mdl-0123456789ab", "reviewed-hash"],
    )

    reviewed_model_cli.assume_approve_main()

    assert service.calls == [("approve", "mdl-0123456789ab", "reviewed-hash")]
    assert json.loads(capsys.readouterr().out)["approval_hash"] == "reviewed-hash"


def test_mcp_assumption_tools_expose_two_stage_contract(monkeypatch):
    service = _FakeAssumptionService()
    monkeypatch.setattr(server, "WorkspaceStore", lambda: object())
    monkeypatch.setattr(server, "EngineeringAssumptionService", lambda _store: service)

    propose_signature = inspect.signature(server.propose_antenna_engineering_assumption)
    assert "confirmed_by_user" not in propose_signature.parameters
    assert list(propose_signature.parameters) == [
        "job_id",
        "symbol",
        "value",
        "unit",
        "rationale",
    ]
    assert list(inspect.signature(server.approve_antenna_engineering_assumption).parameters) == [
        "job_id",
        "approval_hash",
    ]

    proposed = server.propose_antenna_engineering_assumption(
        "mdl-0123456789ab",
        "CuT",
        0.035,
        "mm",
        "The paper leaves copper thickness unresolved.",
    )
    approved = server.approve_antenna_engineering_assumption(
        "mdl-0123456789ab",
        "reviewed-hash",
    )

    assert proposed["status"] == "awaiting_review"
    assert approved["status"] == "completed"
    assert service.calls[-1] == ("approve", "mdl-0123456789ab", "reviewed-hash")


def test_compile_cli_and_mcp_require_external_assumption_hash(monkeypatch, capsys):
    compiler = _FakeCompiler()
    monkeypatch.setattr(reviewed_model_cli, "WorkspaceStore", lambda: object())
    monkeypatch.setattr(
        reviewed_model_cli,
        "ReviewedModelCompiler",
        lambda _store: compiler,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "model-compile",
            "mdl-0123456789ab",
            "--profile",
            "leam_case3",
            "--assumption-approval-hash",
            "reviewed-hash",
        ],
    )
    reviewed_model_cli.compile_main()
    assert json.loads(capsys.readouterr().out)["status"] == "awaiting_artifact_review"
    assert compiler.calls == [("mdl-0123456789ab", "leam_case3", "reviewed-hash")]

    server_compiler = _FakeCompiler()
    monkeypatch.setattr(server, "WorkspaceStore", lambda: object())
    monkeypatch.setattr(server, "ReviewedModelCompiler", lambda _store: server_compiler)
    assert list(inspect.signature(server.compile_reviewed_antenna_model).parameters) == [
        "job_id",
        "assumption_approval_hash",
        "profile",
    ]
    server.compile_reviewed_antenna_model(
        "mdl-0123456789ab",
        "reviewed-hash",
        "leam_case3",
    )
    assert server_compiler.calls == [
        ("mdl-0123456789ab", "leam_case3", "reviewed-hash")
    ]


def test_mcp_codegen_is_an_explicit_license_free_stage(monkeypatch):
    codegen = _FakeCodegen()
    monkeypatch.setattr(server, "WorkspaceStore", lambda: object())
    monkeypatch.setattr(server, "PythonArtifactService", lambda _store: codegen)

    assert list(inspect.signature(server.generate_antenna_python).parameters) == [
        "job_id",
        "through_stage",
    ]
    result = server.generate_antenna_python("mdl-0123456789ab", "boolean")

    assert result["python_file"] == "generated_model.py"
    assert codegen.calls == [("mdl-0123456789ab", "boolean")]


def test_mcp_feedback_loop_never_implies_aedt_execution(monkeypatch):
    feedback = _FakeFeedback()
    monkeypatch.setattr(server, "WorkspaceStore", lambda: object())
    monkeypatch.setattr(server, "ModelFeedbackService", lambda _store: feedback)

    submitted = server.submit_antenna_model_feedback(
        "mdl-0123456789ab",
        "Move the slot 0.5 mm left.",
        ["comparison.png"],
    )
    regenerated = server.regenerate_antenna_python_from_feedback("mdl-0123456789ab")

    assert submitted["status"] == "awaiting_regeneration"
    assert regenerated["status"] == "awaiting_user_comparison"
    assert feedback.calls == [
        (
            "submit",
            "mdl-0123456789ab",
            "Move the slot 0.5 mm left.",
            ["comparison.png"],
        ),
        ("regenerate", "mdl-0123456789ab"),
    ]
