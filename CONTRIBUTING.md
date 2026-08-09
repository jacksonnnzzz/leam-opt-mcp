# Contributing

Contributions are welcome, especially backend adapters, geometry validators, reproducible
paper cases, and optimization strategies.

## Development setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
```

Unit tests must run without API keys, AEDT, or an HFSS license. Put live integration tests
behind explicit environment gates and never make CI launch a simulator by default.

## Adding a reconstruction case

Provide:

1. a request that identifies the exact figure and prevents cross-case dimension mixing;
2. `evidence_and_assumptions.json` with paper evidence, visual interpretation, and engineering
   assumptions clearly separated;
3. an import-safe `generated_model_vNNN.py` exposing `build(hfss)` with no top-level execution;
4. an optional native AEDT wrapper that neither saves nor solves automatically;
5. offline tests for syntax, safety, object topology, and the manifest.

Do not commit source PDFs unless their redistribution license is explicit. Do not commit API
keys, `.env`, `.aedt` files, solver results, license logs, or personal absolute paths.

## Pull requests

Keep changes focused, explain evidence versus assumptions, and include the commands used to
verify the change. A visually plausible model is not sufficient evidence for dimensions,
ports, boundaries, mesh, or simulation settings.
