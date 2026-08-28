# Yeo 2019 Figure 2 reference extraction

This directory separates two different kinds of evidence:

- `literature_targets.json` contains dimensions and S11 targets explicitly stated in the paper. These are the primary correctness targets.
- `figure2_scaled_slot_loaded_digitized.csv` is an approximate raster extraction of the red Figure 1(c) trace.
- `figure2_conventional_visible_only.csv` contains only unoccluded black pixels around the Figure 1(a) resonance. It is deliberately incomplete.

## Reproduction

From the repository root:

```powershell
.\.venv\Scripts\python.exe .\tools\digitize_yeo_figure2.py
```

The script extracts the largest embedded raster on PDF page 4 and refuses to run if its expected `709 x 520` pixel dimensions change. The source PDF SHA-256 is:

```text
45ae972c816bf63e78dbd691bdb6da50f739ddbf3ea6537ccc1dffb89169d12a
```

The extracted Figure 2 raster SHA-256 used during calibration was:

```text
da257407dd65536cc0812ca6771c5b8511d6747e53027e9f1f43746c90513ae5
```

## Axis calibration

The plot box in the embedded image was calibrated from tick intersections:

| Quantity | Pixel/value mapping |
| --- | --- |
| Frequency | `x = 100 -> 1.50 GHz`; `x = 684 -> 3.50 GHz` |
| S11 | `y = 10 -> 0 dB`; `y = 431 -> -30 dB` |
| Horizontal resolution | 3.424657534 MHz/pixel |
| Vertical resolution | 0.071258907 dB/pixel |

Axis placement to half a pixel corresponds to about `+/-1.71 MHz` and `+/-0.036 dB`. The plotted trace is approximately 3-5 pixels thick, so practical uncertainty near a steep band edge is closer to `+/-5 to +/-9 MHz`. Away from a steep edge, the vertical line-width uncertainty is approximately `+/-0.14 to +/-0.21 dB`.

The red trace is selected by color. The red legend sample is masked, and the deepest red pixel in each frequency column is retained so that narrow notches remain visible. This lower-envelope rule slightly broadens narrow notches. Samples at the 0 dB ceiling and -30 dB floor are explicitly labelled in the CSV.

The black conventional trace is dash-dot and is overdrawn by the red trace around 2.5 GHz. Only isolated visible black pixels in the resonance neighbourhood are exported; missing dash intervals and the red-overdrawn centre are not synthesized.

## Primary text-derived targets

| Case | Resonance(s) | Published VSWR < 2 / S11 < -10 dB band | Other published target |
| --- | --- | --- | --- |
| Conventional Figure 1(a) | 2.500 GHz | 2.490-2.510 GHz | 0.8% fractional bandwidth; Q = 125 |
| Scaled slot-loaded Figure 1(c) | 2.500 and 3.465 GHz | first band 2.496-2.503 GHz | first-band 0.28%; Q = 357 |

These numbers come from the prose on PDF page 5, not from pixel estimation.

## Digitized observations

- The scaled first-resonance floor plateau is centred at 2.500 GHz, but the trace hits the -30 dB plot floor. Its actual minimum is therefore only known to be at or below -30 dB.
- The lower-envelope raster extraction gives a first -10 dB interval of approximately 2.4881-2.5095 GHz (21.3 MHz). This conflicts with the explicit 7 MHz value because the published notch is only about two image pixels wide while the plotted stroke is 3-5 pixels wide. The digitized bandwidth must not be used as the acceptance target.
- The scaled second notch gives an approximate raster minimum of -12.0 dB near 3.462 GHz. The paper explicitly states 3.465 GHz, which is within the pixel/line-width uncertainty. Approximate digitized -10 dB crossings are 3.4508 and 3.4738 GHz.
- The conventional trace cannot yield a defensible minimum or bandwidth from Figure 2 because the red trace hides its centre and crossings. Use the published 2.490-2.510 GHz band instead.

## Validation use

Use `literature_targets.json` for geometry, resonance-frequency, and first-band checks. Do not use either CSV for strict full-curve RMSE. The red CSV is suitable only for a loose shape audit, and the black CSV is suitable only for confirming the visible shoulder shape.

The paper states that CST Microwave Studio was used, but it does not specify conductor material/thickness, port construction, boundary clearance, meshing, convergence, or sweep details. Those choices must be recorded as HFSS implementation assumptions before interpreting cross-solver disagreement.
