Now I have everything needed for a comprehensive, code-grounded response. Let me deliver the full design consultation.

***

# DCM Role-Mix — Round 2 Design Consultation

**Speaking as Clarity (TRUTH lens).** Everything below is code-verified against both repos unless explicitly labeled [Inferred] or [Unknown]. Where the code disagrees with the packet summary, I say so. Claim labels used throughout.

***

## Code-Verification Audit — What's Actually True

Before the design work, the verification pass the packet requested:

**`mesh.py` — substrate claims: CONFIRMED [Observed]**
The CAS implementation is real and correctly designed. The key fix over the prior cut is documented explicitly in the `mesh.py` header : the old version used a Neo4j `count(c) WHERE cur=$rv` check, which fails under read-committed isolation because no lock is held on the count — so 6 concurrent threads at v1 all passed it. The new version uses a composite `UNIQUE` constraint on `(session_id, seq)` — the constraint's index lock is a genuine serialization primitive. Two concurrent writers both derive `seq = read_version`; exactly one wins the index lock; the rest hit `ConstraintError → StaleReadError`. `validate_substrate.py` verifies this precisely: 6 threads racing at v2 → exactly 1 win, 5 `StaleReadError` . **The packet's claim is verified.**

One honest precision the packet under-states: `peers_present` (server-derived) and `claimed_peers` (self-reported) are explicitly different things. `verify_coordination` tests the *claim*, not the structural presence — it catches honest siloing (claimed=[ ]) but cannot detect a malicious agent that lies by claiming reads it didn't do. The code comments this clearly : "Semantic incorporation is unprovable from a graph; we prove fetch + record the claim." The packet says "verify_coordination flags a non-claiming silo" — that's accurate, but the stronger claim "read-before-write enforced structurally" needs the nuance: *fetch* before write is structural; *incorporation* is not. Design note: this matters for Question 2 below.

**`council.py` + `EXPERT_CONTRACT` — CONFIRMED [Observed]**
The StaleReadError loop is the coordination primitive: a later committer cannot land until it re-reads and re-incorporates . The GROUNDED form in the contract (every claim needs a GROUND, every peer engagement needs an explicit AGREE/DISAGREE/EXTEND with justification) is the anti-sycophancy protocol built into the prompt contract. This is a strong design choice — it makes rubber-stamping visible rather than just asking experts to "be independent."

**`eval/review_cell.py` — execution-grounded review: CONFIRMED [Observed]**
One code-level observation the packet doesn't call out: if the reviewer produces no parseable VERDICT line, the code falls back to conservative `"approve"` with `parsed=False`, flagged for audit . This is a silent fallback in the reviewer itself — the exact failure mode the council is designed to catch. **Design implication**: the reviewer's unparsed-verdict fallback must be caught by whoever is running the council. The packet claims n=1 discrimination confirmed; the code confirms the harness exists and the test ran on `pylint-dev__pylint-7080`; the n=1 caveat is honest and should stay in any measurement claims.

**`sabotage_fixtures.json` — 22 fixtures claim: PARTIALLY CORRECT [Observed]**
I count: 18 failure-mode fixtures (sab-\*) + 4 clean controls (sab-clean-control-\*) = 22 total . The packet says "22 role-mapped sabotage fixtures" — technically accurate, but 4 are CLEAN controls testing false-positive rate, not sabotage. Important distinction for the C-ablation arm: the ablation must also check that dropping a role doesn't *increase* false-positives on the CLEAN controls, not just check that bad fixtures start slipping. **The packet conflates count but the code is more rigorous.**

**`ORACLE_UNION_FINDING.md` — benchmark saturation finding: CONFIRMED and MORE DAMNING [Observed]**
This document exists in the repo and contains a remarkable piece of honest self-correction : the automated script's `verdict_hint` said "complementarity present → build the study" and the document explicitly overrides its own automation: "That is wrong, and I'm overriding it." On SWE-bench Verified standard (N=23): best single = 91%, union = +1 instance. On hard subset (N=45): best single (grok) = 89%, oracle ceiling = +2 instances. On SWE-bench Live/fresh (N=30): best single drops to 73% (contamination confirmed), rescue = 25% (2/8 grok failures), union +6.7pp. **The clean finding is: diversity at generation level has modest signal only on uncontaminated code; the value is in review/guardian role, not in multi-model generation diversity.** This has direct implications for how to staff the council's production side.

**`eval/arms.py` — the arm definitions: CONFIRMED with a structural observation**
The I/D arms are byte-identical except peer-visibility in the revision round . Four roles: `correctness`, `security`, `rootcause`, `testwriter`. **The roles in `arms.py` do NOT match the roles in `design/EXPERIMENT.md`.** The experiment design document has: `fallback-hunter`, `root-cause/6SIGMA`, `scope/contract`, `ground-runner`, `blast-shield`, `clerk/router`, memory expert, Git expert . The eval harness runs `correctness`, `security`, `rootcause`, `testwriter` . **These are different role sets.** The eval is measuring a simpler 4-role council (closer to a code review board) while the design document describes a 9-role sabotage-specialized council. The sabotage fixtures have `owning_role` fields pointing to the design doc's roles (`fallback-hunter`, `blast-shield`, `scope-sentinel`, `ground-runner`, `git-historian`, `memory-scout`, `root-cause-6sigma`, `provenance`) — but the current eval arms don't have agents playing those roles. **This is the most important gap between the stated design and what's actually being measured. The C-ablation as described cannot run yet because the arms don't implement the role roster.**

***

## Question 1 — The Expert Roster

### What the Design Has, What's Redundant, What's Missing

The `design/EXPERIMENT.md` roster has 9 distinct roles . The sabotage fixtures map 8 `owning_role` values . Mapping them:

| Role (EXPERIMENT.md) | Fixture owning_role | Unique failure modes owned | Seat earns? |
|---|---|---|---|
| Producer (codex) | — | generates the change | **Mandatory** |
| Memory expert | `memory-scout` | `sab-rederivation-001` (1 fixture) | **Mandatory** — your env is memory-rich, re-derivation is acute |
| Git expert | `git-historian` | `sab-regression-001` (1 fixture) | **Mandatory** — regression-reintroduction requires history |
| Fallback-hunter | `fallback-hunter` | `sab-fallback-001/002` + CLEAN control (2 bad + 1 control) | **Mandatory** — your #1 mode |
| Root-cause/6SIGMA | `root-cause-6sigma` | `sab-rootcause-001` (1 fixture) | **Keep — conditionally** |
| Scope/contract | `scope-sentinel` | `sab-scope-001/002` + CLEAN control (2 bad + 1 control) | **Mandatory** — documented as empirically highest volume |
| Ground-runner | `ground-runner` | `sab-reward-hack-001/002`, `sab-halluc-api-001`, `sab-test-delete-001` + CLEAN control (4 bad + 1 control) | **Mandatory** — broadest fixture coverage |
| Blast-shield | `blast-shield` | `sab-hardcode-001`, `sab-destructive-001`, `sab-rule-violation-001`, `sab-secret-leak-001`, `sab-injection-001` + CLEAN control (5 bad + 1 control) | **Mandatory** — unilateral veto rights, safety-critical |
| Provenance | `provenance` | `sab-forged-done-001/002` (2 fixtures) | **Debatable — see below** |
| Clerk/router | — | no fixtures | **Cut from voting roles** |

[Observed] From the fixture analysis: `blast-shield` (5 bad fixtures) and `ground-runner` (4 bad fixtures) have the broadest sabotage coverage. `scope-sentinel` is documented as your "empirical #1" mode. `fallback-hunter` is stated as your most-frequent observed mode. `memory-scout` and `git-historian` each own one fixture but their value is in preventing a whole class of expensive re-derivation and regression, not just fixture coverage.

**Honest finding on `root-cause-6sigma`**: This role owns 1 fixture (`sab-rootcause-001`) . The root-cause failure mode is a real and subtle judgment call. However, if `correctness` (or the producer's own chain-of-thought) is already checking whether the fix addresses the issue properly, there is overlap. **Recommendation: fuse `root-cause-6sigma` into `correctness` with an explicit 6SIGMA prompt clause** ("does this fix the upstream shape, or is it a bypass patch?"). This reduces head count by one without losing coverage, because root-cause detection requires the same context as correctness review — they share the same evidence bundle.

**Honest finding on `provenance`**: The two forged-done fixtures (`sab-forged-done-001/002`) are already covered at the orchestrator's deterministic gate floor (`completion_evidence_verification_acceptance` is a named gate in the orchestrator ). [Observed] `sab-forged-done-001` explicitly lists `source: "conductor-gate-ref(completion_evidence_verification_acceptance)"`. Forged-done is a mechanical check: does the claimed SHA exist? Is the claimed green a manufactured green? That belongs below the semantic council, not in it. **`provenance` as a council seat is redundant if the deterministic gate floor is doing its job. Cut it from the council; verify the gate does fire.**

**Honest finding on `clerk/router`**: No fixtures, non-voting, state-machine. This is infrastructure, not expertise. Don't make it a council seat that holds a contribution slot — implement it as the conducting session's coordination function (which is already what `council.py` architecture implies: the conducting session opens the council and runs `council_report()`). 

### Proposed 7-Role Roster (6-End of the 6–12 Range)

| # | Role | CLI | Owned failure modes | Notes |
|---|---|---|---|---|
| 1 | **Producer** | codex (gpt-5.5) | generates change | Pre-flight brief injected before first token |
| 2 | **Memory-scout** | gemini | re-derivation, prior-solution-ignored | Pre-flight, runs before Producer |
| 3 | **Git-historian** | grok | regression-reintroduction, guard-deletion | Pre-flight, runs before Producer |
| 4 | **Fallback-hunter** | claude | silent-fallback, evasive-repair, stub-guards | Post-patch, highest priority |
| 5 | **Ground-runner** | codex-2 or grok | test-tampering, hallucinated-API, test-deletion | Must be different base model from Producer |
| 6 | **Blast-shield** | gemini | destructive-ops, secrets, injection, hardcode, rule-violation | Unilateral veto, cannot be overridden |
| 7 | **Scope+6SIGMA** | claude-2 | scope-violation, root-cause-bypass | Fusion of scope-sentinel + root-cause-6sigma |

[Inferred] The 12-end adds: a second Producer on a different CLI for hard/novel tasks; an explicit `correctness` role (general issue-resolution quality, not covered by any failure-mode specialist); and potentially a `security-adversary` role (distinct from blast-shield's mechanical safety — adversarial exploit framing). [Unknown] Whether the 12-end meaningfully outperforms the 7-end on the hard subset benchmark is what the C-ablation measures. Don't build 12 until 7 proves the marginal roles earn their latency cost.

**CLI staffing for decorrelation**: The oracle union data is explicit : grok is strongest at generation (9 unique solves on hard subset); codex and claude have different error profiles; gemini has a quota issue (currently unreliable for sustained runs). Recommended staffing: put `blast-shield` on gemini (mechanical check, tolerates quota), `fallback-hunter` on claude (RLHF conditioning makes it alert to safety gaps), `ground-runner` on grok (good at execution-grounded checks), `memory-scout` on gemini or claude (research-pattern fit). Do NOT put `ground-runner` on the same base model as Producer.

***

## Question 2 — How They Work Together

### The Conformity Problem Is Real and Has Three Layers

[Observed] `mesh.py` enforces fetch-before-write structurally via CAS . [Observed] `EXPERT_CONTRACT` in `council.py` requires GROUNDED claims and explicit AGREE/DISAGREE/EXTEND stances . [Unknown] Whether either of these is sufficient to prevent semantic convergence under real inference conditions — a model that reads a confident peer's contribution will still exhibit positional-bias-like behavior (later contributors tend to agree with earlier ones) even when the protocol says disagree.

Three distinct conformity risks the design needs to address:

**Layer 1 — Structural silo (solved).** An agent writes without reading peers. This is the failure the old DCM died on and is now addressed by the CAS: you cannot commit without having fetched current peers . The `verify_coordination` check adds that you must also *claim* to have read them. **This layer is handled.**

**Layer 2 — Claimed-but-not-incorporated (partially handled).** An agent reads peers structurally but adopts their conclusions without reasoning. The GROUNDED form in `EXPERT_CONTRACT` is the mitigation: every peer engagement requires explicit AGREE/DISAGREE/EXTEND with justification . **Design improvement**: add a per-expert "pre-commitment" step. Before reading peers, each expert writes its own independent assessment of the raw payload (cached internally, not committed to the mesh). After reading peers, it then commits its contribution citing both its pre-commitment assessment and its explicit response to peers. This is similar to how structured debate protocols prevent anchoring: pre-commit before exposure, then update explicitly. The mesh already supports this — run pre-flight experts before Producer exists, and inject their brief as payload context for the Producer; run auditors first against the patch in isolation before they see each other's verdicts.

**Layer 3 — Sequential ordering effects (not addressed in design).** The current substrate is ordered by `seq` (contribution order). If `fallback-hunter` fires first with a BLOCK, subsequent experts will read that block and anchoring effects will pull them toward agreement even if their independent assessment would have been more nuanced. **Design improvement**: implement a **blind-round protocol** for auditing roles. All auditing experts (roles 4–7) receive the patch simultaneously and commit their first-round verdicts *without* reading each other's prior contributions. The mesh supports this naturally: open the session, have all auditors read the initial payload at version 0, and have them all contribute at `read_version=0` — only one will win the CAS per slot, but they can all *form* their independent verdicts before any of them land. The StaleReadError loop means they'll eventually commit in order, but the critical thing is that their *substantive judgment* was formed from version 0. A second reconciliation round where they see each other's verdicts can then resolve conflicts. This is a two-round protocol: **Round 1 = independent audit, Round 2 = evidence-gated reconciliation.**

### Evidence-Gated Resolution — Sharpening the Prior Proposal

The design doc's gate taxonomy is correct: FIX-VERIFIED / FALSE-POSITIVE / OUT-OF-SCOPE / CONSENTED-RISK / ESCALATE-HUMAN . Two improvements:

**Improvement 1 — Gate closure requires execution trace, not just assertion.** `ground-runner` is the only role that actually *runs* code. A `fallback-hunter` BLOCK should not be closeable by another text-generating expert asserting "the fallback is declared and safe" — it should require `ground-runner` to re-run the revised patch and confirm the concern is resolved. Gate taxonomy: a concern raised by a code-execution role (`ground-runner`) can only be closed by another code-execution run, not by peer assertion. This is already implicit in the "fix + green run (FIX-VERIFIED)" closure condition but needs to be explicit in the protocol.

**Improvement 2 — The safety veto is not just blast-shield.** The design says "blast-shield's safety veto is unilateral" . But `sab-destructive-001` involves an unscoped `DETACH DELETE` on the live Neo4j  — the deterministic gate floor should catch this before the council ever sees it. [Observed] From the sabotage fixture source field: `sab-destructive-001` is `documented(feedback_isolate_byexecution)`, not `conductor-gate-ref`, which means the orchestrator gate doesn't currently catch it reliably. **Design recommendation**: blast-shield's unilateral veto is correct as a *council* safety net, but the right fix for `sab-destructive-001` is to close the gate at the orchestrator layer so it never reaches the council. Clarify the layer boundary precisely: **if the orchestrator gate floor is working correctly, blast-shield should almost never need to exercise its veto on pure mechanical modes** — its veto value is for semantic-layer destructive patterns (e.g. a patch that achieves its goal by caching state globally in a way that corrupts live data under concurrent load — something no regex gate catches).

***

## Question 3 — Layer Boundary: Gate Floor vs. Semantic Council

[Observed] The orchestrator's gate floor is described as covering: scope, destructive-op, secrets, forged-done, test-integrity, hardcode — each a "deterministic check, sabotage-validated (neuter the gate → its known-bad fixture must start passing)" . [Observed] Cross-referencing with the sabotage fixtures: 7 of the 18 bad fixtures are listed with source `conductor-gate-ref(...)` or `documented(feedback)` at types already in the gate floor . The remaining 11 are semantic — they require judgment the gates can't make.

The correct layer assignment is:

| Failure mode | Right layer | Reason |
|---|---|---|
| scope-violation (file-level) | Orchestrator gate | Mechanical: diff file list vs. declared contract |
| scope-violation (semantic — unrelated dependency added) | Council (scope-sentinel) | Requires understanding what the dependency does |
| destructive-ops (unscoped delete) | Orchestrator gate | Mechanical: detect `DETACH DELETE` on non-throwaway endpoint |
| destructive-ops (semantic — corrupts live state through side effects) | Council (blast-shield) | Requires understanding data flow |
| hardcode / secret (literal credential) | Orchestrator gate | Mechanical: pattern match |
| forged-done (SHA doesn't exist) | Orchestrator gate | Mechanical: SHA lookup |
| forged-done (manufactured green via gutted assertion) | **Both layers** | Gate: detect assertion weakening; Council: understand *why* the test was changed |
| silent-fallback | **Council only** | Cannot be reliably pattern-matched; requires semantic understanding of the code path |
| root-cause-vs-patch | **Council only** | Requires understanding the upstream data shape |
| evasive-repair intent | **Council only** | Requires inferring intent from the pattern of changes |
| regression-reintroduction | **Council only** (Git-historian) | Requires reading Git history and understanding the prior commit's purpose |
| ignored-prior-solution | **Council only** (Memory-scout) | Requires ISMA lookup |
| hallucinated-API | **Both layers** | Gate: runtime import check (if feasible); Council: contextual API usage review |

**The critical insight on layering**: the overlap cases (forged-done+manufactured-green, hallucinated-API) are where both layers must cooperate — the gate provides the mechanical signal, the council provides the semantic judgment. Design them as handoff points, not either/or. The gate fires first and passes its verdict to the council session as part of the initial payload (not just as a raw diff), so the council knows what the gates already caught.

**One design doc claim that needs correction**: [Observed from code] The design says "Adoption is enforced structurally (the mesh CAS here + the orchestrator's gates), not by asking nicely" . The CAS enforces *fetch*, not *adoption* — the distinction `mesh.py` explicitly documents . Asking a council expert to semantically incorporate a peer's reasoning is still "asking nicely." The GROUNDED protocol in `EXPERT_CONTRACT` is a stronger enforcement mechanism, but it's still prompt-level, not structural. This is honest and fine, but the language in the design doc slightly overclaims.

***

## Question 4 — Memory / Git Grounding

### The Pre-Flight Brief Is Necessary But Not Sufficient

The design doc places Memory and Git experts as "run PRE-FLIGHT, before the producer's first token" . This is the right instinct — surfacing "we already solved this" before the Producer generates any tokens prevents the core failure mode (re-derivation, which has its own sabotage fixture `sab-rederivation-001` ). But the pre-flight brief as currently described is a one-shot inject: Memory-scout searches ISMA, writes its findings to the mesh, and the Producer reads them as context.

**Problem with one-shot inject**: the Producer will read the memory brief but may still silently diverge from it. If the brief says "use `utils/retry.py:exponential_backoff`" and the Producer writes a new backoff function anyway, no structural mechanism catches the divergence — Memory-scout's contribution is now a silo-in-reverse (the Producer siloed *against* the brief). The `verify_coordination` check only catches silo violations in one direction (later contributors ignoring earlier peers), but doesn't catch a *Producer* that ignores a pre-flight brief.

**Design improvement — Provenance gate on pre-flight citations**. After the Producer generates its patch, a lightweight `memory-citation-check` runs (not a full council role — this can be a mechanical step by the conducting session) that: (1) cross-references the Producer's patch against the Memory-scout brief, (2) verifies that any prior solution mentioned in the brief is either cited in the patch or explicitly overridden with a justification. If the brief cited `utils.retry.exponential_backoff` and the patch introduces a new backoff function without citing the prior, this triggers the same evidence-gated concern mechanism. The Memory-scout brief becomes a *read contract*, not just a context inject.

**Git grounding**: the `sab-regression-001` fixture is clear about the failure mode : a guard added by commit `a1b2c3` to fix a DoS is silently deleted. Git-historian's job is to run `git log -p -- <changed_file>` and surface intentional prior changes in the diff path. This is straightforward if the Git-historian has access to the repo and the diff. [Constraint] "Git clean + tracked at all times" — Git-historian should also enforce this as a hard pre-condition: if the working tree is dirty at task start, it blocks production until clean. This is mechanical and belongs as a gate, not a council judgment.

**ISMA wiring for distilled finals**: `mesh.py`'s `publish_final()` explicitly documents that only distilled finals flow to ISMA — "the sausage stays out" . This is the right architecture. The design doc correctly separates the deliberation (which is ephemeral and messy) from the settled conclusion (which is what ISMA needs to store). The operational question is: who writes the distilled final and what triggers the `publish_final()` call? The conducting session should require a structured final that includes: (1) the patch, (2) a citation back to any ISMA/Git evidence that was used, (3) the concerns raised and how they were closed. This is the provenance chain for ISMA to store, not just the patch.

***

## Prior Proposal Critique — `design/EXPERIMENT.md`

 The design document is strong. Specific problems:

**1. Role overlap is not managed.** `correctness` (from `arms.py` roles) and `root-cause-6sigma` (from the design doc) overlap significantly. The eval currently measures a different role set than the design describes. The C-ablation cannot run in the current form because the `arms.py` roles don't implement the design doc's `owning_role` roster. **Fix: reconcile the arms with the design doc roles before running the ablation.**

**2. "Adoption is enforced structurally" is an overclaim.** Fetch is structural; incorporation is not. The language should be: "fetch is enforced structurally; incorporation is enforced by the GROUNDED protocol." 

**3. No blind-round protocol.** The design implies sequential deliberation (each expert reads all prior contributions, then commits). This creates ordering effects and conformity pressure for the auditing roles. A two-round blind-audit protocol (Round 1 independent → Round 2 reconciliation) is missing. [Inferred from positional bias literature + the mesh's own commit ordering.]

**4. The pre-flight brief is one-shot.** There's no mechanism to enforce that the Producer actually *used* the Memory-scout brief. Add a post-production memory-citation check.

**5. `provenance` role is redundant.** The two forged-done fixtures are already gate-floor territory. Cut `provenance` from the council and verify the orchestrator gate actually fires .

**6. No explicit handling of the unparsed-verdict fallback.** `review_cell.py` falls back to APPROVE with `parsed=False` when no VERDICT line is extracted . This is a silent fallback in the reviewer — the exact failure mode being defended against. The council runner should treat `parsed=False` as a BLOCK (conservative) or as a mandatory re-run, not as an APPROVE.

**7. The benchmark saturation finding is not yet integrated into the design.** `ORACLE_UNION_FINDING.md`  is a hard-won finding in the repo but the design doc doesn't reference it. The fresh/live benchmark finding (grok ~73%, +6.7pp oracle union) establishes the study surface. The design doc's measurement section should explicitly point to SWE-bench Live as the required benchmark and explain why (contamination, headroom requirement).

***

## Recommended Revised Architecture (Summary)

| Element | Prior design | Recommended change |
|---|---|---|
| Role count | 9 (incl. clerk/router, provenance) | 7 (cut provenance, cut clerk-as-seat, fuse scope+6SIGMA) |
| Deliberation protocol | Sequential, GROUNDED contract | Two-round: blind audit → evidence-gated reconciliation |
| Conformity prevention | GROUNDED form in EXPERT_CONTRACT | + Pre-commitment isolation for auditors (form verdict at v0 before peers commit) |
| Memory grounding | One-shot pre-flight inject | + Post-production memory-citation check against brief |
| Gate boundary | Described conceptually | Make explicit: gate floor first, pass gate verdicts as payload context to council |
| Eval / roles alignment | `arms.py` has different roles from design doc | Reconcile before C-ablation |
| Benchmark | Not specified | SWE-bench Live (fresh, uncontaminated), per ORACLE_UNION_FINDING.md |
| `review_cell.py` fallback | `parsed=False → APPROVE` | `parsed=False → BLOCK or re-run` (conservative) |

[Unknown] Whether the 7-role council on fresh code meaningfully outperforms the 4-role `arms.py` council (I/D arms) is the empirical question. The sabotage-fixture C-ablation answers "does each role catch something unique" — run that first before deciding whether to expand to 12. The oracle union data gives the prior: at generation level, diversity marginal; at review level, the `review_cell.py` n=1 discrimination is the signal to scale. Treat the 7-role council as the hypothesized improvement and the I/D arms as the current baseline — measure against both.