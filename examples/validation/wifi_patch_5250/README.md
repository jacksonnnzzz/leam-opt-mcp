# El-Gendy 5.25 GHz single Wi-Fi patch benchmark

Source: Mohamed S. El-Gendy, Imran Ashraf, and Samy El-Hennawey,
"[Wi-Fi Access Point Design Concept Targeting Indoor Positioning for Smartphones and IoT](https://doi.org/10.3390/s22030797),"
*Sensors*, 22(3), 797, 2022. The included source PDF is CC BY 4.0 and has SHA-256
`8b089a4ef87cbf316bae4e1af59c10e358282f0780e6942b49e778d8738c272b`.

This benchmark implements only the **single rectangular antenna element** from Section
3.1, Figure 2, Table 1, and Figure 3. It deliberately excludes the four-element array,
feeding network, extra-phase boards, and complete access point.

The paper states FR-4 with `er=4.5`, `tan(delta)=0.025`, and `h=1.5 mm`; Table 1 gives
`Lg=25.92 mm`, `Wg=WR=34.44 mm`, `LR=20 mm`, `Lp=12.55 mm`, `Wp=17.22 mm`, and
`Xp=2.89 mm`. Its CST curve is reported below -10 dB from 5.15 to 5.35 GHz, centered at
5.25 GHz. The paper provides no numeric S11 data.

Files:

- `benchmark.json`: frozen paper contract, translation assumptions, solver contract,
  and validation thresholds.
- `reference_model.py`: deterministic HFSS implementation; importing it does not start AEDT.
- `run_reference.py`: creates a new independent design and optionally solves/exports CSV.
- `assumptions.json` and `ASSUMPTIONS.md`: machine- and human-readable separation of
  paper evidence from cross-solver assumptions.
- `assumption_space.json`: frozen one-at-a-time search over only paper-unresolved
  conductor, connector, dielectric, feed-length, and radiation-padding choices.
- `assumption_space_v2.json`: frozen 11-trial interaction study over the four
  paper-unresolved choices that improved the v1 full-band metric; it changes no
  paper-explicit value.
- `assumption_adapter.py`: thin adapter from the generic versioned search engine to
  this deterministic HFSS builder and complete-band evaluator.
- `reference_data/hfss_reference_outcome_2026_08_26.json`: immutable rejected and
  source-corrected local outcomes with curve hashes.
- `reference_data/engineering_assumption_search_2026_08_27.json`: frozen metrics,
  convergence evidence, S11 hashes, and recovery history for all ten completed trials.
- `reference_data/source_gap_audit_2026_08_28.json`: page-level primary-source and
  related-source audit establishing that no additional connector, boundary, mesh, or
  numeric S11 evidence was available before planning v2.
- `reference_data/engineering_assumption_interactions_2026_08_28.json`: frozen v2
  convergence evidence, rankings, curve hashes, and comparison with the v1 best trial.
- `references/el_gendy_2022_wifi_access_point.pdf`: archived source paper.

## Important limitation

The original simulation used CST. Conductor thickness/material, SMA dimensions,
boundary placement, meshing, convergence, and numeric S11 samples are not disclosed.
Consequently this is a reproducible HFSS translation, not a bit-for-bit reconstruction.
The frozen assumptions are documented separately and must never be presented as paper
values.

## Build and solve

With a working AEDT license, a fresh session can be used:

```powershell
$env:ANTENNA_MCP_AEDT_EXECUTABLE = "D:\path\to\AnsysEM\ansysedt.exe"
$env:ANTENNA_MCP_GRPC_MODE = "insecure"

.\.venv\Scripts\python.exe `
  ".\examples\validation\wifi_patch_5250\run_reference.py" `
  --version 2025.1 `
  --solve
```

To use an already open AEDT window, first create or open an empty project. The runner
strictly checks the gRPC port, refuses any fallback launch, creates the independent design
`ElGendySinglePatch5250_EdgeReferencedXp`, and leaves the existing window open:

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\wifi_patch_5250\run_reference.py" `
  --version 2025.1 `
  --grpc-port 50051 `
  --active-project Project8 `
  --solve
```

The command refuses to overwrite an existing design, project, or S11 CSV. Outputs go
under ignored `local_results/`. A solved run returns exit code 2 and
`failed_paper_target` if the HFSS translation does not reproduce the paper's complete
5.15-5.35 GHz -10 dB band; such a curve must not be accepted as the reference baseline.

## Validate an independently generated candidate

Solve the candidate with the same AEDT/PyAEDT versions and frozen assumptions, export
`frequency_ghz,s11_db`, then run:

```powershell
antenna-workflow validate `
  --benchmark ".\examples\validation\wifi_patch_5250\benchmark.json" `
  --candidate ".\path\to\candidate_contract.json" `
  --reference-s11 ".\examples\validation\wifi_patch_5250\local_results\reference_s11.csv" `
  --candidate-s11 ".\path\to\candidate_s11.csv" `
  --report ".\path\to\validation_report.json"
```

Contract-only comparison is useful for plumbing tests but is not electromagnetic
correctness evidence.

## Recorded local outcome (2026-08-26)

The first local build incorrectly treated `Xp` as center-referenced. Figure 2 and
Equation (3) instead define an edge-referenced feed position, so that curve was rejected
and preserved only as audit evidence. The corrected design uses `x=-Lp/2+Xp=-3.385 mm`.
It resonates at 5.183 GHz with minimum S11 of -19.320 dB, but the worst point over the
complete 5.15-5.35 GHz band is -7.107 dB. It therefore fails the paper gate and is not a
candidate reference. Exact hashes and both outcomes are frozen in
`reference_data/hfss_reference_outcome_2026_08_26.json`.

## Versioned engineering-assumption search

The ten-trial one-at-a-time batch changes only PTFE/vacuum coax filling, finite/PEC
conductor representation, unpublished probe radii and feed length, or radiation padding.
All 13 paper parameters have one separate frozen SHA-256 shared by every trial. Plan the
batch without AEDT:

```powershell
antenna-workflow assumption-plan `
  --space ".\examples\validation\wifi_patch_5250\assumption_space.json" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v1" `
  --limit 10
```

Run it only after opening AEDT and replacing the port/project with the actual values:

```powershell
antenna-workflow assumption-run `
  --space ".\examples\validation\wifi_patch_5250\assumption_space.json" `
  --adapter ".\examples\validation\wifi_patch_5250\assumption_adapter.py" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v1" `
  --grpc-port 50051 `
  --active-project Project7 `
  --aedt-version 2025.1 `
  --limit 10 `
  --resume
```

No result can pass unless the adaptive Delta S and interpolating sweep converge and the
entire 5.15-5.35 GHz band is at or below -10 dB. See `docs/ASSUMPTION_SEARCH.md` for the
immutable retry and ranking contract.

### Recorded search outcome (2026-08-27)

All ten trials completed with converged adaptive and interpolating solutions. None passed
the complete paper band. The best was `radiation_padding_mm=10.0`, with a worst-band S11
of -8.0203 dB and resonance at 5.196 GHz. The next-best outer-radius and feed-length
variants reached -7.6831 and -7.6598 dB. The other seven trials range from -7.5404 to
-5.8464 dB at their worst target-band point.

Six trials were temporarily blocked when the `elec_solve_hfss` vendor daemon went down
(FlexNet -97,121), then resumed after license recovery. A global AEDT-idle guard now
prevents a client interruption from changing designs while an orphaned solve is still
running. The final frozen record contains ten completed trials, zero solver failures, and
zero paper-gate passes; therefore no tested assumption set may be accepted as the HFSS
reference.

### Recorded interaction search (v2)

A fresh source audit found no additional published connector dimensions, CST boundary or
mesh settings, or numeric Figure 3 samples. V2 therefore remains an explicitly labelled
engineering-assumption study. It combines only the four v1 changes that individually
improved the complete-band metric: 10 mm radiation padding, 1.0 mm probe outer radius,
5.0 mm feed length, and 0.6 mm probe inner radius. The planner selects only combinations
with two through four changed assumptions, producing 11 trials while preserving the same
13-paper-parameter SHA-256 as v1.

Plan it without AEDT:

```powershell
antenna-workflow assumption-plan `
  --space ".\examples\validation\wifi_patch_5250\assumption_space_v2.json" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v2"
```

After opening `Project7` and confirming its actual gRPC port, run:

```powershell
antenna-workflow assumption-run `
  --space ".\examples\validation\wifi_patch_5250\assumption_space_v2.json" `
  --adapter ".\examples\validation\wifi_patch_5250\assumption_adapter.py" `
  --output-dir ".\examples\validation\wifi_patch_5250\local_results\assumption_search_v2" `
  --grpc-port 50051 `
  --active-project Project7 `
  --aedt-version 2025.1 `
  --limit 11 `
  --resume
```

All 11 V2 trials completed in AEDT 2025.1/PyAEDT 0.26.3; all adaptive solutions and
interpolating sweeps converged and none passed the unchanged complete-band gate. The best
interaction was `probe_outer_radius_mm=1.0` with `radiation_padding_mm=10.0`. It resonates
at 5.213 GHz and improves the worst target-band point from the v1 best of -8.0203 dB to
-8.4920 dB, but still misses the -10 dB gate by 1.5080 dB. The complete interaction study
is therefore negative evidence, not an accepted HFSS reference or optimization baseline.
