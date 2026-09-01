# Khan 2024 28/38 GHz single-element benchmark

This benchmark reconstructs only the proposed single element in Figure 1 of
Khan et al., “A novel dual-band MIMO antenna for 5G millimeter-wave
applications” (Scientific Reports 2024, DOI: 10.1038/s41598-023-50446-0).
The later MIMO arrangement is deliberately excluded.

The paper gives all 22 labels in Table 1, a 5 mm by 9.2 mm Rogers RT5880 board,
0.787 mm substrate thickness, relative permittivity 2.2, loss tangent 0.0009,
and the two reported -10 dB bands 24.86–28.65 GHz and 36.24–40.82 GHz. It does
not give a machine-readable vertex list or numeric S11 data.

`benchmark.json` freezes paper facts. `assumptions.json` separately records the
Figure 1 coordinate interpretation, zero-thickness PEC conductor, port,
radiation region, mesh/convergence, and sweep choices. `reference_model.py`
V2 reference creates `Khan2024SingleElement28_38GHz_V2`; it refuses to build
inside a non-empty design. `paper_targets.py` fails closed unless the converged
curve reproduces both complete paper bands, their approximate resonances, and
their minimum depths.

Run from external PowerShell, not AEDT Tools > Run Script:

```powershell
.\.venv\Scripts\python.exe `
  ".\examples\validation\cases\khan_28_38ghz_monopole\run_case.py" `
  --version 2025.1 `
  --grpc-port 50051 `
  --active-project Project8 `
  --solve
```

The launcher preserves every existing Project8 design and refuses to overwrite
the Khan design or previous local outputs. A normal HFSS solve is not enough:
`local_results_v2/paper_target_report.json` must say `"passed": true` before
this case can be called an accepted reference.

## Recorded outcome

The case is `solved/fail-paper-gate`. V1 converged but used an incorrect
annulus interpretation. Figure 2 exposed that error, so V2 replaced it with the
nested-U topology without changing a Table 1 value. V2 converged in three
adaptive passes (`Delta S=0.01387`) and produced a second band at approximately
37.80–42.17 GHz, but its minimum was displaced to 40.90 GHz and the lower
response did not form the published independent resonance.

A frozen one-at-a-time study then solved an internal lumped-port variant and
1 mm/4 mm radiation-padding variants. All three converged and none passed both
paper bands. The immutable summary and curve hashes are in `reference_data/`;
candidate generation remains closed until new source evidence supports a new
coordinate or HFSS implementation hypothesis.
