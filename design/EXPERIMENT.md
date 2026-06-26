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

## Round-2 consult result (5/5) — see `design/ROUND2_SYNTHESIS.md`

A 5-CLI panel (Grok/Perplexity/Claude-MAX verified the code; Gemini/ChatGPT sandbox-limited)
designed the mix against this repo. **Converged** (≥3 lanes, independent): the function set is
~5 semantic jobs + producer + a non-voting clerk on a fatter deterministic floor — **fuse
Memory+Git → Foundation (pre-flight); fuse Fallback+Root-cause → Evasive-repair; Provenance =
non-voting clerk + floor detection (not a semantic seat)**; resolution is **evidence-gated,
never a vote, safety-veto unilateral, blind-first then reveal**; ground-runner MUST be a
different base than the producer; memory/Git is an enforced **citation gate**, not a brief.
**Count** lands ~6–8 standard, scaling to 12 for high-blast-radius work — settled empirically
by the ablation, not by argument. The substrate's CAS was confirmed a correct serialization
primitive against live code.

### What's NOT built yet (build punch-list, from the code audit — ordered)
1. **Evidence-gated resolution** — the mesh is append-only and `publish_final()` enforces nothing; build typed concern/resolution + **fail-closed `publish_final`** (the #1 item).
2. **Reconcile `eval/arms.py` roles with this roster** — the ablation can't run until they match.
3. **Fixture-grading + ablation harness** (feed fixture → role → assert verdict; drop role → measure slip + clean-control false-positives).
4. **`council.py` is a bookend** — build the real role→CLI conductor (or document the external one honestly).
5. **Sealed blind round-0** in the substrate (`verify_coordination` exempts it).
6. **Wire the Grok adapter** (`_RUNNERS` has only codex+gemini).
7. **`review_cell.py` unparsed verdict → BLOCK/UNVERIFIED**, not `approve` (it currently embodies the silent-fallback mode it audits).

### Safety (before any council runs on untrusted content)
- **Sandbox the acting CLIs** — `cli_adapter.py` seats an acting `codex exec`/`gemini` on attacker-influenceable peer text; "don't edit files" is advisory, not a sandbox. Run in throwaway, fs/network-dropped containers; refuse to seat an acting CLI on untrusted/ISMA-derived payload.
- **Close `destructive-ops` at the orchestrator floor** (currently `documented`, not a wired gate); the veto is the net, not the primary defense.

## How we measure it

The `eval/` harness grades real patches against the objective oracle, with a **sabotage-fixture
registry** (`eval/sabotage_fixtures.json`). On a benchmark with **headroom to fail** (SWE-bench
Live, not saturated Verified):
- **C-ablation** settles the count — unique-catch per role + clean-control false-positive rate; cut/fuse a role with no unique catch (unless rare-but-high-severity, e.g. the safety veto).
- **I/D arm contrast** settles whether real-time peer-visible deliberation helps at all — blind (I) vs peer-visible (D); `D−I` isolates the coordination primitive. If I≥D, the mesh is for write-discipline, not deliberation. Let it decide; don't assume D.
- **Recall-per-mode** (N fixtures per mode) tests the "narrow expert beats broad in-lane" thesis the current 1-per-mode fixtures can't adjudicate.
- **Bespoke history-rich eval** — memory/Git modes (ignored-prior-solution, regression) **cannot** be measured on SWE-bench (fresh, no ISMA/history); seed tasks from our own repos where the right answer requires citing/superseding a known prior. Until built, that mode is asserted, not measured.

The point is evidence: what the council catches that simpler configurations do not, measured.
