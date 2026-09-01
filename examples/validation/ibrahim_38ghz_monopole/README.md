# Ibrahim 2023 38 GHz single-element benchmark

This benchmark reconstructs only **Antenna 3** in Figure 4 of Ibrahim et al.,
“Four-Port 38 GHz MIMO Antenna with High Gain and Isolation for 5G Wireless
Networks” (Sensors 2023, DOI: 10.3390/s23073557). The later four-port MIMO,
isolation stubs, and FSS are outside the contract.

The case is currently `solved/fail-paper-gate`. The AEDT 2025.1 baseline and
eight controlled variants converged, but none produced a -10 dB band or the
reported 38 GHz resonance. It is negative evidence and must not be called a
positive electromagnetic benchmark.

V1 planned five trials: three port/padding variants solved and converged, while
two finite-thickness copper variants stopped during CAD construction because
tangent-only feed and radiator volumes could not be united reliably. V2 then
solved five of five hidden feed-overlap variants. Thus eight variants have
converged electromagnetic evidence, and zero pass the paper gate.

## Evidence boundary

Paper facts are frozen in `benchmark.json`: a 12 mm square, 0.203 mm thick,
epsilon-r 3.55 substrate; 4.94 mm radiator diameter; W1/L1/L2 =
2.2/2.45/2.35 mm slot; Wf/Lf = 0.4/7 mm feed; Lg = 7.7 mm partial ground; and
the reported 36.5-39.5 GHz band with an approximately -30 dB minimum near
38 GHz.

The paper omits loss tangent, conductor model, port plane, open-boundary
placement, mesh/convergence, and sweep details. These are listed separately in
`assumptions.json`. The asymmetric slot coordinate is derived from the three
printed slot dimensions and the circle equation; it is not a solver-tuning
parameter.

## Run in an existing AEDT project

Run this from PowerShell with external CPython, not AEDT `Tools > Run Script`:

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\cases\ibrahim_38ghz_monopole\run_case.py" `
  --version 2025.1 `
  --grpc-port 50051 `
  --active-project Project7 `
  --solve
```

Replace the port and project name with the values shown in the current AEDT
window. The launcher refuses to overwrite an existing same-name design or local
result. A successful solve still passes only if `paper_target_report.json` says
`"passed": true`. The recorded 2026-08-30 run says `false`; its immutable
summary is under `reference_data/`.

After the reference passes, generate an independent workflow candidate and
compare its contract and same-version S11 curve against this accepted reference.

If the baseline fails, `assumption_space.json` and `assumption_adapter.py` define
a five-trial, one-at-a-time diagnostic study. It changes only the undisclosed
port implementation, conductor representation, or radiation padding; all
Figure 4 dimensions remain hash-frozen. Results are written as immutable trial
receipts, convergence records, curves, and rankings by the repository's generic
`assumption-run` command.

`assumption_space_v2.json` addresses a separate CAD-continuity question exposed
by V1: a rectangle ending exactly at a circle tangent has only point contact.
V2 keeps the printed visible `Lf=7 mm` unchanged and varies only how far the
Boolean feed tool extends underneath the circular metal. This hidden overlap
does not change the published outer contour.
