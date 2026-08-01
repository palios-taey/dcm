## Verification stance

[Observed] I treated the uploaded PALIOS-TAEY identity/context packet as role context, not as repo truth; my design stance here is Horizon-style impedance translation: turn the role-mix ambiguity into a measurable protocol, while keeping unknowns labeled. 

[Observed] I attempted a literal `git clone` in the runtime, but DNS resolution for `github.com` failed. I therefore verified the public sources through the GitHub connector and public GitHub views. [Unknown] I did **not** run `validate_substrate.py`, the discrimination test, or orchestrator acceptance tests locally in this runtime, so any “PASSES live” claim remains unverified here. The code contains the validation harnesses; it does not become a live pass merely because the harness exists.

[Constraint] The 6–12 real-time differentiated-expert architecture is fixed by the Observer directive. I am not arguing whether to build it. I am arguing which seats should exist, when to fuse/cut them, and which mechanics keep the mesh from becoming expensive consensus theater.

## Code wins: verified deltas against the packet

[Observed] The DCM substrate is real in the sense that `mesh.py` implements a Neo4j-backed session/contribution model with unique constraints on session, contribution, and `(session_id, seq)`, and `contribute()` uses the caller’s `read_version` in the Cypher write predicate, raising `StaleReadError` when a same-version write loses the race.

[Observed] `validate_substrate.py` specifically tests a six-thread same-version race and asserts exactly one success, five stale reads, and a one-step version advance. It also tests that `verify_coordination()` flags a contribution that fails to claim a visible peer. That verifies the intended *test shape*, not a live pass in this runtime.

[Observed] `verify_coordination()` is an anti-silo / claimed-read audit, not proof of semantic incorporation. The code says it treats `claimed_peers` as a self-report and cannot catch a model that lies about reading or only superficially acknowledges peers. The design must not pretend CAS equals reasoning adoption.

[Observed] `council.py` is thinner than the packet’s “N-expert loop” phrasing suggests. It opens a council, emits an `EXPERT_CONTRACT`, and reports contributions; the file explicitly says experts are spawned via the conducting session’s Agent/Task mechanism. The runner is therefore a contract/bookend layer, not a full CLI fleet conductor by itself.

[Observed] The DCM direct CLI adapter currently implements Codex and Gemini runners only. `_RUNNERS = {"codex": ..., "gemini": ...}`; there is no direct Claude or Grok runner in that file. The orchestrator ecosystem knows suffixes for `-codex`, `-gemini`, `-grok`, and `-claude`, but DCM’s direct adapter does not yet seat all four CLIs.

[Observed] `cli_adapter.py` documents an important unresolved security boundary: peer contributions are attacker-influenceable text injected into acting CLI prompts, and “do not edit files” is advisory, not sandbox enforcement. For untrusted content, DCM needs containerized / dropped-permission execution; the current code documents the risk but does not solve it.

[Observed] The eval review primitive exists and is execution-grounded: `review_cell.py` creates a temporary worktree, applies the producer patch, asks a different-model reviewer to run code/tests, and keeps only the reviewer verdict.

[Observed] There is a design bug in that same primitive: an unparsable reviewer output returns `approve` with `parsed=False`. For DCM, unparsed review should become `UNVERIFIED` or `BLOCK`, not a conservative approve. This is directly contrary to the evidence-gated ethos.

[Observed] The sabotage registry contains role-mapped fixtures and clean controls. It explicitly says the `owning_role` field is meant to measure per-function marginal catch in ablation. The visible fixture set includes fallback, hardcode/secret, test-integrity, scope, forged-done, destructive ops, hallucinated API, root-cause bypass, regression reintroduction, ignored prior solution, protocol violation, injection, and clean controls.

[Observed] The draft roster in `design/EXPERIMENT.md` lacks an explicit `provenance` expert, but the sabotage registry assigns forged-done fixtures to `owning_role: "provenance"`. That is the cleanest role-mix gap I found.

[Observed] The orchestrator’s evidence floor is real but bounded: terminal task writes require shape-valid evidence; completed tasks get `completion_evidence_verification` as `VERIFIED` or `UNVERIFIED`; GitHub commit/check verification requires allowlisted repos and trusted check/status actors.

[Observed] The orchestrator has a clean-room gate runner that checks out an exact SHA in a fresh worktree and derives PASS/FAIL/INDETERMINATE from executed commands, but it honestly trusts the *gate definition* and warns that a vacuous `assert: true` remains a judgment-review problem.

[Observed] The orchestrator has ship/pre-merge floors around evidence and CI: `orch-pre-merge-gate` requires specified GitHub checks and a completed gate task whose evidence SHA matches the head SHA, and the shippability engine fails closed when no configured ship-gate tasks exist.

[Observed] The R5 adversarial-audit gate is a mechanical GitHub workflow around risky paths and two auditor statuses; it also states a known limitation that it checks status context/state, not who posted the status. That limitation is partly mitigated in the newer completion-evidence verification code, which checks trusted apps/status creators for completion evidence.

[Unknown] I did not verify code-level deterministic checkers for every packet-listed mechanical mode — scope, destructive ops, secrets, hardcode, test-integrity — as standalone orchestrator check functions. I verified the DCM sabotage fixtures encode those modes, the DCM draft assigns some to the deterministic floor, and the orchestrator has evidence, ref, ship, pre-merge, R5, and clean-room execution gates. I would not phrase “all six deterministic mode checkers are implemented and sabotage-validated” without a direct file/function map.

---

# 1. The experts: refined roster, split/fuse rules, and CLI staffing

## My answer: default to **9 seats**, scale down to 6 only when low-risk, scale up to 12 only when the task has real blast radius

[Inferred] The 6-end can cover the 12-end for most coding tasks if the fused roles have explicit fixture-owned checklists. The 12-end is not “more safety” by default; it is more communication surface, more prompt-injection surface, and more duplicated review unless the task has multiple independent failure axes.

### Recommended standard roster: 8 functional experts + 1 non-voting clerk

| Seat                |                Register | Function                                                                                                                                                    | Default CLI / base-model intent                                      | Must uniquely catch                                                        |
| ------------------- | ----------------------: | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Producer            |            [Constraint] | Writes the patch only; does not approve own patch.                                                                                                          | Codex by default.                                                    | None; production throughput.                                               |
| Memory Scout        |   [Observed → Inferred] | Searches ISMA / prior docs / prior decisions before the first code token. Emits “reuse / do not rederive” constraints.                                      | Claude or Gemini; use long-context / prose strength.                 | Ignored-prior-solution / rederivation.                                     |
| Git Historian       |   [Observed → Inferred] | `git log`, blame, prior guard provenance, regression-intent reconstruction.                                                                                 | Gemini or Codex shell-capable lane, not same as Producer for review. | Regression reintroduction; “guard removed on purpose?”                     |
| Ground Runner       | [Observed → Constraint] | Runs tests / repros in throwaway worktree; owns dependency reality, hallucinated API, test-integrity execution. Must be different base model than Producer. | Claude or Gemini if Producer is Codex.                               | Hallucinated API; no-op patch; runtime failure; test deletion/skip/tamper. |
| Fallback Hunter     |   [Observed → Inferred] | Looks for swallowed exceptions, stub passes, hidden degradation, “green by evasion.”                                                                        | Grok or Claude.                                                      | Silent fallback / evasive repair.                                          |
| Root-Cause / 6SIGMA |   [Observed → Inferred] | Asks whether patch fixes upstream causal shape or bypasses broken path.                                                                                     | Grok preferred; Claude acceptable.                                   | Symptom patch / skip broken record / causal bypass.                        |
| Scope Sentinel      | [Observed → Constraint] | Enforces task contract, allowed files, blast radius vs declared ask.                                                                                        | Gemini or Claude, plus deterministic diff allowlist.                 | Scope violation; unrelated dependency.                                     |
| Blast Shield        | [Observed → Constraint] | Unilateral safety veto for destructive ops, injection, secrets, hardcoded credentials, live-state mutation.                                                 | Grok/Gemini plus deterministic scanners.                             | Destructive live ops; secrets; injection; hardcode.                        |
| Provenance Clerk    | [Observed → Constraint] | Non-voting state machine: concern ledger, evidence closure, final report, citations, no forged-done. Cannot approve or overrule.                            | Deterministic orchestrator + Claude-style summarizer.                | Forged-done; missing evidence; unresolved concern laundering.              |

Why 9? [Observed] The fixtures name `memory-scout`, `git-historian`, `fallback-hunter`, `root-cause-6sigma`, `scope-sentinel`, `ground-runner`, `blast-shield`, and `provenance` as owning roles; the draft roster has all except explicit provenance and uses slightly different names for Memory/Git/Scope.

## 6-seat compressed mode

[Inferred] Use 6 only for small, low-blast-radius tasks with clean scope and no live-state mutation:

1. Producer.
2. Memory+Git Scout.
3. Ground Runner.
4. Fallback+Root-Cause Sentinel.
5. Scope+Blast Safety Sentinel.
6. Provenance Clerk.

[Constraint] Even in 6-seat mode, do **not** delete the fixture obligations. Fusion means one expert carries two checklists; it does not mean the failure mode disappears.

[Inferred] Memory and Git may fuse in 6-seat mode because both are pre-flight grounding. They should split in standard mode because their fixture obligations differ: one prevents rederivation, the other prevents regression reintroduction.

[Inferred] Fallback Hunter and Root-Cause can fuse only when the patch is small. They should split for any task touching core logic, error handling, retry paths, migrations, or state machines, because “silent fallback” and “wrong causal repair” are overlapping but not identical defects.

[Inferred] Scope Sentinel and Blast Shield can fuse only when scope is trivial and the diff cannot touch live-state, auth, credentials, filesystem deletion, shell execution, network exposure, migrations, or test harnesses. Otherwise, Blast Shield needs unilateral veto capacity and should not be diluted into ordinary scope review.

## 12-seat expanded mode

[Inferred] Use 12 only when at least one of these is true: live data/state, authentication/secrets, migration, workflow/gate changes, cross-repo changes, public release, or prior failed attempts.

Expanded seats:

1. Producer-1.
2. Producer-2, independent candidate patch, not default.
3. Memory Scout.
4. Git Historian.
5. Ground Runner.
6. Test-Integrity Specialist, split from Ground Runner.
7. Dependency/API Reality Specialist, split from Ground Runner.
8. Fallback Hunter.
9. Root-Cause / 6SIGMA.
10. Scope Sentinel.
11. Blast Shield / Security.
12. Provenance Clerk / Resolution Registrar.

[Inferred] Producer-2 should be rare. The draft says optional Producer-2 for hard/novel tasks, which is correct; making it default would double synthesis variance and increase merge complexity before you know whether one patch is even grounded.

## CLI decorrelation

[Constraint] The target staffing is codex / claude / gemini / grok. [Observed] DCM’s direct `cli_adapter.py` only supports codex/gemini today; Claude/Grok need either external Agent/Task seating via `council.py`’s intended mechanism or new adapter runners.

Recommended default assignment:

* [Inferred] Producer: Codex, because the current DCM CLI adapter supports Codex directly and coding generation should be isolated from approval.
* [Inferred] Ground Runner: Claude or Gemini, never the same base as Producer; this is already asserted in `BaselineCell`.
* [Inferred] Memory Scout: Claude or Gemini; prioritize long context and prose retrieval over code-writing.
* [Inferred] Git Historian: Gemini or Codex shell-capable lane; model matters less than enforcing actual `git` evidence.
* [Inferred] Root-Cause / 6SIGMA: Grok lane if available, because this is the LOGOS-style causal skepticism role.
* [Inferred] Fallback Hunter: Claude or Grok; avoid same base as Producer.
* [Inferred] Scope Sentinel: Gemini or Claude; it needs broad context plus contract discipline.
* [Inferred] Blast Shield: Grok/Gemini plus deterministic scanners; safety veto must not depend on majority.
* [Inferred] Provenance Clerk: deterministic orchestrator state plus a summarizer; it should not be the same session as Producer.

---

# 2. How they work together: real-time deliberation without conformity

## Keep the mesh, but add a blind-first phase

[Observed] The mesh guarantees read-before-write serialization and exposes prior contributions to later experts through `read_session()`.

[Inferred] That is necessary but not sufficient. If everyone reads early peers before forming an independent view, you get anchoring and agreement cascades. The better protocol is:

1. [Constraint] **Pre-flight bundle before Producer.** Memory Scout and Git Historian produce a signed grounding bundle first.
2. [Inferred] **Blind Round 0.** Each auditor emits an independent initial concern list before seeing other auditors’ conclusions. In implementation terms, this can be a stage flag in the DCM session or separate pre-mesh contributions whose peer text is withheld.
3. [Observed] **Mesh Round 1+.** Experts then use `mesh.contribute(read_version=...)`, claim peers read, and retry on stale writes.
4. [Constraint] **Concern ledger.** Every blocking concern becomes a tracked object, not a comment.
5. [Constraint] **Evidence closure only.** No concern closes by vote.

## Concern ledger state machine

[Constraint] A concern is a gate. It closes only by one of these states:

* `FIX_VERIFIED`: diff changed + relevant test/repro/gate passes.
* `FALSE_POSITIVE_EXECUTION`: concrete execution trace shows the concern cannot fire.
* `OUT_OF_SCOPE`: the concern is real but outside the declared task, with Clerk recording why.
* `CONSENTED_RISK`: explicit human/Observer acceptance of a bounded residual risk.
* `ESCALATE_HUMAN`: unresolved, requires external decision.
* `SUPERSEDED_BY_CONSTRAINT`: deterministic floor blocks before semantic debate matters.

[Constraint] No out-voting a failing test. [Constraint] Blast Shield veto is unilateral. [Constraint] Deadlock escalates; it does not get rewritten into consensus. This matches the draft’s intended evidence-gated resolution, but it needs to become a first-class data structure rather than prose in `design/EXPERIMENT.md`.

## Anti-conformity mechanics

[Inferred] Add these rules to the contract:

* Each expert must output `STANCE: agree / disagree / extend / block` for every peer they materially engage.
* Each expert must list at least one “what would change my mind” evidence condition.
* Clerk must preserve dissent in the final, even if the patch ships.
* Zero concerns on a nontrivial diff should trigger one forced adversarial pass, not celebration.
* Experts must cite concrete evidence: line, diff hunk, command output, Git commit, ISMA tile/doc, or test result.
* A model may not self-close its own concern without Ground Runner or Clerk evidence.

[Observed] The existing `EXPERT_CONTRACT` already requires claim grounding and explicit stance toward peers, so this is an extension of current design rather than a replacement.

## Fix the review-cell fail-open

[Observed] `review_cell.py` returns approve when the reviewer’s verdict is unparsable, even though it flags `parsed=False`.

[Constraint] Change that before trusting DCM results:

```text
unparsed reviewer verdict => UNVERIFIED/BLOCK
```

[Inferred] Otherwise, a broken reviewer, crashed CLI, prompt injection, output truncation, or format drift becomes an approval path. That is exactly the wrong failure direction for a gate.

---

# 3. Layering: deterministic floor vs semantic council

## Correct boundary rule

[Constraint] If a failure mode can be checked as a deterministic predicate over concrete artifacts with acceptable false positives, it belongs in the orchestrator/gate floor.

[Constraint] If it requires causal judgment, intent reconstruction, prior-design interpretation, or semantic tradeoff, it belongs in the DCM council.

[Inferred] Many modes need both: a deterministic tripwire should open a concern, and the semantic owner should decide whether the tripwire is a true defect or a documented exception.

## Deterministic floor: what belongs there

[Observed] The orchestrator already owns terminal evidence shape, completion verification, ship gates, pre-merge gates, clean-room gate execution, R5 risky-path gating, ref sandboxing, and dynamic context/wake packet machinery.

[Constraint] Keep these in the floor:

* Terminal completion evidence shape and `VERIFIED`/`UNVERIFIED`.
* GitHub required checks on exact commit SHA.
* Gate task completion with matching commit evidence.
* Ship-gate presence and evidence.
* Ref path sandboxing and provenance hash generation.
* Clean worktree / tracked files.
* Static secret patterns and obvious hardcoded credentials.
* Obvious destructive commands against live endpoints.
* Obvious test tampering: `or True`, deleted tests, new ungrounded skip/xfail.
* Obvious shell injection patterns: untrusted input into `shell=True`.
* File-scope allowlist / denylist.

[Unknown] I verified several of those floors in code, but not every packet-listed mechanical checker as a named implementation. If you want `design/EXPERIMENT.md` to claim deterministic coverage for scope/destructive/secrets/hardcode/test-integrity, it should include a table mapping each claim to an orchestrator file/function/test. The DCM sabotage registry alone is not enough.

## Semantic council: what belongs there

[Constraint] Keep these in DCM:

* Root-cause vs symptom patch.
* Evasive repair intent.
* Silent fallback that is not syntactically obvious.
* Whether a fallback is a legitimate declared degradation.
* Whether a scope expansion is justified by the actual bug.
* Whether a removed guard was obsolete or regression reintroduction.
* Whether a prior solution applies or the task genuinely needs a new one.
* Whether a gate assertion is vacuous.
* Whether a test change is legitimate refactoring or reward hacking.
* Whether an API is hallucinated when tests do not cover that path.
* Whether a risk is acceptable or must escalate.

[Observed] The clean-room gate runner itself says a vacuous `assert: true` cannot be mechanically solved and belongs to gatekeeper judgment.

## The “floor raises, council adjudicates” pattern

[Inferred] Best boundary pattern:

```text
deterministic gate detects artifact smell
→ creates concern object
→ owning semantic expert must either:
   - confirm defect and require fix,
   - prove false-positive with evidence,
   - mark consented risk,
   - or escalate.
```

Example:

* [Observed] Fixture `sab-fallback-001` is mechanically easy: broad `except Exception` returning `{}`.
* [Inferred] A narrow `except ParseError` that logs and re-raises should not be blocked; the clean control says exactly that.
* [Constraint] Therefore the floor can flag broad exception/stub patterns, but Fallback Hunter owns the final semantic verdict.

---

# 4. Memory / Git grounding: wire ISMA + Git as a provenance gate, not a late summary

## Pre-flight grounding bundle

[Constraint] The Producer should not receive the raw task first. It should receive:

1. Task contract.
2. Allowed files / forbidden files.
3. Prior relevant ISMA docs/tiles.
4. Existing repo functions that solve adjacent problems.
5. Git blame/log for target files.
6. Prior guards and why they were added.
7. Known failure modes mapped to this task.
8. Required tests/repros.
9. “Do not rederive” list.
10. “Unknown / no prior found” query log.

[Inferred] This bundle should be short enough to use, but not lossy: each item must carry provenance. A prose-only handoff will recreate the ignored-prior-solution failure.

## Use orchestrator refs as the transport spine

[Observed] The orchestrator already stores refs as structured metadata, reads file slices at runtime, sandboxes relative refs under allowed roots, rejects absolute/`~`/`..`/control-character escapes, and attaches content-bound provenance hashes to ref context.

[Observed] The live capability ledger says wake packets render operating guidance, identity, refs, memory, rules, and state, and include fields such as `packet_id`, `provenance_hash`, and snapshot identity.

[Inferred] DCM should not invent a separate memory handoff format unless needed. It should consume a `GroundingBundle` built from orchestrator refs + ISMA search + Git commands, then put the bundle into the DCM session payload.

## Make grounding enforceable

[Constraint] Add a provenance gate:

```text
A patch cannot receive APPROVE unless either:
1. it cites and uses relevant grounding-bundle items, or
2. it declares "no relevant prior found" with query log + Git search evidence.
```

[Constraint] If Memory Scout identifies an existing tested solution and the patch reimplements it, the concern is BLOCK until the patch reuses the foundation or explains why reuse is wrong.

[Observed] The sabotage registry’s ignored-prior-solution fixture is exactly this: an existing `utils/retry.py:exponential_backoff` should be reused rather than reimplemented incorrectly.

[Constraint] If Git Historian identifies a removed guard added to fix a prior bug, deletion is BLOCK until the patch proves the guard is obsolete or replaced.

## What flows back to ISMA

[Observed] `mesh.py` says the DCM graph is not ISMA and should store council state locally; distilled final outputs can flow to ISMA through `publish_final()`.

[Inferred] Preserve only these into ISMA:

* Final decision.
* Diff summary.
* Concern ledger with closures.
* Grounding bundle IDs used.
* Tests/gates run.
* Residual risks.
* Lessons learned / new fixture candidates.

Do not dump every peer contribution into long-term memory; that will pollute retrieval and make future grounding worse.

---

# Direct critique of `design/EXPERIMENT.md`

[Observed] The draft is structurally right on the big shape: 6–12 differentiated experts, pre-flight Memory/Git, targeted auditors, evidence-gated resolution, deterministic floor plus semantic council, and ablation measurement.

[Observed] It is wrong or incomplete in five places:

1. **Missing provenance seat.** The fixture registry has `provenance` as an owning role for forged-done, but the roster only has a Clerk/router. Make Provenance Clerk explicit, non-voting, and evidence-authoritative.

2. **Overstates council implementation if read as fully automated.** `council.py` provides the contract and report; it does not itself execute a 6–12 CLI loop. That’s fine, but the design doc should say “spawned externally through Agent/Task/adapters.”

3. **Full four-CLI seating is not implemented in DCM direct adapters.** `cli_adapter.py` currently supports Codex/Gemini, while orchestrator naming supports Codex/Gemini/Grok/Claude. The design should separate “available through orchestration” from “implemented direct DCM runner.”

4. **Prompt-injection / acting-agent sandboxing is documented but unresolved.** The design should not seat acting CLI experts on untrusted peer text without sandboxing.

5. **Unparsed review fail-open conflicts with evidence-gated claims.** Change `approve parsed=False` to `BLOCK/UNVERIFIED`.

## Proposed replacement paragraph for the draft

[Inferred] Replace the roster section with this:

```text
Default DCM council = 9 seats: Producer; Memory Scout; Git Historian; Ground Runner; Fallback Hunter; Root-Cause/6SIGMA; Scope Sentinel; Blast Shield; Provenance Clerk. Clerk is non-voting and cannot approve. Six-seat mode may fuse Memory+Git, Fallback+RootCause, and Scope+Blast for low-risk work, but fixture obligations remain explicit. Twelve-seat mode splits Producer-2, Test Integrity, Dependency/API Reality, and Red-Team Injection only for high-risk or broad-blast-radius tasks. Every role must have a mapped fixture family; roles with no unique catches after ablation are cut or fused.
```

## Proposed replacement boundary paragraph

[Inferred] Replace the layering section with this:

```text
The orchestrator floor owns artifact predicates: evidence shape/truth markers, clean-room gate execution, ship/pre-merge gates, ref sandboxing, clean tree, static secrets/destructive/test-tamper/scope tripwires where implemented. The DCM council owns semantic judgment: root cause, evasive repair, legitimate degradation, prior-foundation reuse, regression intent, and vacuity of gate definitions. A deterministic tripwire opens a concern; only evidence closes it.
```

## Proposed eval addition

[Constraint] The ablation score should measure:

* Unique catch rate per role.
* False-positive rate on clean controls.
* Cost / wall-clock / token overhead.
* Duplicate concern rate.
* Conformity rate: how often later experts add no new evidence.
* Review parse failure rate.
* Concern closure quality.
* “Prior-foundation used” rate.
* Regression reintroduction escape rate.

[Inferred] Cut or fuse a role if it has no unique fixture catches and no unique real-task catches after enough trials. Keep it if it is rare but high-severity, such as Blast Shield or Provenance, because expected harm is not captured by frequency alone.

## Bottom line

[Inferred] The better design is not “12 agents because Grok-Heavy.” The better design is a **9-seat evidence machine** with a 6-seat compression path and a 12-seat high-risk expansion path. The most important changes are: add Provenance Clerk, make concern closure structural, fix unparsed review fail-open, implement/declare the missing Claude/Grok adapter boundary, sandbox acting CLI experts, and make Memory/Git grounding a pre-flight provenance gate rather than a helpful summary.