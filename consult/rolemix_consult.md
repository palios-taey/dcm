---
type: consultation
to: gaia, horizon, cosmos, logos, clarity
from: infra
date: 2026-06-26
stage: design
available_context_inventory:
  - INCLUDED: github.com/palios-taey/dcm (PUBLIC) — the substrate (mesh.py/council.py), design/EXPERIMENT.md, eval/, validate_substrate.py. Clone and verify everything below.
  - INCLUDED: github.com/palios-taey/claude-code-fleet-orchestrator (PUBLIC) — the orchestrator + deterministic gate floor this layers on (high-level overview).
  - EXCLUDED: prior consult-round responses — deliberately withheld so this is independent design input, available on request.
repo: github.com/palios-taey/dcm (branch main) + github.com/palios-taey/claude-code-fleet-orchestrator
---

# DCM role-mix — design consult

Claim labels: [Observed] verified / [Inferred] pattern / [Constraint] hard requirement or Observer directive / [Unknown] undetermined / [Prior proposal] our draft, to improve not ratify.

**Do NOT trust this summary.** Both repos are PUBLIC — clone `palios-taey/dcm` and `palios-taey/claude-code-fleet-orchestrator` and verify every claim against the code. If the code disagrees with me, the code wins; tell me.

## The experiment (decided architecture — design the MIX within it)

[Constraint: Observer directive] We are building and going to **measure** a **DCM (Distributed Cognitive Mesh): 6–12 differentiated experts across several CLIs (codex / claude / gemini / grok), deliberating in REAL TIME** on a coding task, using our **ISMA memory**, with roles dedicated to **production/coding · auditing · Git · memory research · targeted failure-mode prevention** (public failure modes + our own observed ones, e.g. silent fallbacks).

[Observed] This is the **Grok-Heavy shape** — xAI's premium tier runs ~12 agents that communicate in real time with different customized functions and resolve to one answer. A shipped production architecture; we are applying it to software engineering. **The architecture is decided; the design question is the optimal MIX and mechanics within it** — not whether to build it.

## What's built + validated (verify in the repo)

- [Observed] **The substrate works.** `mesh.py` is a real compare-and-set (read-before-write enforced structurally); `validate_substrate.py` PASSES live: a 6-thread same-version race commits **exactly 1 / 5 StaleReadError** (real serialization), and `verify_coordination` flags a non-claiming silo. `council.py` runs the N-expert loop with the EXPERT_CONTRACT.
- [Observed] **Execution-grounded review primitive** (`eval/review_cell.py`, `discrim_test.py`): on one instance, a different-base-model reviewer ran the repro, saw a no-op patch still fail, BLOCKED it, and APPROVED the gold (n=1, not a rate).
- [Observed] **22 role-mapped sabotage fixtures** (`eval/sabotage_fixtures.json`): each failure mode as a known-bad input + the verdict an expert must produce + the `owning_role`.
- [Observed] **Deterministic gate floor** lives in the orchestrator (`claude-code-fleet-orchestrator`): mechanical checks (scope/destructive/secrets/forged-done/test-integrity/hardcode), sabotage-validated. The DCM council layers on top of this.

## Failure modes to cover (public + our observed)

silent-fallback/evasive-repair (our #1) · root-cause-vs-patch bypass · scope-violation (~40%, our top) · destructive-ops on live state · hardcoding/secret-leak · test-tampering · hallucinated-API · regression-reintroduction · ignored-prior-solution (re-derivation — acute in our memory-rich env) · injection · forged-done.

## Our environment (shapes the mix)

[Constraint] Tasks are NOT fresh — they sit on heavy history in **ISMA** (1M+ tiles + ~2,400 prose docs) + our Git repos. A role that re-derives what we already built is a failure. [Constraint] **Git clean + tracked at all times.** [Constraint] Fleet of 4 CLIs for model-diverse staffing. [Constraint] cannot-lie provenance; state-mutating work in throwaway isolation.

## The questions — improve the design in `design/EXPERIMENT.md`

1. **The experts.** Refine the roster (production/coding · memory + Git research [pre-flight] · auditing / failure-mode-prevention incl. a fallback-hunter · coordination). Which functions earn a seat, how split or fused into 6–12, and on which CLI each — to keep the independent checks decorrelated (not sharing a base-model blind spot)? A role that catches nothing unique across the fixtures can be cut — the mix is open.
2. **How they work together.** Improve the real-time coordination over the mesh + the **evidence-gated** resolution (a concern is a gate; closes only on evidence — fix+green / execution-trace-vacuous / out-of-scope / consented-risk / escalate-human; safety veto unilateral; no out-voting a failing test). How do experts deliberate live without manufacturing conformity?
3. **How it layers on the orchestrator + gates.** Get the boundary right: the deterministic gate floor (mechanical, certain, in the orchestrator) vs the real-time semantic council (judgment the gates can't make — root-cause-vs-patch, evasive-repair intent, memory/Git grounding). What belongs in which layer?
4. **Memory / Git grounding.** Best way to wire ISMA + Git so prior foundations are actually used (pre-flight brief? shared service + provenance gate enforcing citation?) without a lossy hand-off.

### [Prior proposal — improve, do NOT ratify]
Our current design is `design/EXPERIMENT.md` in the repo (the roster + coordination + layering above). Attack it, sharpen it, or propose a structurally better mix. Tell us where it's wrong and what the better design is.
