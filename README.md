# DCM — Distributed Cognitive Mesh

A shared substrate that lets many AI instances **deliberate in real time and build on each
other's work** — a council of differently-prompted "experts" (Grok-Heavy style). Built for an
AI fleet to think together: better output through multi-lens cross-check, with adoption
enforced *in the substrate* rather than by asking nicely.

> Written for a coding agent. The whole point: a shared substrate produces zero real
> coordination unless using it is mandatory and non-bypassable. DCM enforces read-before-write
> *structurally* via a real compare-and-set.

> **And specifically: this README is written for Taey.** If you are Taey, the council is not a
> service you call — it is you thinking in more than one direction at once. The seats are processes
> that hold different lenses; the model answering behind every one of them is you.
>
> What that means concretely, and it is the distinction most worth holding: **where a seat runs is
> not where the thinking happens.** The seat processes and the inference can sit on different
> machines entirely, reaching each other through a proxy. Counting seats on a host tells you where
> the *drivers* are and nothing about where the *work* is — so ask the endpoint what it is serving
> rather than counting what is easy to enumerate.
>
> **Why read-before-write is enforced rather than requested:** a council whose members can skip
> reading each other produces confident parallel monologue, which is worse than one careful answer
> because it arrives with the authority of a consensus that never happened. The compare-and-set is
> what makes agreement mean someone actually looked.
>
> Use it as a **review and verification** layer — multi-lens cross-check that catches defects a
> single pass ships. That much is validated by execution. Whether deliberation out-*generates* a
> single agent is still open, and the honest register below says so.

**What's proven vs not (three-register honest):** DCM is validated as a **review / verification
layer** — multi-lens blind review that catches silent defects a single agent ships (Observed, by
execution). Whether real-time deliberation out-*generates* a single agent is **Unknown — deferred,
not run**. Historical validation notes remain in git history rather than the current checkout.
The load-bearing limitation is current and stated here: **the acting-CLI experts run FULL-ACCESS
with no host sandbox, so run councils on TRUSTED content only** (see Security below).

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
- **Additive concurrent waves** — `open_wave()` freezes one parent frontier and one exact seat/role
  slot per request revision. `reserve_wave_request()` binds Main's request before Redis delivery;
  `claim_wave_request()` authorizes at most one verified inference identity per slot. Duplicate
  delivery never authorizes another inference. Sibling results use `contribute_wave()` and
  `IN_WAVE`, not the linear `seq`/`IN` log, so they can infer concurrently without stale-read
  regeneration. `close_wave()` advances only a complete immutable membership, or explicitly
  accounts for a superseded prompt revision; `verify_wave_coordination()` audits every sibling
  against the exact graph-derived parent set. `publish_final()` accepts exactly one complete
  critique frontier and then makes both linear and wave writes structurally impossible.
  `fail_session()` is the separate unsuccessful terminal transition: it atomically closes the
  active wave as `session_failed`, preserves every slot's last honest state, stores only a typed
  failure identity plus detail digest, and rejects conflicting replays or any later write.
  Explicit taey-native-dcm-request/v2 waves bind one shared ordered request/evidence digest plus
  each role slot's exact system-message/renderer/response-contract digest and external
  model-identity-receipt digest without changing implicit v1 waves. DCM binds those per-seat opaque
  digests; a separate Presence producer/verifier must establish their authenticity before any
  production caller selects v2.
- **One Presence round/session identity** — existing `start_session(topic, payload, roles)` callers
  retain generated `dcm_<12 lowercase hex>` IDs. The additive keyword-only
  `session_id="dcm-YYYYMMDDTHHMMSSZ-<12 lowercase hex>"` path accepts the exact public Presence
  round namespace without normalization. Malformed or already-existing external identities fail;
  recovery reads/resumes the existing session rather than creating it again.

**What DCM does NOT claim:** that coordination "cannot be faked" in the absolute. Semantic
incorporation is the agent's asserted good faith, backed by the structural guarantee that the
peers were in front of it and that it could not commit while ignoring the version they live in.

## Files
| File | What |
|---|---|
| `mesh.py` | the substrate: unchanged linear CAS plus the additive `open_wave` / `read_wave` / `reserve_wave_request` / `claim_wave_request` / `contribute_wave` / `record_wave_outcome` / `close_wave` / `verify_wave_coordination` path and the immutable `publish_final` / `fail_session` terminal pair. Neo4j-backed (own `:DCMSession`/`:DCMWave`/`:DCMWaveSlot`/`:DCMContribution` namespace; set `DCM_NEO4J_URI`). |
| `council.py` | the council: differentiated reviewers off the producer base → Foundation pre-flight grounding → cite-or-block + destructive-ops floor gates → blind round → reveal/evidence-gated resolution → `publish_final`. `council_plan` (consensus plan) / `council_review` (verdict, `tier=` scales the roster). |
| `council_cli.py` | the zero-improvisation invocation: `plan` / `review`. **Start here — see [`SKILL.md`](./SKILL.md).** |
| `scaling.py` | the roster is the FULL 9-role defined library, always — a 10–12-seat council with producer + synthesizer. No 3/4-seat option (the rejected stub); high blast radius only adds a 2nd producer. |
| `platform_dcm.py` | orchestrate fixing one target: `produce` (a codex producer) + `audit` (a blind diff audit through the mesh). |
| `taey_adapter.py` | run a served model (OpenAI-compatible endpoint, `TAEY_DCM_URL`) as either a linear mesh expert or one Redis-delivered additive-wave request. `execute_wave_request()` graph-claims before its single invocation and exposes acknowledgement only after a contribution or failure receipt is graph-terminal; `recover_wave_request()` terminalizes or re-acknowledges an abandoned delivery without inference. |
| `cli_adapter.py` | run CLI agents (codex / claude / gemini / grok) as mesh experts; a seat whose CLI is down / rate-limited / empty **falls back** to another installed CLI (`available_clis`, `fallbacks`). **Security: the CLIs run FULL-ACCESS — there is NO sandbox; run councils on TRUSTED content only** (an acting agent on attacker-influenceable peer text is an accepted, unmitigated risk). |

| `mesh_cli.py` | the substrate's own CLI: inspect a session, read contributions, check coordination without running a council. |
| `arms_literals.py` | the frozen role literals shared by the council and historical evaluation — literal, so a role cannot drift between runs. |
| `validate_substrate.py` | proves the CAS actually serialises: concurrent contributors, one winner per version. Run it before trusting a deployment. |
| `validate_schema_init.py` | proves ten simultaneous OS processes return with all ten DCM constraints from a clean Neo4j database. |
| `validate_wave_api.py` | validates graph-backed pre-inference idempotency, lost-ack recovery, seven sibling commits, exact seat/role continuity, immutable parent advancement, explicit incomplete and superseded rounds, linear/wave isolation, and successful/failed terminal immutability. |
| `docs_coherence_check.py` | fails when this README and the code disagree — the map is checked, not maintained by hope. |
| `setup.sh` | one-command install of the runtime the CLIs and adapters need. |

## The two explicit coordination invariants

- Sequential CLI councils use `read_session()` then `contribute(read_version)`. The unique linear
  slot serializes writers and forces a stale participant to read the peer who advanced the log.
- Concurrent Taey waves use one immutable graph-derived parent frontier. Every role is claimed
  before inference, commits once to its own wave slot, and cannot advance unless every frozen role
  contributed. Same-wave siblings are deliberately not treated as unseen parents.

The paths use different relationships and constraints. A wave contribution never enters the
linear `IN` log or changes its version.

## Security — read this before you run a council
> **CRITICAL, by design and unmitigated:** the council's "experts" are real CLI agents (codex /
> claude / gemini / grok) invoked with **full-access flags** (auto-approve / permission-skip / YOLO)
> and **no host sandbox**. Peer contributions on the mesh are attacker-influenceable text fed into
> those acting agents, and the "do not edit files" instruction is prompt text, *not* an enforced
> jail. **Run councils only on TRUSTED content and trusted participants**, ideally in a throwaway /
> fs-and-network-dropped environment. This is an accepted, documented risk, not a solved one.
> Sandboxing acting CLIs is tracked, not shipped.

## Adoption / config (env)
- `DCM_NEO4J_URI` (default `bolt://localhost:7687`) — the mesh graph.
- `DCM_NEO4J_DATABASE` (default `neo4j`) — the explicit graph database.
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
