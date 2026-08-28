# Correctness validation

The validation layer prevents a visually plausible model from being reported as a
validated antenna. It separates two claims:

1. **Contract validation** compares parameters, materials, objects, Boolean operations,
   ports, boundaries, and solver declarations against a frozen reference.
2. **Electromagnetic validation** compares numeric S11 curves produced with the same
   AEDT/PyAEDT environment.

If a benchmark requires S11 and either curve is absent, full validation returns
`status: incomplete` and `quality_gate_passed: false`. An optional S11 check may be omitted
only when **both** curves are absent; supplying just one curve is always incomplete. A run
without a completed curve comparison reports `validation_level: contract` and leaves
`electromagnetic_results_validated` false. Use `--contract-only` only when you intentionally
want an offline structural check.

## Candidate manifest

```json
{
  "schema_version": "1.0",
  "benchmark_id": "ansys_pyaedt_probe_patch",
  "provenance": {
    "kind": "generated_model",
    "job_id": "mdl-example"
  },
  "model": {
    "parameters": {},
    "materials": {},
    "objects": {},
    "operations": [],
    "solver": {}
  }
}
```

The benchmark's `reference` object is treated as the required subset for ordinary nested
records, so additional descriptive metadata fields in a candidate are allowed. The
top-level semantic collections `parameters`, `materials`, and `objects` are exact by name,
and arrays such as `operations` are exact by membership and order: unexpected members fail
because extra solids, variables, materials, or operations can change the electromagnetic
result.

## Contract check

```powershell
antenna-workflow validate `
  --benchmark ".\examples\validation\ansys_pyaedt_probe_patch\benchmark.json" `
  --candidate ".\examples\validation\ansys_pyaedt_probe_patch\candidate_contract.example.json" `
  --contract-only `
  --report ".\tmp\validation_report.json"
```

For a modeling job that already contains `parameters.json`, `materials.json`,
`solids.json`, and `dimensions.json`:

```powershell
antenna-workflow validate `
  --benchmark ".\path\to\benchmark.json" `
  --job-id "mdl-..." `
  --contract-only
```

The job form stores immutable `validation_candidate_vNNN.json` and
`validation_report_vNNN.json` revisions plus latest-name aliases in the job directory,
and registers all paths in `state.json`.

Generic `ModelingService` jobs do not necessarily have `geometry_manifest.json`. In that
case the validator copies `source_analysis.operations` without reinterpretation and records
the fallback in `model._assembly_audit`; it also accepts the generated
`dimensions.output_contract.solids` layout as well as the older `dimensions.dimensions`
layout. If an operation or solver declaration is absent, that field remains absent so the
contract reports a missing requirement. Benchmark values are never inserted into a
candidate.

Material schemas must describe the same thing on both sides. Generic jobs currently key
`materials.json` by material definition (`copper`, `Duroid (tm)`, `vacuum`, and so on),
while some early benchmarks key `reference.materials` by object role (`ground`,
`substrate`, `signal`). The validator deliberately does not coerce one into the other.
Such a comparison fails until the benchmark separates material definitions from object
assignments (the latter already belong in `objects.*.material`). This preserves checks of
physical properties: for example, a generated relative permeability of `0` must fail a
definition contract that requires `1` rather than disappearing behind a role alias.

## Full S11 check

By default, both CSV inputs must contain the exact header below and at least three unique
frequencies:

```csv
frequency_ghz,s11_db
8.0,-0.2
8.1,-0.3
```

```powershell
antenna-workflow validate `
  --benchmark ".\path\to\benchmark.json" `
  --candidate ".\path\to\candidate.json" `
  --reference-s11 ".\path\to\reference_s11.csv" `
  --candidate-s11 ".\path\to\candidate_s11.csv" `
  --report ".\path\to\validation_report.json"
```

The `s11.frequency_unit` benchmark field declares the numeric input unit (`Hz`, `kHz`,
`MHz`, or `GHz`; default `GHz`), and all reported frequencies are normalized to GHz. A unit
suffix in `frequency_column` must agree with that declaration. `value_unit` is currently
restricted to `dB`. Frequencies must be finite and strictly increasing after unit
conversion; the validator does not sort malformed input silently.

The comparison evaluates RMSE on a uniformly spaced grid over the overlap, with at least
the denser input curve's point count. `minimum_overlap_points` applies independently to
both curves, preventing a very sparse candidate from passing merely because the reference
is dense. By default, the overlap must cover at least 99% of the reference sweep; tune
`minimum_reference_coverage_fraction` explicitly only when a benchmark has a justified
partial-range comparison. Legacy single-resonance mode also requires an interior local
minimum and two real threshold crossings; a minimum or apparent band edge at a sweep
boundary fails. It then
checks resonance-frequency error, contiguous -10 dB bandwidth error, and curve RMSE.
Thresholds live in each benchmark and are project acceptance criteria rather than
universal standards.

### Multiple S11 targets

The original `s11` object remains fully supported. If `targets` is absent or empty, the
validator keeps the hardened single-resonance checks and report shape. A multiband
benchmark can add named targets without changing the CSV format:

```json
{
  "s11": {
    "frequency_column": "frequency_ghz",
    "frequency_unit": "GHz",
    "value_column": "s11_db",
    "value_unit": "dB",
    "threshold_db": -10.0,
    "minimum_overlap_points": 20,
    "minimum_reference_coverage_fraction": 0.99,
    "resonance_relative_error_max": 0.01,
    "bandwidth_relative_error_max": 0.05,
    "curve_rmse_db_max": 1.0,
    "required": true,
    "targets": [
      {
        "name": "low_band",
        "kind": "resonance",
        "window_ghz": [2.35, 2.55]
      },
      {
        "name": "high_band",
        "kind": "resonance",
        "window_ghz": [5.65, 5.95],
        "threshold_db": -10.0,
        "resonance_relative_error_max": 0.008,
        "band_edge_relative_error_max": 0.08,
        "minimum_points": 5
      },
      {
        "name": "matched_service_band",
        "kind": "passband",
        "window_ghz": [5.75, 5.85],
        "threshold_db": -10.0
      },
      {
        "name": "wifi_rejection_notch",
        "kind": "notch",
        "window_ghz": [5.15, 5.35],
        "threshold_db": -6.0
      }
    ]
  }
}
```

Unknown benchmark/source/S11 keys and non-finite limits are rejected instead of being
silently ignored. Target names must be unique, windows must contain two finite increasing
GHz values, and
each curve must cover the complete window with at least `minimum_points` measured samples
(default: 3). A target-specific `threshold_db` overrides the enclosing S11 threshold.

- A `resonance` target finds the deepest **interior local minimum** in its window for each
  curve. It checks the candidate/reference frequency error, requires both minima to be at
  or below the threshold, and finds the two threshold crossings around that resonance.
  Each candidate band-edge error is divided by the reference band's width and checked
  separately. Target limits inherit `resonance_relative_error_max` and
  `bandwidth_relative_error_max` unless overridden.
- A `passband` target requires the worst value in the complete window—the maximum S11—to
  be at or below `threshold_db` for both reference and candidate curves.
- A `stopband` or `notch` target represents a window that must be rejected throughout its
  complete width. It requires the minimum S11 in that window to be at or above
  `threshold_db` for both curves. This is intentionally stricter than merely locating one
  high-reflection peak; for a band-notched antenna, use the two published rejection-band
  edges as the target window. If the source only reports a notch centre, a separate
  point/peak criterion is needed rather than pretending that a full interval is known.

Endpoint values are linearly interpolated when a window boundary lies between CSV samples.
A resonance whose threshold region reaches either end of the available sweep has unknown
band edges and therefore fails that target; widen the sweep rather than treating a sweep
boundary as a physical -10 dB crossing. In target mode, the overall curve RMSE is still
checked, and every named target must pass for electromagnetic validation to pass.

## Evidence discipline

- Generate reference and candidate curves with the same AEDT version, material database,
  solution type, port definition, mesh settings, and sweep.
- Keep tutorial PDFs and proprietary `.aedt` files out of Git unless redistribution is
  explicitly permitted. A benchmark can store the source URL and locally computed hashes.
- A passing smoke fixture proves only that the comparison engine works.
- LEAM examples with unresolved ports, boundaries, mesh, or sweep remain demonstrations,
  not electromagnetic validation benchmarks.
