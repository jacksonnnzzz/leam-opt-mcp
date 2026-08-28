# Translation assumptions

This benchmark reconstructs only the **single rectangular patch element** in Section 3.1
of El-Gendy et al. It does not model the four-element array, reflector optimization,
feeding network, extra-phase circuits, or complete access point.

## Values stated by the paper

Pages 5-6, Figure 2, Table 1, and Figure 3 explicitly provide:

- FR-4 relative permittivity `4.5`, loss tangent `0.025`, and thickness `1.5 mm`;
- `Lg = 25.92 mm`, `Wg = WR = 34.44 mm`, and `LR = 20 mm`;
- `Lp = 12.55 mm`, `Wp = 17.22 mm`, and `Xp = 2.89 mm`;
- center frequency `5.25 GHz` and a stated `S11 <= -10 dB` band from `5.15 GHz`
  through `5.35 GHz`.

These values are frozen and must not be tuned during reference construction.

## Engineering assumptions required for HFSS

The paper shows an SMA/probe feed but does not publish connector dimensions. It also
omits conductor thickness/material, CST boundary distance, mesh settings, convergence
criteria, and numeric S11 samples. The HFSS translation therefore freezes the following
choices separately from the paper parameters:

- zero-thickness PEC sheets for the patch and reflector;
- a coaxial probe with `0.5 mm` inner radius, `1.225 mm` outer radius, `7.35 mm`
  feed length, vacuum dielectric, and a 50 ohm terminal wave port;
- a continuous reflector of size `WR x (Lg + 2*LR)`, following Figure 2;
- `Lp` along global X and `Wp` along global Y. Figure 2 and Equation (3) define `Xp`
  from a radiating patch edge, so the deterministic feed coordinate is
  `(-Lp/2 + Xp, 0)`; choosing the opposite edge or rotating in-plane is physically
  equivalent for the isolated symmetric element;
- a 15 mm absolute radiation-region offset;
- HFSS Driven Terminal, adaptation at `5.25 GHz`, and an interpolating sweep from
  `5.0 GHz` to `5.5 GHz`.

These assumptions define a reproducible **translation baseline**, not a claim that they
were used in the original CST model. If the local HFSS reference does not meet the
paper's stated 5.15-5.35 GHz passband, revise a documented assumption and record a new
benchmark revision. Never silently change a Table 1 value to force agreement.
