# Paper benchmark candidate screen

Screened on 2026-08-30 for a second positive G0-G5 validation case. The goal is
not to collect visually plausible antennas. A candidate must have an openly
accessible primary source, be simulated originally in Ansys HFSS, disclose enough
geometry and material information to freeze a source contract, and publish an S11
target that can be checked numerically.

The papers below disclose geometry much better than the previously translated CST
examples. None of them discloses every HFSS implementation detail. Port, air-region,
boundary, mesh, and adaptive-pass choices must therefore remain explicit, versioned
engineering assumptions rather than being presented as paper facts.

## First attempted benchmark (negative result)

### Ibrahim et al. (2023): 38 GHz circular slotted monopole

- Source: https://doi.org/10.3390/s23073557
- License: CC BY 4.0.
- HFSS evidence: the paper states that the antenna is designed and simulated using
  HFSS and reports an HFSS parametric study for the slot width.
- Selected scope: the published single-element `Antenna 3`, not the later four-port
  MIMO/FSS assembly.
- Explicit geometry: 12 mm by 12 mm substrate; circular radiator diameter `R =
  4.94 mm`; slot `W1 = 2.2 mm`, `L1 = 2.45 mm`, `L2 = 2.35 mm`; feedline `Wf =
  0.4 mm`, `Lf = 7 mm`; partial ground `Lg = 7.7 mm`.
- Explicit material facts: Rogers RT/duroid 4003, substrate thickness `0.203 mm`,
  relative permittivity `3.55`.
- Published electromagnetic target: approximately 38 GHz resonance, about -30 dB
  S11 at the minimum, and a reported -10 dB band of about 36.5-39.5 GHz.
- Unresolved paper details: dielectric loss tangent, conductor thickness, exact
  conductor model, excitation/port plane, air-region padding, radiation boundary,
  adaptive setup, mesh controls, and sweep interpolation settings.

Why it was first: it has the smallest Boolean/geometry surface area of the three
candidates and a clearly published single-element S11 curve and passband. The
2026-08-30 AEDT 2025.1 baseline converged (`Delta S=0.01816`), as did eight
controlled variants covering port type, radiation padding, and hidden feed Boolean
continuity. None produced a -10 dB band; the baseline was -4.2186 dB at 38 GHz.
The first study also recorded two finite-thickness conductor variants that stopped
during CAD construction because the tangent-only feed and radiator volumes could
not be united; they are not counted as converged electromagnetic trials.
It is therefore retained as `solved/fail-paper-gate`, not promoted to a positive case.

## Executed candidates

### Khan et al. (2024): 28/38 GHz dual-band monopole

- Source: https://doi.org/10.1038/s41598-023-50446-0
- License: CC BY 4.0.
- HFSS evidence: the paper explicitly states that Ansys HFSS is used for the
  simulation.
- Explicit geometry: a dimensioned front/back/side drawing plus a table containing
  `L`, `W`, `PL`, `QW`, `QL`, `T`, `RL`, `RW`, `SW`, `SL`, `BW`, `BL`, `FL`,
  `FW`, `AL`, `GL`, `Lc`, `AW`, `Ri`, `R`, `G`, and `GW`.
- Explicit material facts: Rogers RT5880, `epsilon_r = 2.2`, `tan_delta = 0.0009`,
  substrate size `5 x 9.2 x 0.787 mm`.
- Published electromagnetic targets for the proposed single element: about
  `24.86-28.65 GHz` and `36.24-40.82 GHz`.
- Unresolved paper details: conductor material/thickness, port construction and
  reference plane, radiation region, mesh, adaptive convergence, and exact sweep.

The 2026-08-31 Project8 run is retained as `solved/fail-paper-gate`. V1 exposed a
visual-topology error; Figure 2 then supported a nested-U V2 correction without changing
Table 1. V2 and three bounded port/boundary variants all converged, but none reproduced
both published resonances and complete bands. This case is negative translation evidence,
not a positive benchmark. A further revision requires new coordinate or author-project
evidence rather than tuning paper dimensions.

## Reserve candidates

### Nejdi et al. (2023): UWB circular fractal monopole

- Source: https://doi.org/10.3390/s23084172
- License: CC BY 4.0.
- HFSS evidence: the paper states that HFSS is used for design, optimization, and
  the reported simulated S11 curve.
- Explicit geometry: dimensioned top/back views and the parameter set `L = 40 mm`,
  `W = 24.5 mm`, `Rp = 12.25 mm`, `Ep = 0.5 mm`, `Z = 4 mm`, `R1 = 12 mm`,
  `R2 = 4.5 mm`, `R3 = 1.68 mm`, `Lp = 13.08 mm`, `Wp = 2.75 mm`, and
  `K = 2 mm`.
- Explicit material facts: FR-4, `epsilon_r = 4.4`, substrate thickness `1.6 mm`,
  and a 50-ohm microstrip feed.
- Published electromagnetic target: UWB behavior with four reported resonance
  regions; the measured -10 dB band is `2.83-10.16 GHz`. The simulated curve must
  be digitized separately before it can be frozen as a numeric reference.
- Unresolved paper details: FR-4 loss tangent, conductor thickness/model, feed-port
  definition, radiation region, mesh, adaptive convergence, and exact sweep.

This case is retained as a visual/Boolean stress test. Its nested fractal rings make it
less suitable than the other two for isolating the first paper-to-HFSS validation
failure.

## Acceptance sequence for the selected case

1. Freeze the paper-only source contract and digitized S11 target without adding HFSS
   assumptions.
2. Generate an import-safe candidate model from the PDF/image/text workflow.
3. Approve a separate engineering-assumption record for every undisclosed HFSS choice.
4. Build reference and generated models under the same AEDT 2025 R1 environment.
5. Require structural contract parity, adaptive convergence, complete S11 coverage,
   resonance/band-edge checks, and curve RMSE before calling the case G0-G5 positive.
6. If the electromagnetic gate fails, retain it as a negative evidence case; do not
   tune paper-explicit dimensions merely to force a pass.
