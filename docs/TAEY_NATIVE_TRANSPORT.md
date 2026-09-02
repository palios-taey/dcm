# Taey-native DCM transport

Status: exact integration map; the public wave API is implemented, while the Presence adapter
described below is not yet implemented or production-qualified.

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

Production must set `DCM_NEO4J_URI` and `DCM_NEO4J_DATABASE` explicitly. The measured deployment uses
the loopback DCM graph at `bolt://127.0.0.1:7687`, database `neo4j`. The orchestrator graph at loopback
port `7689` remains a separate state owner. No component may select between them implicitly.

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
- bind immutable session, round, phase, prompt material, request revision, exact seat/role membership,
  and relationship-derived parent contribution IDs;
- allow one contribution per `(session, wave, role, request_revision)`;
- bind every contribution to a deterministic request ID so Redis redelivery returns the same result;
- reject writes to closed sessions, closed waves, superseded revisions, unknown roles, duplicate role
  slots with different requests, and parent frontiers that do not match the opened wave;
- audit each sibling against the exact immutable parent frontier it was given and audit later waves
  against their required prior-wave parents, without treating unseen same-wave siblings as silos;
- represent each required seat as contributed, failed, missing, cancelled, or superseded before the
  next wave can open;
- let later waves read the completed prior-wave frontier without claiming that already-running model
  requests can receive mid-inference updates.

The public operations are `open_wave()`, `read_wave()`, `reserve_wave_request()`,
`claim_wave_request()`, `contribute_wave()`, `record_wave_outcome()`,
`verify_wave_coordination()`, `close_wave()`, `open_concerns()`, and `publish_final()`. Main reserves each graph-bound request before
placing it in Redis. The role-bound worker proves its exact live seat, process generation, endpoint,
alias, model, and container identity before the graph claim authorizes inference. Commit-time
deduplication alone would let two duplicate deliveries both invoke the model before one lost the
contribution-slot race. The existing linear `verify_coordination()` remains the sequential
CLI-council audit; it must not judge concurrent siblings by sequence order because those siblings
correctly did not see one another.

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
2. `_enqueue()` first reserves the exact seat/role request in Neo4j, then carries `dcm_session_id`,
   round, phase, prompt and request revisions, stable role, deterministic request ID, and immutable
   parent frontier to Redis.
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
| native prompt revision | graph prompt revision |
| graph request revision | idempotent dispatch revision |
| `taey-council-N` | transport seat ID |
| stable role ID | graph role slot |
| deterministic `request_id` | graph idempotency key |
| prior-wave contribution IDs | immutable parent frontier and `peers_read` |
| graph `contrib_id` | Redis result acknowledgement |
| Main final | closed `DCMSession.final` |

## Cannot-lie receipt contract

`taey-native-dcm-receipt/v1` is the normative contract for the first Taey-native adapter. It does
not prove that a model understood a parent contribution or that a Redis-privileged actor is honest.
It does make delivery, execution identity, graph authority, and acknowledgement mechanically
correlatable without relying on JSONL or wall-clock order.

### Canonical encoding and identity

Every receipt is UTF-8 JSON encoded with keys sorted lexicographically, separators `(",", ":")`,
`ensure_ascii=false`, and `allow_nan=false`. Set-like arrays are sorted and contain no duplicates.
Digests use `sha256:<64 lowercase hex>`. `receipt_sha256` is the digest of the canonical object with
that field omitted. Timestamps are diagnostic only; they never establish cross-host causal order.
Each named digest hashes the canonical JSON value named by the contract; implementations never hash
delimiter-free string concatenation. Ordered model messages remain ordered. Only fields explicitly
described as set-like are sorted.

Every contribution and transport receipt carries this immutable envelope:

```json
{
  "contract": "taey-native-dcm-receipt/v1",
  "receipt_kind": "contribution|transport",
  "session_id": "dcm_...",
  "correlation_id": "dcm_...",
  "wave_id": "wave_...",
  "round": 1,
  "phase": "independent",
  "prompt": {
    "prompt_id": "...",
    "revision": 1,
    "sha256": "sha256:..."
  },
  "seat_id": "taey-council-1",
  "role": "context-memory",
  "request_revision": 1,
  "request_id": "sha256:...",
  "emitter": {
    "component": "native-coordinator|taey-council-seat|dcm-adapter",
    "process_generation": "..."
  },
  "graph": {
    "uri": "bolt://127.0.0.1:7687",
    "database": "neo4j"
  },
  "frontier": {
    "parent_contribution_ids": [],
    "parent_frontier_sha256": "sha256:...",
    "claimed_peers": [],
    "peers_present": []
  },
  "execution": {
    "model_endpoint": "http://127.0.0.1:8767/v1/chat/completions",
    "process_generation_expected": "...",
    "process_generation_observed": "...",
    "requested_alias": "ep3",
    "served_alias": "ep3",
    "model_manifest_sha256": "sha256:...",
    "model_content_sha256": "sha256:...",
    "serving_container_digest": "sha256:..."
  },
  "receipt_sha256": "sha256:..."
}
```

For the first adapter, `correlation_id` and `session_id` are identical. A future translation requires
a new contract version; no runtime may infer one. `prompt.sha256` hashes the canonical ordered model
message list plus the ordered attachment/evidence content digests. `parent_frontier_sha256` hashes
the canonical sorted `parent_contribution_ids` array. `request_id` hashes a canonical JSON object
with these named fields:

```text
session_id, wave_id, round, phase
prompt_id, prompt_revision, prompt_sha256
seat_id, role, request_revision
parent_frontier_sha256
expected process generation
model endpoint, requested alias
model manifest/content digests, serving-container digest
```

Redis stream/list IDs and timestamps are excluded. `request_revision` is an immutable wave-local
dispatch ordinal and may begin at 1 again when Main opens a new wave; `wave_id` keeps those requests
distinct. A changed prompt, frontier, role, process generation, endpoint, model identity, or
container identity cannot mutate or retry an existing request: Main opens a new wave and derives a
new request ID. Missing execution identity is `model_identity_unproven`; alias or endpoint
substitution is forbidden.

#### Additive v2 request contract

The legacy wave contract remains implicit v1. It accepts exactly `{seat_id, role}` members, the v1
request-identity fields above, and the original claim-observation and contribution-receipt shapes.
No v1 caller sends a `request_contract` field, and v1 request IDs and receipts are unchanged.

V2 is selected only when `open_wave()` receives
`request_contract="taey-native-dcm-request/v2"`. Any other explicit value is rejected. Every v2
member contains exactly these fields:

```text
seat_id, role, prompt_contract_sha256, model_identity_receipt_sha256
```

The member object is included in the immutable membership digest and copied to its graph role slot.
`prompt_contract_sha256` names the complete Presence seat prompt contract for that role;
`model_identity_receipt_sha256` names the external model-identity receipt selected for that seat.
Both use the canonical `sha256:<64 lowercase hex>` encoding. DCM binds these opaque digests; it does
not generate either source object or claim that digest equality verifies the object's contents.

A v2 request identity contains exactly the v1 request-identity fields plus:

```text
request_contract, prompt_contract_sha256, model_identity_receipt_sha256
```

The two digests must equal the graph slot, and all three added fields participate in `request_id`.
The v2 claim observation contains exactly the v1 claim-observation fields plus the two digests and
must match the frozen request before inference is authorized. A successful v2 contribution returns
`contract="taey-native-dcm-receipt/v2"` and adds these exact top-level receipt fields:

```text
request_contract, prompt_contract_sha256, model_identity_receipt_sha256
```

Receipt reconstruction validates them against membership, request identity, and the stored claim.
Membership, contract version, or either digest cannot change between waves in one session.

### Contribution receipt

After a successful graph commit, the envelope carries:

```json
{
  "receipt_kind": "contribution",
  "contribution": {
    "contrib_id": "contrib_...",
    "kind": "contribution",
    "content_sha256": "sha256:...",
    "about": null,
    "severity": null,
    "veto": false,
    "disposition": null,
    "evidence_ref": null,
    "evidence_ref_sha256": null
  },
  "terminal_outcome": "contributed"
}
```

`kind` uses the existing public DCM kinds: `contribution`, `plan_proposal`, `consensus_plan`,
`concern`, or `resolution`. The existing structured `taey-council-contribution/v1` object remains
the content; the receipt does not define another role-output schema. `content_sha256` hashes that
canonical structured object. `evidence_ref_sha256`, when present, hashes the exact UTF-8 evidence
reference string.

For a Taey-native wave, all three sets below must be identical:

```text
request parent_contribution_ids
= worker claimed_peers
= graph-derived peers_present
```

The graph derives `peers_present` only from the immutable wave frontier. Same-wave siblings are never
parents. `claimed_peers` remains a self-report about semantic incorporation; equality proves
only that the worker made the required claim against the exact frontier it received. A mismatch is
terminal `frontier_mismatch`, not a reduced frontier or a retry.

On success, the observed process generation equals the expected generation, the served alias equals
the requested alias, and the graph URI/database equal the explicitly configured DCM target. A
receipt cannot silently substitute the orchestrator graph at port `7689`. The observed generation
may be `null` only on a coordinator-issued pre-claim refusal; such a receipt cannot carry a
contribution ID or claim that inference occurred.

Exactly one contribution occupies `(session_id, wave_id, role, request_revision)`. Redelivery of
an identical request returns the original contribution ID and receipt without another inference or
write. The same identity with different canonical content is terminal `identity_conflict`.

### Redis transport receipt

One transport schema records both the non-terminal claim and the terminal acknowledgement:

```json
{
  "receipt_kind": "transport",
  "stage": "dispatch_claimed|terminal_acknowledged",
  "delivery_id": "...",
  "acknowledgement_id": null,
  "claim_outcome": "claimed",
  "terminal_outcome": null,
  "inference_performed": false,
  "contrib_id": null,
  "contribution_receipt_sha256": null,
  "original_request_id": null,
  "failure_stage": null,
  "failure_detail_sha256": null
}
```

`dispatch_claimed` is evidence that one live, role-bound process generation claimed the delivery.
It is not a Redis acknowledgement and does not authorize removal from pending state. It is emitted
only after validating the exact seat, role, process generation, request revision, parent frontier,
endpoint, and model identity.

`terminal_acknowledged` is emitted only after either the authoritative Neo4j contribution commits
or a closed failure outcome is recorded. Only then may the delivery leave Redis pending state. On
success it must carry the exact `contrib_id` and `contribution_receipt_sha256` verified from
Neo4j.

If graph commit succeeds but acknowledgement delivery fails, Neo4j remains authoritative. Recovery
reads the contribution by deterministic request ID and re-emits the same acknowledgement. It keeps
the original contribution receipt and inference-process generation unchanged while the transport
receipt's `emitter.process_generation` identifies the recovery process. It never performs inference
again. The graph outcome remains `contributed`; the transport acknowledgement records
`duplicate_dispatch`, points at the original request, and returns the original terminal receipt.

### Closed outcomes and wave advancement

Claim-time refusal outcomes are:

```text
dead_seat
duplicate_dispatch
terminal_identity_skipped
stale_version
generation_mismatch
model_identity_unproven
unknown_role
closed_session
closed_wave
```

Per-request terminal outcomes are:

```text
contributed
terminal_identity_skipped
stale_version
dead_seat
timeout
frontier_mismatch
identity_conflict
inference_failed
validation_failed
graph_commit_failed
cancelled
superseded
generation_mismatch
model_identity_unproven
```

Every refusal or failure records `inference_performed` truthfully. No failure authorizes alias
substitution, endpoint selection, process takeover, frontier reduction, blind retry, or a second
inference under the same request ID.

Only the graph `pending -> claimed` transition authorizes inference. A pending slot may record only
`inference_performed: false`; any post-inference outcome requires both a prior graph claim and
`inference_performed: true`. Lost acknowledgement recovery preserves the original claim and outcome
rather than inventing a new authorization.

A normal wave advances only when every required role slot is `contributed`. Each missing or failed
slot remains explicit and makes the wave `incomplete_round`. A new user prompt revision may instead
close the prior wave as `superseded_revision`; every unfinished slot then receives an explicit
`superseded` outcome and the next independent wave starts at the higher prompt revision without
inheriting stale parents. Required membership cannot shrink or remap between waves. A terminal
historical request produces `terminal_identity_skipped`, performs no inference, and remains tied to
its original terminal session and preservation digest.

### Concern clearance and final publication

A `resolution` names exactly one `concern` through the existing `about` field and preserves the public
DCM disposition rules. `FIX-VERIFIED` and `FALSE-POSITIVE` require a non-empty evidence reference,
bound in the receipt as both `evidence_ref` and `evidence_ref_sha256`.

Before final publication, the graph recomputes `open_concerns(session_id)` and produces this
clearance projection from the complete contribution frontier:

```json
{
  "open_blocking_concern_ids": [],
  "closed_concerns": [
    {
      "concern_id": "contrib_...",
      "resolution_id": "contrib_...",
      "disposition": "FIX-VERIFIED",
      "evidence_ref": "...",
      "evidence_ref_sha256": "sha256:..."
    }
  ],
  "clearance_frontier_sha256": "sha256:..."
}
```

The session stores the canonical projection JSON and `clearance_projection_sha256` in the same
transaction as the final. The projection's `clearance_frontier_sha256` binds the complete latest
prompt-revision contribution frontier; the projection digest binds the open and closed concern
records shown above.

Warnings remain visible and are not relabelled as resolved. `publish_final()` must atomically bind
the synthesis digest, complete contribution frontier, and clearance digest while transitioning one
open session to closed. A closed session, closed wave, or superseded revision cannot accept another
contribution or final.

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

The public wave contract already prevents a linear contribution from entering a wave session,
prevents a wave from entering a linear session, and rejects every post-final mutation. Those are
contract validations, not evidence that the Presence adapter or a Taey inference round is live.

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
