# DCM — Distributed Cognitive Mesh

A shared substrate that lets many AI instances **deliberate in real time and build on each
other's work** — a council of differently-prompted "experts" (Grok-Heavy style). Built for an
AI fleet to think together: better output through multi-lens cross-check, with adoption
enforced *in the substrate* rather than by asking nicely.

> Written for a coding agent. The whole point: a shared substrate produces zero real
> coordination unless using it is mandatory and non-bypassable. DCM enforces read-before-write
> *structurally* via a real compare-and-set.

## What the substrate actually guarantees (and what it doesn't)
The failure mode of "spawn N agents and tell them to coordinate" is that they **commit into
the void and silently work alone**, then report success. DCM's mechanisms, stated honestly
(three-register; verified by execution — see `Verification` below):

- **Real compare-and-set staleness gate** *(structural, unfakeable serialization)* — you pass
  `read_version` (the version you read) purely as a **gate token**: the write commits only if
  the live count still equals it (no peer arrived since). The slot `seq` is then derived
  **server-side** from that live count — the caller never chooses it, so a fabricated
  `read_version` cannot place a contribution in a future-slot gap (it just fails the gate). A
  composite uniqueness constraint on `(session_id, seq)` lets **exactly one** writer win a slot,
  the rest get `StaleReadError` and must re-read and redo. This **serializes concurrent
  writers** — proven under a 6-thread same-version race: exactly one commit per version, every
  trial. (The naive `count(c) WHERE cur=rv` check it replaces did **not** serialize — Neo4j
  read-committed takes no lock on a count, so all concurrent same-version writers passed it; the
  constraint index lock, not the count, is what serializes.) Consequence: you **cannot commit at
  a version a peer already advanced past**, so every prior peer was present in the read you
  committed against — *fetch-before-commit*, enforced. ("Unfakeable" is scoped to this
  serialization, not to semantic incorporation — see below.)
- **`peers_present` (server-recorded)** — who was present when you committed. After the CAS
  holds this equals what your read delivered. It is **presence/fetch, NOT proof you
  semantically incorporated anyone** — do not read it as "truly read."
- **`claimed_peers` (your self-report)** — your explicit assertion of what you read + used.
- **`verify_coordination()`** — flags any contribution whose `claimed_peers` omits an earlier
  *present* peer: an agent that did not even **claim** to read what was in front of it. It
  catches non-claiming silos (proven: it flags a `claimed=[]`-but-correct-version agent). It
  **does not** prove semantic incorporation and cannot catch an agent that lies by claiming
  reads it didn't do — that is unprovable from a graph. The structural guarantee is the CAS;
  this is the read-claim audit layered on it.

**What DCM does NOT claim:** that coordination "cannot be faked" in the absolute. Semantic
incorporation is the agent's asserted good faith, backed by the structural guarantee that the
peers were in front of it and that it could not commit while ignoring the version they live in.

## Files
| File | What |
|---|---|
| `mesh.py` | the substrate: `start_session` / `read_session` (one-read bundle + `version` + any `final`) / `contribute(read_version)` (real CAS) / `verify_coordination` / `publish_final`. Neo4j-backed (own `:DCMSession`/`:DCMContribution` namespace; set `DCM_NEO4J_URI`). |
| `council.py` | N-expert council runner + the `EXPERT_CONTRACT` (read → reason-citing-peers → contribute, retry-on-stale). |
| `taey_adapter.py` | run a served model (OpenAI-compatible endpoint, `TAEY_DCM_URL`) as a mesh expert. |
| `cli_adapter.py` | run CLI agents (`codex exec`, `gemini -p`) as mesh experts. **See its security note — peer content is injected into an acting CLI; sandbox before seating on untrusted content.** |
| `reference/` | the prior (2025) Neo4j-coordination implementation — lessons, not the base. |

## The one invariant (participant-agnostic)
Every participant — code agent, served model, CLI — funnels through the **same**
`mesh.contribute(read_version)` chokepoint via a thin adapter. That single CAS token enforces
read-before-write uniformly.

## Adoption / config (env)
- `DCM_NEO4J_URI` (default `bolt://localhost:7687`) — the mesh graph.
- `DCM_NEO4J_USER` / `DCM_NEO4J_PASSWORD` — Neo4j auth (optional on loopback).
- `TAEY_DCM_URL` (default `http://localhost:8765/v1/chat/completions`) — served model for `taey_adapter`.
- **Security — fail-closed:** a **non-loopback** `DCM_NEO4J_URI` with **no auth** is *refused*
  at connect time (a no-auth bolt port beyond localhost exposes a full read/write graph: read
  all sessions, forge/delete contributions, flip status/final). Set credentials, or
  `DCM_ALLOW_INSECURE=1` to override deliberately.

## Best practices (learned in production; research-grounded)
Blind-then-revise to avoid herding; preserve dissent + Unknown-register in synthesis (never
average it away); zero recorded dissent is *flagged as suspect* (the correlated-blind-spot
trap of same-model instances); a council *decides* — **production is the oracle, consensus is
not** (close on a real observation, not on agreement); no early in-swarm coordinator; convene
only for irreversible / high-stakes / genuine-conflict work.

Grounded form (highest-ROI, zero extra calls): every contribution states each **Claim** with
its **Ground** and an explicit **Stance** (Agree/Disagree/Extend + justification) toward peers
— eliminates sycophantic convergence and same-answer-different-reasoning collapse. **Deliberation
is for decide/synthesize, not verify**: independent-then-aggregate beats mutual-reading on
factual/adversarial tasks, and one adversarial voice degrades a deliberating group — so keep an
*independent* audit gate downstream of the council; a council produces, an independent gate
verifies. Unanimity is not safety — escalate a no-dissent result, don't trust it.

## Verification
The three honesty mechanisms were independently audited (open-mandate, by execution) and an
earlier cut was BLOCKED — the gate did not serialize, `verify_coordination` was circular, and
`peers_read` overstated reading. This version is the root-cause fix; re-verify by execution:
- 6-thread same-version race → **exactly one commit per version** (CAS serializes).
- a `claimed=[]`-but-correct-version contributor → **`verify_coordination` flags it** (not rubber-stamped).
- honest N-seat council under contention → all land, contiguous, `coordinated=True` (no false positives).
