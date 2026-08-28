from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .aedt_runtime import (
    aedt_license_preflight,
    aedt_grpc_session_is_active,
    describe_aedt_exception,
    ensure_strict_existing_attachment,
    is_aedt_app_released,
    prepare_pyaedt_environment,
    temporary_multi_desktop,
    temporary_grpc_session_probe,
)
from .discovery import preferred_aedt_version


def _load_hfss_class():
    from ansys.aedt.core import Hfss

    return Hfss


def _grpc_session_is_active(port: int) -> bool:
    return aedt_grpc_session_is_active(port, "127.0.0.1")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create and save a minimal HFSS project.")
    parser.add_argument("--output", default="hfss-smoke.aedt")
    parser.add_argument(
        "--attach-existing",
        action="store_true",
        help="Strictly attach to an already open AEDT gRPC session without fallback launch.",
    )
    parser.add_argument("--grpc-port", type=int, help="Explicit AEDT gRPC port for --attach-existing.")
    args = parser.parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    if output.suffix.lower() != ".aedt":
        raise ValueError("--output must be an .aedt file")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing smoke-test output: {output}")
    if args.attach_existing and args.grpc_port is None:
        raise ValueError("--grpc-port is required with --attach-existing")
    if not args.attach_existing and args.grpc_port is not None:
        raise ValueError("--grpc-port is only valid with --attach-existing")
    if args.grpc_port is not None and not 1 <= args.grpc_port <= 65535:
        raise ValueError("--grpc-port must be between 1 and 65535")
    output.parent.mkdir(parents=True, exist_ok=True)

    config = prepare_pyaedt_environment()
    Hfss = _load_hfss_class()

    hfss = None
    try:
        if args.attach_existing:
            if not _grpc_session_is_active(args.grpc_port):
                raise RuntimeError(
                    f"no active AEDT gRPC session is available on port {args.grpc_port}; "
                    "refusing to launch a fallback session"
                )
        else:
            preflight = aedt_license_preflight([output.parent])
            if preflight:
                raise RuntimeError(preflight)
        options = {
            "version": preferred_aedt_version(),
            "new_desktop": not args.attach_existing,
            "close_on_exit": False,
        }
        if args.attach_existing:
            options.update(
                project=str(output),
                design="SmokeTest",
                solution_type="Modal",
                non_graphical=False,
                port=args.grpc_port,
            )
        else:
            options.update(
                project=str(output),
                design="SmokeTest",
                solution_type="Modal",
                non_graphical=True,
            )
        if args.attach_existing:
            with temporary_grpc_session_probe(), temporary_multi_desktop():
                hfss = Hfss(**options)
        else:
            hfss = Hfss(**options)
        if args.attach_existing:
            ensure_strict_existing_attachment(hfss, args.grpc_port)
        if getattr(hfss, "odesign", None) is None:
            if args.attach_existing:
                raise RuntimeError("AEDT failed to create the dedicated SmokeTest HFSS design")
            raise RuntimeError("AEDT failed to create the SmokeTest HFSS design")
        if list(hfss.modeler.object_names):
            raise RuntimeError("the dedicated SmokeTest HFSS design is not empty; refusing to modify it")
        hfss["L"] = "10mm"
        box = hfss.modeler.create_box(
            [0, 0, 0], ["L", "2mm", "1mm"], name="SmokeBox", material="vacuum"
        )
        if not box:
            raise RuntimeError("HFSS failed to create SmokeBox")
        if not hfss.save_project(str(output)):
            raise RuntimeError("HFSS failed to save the smoke project")
        print(f"HFSS smoke test passed: {output}")
        print(f"Transport: {config['mode']}")
        print(f"Session: {'attached' if args.attach_existing else 'new'}")
    except Exception as exc:
        message = str(exc)
        if not message.startswith("AEDT license error:"):
            message = describe_aedt_exception(exc, [output.parent])
        if args.attach_existing and message.startswith(f"{type(exc).__name__}:"):
            message += ". Open AEDT with gRPC enabled, then retry."
        raise SystemExit(f"HFSS smoke test failed: {message}") from None
    finally:
        if hfss is not None and not is_aedt_app_released(hfss):
            hfss.release_desktop(
                close_projects=not args.attach_existing,
                close_desktop=not args.attach_existing,
            )


if __name__ == "__main__":
    main()
