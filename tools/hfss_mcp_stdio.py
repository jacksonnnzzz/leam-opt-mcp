"""Launch leonardwy/HFSS_McpServer safely over MCP stdio.

PyAEDT writes screen logs to stdout by default, which corrupts MCP JSON-RPC.
AEDT 2025 R1 before SP04 also needs the legacy insecure gRPC transport.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path


raw_server = os.getenv("HFSS_MCP_SERVER_PATH")
if not raw_server:
    raise SystemExit(
        "Set HFSS_MCP_SERVER_PATH to the cloned HFSS_McpServer/hfss_server.py file."
    )
SERVER = Path(raw_server).expanduser().resolve()
if not SERVER.is_file():
    raise SystemExit("HFSS_MCP_SERVER_PATH does not point to a file: " + str(SERVER))

os.environ.setdefault("PYAEDT_USE_PRE_GRPC_ARGS", "True")

from ansys.aedt.core import settings
from ansys.aedt.core.aedt_logger import pyaedt_logger

settings.enable_screen_logs = False
settings.grpc_secure_mode = False
settings.grpc_local = True
pyaedt_logger.disable_stdout_log()

runpy.run_path(str(SERVER), run_name="__main__")
