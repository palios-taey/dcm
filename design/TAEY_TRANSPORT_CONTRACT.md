# Taey-native DCM transport contract

Status: design contract; the synchronous reference adapter does not yet satisfy this contract.

## Purpose

Taey-native mode exists to make one executive model both faster and deeper. Seven
differently prompted supporting instances investigate the same request concurrently, build
on one another through DCM, and return a provenance-rich deliberation for the executive to
synthesize.

This mode is not seven isolated model calls followed by concatenation. It is one DCM session
with a shared causal history, amendments, revisions, and an observable lifecycle.

The existing CLI-backed council remains a separate supported mode. Taey-native mode changes
the participant transport, not the DCM provenance standard.

## Participant topology

One council contains:

- **Main / executive** — owns the user conversation, opens the DCM session, follows progress,
  decides whether another revision is needed, and synthesizes the final answer.
- **context-memory** — finds relevant prior decisions, user context, constraints, and
  continuity risks.
- **evidence-reality** — separates Observed, Inferred, and Unknown claims; checks evidence and
  provenance.
- **systems-dependencies** — maps components, interfaces, ordering constraints, and downstream
  effects.
- **adversarial-failure** — attacks the proposal for silent failure, regressions, unsafe
  assumptions, and abuse cases.
- **scope-intent** — protects the user's actual goal, exclusions, success criteria, and
  authorization boundary.
- **options-alternatives** — develops materially different approaches and makes trade-offs
  explicit.
- **control-acceptance** — defines production observations, rollback conditions, and evidence
  required to call the work complete.

The role names and system prompts are stable application assets. Endpoint locations, model
paths, and hardware names are runtime configuration and must not be embedded in DCM.

## Required deliberation lifecycle

### 1. Open

The executive writes the user request, applicable context, council policy, and a monotonically
increasing request revision into one DCM session.

### 2. Dispatch a concurrent first wave

All seven supporting seats start against the same immutable round frontier. The controller
must demonstrate overlapping inference; dispatching the seats serially defeats the purpose of
this mode.

The current `taey_adapter.taey_expert` is not this controller. It performs a blocking request
and retries the entire inference after a stale CAS failure.

### 3. Commit a wave without serial re-inference

Sibling contributions in the same round share a causal parent. They must be able to land
without forcing every slower sibling to repeat inference merely because another sibling
committed first. A Taey-native implementation therefore needs a wave-aware append contract,
such as immutable round membership plus uniqueness on
`(session, round, role, request_revision)`.

The next round cannot open until the required prior-round seats are terminal or explicitly
recorded as missing. Every next-round participant reads the completed prior-round frontier.
This preserves read-before-write across rounds while retaining concurrency within a round.

### 4. Deliberate, do not just report

At least one revision wave must let seats engage peer contributions when the task requires
cross-checking. Each revised contribution records:

- the contribution IDs it read;
- an explicit Agree, Disagree, or Extend stance where it engages a peer;
- what changed from its prior contribution;
- claims and grounds;
- unresolved uncertainty or dissent.

The executive may stop after the first wave only when policy says independent aggregation is
appropriate and the ledger labels that choice. It must not describe independent first-wave
outputs as real-time deliberation.

### 5. Synthesize

The executive reads the terminal session state and produces one answer that preserves
material dissent and Unknowns. The final artifact records the contribution IDs and request
revision it synthesized.

## What “real time” means

Neo4j makes committed events visible immediately to the controller, executive, UI, and later
inference calls. It cannot inject a new peer message into a transformer request that is already
running.

Accordingly:

- partial progress may stream to the UI as non-authoritative run events;
- committed contributions become authoritative graph events;
- peer incorporation is guaranteed at a round boundary; and
- a user amendment during inference requires cancellation and redispatch, not wishful
  mid-request prompt mutation.

The UI may project this event stream, but the graph is the source of truth. A JSONL file,
process-local dictionary, or seven unrelated HTTP responses cannot serve as the council
ledger.

## Amendments and cancellation

Every user amendment is persisted before dispatch and increments the request revision.
For each affected in-flight seat, the controller must:

1. mark the old run superseded;
2. close or cancel its HTTP request;
3. confirm the serving layer released the request or record a timeout/failure;
4. reread the new graph frontier; and
5. dispatch a replacement tied to the new revision.

An output from a superseded revision may remain in the audit history but cannot enter the
active synthesis. DCM's existing contribution CAS rejects a stale commit; it does not cancel
the compute that produced it.

## Serving boundary

DCM addresses a configured OpenAI-compatible endpoint and stable model alias. A new trained
checkpoint is promoted behind that alias; council code, role identity, UI routes, and graph
schema do not change for each model release.

**A council participant must never address the executive's own proxy.** The executive can
hold a request open while it waits for the council. Routing a supporting seat back through
that proxy creates a dependency cycle: the seat waits on the executive request that is waiting
on the seat. This can look like a quiet service hang rather than a connection error. Supporting
seats use a dedicated participant endpoint that reaches the intended inference backend without
entering the executive's request path.

Participant requests need unique run IDs, cancellation-capable clients, bounded timeouts, and
observable terminal states. A controller must not silently fall back to a different endpoint,
model, role, or ledger.

### Target deployment topology and capacity

The target application topology assigns the executive plus seven local deliberation peers to
one primary, wired inference node. A separate inference node is reserved for up to eight remote
task executors after Main delegates concrete work. Moving the deliberation seats to the task
executor node changes the ratified topology and requires an explicit architecture revision.

The primary node must demonstrate at least eight overlapping sequence slots for one executive
plus seven supporting requests. Configured environment values or unit templates are not proof
of the effective runtime ceiling; acceptance reads the launched server arguments and observes
the production scheduler under an eight-request workload. If the capacity gate fails, startup
fails loudly rather than silently serializing the council. An amendment replacement starts only
after its superseded request releases a slot, so amendment handling does not assume a ninth
slot.

## Observable states

At minimum, each seat run exposes:

- session ID, round, role, and request revision;
- queued, active, contributed, superseded, cancelled, failed, or missing state;
- start and terminal timestamps;
- endpoint/model identity as deployment metadata;
- contribution ID when committed;
- failure or degradation reason; and
- peer contribution IDs read.

The user-facing projection shows Main separately from the seven supporting seats and streams
relevant progress without presenting hidden reasoning tokens as authoritative evidence.

## Acceptance evidence

Taey-native mode is complete only after a production run demonstrates all of the following:

1. one user request creates one DCM session;
2. all seven supporting roles are present with their stable prompts;
3. serving telemetry shows overlapping inference for the first wave;
4. graph and UI events agree on seat state and contribution identity;
5. a revision wave reads and responds to prior peer contributions;
6. a user amendment supersedes old runs, releases their serving slots, and excludes stale
   outputs from synthesis;
7. Main produces one provenance-linked synthesis with dissent and Unknowns preserved;
8. a missing or failed seat is labeled rather than silently replaced;
9. replacing the model behind the stable alias requires no council or UI code change; and
10. the existing CLI-backed council still runs unchanged.

Until those observations exist, the honest state is “contract specified, transport not yet
validated,” not “DCM is operational for Taey-native concurrent deliberation.”
