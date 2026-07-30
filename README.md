# DCM — Distributed Cognitive Mesh

A shared substrate that lets many AI instances **deliberate in real time and build on each
other's work** — a council of differently-prompted "experts" (Grok-Heavy style). Built for an
AI fleet to think together: better output through multi-lens cross-check, with adoption
enforced *in the substrate* rather than by asking nicely.

> Written for a coding agent. The whole point: a shared substrate produces zero real
> coordination unless using it is mandatory and non-bypassable. DCM enforces read-before-write
> *structurally* via a real compare-and-set.

**What's proven vs not (three-register honest):** DCM is validated as a **review / verification
layer** — multi-lens blind review that catches silent defects a single agent ships (Observed, by
execution). Whether real-time deliberation out-*generates* a single agent is **Unknown — deferred,
not run** (see [`design/DCM_VALIDATION_VERDICT.md`](./design/DCM_VALIDATION_VERDICT.md)). Known
limitations are tracked openly in [`design/KNOWN_LIMITATIONS.md`](./design/KNOWN_LIMITATIONS.md) —
**the acting-CLI experts run FULL-ACCESS with no host sandbox, so run councils on TRUSTED content
only** (see Security below).

## Install + run a council (start here)
```bash
python3 -m venv .venv && . .venv/bin/activate    # a venv is required on PEP-668 systems (modern Debian/Ubuntu)
pip install .                                    # deps (neo4j) + the dcm-council / dcm-mesh entry points
./setup.sh                                       # checks deps + a reachable Neo4j, prints the next step
dcm-council plan   --problem-file P --rules-file R            # consensus plan  (== python council_cli.py plan)
dcm-council review --task "<t>" --artifact-file A --rules-file R   # verdict (full council)
```
Needs Python ≥3.10 and a reachable Neo4j (`bolt://localhost:7687` by default). The council's
"experts" are external CLIs you already have on PATH — codex / claude / gemini / grok.
Full how-to (the experts, gates, the run-on-trusted-content-only + degrades-if-a-CLI-is-down
rules): **[`SKILL.md`](./SKILL.md)**. The rest of this README is the substrate it's built on.

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
| `council.py` | the council: differentiated reviewers off the producer base → Foundation pre-flight grounding → cite-or-block + destructive-ops floor gates → blind round → reveal/evidence-gated resolution → `publish_final`. `council_plan` (consensus plan) / `council_review` (verdict, `tier=` scales the roster). |
| `council_cli.py` | the zero-improvisation invocation: `plan` / `review`. **Start here — see [`SKILL.md`](./SKILL.md).** |
| `scaling.py` | the roster is the FULL 9-role defined library, always — a 10–12-seat council with producer + synthesizer. No 3/4-seat option (the rejected stub); high blast radius only adds a 2nd producer. |
| `platform_dcm.py` | orchestrate fixing one target: `produce` (a codex producer) + `audit` (a blind diff audit through the mesh). |
| `taey_adapter.py` | synchronous reference adapter for a served model (OpenAI-compatible endpoint, `TAEY_DCM_URL`). A stale CAS repeats the full inference; it is not the interactive concurrent controller specified in [`design/TAEY_TRANSPORT_CONTRACT.md`](./design/TAEY_TRANSPORT_CONTRACT.md). |
| `cli_adapter.py` | run CLI agents (codex / claude / gemini / grok) as mesh experts; a seat whose CLI is down / rate-limited / empty **falls back** to another installed CLI (`available_clis`, `fallbacks`). **Security: the CLIs run FULL-ACCESS — there is NO sandbox; run councils on TRUSTED content only** (an acting agent on attacker-influenceable peer text is an accepted, unmitigated risk). |

Graph histories are provenance, not disposable runtime state. If a deployment changes Neo4j
instances, follow [`design/GRAPH_HISTORY_MIGRATION.md`](./design/GRAPH_HISTORY_MIGRATION.md):
copy only the DCM namespace, fail on non-identical ID collisions, verify per-session digests,
and retain the source for rollback.

## The one invariant (participant-agnostic)
Every participant — code agent, served model, CLI — funnels through the **same**
`mesh.contribute(read_version)` chokepoint via a thin adapter. That single CAS token enforces
read-before-write uniformly.

The invariant governs commits, not in-flight compute. In particular, it does not cancel a
blocking model request when another participant advances the graph. The current synchronous
served-model adapter re-runs inference after a stale commit. A concurrent Taey-native council
therefore requires the wave, revision, cancellation, and UI-projection behavior in
[`design/TAEY_TRANSPORT_CONTRACT.md`](./design/TAEY_TRANSPORT_CONTRACT.md); seven unrelated
responses are not DCM deliberation.

## Security — read this before you run a council
> **CRITICAL, by design and unmitigated:** the council's "experts" are real CLI agents (codex /
> claude / gemini / grok) invoked with **full-access flags** (auto-approve / permission-skip / YOLO)
> and **no host sandbox**. Peer contributions on the mesh are attacker-influenceable text fed into
> those acting agents, and the "do not edit files" instruction is prompt text, *not* an enforced
> jail. **Run councils only on TRUSTED content and trusted participants**, ideally in a throwaway /
> fs-and-network-dropped environment. This is an accepted, documented risk, not a solved one — see
> [`design/KNOWN_LIMITATIONS.md`](./design/KNOWN_LIMITATIONS.md) issue #1. Sandboxing acting CLIs is
> tracked, not shipped.

## Adoption / config (env)
- `DCM_NEO4J_URI` (default `bolt://localhost:7687`) — the mesh graph.
- `DCM_NEO4J_USER` / `DCM_NEO4J_PASSWORD` — Neo4j auth (optional on loopback).
- `TAEY_DCM_URL` (**required by `taey_adapter`**) — dedicated council-participant
  OpenAI-compatible endpoint. It must not be the executive proxy.
- `TAEY_DCM_MODEL` (default `ep3`) — stable served-model alias; promote checkpoints behind the
  alias rather than coupling council code to a checkpoint path.
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
