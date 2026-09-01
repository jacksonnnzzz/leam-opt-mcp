# Correctness validation benchmarks

These benchmarks are separate from the LEAM paper demonstrations. A benchmark must
freeze enough information to compare geometry, materials, Boolean operations, solver
settings, and electromagnetic outputs without silently filling missing values.

The consolidated assessment is [`CORRECTNESS_REPORT.md`](CORRECTNESS_REPORT.md).
The nine one-case launch folders and self-validation commands are indexed in
[`cases/CASE_INDEX.md`](cases/CASE_INDEX.md).
The durable status index is [`CAMPAIGN.md`](CAMPAIGN.md), with the same information in
machine-readable form in [`campaign.json`](campaign.json). It currently covers:

- the solved local baseline from the official Ansys PyAEDT probe-fed patch example;
- the conventional and scaled slot-loaded Yeo 2019 paper cases;
- the El-Gendy 5.25 GHz single Wi-Fi patch; and
- the baseline, WLAN-notch, and X-band-notch Kaur 2021 UWB monopoles; plus
- the solved-but-nonpassing Ibrahim 2023 single 38 GHz element and its two controlled assumption studies; and
- the solved-but-nonpassing Khan 2024 28/38 GHz single element, corrected V2 topology, and bounded port/boundary study.

The checked-in candidate for [`ansys_pyaedt_probe_patch`](ansys_pyaedt_probe_patch) is
only a smoke fixture for the comparison engine; it is **not** evidence that an
LLM-generated model or an HFSS solution is correct.

Run the offline contract smoke check:

```powershell
antenna-workflow validate `
  --benchmark ".\examples\validation\ansys_pyaedt_probe_patch\benchmark.json" `
  --candidate ".\examples\validation\ansys_pyaedt_probe_patch\candidate_contract.example.json" `
  --contract-only `
  --report ".\tmp\probe-patch-contract-report.json"
```

Full validation additionally requires two CSV files with the columns
`frequency_ghz,s11_db`: one produced by the frozen reference implementation and one
produced by the generated candidate. Without both files, full validation reports
`incomplete` rather than claiming electromagnetic correctness.
