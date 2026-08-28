# Offline Python generation

Image/PDF recognition and Python generation are simulator-independent stages. They do not
start AEDT and do not check out an HFSS license.

After source review, generate the Python artifact with the MCP tool:

```text
generate_antenna_python(job_id, through_stage="boolean")
```

or with PowerShell:

```powershell
.\.venv\Scripts\antenna-codegen.exe <job-id>
```

The command writes a versioned pair and updates stable `latest` aliases:

- `generated_model_v001.py`, an immutable revision containing all parameter assignments and
  a `build(hfss)` function;
- `python_export_manifest_v001.json`, which records source hashes and the license boundary;
- `generated_model.py` and `python_export_manifest.json`, stable aliases for the latest revision.
- for geometry-only (`boolean`) exports, `run_in_aedt_vNNN.py` and `run_in_aedt.py`,
  IronPython-compatible AEDT entrypoints for the immutable and latest geometry models.
  They delegate to the single reviewed native adapter, create a new design, and never save
  or solve.

Reading, importing, or reviewing `generated_model.py` requires neither AEDT nor PyAEDT.
To inspect geometry, keep the target project open and choose the generated `run_in_aedt_vNNN.py`
with **AEDT > Tools > Run Script**. Do not select `generated_model_vNNN.py` directly. The wrapper
hash-checks the adapter shipped by the installed package or complete source checkout before it
executes anything.
Exports through `simulation_setup` deliberately do not create or replace a native wrapper: the
native adapter does not expose setup, port, or boundary APIs. Run those full artifacts with
external CPython/PyAEDT. This prevents a native run from building partial geometry and then
failing halfway through simulation setup.
Executing `build(hfss)` modifies the explicitly supplied HFSS session and therefore requires
a working AEDT installation and license at that later stage.

## User comparison and feedback loop

The default workflow stops after Python generation. The user decides when to open HFSS, runs
the code, compares the geometry with the source image, and then submits concrete corrections:

```text
submit_antenna_model_feedback(
  job_id,
  feedback="Move the feedline 0.5 mm left; keep every reviewed dimension unchanged.",
  comparison_images=["hfss-comparison.png"]
)
regenerate_antenna_python_from_feedback(job_id)
```

The comparison files are copied into the job and hash-frozen. Regeneration produces
`generated_model_v002.py`; it never overwrites `v001`. Repeating the same export inputs is
idempotent and returns the existing revision.

Simulator build, solve, and optimization remain separate explicit operations. They are not
triggered by source analysis, code generation, feedback submission, or regeneration.
