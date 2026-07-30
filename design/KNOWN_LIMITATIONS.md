# DCM — Known Limitations

Current implementation sweep: 2026-07-30. This ledger covers the supported runtime surface, not
historical consultations, evaluation harnesses, or advisory orchestration experiments. Those remain
in version-control history as research provenance.

Truth register:

- **Observed:** directly verified in current source or live execution.
- **Inferred:** a likely consequence of observed behavior.
- **Unknown:** not established by current evidence.

## 1. Acting CLI experts are not host-sandboxed

Severity: CRITICAL

Observed:

- `cli_adapter.py` invokes acting CLIs with permissive execution flags.
- Peer contributions are included in prompts supplied to those CLIs.
- The instruction not to modify the host is prompt text, not an enforced filesystem or network jail.

Inferred:

- Attacker-controlled problem, artifact, or peer text could induce a host-side action.

Required closure:

- Run acting CLIs inside an enforced filesystem/network sandbox.
- Treat peer content as untrusted data.
- Until then, operate councils only on trusted content and trusted participants.

## 2. Evidence references are not mechanically validated

Severity: HIGH

Observed:

- `mesh.py` closes a concern when a resolution has an accepted disposition and a nonempty
  `evidence_ref`.
- `publish_final` checks the resolution shape, not whether the referenced artifact exists, matches
  the reviewed artifact, or proves the stated closure.

Inferred:

- A plausible but nonexistent or irrelevant reference can close a concern.

Required closure:

- Bind evidence to immutable artifact hashes and the specific concern.
- Require a separately verifiable production receipt before a blocking concern can close.

## 3. Cross-model decorrelation can degrade without blocking publish

Severity: HIGH

Observed:

- `council.py` reports `decorrelated`, `partial-decorrelation`, or `single-model`.
- `cli_adapter.py` can fall back to another installed CLI after provider failure.
- The current publish gate discloses degraded decorrelation but does not require a minimum.

Inferred:

- A council can publish with less independent model diversity than the operator expected.

Required closure:

- Add an explicit caller policy for minimum decorrelation.
- Fail closed, or return a distinct non-publishable result, when the requirement is not met.

## 4. Plan mode lacks a typed concern-resolution ledger

Severity: MEDIUM-HIGH

Observed:

- `council_plan` records proposals and a synthesis, then publishes.
- `council_review` has typed concerns and resolution; plan mode does not apply the same ledger.

Inferred:

- A polished plan can publish while important unknowns remain implicit.

Required closure:

- Turn missing facts and unresolved assumptions into typed blocking concerns.
- Require evidence closure before publishing an environment-dependent plan.

## 5. Citation and regression-risk gates are shallow

Severity: MEDIUM

Observed:

- `council.py` derives citation tokens from grounding text and searches for those tokens in the
  artifact.
- A token mention does not prove that the cited constraint was incorporated.
- `REGRESSION_RISK` lines are prompt context rather than independently closed gate items.

Inferred:

- An artifact can name a prior source while ignoring its substantive constraint.

Required closure:

- Replace token scraping with structured grounding records.
- Require a cite, supersede, or not-applicable disposition for every grounding and regression item.

## 6. CAS retries can repeat expensive inference

Severity: MEDIUM

Observed:

- `cli_adapter.py` re-runs an acting CLI after a stale compare-and-set commit.
- The model output is produced before the stale write is detected.

Inferred:

- Concurrent slow seats can consume repeated provider calls and still exhaust their retry budget.

Required closure:

- Separate inference completion from ordered graph publication, or reserve an immutable wave
  frontier before inference.
- Preserve clear attempt and provider-failure receipts.

## 7. Rules-file completeness is an operator-controlled weakness

Severity: MEDIUM

Observed:

- Council experts see the supplied problem, rules, artifacts, and mesh contributions.
- They do not automatically know an operator's private schemas, endpoints, or deployment invariants.

Inferred:

- An incomplete rules file can yield a coherent answer grounded in the wrong environment.

Required closure:

- Define a required-input schema for environment-dependent work.
- Emit a missing-facts result instead of an actionable plan when critical facts are absent.

## 8. The served-model adapter is synchronous and cannot cancel amendments

Severity: HIGH

Observed:

- `taey_adapter.py` performs a blocking model request.
- A stale commit is discovered only after inference completes; retry repeats the request.
- The adapter has no run revision, immutable wave frontier, cancellation handle, or serving-slot
  release receipt.

Inferred:

- Several served-model seats can waste work on stale context.
- A user amendment cannot reliably supersede in-flight work.
- Seven successful responses do not by themselves prove DCM deliberation.

Required closure:

- Implement the lifecycle in `TAEY_TRANSPORT_CONTRACT.md`.
- Require each revision wave to read the completed prior frontier.
- Cancel and exclude superseded work, prove slot release, and project the same session state to the
  UI and Neo4j.
- Validate overlap, peer engagement, amendment cancellation, and graph/UI parity on the production
  serving path.

## Unknowns requiring production evidence

- Whether sandboxed acting-mode execution preserves enough capability for practical throughput.
- Which commands should require strict cross-model decorrelation.
- Whether evidence should be verified by command execution, immutable receipts, or a separate
  verifier.
- Whether concurrent served-model DCM improves result quality over one Taey instance.
