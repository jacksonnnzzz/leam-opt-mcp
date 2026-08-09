from pathlib import Path

import pytest

import antenna_mcp.aedt_runtime as aedt_runtime
import antenna_mcp.smoke as smoke


class _Settings:
    use_multi_desktop = False


class _Desktop:
    def __init__(self, port, launched_by_pyaedt):
        self.port = port
        self.launched_by_pyaedt = launched_by_pyaedt


class _Modeler:
    def __init__(self, events):
        self.object_names = []
        self.events = events

    def create_box(self, origin, sizes, **kwargs):
        self.events.append(("create_box", origin, sizes, kwargs))
        self.object_names.append(kwargs["name"])
        return object()


class _FakeHfss:
    instances = []
    settings = None
    launched_by_pyaedt = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.multi_desktop_at_init = self.settings.use_multi_desktop
        self.desktop_class = _Desktop(kwargs["port"], self.launched_by_pyaedt)
        self.odesign = object()
        self.events = []
        self.modeler = _Modeler(self.events)
        self.variables = {}
        self.saved = []
        self.releases = []
        self.instances.append(self)

    def __setitem__(self, name, value):
        self.variables[name] = value
        self.events.append(("set_variable", name, value))

    def save_project(self, path):
        self.events.append(("save_project", path))
        self.saved.append(path)
        Path(path).write_bytes(b"fake aedt")
        return True

    def release_desktop(self, **kwargs):
        self.releases.append(kwargs)


def _configure_attach_fakes(monkeypatch):
    settings = _Settings()
    _FakeHfss.instances = []
    _FakeHfss.settings = settings
    _FakeHfss.launched_by_pyaedt = False
    monkeypatch.setattr(aedt_runtime, "_pyaedt_settings", lambda: settings)
    monkeypatch.setattr(smoke, "prepare_pyaedt_environment", lambda: {"mode": "insecure"})
    monkeypatch.setattr(smoke, "preferred_aedt_version", lambda: "2025.1")
    monkeypatch.setattr(smoke, "_load_hfss_class", lambda: _FakeHfss)
    monkeypatch.setattr(smoke, "_grpc_session_is_active", lambda port: port == 50061)
    return settings


def test_attach_creates_dedicated_project_and_design_without_copying_active_project(
    tmp_path, monkeypatch, capsys
):
    settings = _configure_attach_fakes(monkeypatch)
    output = tmp_path / "dedicated-smoke.aedt"

    smoke.main(
        [
            "--attach-existing",
            "--grpc-port",
            "50061",
            "--output",
            str(output),
        ]
    )

    assert len(_FakeHfss.instances) == 1
    app = _FakeHfss.instances[0]
    assert app.kwargs == {
        "version": "2025.1",
        "new_desktop": False,
        "close_on_exit": False,
        "project": str(output.resolve()),
        "design": "SmokeTest",
        "solution_type": "Modal",
        "non_graphical": False,
        "port": 50061,
    }
    assert app.multi_desktop_at_init is True
    assert settings.use_multi_desktop is False
    assert app.variables == {"L": "10mm"}
    assert [event[0] for event in app.events] == ["set_variable", "create_box", "save_project"]
    assert app.saved == [str(output.resolve())]
    assert app.releases == [{"close_projects": False, "close_desktop": False}]
    assert output.read_bytes() == b"fake aedt"
    assert "Session: attached" in capsys.readouterr().out


def test_smoke_refuses_existing_output_before_loading_pyaedt(tmp_path, monkeypatch):
    output = tmp_path / "existing.aedt"
    output.write_bytes(b"keep me")
    loaded = []
    monkeypatch.setattr(smoke, "prepare_pyaedt_environment", lambda: loaded.append(True))

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        smoke.main(
            [
                "--attach-existing",
                "--grpc-port",
                "50061",
                "--output",
                str(output),
            ]
        )

    assert loaded == []
    assert output.read_bytes() == b"keep me"


def test_attach_fallback_cleanup_is_not_released_twice(tmp_path, monkeypatch):
    _configure_attach_fakes(monkeypatch)
    _FakeHfss.launched_by_pyaedt = True
    monkeypatch.setattr(
        smoke,
        "describe_aedt_exception",
        lambda exc, search_dirs: f"{type(exc).__name__}: {exc}",
    )
    output = tmp_path / "fallback-smoke.aedt"

    with pytest.raises(SystemExit, match="fallback"):
        smoke.main(
            [
                "--attach-existing",
                "--grpc-port",
                "50061",
                "--output",
                str(output),
            ]
        )

    assert len(_FakeHfss.instances) == 1
    app = _FakeHfss.instances[0]
    assert app.releases == [{"close_projects": True, "close_desktop": True}]
    assert aedt_runtime.is_aedt_app_released(app) is True
