# Backend operation reference

These commands were checked against the repository's `workflow_cli.py`. Read `--help` when
the installed version differs. Replace illustrative IDs/paths using actual state.

## Resolve commands

On Windows, from the project root, prefer `.\.venv\Scripts\antenna-workflow.exe`.
On other platforms use the corresponding environment executable. Pass `--workspace` before
the subcommand so a terminal opened elsewhere cannot silently select another task store.
Use the configured providers; inspect with `antenna-doctor` if needed without printing keys.
The CLI routes model work through its configured provider; host vision alone does not mean
the backend vision provider is configured.

## Existing assessment

```powershell
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" status <job-id>
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" reproducibility-audit --job-id <job-id>
```

`status` reads state; `reproducibility-audit --job-id` writes the latest assessment and Markdown
report and updates artifact pointers. Preserve previous results before replacing them.
For a read-only assessment of an independent or candidate source file:

```powershell
.\.venv\Scripts\antenna-workflow.exe reproducibility-audit --source-analysis "<absolute-source-json>"
```

That form returns JSON to stdout; it does not register new job artifacts. Use the returned
JSON to explain the score and avoid claiming that an on-disk report was generated.

## New paper or image

```powershell
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" model-create --template paper_reconstruction --description "<case-specific intent>" --attachment "<absolute-source-path>" --no-2d
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" model-run <returned-job-id> --through-stage source_analysis
```

Add `--include-simulation` at creation only when simulation setup is in scope. Select one
case/figure from a multi-design paper. Keep original solver and simulated versus measured
targets explicit. Accepted source analysis automatically writes the assessment artifacts.
Read the returned status and error rather than assuming a command exit means completion.

## Source or score-evidence correction

Example: "The thesis gives loss tangent in Table 3.2; it should not be missing."

Verify that table against the selected design. Snapshot the baseline source and assessment.
Then produce a source candidate with:

```powershell
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" source-refine <job-id> --description "<specific correction and evidence location>"
```

Inspect the candidate, refinement report, and review packet. Audit the candidate using the
read-only `--source-analysis` form. Compare the baseline/candidate criterion, evidence,
score, and grade. The backend adopts it through `source-approve <job-id> <approval_hash>`
when the user has approved that concrete candidate. Follow any subsequent source invalidation
or retry requirements from `docs/PIPELINE.md`; a source correction can make prior code stale.

## Code generation and geometry feedback

After completing the source/assumption review required by the selected backend workflow:

```powershell
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" codegen <job-id> --through-stage boolean
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" feedback <job-id> "<geometry correction>" --comparison-image "<absolute-image-path>"
.\.venv\Scripts\antenna-workflow.exe --workspace ".\.antenna-mcp" regenerate <job-id>
```

Comparison images are optional. `feedback` freezes the comment and optional attachments;
it does not itself fix geometry. `regenerate` currently targets geometry through `boolean`.
For simulation-setting feedback inspect the stage-specific retry workflow and revalidate
the solver contract; do not promise that geometry regeneration updates simulation settings.
For a requested setup export, `codegen --through-stage simulation_setup` is available.
Export can reuse existing fragments, so ensure changed upstream evidence has invalidated
stale downstream artifacts before exporting.

## Report handoff

Present a compact result such as:

- Case and source artifact/version.
- Completeness score/grade from the backend; preliminary if fallback extraction was used.
- Evidence change: criterion, old status, new status, and source location.
- Remaining material gaps and one concrete next action.
- Current artifact links and actual HFSS validation state.

Keep full criterion records in the JSON. A concise conversational explanation is enough for
routine feedback; a formal report can be generated when requested.

## Automatic iterative reconstruction

For a frozen paper target, bounded unresolved-assumption space, executable adapter, and baseline
curve, run or resume the complete loop with:

```powershell
.\.venv\Scripts\antenna-workflow.exe iteration-run `
  --curve "<baseline-s11.csv>" `
  --target "<iterative-target.json>" `
  --space "<assumption-space.json>" `
  --adapter "<assumption-adapter.py>" `
  --output-dir "<iteration-output>" `
  --study-output-dir "<assumption-study-output>" `
  --grpc-port 50051 `
  --active-project "<open-project>" `
  --aedt-version 2025.1
```

The controller executes only the exact proposed trial, verifies the immutable S11 artifact,
feeds it into the next diagnosis, and stops on a paper-gate pass, budget limit, exhausted
hypotheses, or execution failure. It resumes completed trials without solving them again and
writes versioned JSON and Markdown reports. Use `--retry-failed` only after the external failure
has been resolved. A completed loop with no passing variant is a valid negative result, not an
HFSS execution failure and not permission to change paper-explicit parameters.
