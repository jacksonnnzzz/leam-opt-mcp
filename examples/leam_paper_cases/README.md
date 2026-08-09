# LEAM paper reconstruction scripts

This directory is an offline deliverable for the paper examples.  Each
`generated_model_v001.py` file is import-safe: generating or importing it does
not start AEDT, consume a license, create a project, save a project, or solve a
model.  It exposes one function:

```python
build(hfss)
```

Pass an HFSS object that you connected to deliberately.  The function only
creates parameters, materials, geometry, and Boolean operations in that
existing object.  Ports, boundaries, meshes, sweeps, analysis, and project
saving are intentionally outside these reconstruction scripts.

For direct execution inside AEDT, each case directory also contains
`run_in_aedt.py`.  Keep the target project open, choose **Tools > Run Script**,
and select that file.  The wrapper activates the sole open project and creates
a new, uniquely named HFSS design for the case.  It uses AEDT's native scripting
API, so it does not need PyAEDT inside AEDT's embedded Python environment.  It
builds the model but intentionally leaves the project unsaved and unsolved.

## Cases

| Directory | Paper object | Reconstruction state |
| --- | --- | --- |
| `demo_l_slot` | Fig. 3 L-slot patch demonstration | All shown conductor dimensions resolved |
| `case1_vivaldi` | Fig. 4 Vivaldi antenna | Topology and figure dimensions resolved; 20 spline samples are an explicit visual-fit assumption |
| `case2_slotted_patch` | Fig. 5 rectangular slotted patch | Patch and slot resolved; substrate/feed/metal values are explicit engineering assumptions |
| `case3_monopole` | Fig. 7 quasi-cross-slotted monopole | Figure dimensions, correction, FR-4 data, and topology resolved; copper thickness is explicit assumption |

Every case also has `evidence_and_assumptions.json`.  Values tagged `paper` are
transcribed from the PDF or its accompanying description.  Values tagged
`visual_interpretation` describe topology read from the figure.  Values tagged
`assumption` are deliberately isolated and should be replaced after comparison
in HFSS.

`reconstruction_requests.json` records the paired visual target and language
constraint used for each result.  It is the reproducible input side of the
example package; `case_manifest.json` is the output index.

The paper PDF is intentionally not redistributed. Put your legally obtained
copy in `references/` using the filename shown in `reconstruction_requests.json`,
or change the request to another local path. PDF files under that directory are
ignored by Git.

## Human-in-the-loop workflow

1. Review `evidence_and_assumptions.json` and edit only the uncertain values at
   the top of `generated_model_v001.py`.
2. Open AEDT/HFSS yourself and connect a PyAEDT `Hfss` object to the desired
   project/design.
3. Import the selected script and call `build(hfss)`.
4. Compare the HFSS geometry with the source figure.
5. Submit textual corrections and optional screenshots through the project's
   feedback workflow.  Regeneration should create `generated_model_v002.py`
   rather than overwriting version 1.

For a Windows project, the helper below applies a selected file to the currently
active HFSS design and deliberately leaves the project unsaved:

```powershell
.\.venv\Scripts\python.exe .\tools\apply_generated_model.py `
  ".\examples\leam_paper_cases\case3_monopole\generated_model_v001.py" `
  --expect-project "<your-project-name>"
```

Use `--validate-only` first if you only want to check the artifact without
connecting to AEDT.  Activate an empty HFSS design before running the real
command, because rerunning a script in a populated design can create duplicate
object names.

The scripts are geometry drafts, not validated antenna designs.  A visually
similar shape does not establish the missing excitation, boundary, radiation
box, mesh, or sweep settings.
