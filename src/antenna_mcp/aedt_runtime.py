from __future__ import annotations

import os
import re
import socket
import subprocess
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Mapping

from .discovery import discover_aedt_installations


_PYAEDT_SETTINGS_LOCK = threading.RLock()
_AEDT_APP_RELEASED_ATTRIBUTE = "_antenna_mcp_released"


def _pyaedt_settings() -> Any:
    from ansys.aedt.core import settings

    return settings


@contextmanager
def temporary_multi_desktop(enabled: bool = True) -> Iterator[None]:
    """Temporarily change PyAEDT's process-global multi-Desktop setting.

    PyAEDT exposes ``settings.use_multi_desktop`` as mutable global state.  A
    process-level reentrant lock keeps concurrent and nested callers from
    restoring each other's value while an AEDT application is being created.
    """
    with _PYAEDT_SETTINGS_LOCK:
        settings = _pyaedt_settings()
        previous = settings.use_multi_desktop
        settings.use_multi_desktop = enabled
        try:
            yield
        finally:
            settings.use_multi_desktop = previous


@contextmanager
def temporary_grpc_session_probe() -> Iterator[None]:
    """Make PyAEDT Desktop port validation use the localized-Windows-safe probe.

    ``Desktop`` imports its probe into module scope, so fixing the preflight
    alone is insufficient: the constructor can otherwise report the existing
    port as absent and attempt a fallback launch.  Patch only for the protected
    attach operation and always restore PyAEDT's process-global function.
    """
    from ansys.aedt.core import desktop as desktop_module

    with _PYAEDT_SETTINGS_LOCK:
        original = desktop_module.is_grpc_session_active
        desktop_module.is_grpc_session_active = aedt_grpc_session_is_active
        try:
            yield
        finally:
            desktop_module.is_grpc_session_active = original


def is_aedt_app_released(app: Any) -> bool:
    """Return whether strict-attachment cleanup already released this app."""
    return bool(getattr(app, _AEDT_APP_RELEASED_ATTRIBUTE, False))


def planned_transport(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = environment or os.environ
    installations = discover_aedt_installations(env)
    installation = installations[0] if installations else None
    requested = env.get("ANTENNA_MCP_GRPC_MODE", "auto").lower()
    if requested not in {"auto", "secure", "insecure"}:
        raise ValueError("ANTENNA_MCP_GRPC_MODE must be auto, secure, or insecure")
    version_code = installation["version_code"] if installation else None
    # AEDT 2025 R1/R2 require SP04+ for WNUA. Insecure mode works across service packs.
    insecure = requested == "insecure" or (requested == "auto" and version_code in {"251", "252"})
    return {
        "mode": "insecure" if insecure else "secure",
        "pre_service_pack_args": insecure and version_code in {"251", "252"},
        "installation": installation,
    }


def prepare_pyaedt_environment() -> dict[str, Any]:
    config = planned_transport()
    installation = config["installation"]
    if installation and installation["version_code"]:
        os.environ.setdefault(f"ANSYSEM_ROOT{installation['version_code']}", installation["root"])
    if config["pre_service_pack_args"]:
        os.environ.setdefault("PYAEDT_USE_PRE_GRPC_ARGS", "True")

    from ansys.aedt.core import settings

    settings.grpc_secure_mode = config["mode"] == "secure"
    settings.grpc_local = True
    # MCP stdio reserves stdout for JSON-RPC. PyAEDT screen logs would corrupt
    # the transport, so route them to its file/logger handlers by default.
    if os.getenv("ANTENNA_MCP_PYAEDT_SCREEN_LOGS") != "1":
        settings.enable_screen_logs = False
        try:
            from ansys.aedt.core.aedt_logger import pyaedt_logger

            pyaedt_logger.disable_stdout_log()
        except (ImportError, AttributeError):
            pass
    return config


def aedt_grpc_session_is_active(port: int, machine: str | None = None) -> bool:
    """Return whether an AEDT process owns a listening gRPC port.

    PyAEDT 0.26.3 discovers Windows process IDs by parsing ``tasklist`` output.
    That parser can return an empty result on localized Windows installations,
    even when ``ansysedt.exe`` is visibly listening.  Retain PyAEDT's probe as
    the first choice, then use a process-owner-checked local psutil fallback.
    An arbitrary process occupying the port is never accepted as AEDT.
    """
    if not 1 <= int(port) <= 65535:
        return False
    if _pyaedt_grpc_probe(port, machine):
        return True
    if not _is_local_machine(machine):
        return False
    return _local_aedt_listener_is_active(port)


def _pyaedt_grpc_probe(port: int, machine: str | None) -> bool:
    try:
        from ansys.aedt.core.generic.general_methods import is_grpc_session_active

        return bool(is_grpc_session_active(port, machine or "127.0.0.1"))
    except (ImportError, OSError, RuntimeError):
        return False


def _is_local_machine(machine: str | None) -> bool:
    normalized = str(machine or "").strip().casefold()
    return normalized in {
        "",
        "localhost",
        "127.0.0.1",
        "::1",
        "::ffff:127.0.0.1",
        socket.gethostname().casefold(),
    }


def _local_aedt_listener_is_active(port: int) -> bool:
    try:
        import psutil
    except ImportError:
        return False
    try:
        connections = psutil.net_connections(kind="tcp")
    except (OSError, psutil.AccessDenied):
        return False
    for connection in connections:
        local_address = getattr(connection, "laddr", None)
        local_port = getattr(local_address, "port", None)
        if local_port is None and local_address:
            local_port = local_address[1]
        if (
            local_port != port
            or str(getattr(connection, "status", "")).upper() not in {"LISTEN", "LISTENING"}
            or getattr(connection, "pid", None) is None
        ):
            continue
        try:
            process_name = psutil.Process(connection.pid).name().casefold()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if process_name in {"ansysedt", "ansysedt.exe", "ansysedtsv", "ansysedtsv.exe"}:
            return True
    return False


def ensure_strict_existing_attachment(app: Any, expected_port: int) -> None:
    """Reject and close any PyAEDT fallback launch during an attach-only operation."""
    desktop = getattr(app, "desktop_class", None)
    launched = getattr(desktop, "launched_by_pyaedt", None)
    actual_port = getattr(desktop, "port", getattr(app, "port", None))
    try:
        port_matches = int(actual_port) == int(expected_port)
    except (TypeError, ValueError):
        port_matches = False
    if launched is False and port_matches:
        return

    # If PyAEDT raced with the preflight and started its own fallback Desktop,
    # close only that newly launched instance. Never close a pre-existing GUI.
    try:
        app.release_desktop(
            close_projects=launched is True,
            close_desktop=launched is True,
        )
    except Exception:
        pass
    else:
        setattr(app, _AEDT_APP_RELEASED_ATTRIBUTE, True)
    detail = f"launched_by_pyaedt={launched!r}, actual_port={actual_port!r}"
    raise RuntimeError(
        "strict AEDT attachment failed or PyAEDT launched a fallback session; "
        f"expected existing port {expected_port}, {detail}"
    )


def aedt_failure_diagnostic(search_dirs: list[Path] | None = None) -> str | None:
    """Extract the most useful AEDT failure from recent batch logs.

    PyAEDT can report a secondary ``NoneType`` failure when AEDT could not
    check out the HFSS license.  The actual FlexNet error is written to the
    AEDT batch log, so surface it directly to MCP clients and command-line
    users.
    """
    for path in _aedt_log_candidates(search_dirs):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-65536:]
        except OSError:
            continue
        if "Ansys Electronics Desktop Version" in text:
            text = re.split(r"(?=Ansys Electronics Desktop Version)", text)[-1]
        if "FlexNet Licensing error" not in text:
            errors = re.findall(r"\[error\]\s*([^\r\n]+)", text, flags=re.IGNORECASE)
            if errors:
                return f"AEDT error: {errors[-1].strip()} Source: {path}"
            continue
        return _license_diagnostic(text, path)
    return None


def aedt_license_preflight(search_dirs: list[Path] | None = None) -> str | None:
    """Fail fast when a known AEDT license server is still unreachable."""
    diagnostic = None
    for path in _aedt_log_candidates(search_dirs):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-65536:]
        except OSError:
            continue
        if "FlexNet Licensing error" in text:
            diagnostic = _license_diagnostic(text, path)
            break
    if diagnostic is None:
        return None
    match = re.search(r"license_server=(\d+)@([^;\s]+)", diagnostic)
    if not match:
        return None
    license_server = f"{match.group(1)}@{match.group(2)}"
    installation = planned_transport().get("installation")
    if not installation:
        return None
    root = Path(installation["root"])
    lmutil_candidates = (
        root / "licensingclient" / "winx64" / "lmutil.exe",
        root.parent / "licensingclient" / "winx64" / "lmutil.exe",
    )
    lmutil = next((path for path in lmutil_candidates if path.is_file()), None)
    if lmutil is None:
        return None
    try:
        result = subprocess.run(
            [str(lmutil), "lmstat", "-a", "-c", license_server],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    status = f"{result.stdout}\n{result.stderr}".lower()
    if re.search(r"ansyslmd\s*:\s*up\b", status):
        return None
    if "ansyslmd" in status and any(word in status for word in ("failed", "down", "cannot")):
        return diagnostic
    return None


def describe_aedt_exception(exc: BaseException, search_dirs: list[Path] | None = None) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    normalized = detail.lower()
    if any(
        token in normalized
        for token in ("aedt", "desktop", "grpc", "nonetype", "odesign", "setsolutiontype", "failed to create design")
    ):
        diagnostic = aedt_failure_diagnostic(search_dirs)
        if diagnostic:
            return diagnostic
    return detail


def _log_field(block: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}\s*\r?\n\s*([^\r\n]+)", block, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


def _aedt_log_candidates(search_dirs: list[Path] | None) -> list[Path]:
    roots = [Path.cwd(), *(search_dirs or [])]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.expanduser().resolve()
        for candidate in (root / "batch.log", *root.glob("*.log")):
            if candidate.is_file() and candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates


def _license_diagnostic(text: str, path: Path) -> str:
    blocks = re.findall(
        r"\[error\]\s*(.*?FlexNet Licensing error\s*\n\s*-?\d+(?:,\d+)?)",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    block = blocks[-1] if blocks else text
    feature = _log_field(block, "Feature") or "unknown"
    license_path = (_log_field(block, "License path") or "unknown").rstrip(";")
    code_match = re.search(r"FlexNet Licensing error\s*\n\s*(-?\d+(?:,\d+)?)", block, re.I)
    code = code_match.group(1) if code_match else "unknown"
    daemon_down = "vendor daemon is down" in block.lower()
    reason = "Ansys vendor daemon is not running" if daemon_down else "license checkout failed"
    return (
        f"AEDT license error: {reason}; feature={feature}; "
        f"license_server={license_path}; FlexNet={code}. "
        "Start/configure a valid Ansys License Manager or contact your license administrator, then retry. "
        f"Source: {path}"
    )
