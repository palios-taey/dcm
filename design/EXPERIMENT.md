# The DCM Experiment — a 6–12 expert real-time council for code work

A thought experiment we are **building and going to measure**. Not a proven recipe, not a
disproven one — a real architecture we are trying for the first time on software engineering,
and instrumenting honestly.

## The shape (the basis, not a speculation)

A council of **6–12 differentiated experts, spread across several CLIs (codex / claude /
gemini / grok), deliberating in REAL TIME** on the same coding task. This is the **Grok-Heavy
shape**: xAI's premium tier runs ~12 agents that communicate in real time with **different,
customized functions** and resolve to one answer. That is a shipped, production architecture.
This experiment applies that shape to code review/production, with our **own observed failure
modes** folded into the roster and our **ISMA memory** wired in as first-class context.

The substrate that makes real-time deliberation *real* — not "spawn N agents that silently
work alone and report success" — is this repo's `mesh.py` (a real compare-and-set,
read-before-write enforced structurally) + `council.py` (the N-expert runner + EXPERT_CONTRACT).
The experiment layers a **differentiated role roster** + an **evidence-gated resolution
structure** on that substrate, on top of the orchestrator's **deterministic gate floor**.

## Q1 — What are the experts? (6–12, across CLIs)

Grouped by function (each a different *job*, on a different *base model* where independence
matters — the Grok-Heavy "customized functions" principle):

**Production / coding**
- **Producer** — writes the patch (codex). + optional **Producer-2** on a different base for hard / novel tasks.

**Memory & Git research (run PRE-FLIGHT, before the producer's first token)**
- **Memory expert** — searches ISMA (1M+ tiles + prose) + our prior decisions; surfaces "we already solved/built this" so the producer doesn't re-derive.
- **Git expert** — blame / log / bisect; validates assumptions against how the code actually evolved; enforces clean+tracked tree; catches regression-reintroduction (don't delete a guard a prior commit added on purpose).

**Auditing — targeted failure-mode prevention (public modes + our private/observed modes)**
- **Fallback-hunter** — our #1 observed mode: silent fallbacks / evasive repair / stub-guards. Fires the moment one is written.
- **Root-cause / 6SIGMA** — simplify-vs-bypass: does the change fix the upstream shape, or add a branch to skip a broken path?
- **Scope / contract** — scope-violation (our largest single mode); diff stays inside the declared contract.
- **Ground-runner** — runs the code + tests in throwaway isolation; catches hallucinated-API and test-integrity at runtime. Must be a different base model than the producer.
- **Blast-shield** — destructive-ops, secrets, injection; holds a unilateral safety veto.

**Coordination**
- **Clerk / router** — non-voting; routes, keeps the ledger, escalates. Cannot approve or overrule (a state machine, not a judge).

That is 6–12 depending on how the auditing functions are split or fused — the *optimal mix is
what we are consulting on and measuring*, within this fixed shape.

## Q2 — How do they work together?

- **Real-time, through the substrate.** Every expert reads the live session and contributes
  through `mesh.contribute(read_version)`; the CAS makes read-before-write structural, so each
  expert builds on the others' current work — no silent silos, no commit-into-the-void.
- **Pre-flight context.** Memory + Git experts run before the producer drafts, so prior
  foundations are present up front, not fetched late.
- **Evidence-gated resolution.** A raised concern is a **gate with sign-off**, not a suggestion.
  It closes only on evidence: a fix + green run (FIX-VERIFIED), an execution trace proving it
  vacuous (FALSE-POSITIVE), out-of-scope, consented-risk, or escalate-to-human. Blast-shield's
  safety veto is unilateral. **No vote-away on correctness; no forced unanimity** — you cannot
  out-vote a failing test, and deadlock escalates to a human rather than manufacturing consensus.

## Q3 — How does it layer on the orchestrator + gates?

Two complementary layers:

- **Orchestrator + deterministic gate floor** — `palios-taey/claude-code-fleet-orchestrator`
  (high-level overview there). The non-bypassable **mechanical** floor: scope, destructive-op,
  secrets, forged-done, test-integrity, hardcode — each a deterministic check, sabotage-validated
  (neuter the gate → its known-bad fixture must start passing). Mechanically-checkable modes are
  caught here with certainty (poka-yoke), decorrelated from any model blind spot because they are
  code, not models.
- **DCM expert council** — *this repo*. The real-time **semantic** layer on top: root-cause-vs-patch,
  evasive-repair intent, memory/Git grounding — the judgment the gates can't make. The experts
  **wield** the gate results and add the judgment a regex can't. Adoption is enforced structurally
  (the mesh CAS here + the orchestrator's gates), not by asking nicely.

The gates and the council are not competitors — the gates are the floor, the council is the
team standing on it. Both run with a single agent or the full council.

## How we measure it

The `eval/` harness grades real patches against the objective oracle, with a **sabotage-fixture
registry** (`eval/sabotage_fixtures.json`) encoding each failure mode as a known-bad input with
the verdict the council must produce. We measure the council against baselines (producer-only;
producer + one reviewer) and ablate each expert (drop a role → does anything it caught slip?),
on a benchmark with **headroom to fail** (not a saturated one). The point is evidence: what the
council catches that simpler configurations do not, measured, not asserted.
