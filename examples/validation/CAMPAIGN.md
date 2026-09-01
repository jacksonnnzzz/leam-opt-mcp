# Antenna validation campaign

This file is the human-readable index for `campaign.json`. It reports implementation and electromagnetic evidence separately: passing offline tests does not mean that HFSS results are correct, and a license failure does not mean that a model is wrong.

## Current matrix

| Case | Source | Repository state | HFSS evidence | Next gate |
| --- | --- | --- | --- | --- |
| Official PyAEDT probe-fed patch | Official Ansys example | Reference and independent candidate solved; G4 contract 124/124; 12-trial optimizer regression frozen | **candidate-solved/pass**; G5 passes; optimization 12/12 converged and improved worst 9.9-10.1 GHz S11 to -11.4999 dB | Keep as regression baseline; repeat G0-G5 before optimizing paper cases |
| Yeo conventional inset patch | [10.3390/electronics8050502](https://doi.org/10.3390/electronics8050502) | Baseline plus three controlled variants solved | **solved/fail-paper-gate**; no variant reproduced the complete 2.5 GHz and 2.490-2.510 GHz target | Preserve the negative result; do not generate a candidate until a reference translation passes |
| Yeo scaled slot-loaded patch | [10.3390/electronics8050502](https://doi.org/10.3390/electronics8050502) | Baseline plus three controlled variants solved | **solved/fail-paper-gate**; no variant reproduced both modes and the narrow first band | Preserve the negative result; keep candidate gates closed |
| El-Gendy 5.25 GHz Wi-Fi patch | [10.3390/s22030797](https://doi.org/10.3390/s22030797) | Source-corrected design, 10 one-at-a-time variants, and 11 interaction variants solved | **solved/fail-paper-gate**; all 21 trials converged, best worst-band point is -8.492 dB | Freeze negative v2 evidence and keep candidate gates closed |
| Kaur baseline/WLAN-notch/X-band-notch UWB monopoles | [10.2528/PIERC20122401](https://doi.org/10.2528/PIERC20122401) | All three isolated designs solved | **solved/fail-paper-gate**; baseline and both notch cases fail their complete gates | Preserve the negative results; keep candidate gates closed |
| Ibrahim single 38 GHz slotted monopole | [10.3390/s23073557](https://doi.org/10.3390/s23073557) | Baseline and two versioned assumption studies completed in Project8 | **solved/fail-paper-gate**; baseline plus eight controlled variants converged, none produced a -10 dB band | Freeze negative evidence; move to the pre-screened Khan native-HFSS case unless new source files appear |
| Khan single 28/38 GHz nested-U monopole | [10.1038/s41598-023-50446-0](https://doi.org/10.1038/s41598-023-50446-0) | V1, Figure-2-corrected V2, and three controlled variants completed in Project8 | **solved/fail-paper-gate**; V2 converged but shifted the upper minimum to 40.90 GHz and did not produce the independent lower resonance | Freeze negative evidence; obtain the authors' HFSS project or stronger coordinate evidence before another interpretation |

All nine reference designs are now locally solved. The official Ansys probe patch is
the only accepted reference; eight paper-derived designs fail their complete paper
gates. The newest Khan case corrected a V1 visual-topology error using Figure 2, then
solved V2 and three port/boundary variants; all converged and none reproduced both
published bands. It is negative evidence and must not be described as a positive benchmark. A failed paper
translation is useful negative evidence under the recorded assumptions, not proof that
the published geometry itself is wrong.

## 1. Official PyAEDT probe-fed patch - candidate-solved/pass

The official example uses 0.035 mm copper layers, a 0.5 mm Duroid substrate, a 9.57 mm by 9.25 mm patch, a relative probe offset of 0.485, a 10 GHz adaptive setup, and an 8-12 GHz sweep. The expanded HFSS construction has nine objects and Radiation, Perfect E, and Wave Port boundaries.

The local reference passed its gate: resonance at 9.98 GHz, minimum S11 of about -16.12 dB, and -10 dB crossings at approximately 9.85384 and 10.11111 GHz. The independently generated candidate passes G4 at 124/124 and all static Python/PyAEDT execution gates. It then converged in AEDT 2025.1 at adaptive pass 11 (`Max Mag. Delta S=0.013212 < 0.02`); the interpolating sweep converged, remained passive, and completed normally. G5 passed with candidate resonance 10.04 GHz (0.6012% error), -10 dB bandwidth 259.767 MHz (0.9736% error), and full-curve RMSE 0.930577 dB against the same-version reference.

On 2026-08-28 the hardened optimizer also completed a 12-trial real-HFSS regression on
the accepted reference. Variable-effect preflight passed for patch length, patch width,
and relative probe offset; all 12 trials converged, with no rejection or execution failure.
Trial 9 improved the worst S11 over 9.9-10.1 GHz from the baseline -9.934836 dB to
-11.499926 dB and S11 at 10 GHz from -15.475447 dB to -19.285516 dB. The source project
hash was unchanged, and the best project was saved separately. The complete frozen record
is `ansys_pyaedt_probe_patch/reference_data/optimization_study_2026_08_28.json`. This bounded
study validates the optimizer workflow on one accepted case; it is not a global-optimum claim.

On 2026-08-20 the repository's own staged generator attempted an independent candidate.
The first local `qwen3-vl:8b` attempt produced five structured artifacts and then timed out
after 900 seconds in `model_3d`. A safe same-job retry switched only the text stages to
DeepSeek, preserved the Ollama vision model, reused the reviewed source/parameters, and
generated through `simulation_setup`. The retry receipt preserves the previous downstream
files by path, size, and SHA-256; no old versioned artifact was deleted.

The complete candidate still fails offline acceptance. The second contract report passes
90 of 103 checks: parameters 24/24, materials 5/5, operations 42/42, objects 15/22, and
solver 4/10. Five source primitives drifted (`stackup_*`, `rectangular_patch`, and
`open_region` became generic boxes/rectangle), the patch parent and Region boundary are
missing, and six solver fields have the wrong or missing schema. Separately, `model_3d` and
`simulation_setup` incorrectly return nested `def build(hfss)` functions, so their statements
would not execute when assembled. Geometry subtraction is duplicated and the coaxial outer
dielectric conflicts with its annular dimension contract. No candidate HFSS build or solve
was attempted.

The pipeline now rejects those conditions before accepting future artifacts: parameter,
material, and solid records are checked against the immutable source-analysis contract, and
all Python stages must be immediate fragments using the existing `hfss` object. The next
candidate retry starts at `solids`; AEDT remains out of scope until G4 and fragment validation
both pass.

A second strict retry from `solids` then preserved every source primitive and returned immediate
Python fragments. Its generation status is `completed`, but the third contract report passes
only 96 of 104 checks: objects 20/22 and solver 5/11, with eight missing or mismatched relationship
and solver fields. A PyAEDT 0.26.3 static audit also rejects the code: required orientation
arguments are missing, several keyword/method names do not exist in this version, and `model_3d`
already performs the boolean and simulation work that the later stages repeat. The assembled
builder would therefore duplicate objects, boundaries, ports, setup, sweep, and subtraction.
The coaxial geometry and full PEC port disk also overlap physically and lack a closed integration-
line definition. No AEDT execution was attempted. Stage-ownership, structured dimensions/solver,
and installed-PyAEDT API gates are now integrated and reject the old artifacts in a read-only
audit; the next candidate retry starts from `solids`.

That retry then preserved the source relationships in `solids.json` but omitted
`Patch.parent_layer` from its in-memory dimensions candidate. The structured gate stopped at
`dimensions` before registering the artifact or generating Python. A subsequent audit against the
installed PyAEDT 0.26.3 `Stackup3D` implementation showed that the accepted upstream source and
solids were themselves incomplete: they lost the 25% stackup resize and air-filled signal-body
semantics, used the wrong probe-offset equation, made Patch a zero-thickness sheet on the signal
top face, and extended Probe through the signal layer. The generic dimensions gate now checks
stackup continuity, Patch parent-layer elevation/thickness, Probe ground-to-signal span, and
explicit fill/body-material preservation. Because the earliest drift is in source evidence, the
next auditable retry starts at `source_analysis`, not `dimensions`; no candidate AEDT build was
attempted.

The first source-only retry then failed before artifact registration because the old provider
router treated every attachment as visual: the UTF-8 `benchmark.json` was sent to Ollama instead
of the configured DeepSeek text provider and returned an empty response. The generic router now
inlines bounded JSON/Markdown/CSV evidence for the text provider, labels it as untrusted data, and
reserves vision routing for image/PDF or mixed visual requests. The v004 retry receipt remains in
the audit history, while the failed job can resume directly at `source_analysis` without another
retry receipt.

That resumed text request reached DeepSeek correctly but returned `coordinate_system.axes` in a
non-array shape. The source schema stopped it before registration. The prompt now carries an exact
HFSS coordinate-system JSON example and future gate-rejected responses are retained as versioned,
non-executable audit artifacts with an error report and SHA-256.

The next DeepSeek response fixed the coordinate shape but used `evidence` instead of the required
`evidence_source`, placed prose inside `required_relationships`, and described the air-filled signal
layer body as copper. It was retained as rejected source audit v001 and was never registered or
executed. Frozen benchmark attachments can now publish a generic machine-readable source contract;
the source stage checks component identities, role/primitive/material semantics, parameters,
relationships, and ordered operations together. The official contract now also exposes the
Stackup3D internal signal-minus-Patch subtraction with `keep_originals=true`. All 286 offline tests
and the updated 124-field contract smoke fixture pass; this still is not candidate EM evidence.

The subsequent source run completed with the correct 9 components, 12 parameters, and 14 operations,
but pre-downstream human review found that exact ranges had been flattened into prose-only
`geometric_evidence` and that an explicit conductor/fill distinction was still described as
ambiguous. It is therefore not approved for Python/AEDT generation. The frozen source contract now
contains structured per-component geometry, the source gate requires it verbatim, and the solids
gate preserves it into the numeric dimensions gate. The full offline suite is now 286/286.

The structured-geometry retry then matched all components and operations but promoted the producer
constant `percentage_offset_argument=0.25` into a thirteenth design parameter. The frozen reference
has exactly twelve; the source gate rejected and versioned that response. Producer constants may
remain in derived relations but cannot silently become sweep/optimization parameters. The failed
stage can resume directly with `model-run` and does not need another retry receipt.

That direct resume succeeded. The accepted source artifact contains exactly 9 components,
12 reference parameters, 14 ordered operations, X/Y/Z axes, and structured geometry for every
component; Stackup material/body/fill and Probe relationships pass the source topology gate. This
authorizes downstream offline generation only, not candidate AEDT execution or EM acceptance.

The v006 downstream retry reached dimensions. Its candidate was numerically complete, but the
generic validator incorrectly rejected the explicitly null Region material and failed to read the
repository's nested `dimensions` container. Both were validator compatibility defects, not missing
LLM geometry. The corrected gate preserves an upstream null material and compares explicit X/Y/Z
ranges across `geometric_evidence`, `geometry`, or `dimensions`; the original rejected v001 text now
passes read-only revalidation. It remains an immutable rejected audit artifact, so the job must
resume once to register a fresh candidate. At that checkpoint, the full offline suite was 287/287.

The recovered downstream run subsequently passed every structured and Python gate and exported
an immutable full model. The candidate was built into an empty existing Terminal design, inspected,
solved, and exported through the strict read-only S11 helper. Validation revision v005 reports
`status=passed`, `validation_level=electromagnetic`, contract 124/124, and all five S11 checks passed.
The candidate curve contains 401 points over 8-12 GHz and has SHA-256
`ad52401ee13aa7b7f015cbf5e6605425c27de792cac276af067d79366531ed1c`. The current full offline
suite, including the reusable strict exporter, frozen campaign-result, and engineering-assumption
search tests, is 333/333.

## 2. Yeo 2019 conventional inset patch - solved/fail-paper-gate

Paper evidence gives an 80 mm by 80 mm RF-35 board (`er=3.5`, `tan(delta)=0.0018`, `h=0.76 mm`), a 40 mm by 31.9 mm patch, a 1.66 mm by 24.5 mm feed, and a 2.8 mm by 9 mm inset. The paper states a 2.5 GHz resonance and a 2.490-2.510 GHz VSWR < 2 / S11 < -10 dB band, with 0.8% fractional bandwidth and Q = 125.

The deterministic HFSS baseline and three controlled variants were solved in AEDT 2025.1/PyAEDT 0.26.3 with all paper-explicit dimensions and RF-35 properties held fixed. The baseline solid-copper/wave-port translation produced a 2.468 GHz minimum at -7.833 dB and no -10 dB band. The closest variant, solid copper with an internal lumped port, produced 2.470 GHz at -10.061 dB and a 2.469031-2.472113 GHz -10 dB band. Its resonance error is 1.20% (limit 1%), and both band edges and bandwidth fail the published 2.490-2.510 GHz gate. Zero-thickness PEC with wave and lumped ports also failed. Consequently no variant is an accepted local reference and no independent candidate is generated for this case.

The machine-readable negative result, curve hashes, and exact observed values are frozen in `yeo_slot_loaded_patch/reference_data/conventional_hfss_assumption_study_2026_08_26.json`. The paper used CST and leaves conductor, port, boundary, mesh, convergence, and sweep choices unresolved, so this is a CST-to-HFSS reproduction mismatch under unpublished assumptions—not proof that Figure 1(a) was visually extracted incorrectly. Paper-explicit dimensions must not be silently tuned to force a pass.

## 3. Yeo 2019 scaled slot-loaded patch - solved/fail-paper-gate

The scaled Figure 1(c) case uses the same RF-35 board, a 31.8 mm by 25.4 mm patch, a 1.66 mm by 27.3 mm feed, a 2.3 mm by 12 mm inset, and a 1 mm by 29.8 mm radiating-edge slot placed 1 mm from the edge. The paper states resonances at 2.5 and 3.465 GHz and a first -10 dB interval of 2.496-2.503 GHz, with 0.28% fractional bandwidth and Q = 357.

The first band is only about two pixels wide in the published Figure 2 raster, while the plotted red line is 3-5 pixels thick. Accordingly, the digitized CSV is a loose visual aid, not a strict golden curve. The explicit prose values are the paper gate.

The AEDT 2025.1/PyAEDT 0.26.3 baseline and three controlled conductor/port variants were solved with all paper dimensions fixed. The baseline first mode is 2.482 GHz at -12.307 dB (0.72% frequency error) and its 2.478674-2.486260 GHz first band has a close width, but both edges are about 17 MHz below the published interval and no second local minimum appears near 3.465 GHz. The three variants likewise fail to reproduce both modes and the narrow first band. No local reference is accepted, so G4/G5 remain closed. The frozen result and curve hashes are in `yeo_slot_loaded_patch/reference_data/scaled_hfss_assumption_study_2026_08_26.json`.

## 4. El-Gendy 5.25 GHz Wi-Fi patch - solved/fail-paper-gate

Section 3.1, Figure 2, Table 1, and Figure 3 define the single element used here: FR-4 with `er=4.5`, `tan(delta)=0.025`, and `h=1.5 mm`; `Lg=25.92 mm`, `Wg=WR=34.44 mm`, `LR=20 mm`, `Lp=12.55 mm`, `Wp=17.22 mm`, and `Xp=2.89 mm`. The paper target is S11 <= -10 dB across 5.15-5.35 GHz around 5.25 GHz.

The first local build incorrectly treated `Xp` as center-referenced. A visual audit of Figure 2 and Equation (3) established that it is measured from a radiating edge, so the contract and deterministic coordinate were corrected to `x=-Lp/2+Xp=-3.385 mm`; the old curve remains only as rejected audit evidence. The corrected AEDT 2025.1/PyAEDT 0.26.3 design resonates at 5.183 GHz with minimum S11 -19.320 dB, but its worst point over the complete 5.15-5.35 GHz band is -7.107 dB. The paper gate therefore fails and G4/G5 remain closed.

The paper does not publish conductor or SMA/coax details, boundary/mesh settings, or numeric S11 data. Those remain possible cross-solver causes but do not authorize changing Table 1 dimensions. Both curve hashes and the correction record are frozen in `wifi_patch_5250/reference_data/hfss_reference_outcome_2026_08_26.json`.

On 2026-08-27 the repository added a generic immutable assumption-search ledger and a
Wi-Fi adapter. Ten one-at-a-time trials share one frozen hash for all 13 paper parameters
and vary only paper-unresolved conductor, connector dielectric, probe radii, feed length,
or radiation padding choices. All ten designs completed in AEDT 2025.1/PyAEDT 0.26.3;
every adaptive solution reached `Max Mag. Delta S <= 0.02` and every interpolating sweep
converged. None passed the complete published band. The best variant uses 10 mm radiation
padding, resonates at 5.196 GHz, and has a worst target-band S11 of -8.0203 dB. The other
nine worst-band values range from -7.6831 to -5.8464 dB.

Six trials were initially blocked by a FlexNet vendor-daemon outage and later resumed.
Four solves interrupted by the abandoned client were then recovered after the generic
runner gained a global AEDT-idle guard, which prevents switching designs while a shared
Desktop still has an active solve. The final frozen record contains ten completed trials,
zero failed trials, curve hashes, convergence evidence, and zero paper-gate passes in
`wifi_patch_5250/reference_data/engineering_assumption_search_2026_08_27.json`. This is
negative cross-solver evidence, not permission to tune any Table 1 value.

On 2026-08-28 a page-level audit of the primary paper and its related sources found no
additional connector dimensions, CST boundary/mesh settings, numeric Figure 3 samples,
or downloadable CST project. The result is frozen in
`wifi_patch_5250/reference_data/source_gap_audit_2026_08_28.json`. Consequently the v2
plan does not add any claimed paper value. It selects the four v1 changes that individually
improved the complete-band metric and enumerates only their 2-way, 3-way, and 4-way
combinations: 11 immutable trials in `wifi_patch_5250/assumption_space_v2.json`. These
trials were then solved in Project7 with AEDT 2025.1/PyAEDT 0.26.3. All 11 adaptive
solutions and interpolating sweeps converged, with no infrastructure failures, but none
passed the paper gate. The best two-way interaction combines a 1.0 mm probe outer radius
with 10 mm radiation padding; it improves the v1 best worst-band point by 0.4717 dB to
-8.4920 dB at a 5.213 GHz resonance, still 1.5080 dB short of the complete-band gate.
The immutable rankings and S11 hashes are frozen in
`wifi_patch_5250/reference_data/engineering_assumption_interactions_2026_08_28.json`.

## 5. Kaur 2021 split-ring-slot monopoles - solved/fail-paper-gate

The candidate paper reports an 18 mm by 18 mm FR-4 CPW-fed monopole (`er=4.4`, `tan(delta)=0.02`, `h=1.6 mm`). Table 1 gives `WF=1.2`, `LF=5.934`, `WP=13.5`, `LP=9`, `WG=7.95`, `LG=5.4`, `X1=6`, and `Y1=1.5 mm`. Table 2 gives WLAN SRS radii/gap `2.4/2.1/0.4 mm` and X-band values `2.1/1.6/0.4 mm`.

The three cases are now frozen as separate contracts and deterministic HFSS designs:
an unnotched baseline, a WLAN-only SRS, and an X-band-only SRS. The feed, matching
stub, and patch are constrained to touch exactly. Figure 7's undimensioned vertical
placement, SRS centre, split direction, conductor model, CPW lumped port, radiation
padding, and solver settings are all labelled assumptions rather than paper facts.

The baseline gate requires VSWR <= 2 over 3-12 GHz. Each notched gate requires the
complete published rejection interval at VSWR >= 2, matching outside the notch,
peak-centre error <= 0.15 GHz, and simulated peak-VSWR relative error <= 20%. The
builder, CSV exporter, case isolation, physical connectivity, unit conversion, and
reversed notch inequality pass offline tests.

All three designs were then solved in AEDT 2025.1/PyAEDT 0.26.3 after correcting the
XZ rectangle API ordering for the physical 2.1 mm by 1.6 mm lumped-port sheet. The
baseline's worst S11 over 3-12 GHz is -1.244 dB, far above the -9.542 dB VSWR=2
boundary. The WLAN and X-band cases do remain high-reflection across their reported
notch intervals, but their observed peaks occur at 5.81 and 7.71 GHz, and both fail
their surrounding matched bands and peak-magnitude checks. None is an accepted local
reference. Exact values and hashes are frozen in
`kaur_split_ring_monopole/reference_data/hfss_reference_outcomes_2026_08_26.json`.

## 6. Ibrahim 2023 38 GHz single element - solved/fail-paper-gate

The benchmark is limited to Antenna 3 in Figure 4 and keeps the paper-explicit
`12 x 12 x 0.203 mm` substrate, `er=3.55`, `4.94 mm` circular radiator diameter,
`W1/L1/L2=2.2/2.45/2.35 mm`, `Wf/Lf=0.4/7.0 mm`, and `Lg=7.7 mm` fixed. It does not
mix in the four-port MIMO, decoupling, or FSS structures.

The Project8 baseline converged after six adaptive passes with final `Delta S=0.01816`,
but its minimum is only -5.2409 dB at 41.47 GHz; S11 at 38 GHz is -4.2186 dB and there
is no -10 dB band. The first bounded study changed only unpublished port, conductor,
and radiation-padding choices: three variants solved and converged, two finite-thickness
variants failed the tangent-volume union, and none passed. The second study changed only
the hidden feed/radiator Boolean overlap while preserving the published visible outline;
all five variants solved and converged, but the equivalent united geometry produced the
same approximately -5.7676 dB minimum at 41.10 GHz and no -10 dB band.

The baseline and eight completed controlled variants therefore remain negative evidence.
G4/G5 are closed, and paper-explicit dimensions must not be tuned to force agreement.
The immutable convergence metrics and curve hashes are in
`ibrahim_38ghz_monopole/reference_data/hfss_reference_and_assumption_studies_2026_08_30.json`.

## 7. Khan 2024 28/38 GHz single element - solved/fail-paper-gate

The paper supplies a 5 x 9.2 x 0.787 mm Rogers RT5880 board, `er=2.2`,
`tan(delta)=0.0009`, 22 Table 1 geometry labels, and two reported bands at
24.86-28.65 and 36.24-40.82 GHz. Only the proposed single element is in scope.

V1 converged but incorrectly treated the round-ended top slot as a separate annulus.
Figure 2 makes the actual topology explicit: an outer inverted-U trace, an inner U,
and two top rods separated by a round-ended slot. V2 corrected only this topology
interpretation, preserving every Table 1 value. It converged in three adaptive passes
with final `Delta S=0.01387`. Its second -10 dB interval is approximately
37.80-42.17 GHz with a -20.0647 dB minimum at 40.90 GHz; the lower response stays
below -10 dB over the target interval but does not form the separate plotted minimum.
It therefore fails the two-resonance, depth, and band-edge gate.

A three-trial one-at-a-time study then changed only the unpublished port construction
and radiation padding. All three trials converged and none passed. The best complete
band result used 1 mm padding: lower-band worst S11 -12.1844 dB, upper-band worst
S11 -9.0388 dB, with the minima still displaced to the right edges. The frozen record
and curve hashes are in
`khan_28_38ghz_monopole/reference_data/hfss_reference_and_assumption_study_2026_08_31.json`.
No independent candidate is generated while G3 remains closed.

## Evidence policy

The durable source of machine-readable status is `campaign.json`. Generated projects, ports, user-specific directories, and temporary job identifiers are intentionally excluded. The campaign is complete only when each implemented paper case has:

1. a frozen evidence/assumption contract;
2. an offline-tested deterministic builder;
3. a same-version local HFSS reference that passes the paper gate;
4. an independently generated and independently solved candidate;
5. a full geometry plus S11 comparison report.
