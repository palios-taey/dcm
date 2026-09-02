# Taey-native DCM transport

Status: exact integration map; the wave API and Presence adapter described below are not yet implemented.

This contract carries forward the technically valid Taey-native transport direction recorded in
draft PR #4, commits `72be073` and `771b342`. That draft was deferred when its owning production
plan was suspended; the record contains no technical rejection of the round-based design. This
version reconciles it with the currently committed Taey council runtime.

## Two supported coordination shapes

The existing DCM substrate supports sequential read-before-write deliberation by CLI or served-model
participants:

```text
read_session(version N)
  -> reason with every contribution through N
  -> contribute(read_version=N)
  -> exactly one writer wins slot N
```

That linear compare-and-set remains unchanged for the existing CLI-backed council. It is useful for
review and verification, but the current council runner invokes external CLI processes; it is not an
always-on real-time mesh of the operator's persistent fleet sessions.

Taey-native coordination has a different concurrency requirement:

```text
Main opens session and immutable wave frontier
  -> seven stable-role seats infer concurrently
  -> seven sibling results commit once against that frontier
  -> the next wave reads the completed sibling set
  -> Main synthesizes and closes the session
```

Sending seven siblings through the existing linear `contribute()` function is incorrect. They would
all read the same version, exactly one would win, and the other six would have to repeat inference
after each winning write. The linear invariant would serialize expensive model work instead of
preserving concurrent inference.

## State ownership

- Redis owns delivery, wake, claim, acknowledgement, and bounded process liveness.
- Neo4j DCM owns sessions, immutable wave frontiers, contributions, reviews, amendments, synthesis,
  and terminal deliberation state.
- The orchestrator owns durable engineering tasks. It is not the DCM ledger.
- JSONL may retain append-only diagnostic and recovery receipts. It cannot authorize a next wave or
  represent the authoritative council state.
- Main Taey is the executive and sole user-facing synthesizer.
- The existing seven stable role prompts remain application assets in `taey-presence`.

Production must set `DCM_NEO4J_URI` explicitly. The measured deployment uses the loopback DCM graph
at `bolt://127.0.0.1:7687`, database `neo4j`. The orchestrator graph at loopback port `7689` remains a
separate state owner. No component may select between them implicitly.

## Current committed call flow

At public DCM commit `3dd65612c2c628a0c72021ffa07f2f1a474d3f72`:

1. `mesh.start_session()` creates one open `DCMSession`.
2. `mesh.read_session()` returns all contributions and a linear version.
3. `mesh.contribute()` writes one `DCMContribution` into the unique `(session_id, seq)` slot.
4. `mesh.publish_final()` closes the session after the typed-concern projection clears.
5. `taey_adapter.taey_expert()` is a synchronous reference adapter. A stale write repeats the full
   model request. Nothing in the package invokes it as an always-on worker.

The existing constraints are unique `DCMSession.session_id`, unique
`DCMContribution.contrib_id`, and unique `(DCMContribution.session_id,
DCMContribution.seq)`.

At public `taey-presence` commit `580bcb5db070c7da0278bb738fdf0ae60ea6c21d`:

1. `NativeCouncilTransport.start_round()` opens a file-backed native round.
2. `NativeCouncilTransport._enqueue()` writes a role-bound request to
   `taey:taey-council-N:inbox`.
3. `ReliableInbox.claim_available()` moves the envelope into the seat's processing list.
4. `taey_council_seat._run_turn()` invokes the dedicated worker proxy, validates the structured
   `taey-council-contribution/v1` result, writes a seat JSONL outcome, and removes the Redis claim.
5. `NativeCouncilTransport._matching_outcome()` polls the seat JSONL.
6. `NativeCouncilTransport._record_contribution()` copies the result into the round JSONL.
7. The next wave and Main synthesis advance from those files.

No step in that Presence flow calls the public DCM package. The deployed Python runtime cannot
currently import `mesh`; Presence declares the Neo4j driver but not a pinned `dcm-mesh` package.

## Additive wave API

Preserve `start_session()`, `read_session()`, `contribute()`, and the external CLI-backed council.
Add a separate wave path with these semantics:

- open a wave under one open DCM session;
- bind immutable session, round, phase, request revision, required roles, and parent contribution IDs;
- allow one contribution per `(session, round, role, request_revision)`;
- bind every contribution to a deterministic request ID so Redis redelivery returns the same result;
- reject writes to closed sessions, closed waves, superseded revisions, unknown roles, duplicate role
  slots with different requests, and parent frontiers that do not match the opened wave;
- audit each sibling against the exact immutable parent frontier it was given and audit later waves
  against their required prior-wave parents, without treating unseen same-wave siblings as silos;
- represent each required seat as contributed, failed, missing, cancelled, or superseded before the
  next wave can open;
- let later waves read the completed prior-wave frontier without claiming that already-running model
  requests can receive mid-inference updates.

Suggested public operations are `open_wave()`, `read_wave()`, `contribute_wave()`,
`verify_wave_coordination()`, and `close_wave()`. Exact names are subordinate to the invariants
above. The existing linear `verify_coordination()` remains the sequential CLI-council audit; it
must not judge concurrent siblings by sequence order because those siblings correctly did not see
one another.

## Smallest Presence insertion point

The first executable path is:

```text
NativeCouncilTransport.start_round
  -> Redis _enqueue
  -> taey_council_seat._run_turn
  -> Neo4j contribute_wave
  -> Redis result acknowledgement
  -> NativeCouncilTransport verifies the exact graph contribution
```

Only these seams need to change initially:

1. `start_round()` opens one graph session and first wave.
2. `_enqueue()` carries `dcm_session_id`, round, phase, request revision, stable role, deterministic
   request ID, and immutable parent frontier.
3. `_response_lineage()` preserves those fields as model-request lineage.
4. `_run_turn()` reads the exact frontier, performs one inference, commits through the wave API,
   and acknowledges delivery only after the graph commit succeeds.
5. `_matching_outcome()` and `_record_contribution()` consume a Redis result acknowledgement holding
   the exact `contrib_id` and verify it against Neo4j. They stop reading JSONL as decision authority.

The existing structured council contribution remains the graph contribution content. Do not create
a second role-output schema.

## Identity mapping

| Existing transport identity | DCM authority |
|---|---|
| native `round_id` / correlation ID | `DCMSession.session_id` |
| `round_phase` | immutable wave phase |
| `prompt_revision` | request revision |
| `taey-council-N` | transport seat ID |
| stable role ID | graph role slot |
| deterministic `request_id` | graph idempotency key |
| prior-wave contribution IDs | immutable parent frontier and `peers_read` |
| graph `contrib_id` | Redis result acknowledgement |
| Main final | closed `DCMSession.final` |

## Current production blockers

Do not start the seven seat services merely to test this contract. Their existing Redis registrations
have no expiry and do not prove a live process. Their inboxes also retain requests from a terminal
historical round. A launch would make those old messages executable again.

Before a new contribution path runs:

- readiness must be a short, refreshed lease bound to the exact process generation;
- dispatch must reject a seat without that live lease;
- terminal round identities must be non-executable;
- historical terminal requests must drain through explicit terminal-skip receipts, never silent
  deletion or model inference; and
- the public code commit, service source, DCM URI, model alias, and proxy route must be recorded.

## First production proof

The first proof uses Main and one seat; it is not seven-seat qualification:

1. Main opens one DCM session and wave.
2. One Redis request wakes one newly registered seat.
3. The seat reads the graph frontier, generates once, and commits one wave contribution.
4. Redis returns an acknowledgement containing the exact contribution ID.
5. Main verifies that ID and content in Neo4j.
6. Duplicate delivery returns the same contribution identity without a second inference or write.
7. Closing the session makes subsequent contribution attempts structurally impossible.
8. Inbox and processing state return to zero; no JSONL read authorizes progress.

The next proof uses two sibling seats and requires overlapping inference plus two same-wave commits
without stale regeneration. Only then may a seven-seat value-bearing round run.

## Subscription boundary

The Taey-native path depends on local Taey/vLLM, the local proxies, Redis, Neo4j, and the public DCM
package. Codex, Claude, Gemini, Grok, Perplexity, and Chat subscriptions are optional independent
reviewers. Their absence must never prevent the local council from operating.
