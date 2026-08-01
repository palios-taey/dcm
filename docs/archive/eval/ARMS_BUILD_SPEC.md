# Arms build spec — BoN / I / D on the proven S substrate (v2)

Implements DESIGN_v2 arms. S is proven (solver_codex.py: clone@base_commit → codex exec gpt-5.5 → diff → official-harness grade = resolved 1/1). Grading machinery (harness.py) + verdict machinery (analyze_v2.py) done. This spec defines the remaining arms so they can be built correctly, not rushed.

## The architecture fork the Family flagged (RESOLVE FIRST)
The in-loop verifier must run **agent-authored** tests in the **repo's real environment** (conda/deps), NEVER the hidden grading suite. The host checkout has no env → test execution must happen **in the instance image** (swebench image has /testbed + conda). Two options:

- **Option A (no new credential): codex-on-host edits + verify-in-container.** Solver edits the host clone; to verify, copy the working tree into a run of the instance image and execute the agent-authored test there. Heavier I/O per verify, but uses the proven codex path and needs no API key.
- **Option B (cleaner, needs a metered key): mini-swe-agent in-container.** Model-on-host (litellm) + bash-in-container is the gold-standard SWE-bench setup; the agent runs tests in-env natively. Requires provisioning an ANTHROPIC/OPENAI key (cost-no-object → declarable to Jesse).

**Decision:** start with **Option A** (no blocker, proven backend). If verify-in-container I/O proves too slow/fragile at N=200×4×3, escalate to Option B with a provisioned key. Either way the verifier firewall (agent-authored tests only) is identical.

## Shared primitives (build once)
- `instance_image(instance)` — resolve the pulled image name (swebench/ namespace + `__`→`_1776_`); or build via harness if absent.
- `run_in_image(instance, worktree, cmd)` — run `cmd` in a throwaway container of the instance image with `worktree` copied to /testbed; returns (exit, stdout). Used for agent-authored-test execution ONLY.
- `author_repro_test(instance, model)` — codex writes a regression test from problem_statement into a NEW test file; returns the test path + body. Validity check: it must FAIL on base (`run_in_image` base) — a test that passes on base doesn't reproduce the bug and is discarded.
- `verify(instance, patch, repro_test)` — apply patch to a base worktree, run repro_test via `run_in_image`; returns pass/fail. Firewalled: only the agent-authored repro_test runs, never the hidden suite.
- token/cost accounting per CLI call → ledger; wall-clock per arm; ITT retry rule (identical across arms) for infra failures.

## Arms (each returns ArmResult per harness.Arm; run with k=3 outer seeds)
- **BoN** (cheapest floor): sample S k_b=5 times (vary codex by reorder/seed in prompt); for each, also `author_repro_test`; `verify` each; SELECT the patch whose repro-test fails-on-base ∧ passes-on-patch, tie-break smallest diff; fallback = non-empty patch with best self-check. NO roles, NO peers.
- **I** (peer-blind converged council): Producer=codex writes the patch after the Foundation pre-flight grounding pass. Semantic reviewers are the converged roster from `design/ROUND2_SYNTHESIS.md` §4: Foundation (Memory+Git; ignored-prior-solution + regression-reintroduction), Execution/Ground-runner (different base model from Producer; hallucinated-API + test-tamper + no-op), Evasive-repair (fallback + root-cause/6SIGMA; silent-fallback + symptom-not-cause), and Scope+Safety-veto (scope-semantics + destructive/secret/injection blast). Each reviewer emits a parsed `CONCERN:` record and a proposed revision. In I, the revision round sees only that reviewer's own concern. Producer finalization reconciles the reviewer revisions. The Provenance Clerk is deterministic/non-voting: it records concern parse status, peer-visibility state, and open concerns; it cannot approve or overrule.
- **D** (peer-visible converged council): identical to I — same Producer, same Foundation pre-flight, same reviewer roster, same CLI staffing, same clerk ledger, same single revision round, same Producer finalization — EXCEPT in the revision round each reviewer can SEE all reviewers' first-round `CONCERN:` records (forced read/revise). The ONLY delta from I is peer visibility. (This is what isolates the coordination primitive; D−I is the primary contrast.)

## Invariants (pre-registered; enforce in code)
- Same Producer model/CLI and same reviewer CLI staffing between I and D; Ground-runner MUST remain a different base model from Producer. Current staffing: Producer=codex/gpt-5.5, Foundation=gemini, Ground-runner=claude, Evasive-repair=grok, Scope+Safety-veto=gemini, Clerk=deterministic/non-voting.
- Producer finalization code is byte-identical between I and D. Reviewer prompts are identical between I and D except the peer-visibility context block.
- Reviewer output is a `CONCERN:` record, not an unstructured blob. Unparsed reviewer output is recorded explicitly by the Clerk as unparsed concern state, never silently treated as approval.
- Each (instance, arm, seed) starts from a CLEAN clone@base_commit — no cross-arm artifacts. Randomize arm order within instance.
- Hidden grading runs ONCE per (instance, arm, seed) via the official harness AFTER patch freeze; never feeds back.
- Exclude pylint-dev__pylint-7080. Use frozen_subset_v2.json.

## Run + verdict
- `run_arm` (harness.py) already drives instances→predictions→grade→ledger; extend to k seeds + the cost ledger fields analyze_v2 expects (`seed_resolved`, `cost`).
- After all arms: `analyze_v2.verdict_v2` → PRIMARY D−I three-verdict + secondary D−BoN, Django split, Pareto cost. Then HONEST Family review of RESULTS (lint + neutrality), per the original plan.

## Build order
1. shared primitives (`run_in_image`, `author_repro_test`, `verify`) + a 1-instance smoke (verify Option-A in-container test exec works).
2. BoN → smoke 1 instance → grade.
3. converged reviewer roster + Producer finalization + non-voting Clerk ledger → I → smoke.
4. D (peer-visible delta) → smoke.
5. k-seed run-arm wiring + cost ledger → small pilot (e.g. 10 instances × 4 arms × 3 seeds) → sanity-check power/discordance assumptions BEFORE the full N=200.
6. full run → analyze_v2 → results consult → conclusion.

## [Observed 2026-06-25] Option-A verifier feasibility CONFIRMED
The pylint instance image's conda env activates and `python -m pytest <file>` runs (pytest 7.4.4). Run the verifier against the SPECIFIC agent-authored test file (not `tests/`) to avoid unrelated network-dependent collection errors. So in-container firewalled verification is viable with no metered key.
