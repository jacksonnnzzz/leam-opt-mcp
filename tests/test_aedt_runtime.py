import pytest

import antenna_mcp.aedt_runtime as aedt_runtime
from antenna_mcp.aedt_runtime import (
    ensure_strict_existing_attachment,
    is_aedt_app_released,
    planned_transport,
    temporary_multi_desktop,
)


def _fake_install(tmp_path, code):
    root = tmp_path / f"v{code}" / "AnsysEM"
    root.mkdir(parents=True)
    (root / "ansysedt.exe").write_text("", encoding="utf-8")
    (root / "syslib").mkdir()
    return root


def test_aedt_2025_defaults_to_pre_sp_insecure(tmp_path):
    root = _fake_install(tmp_path, "251")
    config = planned_transport({"ANSYSEM_ROOT251": str(root)})
    assert config["mode"] == "insecure"
    assert config["pre_service_pack_args"] is True


def test_transport_can_be_forced_secure(tmp_path):
    root = _fake_install(tmp_path, "251")
    config = planned_transport({"ANSYSEM_ROOT251": str(root), "ANTENNA_MCP_GRPC_MODE": "secure"})
    assert config["mode"] == "secure"
    assert config["pre_service_pack_args"] is False


class _Desktop:
    def __init__(self, launched, port):
        self.launched_by_pyaedt = launched
        self.port = port


class _App:
    def __init__(self, launched, port):
        self.desktop_class = _Desktop(launched, port)
        self.releases = []

    def release_desktop(self, **kwargs):
        self.releases.append(kwargs)


def test_strict_existing_attachment_accepts_only_preexisting_requested_port():
    app = _App(False, 50061)
    ensure_strict_existing_attachment(app, 50061)
    assert app.releases == []


def test_strict_existing_attachment_closes_pyaedt_fallback():
    app = _App(True, 50061)
    with pytest.raises(RuntimeError, match="fallback"):
        ensure_strict_existing_attachment(app, 50061)
    assert app.releases == [{"close_projects": True, "close_desktop": True}]
    assert is_aedt_app_released(app) is True


def test_strict_existing_attachment_never_closes_wrong_preexisting_gui():
    app = _App(False, 50062)
    with pytest.raises(RuntimeError, match="expected existing port"):
        ensure_strict_existing_attachment(app, 50061)
    assert app.releases == [{"close_projects": False, "close_desktop": False}]
    assert is_aedt_app_released(app) is True


class _Settings:
    use_multi_desktop = False


def test_temporary_multi_desktop_is_reentrant_and_restores(monkeypatch):
    settings = _Settings()
    monkeypatch.setattr(aedt_runtime, "_pyaedt_settings", lambda: settings)

    with temporary_multi_desktop():
        assert settings.use_multi_desktop is True
        with temporary_multi_desktop(False):
            assert settings.use_multi_desktop is False
        assert settings.use_multi_desktop is True

    assert settings.use_multi_desktop is False


def test_temporary_multi_desktop_restores_after_exception(monkeypatch):
    settings = _Settings()
    monkeypatch.setattr(aedt_runtime, "_pyaedt_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="boom"):
        with temporary_multi_desktop():
            assert settings.use_multi_desktop is True
            raise RuntimeError("boom")

    assert settings.use_multi_desktop is False
