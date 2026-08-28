# Kaur 2021 split-ring-slot UWB validation cases

Source: Kaur, Kumar, and Sharma, "[Split Ring Slot Loaded Compact CPW-Fed
Printed Monopole Antennas for Ultra-Wideband Applications with Band Notch
Characteristics](https://doi.org/10.2528/PIERC20122401)," *Progress In
Electromagnetics Research C*, 110, 39-54, 2021. The archived PDF has SHA-256
`a963e940398cfbdb4587128fd74557637695b90c223267b9dc5edb3a0b6fc580`.

This directory freezes three separate designs. Their parameters must never be mixed:

- `Kaur2021BaselineUWB`: rectangular CPW-fed UWB monopole without a slot;
- `Kaur2021WLANNotch`: only the `R1=2.4`, `R2=2.1`, `S1=0.4 mm` split-ring slot;
- `Kaur2021XBandNotch`: only the primed `R1=2.1`, `R2=1.6`, `S1=0.4 mm` slot.

The common paper dimensions are an 18 x 18 x 1.6 mm FR-4 board with
`er=4.4`, `tan(delta)=0.02`, `WP=13.5`, `LP=9`, `WG=7.95`, `LG=5.4`,
`WF=1.2`, `LF=5.934`, `X1=6`, and `Y1=1.5 mm`. The paper reports VSWR at
or below 2 from 3-12 GHz for the baseline, a 5.15-5.81 GHz WLAN rejection
band centred near 5.3 GHz, and a 7.16-7.71 GHz X-band rejection band centred
near 7.4 GHz.

## Evidence boundary

The figures do not dimension the vertical patch position or split-ring centre,
and the paper does not specify conductor thickness, CPW port construction,
radiation clearance, mesh, convergence, or numeric S-parameter samples. The HFSS
translation therefore isolates these choices in `assumptions.json`. In particular,
the feed, matching stub, and patch are forced to be contiguous; the ring centre is
placed on the lower patch edge; conductors are zero-thickness PEC sheets; and an
assumed 50-ohm lumped CPW port is used.

Files:

- `benchmark_*.json`: three non-mixed geometry, material, operation, and solver contracts;
- `reference_model.py`: import-safe deterministic builder for all three designs;
- `run_reference.py`: safe builder, optional solver, and S11 exporter;
- `paper_targets.py`: VSWR-to-dB paper gate for matching, notch interval, centre,
  and reported simulated peak;
- `reference_data/hfss_reference_outcomes_2026_08_26.json`: immutable three-case
  local outcomes, curve hashes, and XZ-port execution correction;
- `assumptions.json`: explicit paper/assumption boundary;
- `references/kaur_2021_split_ring_monopole.pdf`: archived source paper.

## Build and solve

With a legitimate working AEDT license, build all three designs in a new project:

```powershell
$env:ANTENNA_MCP_AEDT_EXECUTABLE = "D:\path\to\AnsysEM\ansysedt.exe"
$env:ANTENNA_MCP_GRPC_MODE = "insecure"

.\.venv\Scripts\python.exe `
  ".\examples\validation\kaur_split_ring_monopole\run_reference.py" `
  --version 2025.1 `
  --case all `
  --solve
```

To attach to an empty project in an already open AEDT window:

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\kaur_split_ring_monopole\run_reference.py" `
  --version 2025.1 `
  --grpc-port 50051 `
  --active-project Project8 `
  --case all `
  --solve
```

The runner refuses fallback launch, existing target designs, existing projects, and
existing result files. It leaves an attached AEDT window open. Each solved design
gets an independent `reference_<case>_s11.csv` and `paper_target_<case>.json` under
ignored `local_results/`. Exit code 2 means HFSS completed but at least one curve did
not pass the paper gate; that curve must not become a reference baseline.

The paper gate uses the exact conversion `VSWR=2` to `S11=-9.542425 dB`. A notch is
high reflection, so its complete reported interval is required to remain at or above
that boundary while the two surrounding bands remain at or below it. The observed
notch peak must be within 0.15 GHz of the stated centre and within 20% of the paper's
simulated peak VSWR. These are declared project thresholds, not universal standards.

Passing these gates only establishes a local reference. An independently generated
candidate must still pass its matching benchmark contract and a same-version S11
comparison through `antenna-workflow validate`.

## Recorded local outcome (2026-08-26)

All three designs were solved in AEDT 2025.1/PyAEDT 0.26.3 after correcting the API
ordering of the physical 2.1 mm by 1.6 mm XZ lumped-port sheet. The baseline's worst
S11 over 3-12 GHz is -1.244 dB instead of at or below -9.542 dB. The WLAN and X-band
cases maintain high reflection inside their reported notch intervals, but both fail
the surrounding UWB matched bands and miss the notch-centre/peak gates. None is an
accepted reference. Exact values and curve hashes are frozen in
`reference_data/hfss_reference_outcomes_2026_08_26.json`.
