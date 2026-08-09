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

Reading, importing, or reviewing `generated_model.py` requires neither AEDT nor PyAEDT.
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
