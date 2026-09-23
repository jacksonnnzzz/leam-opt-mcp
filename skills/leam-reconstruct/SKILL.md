---
name: leam-reconstruct
description: 使用 LEAM Opt MCP 从天线论文、尺寸图和语言描述评估复现资料完整度，解释评分证据，生成 HFSS Python 建模文件，并根据用户对照意见修订来源或模型。适用于天线论文复现评估、建模及反馈，不用于泛论文质量排名。
---

# LEAM antenna reconstruction

Use the installed LEAM Python backend as the implementation; this skill is its conversational
interface. Follow the user's selected case, model provider, and requested stopping point.
Respond in the user's language and lead with the practical result and unresolved evidence.

## Locate the backend

Find the project root containing `pyproject.toml` with project name `leam-opt-mcp` and
`src/antenna_mcp`. When this skill is in its source checkout, that root is two directories
above its folder. Otherwise use the user's selected workspace; do not hard-code a machine path.
Use that project's virtual environment and explicit workspace path for all commands.
Read [references/workflow.md](references/workflow.md) for supported commands and feedback routes.
Read the project's `docs/REPRODUCIBILITY_ASSESSMENT.md` when interpreting or revising scores.
If a configured LEAM MCP tool provides the same operation, it may replace the CLI call.
Installing the skill does not install Python dependencies, configure model credentials, or supply AEDT.

## Choose the requested operation

- **Explain or assess:** inspect the existing job and source, or extract the specifically selected
  design from supplied sources. Stop at assessment when that is what the user requested.
- **Generate:** carry accepted evidence through the existing generation/export workflow and
  return the versioned Python artifact and its actual execution instructions.
- **Correct:** distinguish source-evidence corrections, model-code corrections, and changes to
  the evaluation method. Apply the corresponding route in the reference.
- **Validate in HFSS:** use the existing execution/validation services only when the user
  requested execution. Resolve the intended project/design/port from current state. A prior
  project name or an open desktop alone does not identify the present execution target.

## Make assessments reviewable

For each assessed antenna report:

1. Which thesis/paper, chapter, figure, design variant, and job were assessed.
2. Backend-reported score and grade, extraction mode, and six dimension scores.
3. The most consequential gaps, with criterion ID, cited page/figure/table, and what would
   resolve them. Distinguish missing source information from information the extractor missed.
4. What the user can do next and clickable paths to the report and source artifacts.

Treat the current weights and grade thresholds as a provisional completeness rubric awaiting
empirical calibration. A score is not a probability of successful reproduction. Model-extracted
status labels still require evidence review even though arithmetic is deterministic.
Legacy `deterministic_fallback` uses compatibility heuristics; label its score preliminary and
check the actual pages before declaring that the paper omitted a parameter.
Inspect critical missing/conflicting geometry, materials, and excitation even for a high total.
Grades are recommendations in the current backend, not a replacement for existing approvals
or a guarantee that all downstream execution gates are enforced by this assessment module.

Keep source completeness, repeated-extraction stability, gold-reference accuracy, HFSS build,
mesh convergence, and paper-target agreement distinct. Only report measurements actually
computed. The repeated-extraction and gold-accuracy campaign is currently a plan, not a
completed feature; check the installed code before claiming those commands exist.

## Preserve the feedback loop

Record the user's correction in terms of the affected case, field/entity, old claim, new claim,
and supporting evidence. Preserve the existing assessment and source as an independently
named snapshot with hashes before an operation that replaces them. Current audit outputs
reuse filenames, so they are not themselves an immutable assessment history.
Use the backend's candidate/review/versioning workflow; show the resulting evidence and score
delta. Corrections to one case do not change global rules unless requested.
For general scoring-method changes, modify and test the backend rubric, identify the version
or source commit used for each report, and compare old/new scores on the same frozen inputs.
Never adjust source evidence simply to obtain a higher grade.

## Artifact and execution semantics

`completed` is scoped to the reported stage. Python export, successful HFSS build, adaptive
convergence, and agreement with paper results are separate outcomes.
`generated_model_vNNN.py` may require external Python/PyAEDT; native AEDT execution needs
the wrapper explicitly returned by the export metadata. Do not direct a user to run arbitrary
Python 3 fragments through AEDT's older embedded interpreter.
Use the latest actual artifact paths, not historical paths remembered from conversation.
Do not copy credentials, source PDFs, or local simulation results into the skill package.
