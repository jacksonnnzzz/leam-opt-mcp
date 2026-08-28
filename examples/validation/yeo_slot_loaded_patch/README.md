# Yeo 2019 slot-loaded patch validation cases

Source: [Yeo and Lee, *Electronics* 2019, 8, 502](https://doi.org/10.3390/electronics8050502)

This directory contains two unloaded antenna cases from Figure 1 of the open-access
paper:

- `YeoConventionalPatch`: the Figure 1(a) conventional inset-fed patch.
- `YeoScaledSlotLoadedPatch`: the Figure 1(c) scaled radiating-edge-slot-loaded patch.

The Figure 1(b) unscaled slot-loaded antenna is deliberately not mixed into either
case. The paper used CST Microwave Studio. These scripts provide a reproducible HFSS
2025.1/PyAEDT 0.26.3 translation, so cross-solver differences remain possible.

## Evidence boundary

Published values are frozen in the two `benchmark_*.json` contracts and in
`reference_data/literature_targets.json`. The shared RF-35 substrate is 80 mm by
80 mm by 0.76 mm with relative permittivity 3.5 and loss tangent 0.0018.

| Quantity | Conventional | Scaled slot-loaded |
| --- | ---: | ---: |
| Patch width x length | 40 x 31.9 mm | 31.8 x 25.4 mm |
| Feed width x length | 1.66 x 24.5 mm | 1.66 x 27.3 mm |
| Inset width x length | 2.8 x 9 mm | 2.3 x 12 mm |
| Slot width x length | - | 1 x 29.8 mm |
| Slot-to-radiating-edge distance | - | 1 mm |
| Paper resonance(s) | 2.5 GHz | 2.5 and 3.465 GHz |
| Paper first -10 dB band | 2.490-2.510 GHz | 2.496-2.503 GHz |

The paper does **not** resolve conductor material/thickness, port geometry, radiation
boundary clearance, mesh settings, or sweep settings. The implementation therefore
records these separately as engineering assumptions:

- copper conductors, 0.035 mm thick;
- a centered inset opening with the feed entering from the `y=0` board edge;
- an unloaded model (no material-under-test superstrate);
- HFSS Driven Modal with a one-mode 50-ohm wave port;
- 30 mm radiation padding, except at the feed-plane port face;
- 2.5 GHz adaptive setup and a dense 1.5-3.7 GHz interpolating sweep.

Do not report an HFSS-to-paper mismatch as a geometry-recognition failure until these
unresolved assumptions and the CST-to-HFSS solver change have been considered.

## Files

- `reference_model.py`: import-safe parameter source and builder for both designs.
- `run_reference.py`: safe project builder, optional solver, and CSV exporter.
- `paper_targets.py`: compares solved local HFSS curves with explicit prose targets.
- `benchmark_conventional.json`: conventional-case validation contract.
- `benchmark_scaled_slot_loaded.json`: scaled-case validation contract.
- `reference_data/literature_targets.json`: text-derived targets and provenance.
- `reference_data/conventional_hfss_assumption_study_2026_08_26.json`: immutable
  local baseline/controlled-variant outcome and curve hashes.
- `reference_data/scaled_hfss_assumption_study_2026_08_26.json`: immutable
  scaled-case baseline/controlled-variant outcome and curve hashes.
- `reference_data/DIGITIZATION.md`: Figure 2 digitization method and limitations.

The Figure 2 CSVs are visual diagnostics only. In particular, the conventional curve
is obscured near resonance and the red scaled curve is broadened by raster line width.
They must not be used as strict full-curve RMSE golden data. Use the explicit prose
targets for paper agreement and same-version local HFSS reference curves for strict
candidate-to-reference comparison.

## Build in a new AEDT session

The default builds both designs in one project and refuses to overwrite an existing
project or S11 file:

```powershell
$env:ANTENNA_MCP_AEDT_EXECUTABLE = "D:\path\to\AnsysEM\ansysedt.exe"
$env:ANTENNA_MCP_GRPC_MODE = "insecure"

.\.venv\Scripts\python.exe `
  ".\examples\validation\yeo_slot_loaded_patch\run_reference.py" `
  --version 2025.1 `
  --solve
```

Use `--case conventional` or `--case scaled_slot_loaded` to build only one design.

## Attach strictly to an open AEDT project

First open a project in AEDT and read its gRPC port from Message Manager. The command
below refuses to launch a fallback Desktop, refuses to modify either target design if
it already exists, and leaves the user's AEDT window open:

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\yeo_slot_loaded_patch\run_reference.py" `
  --version 2025.1 `
  --grpc-port 50051 `
  --active-project Project5 `
  --solve
```

Local projects and curves are written under `local_results/` and ignored by Git.
Because a failed AEDT operation can leave a partially constructed design, use an empty
or backed-up project for reference generation.

When `--solve` succeeds, the runner also writes
`local_results/paper_target_report.json`. This report is a separate, machine-readable
quality gate with per-check observed values:

- every stated resonance must have an interior local minimum within its case window,
  with at most 1% relative frequency error;
- the first -10 dB lower and upper edges may each differ by at most 50% of the
  paper-reported first-band width;
- the first -10 dB bandwidth may differ by at most 50%.

These deliberately stated project thresholds are not universal EM standards. The main
command reports both successful execution and `paper_target_status`; a paper-target
mismatch remains distinct from an AEDT execution or license failure. The S11 CSV is
exported only after the runner confirms that PyAEDT returned exactly one self-reflection
expression of the form `dB(S(port,port))`, and numeric frequencies are converted using
the unit reported by `SolutionData.units_sweeps`.

## Recorded conventional-case outcome (2026-08-26)

The baseline and three controlled conductor/port variants were solved locally in AEDT
2025.1/PyAEDT 0.26.3. All four retained the paper-explicit dimensions and RF-35
properties. None passed the complete paper gate. The closest result was the solid-copper
internal-lumped-port variant: 2.470 GHz, -10.061 dB, with a 2.469031-2.472113 GHz
-10 dB interval. It still fails the 1% resonance limit and the published
2.490-2.510 GHz band-edge/bandwidth checks. Therefore it is not promoted to a local
candidate reference. The exact values and curve SHA-256 hashes are recorded in
`reference_data/conventional_hfss_assumption_study_2026_08_26.json`.

The scaled slot-loaded baseline and the same three controlled variants were also solved.
The baseline placed the first local minimum at 2.482 GHz and produced a
2.478674-2.486260 GHz -10 dB band, but it did not produce the required second local
minimum near 3.465 GHz and its first-band edges were about 17 MHz too low. No controlled
variant reproduced both modes and the narrow first band, so this case is likewise not
promoted to a candidate reference. See
`reference_data/scaled_hfss_assumption_study_2026_08_26.json`.

## Validate an independently generated candidate

After exporting candidate curves with the same AEDT/PyAEDT versions, validate each
case separately. For example:

```powershell
antenna-workflow validate `
  --benchmark ".\examples\validation\yeo_slot_loaded_patch\benchmark_conventional.json" `
  --candidate ".\path\to\candidate_conventional_contract.json" `
  --reference-s11 ".\examples\validation\yeo_slot_loaded_patch\local_results\reference_conventional_s11.csv" `
  --candidate-s11 ".\path\to\candidate_conventional_s11.csv" `
  --report ".\path\to\validation_conventional.json"
```

Passing a contract-only comparison establishes geometry/solver-contract agreement,
not electromagnetic correctness. A full claim additionally requires both S11 curves
and a separate check of the local reference against the paper's prose targets.
