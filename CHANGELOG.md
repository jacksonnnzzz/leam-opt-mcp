# Changelog

All notable changes will be documented in this file.

## [Unreleased]

- Add a resumable `iteration-run` controller that closes the diagnosis/HFSS/evaluation loop,
  executes only hash-frozen proposed trials, stops deterministically, and writes versioned JSON
  and Markdown reports. Pre-create only the active design's local AEDT results directory to avoid
  first-solve WinError 5 failures without deleting existing solver data.
- Add opt-in staged geometry extraction (entities/topology, parameters/relations, evidence),
  strict field ownership and deterministic merging with versioned subpass diagnostics.
- Bound Ollama output tokens, reject token-limited responses before parsing (including valid-looking
  JSON), and retain versioned content-free request metrics on modeling success and failure.
- Add explicit geometry-only attachment selection, compact geometry prompts, shared strict
  geometry output validation, and native Ollama JSON Schema decoding without relaxing evidence gates.
- Add up to two evidence-preserving correction requests per failed source part, with
  versioned prompts, raw attempts, validation errors, and no automatic transport retries.
- Add opt-in four-pass source extraction with deterministic merging, strict part ownership,
  versioned raw outputs, and fail-closed parameter/material conflict handling.
- Reject covered evidence-label/description contradictions and structured missing-field
  contradictions before scoring; keep source-citation verification explicitly unclaimed.
- Reject HTML login/bot pages mislabeled as PDF before local vision rendering or text extraction.
- Add a deterministic, evidence-based 100-point paper reproducibility assessment with
  A/B/C workflow grades, JSON and Markdown reports, CLI support, and an MCP tool.
- Record criterion-level evidence separately from the score so language and vision
  models cannot assign their own reproducibility grade.
- Restrict source distributions to release-relevant code, tests, and documentation.
- Add generic hash-frozen engineering-assumption planning, strict existing-AEDT execution,
  convergence-gated S11 evaluation, immutable retry attempts, and versioned ranking reports.
- Add versioned build receipts, receipt-matched post-processing recovery, AEDT license/error
  classification, a shared-Desktop idle guard against orphaned-solve cascades, and the frozen
  10/10-completed Wi-Fi assumption-search execution record.
- Add validated minimum/maximum changed-assumption filters for Cartesian interaction studies,
  plus an evidence-audited 11/11-completed Wi-Fi v2 interaction record that preserves every
  paper parameter, converges every trial, and records zero paper-gate passes.
- Harden HFSS optimization with geometry-effect preflight, strict existing-session attachment,
  convergence-gated ranking, automatic S11 trace selection, dimensionless variables, unique
  working projects, resumable trials, source/request hashes, and idempotent completion.
- Freeze a 12/12-converged official probe-patch optimization regression that improves worst
  S11 over 9.9-10.1 GHz from -9.9348 dB to -11.4999 dB without modifying the source project.
- Add fail-closed stage-ownership, structured dimensions/solver, and PyAEDT 0.26.3
  static API gates for generated modeling fragments.
- Preserve no-boolean designs with a deterministic empty stage instead of asking an LLM
  to invent a geometry operation.
- Add benchmark-driven geometry, solver-contract, and S11 curve validation.
- Add an official PyAEDT probe-fed patch benchmark and local reference runner.
- Add Yeo 2019 conventional/scaled slot-loaded, El-Gendy 5.25 GHz, and Kaur 2021
  baseline/WLAN-notch/X-band-notch paper benchmarks, paper-target gates,
  source-figure digitization notes, and a validation campaign index.
- Add named multiband resonance, passband, and notch checks to S11 validation.
- Expose validation through both `antenna-workflow validate` and MCP.
- Added a unified `antenna-workflow` CLI for the complete reviewable pipeline.
- Added credential-safe `antenna-doctor` diagnostics.
- Added offline LEAM paper reconstruction examples and native AEDT wrappers.
- Added GitHub CI, contribution, security, architecture, and release documentation.

## [0.1.0] - 2026-08-08

- Initial alpha implementation of staged multimodal modeling, source and artifact approval
  gates, versioned Python generation, feedback revisions, HFSS execution, and surrogate
  optimization.
