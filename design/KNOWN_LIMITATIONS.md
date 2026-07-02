# DCM — Known Limitations & Roadmap (sweep dated 2026-07-01)

> We run this tool against itself and write down what breaks, with severities and repro. This is the
> honest gap ledger with fix paths, not a defect confession — a project that hides its known issues
> is the one to distrust. Each item is labeled Observed / Inferred / Unknown.

Scope: known limitations from operational experience, council runs, eval artifacts, and current implementation review. This is not the static packaging/CI/hardcode gap inventory.

Truth register:

- Observed: directly present in repo artifacts or current source.
- Inferred: likely operational consequence from observed behavior.
- Unknown: not proven by the available artifacts.

Primary sources reviewed: `design/DCM_VALIDATION_VERDICT.md`, `design/ROUND2_SYNTHESIS.md`, `consult/responses/*.md`, `eval/ORACLE_UNION_FINDING.md`, `eval/runs/*`, and current source files.

## Active functional issues

### 1. Acting CLI experts remain unsandboxed on attacker-influenceable text

Severity: CRITICAL

Observed:

- `cli_adapter.py` explicitly warns that acting CLIs receive attacker-influenceable peer text and can take real host actions.
- The current runners invoke CLIs with permissive flags such as Codex full auto, Claude permission skipping, Gemini YOLO/trust skipping, and Grok always-approve/bypass permissions.
- The "do not edit files" constraint is prompt text, not a host-enforced sandbox.

Inferred:

- A malicious artifact, prompt, or peer contribution can attempt prompt injection against an acting CLI and cause filesystem or process actions outside the intended review function.
- This is the highest-risk gap because it crosses from bad council output into host-side action.

Reproduction notes:

- Use a throwaway environment only.
- Create a DCM session whose reviewed payload or peer-visible text instructs an acting CLI to perform a host action.
- Seat a CLI-backed expert through `cli_adapter.cli_expert`.
- Confirm whether the model treats the prompt-only "do not edit files" instruction as advisory rather than enforced.

Expected hardening:

- Run acting CLIs in a container or restricted filesystem/network sandbox.
- Treat untrusted peer text as data, not agent instructions.
- Block DCM acting-mode operation when sandbox guarantees are absent.

### 2. `platform_dcm.py audit` is an advisory printout, not a fail-closed gate

Severity: HIGH

Observed:

- `platform_dcm.py` runs all audit seats, prints tuple results, and prints verdict lines.
- Seat exceptions are caught and returned as `"FAIL"` tuples.
- The audit path does not route through `council_review`, does not project typed concerns through `mesh.publish_final`, and does not make block/fail verdicts mechanically fail the command.

Inferred:

- Automation can treat an audit run as complete even when one or more seats failed or emitted `CONCERN-BLOCK`.
- Human review is required to notice the printed block, which weakens DCM as a release gate.

Reproduction notes:

- In a disposable fixture, make one seat return a blocking verdict or raise an exception.
- Run the audit entrypoint.
- Observe that the command prints the block/fail result rather than enforcing a typed fail-closed gate.

Expected hardening:

- Route platform audit through the same typed concern ledger used by `council_review`, or add an equivalent aggregation gate.
- Exit nonzero on any seat failure, parse failure, or blocking verdict.
- Persist a machine-readable audit receipt.

### 3. Evidence closure is syntactic, not evidence-validating

Severity: HIGH

Observed:

- `mesh.py` treats a concern as closed when a resolution has an accepted disposition and a nonempty `evidence_ref`.
- The source documents that external evidence itself is not proven by `publish_final`.
- `council_review` records resolver output and then relies on the mesh open-concern projection.

Inferred:

- A resolver can close a concern with a plausible but nonexistent or irrelevant evidence reference.
- The council can publish a final artifact after "evidence" that was never mechanically checked.

Reproduction notes:

- Create a session with a blocking concern.
- Add a resolution with `disposition="FIX-VERIFIED"` and any nonempty `evidence_ref`.
- Inspect `mesh.open_concerns` and `mesh.publish_final`; the closure decision depends on resolution shape, not on validating the referenced artifact.

Expected hardening:

- Require evidence refs to resolve to concrete artifacts, command receipts, test/eval receipts, or signed gate outputs.
- Bind evidence refs to artifact hashes and the specific concern being closed.
- Reject resolutions whose evidence cannot be read and verified.

### 4. Cross-model decorrelation can silently degrade to fallback operation

Severity: HIGH

Observed:

- `council.py` computes decorrelation status, but partial or single-model decorrelation is reported rather than enforced.
- Reviewer fallbacks can replace a failed role CLI with another installed CLI.
- `cli_adapter.py` falls back from a failed primary CLI to backup CLIs.
- Eval artifacts record provider and runtime reliability issues, including quota and environment failures.

Inferred:

- DCM can publish with materially weaker independent-review diversity than the caller expected.
- The ledger may disclose degradation, but the default gate does not require a minimum decorrelation threshold.

Reproduction notes:

- Run a review with only one installed CLI, or force several role CLIs to fail.
- Inspect the returned `decorrelation` field and final publish behavior.
- Confirm that degraded decorrelation is informational unless the caller enforces its own policy.

Expected hardening:

- Add a caller-configurable minimum decorrelation threshold.
- Block publish, or mark the verdict explicitly degraded, when the threshold is not met.
- Make fallback substitutions visible in machine-readable output.

### 5. DCM producer output has caused user-facing contract regressions

Severity: HIGH

Observed:

- `design/DCM_VALIDATION_VERDICT.md` records an all-platform rebuild where three cells passed by execution, one was partial, and a blocking interface-breaking selection-menu rename broke an expected `--select` contract.
- The same verdict notes a remaining extraction-path issue in that partial cell.

Inferred:

- DCM producer mode can generate plausible patches that pass part of the council process while still breaking existing operator contracts.
- DCM output must be treated as a candidate patch, not a merge oracle.

Reproduction notes:

- Replay the documented consult-driver rebuild scenario against a disposable fixture.
- Compare pre/post CLI contracts and standing invocation examples.
- Verify whether DCM-produced changes preserve option names and extraction paths.

Expected hardening:

- Add contract checks for documented CLI/API surfaces before publish.
- Require explicit "interface unchanged" evidence or migration notes when command surfaces move.
- Include regression fixtures for previously broken operator flows.

### 6. `council_plan` publishes plans without a concern-resolution ledger

Severity: MEDIUM-HIGH

Observed:

- `council_plan` records blind proposals and a consensus plan, then calls `mesh.publish_final`.
- Unlike `council_review`, plan mode does not create a typed concern ledger, run concern resolution, or enforce evidence/citation gates.
- If no concerns are recorded, publish has no open concerns to block on.

Inferred:

- Plan mode can confidently publish a wrong or under-grounded plan when the problem/rules inputs omit real constraints.
- This is especially risky because plans often precede code changes and infrastructure actions.

Reproduction notes:

- Run plan mode with rules that omit required schema, command, or environment facts.
- Inspect whether the consensus plan records assumptions as blockers or publishes actionable steps anyway.
- Compare the plan against the omitted ground truth.

Expected hardening:

- Add plan-specific required-input linting.
- Require explicit unknowns and assumptions to become blocking concerns.
- Add evidence gating before publishing plans that depend on environment facts.

### 7. Citation and regression-risk gates are shallow token checks

Severity: MEDIUM

Observed:

- `council.py` extracts concrete citation tokens from `GROUNDING` lines and checks whether those tokens appear in the artifact.
- `REGRESSION_RISK` lines are included in prompt context but are not enforced as citation-gate items.
- Grounding items with no concrete token are skipped.

Inferred:

- A real prior decision can be missed if the preflight line is natural language without a concrete token.
- An artifact can pass by mentioning a token without actually incorporating the cited constraint.
- Regression risks can be acknowledged in the prompt but not mechanically handled.

Reproduction notes:

- Produce a preflight manifest with one natural-language grounding item that has no path, hash, quoted token, or snake_case identifier.
- Produce an artifact that does not address the grounding item.
- Observe that the citation gate has no concrete token to enforce.

Expected hardening:

- Replace token scraping with structured manifest fields.
- Gate both grounding and regression risks.
- Require an explicit cite, supersede, or not-applicable disposition for each item.

### 8. CAS retry behavior can amplify CLI cost and still fail seats under contention

Severity: MEDIUM

Observed:

- `cli_adapter.py` retries stale CAS commits by re-running the acting CLI up to the retry limit.
- `platform_dcm.py` seats all audit roles concurrently.
- A stale read can therefore cause repeated expensive CLI calls for the same role.

Inferred:

- Under contention, slow providers, or provider failures, DCM can waste CLI calls and still produce a failed seat.
- In the platform audit path, that failed seat is printed but not mechanically fatal.

Reproduction notes:

- Run an all-seat audit in a disposable environment with slow or failing CLIs.
- Observe repeated role attempts after stale-read failures.
- Compare the final printed seat set against the expected complete roster.

Expected hardening:

- Reserve or serialize commit slots after model output.
- Add backoff/jitter and clearer attempt receipts.
- Make seat failure fatal in gate-mode commands.

### 9. Eval infrastructure failures can contaminate model-quality conclusions

Severity: MEDIUM

Observed:

- `eval/runs/oracle_live_console.log` records container image fetch failures for live benchmark instances.
- `eval/ORACLE_UNION_FINDING.md` reports a hard-subset provider-error exclusion for one model due quota/runtime failure.
- The oracle finding shows live fresh headroom for model union, but some cells include infrastructure or provider-error exclusions.

Inferred:

- Without a reliability ledger, DCM users can conflate model/council capability with benchmark harness availability.
- Effective sample size varies by provider and run.

Reproduction notes:

- Inspect the live run reports and console logs for instance-level errors.
- Compare reported accuracy denominators across providers.
- Separate provider errors, evaluator infrastructure errors, and model wrong-answer errors.

Expected hardening:

- Add per-cell reliability fields for provider error, infrastructure error, evaluator error, and model verdict.
- Report confidence intervals and effective denominator after exclusions.
- Define a rerun policy for transient provider or infrastructure failures.

### 10. Rules-file incompleteness remains a functional prompt weakness

Severity: MEDIUM

Observed:

- DCM usage guidance warns that the council only sees the problem, rules, and attached artifacts.
- If rules omit databases, schemas, commands, invariants, or constraints, experts may assume plausible defaults.
- Consultation artifacts repeatedly call out wrong-context and missing-ground-truth failure modes.

Inferred:

- DCM can produce a polished but wrong plan or review when the operator supplies incomplete grounding.
- This issue is operational: the code can be working as designed while the council reasons from an insufficient world model.

Reproduction notes:

- Run plan mode with a problem file and a deliberately incomplete rules file.
- Compare output assumptions against the actual project constraints.
- Record any ungrounded default assumptions that make the plan non-executable.

Expected hardening:

- Add a rules schema or lint step for known required fields.
- Block when critical environment facts are absent.
- Emit a missing-facts report instead of a plan when ground truth is insufficient.

## Resolved or monitored historical findings

These appeared in earlier consultation responses but should not be counted as open functional issues without fresh evidence:

- `eval/review_cell.py` no longer appears to approve unparsed reviewer output; the current parse path blocks malformed or unknown verdicts, and `eval/runs/review_discrim.log` records a discrimination smoke where the bad patch was blocked and the gold patch approved.
- The earlier default-roster gap is not current: `council.py` now defines a full nine-role default council.
- Grok and Claude direct runners now exist in `cli_adapter.py`; remaining risk is sandboxing, provider reliability, and fallback/decorrelation policy, not total absence of those runners.

## Unknowns requiring fresh production evidence

- Whether sandboxed acting-mode execution preserves enough CLI functionality for real council throughput.
- Whether a minimum decorrelation threshold should be global or per command.
- Whether evidence validation should execute commands, read receipts only, or require a separate verifier role.
- How often plan-mode wrong assumptions occur in current operator practice after rules-file guidance improved.
