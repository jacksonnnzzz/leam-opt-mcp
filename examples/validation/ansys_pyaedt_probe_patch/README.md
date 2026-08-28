# Official PyAEDT probe-fed patch benchmark

Source: [Ansys PyAEDT probe-fed patch antenna example](https://examples.aedt.docs.pyansys.com/version/dev/examples/high_frequency/antenna/patch.html)

The official example defines a terminal-solution HFSS model with a copper ground layer,
a 0.5 mm Duroid substrate, a copper signal layer, a 9.57 mm by 9.25 mm patch, and a
probe at relative x offset 0.485. It adapts at 10 GHz and uses an interpolating sweep
from 8 GHz to 12 GHz.

The contract expands PyAEDT's `create_probe_port()` helper into its resulting probe/feed
objects and boundary operations. The expected HFSS model therefore contains nine objects,
matching the official example output rather than treating the entire feed as one object.
The top-level `materials` collection is keyed by material-definition names
(`copper`, `Duroid (tm)`, `vacuum`, `air`, and the system `pec` definition); object-to-material assignments remain
under `objects.*.material`. This matches the generic modeling-job artifact schema and
prevents the validator from disguising a material definition as an object role.

Files:

- `benchmark.json`: frozen comparison contract and project acceptance tolerances.
- `reference_model.py`: local reference implementation of the documented construction.
- `run_reference.py`: builds the reference project and optionally solves/exports S11.
- `candidate_contract.example.json`: validator smoke fixture only; not scientific evidence.

## Generation evidence

`benchmark.json` also carries a top-level `generation_evidence` record verified against
the installed PyAEDT 0.26.3 implementation. It makes the helper-generated geometry
explicit: layer elevations, the `0.25` (25%) stackup resize formulas, patch coordinates,
probe and negative-height feed cylinders, the six 3 mm absolute region offsets, the
largest-area outer-feed face selected for `Probe_PEC`, and the bottom-face wave port plus
PEC-cap construction. The evaluated coordinates use millimetres and the exact parameters
in this benchmark.

This record is producer-side provenance, not an acceptance oracle. The validator parses
it but compares candidates only with the top-level `reference` object, so adding or
refining generation evidence cannot silently add contract checks. The comparison contract
and electromagnetic S11 gate remain unchanged.

## Generate the local reference

Use the same AEDT/PyAEDT installation for the reference and every candidate. The official
web example currently shows AEDT 2026 R1, while this repository also supports AEDT 2025 R1;
therefore the local reference curve must be generated on the machine used for validation.

```powershell
$env:ANTENNA_MCP_AEDT_EXECUTABLE = "D:\path\to\AnsysEM\ansysedt.exe"
$env:ANTENNA_MCP_GRPC_MODE = "insecure"

.\.venv\Scripts\python.exe `
  ".\examples\validation\ansys_pyaedt_probe_patch\run_reference.py" `
  --solve
```

The command refuses to overwrite an existing project. It writes local outputs under
`local_results/`, which is ignored by Git.

If a fresh non-graphical AEDT launch reports `Unable to detect installed products`, open
AEDT manually and attach to the gRPC port displayed in Message Manager. This mode refuses
to fall back to a newly launched Desktop and does not close the existing window:

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\ansys_pyaedt_probe_patch\run_reference.py" `
  --grpc-port 50051 `
  --active-project Project5 `
  --solve
```

## Full comparison

After running the generated candidate with the same HFSS version and solution settings,
export its S11 data as `frequency_ghz,s11_db`, then run:

```powershell
antenna-workflow validate `
  --benchmark ".\examples\validation\ansys_pyaedt_probe_patch\benchmark.json" `
  --candidate ".\path\to\candidate_contract.json" `
  --reference-s11 ".\examples\validation\ansys_pyaedt_probe_patch\local_results\reference_s11.csv" `
  --candidate-s11 ".\path\to\candidate_s11.csv" `
  --report ".\path\to\validation_report.json"
```

The 1% resonance, 5% bandwidth, and 1 dB curve-RMSE thresholds are project acceptance
criteria, not universal electromagnetic standards.

## Recorded independent-candidate result

On 2026-08-26, the independently generated candidate passed the 124/124 frozen contract,
converged in AEDT 2025.1 at adaptive pass 11 (`Max Mag. Delta S=0.013212`), and completed
an 8-12 GHz passive interpolating sweep. Against the same-version 401-point reference,
the candidate passed all G5 criteria:

- resonance: 10.04 GHz versus 9.98 GHz, 0.6012% relative error (limit 1%);
- -10 dB bandwidth: 259.767 MHz versus 257.262 MHz, 0.9736% relative error (limit 5%);
- full-curve RMSE: 0.930577 dB (limit 1 dB).

The candidate S11 SHA-256 is
`ad52401ee13aa7b7f015cbf5e6605425c27de792cac276af067d79366531ed1c`.
Local candidate files and job reports remain ignored; the durable summary is recorded in
`../campaign.json` and `../CORRECTNESS_REPORT.md`.

## Recorded convergence-gated optimization

On 2026-08-28, the repository optimizer ran a bounded 12-trial regression against the
accepted local `OfficialProbeFedPatch` project in AEDT 2025.1/PyAEDT 0.26.3. Before any
solve, the preflight changed `Patch_length`, `Patch_width`, and `probe_x_rel` separately
and verified that every variable changed the full-model bounding-box signature. All 12
trials passed both the adaptive `Delta S <= 0.02` gate and the interpolating-sweep gate.

The first trial was the exact reference parameter point. The best eligible trial was 9:

| Quantity | Baseline | Best trial 9 | Improvement |
| --- | ---: | ---: | ---: |
| `Patch_length` | 9.57 mm | 9.594167 mm | — |
| `Patch_width` | 9.25 mm | 9.206624 mm | — |
| `probe_x_rel` | 0.485 | 0.456882 | — |
| worst S11 in 9.9-10.1 GHz | -9.934836 dB | -11.499926 dB | 1.565090 dB |
| S11 at 10 GHz | -15.475447 dB | -19.285516 dB | 3.810069 dB |
| weighted score (lower is better) | -13.803698 | -16.321306 | 2.517608 |

The source project SHA-256 was identical before and after the run. The separately saved
best project has its own hash. The tracked, machine-readable evidence is
[`reference_data/optimization_study_2026_08_28.json`](reference_data/optimization_study_2026_08_28.json);
raw `.aedt`, `.aedtresults`, and local job files remain Git-ignored. This is evidence that
the optimization workflow works on one accepted benchmark, not proof of a global optimum.

To repeat the workflow on a local accepted project, copy
[`optimization_request.example.json`](optimization_request.example.json), change only the
local project path/session values, then run:

```powershell
antenna-workflow optimization-create .\optimization_request.local.json
antenna-workflow optimization-preflight <optimization-job-id>

$env:ANTENNA_MCP_ALLOW_SIMULATION = "1"
antenna-workflow optimization-run <optimization-job-id>
```
