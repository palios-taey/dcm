"""DCM mesh — the substrate for fleet/Taey real-time cognitive coordination.

Design informed by lessons from a prior DCM implementation AND an open-mandate audit of the
first cut that defeated all three honesty mechanisms BY EXECUTION (the gate did not serialize,
verify_coordination was circular, peers_read overstated reading). This version is the
root-cause fix; the substrate below is re-verifiable by execution (see validate_substrate.py).

  * The old DCM died on ADOPTION: agents WROTE to the graph but never READ, then reported
    success while working in silos. We make that failure STRUCTURALLY HARD via a REAL
    compare-and-set (see contribute): a contribution claims slot `seq = read_version`, and a
    composite uniqueness constraint on (session_id, seq) lets EXACTLY ONE writer win that
    slot. Concurrent writers at the same version collide on the constraint and are rejected
    (StaleReadError) — they must re-read the slot the winner took and redo. This is the
    serialization the first cut's `count(c) WHERE cur=$rv` could NOT provide: Neo4j
    read-committed takes no lock on a count, so every concurrent same-version writer passed
    it (audit Finding A: 6 threads at v1 all committed). The constraint index lock is a true
    CAS — verified under the same 6-thread race (exactly one commit per version).

  * HONEST integrity model (audit Findings B/C — cannot-lie):
      - The CAS gate proves FETCH-before-commit: you can only commit at the version you read,
        so every prior peer was present in your read bundle. Unfakeable (structural).
      - `peers_present` (server-derived) records who was PRESENT at commit. After the CAS
        holds this equals the set delivered to your read. It is PRESENCE, NOT proof you
        semantically incorporated anyone — do not overclaim it as "truly read".
      - `claimed_peers` is YOUR explicit assertion of what you read+incorporated (self-report).
      - `verify_coordination` flags any contribution whose `claimed_peers` omits earlier
        present peers — i.e. an agent that did not even CLAIM to read what was in front of it.
        It catches honest siloing (the audit's claimed=[] case); it cannot detect a malicious
        agent that lies by claiming reads it didn't do. Semantic incorporation is unprovable
        from a graph; we prove fetch + record the claim. Three-register honest.

  * Own graph namespace (:DCMSession / :DCMContribution) on the fleet Neo4j — isolated from
    the orchestrator's graph and NOT in ISMA (the "sausage" stays out; only distilled finals
    flow there via publish_final()).
  * AI-speed / AI-native: one-read context bundle (read_session) is the primitive — an
    instance gets the topic + all peer work (+ any published final) in one call.

Auth: loopback Neo4j may run auth-disabled; a NON-loopback URI without auth is REFUSED
(fail-closed — a no-auth bolt port beyond localhost exposes a full RW graph). Set
DCM_NEO4J_USER/DCM_NEO4J_PASSWORD, or DCM_ALLOW_INSECURE=1 to override deliberately.
"""

from __future__ import annotations
import hashlib
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse
from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError

DCM_NEO4J_URI = os.environ.get("DCM_NEO4J_URI", "bolt://localhost:7687")
DCM_NEO4J_DATABASE = os.environ.get("DCM_NEO4J_DATABASE", "neo4j")
_USER = os.environ.get("DCM_NEO4J_USER")
_PASSWORD = os.environ.get("DCM_NEO4J_PASSWORD")
_AUTH = (_USER, _PASSWORD) if _USER and _PASSWORD else None
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]", "", None}
_driver = None

_PLAIN_CONTRIBUTION_KINDS = {"contribution", "plan_proposal", "consensus_plan"}
_CONTRIBUTION_KINDS = _PLAIN_CONTRIBUTION_KINDS | {"concern", "resolution"}
_CONCERN_SEVERITIES = {"block", "warn"}
_RESOLUTION_DISPOSITIONS = {
    "FIX-VERIFIED",
    "FALSE-POSITIVE",
    "OUT-OF-SCOPE",
    "ACCEPTED-RISK",
    "ESCALATE",
}
_CLOSING_DISPOSITIONS = {
    "FIX-VERIFIED",
    "FALSE-POSITIVE",
    "OUT-OF-SCOPE",
    "ACCEPTED-RISK",
}
_EVIDENCE_REQUIRED_DISPOSITIONS = {"FIX-VERIFIED", "FALSE-POSITIVE"}
_VETO_CLOSING_DISPOSITIONS = {"FIX-VERIFIED", "ACCEPTED-RISK"}
_WAVE_TERMINAL_STATES = {"contributed", "failed", "missing", "cancelled", "superseded"}
_WAVE_FAILURE_OUTCOMES = {
    "terminal_identity_skipped",
    "stale_version",
    "dead_seat",
    "timeout",
    "frontier_mismatch",
    "identity_conflict",
    "inference_failed",
    "validation_failed",
    "graph_commit_failed",
    "cancelled",
    "superseded",
    "generation_mismatch",
    "model_identity_unproven",
}
_REQUEST_CONTRACT_V2 = "taey-native-dcm-request/v2"
_RECEIPT_CONTRACT_V2 = "taey-native-dcm-receipt/v2"
_PRESENCE_ROUND_SESSION_ID_RE = re.compile(
    r"^dcm-[0-9]{8}T(?:[01][0-9]|2[0-3])(?:[0-5][0-9]){2}Z-[0-9a-f]{12}$"
)
_REQUEST_IDENTITY_FIELDS = {
    "session_id",
    "wave_id",
    "round",
    "phase",
    "prompt_id",
    "prompt_revision",
    "prompt_sha256",
    "seat_id",
    "role",
    "request_revision",
    "parent_frontier_sha256",
    "process_generation_expected",
    "model_endpoint",
    "requested_alias",
    "model_manifest_sha256",
    "model_content_sha256",
    "serving_container_digest",
}
_REQUEST_IDENTITY_V2_FIELDS = _REQUEST_IDENTITY_FIELDS | {
    "request_contract",
    "prompt_contract_sha256",
    "model_identity_receipt_sha256",
}
_OBSERVED_EXECUTION_FIELDS = {
    "process_generation_observed",
    "model_endpoint",
    "served_alias",
    "model_manifest_sha256",
    "model_content_sha256",
    "serving_container_digest",
}
_CLAIM_OBSERVATION_FIELDS = _OBSERVED_EXECUTION_FIELDS | {"seat_id"}
_CLAIM_OBSERVATION_V2_FIELDS = _CLAIM_OBSERVATION_FIELDS | {
    "prompt_contract_sha256",
    "model_identity_receipt_sha256",
}
_EMITTER_FIELDS = {"component", "process_generation"}
_driver_init_lock = threading.Lock()
_SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT dcm_session_id IF NOT EXISTS "
    "FOR (x:DCMSession) REQUIRE x.session_id IS UNIQUE",
    "CREATE CONSTRAINT dcm_contrib_id IF NOT EXISTS "
    "FOR (c:DCMContribution) REQUIRE c.contrib_id IS UNIQUE",
    "CREATE CONSTRAINT dcm_contrib_slot IF NOT EXISTS "
    "FOR (c:DCMContribution) REQUIRE (c.session_id, c.seq) IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_id IF NOT EXISTS "
    "FOR (w:DCMWave) REQUIRE w.wave_id IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_logical_identity IF NOT EXISTS "
    "FOR (w:DCMWave) REQUIRE "
    "(w.session_id, w.round, w.phase, w.prompt_revision, w.request_revision) IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_slot_identity IF NOT EXISTS "
    "FOR (z:DCMWaveSlot) REQUIRE "
    "(z.session_id, z.wave_id, z.role, z.request_revision) IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_slot_request_id IF NOT EXISTS "
    "FOR (z:DCMWaveSlot) REQUIRE z.request_id IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_slot_seat IF NOT EXISTS "
    "FOR (z:DCMWaveSlot) REQUIRE "
    "(z.session_id, z.wave_id, z.seat_id, z.request_revision) IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_contrib_request_id IF NOT EXISTS "
    "FOR (c:DCMContribution) REQUIRE c.request_id IS UNIQUE",
    "CREATE CONSTRAINT dcm_wave_contrib_slot IF NOT EXISTS "
    "FOR (c:DCMContribution) REQUIRE "
    "(c.session_id, c.wave_id, c.role, c.request_revision) IS UNIQUE",
)


class StaleReadError(Exception):
    """Raised when a contribution loses the compare-and-set for its slot — a peer took the
    version you were writing into, so committing would ignore them. The adoption contract,
    enforced structurally: re-read (read_session) and redo your turn on the fresh state.
    """

    def __init__(
        self, current_version: int, your_version: int, new_peer_ids: list[str]
    ):
        self.current_version = current_version
        self.your_version = your_version
        self.new_peer_ids = new_peer_ids
        super().__init__(
            f"stale read: session at v{current_version}, you read v{your_version}; "
            f"{len(new_peer_ids)} new peer(s) arrived — re-read and incorporate them"
        )


class UnresolvedConcernsError(Exception):
    """Raised when publish_final would close over unresolved block-severity concerns."""

    def __init__(self, open_concern_ids: list[str]):
        self.open_concern_ids = open_concern_ids
        super().__init__(
            "cannot publish final; unresolved block concern(s): "
            f"{', '.join(open_concern_ids)}"
        )


class WaveStateError(Exception):
    """Raised when an additive wave operation is structurally unavailable."""

    def __init__(self, outcome: str, detail: str):
        self.outcome = outcome
        self.detail = detail
        super().__init__(f"{outcome}: {detail}")


class WaveIdentityConflictError(WaveStateError):
    """Raised when one immutable wave/request identity is reused with different content."""

    def __init__(self, detail: str):
        super().__init__("identity_conflict", detail)


class WaveFrontierMismatchError(WaveStateError):
    """Raised when a request does not carry its wave's exact immutable parent frontier."""

    def __init__(self, detail: str):
        super().__init__("frontier_mismatch", detail)


class _IdempotentWaveReplay(Exception):
    def __init__(self, result: dict):
        self.result = result
        super().__init__("idempotent wave replay")


def _require_safe_uri(uri: str) -> None:
    host = urlparse(uri).hostname
    if (
        host not in _LOOPBACK
        and _AUTH is None
        and os.environ.get("DCM_ALLOW_INSECURE") != "1"
    ):
        raise RuntimeError(
            f"refusing to connect to non-loopback Neo4j host {host!r} with no auth — a no-auth "
            f"bolt port beyond loopback exposes a full read/write graph (read all sessions, "
            f"forge/delete contributions, flip status/final). Set DCM_NEO4J_USER/DCM_NEO4J_PASSWORD, "
            f"or DCM_ALLOW_INSECURE=1 to override deliberately."
        )


def _db():
    global _driver
    if _driver is not None:
        return _driver
    with _driver_init_lock:
        if _driver is not None:
            return _driver
        _require_safe_uri(DCM_NEO4J_URI)
        candidate = GraphDatabase.driver(DCM_NEO4J_URI, auth=_AUTH)
        try:
            candidate.verify_connectivity()
            with candidate.session(database=DCM_NEO4J_DATABASE) as session:
                for statement in _SCHEMA_STATEMENTS:
                    session.execute_write(
                        lambda tx, query=statement: tx.run(query).consume()
                    )
        except Exception:
            candidate.close()
            raise
        _driver = candidate
    return _driver


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_sha256(value) -> str:
    return (
        "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    )


def _text_sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _require_positive_int(value, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive int")
    return value


def _canonical_text_set(values, field: str, *, allow_empty: bool) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field} must be a list")
    cleaned = [_require_text(value, field) for value in values]
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{field} cannot contain duplicates")
    if not allow_empty and not cleaned:
        raise ValueError(f"{field} cannot be empty")
    return sorted(cleaned)


def _require_sha256(value, field: str) -> str:
    value = _require_text(value, field)
    if len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError(f"{field} must be sha256:<64 lowercase hex>")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be sha256:<64 lowercase hex>") from exc
    if value[7:] != value[7:].lower():
        raise ValueError(f"{field} must be sha256:<64 lowercase hex>")
    return value


def _validated_presence_round_session_id(value) -> str:
    if (
        not isinstance(value, str)
        or _PRESENCE_ROUND_SESSION_ID_RE.fullmatch(value) is None
    ):
        raise ValueError(
            "session_id must match the Presence round identity "
            "dcm-YYYYMMDDTHHMMSSZ-<12 lowercase hex>"
        )
    try:
        time.strptime(value[4:20], "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise ValueError(
            "session_id must contain a valid UTC timestamp in the Presence round identity"
        ) from exc
    return value


def _ensure_wave_schema():
    return _db()


def _validated_request_contract(request_contract: str | None) -> str | None:
    if request_contract is None:
        return None
    if request_contract != _REQUEST_CONTRACT_V2:
        raise ValueError(
            f"request_contract must be {_REQUEST_CONTRACT_V2!r} when provided"
        )
    return request_contract


def canonical_wave_request_id(request_identity: dict) -> str:
    """Return the normative request identity digest after validating the frozen field set."""
    if not isinstance(request_identity, dict):
        raise ValueError("request_identity must be a dict")
    request_contract = _validated_request_contract(
        request_identity.get("request_contract")
    )
    expected_fields = (
        _REQUEST_IDENTITY_V2_FIELDS
        if request_contract == _REQUEST_CONTRACT_V2
        else _REQUEST_IDENTITY_FIELDS
    )
    fields = set(request_identity)
    if fields != expected_fields:
        missing = sorted(expected_fields - fields)
        extra = sorted(fields - expected_fields)
        raise ValueError(
            f"request_identity fields mismatch; missing={missing}, extra={extra}"
        )
    for field in (
        "session_id",
        "wave_id",
        "phase",
        "prompt_id",
        "seat_id",
        "role",
        "process_generation_expected",
        "model_endpoint",
        "requested_alias",
    ):
        _require_text(request_identity[field], field)
    for field in ("round", "prompt_revision", "request_revision"):
        _require_positive_int(request_identity[field], field)
    for field in (
        "prompt_sha256",
        "parent_frontier_sha256",
        "model_manifest_sha256",
        "model_content_sha256",
        "serving_container_digest",
    ):
        _require_sha256(request_identity[field], field)
    if request_contract == _REQUEST_CONTRACT_V2:
        for field in (
            "prompt_contract_sha256",
            "model_identity_receipt_sha256",
        ):
            _require_sha256(request_identity[field], field)
    return _canonical_sha256(request_identity)


def _validated_request_identity(*, wave: dict, slot: dict) -> dict:
    request_identity = slot.get("request_identity")
    if not isinstance(request_identity, dict):
        raise WaveIdentityConflictError("canonical request identity is missing")
    if (
        wave.get("graph_uri") != DCM_NEO4J_URI
        or wave.get("graph_database") != DCM_NEO4J_DATABASE
    ):
        raise WaveIdentityConflictError(
            "wave graph identity differs from the active DCM target"
        )
    graph_fields = {
        "session_id": wave["session_id"],
        "wave_id": wave["wave_id"],
        "round": wave["round"],
        "phase": wave["phase"],
        "prompt_id": wave["prompt_id"],
        "prompt_revision": wave["prompt_revision"],
        "prompt_sha256": wave["prompt_sha256"],
        "seat_id": slot["seat_id"],
        "role": slot["role"],
        "request_revision": slot["request_revision"],
        "parent_frontier_sha256": wave["parent_frontier_sha256"],
        "request_contract": wave.get("request_contract"),
    }
    if wave.get("request_contract") == _REQUEST_CONTRACT_V2:
        graph_fields.update(
            {
                "prompt_contract_sha256": slot.get("prompt_contract_sha256"),
                "model_identity_receipt_sha256": slot.get(
                    "model_identity_receipt_sha256"
                ),
            }
        )
    mismatched = [
        field
        for field, expected in graph_fields.items()
        if request_identity.get(field) != expected
    ]
    if mismatched:
        raise WaveIdentityConflictError(
            f"request identity differs from graph-bound fields: {sorted(mismatched)}"
        )
    if canonical_wave_request_id(request_identity) != slot.get("request_id"):
        raise WaveIdentityConflictError(
            "request ID is not the canonical digest of the frozen request identity"
        )
    parents = _canonical_text_set(
        list(wave.get("parent_contribution_ids") or []),
        "wave.parent_contribution_ids",
        allow_empty=True,
    )
    if _canonical_sha256(parents) != wave.get("parent_frontier_sha256"):
        raise WaveFrontierMismatchError(
            "wave parent frontier digest differs from its frozen parent IDs"
        )
    edge_ids = wave.get("parent_edge_contribution_ids")
    if edge_ids is not None and parents != list(edge_ids):
        raise WaveFrontierMismatchError(
            "wave parent properties differ from graph relationships"
        )
    return request_identity


def canonical_prompt_sha256(
    messages: list, attachment_evidence_digests: list[str]
) -> str:
    """Hash the ordered prompt messages and ordered attachment/evidence content digests."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty ordered list")
    if not isinstance(attachment_evidence_digests, list):
        raise ValueError("attachment_evidence_digests must be an ordered list")
    digests = [
        _require_sha256(value, "attachment_evidence_digests")
        for value in attachment_evidence_digests
    ]
    return _canonical_sha256(
        {
            "messages": messages,
            "attachment_evidence_content_digests": digests,
        }
    )


def _canonical_members(
    required_members: list[dict], *, request_contract: str | None = None
) -> list[dict]:
    request_contract = _validated_request_contract(request_contract)
    if not isinstance(required_members, list) or not required_members:
        raise ValueError("required_members must be a non-empty list")
    required_fields = {"seat_id", "role"}
    if request_contract == _REQUEST_CONTRACT_V2:
        required_fields |= {
            "prompt_contract_sha256",
            "model_identity_receipt_sha256",
        }
    members = []
    for member in required_members:
        if not isinstance(member, dict) or set(member) != required_fields:
            if request_contract is None:
                raise ValueError(
                    "each required member must contain exactly seat_id and role"
                )
            raise ValueError(
                f"each required member must contain exactly {sorted(required_fields)}"
            )
        normalized = {
            "seat_id": _require_text(member["seat_id"], "seat_id"),
            "role": _require_text(member["role"], "role"),
        }
        if request_contract == _REQUEST_CONTRACT_V2:
            normalized.update(
                {
                    field: _require_sha256(member[field], field)
                    for field in (
                        "prompt_contract_sha256",
                        "model_identity_receipt_sha256",
                    )
                }
            )
        members.append(normalized)
    if len({member["seat_id"] for member in members}) != len(members):
        raise ValueError("required_members cannot repeat a seat_id")
    if len({member["role"] for member in members}) != len(members):
        raise ValueError("required_members cannot repeat a role")
    return sorted(members, key=lambda member: member["role"])


def _slot_member(slot: dict, request_contract: str | None) -> dict:
    member = {"seat_id": slot["seat_id"], "role": slot["role"]}
    if request_contract == _REQUEST_CONTRACT_V2:
        member.update(
            {
                "prompt_contract_sha256": slot.get("prompt_contract_sha256"),
                "model_identity_receipt_sha256": slot.get(
                    "model_identity_receipt_sha256"
                ),
            }
        )
    return member


def _validated_claim_observation(
    claim_observation: dict, request_identity: dict
) -> dict:
    expected_fields = (
        _CLAIM_OBSERVATION_V2_FIELDS
        if request_identity.get("request_contract") == _REQUEST_CONTRACT_V2
        else _CLAIM_OBSERVATION_FIELDS
    )
    if (
        not isinstance(claim_observation, dict)
        or set(claim_observation) != expected_fields
    ):
        raise WaveStateError(
            "model_identity_unproven",
            f"claim_observation fields must be exactly {sorted(expected_fields)}",
        )
    if claim_observation["seat_id"] != request_identity["seat_id"]:
        raise WaveIdentityConflictError(
            "observed seat differs from the reserved role owner"
        )
    observed = {field: claim_observation[field] for field in _OBSERVED_EXECUTION_FIELDS}
    _validated_observed_execution(observed, request_identity)
    if request_identity.get("request_contract") == _REQUEST_CONTRACT_V2:
        digest_fields = (
            "prompt_contract_sha256",
            "model_identity_receipt_sha256",
        )
        try:
            for field in digest_fields:
                _require_sha256(claim_observation[field], field)
        except ValueError as exc:
            raise WaveStateError("model_identity_unproven", str(exc)) from exc
        mismatched = [
            field
            for field in digest_fields
            if claim_observation[field] != request_identity[field]
        ]
        if mismatched:
            raise WaveStateError(
                "model_identity_unproven",
                f"observed contract identity differs from frozen request: {mismatched}",
            )
    return dict(claim_observation)


def _validated_emitter(emitter: dict, observed_execution: dict) -> dict:
    if not isinstance(emitter, dict) or set(emitter) != _EMITTER_FIELDS:
        raise WaveStateError(
            "model_identity_unproven",
            f"emitter fields must be exactly {sorted(_EMITTER_FIELDS)}",
        )
    try:
        component = _require_text(emitter["component"], "emitter.component")
        process_generation = _require_text(
            emitter["process_generation"], "emitter.process_generation"
        )
    except ValueError as exc:
        raise WaveStateError("model_identity_unproven", str(exc)) from exc
    if component not in {"native-coordinator", "taey-council-seat", "dcm-adapter"}:
        raise WaveStateError(
            "model_identity_unproven",
            "emitter.component is not a supported receipt emitter",
        )
    if (
        component == "taey-council-seat"
        and process_generation != observed_execution["process_generation_observed"]
    ):
        raise WaveStateError(
            "generation_mismatch",
            "seat emitter generation differs from observed inference generation",
        )
    return {"component": component, "process_generation": process_generation}


def _build_contribution_receipt(
    *,
    wave: dict,
    slot: dict,
    request_identity: dict,
    emitter: dict,
    observed_execution: dict,
    contrib_id: str,
    typed_props: dict,
    content_sha256: str,
    claimed_peers: list[str],
) -> dict:
    evidence_ref = typed_props.get("evidence_ref")
    receipt = {
        "contract": "taey-native-dcm-receipt/v1",
        "receipt_kind": "contribution",
        "session_id": wave["session_id"],
        "correlation_id": wave["session_id"],
        "wave_id": wave["wave_id"],
        "round": wave["round"],
        "phase": wave["phase"],
        "prompt": {
            "prompt_id": wave["prompt_id"],
            "revision": wave["prompt_revision"],
            "sha256": wave["prompt_sha256"],
        },
        "seat_id": slot["seat_id"],
        "role": slot["role"],
        "request_revision": slot["request_revision"],
        "request_id": slot["request_id"],
        "emitter": emitter,
        "graph": {"uri": wave["graph_uri"], "database": wave["graph_database"]},
        "frontier": {
            "parent_contribution_ids": list(wave["parent_contribution_ids"]),
            "parent_frontier_sha256": wave["parent_frontier_sha256"],
            "claimed_peers": claimed_peers,
            "peers_present": list(wave["parent_contribution_ids"]),
        },
        "execution": {
            "model_endpoint": observed_execution["model_endpoint"],
            "process_generation_expected": request_identity[
                "process_generation_expected"
            ],
            "process_generation_observed": observed_execution[
                "process_generation_observed"
            ],
            "requested_alias": request_identity["requested_alias"],
            "served_alias": observed_execution["served_alias"],
            "model_manifest_sha256": observed_execution["model_manifest_sha256"],
            "model_content_sha256": observed_execution["model_content_sha256"],
            "serving_container_digest": observed_execution["serving_container_digest"],
        },
        "contribution": {
            "contrib_id": contrib_id,
            "kind": typed_props["kind"],
            "content_sha256": content_sha256,
            "about": typed_props.get("about"),
            "severity": typed_props.get("severity"),
            "veto": bool(typed_props.get("veto")),
            "disposition": typed_props.get("disposition"),
            "evidence_ref": evidence_ref,
            "evidence_ref_sha256": _text_sha256(evidence_ref) if evidence_ref else None,
        },
        "terminal_outcome": "contributed",
    }
    if request_identity.get("request_contract") == _REQUEST_CONTRACT_V2:
        receipt.update(
            {
                "contract": _RECEIPT_CONTRACT_V2,
                "request_contract": _REQUEST_CONTRACT_V2,
                "prompt_contract_sha256": request_identity[
                    "prompt_contract_sha256"
                ],
                "model_identity_receipt_sha256": request_identity[
                    "model_identity_receipt_sha256"
                ],
            }
        )
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def _validated_contribution_receipt(
    *, wave: dict, slot: dict, contribution: dict
) -> dict:
    receipt = contribution.get("contribution_receipt")
    request_identity = _validated_request_identity(wave=wave, slot=slot)
    observed_execution = contribution.get("observed_execution")
    if not isinstance(receipt, dict):
        raise WaveIdentityConflictError("canonical contribution receipt is missing")
    try:
        observed_execution = _validated_observed_execution(
            observed_execution, request_identity
        )
        _validated_claim_observation(slot.get("claim_observation"), request_identity)
    except WaveStateError as exc:
        raise WaveIdentityConflictError(exc.detail) from exc
    emitter = receipt.get("emitter")
    validated_emitter = _validated_emitter(emitter, observed_execution)
    structured_content = contribution.get("structured_content")
    if not isinstance(structured_content, dict):
        raise WaveIdentityConflictError("structured contribution content is missing")
    content_sha256 = _canonical_sha256(structured_content)
    if content_sha256 != contribution.get(
        "content_sha256"
    ) or content_sha256 != slot.get("content_sha256"):
        raise WaveIdentityConflictError(
            "contribution content digest differs from its graph-bound content"
        )
    expected_identity = {
        "session_id": wave["session_id"],
        "wave_id": wave["wave_id"],
        "round": wave["round"],
        "phase": wave["phase"],
        "role": slot["role"],
        "request_revision": slot["request_revision"],
        "request_id": slot["request_id"],
        "contrib_id": slot.get("contrib_id"),
        "parent_frontier_sha256": wave["parent_frontier_sha256"],
    }
    mismatched = [
        field
        for field, expected_value in expected_identity.items()
        if contribution.get(field) != expected_value
    ]
    if mismatched:
        raise WaveIdentityConflictError(
            f"contribution differs from graph-bound identity: {sorted(mismatched)}"
        )
    try:
        claimed_peers = _canonical_text_set(
            list(contribution.get("claimed_peers") or []),
            "claimed_peers",
            allow_empty=True,
        )
        typed_props = _typed_props(
            contribution.get("kind") or "contribution",
            contribution.get("severity"),
            contribution.get("about"),
            bool(contribution.get("veto")),
            contribution.get("disposition"),
            contribution.get("evidence_ref"),
        )
    except ValueError as exc:
        raise WaveIdentityConflictError(str(exc)) from exc
    parent_ids = list(wave.get("parent_contribution_ids") or [])
    edge_ids = list(wave.get("parent_edge_contribution_ids") or [])
    if (
        claimed_peers != parent_ids
        or claimed_peers != edge_ids
        or list(contribution.get("peers_present") or []) != parent_ids
        or _canonical_sha256(claimed_peers) != wave["parent_frontier_sha256"]
    ):
        raise WaveFrontierMismatchError(
            "contribution receipt is not bound to the relationship-derived frontier"
        )
    _validated_resolution_parent(wave, typed_props)
    fingerprint = _canonical_sha256(
        {
            "request_id": slot["request_id"],
            "structured_content": structured_content,
            "claimed_peers": claimed_peers,
            "observed_execution": observed_execution,
            "emitter": validated_emitter,
            "typed_props": typed_props,
        }
    )
    if fingerprint != contribution.get(
        "contribution_fingerprint"
    ) or fingerprint != slot.get("contribution_fingerprint"):
        raise WaveIdentityConflictError(
            "contribution fingerprint differs across graph-bound records"
        )
    expected = _build_contribution_receipt(
        wave=wave,
        slot=slot,
        request_identity=request_identity,
        emitter=validated_emitter,
        observed_execution=observed_execution,
        contrib_id=contribution["contrib_id"],
        typed_props=typed_props,
        content_sha256=content_sha256,
        claimed_peers=claimed_peers,
    )
    if receipt != expected:
        raise WaveIdentityConflictError(
            "canonical contribution receipt fields differ from graph-bound state"
        )
    if receipt["receipt_sha256"] != contribution.get(
        "contribution_receipt_sha256"
    ) or receipt["receipt_sha256"] != slot.get("contribution_receipt_sha256"):
        raise WaveIdentityConflictError(
            "canonical contribution receipt digest differs across graph records"
        )
    return receipt


def _validated_resolution_parent(wave: dict, typed_props: dict) -> None:
    if typed_props["kind"] != "resolution":
        return
    parents = wave.get("parent_edge_contributions")
    if not isinstance(parents, list):
        raise WaveIdentityConflictError(
            "relationship-derived parent contributions are unavailable"
        )
    matching = [
        parent
        for parent in parents
        if parent.get("contrib_id") == typed_props.get("about")
        and (parent.get("kind") or "contribution") == "concern"
    ]
    if len(matching) != 1:
        raise WaveFrontierMismatchError(
            "resolution about must name one concern in the exact parent frontier"
        )


def _build_outcome_record(
    *,
    wave: dict,
    slot: dict,
    terminal_outcome: str,
    inference_performed: bool,
    failure_stage: str | None,
    failure_detail_sha256: str | None,
    recorded_by: str,
) -> dict:
    record = {
        "contract": "taey-native-dcm-graph-outcome/v1",
        "session_id": wave["session_id"],
        "correlation_id": wave["session_id"],
        "wave_id": wave["wave_id"],
        "round": wave["round"],
        "phase": wave["phase"],
        "seat_id": slot["seat_id"],
        "role": slot["role"],
        "request_revision": slot["request_revision"],
        "request_id": slot.get("request_id"),
        "terminal_outcome": terminal_outcome,
        "inference_performed": inference_performed,
        "failure_stage": failure_stage,
        "failure_detail_sha256": failure_detail_sha256,
        "recorded_by": recorded_by,
    }
    record["outcome_record_sha256"] = _canonical_sha256(record)
    return record


def _validated_outcome_record(*, wave: dict, slot: dict) -> dict:
    record = slot.get("outcome_record")
    recorded_by = slot.get("outcome_recorded_by")
    if not isinstance(record, dict) or recorded_by not in {
        "record_wave_outcome",
        "close_wave",
    }:
        raise WaveIdentityConflictError("canonical terminal outcome record is missing")
    if slot.get("request_id") is not None or slot.get("request_identity") is not None:
        _validated_request_identity(wave=wave, slot=slot)
    inference_performed = slot.get("inference_performed")
    if not isinstance(inference_performed, bool):
        raise WaveIdentityConflictError("terminal inference truth is missing")
    claim_observation = slot.get("claim_observation")
    if inference_performed and claim_observation is None:
        raise WaveIdentityConflictError(
            "terminal outcome claims inference without a graph inference authorization"
        )
    if claim_observation is not None:
        request_identity = _validated_request_identity(wave=wave, slot=slot)
        try:
            _validated_claim_observation(claim_observation, request_identity)
        except WaveStateError as exc:
            raise WaveIdentityConflictError(exc.detail) from exc
    terminal_outcome = slot.get("terminal_outcome")
    pre_inference_outcomes = {"terminal_identity_skipped", "stale_version", "dead_seat"}
    post_inference_outcomes = {
        "inference_failed",
        "validation_failed",
        "graph_commit_failed",
    }
    if terminal_outcome in pre_inference_outcomes and inference_performed:
        raise WaveIdentityConflictError(
            f"{terminal_outcome} cannot claim inference_performed=true"
        )
    if terminal_outcome in post_inference_outcomes and not inference_performed:
        raise WaveIdentityConflictError(
            f"{terminal_outcome} requires inference_performed=true"
        )
    expected_state = (
        terminal_outcome
        if terminal_outcome in {"cancelled", "superseded"}
        else "missing"
        if terminal_outcome == "missing"
        else "failed"
    )
    if slot.get("state") != expected_state:
        raise WaveIdentityConflictError(
            "terminal outcome differs from the graph slot state"
        )
    expected = _build_outcome_record(
        wave=wave,
        slot=slot,
        terminal_outcome=terminal_outcome,
        inference_performed=inference_performed,
        failure_stage=slot.get("failure_stage"),
        failure_detail_sha256=slot.get("failure_detail_sha256"),
        recorded_by=recorded_by,
    )
    if record != expected or record["outcome_record_sha256"] != slot.get(
        "outcome_record_sha256"
    ):
        raise WaveIdentityConflictError(
            "terminal outcome record differs from graph-bound state"
        )
    fingerprint = _canonical_sha256(
        {
            "request_id": slot.get("request_id"),
            "state": slot["state"],
            "terminal_outcome": terminal_outcome,
            "inference_performed": inference_performed,
            "failure_stage": slot.get("failure_stage"),
            "failure_detail_sha256": slot.get("failure_detail_sha256"),
        }
    )
    if fingerprint != slot.get("outcome_fingerprint"):
        raise WaveIdentityConflictError(
            "terminal outcome fingerprint differs from graph-bound state"
        )
    return record


def _validated_observed_execution(
    observed_execution: dict, request_identity: dict
) -> dict:
    if not isinstance(observed_execution, dict):
        raise WaveStateError(
            "model_identity_unproven", "observed_execution must be a dict"
        )
    fields = set(observed_execution)
    if fields != _OBSERVED_EXECUTION_FIELDS:
        missing = sorted(_OBSERVED_EXECUTION_FIELDS - fields)
        extra = sorted(fields - _OBSERVED_EXECUTION_FIELDS)
        raise WaveStateError(
            "model_identity_unproven",
            f"observed_execution fields mismatch; missing={missing}, extra={extra}",
        )
    try:
        for field in (
            "process_generation_observed",
            "model_endpoint",
            "served_alias",
        ):
            _require_text(observed_execution[field], field)
        for field in (
            "model_manifest_sha256",
            "model_content_sha256",
            "serving_container_digest",
        ):
            _require_sha256(observed_execution[field], field)
    except ValueError as exc:
        raise WaveStateError("model_identity_unproven", str(exc)) from exc
    expected = {
        "process_generation_observed": request_identity["process_generation_expected"],
        "model_endpoint": request_identity["model_endpoint"],
        "served_alias": request_identity["requested_alias"],
        "model_manifest_sha256": request_identity["model_manifest_sha256"],
        "model_content_sha256": request_identity["model_content_sha256"],
        "serving_container_digest": request_identity["serving_container_digest"],
    }
    if (
        observed_execution["process_generation_observed"]
        != expected["process_generation_observed"]
    ):
        raise WaveStateError(
            "generation_mismatch",
            "observed process generation differs from the frozen request identity",
        )
    mismatched = [
        field
        for field, value in expected.items()
        if field != "process_generation_observed" and observed_execution[field] != value
    ]
    if mismatched:
        raise WaveStateError(
            "model_identity_unproven",
            f"observed model identity differs from frozen request: {sorted(mismatched)}",
        )
    return dict(observed_execution)


def _clean_optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must be non-empty when provided")
    return value


def _typed_props(
    kind: str,
    severity: str | None,
    about: str | None,
    veto: bool,
    disposition: str | None,
    evidence_ref: str | None,
) -> dict:
    if kind not in _CONTRIBUTION_KINDS:
        raise ValueError(f"kind must be one of {sorted(_CONTRIBUTION_KINDS)}")
    if not isinstance(veto, bool):
        raise ValueError("veto must be a bool")

    about = _clean_optional_text(about, "about")
    evidence_ref = _clean_optional_text(evidence_ref, "evidence_ref")
    props = {"kind": kind}

    if kind in _PLAIN_CONTRIBUTION_KINDS:
        if (
            severity is not None
            or about is not None
            or veto
            or disposition is not None
            or evidence_ref is not None
        ):
            raise ValueError(
                "plain contribution kinds cannot carry concern/resolution fields"
            )
        return props

    if kind == "concern":
        if severity not in _CONCERN_SEVERITIES:
            raise ValueError(
                f"concern severity must be one of {sorted(_CONCERN_SEVERITIES)}"
            )
        if disposition is not None or evidence_ref is not None:
            raise ValueError(
                "concerns cannot carry resolution disposition/evidence_ref"
            )
        props["severity"] = severity
        if about is not None:
            props["about"] = about
        if veto:
            props["veto"] = True
        return props

    if severity is not None or veto:
        raise ValueError("resolutions cannot carry severity or veto")
    if about is None:
        raise ValueError("resolution about must name the concern contrib_id")
    if disposition not in _RESOLUTION_DISPOSITIONS:
        raise ValueError(
            f"resolution disposition must be one of {sorted(_RESOLUTION_DISPOSITIONS)}"
        )
    if disposition in _EVIDENCE_REQUIRED_DISPOSITIONS and evidence_ref is None:
        raise ValueError(f"{disposition} resolution requires non-empty evidence_ref")
    props["about"] = about
    props["disposition"] = disposition
    if evidence_ref is not None:
        props["evidence_ref"] = evidence_ref
    return props


def _has_evidence(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _resolution_closes(concern: dict, resolution: dict) -> bool:
    if (
        resolution.get("kind") != "resolution"
        or resolution.get("about") != concern["contrib_id"]
    ):
        return False
    disposition = resolution.get("disposition")
    if disposition not in _CLOSING_DISPOSITIONS:
        return False
    if concern.get("veto") and disposition not in _VETO_CLOSING_DISPOSITIONS:
        return False
    if disposition in _EVIDENCE_REQUIRED_DISPOSITIONS and not _has_evidence(
        resolution.get("evidence_ref")
    ):
        return False
    return True


def _project_open_concerns(contributions: list[dict]) -> list[dict]:
    resolutions = [item for item in contributions if item.get("kind") == "resolution"]
    return [
        item
        for item in contributions
        if item.get("kind") == "concern"
        and item.get("severity") == "block"
        and not any(_resolution_closes(item, resolution) for resolution in resolutions)
    ]


def _build_clearance_projection(contributions: list[dict]) -> dict:
    ordered = sorted(contributions, key=lambda item: item["contrib_id"])
    resolutions = sorted(
        (item for item in ordered if item.get("kind") == "resolution"),
        key=lambda item: item["contrib_id"],
    )
    open_ids = sorted(item["contrib_id"] for item in _project_open_concerns(ordered))
    closed = []
    for concern in ordered:
        if concern.get("kind") != "concern" or concern.get("severity") != "block":
            continue
        matching = [
            resolution
            for resolution in resolutions
            if _resolution_closes(concern, resolution)
        ]
        if not matching:
            continue
        resolution = matching[0]
        evidence_ref = resolution.get("evidence_ref")
        closed.append(
            {
                "concern_id": concern["contrib_id"],
                "resolution_id": resolution["contrib_id"],
                "disposition": resolution["disposition"],
                "evidence_ref": evidence_ref,
                "evidence_ref_sha256": (
                    _text_sha256(evidence_ref) if evidence_ref else None
                ),
            }
        )
    frontier = [item["contrib_id"] for item in ordered]
    return {
        "open_blocking_concern_ids": open_ids,
        "closed_concerns": closed,
        "clearance_frontier_sha256": _canonical_sha256(frontier),
    }


def start_session(
    topic: str,
    payload: str,
    roles: list[str] | None = None,
    *,
    session_id: str | None = None,
) -> str:
    """Open a session, optionally reusing one exact fresh Presence round identity."""
    external_identity = session_id is not None
    sid = (
        _validated_presence_round_session_id(session_id)
        if external_identity
        else f"dcm_{uuid.uuid4().hex[:12]}"
    )
    try:
        with _db().session(database=DCM_NEO4J_DATABASE) as s:
            result = s.run(
                """CREATE (x:DCMSession {session_id:$sid, topic:$topic, payload:$payload,
                     roles:$roles, status:'open', created:$ts})""",
                sid=sid,
                topic=topic,
                payload=payload,
                roles=roles or [],
                ts=time.time(),
            )
            if external_identity:
                result.consume()
    except ConstraintError as exc:
        if not external_identity:
            raise
        raise ValueError(
            f"DCM session {sid} already exists; recovery must use read_session()"
        ) from exc
    return sid


def read_session(session_id: str) -> dict:
    """One-read context bundle: topic + payload + ALL peer contributions so far (+ any
    published final). Call this BEFORE contributing — it returns peer work so you can build
    on it, and the `version` you pass back to contribute() as your compare-and-set token.

    Each contribution carries contrib_id (cite the ones you actually read as peers_read),
    plus claimed_peers (each author's own read-claim) and peers_present (who the server saw
    present when they committed).
    """
    with _db().session(database=DCM_NEO4J_DATABASE) as s:
        rec = s.run(
            "MATCH (x:DCMSession {session_id:$sid}) RETURN x", sid=session_id
        ).single()
        if not rec:
            raise ValueError(f"no DCM session {session_id}")
        x = rec["x"]
        # match by the [:IN] relationship so pre-`seq` historical contributions stay readable;
        # order by seq (authoritative for current contributions), created as the null-seq fallback.
        contribs = s.run(
            """MATCH (c:DCMContribution)-[:IN]->(:DCMSession {session_id:$sid})
                            RETURN c ORDER BY c.seq, c.created""",
            sid=session_id,
        )
        cs = []
        for c in contribs:
            n = c["c"]
            cs.append(
                {
                    "contrib_id": n["contrib_id"],
                    "role": n["role"],
                    "content": n["content"],
                    "seq": n.get("seq"),
                    "kind": n.get("kind") or "contribution",
                    "severity": n.get("severity"),
                    "about": n.get("about"),
                    "veto": bool(n.get("veto")),
                    "disposition": n.get("disposition"),
                    "evidence_ref": n.get("evidence_ref"),
                    "claimed_peers": n.get("claimed_peers"),
                    "peers_present": n.get("peers_present"),
                    "created": n["created"],
                }
            )
    return {
        "session_id": session_id,
        "topic": x["topic"],
        "payload": x["payload"],
        "status": x["status"],
        "final": x.get("final"),
        "clearance_projection": (
            json.loads(x["clearance_projection_json"])
            if x.get("clearance_projection_json")
            else None
        ),
        "clearance_projection_sha256": x.get("clearance_projection_sha256"),
        "contributions": cs,
        "version": len(cs),
    }


def contribute(
    session_id: str,
    role: str,
    content: str,
    peers_read: list[str],
    read_version: int,
    *,
    kind: str = "contribution",
    severity: str | None = None,
    about: str | None = None,
    veto: bool = False,
    disposition: str | None = None,
    evidence_ref: str | None = None,
) -> str:
    """Write a contribution against a compare-and-set on the slot you read.

    read_version = the `version` you got from read_session (REQUIRED; open a fresh session
    with read_version=0). It is purely the GATE TOKEN: the write commits only if the live
    contribution count still equals it (WHERE cnt = $rv), i.e. no peer arrived since you read.
    The slot `seq` is then derived SERVER-SIDE from that live count — the caller cannot choose
    it, so a fabricated read_version cannot land a contribution in a future-slot gap (it just
    fails the gate). The composite uniqueness constraint on (session_id, seq) is the real CAS:
    if two writers pass the gate at the same version concurrently, both derive the same seq and
    collide on the constraint — exactly ONE commits, the rest are REJECTED with StaleReadError
    and must re-read + redo. This makes the old DCM's fatal failure (commit-into-the-void while
    siloed) structurally rejected, and unlike the first cut's count-check it actually serializes
    under concurrency (the constraint index lock, not the racy count, is what serializes).

    peers_read = the contrib_ids you ACTUALLY read + incorporated. Recorded as `claimed_peers`
    (your self-report; verify_coordination flags you if it omits a peer present to you). The
    server ALSO records `peers_present` (who was present at commit) — that is PRESENCE, not a
    proof you incorporated them.

    kind = "contribution" keeps existing callers unchanged. Plan councils use contribution-like
    kinds "plan_proposal" and "consensus_plan"; they carry no concern/resolution fields.
    kind="concern" records a typed blocking/warning concern; kind="resolution" records the
    append-only close attempt for a concern. FIX-VERIFIED and FALSE-POSITIVE resolutions
    require non-empty evidence_ref, and open_concerns() is the projection publish_final uses
    to fail closed.
    """
    if not isinstance(read_version, int) or read_version < 0:
        raise ValueError(
            "read_version must be the non-negative int 'version' from read_session "
            "(open a fresh session with read_version=0)"
        )
    typed_props = _typed_props(kind, severity, about, veto, disposition, evidence_ref)
    cid = f"contrib_{uuid.uuid4().hex[:12]}"

    def commit(tx):
        owner = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               SET x.coordination_tx_epoch = coalesce(x.coordination_tx_epoch, 0) + 1
               RETURN x.status AS status, x.coordination_mode AS coordination_mode,
                      x.active_wave_id AS active_wave_id""",
            sid=session_id,
        ).single()
        if owner is None:
            raise ValueError(f"no DCM session {session_id}")
        if owner["status"] != "open":
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if owner["coordination_mode"] == "wave" or owner["active_wave_id"] is not None:
            raise WaveStateError(
                "coordination_mode_conflict",
                "linear contribution cannot enter a wave session",
            )
        state = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               OPTIONAL MATCH (c:DCMContribution)-[:IN]->(x)
               RETURN collect(c.contrib_id) AS present, count(c) AS count""",
            sid=session_id,
        ).single()
        present = list(state["present"])
        current_version = state["count"]
        if current_version != read_version:
            known = set(peers_read or [])
            new_ids = [contrib_id for contrib_id in present if contrib_id not in known]
            raise StaleReadError(current_version, read_version, new_ids)
        rec = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               CREATE (n:DCMContribution {contrib_id:$cid, session_id:$sid, seq:$seq,
                       role:$role, content:$content, claimed_peers:$claimed,
                       peers_present:$present, created:$ts})
               SET n += $typed_props
               CREATE (n)-[:IN]->(x)
               SET x.coordination_mode='linear'
               RETURN n.contrib_id AS cid""",
            sid=session_id,
            cid=cid,
            seq=current_version,
            role=role,
            content=content,
            claimed=peers_read or [],
            present=present,
            ts=time.time(),
            typed_props=typed_props,
        ).single()
        return rec["cid"]

    try:
        with _db().session(database=DCM_NEO4J_DATABASE) as session:
            return session.execute_write(commit)
    except ConstraintError:
        fresh = read_session(session_id)
        known = set(peers_read or [])
        new_ids = [
            item["contrib_id"]
            for item in fresh["contributions"]
            if item["contrib_id"] not in known
        ]
        raise StaleReadError(fresh["version"], read_version, new_ids)


def read_wave(session_id: str, wave_id: str) -> dict:
    """Read one immutable-frontier wave, its role slots, and its sibling contributions."""
    session_id = _require_text(session_id, "session_id")
    wave_id = _require_text(wave_id, "wave_id")
    with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
        rec = session.run(
            """MATCH (w:DCMWave {session_id:$sid, wave_id:$wid})-[:IN_SESSION]->
                     (x:DCMSession {session_id:$sid})
               OPTIONAL MATCH (z:DCMWaveSlot)-[:IN_WAVE]->(w)
               WITH w, x, [item IN collect(DISTINCT z) WHERE item IS NOT NULL |
                            properties(item)] AS slots
               OPTIONAL MATCH (c:DCMContribution)-[:IN_WAVE]->(w)
               WITH w, x, slots,
                    [item IN collect(DISTINCT c) WHERE item IS NOT NULL |
                     properties(item)] AS contributions
               OPTIONAL MATCH (w)-[:HAS_PARENT]->(p:DCMContribution)
               RETURN properties(w) AS wave, x.status AS session_status,
                      x.active_wave_id AS active_wave_id,
                      x.last_closed_wave_id AS last_closed_wave_id,
                      slots, contributions,
                      [item IN collect(DISTINCT p) WHERE item IS NOT NULL |
                       item.contrib_id] AS parent_edges,
                      [item IN collect(DISTINCT p) WHERE item IS NOT NULL |
                       properties(item)] AS parent_contributions""",
            sid=session_id,
            wid=wave_id,
        ).single()
        if rec is None:
            raise ValueError(f"no DCM wave {wave_id} in session {session_id}")
        slots = sorted(
            (dict(slot) for slot in rec["slots"]), key=lambda slot: slot["role"]
        )
        for slot in slots:
            for source, target in (
                ("request_identity_json", "request_identity"),
                ("claim_observation_json", "claim_observation"),
                ("outcome_record_json", "outcome_record"),
            ):
                value = slot.pop(source, None)
                if value is not None:
                    slot[target] = json.loads(value)
        contributions = []
        for raw_contribution in rec["contributions"]:
            contribution = dict(raw_contribution)
            content_json = contribution.pop("structured_content_json", None)
            if content_json is not None:
                contribution["structured_content"] = json.loads(content_json)
            execution_json = contribution.pop("observed_execution_json", None)
            if execution_json is not None:
                contribution["observed_execution"] = json.loads(execution_json)
            receipt_json = contribution.pop("contribution_receipt_json", None)
            if receipt_json is not None:
                contribution["contribution_receipt"] = json.loads(receipt_json)
            contributions.append(contribution)
        contributions.sort(key=lambda contribution: contribution["role"])
        parent_edges = sorted(rec["parent_edges"])
        parent_contributions = sorted(
            (dict(item) for item in rec["parent_contributions"]),
            key=lambda item: item["contrib_id"],
        )
    wave = dict(rec["wave"])
    wave.update(
        {
            "session_status": rec["session_status"],
            "active_wave_id": rec["active_wave_id"],
            "last_closed_wave_id": rec["last_closed_wave_id"],
            "slots": slots,
            "contributions": contributions,
            "parent_edge_contribution_ids": parent_edges,
            "parent_edge_contributions": parent_contributions,
        }
    )
    return wave


def open_wave(
    session_id: str,
    *,
    round: int,
    phase: str,
    prompt_id: str,
    prompt_revision: int,
    prompt_messages: list,
    attachment_evidence_digests: list[str],
    request_revision: int,
    required_members: list[dict],
    request_contract: str | None = None,
    parent_wave_id: str | None = None,
) -> dict:
    """Open one role-complete wave whose parent frontier is derived from the graph.

    A session has at most one active wave. A critique consumes the immediately preceding complete
    independent frontier. A higher prompt revision restarts independent work in the same native
    round without treating stale prior-revision output as parents.
    """
    session_id = _require_text(session_id, "session_id")
    round = _require_positive_int(round, "round")
    phase = _require_text(phase, "phase")
    if phase not in {"independent", "critique"}:
        raise ValueError("phase must be independent or critique")
    prompt_id = _require_text(prompt_id, "prompt_id")
    prompt_revision = _require_positive_int(prompt_revision, "prompt_revision")
    prompt_sha256 = canonical_prompt_sha256(
        prompt_messages, attachment_evidence_digests
    )
    request_revision = _require_positive_int(request_revision, "request_revision")
    request_contract = _validated_request_contract(request_contract)
    members = _canonical_members(
        required_members, request_contract=request_contract
    )
    roles = [member["role"] for member in members]
    members_json = _canonical_json(members)
    membership_sha256 = _canonical_sha256(members)
    if parent_wave_id is not None:
        parent_wave_id = _require_text(parent_wave_id, "parent_wave_id")

    wave_id = f"wave_{uuid.uuid4().hex[:16]}"
    now = time.time()

    def create(tx):
        session_record = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               SET x.wave_tx_epoch = coalesce(x.wave_tx_epoch, 0) + 1
               RETURN x.status AS status, x.coordination_mode AS coordination_mode,
                      x.active_wave_id AS active_wave_id,
                      x.last_closed_wave_id AS last_closed_wave_id,
                      x.roles AS session_roles,
                      x.wave_membership_sha256 AS wave_membership_sha256,
                      x.wave_members_json AS wave_members_json""",
            sid=session_id,
        ).single()
        if session_record is None:
            raise ValueError(f"no DCM session {session_id}")

        existing = tx.run(
            """MATCH (w:DCMWave {session_id:$sid, round:$round, phase:$phase,
                                  prompt_revision:$prompt_revision,
                                  request_revision:$request_revision})
               RETURN properties(w) AS wave""",
            sid=session_id,
            round=round,
            phase=phase,
            prompt_revision=prompt_revision,
            request_revision=request_revision,
        ).single()

        if session_record["status"] != "open":
            if existing is not None:
                raise _IdempotentWaveReplay({"existing": dict(existing["wave"])})
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if session_record["coordination_mode"] not in (None, "wave"):
            raise WaveStateError(
                "coordination_mode_conflict",
                f"session {session_id} is not a wave session",
            )

        linear_count = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               OPTIONAL MATCH (c:DCMContribution)-[:IN]->(x)
               RETURN count(c) AS count""",
            sid=session_id,
        ).single()["count"]
        if linear_count:
            raise WaveStateError(
                "coordination_mode_conflict",
                "a session with linear contributions cannot open a wave",
            )
        session_roles = sorted(session_record["session_roles"] or [])
        if session_roles and session_roles != roles:
            raise WaveIdentityConflictError(
                "wave membership roles differ from the immutable session roster"
            )
        if session_record["wave_membership_sha256"] is not None and (
            session_record["wave_membership_sha256"] != membership_sha256
            or session_record["wave_members_json"] != members_json
        ):
            raise WaveIdentityConflictError(
                "wave seat/role membership differs from the immutable session roster"
            )

        if existing is not None:
            raise _IdempotentWaveReplay({"existing": dict(existing["wave"])})
        if session_record["active_wave_id"] is not None:
            raise WaveStateError(
                "wave_already_open",
                f"session {session_id} already has active wave {session_record['active_wave_id']}",
            )

        parents = []
        parent_frontier_sha256 = _canonical_sha256(parents)
        last_closed_wave_id = session_record["last_closed_wave_id"]
        if last_closed_wave_id is None:
            if parent_wave_id is not None:
                raise WaveFrontierMismatchError(
                    "the first wave cannot name a parent wave"
                )
            if round != 1 or phase != "independent":
                raise WaveStateError(
                    "invalid_wave_order", "the first wave must be round 1 independent"
                )
        else:
            if parent_wave_id != last_closed_wave_id:
                raise WaveFrontierMismatchError(
                    f"next wave must name the last closed wave {last_closed_wave_id}"
                )
            parent = tx.run(
                """MATCH (p:DCMWave {session_id:$sid, wave_id:$parent_wave_id})
                   RETURN p.status AS status, p.close_outcome AS close_outcome,
                          p.completion_frontier AS completion_frontier,
                          p.completion_frontier_sha256 AS completion_frontier_sha256,
                          p.round AS round, p.phase AS phase,
                          p.prompt_revision AS prompt_revision,
                          p.required_members_json AS required_members_json,
                          p.membership_sha256 AS membership_sha256,
                          p.request_contract AS request_contract,
                          p.superseded_by_prompt_revision AS superseded_by_prompt_revision""",
                sid=session_id,
                parent_wave_id=parent_wave_id,
            ).single()
            if parent is None or parent["status"] != "closed":
                raise WaveStateError(
                    "incomplete_round", "a next wave requires a closed predecessor"
                )
            if parent["close_outcome"] not in {"complete", "superseded_revision"}:
                raise WaveStateError(
                    "incomplete_round",
                    "a next wave requires a complete or superseded predecessor",
                )
            if (
                parent["membership_sha256"] != membership_sha256
                or parent["required_members_json"] != members_json
                or parent["request_contract"] != request_contract
            ):
                raise WaveIdentityConflictError(
                    "wave request contract or membership differs from its predecessor"
                )
            critique_transition = (
                parent["close_outcome"] == "complete"
                and parent["phase"] == "independent"
                and phase == "critique"
                and round == parent["round"]
                and prompt_revision == parent["prompt_revision"]
            )
            amendment_transition = (
                parent["close_outcome"] in {"complete", "superseded_revision"}
                and phase == "independent"
                and round == parent["round"]
                and prompt_revision > parent["prompt_revision"]
                and (
                    parent["close_outcome"] == "complete"
                    or prompt_revision == parent["superseded_by_prompt_revision"]
                )
            )
            if not (critique_transition or amendment_transition):
                raise WaveStateError(
                    "invalid_wave_order",
                    "wave phase, round, or prompt revision does not follow its parent",
                )
            parents = (
                list(parent["completion_frontier"] or []) if critique_transition else []
            )
            parent_frontier_sha256 = _canonical_sha256(parents)
            if (
                critique_transition
                and parent["completion_frontier_sha256"] != parent_frontier_sha256
            ):
                raise WaveFrontierMismatchError(
                    "parent wave completion frontier digest is invalid"
                )
            if critique_transition:
                actual_parents = sorted(
                    row["contrib_id"]
                    for row in tx.run(
                        """MATCH (c:DCMContribution)-[:IN_WAVE]->
                             (p:DCMWave {session_id:$sid, wave_id:$parent_wave_id})
                       MATCH (c)-[:FILLS_SLOT]->(:DCMWaveSlot)-[:IN_WAVE]->(p)
                       RETURN c.contrib_id AS contrib_id ORDER BY contrib_id""",
                        sid=session_id,
                        parent_wave_id=parent_wave_id,
                    )
                )
                if actual_parents != parents:
                    raise WaveFrontierMismatchError(
                        "parent wave contributions differ from its persisted completion frontier"
                    )

        identity = {
            "session_id": session_id,
            "round": round,
            "phase": phase,
            "prompt_id": prompt_id,
            "prompt_revision": prompt_revision,
            "prompt_sha256": prompt_sha256,
            "request_revision": request_revision,
            "graph_uri": DCM_NEO4J_URI,
            "graph_database": DCM_NEO4J_DATABASE,
            "required_members": members,
            "membership_sha256": membership_sha256,
            "parent_wave_id": parent_wave_id,
            "parent_contribution_ids": parents,
            "parent_frontier_sha256": parent_frontier_sha256,
            "transition": (
                "first"
                if parent_wave_id is None
                else "critique"
                if parents
                else "prompt_amendment"
            ),
        }
        if request_contract == _REQUEST_CONTRACT_V2:
            identity["request_contract"] = request_contract
        wave_fingerprint = _canonical_sha256(identity)
        tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               CREATE (w:DCMWave {wave_id:$wid, session_id:$sid, round:$round,
                       phase:$phase, prompt_id:$prompt_id, prompt_revision:$prompt_revision,
                       prompt_sha256:$prompt_sha256, request_revision:$request_revision,
                       request_contract:$request_contract,
                       graph_uri:$graph_uri, graph_database:$graph_database,
                       prompt_messages_json:$prompt_messages_json,
                       attachment_evidence_digests:$attachment_evidence_digests,
                       required_roles:$roles, required_members_json:$members_json,
                       membership_sha256:$membership_sha256, parent_wave_id:$parent_wave_id,
                       parent_contribution_ids:$parents,
                       parent_frontier_sha256:$parent_frontier_sha256,
                       transition:$transition,
                       wave_fingerprint:$wave_fingerprint, status:'open', created:$now,
                       wave_tx_epoch:0, superseded_by_prompt_revision:0})
               CREATE (w)-[:IN_SESSION]->(x)
               SET x.coordination_mode='wave', x.active_wave_id=$wid,
                   x.wave_membership_sha256=coalesce(
                       x.wave_membership_sha256, $membership_sha256),
                   x.wave_members_json=coalesce(x.wave_members_json, $members_json)""",
            sid=session_id,
            wid=wave_id,
            round=round,
            phase=phase,
            prompt_id=prompt_id,
            prompt_revision=prompt_revision,
            prompt_sha256=prompt_sha256,
            request_revision=request_revision,
            request_contract=request_contract,
            graph_uri=DCM_NEO4J_URI,
            graph_database=DCM_NEO4J_DATABASE,
            prompt_messages_json=_canonical_json(prompt_messages),
            attachment_evidence_digests=attachment_evidence_digests,
            roles=roles,
            members_json=members_json,
            membership_sha256=membership_sha256,
            parent_wave_id=parent_wave_id,
            parents=parents,
            parent_frontier_sha256=parent_frontier_sha256,
            transition=identity["transition"],
            wave_fingerprint=wave_fingerprint,
            now=now,
        ).consume()
        if parents:
            tx.run(
                """MATCH (w:DCMWave {wave_id:$wid})
                   UNWIND $parents AS parent_id
                   MATCH (c:DCMContribution {contrib_id:parent_id})
                   CREATE (w)-[:HAS_PARENT]->(c)""",
                wid=wave_id,
                parents=parents,
            ).consume()
        tx.run(
            """MATCH (w:DCMWave {wave_id:$wid})
               UNWIND $members AS member
               CREATE (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, round:$round,
                       role:member.role, seat_id:member.seat_id,
                       prompt_contract_sha256:member.prompt_contract_sha256,
                       model_identity_receipt_sha256:member.model_identity_receipt_sha256,
                       request_revision:$request_revision, state:'pending',
                       created:$now, updated:$now})
               CREATE (z)-[:IN_WAVE]->(w)""",
            sid=session_id,
            wid=wave_id,
            round=round,
            members=members,
            request_revision=request_revision,
            now=now,
        ).consume()
        return {"wave_id": wave_id}

    try:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            result = session.execute_write(create)
    except _IdempotentWaveReplay as replay:
        result = replay.result
    except ConstraintError as exc:
        raise WaveIdentityConflictError(
            "a concurrent wave open occupied this immutable wave identity"
        ) from exc

    if "existing" in result:
        existing = result["existing"]
        existing_identity = {
            "session_id": existing["session_id"],
            "round": existing["round"],
            "phase": existing["phase"],
            "prompt_id": existing["prompt_id"],
            "prompt_revision": existing["prompt_revision"],
            "prompt_sha256": existing["prompt_sha256"],
            "request_revision": existing["request_revision"],
            "graph_uri": existing["graph_uri"],
            "graph_database": existing["graph_database"],
            "required_members": json.loads(existing["required_members_json"]),
            "membership_sha256": existing["membership_sha256"],
            "parent_wave_id": existing.get("parent_wave_id"),
            "parent_contribution_ids": list(existing["parent_contribution_ids"]),
            "parent_frontier_sha256": existing["parent_frontier_sha256"],
            "transition": existing["transition"],
        }
        if existing.get("request_contract") is not None:
            existing_identity["request_contract"] = existing["request_contract"]
        requested_identity = dict(existing_identity)
        requested_identity.update(
            {
                "prompt_id": prompt_id,
                "prompt_sha256": prompt_sha256,
                "required_members": members,
                "membership_sha256": membership_sha256,
                "parent_wave_id": parent_wave_id,
            }
        )
        if request_contract is None:
            requested_identity.pop("request_contract", None)
        else:
            requested_identity["request_contract"] = request_contract
        if existing["wave_fingerprint"] != _canonical_sha256(requested_identity):
            raise WaveIdentityConflictError(
                "the logical wave identity already exists with different immutable fields"
            )
        wave_id = existing["wave_id"]
    else:
        wave_id = result["wave_id"]
    return read_wave(session_id, wave_id)


def reserve_wave_request(
    session_id: str,
    wave_id: str,
    *,
    role: str,
    request_revision: int,
    request_identity: dict,
    parent_contribution_ids: list[str],
) -> dict:
    """Coordinator-side reservation performed before a request is placed in Redis."""
    session_id = _require_text(session_id, "session_id")
    wave_id = _require_text(wave_id, "wave_id")
    role = _require_text(role, "role")
    request_revision = _require_positive_int(request_revision, "request_revision")
    request_id = canonical_wave_request_id(request_identity)
    parents = _canonical_text_set(
        parent_contribution_ids, "parent_contribution_ids", allow_empty=True
    )
    request_identity_json = _canonical_json(request_identity)

    existing_wave = read_wave(session_id, wave_id)
    existing_slot = next(
        (
            slot
            for slot in existing_wave["slots"]
            if slot["role"] == role and slot["request_revision"] == request_revision
        ),
        None,
    )
    if (
        parents != list(existing_wave["parent_contribution_ids"])
        or parents != list(existing_wave["parent_edge_contribution_ids"])
        or _canonical_sha256(parents) != existing_wave["parent_frontier_sha256"]
    ):
        raise WaveFrontierMismatchError(
            "reserved request parents differ from the graph-derived wave frontier"
        )
    if existing_slot and existing_slot.get("request_id") is not None:
        if (
            existing_slot["request_id"] == request_id
            and existing_slot.get("request_identity") == request_identity
        ):
            _validated_request_identity(wave=existing_wave, slot=existing_slot)
            return {
                "session_id": session_id,
                "wave_id": wave_id,
                "role": role,
                "request_id": request_id,
                "state": existing_slot["state"],
                "outcome": "reserved",
                "duplicate": True,
            }
        raise WaveIdentityConflictError(
            "the role slot is reserved for a different request"
        )

    def reserve(tx):
        locked = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})<-[:IN_SESSION]-
                     (w:DCMWave {session_id:$sid, wave_id:$wid})<-[:IN_WAVE]-
                     (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                     request_revision:$request_revision})
               SET x.wave_tx_epoch = coalesce(x.wave_tx_epoch, 0) + 1,
                   w.wave_tx_epoch = coalesce(w.wave_tx_epoch, 0) + 1,
                   z.slot_tx_epoch = coalesce(z.slot_tx_epoch, 0) + 1
               RETURN x.status AS session_status, properties(w) AS wave,
                      properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
        ).single()
        if locked is None:
            raise WaveStateError("unknown_role", "the wave role slot does not exist")
        wave = dict(locked["wave"])
        slot = dict(locked["slot"])
        graph_fields = {
            "session_id": session_id,
            "wave_id": wave_id,
            "round": wave["round"],
            "phase": wave["phase"],
            "prompt_id": wave["prompt_id"],
            "prompt_revision": wave["prompt_revision"],
            "prompt_sha256": wave["prompt_sha256"],
            "seat_id": slot["seat_id"],
            "role": role,
            "request_revision": request_revision,
            "parent_frontier_sha256": wave["parent_frontier_sha256"],
            "request_contract": wave.get("request_contract"),
        }
        if wave.get("request_contract") == _REQUEST_CONTRACT_V2:
            graph_fields.update(
                {
                    "prompt_contract_sha256": slot.get("prompt_contract_sha256"),
                    "model_identity_receipt_sha256": slot.get(
                        "model_identity_receipt_sha256"
                    ),
                }
            )
        mismatched = [
            field
            for field, expected in graph_fields.items()
            if request_identity.get(field) != expected
        ]
        if mismatched:
            raise WaveIdentityConflictError(
                f"request_identity differs from graph-bound fields: {sorted(mismatched)}"
            )
        actual_parents = sorted(
            row["contrib_id"]
            for row in tx.run(
                """MATCH (:DCMWave {session_id:$sid, wave_id:$wid})-[:HAS_PARENT]->
                     (c:DCMContribution)
               RETURN c.contrib_id AS contrib_id ORDER BY contrib_id""",
                sid=session_id,
                wid=wave_id,
            )
        )
        if (
            parents != list(wave["parent_contribution_ids"])
            or parents != actual_parents
            or _canonical_sha256(parents) != wave["parent_frontier_sha256"]
        ):
            raise WaveFrontierMismatchError(
                "reserved request parents differ from the graph-derived wave frontier"
            )
        if slot.get("request_id") is not None:
            if (
                slot["request_id"] != request_id
                or slot.get("request_identity_json") != request_identity_json
            ):
                raise WaveIdentityConflictError(
                    "the role slot is reserved for a different request"
                )
            raise _IdempotentWaveReplay({"slot": slot, "duplicate": True})
        if locked["session_status"] != "open":
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if wave["status"] != "open":
            raise WaveStateError("closed_wave", f"wave {wave_id} is not open")
        if slot["state"] != "pending":
            raise WaveStateError(slot["state"], f"role slot {role} is already terminal")
        updated = tx.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                      request_revision:$request_revision})
               WHERE z.state='pending' AND z.request_id IS NULL
               SET z.request_id=$request_id,
                   z.request_identity_json=$request_identity_json,
                   z.updated=$now
               RETURN properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
            request_id=request_id,
            request_identity_json=request_identity_json,
            now=time.time(),
        ).single()
        if updated is None:
            raise WaveIdentityConflictError(
                "the role slot changed while it was being reserved"
            )
        return {"slot": dict(updated["slot"]), "duplicate": False}

    try:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            result = session.execute_write(reserve)
    except _IdempotentWaveReplay as replay:
        result = replay.result
    except ConstraintError as exc:
        raise WaveIdentityConflictError(
            "request_id is already bound to another wave slot"
        ) from exc
    return {
        "session_id": session_id,
        "wave_id": wave_id,
        "role": role,
        "request_id": request_id,
        "state": result["slot"]["state"],
        "outcome": "reserved",
        "duplicate": result["duplicate"],
    }


def claim_wave_request(
    session_id: str,
    wave_id: str,
    *,
    role: str,
    request_revision: int,
    request_id: str,
    parent_contribution_ids: list[str],
    claim_observation: dict,
) -> dict:
    """Validate a reserved request and exact live identity before authorizing inference."""
    session_id = _require_text(session_id, "session_id")
    wave_id = _require_text(wave_id, "wave_id")
    role = _require_text(role, "role")
    request_revision = _require_positive_int(request_revision, "request_revision")
    request_id = _require_sha256(request_id, "request_id")
    parents = _canonical_text_set(
        parent_contribution_ids, "parent_contribution_ids", allow_empty=True
    )

    existing_wave = read_wave(session_id, wave_id)
    existing_slot = next(
        (
            slot
            for slot in existing_wave["slots"]
            if slot["role"] == role and slot["request_revision"] == request_revision
        ),
        None,
    )
    if existing_slot is None or existing_slot.get("request_id") is None:
        raise WaveStateError(
            "unreserved_request", "request must be reserved before dispatch"
        )
    if existing_slot["request_id"] != request_id:
        raise WaveIdentityConflictError(
            "the role slot is reserved for a different request"
        )
    request_identity = _validated_request_identity(
        wave=existing_wave, slot=existing_slot
    )
    if (
        parents != list(existing_wave["parent_contribution_ids"])
        or parents != list(existing_wave["parent_edge_contribution_ids"])
        or _canonical_sha256(parents) != existing_wave["parent_frontier_sha256"]
    ):
        raise WaveFrontierMismatchError(
            "claim parents differ from the reserved wave frontier"
        )
    if existing_slot["state"] != "pending":
        receipt = None
        outcome_record = None
        if existing_slot["state"] == "contributed":
            contribution = next(
                item
                for item in existing_wave["contributions"]
                if item["contrib_id"] == existing_slot["contrib_id"]
            )
            receipt = _validated_contribution_receipt(
                wave=existing_wave, slot=existing_slot, contribution=contribution
            )
        elif existing_slot["state"] in _WAVE_TERMINAL_STATES:
            outcome_record = _validated_outcome_record(
                wave=existing_wave, slot=existing_slot
            )
        return {
            "session_id": session_id,
            "wave_id": wave_id,
            "role": role,
            "request_id": request_id,
            "state": existing_slot["state"],
            "outcome": (
                "duplicate_inflight"
                if existing_slot["state"] == "claimed"
                else existing_slot.get("terminal_outcome") or existing_slot["state"]
            ),
            "inference_authorized": False,
            "contrib_id": existing_slot.get("contrib_id"),
            "contribution_receipt": receipt,
            "outcome_record": outcome_record,
            "inference_performed": existing_slot.get("inference_performed"),
            "failure_stage": existing_slot.get("failure_stage"),
            "failure_detail_sha256": existing_slot.get("failure_detail_sha256"),
        }
    observed_claim = _validated_claim_observation(claim_observation, request_identity)

    def claim(tx):
        locked = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})<-[:IN_SESSION]-
                     (w:DCMWave {session_id:$sid, wave_id:$wid})<-[:IN_WAVE]-
                     (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                     request_revision:$request_revision})
               SET x.wave_tx_epoch = coalesce(x.wave_tx_epoch, 0) + 1,
                   w.wave_tx_epoch = coalesce(w.wave_tx_epoch, 0) + 1,
                   z.slot_tx_epoch = coalesce(z.slot_tx_epoch, 0) + 1
               RETURN x.status AS session_status, properties(w) AS wave,
                      properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
        ).single()
        if locked is None:
            raise WaveStateError("unknown_role", "the wave role slot does not exist")
        wave = dict(locked["wave"])
        slot = dict(locked["slot"])
        if slot.get("request_id") != request_id:
            raise WaveIdentityConflictError(
                "the role slot is not owned by this request"
            )
        actual_parents = sorted(
            row["contrib_id"]
            for row in tx.run(
                """MATCH (:DCMWave {session_id:$sid, wave_id:$wid})-[:HAS_PARENT]->
                     (c:DCMContribution)
               RETURN c.contrib_id AS contrib_id ORDER BY contrib_id""",
                sid=session_id,
                wid=wave_id,
            )
        )
        wave["parent_edge_contribution_ids"] = actual_parents
        if (
            parents != actual_parents
            or parents != list(wave["parent_contribution_ids"])
            or _canonical_sha256(parents) != wave["parent_frontier_sha256"]
        ):
            raise WaveFrontierMismatchError(
                "claim parents differ from graph relationships"
            )
        if slot["state"] != "pending":
            raise _IdempotentWaveReplay({"slot": slot, "new_claim": False})
        frozen_identity = json.loads(slot["request_identity_json"])
        slot["request_identity"] = frozen_identity
        _validated_request_identity(wave=wave, slot=slot)
        _validated_claim_observation(observed_claim, frozen_identity)
        if locked["session_status"] != "open":
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if wave["status"] != "open":
            raise WaveStateError("closed_wave", f"wave {wave_id} is not open")
        updated = tx.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                      request_revision:$request_revision})
               WHERE z.state='pending' AND z.request_id=$request_id
               SET z.state='claimed', z.claim_observation_json=$claim_observation_json,
                   z.updated=$now
               RETURN properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
            request_id=request_id,
            claim_observation_json=_canonical_json(observed_claim),
            now=time.time(),
        ).single()
        if updated is None:
            raise WaveIdentityConflictError(
                "the reserved role slot changed during claim"
            )
        return {"slot": dict(updated["slot"]), "new_claim": True}

    try:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            result = session.execute_write(claim)
    except _IdempotentWaveReplay as replay:
        result = replay.result

    slot = result["slot"]
    if result["new_claim"]:
        outcome = "claimed"
        inference_authorized = True
    elif slot["state"] == "claimed":
        outcome = "duplicate_inflight"
        inference_authorized = False
    elif slot["state"] == "contributed":
        outcome = "contributed"
        inference_authorized = False
    else:
        outcome = slot.get("terminal_outcome") or slot["state"]
        inference_authorized = False
    response = {
        "session_id": session_id,
        "wave_id": wave_id,
        "role": role,
        "request_id": request_id,
        "state": slot["state"],
        "outcome": outcome,
        "inference_authorized": inference_authorized,
        "contrib_id": slot.get("contrib_id"),
    }
    if slot["state"] == "contributed":
        latest = read_wave(session_id, wave_id)
        latest_slot = next(item for item in latest["slots"] if item["role"] == role)
        contribution = next(
            item
            for item in latest["contributions"]
            if item["contrib_id"] == slot["contrib_id"]
        )
        response["contribution_receipt"] = _validated_contribution_receipt(
            wave=latest, slot=latest_slot, contribution=contribution
        )
    elif slot["state"] in _WAVE_TERMINAL_STATES:
        latest = read_wave(session_id, wave_id)
        latest_slot = next(item for item in latest["slots"] if item["role"] == role)
        response["outcome_record"] = _validated_outcome_record(
            wave=latest, slot=latest_slot
        )
        response["inference_performed"] = latest_slot.get("inference_performed")
        response["failure_stage"] = latest_slot.get("failure_stage")
        response["failure_detail_sha256"] = latest_slot.get("failure_detail_sha256")
    return response


def contribute_wave(
    session_id: str,
    wave_id: str,
    *,
    role: str,
    request_revision: int,
    request_id: str,
    structured_content: dict,
    claimed_peers: list[str],
    observed_execution: dict,
    emitter: dict,
    kind: str = "contribution",
    severity: str | None = None,
    about: str | None = None,
    veto: bool = False,
    disposition: str | None = None,
    evidence_ref: str | None = None,
) -> dict:
    """Commit one previously claimed sibling result without using the linear CAS sequence."""
    session_id = _require_text(session_id, "session_id")
    wave_id = _require_text(wave_id, "wave_id")
    role = _require_text(role, "role")
    request_revision = _require_positive_int(request_revision, "request_revision")
    request_id = _require_sha256(request_id, "request_id")
    if not isinstance(structured_content, dict):
        raise ValueError("structured_content must be a dict")
    claimed = _canonical_text_set(claimed_peers, "claimed_peers", allow_empty=True)
    typed_props = _typed_props(kind, severity, about, veto, disposition, evidence_ref)
    structured_content_json = _canonical_json(structured_content)
    content_sha256 = _canonical_sha256(structured_content)

    wave_snapshot = read_wave(session_id, wave_id)
    slot_snapshot = next(
        (
            slot
            for slot in wave_snapshot["slots"]
            if slot["role"] == role and slot["request_revision"] == request_revision
        ),
        None,
    )
    if slot_snapshot is None:
        raise WaveStateError("unknown_role", "the wave role slot does not exist")
    if slot_snapshot.get("request_id") != request_id:
        raise WaveIdentityConflictError("the role slot is not owned by this request")
    if slot_snapshot.get("request_identity") is None:
        raise WaveStateError(
            "unreserved_request", "request must be reserved before contribution"
        )
    request_identity_snapshot = _validated_request_identity(
        wave=wave_snapshot, slot=slot_snapshot
    )
    _validated_claim_observation(
        slot_snapshot.get("claim_observation"), request_identity_snapshot
    )
    observed_snapshot = _validated_observed_execution(
        observed_execution, request_identity_snapshot
    )
    emitter_snapshot = _validated_emitter(emitter, observed_snapshot)
    if (
        claimed != list(wave_snapshot["parent_contribution_ids"])
        or claimed != list(wave_snapshot["parent_edge_contribution_ids"])
        or _canonical_sha256(claimed) != wave_snapshot["parent_frontier_sha256"]
    ):
        raise WaveFrontierMismatchError(
            "claimed_peers must equal the exact relationship-derived wave frontier"
        )
    _validated_resolution_parent(wave_snapshot, typed_props)
    contribution_fingerprint = _canonical_sha256(
        {
            "request_id": request_id,
            "structured_content": structured_content,
            "claimed_peers": claimed,
            "observed_execution": observed_snapshot,
            "emitter": emitter_snapshot,
            "typed_props": typed_props,
        }
    )

    def replay_result(wave: dict, slot: dict) -> dict:
        if slot.get("contribution_fingerprint") != contribution_fingerprint:
            raise WaveIdentityConflictError(
                "the contributed request was replayed with different content or execution"
            )
        contribution = next(
            (
                item
                for item in wave["contributions"]
                if item["contrib_id"] == slot.get("contrib_id")
            ),
            None,
        )
        if contribution is None:
            raise WaveIdentityConflictError(
                "the contributed slot has no relationship-bound canonical receipt"
            )
        receipt = _validated_contribution_receipt(
            wave=wave, slot=slot, contribution=contribution
        )
        return {
            "session_id": session_id,
            "wave_id": wave_id,
            "role": role,
            "request_id": request_id,
            "contrib_id": contribution["contrib_id"],
            "outcome": "contributed",
            "duplicate": True,
            "contribution_receipt": receipt,
        }

    if slot_snapshot["state"] == "contributed":
        return replay_result(wave_snapshot, slot_snapshot)
    if slot_snapshot["state"] != "claimed":
        raise WaveStateError(
            slot_snapshot.get("terminal_outcome") or slot_snapshot["state"],
            f"role slot {role} cannot accept a contribution",
        )
    if wave_snapshot["session_status"] != "open":
        raise WaveStateError("closed_session", f"session {session_id} is not open")
    if wave_snapshot["status"] != "open":
        raise WaveStateError("closed_wave", f"wave {wave_id} is not open")

    def commit(tx):
        locked = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})<-[:IN_SESSION]-
                     (w:DCMWave {session_id:$sid, wave_id:$wid})<-[:IN_WAVE]-
                     (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                     request_revision:$request_revision})
               SET x.wave_tx_epoch = coalesce(x.wave_tx_epoch, 0) + 1,
                   w.wave_tx_epoch = coalesce(w.wave_tx_epoch, 0) + 1,
                   z.slot_tx_epoch = coalesce(z.slot_tx_epoch, 0) + 1
               RETURN x.status AS session_status, properties(w) AS wave,
                      properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
        ).single()
        if locked is None:
            raise WaveStateError("unknown_role", "the wave role slot does not exist")
        wave = dict(locked["wave"])
        slot = dict(locked["slot"])
        if slot.get("request_id") != request_id:
            raise WaveIdentityConflictError(
                "the role slot is not owned by this request"
            )
        request_identity = json.loads(slot["request_identity_json"])
        slot["request_identity"] = request_identity
        claim_observation_json = slot.get("claim_observation_json")
        claim_observation = (
            json.loads(claim_observation_json) if claim_observation_json else None
        )
        _validated_claim_observation(claim_observation, request_identity)
        slot["claim_observation"] = claim_observation
        observed = _validated_observed_execution(observed_execution, request_identity)
        validated_emitter = _validated_emitter(emitter, observed)
        parent_rows = [
            dict(row)
            for row in tx.run(
                """MATCH (:DCMWave {session_id:$sid, wave_id:$wid})-[:HAS_PARENT]->
                     (c:DCMContribution)
               RETURN c.contrib_id AS contrib_id,
                      coalesce(c.kind, 'contribution') AS kind
               ORDER BY contrib_id""",
                sid=session_id,
                wid=wave_id,
            )
        ]
        actual_parents = [row["contrib_id"] for row in parent_rows]
        wave["parent_edge_contribution_ids"] = actual_parents
        wave["parent_edge_contributions"] = parent_rows
        if (
            claimed != list(wave["parent_contribution_ids"])
            or claimed != actual_parents
            or _canonical_sha256(claimed) != wave["parent_frontier_sha256"]
        ):
            raise WaveFrontierMismatchError(
                "claimed_peers must equal the exact relationship-derived wave frontier"
            )
        _validated_request_identity(wave=wave, slot=slot)
        _validated_resolution_parent(wave, typed_props)
        locked_fingerprint = _canonical_sha256(
            {
                "request_id": request_id,
                "structured_content": structured_content,
                "claimed_peers": claimed,
                "observed_execution": observed,
                "emitter": validated_emitter,
                "typed_props": typed_props,
            }
        )

        if slot["state"] == "contributed":
            if slot.get("contribution_fingerprint") != locked_fingerprint:
                raise WaveIdentityConflictError(
                    "the contributed request was replayed with different content or execution"
                )
            raise _IdempotentWaveReplay({"duplicate": True})
        if slot["state"] != "claimed":
            raise WaveStateError(
                slot.get("terminal_outcome") or slot["state"],
                f"role slot {role} cannot accept a contribution",
            )
        if locked["session_status"] != "open":
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if wave["status"] != "open":
            raise WaveStateError("closed_wave", f"wave {wave_id} is not open")

        cid = f"contrib_{uuid.uuid4().hex[:16]}"
        now = time.time()
        wave["parent_contribution_ids"] = actual_parents
        receipt = _build_contribution_receipt(
            wave=wave,
            slot=slot,
            request_identity=request_identity,
            emitter=validated_emitter,
            observed_execution=observed,
            contrib_id=cid,
            typed_props=typed_props,
            content_sha256=content_sha256,
            claimed_peers=claimed,
        )
        receipt_json = _canonical_json(receipt)
        rec = tx.run(
            """MATCH (w:DCMWave {session_id:$sid, wave_id:$wid})
               MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                     request_revision:$request_revision})
               WHERE w.status='open' AND z.state='claimed' AND z.request_id=$request_id
               CREATE (c:DCMContribution {contrib_id:$cid, session_id:$sid, wave_id:$wid,
                       round:w.round, phase:w.phase, role:$role,
                       request_revision:$request_revision, request_id:$request_id,
                       structured_content_json:$structured_content_json,
                       content_sha256:$content_sha256, claimed_peers:$claimed,
                       peers_present:$actual_parents,
                       parent_frontier_sha256:w.parent_frontier_sha256,
                       observed_execution_json:$observed_execution_json,
                       contribution_fingerprint:$contribution_fingerprint,
                       contribution_receipt_json:$receipt_json,
                       contribution_receipt_sha256:$receipt_sha256, created:$now})
               SET c += $typed_props
               CREATE (c)-[:IN_WAVE]->(w)
               CREATE (c)-[:FILLS_SLOT]->(z)
               SET z.state='contributed', z.contrib_id=$cid,
                   z.content_sha256=$content_sha256,
                   z.contribution_fingerprint=$contribution_fingerprint,
                   z.contribution_receipt_sha256=$receipt_sha256, z.updated=$now
               RETURN c.contrib_id AS contrib_id, c.contribution_receipt_json AS receipt_json""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
            request_id=request_id,
            cid=cid,
            structured_content_json=structured_content_json,
            content_sha256=content_sha256,
            claimed=claimed,
            actual_parents=actual_parents,
            observed_execution_json=_canonical_json(observed),
            contribution_fingerprint=locked_fingerprint,
            receipt_json=receipt_json,
            receipt_sha256=receipt["receipt_sha256"],
            typed_props=typed_props,
            now=now,
        ).single()
        if rec is None:
            raise WaveIdentityConflictError(
                "the claimed role slot changed before graph commit"
            )
        return {
            "contrib_id": rec["contrib_id"],
            "duplicate": False,
            "contribution_receipt": json.loads(rec["receipt_json"]),
        }

    try:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            result = session.execute_write(commit)
    except _IdempotentWaveReplay:
        latest = read_wave(session_id, wave_id)
        latest_slot = next(
            item
            for item in latest["slots"]
            if item["role"] == role and item["request_revision"] == request_revision
        )
        return replay_result(latest, latest_slot)
    except ConstraintError as exc:
        wave = read_wave(session_id, wave_id)
        slot = next(
            (
                item
                for item in wave["slots"]
                if item["role"] == role and item["request_revision"] == request_revision
            ),
            None,
        )
        if (
            slot
            and slot.get("request_id") == request_id
            and slot.get("state") == "contributed"
        ):
            expected_fingerprint = _canonical_sha256(
                {
                    "request_id": request_id,
                    "structured_content": structured_content,
                    "claimed_peers": claimed,
                    "observed_execution": observed_execution,
                    "emitter": emitter,
                    "typed_props": typed_props,
                }
            )
            if slot.get("contribution_fingerprint") == expected_fingerprint:
                return replay_result(wave, slot)
        raise WaveIdentityConflictError(
            "request_id or role slot is already occupied by a different contribution"
        ) from exc

    return {
        "session_id": session_id,
        "wave_id": wave_id,
        "role": role,
        "request_id": request_id,
        "contrib_id": result["contrib_id"],
        "outcome": "contributed",
        "duplicate": result["duplicate"],
        "contribution_receipt": result["contribution_receipt"],
    }


def record_wave_outcome(
    session_id: str,
    wave_id: str,
    *,
    role: str,
    request_revision: int,
    request_id: str,
    terminal_outcome: str,
    inference_performed: bool,
    failure_stage: str | None = None,
    failure_detail_sha256: str | None = None,
) -> dict:
    """Terminalize one reserved role slot without creating a contribution."""
    session_id = _require_text(session_id, "session_id")
    wave_id = _require_text(wave_id, "wave_id")
    role = _require_text(role, "role")
    request_revision = _require_positive_int(request_revision, "request_revision")
    request_id = _require_sha256(request_id, "request_id")
    terminal_outcome = _require_text(terminal_outcome, "terminal_outcome")
    if terminal_outcome not in _WAVE_FAILURE_OUTCOMES:
        raise ValueError(
            f"terminal_outcome must be one of {sorted(_WAVE_FAILURE_OUTCOMES)}"
        )
    if not isinstance(inference_performed, bool):
        raise ValueError("inference_performed must be a bool")
    pre_inference_outcomes = {"terminal_identity_skipped", "stale_version", "dead_seat"}
    post_inference_outcomes = {
        "inference_failed",
        "validation_failed",
        "graph_commit_failed",
    }
    if terminal_outcome in pre_inference_outcomes and inference_performed:
        raise ValueError(f"{terminal_outcome} cannot claim inference_performed=true")
    if terminal_outcome in post_inference_outcomes and not inference_performed:
        raise ValueError(f"{terminal_outcome} requires inference_performed=true")
    if failure_stage is not None:
        failure_stage = _require_text(failure_stage, "failure_stage")
    if failure_detail_sha256 is not None:
        failure_detail_sha256 = _require_sha256(
            failure_detail_sha256, "failure_detail_sha256"
        )
    state = (
        terminal_outcome
        if terminal_outcome in {"cancelled", "superseded"}
        else "failed"
    )
    outcome_fingerprint = _canonical_sha256(
        {
            "request_id": request_id,
            "state": state,
            "terminal_outcome": terminal_outcome,
            "inference_performed": inference_performed,
            "failure_stage": failure_stage,
            "failure_detail_sha256": failure_detail_sha256,
        }
    )

    wave_snapshot = read_wave(session_id, wave_id)
    slot_snapshot = next(
        (
            slot
            for slot in wave_snapshot["slots"]
            if slot["role"] == role and slot["request_revision"] == request_revision
        ),
        None,
    )
    if slot_snapshot is None:
        raise WaveStateError("unknown_role", "the wave role slot does not exist")
    if slot_snapshot.get("request_id") != request_id:
        raise WaveIdentityConflictError("the role slot is not owned by this request")
    if slot_snapshot["state"] in _WAVE_TERMINAL_STATES:
        if slot_snapshot.get("outcome_fingerprint") != outcome_fingerprint:
            raise WaveIdentityConflictError(
                "the request already has a different terminal graph outcome"
            )
        outcome_record = _validated_outcome_record(
            wave=wave_snapshot, slot=slot_snapshot
        )
        return {
            "session_id": session_id,
            "wave_id": wave_id,
            "role": role,
            "request_id": request_id,
            "state": slot_snapshot["state"],
            "outcome": slot_snapshot["terminal_outcome"],
            "duplicate": True,
            "inference_performed": slot_snapshot["inference_performed"],
            "outcome_record": outcome_record,
        }
    if slot_snapshot["state"] not in {"pending", "claimed"}:
        raise WaveStateError(slot_snapshot["state"], "role slot cannot be terminalized")
    if slot_snapshot["state"] == "pending" and inference_performed:
        raise WaveStateError(
            "inference_not_authorized",
            "a pending request cannot claim that inference was performed",
        )
    request_identity = _validated_request_identity(
        wave=wave_snapshot, slot=slot_snapshot
    )
    if slot_snapshot["state"] == "claimed":
        _validated_claim_observation(
            slot_snapshot.get("claim_observation"), request_identity
        )

    def terminalize(tx):
        locked = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})<-[:IN_SESSION]-
                     (w:DCMWave {session_id:$sid, wave_id:$wid})<-[:IN_WAVE]-
                     (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                     request_revision:$request_revision})
               SET x.wave_tx_epoch = coalesce(x.wave_tx_epoch, 0) + 1,
                   w.wave_tx_epoch = coalesce(w.wave_tx_epoch, 0) + 1,
                   z.slot_tx_epoch = coalesce(z.slot_tx_epoch, 0) + 1
               RETURN x.status AS session_status, properties(w) AS wave,
                      properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
        ).single()
        if locked is None:
            raise WaveStateError("unknown_role", "the wave role slot does not exist")
        slot = dict(locked["slot"])
        wave = dict(locked["wave"])
        if slot.get("request_id") != request_id:
            raise WaveIdentityConflictError(
                "the role slot is not owned by this request"
            )
        if slot["state"] in _WAVE_TERMINAL_STATES:
            if slot.get("outcome_fingerprint") != outcome_fingerprint:
                raise WaveIdentityConflictError(
                    "the request already has a different terminal graph outcome"
                )
            raise _IdempotentWaveReplay({"slot": slot, "duplicate": True})
        if slot["state"] not in {"pending", "claimed"}:
            raise WaveStateError(slot["state"], "role slot cannot be terminalized")
        if slot["state"] == "pending" and inference_performed:
            raise WaveStateError(
                "inference_not_authorized",
                "a pending request cannot claim that inference was performed",
            )
        request_identity_json = slot.get("request_identity_json")
        if request_identity_json is None:
            raise WaveIdentityConflictError(
                "reserved request has no frozen request identity"
            )
        request_identity = json.loads(request_identity_json)
        slot["request_identity"] = request_identity
        wave["parent_edge_contribution_ids"] = sorted(
            row["contrib_id"]
            for row in tx.run(
                """MATCH (:DCMWave {session_id:$sid, wave_id:$wid})-[:HAS_PARENT]->
                     (c:DCMContribution)
                   RETURN c.contrib_id AS contrib_id ORDER BY contrib_id""",
                sid=session_id,
                wid=wave_id,
            )
        )
        _validated_request_identity(wave=wave, slot=slot)
        if slot["state"] == "claimed":
            claim_observation_json = slot.get("claim_observation_json")
            claim_observation = (
                json.loads(claim_observation_json)
                if claim_observation_json is not None
                else None
            )
            _validated_claim_observation(claim_observation, request_identity)
        if locked["session_status"] != "open":
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if wave["status"] != "open":
            raise WaveStateError("closed_wave", f"wave {wave_id} is not open")
        outcome_record = _build_outcome_record(
            wave=wave,
            slot=slot,
            terminal_outcome=terminal_outcome,
            inference_performed=inference_performed,
            failure_stage=failure_stage,
            failure_detail_sha256=failure_detail_sha256,
            recorded_by="record_wave_outcome",
        )
        updated = tx.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:$role,
                                      request_revision:$request_revision})
               WHERE z.state IN ['pending', 'claimed'] AND z.request_id=$request_id
               SET z.state=$state, z.terminal_outcome=$terminal_outcome,
                   z.inference_performed=$inference_performed,
                   z.failure_stage=$failure_stage,
                   z.failure_detail_sha256=$failure_detail_sha256,
                   z.outcome_record_json=$outcome_record_json,
                   z.outcome_record_sha256=$outcome_record_sha256,
                   z.outcome_recorded_by='record_wave_outcome',
                   z.outcome_fingerprint=$outcome_fingerprint, z.updated=$now
               RETURN properties(z) AS slot""",
            sid=session_id,
            wid=wave_id,
            role=role,
            request_revision=request_revision,
            request_id=request_id,
            state=state,
            terminal_outcome=terminal_outcome,
            inference_performed=inference_performed,
            failure_stage=failure_stage,
            failure_detail_sha256=failure_detail_sha256,
            outcome_record_json=_canonical_json(outcome_record),
            outcome_record_sha256=outcome_record["outcome_record_sha256"],
            outcome_fingerprint=outcome_fingerprint,
            now=time.time(),
        ).single()
        if updated is None:
            raise WaveIdentityConflictError(
                "the role slot changed before terminalization"
            )
        return {"slot": dict(updated["slot"]), "duplicate": False}

    try:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            result = session.execute_write(terminalize)
    except _IdempotentWaveReplay as replay:
        result = replay.result
    latest = read_wave(session_id, wave_id)
    slot = next(
        item
        for item in latest["slots"]
        if item["role"] == role and item["request_revision"] == request_revision
    )
    outcome_record = _validated_outcome_record(wave=latest, slot=slot)
    return {
        "session_id": session_id,
        "wave_id": wave_id,
        "role": role,
        "request_id": request_id,
        "state": slot["state"],
        "outcome": slot["terminal_outcome"],
        "duplicate": result["duplicate"],
        "inference_performed": slot["inference_performed"],
        "outcome_record": outcome_record,
    }


def close_wave(
    session_id: str, wave_id: str, *, superseded_by_prompt_revision: int | None = None
) -> dict:
    """Close one terminal wave, optionally recording a newer prompt revision."""
    session_id = _require_text(session_id, "session_id")
    wave_id = _require_text(wave_id, "wave_id")
    wave_snapshot = read_wave(session_id, wave_id)
    if superseded_by_prompt_revision is not None:
        superseded_by_prompt_revision = _require_positive_int(
            superseded_by_prompt_revision, "superseded_by_prompt_revision"
        )
        if superseded_by_prompt_revision <= wave_snapshot["prompt_revision"]:
            raise ValueError(
                "superseded_by_prompt_revision must exceed the wave prompt revision"
            )
    if wave_snapshot["status"] == "closed":
        if superseded_by_prompt_revision is not None and (
            wave_snapshot.get("close_outcome") != "superseded_revision"
            or wave_snapshot.get("superseded_by_prompt_revision")
            != superseded_by_prompt_revision
        ):
            raise WaveIdentityConflictError(
                "closed wave cannot be relabelled with another prompt revision"
            )
        return wave_snapshot

    def close(tx):
        locked = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})<-[:IN_SESSION]-
                     (w:DCMWave {session_id:$sid, wave_id:$wid})
               SET x.wave_tx_epoch = coalesce(x.wave_tx_epoch, 0) + 1,
                   w.wave_tx_epoch = coalesce(w.wave_tx_epoch, 0) + 1
               RETURN x.status AS session_status, x.active_wave_id AS active_wave_id,
                      properties(w) AS wave""",
            sid=session_id,
            wid=wave_id,
        ).single()
        if locked is None:
            raise ValueError(f"no DCM wave {wave_id} in session {session_id}")
        wave = dict(locked["wave"])
        if wave["status"] == "closed":
            raise _IdempotentWaveReplay({"already_closed": True})
        if locked["session_status"] != "open":
            raise WaveStateError("closed_session", f"session {session_id} is not open")
        if locked["active_wave_id"] != wave_id:
            raise WaveStateError(
                "closed_wave", f"wave {wave_id} is not the active wave"
            )
        slot_rows = list(
            tx.run(
                """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid})-[r:IN_WAVE]->
                     (:DCMWave {session_id:$sid, wave_id:$wid})
               RETURN properties(z) AS slot, count(r) AS wave_links,
                      z.role AS role ORDER BY role""",
                sid=session_id,
                wid=wave_id,
            )
        )
        slots = [dict(row["slot"]) for row in slot_rows]
        if any(row["wave_links"] != 1 for row in slot_rows):
            raise WaveStateError(
                "invalid_slot_state",
                "a role slot does not have exactly one wave relationship",
            )
        required_members = json.loads(wave["required_members_json"])
        observed_members = sorted(
            (
                _slot_member(slot, wave.get("request_contract"))
                for slot in slots
            ),
            key=lambda member: member["role"],
        )
        if observed_members != required_members:
            raise WaveStateError(
                "invalid_slot_state",
                "slot seat/role membership differs from the immutable roster",
            )
        if _canonical_sha256(observed_members) != wave["membership_sha256"]:
            raise WaveStateError(
                "invalid_slot_state", "immutable membership digest is invalid"
            )
        parent_rows = [
            dict(row)
            for row in tx.run(
                """MATCH (:DCMWave {session_id:$sid, wave_id:$wid})-[:HAS_PARENT]->
                     (c:DCMContribution)
                   RETURN properties(c) AS contribution ORDER BY c.contrib_id""",
                sid=session_id,
                wid=wave_id,
            )
        ]
        parent_contributions = [row["contribution"] for row in parent_rows]
        wave["parent_edge_contributions"] = parent_contributions
        wave["parent_edge_contribution_ids"] = [
            contribution["contrib_id"] for contribution in parent_contributions
        ]
        if (
            list(wave["parent_contribution_ids"])
            != wave["parent_edge_contribution_ids"]
            or _canonical_sha256(wave["parent_edge_contribution_ids"])
            != wave["parent_frontier_sha256"]
        ):
            raise WaveFrontierMismatchError(
                "wave parent properties differ from graph relationships"
            )
        if [slot for slot in slots if slot["state"] == "claimed"]:
            raise WaveStateError(
                "inflight_requests", "claimed requests must terminalize first"
            )
        unexpected = sorted(
            {
                slot["state"]
                for slot in slots
                if slot["state"] not in _WAVE_TERMINAL_STATES | {"pending"}
            }
        )
        if unexpected:
            raise WaveStateError(
                "invalid_slot_state", f"unexpected slot states: {unexpected}"
            )
        for slot in slots:
            if slot["state"] == "pending":
                request_id = slot.get("request_id")
                request_identity_json = slot.get("request_identity_json")
                if request_id is None and request_identity_json is None:
                    continue
                if request_id is None or request_identity_json is None:
                    raise WaveIdentityConflictError(
                        "pending request has incomplete frozen request identity"
                    )
                validation_slot = dict(slot)
                validation_slot["request_identity"] = json.loads(request_identity_json)
                _validated_request_identity(wave=wave, slot=validation_slot)
                continue
            if slot["state"] not in _WAVE_TERMINAL_STATES - {"contributed"}:
                continue
            validation_slot = dict(slot)
            for source, target in (
                ("request_identity_json", "request_identity"),
                ("claim_observation_json", "claim_observation"),
                ("outcome_record_json", "outcome_record"),
            ):
                value = validation_slot.pop(source, None)
                if value is not None:
                    validation_slot[target] = json.loads(value)
            _validated_outcome_record(wave=wave, slot=validation_slot)

        contribution_rows = list(
            tx.run(
                """MATCH (c:DCMContribution)-[iw:IN_WAVE]->
                     (w:DCMWave {session_id:$sid, wave_id:$wid})
               OPTIONAL MATCH (c)-[fs:FILLS_SLOT]->
                     (z:DCMWaveSlot {session_id:$sid, wave_id:$wid})-[:IN_WAVE]->(w)
               RETURN properties(c) AS contribution,
                      count(DISTINCT iw) AS wave_links,
                      count(DISTINCT fs) AS slot_links,
                      [item IN collect(DISTINCT z) WHERE item IS NOT NULL |
                       properties(item)] AS slots""",
                sid=session_id,
                wid=wave_id,
            )
        )
        orphan_slot_links = tx.run(
            """MATCH (c:DCMContribution)-[:FILLS_SLOT]->
                     (:DCMWaveSlot {session_id:$sid, wave_id:$wid})-[:IN_WAVE]->
                     (w:DCMWave {session_id:$sid, wave_id:$wid})
               WHERE NOT EXISTS { MATCH (c)-[:IN_WAVE]->(w) }
               RETURN count(c) AS count""",
            sid=session_id,
            wid=wave_id,
        ).single()["count"]
        if orphan_slot_links:
            raise WaveStateError(
                "invalid_slot_state",
                "a contribution fills a slot without belonging to the wave",
            )
        contributions_by_role = {}
        for row in contribution_rows:
            contribution = dict(row["contribution"])
            if (
                row["wave_links"] != 1
                or row["slot_links"] != 1
                or len(row["slots"]) != 1
            ):
                raise WaveStateError(
                    "invalid_slot_state",
                    "a contribution does not have exactly one wave and one slot relationship",
                )
            linked_slot = dict(row["slots"][0])
            fields_match = (
                contribution.get("session_id") == session_id
                and contribution.get("wave_id") == wave_id
                and contribution.get("role") == linked_slot["role"]
                and contribution.get("request_revision")
                == linked_slot["request_revision"]
                and contribution.get("request_id") == linked_slot.get("request_id")
                and contribution.get("contrib_id") == linked_slot.get("contrib_id")
            )
            if not fields_match:
                raise WaveIdentityConflictError(
                    "contribution properties differ from its graph-bound role slot"
                )
            request_identity_json = linked_slot.pop("request_identity_json", None)
            if request_identity_json is not None:
                linked_slot["request_identity"] = json.loads(request_identity_json)
            claim_observation_json = linked_slot.pop("claim_observation_json", None)
            if claim_observation_json is not None:
                linked_slot["claim_observation"] = json.loads(claim_observation_json)
            structured_content_json = contribution.pop("structured_content_json", None)
            if structured_content_json is not None:
                contribution["structured_content"] = json.loads(structured_content_json)
            observed_execution_json = contribution.pop("observed_execution_json", None)
            if observed_execution_json is not None:
                contribution["observed_execution"] = json.loads(observed_execution_json)
            receipt_json = contribution.pop("contribution_receipt_json", None)
            if receipt_json is not None:
                contribution["contribution_receipt"] = json.loads(receipt_json)
            _validated_contribution_receipt(
                wave=wave, slot=linked_slot, contribution=contribution
            )
            contributions_by_role.setdefault(linked_slot["role"], []).append(
                contribution
            )

        for slot in slots:
            linked = contributions_by_role.get(slot["role"], [])
            if slot["state"] == "contributed" and len(linked) != 1:
                raise WaveStateError(
                    "invalid_slot_state",
                    "contributed slot lacks exactly one contribution",
                )
            if slot["state"] != "contributed" and linked:
                raise WaveStateError(
                    "invalid_slot_state", "non-contributed slot has a contribution"
                )

        pending_updates = []
        pending_outcome = "superseded" if superseded_by_prompt_revision else "missing"
        pending_state = "superseded" if superseded_by_prompt_revision else "missing"
        pending_stage = (
            "prompt_revision" if superseded_by_prompt_revision else "wave_close"
        )
        pending_detail_sha256 = _canonical_sha256(
            {
                "reason": (
                    "newer prompt revision superseded this wave"
                    if superseded_by_prompt_revision
                    else "required seat had no terminal request outcome"
                ),
                "superseded_by_prompt_revision": superseded_by_prompt_revision,
            }
        )
        for slot in slots:
            if slot["state"] != "pending":
                continue
            outcome_record = _build_outcome_record(
                wave=wave,
                slot=slot,
                terminal_outcome=pending_outcome,
                inference_performed=False,
                failure_stage=pending_stage,
                failure_detail_sha256=pending_detail_sha256,
                recorded_by="close_wave",
            )
            outcome_fingerprint = _canonical_sha256(
                {
                    "request_id": slot.get("request_id"),
                    "state": pending_state,
                    "terminal_outcome": pending_outcome,
                    "inference_performed": False,
                    "failure_stage": pending_stage,
                    "failure_detail_sha256": outcome_record["failure_detail_sha256"],
                }
            )
            pending_updates.append(
                {
                    "role": slot["role"],
                    "state": pending_state,
                    "terminal_outcome": pending_outcome,
                    "failure_stage": pending_stage,
                    "outcome_record_json": _canonical_json(outcome_record),
                    "outcome_record_sha256": outcome_record["outcome_record_sha256"],
                    "outcome_fingerprint": outcome_fingerprint,
                }
            )
        if pending_updates:
            tx.run(
                """UNWIND $updates AS update
                   MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                         role:update.role, state:'pending'})
                   SET z.state=update.state, z.terminal_outcome=update.terminal_outcome,
                       z.inference_performed=false, z.failure_stage=update.failure_stage,
                       z.failure_detail_sha256=$failure_detail_sha256,
                       z.outcome_record_json=update.outcome_record_json,
                       z.outcome_record_sha256=update.outcome_record_sha256,
                       z.outcome_recorded_by='close_wave',
                       z.outcome_fingerprint=update.outcome_fingerprint, z.updated=$now""",
                sid=session_id,
                wid=wave_id,
                updates=pending_updates,
                failure_detail_sha256=pending_detail_sha256,
                now=time.time(),
            ).consume()

        states = [
            pending_state if slot["state"] == "pending" else slot["state"]
            for slot in slots
        ]
        completion_frontier = sorted(
            contribution["contrib_id"]
            for contributions in contributions_by_role.values()
            for contribution in contributions
        )
        if superseded_by_prompt_revision is not None:
            close_outcome = "superseded_revision"
        else:
            close_outcome = (
                "complete"
                if states and all(state == "contributed" for state in states)
                else "incomplete_round"
            )
        completion_frontier_sha256 = _canonical_sha256(completion_frontier)
        superseded_marker = superseded_by_prompt_revision or 0
        tx.run(
            """MATCH (x:DCMSession {session_id:$sid})<-[:IN_SESSION]-
                     (w:DCMWave {session_id:$sid, wave_id:$wid})
               SET w.status='closed', w.close_outcome=$close_outcome,
                   w.completion_frontier=$completion_frontier,
                   w.completion_frontier_sha256=$completion_frontier_sha256,
                   w.superseded_by_prompt_revision=$superseded_marker,
                   w.closed=$now,
                   x.active_wave_id=null, x.last_closed_wave_id=$wid""",
            sid=session_id,
            wid=wave_id,
            close_outcome=close_outcome,
            completion_frontier=completion_frontier,
            completion_frontier_sha256=completion_frontier_sha256,
            superseded_marker=superseded_marker,
            now=time.time(),
        ).consume()
        return {"already_closed": False}

    try:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            session.execute_write(close)
    except _IdempotentWaveReplay:
        pass
    closed = read_wave(session_id, wave_id)
    if superseded_by_prompt_revision is not None and (
        closed.get("close_outcome") != "superseded_revision"
        or closed.get("superseded_by_prompt_revision") != superseded_by_prompt_revision
    ):
        raise WaveIdentityConflictError(
            "concurrent close used a different prompt supersession identity"
        )
    return closed


def verify_wave_coordination(session_id: str, wave_id: str | None = None) -> dict:
    """Audit exact parent-frontier claims without treating same-wave siblings as parents."""
    session_id = _require_text(session_id, "session_id")
    if wave_id is None:
        with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
            wave_ids = [
                row["wave_id"]
                for row in session.run(
                    """MATCH (w:DCMWave {session_id:$sid})
                   RETURN w.wave_id AS wave_id ORDER BY w.created, w.wave_id""",
                    sid=session_id,
                )
            ]
        results = [verify_wave_coordination(session_id, item) for item in wave_ids]
        return {
            "session_id": session_id,
            "waves": results,
            "coordinated": bool(results)
            and all(item["coordinated"] for item in results),
        }
    wave_id = _require_text(wave_id, "wave_id")
    wave = read_wave(session_id, wave_id)
    required_roles = list(wave["required_roles"])
    required_members = json.loads(wave["required_members_json"])
    expected_parents = list(wave["parent_contribution_ids"])
    frontier_violations = []
    sequential_parent_violations = []
    duplicate_slot_violations = []
    receipt_violations = []
    relationship_violations = []
    slots_by_role = {}
    for slot in wave["slots"]:
        slots_by_role.setdefault(slot["role"], []).append(slot)
    for role, slots in slots_by_role.items():
        if len(slots) != 1:
            duplicate_slot_violations.append({"role": role, "count": len(slots)})
    if sorted(slots_by_role) != required_roles:
        duplicate_slot_violations.append(
            {
                "membership_expected": required_roles,
                "membership_observed": sorted(slots_by_role),
            }
        )
    observed_members = sorted(
        (
            _slot_member(slots[0], wave.get("request_contract"))
            for role, slots in slots_by_role.items()
            if len(slots) == 1
        ),
        key=lambda member: member["role"],
    )
    if (
        observed_members != required_members
        or _canonical_sha256(observed_members) != wave["membership_sha256"]
    ):
        duplicate_slot_violations.append(
            {
                "membership_expected": required_members,
                "membership_observed": observed_members,
            }
        )
    contributions_by_role = {}
    for contribution in wave["contributions"]:
        contributions_by_role.setdefault(contribution["role"], []).append(contribution)
        violations = []
        if list(contribution.get("claimed_peers") or []) != expected_parents:
            violations.append("claimed_peers")
        if list(contribution.get("peers_present") or []) != expected_parents:
            violations.append("peers_present")
        if contribution.get("parent_frontier_sha256") != wave["parent_frontier_sha256"]:
            violations.append("parent_frontier_sha256")
        if contribution.get("request_revision") != wave["request_revision"]:
            violations.append("request_revision")
        if violations:
            frontier_violations.append(
                {
                    "contrib_id": contribution["contrib_id"],
                    "role": contribution["role"],
                    "fields": violations,
                }
            )
        slot = slots_by_role.get(contribution["role"], [{}])[0]
        try:
            _validated_contribution_receipt(
                wave=wave, slot=slot, contribution=contribution
            )
        except WaveStateError as exc:
            receipt_violations.append(
                {
                    "contrib_id": contribution["contrib_id"],
                    "fields": [exc.outcome],
                }
            )
    for role, contributions in contributions_by_role.items():
        if len(contributions) != 1:
            duplicate_slot_violations.append(
                {"role": role, "contribution_count": len(contributions)}
            )
    for role, slots in slots_by_role.items():
        slot = slots[0]
        count = len(contributions_by_role.get(role, []))
        if slot["state"] == "contributed" and count != 1:
            duplicate_slot_violations.append(
                {"role": role, "state": "contributed", "contribution_count": count}
            )
        if slot["state"] != "contributed" and count:
            duplicate_slot_violations.append(
                {"role": role, "state": slot["state"], "contribution_count": count}
            )
        if slot["state"] == "contributed":
            contribution = contributions_by_role.get(role, [{}])[0]
            if slot.get("contribution_receipt_sha256") != contribution.get(
                "contribution_receipt_sha256"
            ):
                receipt_violations.append(
                    {"role": role, "fields": ["slot_receipt_sha256"]}
                )
        elif slot["state"] in _WAVE_TERMINAL_STATES:
            try:
                _validated_outcome_record(wave=wave, slot=slot)
            except WaveStateError as exc:
                receipt_violations.append({"role": role, "fields": [exc.outcome]})

    with _ensure_wave_schema().session(database=DCM_NEO4J_DATABASE) as session:
        relation_rows = list(
            session.run(
                """MATCH (c:DCMContribution)-[iw:IN_WAVE]->
                     (w:DCMWave {session_id:$sid, wave_id:$wid})
               OPTIONAL MATCH (c)-[fs:FILLS_SLOT]->
                     (z:DCMWaveSlot {session_id:$sid, wave_id:$wid})-[:IN_WAVE]->(w)
               RETURN c.contrib_id AS contrib_id, count(DISTINCT iw) AS wave_links,
                      count(DISTINCT fs) AS slot_links,
                      [item IN collect(DISTINCT z.role) WHERE item IS NOT NULL | item]
                      AS slot_roles""",
                sid=session_id,
                wid=wave_id,
            )
        )
        orphan_slot_links = session.run(
            """MATCH (c:DCMContribution)-[:FILLS_SLOT]->
                     (:DCMWaveSlot {session_id:$sid, wave_id:$wid})-[:IN_WAVE]->
                     (w:DCMWave {session_id:$sid, wave_id:$wid})
               WHERE NOT EXISTS { MATCH (c)-[:IN_WAVE]->(w) }
               RETURN count(c) AS count""",
            sid=session_id,
            wid=wave_id,
        ).single()["count"]
    for row in relation_rows:
        contribution = next(
            (
                item
                for item in wave["contributions"]
                if item["contrib_id"] == row["contrib_id"]
            ),
            None,
        )
        if (
            contribution is None
            or row["wave_links"] != 1
            or row["slot_links"] != 1
            or row["slot_roles"] != [contribution["role"]]
        ):
            relationship_violations.append(
                {
                    "contrib_id": row["contrib_id"],
                    "wave_links": row["wave_links"],
                    "slot_links": row["slot_links"],
                    "slot_roles": row["slot_roles"],
                }
            )
    if orphan_slot_links:
        relationship_violations.append({"orphan_slot_links": orphan_slot_links})

    if wave["parent_edge_contribution_ids"] != expected_parents:
        sequential_parent_violations.append(
            "HAS_PARENT edges differ from stored frontier"
        )
    if _canonical_sha256(expected_parents) != wave["parent_frontier_sha256"]:
        sequential_parent_violations.append("stored parent frontier digest is invalid")
    try:
        prompt_sha256 = canonical_prompt_sha256(
            json.loads(wave["prompt_messages_json"]),
            list(wave["attachment_evidence_digests"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        prompt_sha256 = None
    if prompt_sha256 != wave["prompt_sha256"]:
        sequential_parent_violations.append("stored prompt material digest is invalid")
    if (
        wave.get("graph_uri") != DCM_NEO4J_URI
        or wave.get("graph_database") != DCM_NEO4J_DATABASE
    ):
        sequential_parent_violations.append(
            "wave graph identity differs from executing target"
        )
    parent_wave_id = wave.get("parent_wave_id")
    if parent_wave_id is None:
        if expected_parents:
            sequential_parent_violations.append("first wave has non-empty parents")
        if wave["round"] != 1 or wave["phase"] != "independent":
            sequential_parent_violations.append("first wave is not round 1 independent")
        if wave.get("transition") != "first":
            sequential_parent_violations.append(
                "first wave transition marker is invalid"
            )
    else:
        try:
            parent = read_wave(session_id, parent_wave_id)
            if parent["status"] != "closed":
                sequential_parent_violations.append("parent wave is not closed")
            if (
                parent["required_members_json"] != wave["required_members_json"]
                or parent["membership_sha256"] != wave["membership_sha256"]
                or parent.get("request_contract") != wave.get("request_contract")
            ):
                sequential_parent_violations.append(
                    "request contract or membership changed across waves"
                )
            critique_transition = (
                wave.get("transition") == "critique"
                and parent.get("close_outcome") == "complete"
                and parent["phase"] == "independent"
                and wave["phase"] == "critique"
                and wave["round"] == parent["round"]
                and wave["prompt_revision"] == parent["prompt_revision"]
                and list(parent.get("completion_frontier") or []) == expected_parents
            )
            amendment_transition = (
                wave.get("transition") == "prompt_amendment"
                and parent.get("close_outcome") in {"complete", "superseded_revision"}
                and wave["phase"] == "independent"
                and wave["round"] == parent["round"]
                and wave["prompt_revision"] > parent["prompt_revision"]
                and not expected_parents
                and (
                    parent.get("close_outcome") == "complete"
                    or wave["prompt_revision"]
                    == parent.get("superseded_by_prompt_revision")
                )
            )
            valid_transition = critique_transition or amendment_transition
            if not valid_transition:
                sequential_parent_violations.append(
                    "invalid phase/round/prompt lineage"
                )
        except ValueError:
            sequential_parent_violations.append("parent wave does not exist")

    actual_contribution_ids = sorted(
        contribution["contrib_id"] for contribution in wave["contributions"]
    )
    if wave["status"] == "closed":
        completion_frontier = list(wave.get("completion_frontier") or [])
        if completion_frontier != actual_contribution_ids or wave.get(
            "completion_frontier_sha256"
        ) != _canonical_sha256(completion_frontier):
            sequential_parent_violations.append("closed completion frontier is invalid")

    slot_states = {
        role: slots[0]["state"]
        for role, slots in slots_by_role.items()
        if len(slots) == 1
    }
    complete = (
        wave["status"] == "closed"
        and wave.get("close_outcome") == "complete"
        and sorted(slot_states) == required_roles
        and all(state == "contributed" for state in slot_states.values())
    )
    superseded = (
        wave["status"] == "closed"
        and wave.get("close_outcome") == "superseded_revision"
        and isinstance(wave.get("superseded_by_prompt_revision"), int)
        and wave["superseded_by_prompt_revision"] > wave["prompt_revision"]
        and sorted(slot_states) == required_roles
        and all(state in _WAVE_TERMINAL_STATES for state in slot_states.values())
    )
    coordinated = (complete or superseded) and not (
        frontier_violations
        or sequential_parent_violations
        or duplicate_slot_violations
        or receipt_violations
        or relationship_violations
    )
    return {
        "session_id": session_id,
        "wave_id": wave_id,
        "required_roles": required_roles,
        "slot_states": slot_states,
        "parent_contribution_ids": expected_parents,
        "frontier_violations": frontier_violations,
        "sequential_parent_violations": sequential_parent_violations,
        "duplicate_slot_violations": duplicate_slot_violations,
        "receipt_violations": receipt_violations,
        "relationship_violations": relationship_violations,
        "complete": complete,
        "superseded": superseded,
        "coordinated": coordinated,
    }


def verify_coordination(session_id: str) -> dict:
    """Honesty gate: did each later contributor at least CLAIM to read the peers present to it?

    Tests `claimed_peers` (the author's own assertion), NOT the server's presence stamp — the
    first cut tested the auto-stamp and was therefore circular (audit Finding B: it could never
    flag a silo). A contribution is a silo if earlier peers were present but its claimed_peers
    omits them: the author did not even claim to read what the CAS gate put in front of it.

    Honest scope: this catches non-claiming silos (incl. the audit's claimed=[] case). It does
    NOT prove semantic incorporation and cannot catch an author that lies by claiming reads it
    did not do — that is unprovable from the graph. The structural guarantee (fetch-before-
    commit) comes from the CAS gate; this is the read-claim audit layered on top.
    """
    sess = read_session(session_id)
    cs = sess["contributions"]
    silos = []
    for i, c in enumerate(cs):
        earlier = {p["contrib_id"] for p in cs[:i]}
        claimed = set(c.get("claimed_peers") or [])
        ignored = earlier - claimed
        if earlier and ignored:
            silos.append(
                {"role": c["role"], "seq": c.get("seq"), "ignored_count": len(ignored)}
            )
    return {
        "contributions": len(cs),
        "opening": cs[0]["role"] if cs else None,
        "built_on_peers": [
            c["role"]
            for i, c in enumerate(cs)
            if i > 0
            and (set(c.get("claimed_peers") or []) & {p["contrib_id"] for p in cs[:i]})
        ],
        "silo_violations": silos,
        "coordinated": len(cs) > 1 and not silos,
    }


def open_concerns(session_id: str) -> list[dict]:
    """Return block-severity concerns that lack a valid closing resolution.

    Closure is a projection over the append log: a resolution closes only the concern it names
    in `about`, ESCALATE does not close, FIX-VERIFIED/FALSE-POSITIVE need non-empty evidence,
    and safety veto concerns can be closed only by FIX-VERIFIED or ACCEPTED-RISK. This is graph
    state, not a semantic claim that the evidence is true.
    """
    session_id = _require_text(session_id, "session_id")
    with _db().session(database=DCM_NEO4J_DATABASE) as session:
        owner = session.run(
            """MATCH (x:DCMSession {session_id:$sid})
               RETURN x.coordination_mode AS coordination_mode""",
            sid=session_id,
        ).single()
        if owner is None:
            raise ValueError(f"no DCM session {session_id}")
        if owner["coordination_mode"] == "wave":
            rows = session.run(
                """MATCH (w:DCMWave {session_id:$sid, status:'closed',
                                       close_outcome:'complete'})
                   WITH max(w.prompt_revision) AS latest_revision
                   MATCH (c:DCMContribution)-[:IN_WAVE]->
                         (:DCMWave {session_id:$sid, prompt_revision:latest_revision,
                                    status:'closed', close_outcome:'complete'})
                   RETURN properties(c) AS contribution ORDER BY c.created, c.contrib_id""",
                sid=session_id,
            )
        else:
            rows = session.run(
                """MATCH (c:DCMContribution)-[:IN]->
                         (:DCMSession {session_id:$sid})
                   RETURN properties(c) AS contribution ORDER BY c.seq, c.created""",
                sid=session_id,
            )
        contributions = [dict(row["contribution"]) for row in rows]
    return _project_open_concerns(contributions)


def publish_final(session_id: str, final: str) -> None:
    """Close the session only when the append-log concern projection is clear.

    The DISTILLED final is what's eligible to flow to ISMA (not the sausage). read_session()
    surfaces it as `final`. Honest scope: this enforces that block concerns have valid typed
    resolutions with required evidence refs; it does not prove the external evidence itself.
    """
    session_id = _require_text(session_id, "session_id")
    final = _require_text(final, "final")
    wave_control = None
    wave_control_sha256 = None
    with _db().session(database=DCM_NEO4J_DATABASE) as session:
        mode_record = session.run(
            """MATCH (x:DCMSession {session_id:$sid})
               RETURN x.coordination_mode AS coordination_mode""",
            sid=session_id,
        ).single()
    if mode_record is None:
        raise ValueError(f"no DCM session {session_id}")
    coordination_mode = mode_record["coordination_mode"] or "linear"
    if coordination_mode == "wave":
        verification = verify_wave_coordination(session_id)
        if not verification["coordinated"]:
            raise WaveStateError(
                "unverified_wave",
                "all terminal waves must pass wave coordination verification",
            )
        wave_control = [
            {
                "wave_id": item["wave_id"],
                "wave_fingerprint": item["wave_fingerprint"],
                "wave_tx_epoch": item.get("wave_tx_epoch", 0),
                "status": item["status"],
                "close_outcome": item.get("close_outcome"),
                "completion_frontier_sha256": item.get("completion_frontier_sha256"),
            }
            for item in (
                read_wave(session_id, result["wave_id"])
                for result in verification["waves"]
            )
        ]
        wave_control.sort(key=lambda item: item["wave_id"])
        wave_control_sha256 = _canonical_sha256(wave_control)

    def finalize(tx):
        owner = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               SET x.coordination_tx_epoch = coalesce(x.coordination_tx_epoch, 0) + 1
               RETURN properties(x) AS session""",
            sid=session_id,
        ).single()
        if owner is None:
            raise ValueError(f"no DCM session {session_id}")
        current = dict(owner["session"])
        if current["status"] != "open" or current.get("final") is not None:
            raise WaveStateError(
                "closed_session", f"session {session_id} is already terminal"
            )
        if (current.get("coordination_mode") or "linear") != coordination_mode:
            raise WaveStateError(
                "coordination_mode_conflict", "session mode changed before final"
            )

        if coordination_mode == "wave":
            if current.get("active_wave_id") is not None:
                raise WaveStateError(
                    "inflight_requests", "an active wave cannot be finalized"
                )
            wave_rows = list(
                tx.run(
                    """MATCH (w:DCMWave {session_id:$sid})
                   RETURN w.wave_id AS wave_id, w.wave_fingerprint AS wave_fingerprint,
                          coalesce(w.wave_tx_epoch, 0) AS wave_tx_epoch,
                          w.status AS status, w.close_outcome AS close_outcome,
                          w.completion_frontier_sha256 AS completion_frontier_sha256""",
                    sid=session_id,
                )
            )
            current_control = sorted(
                (dict(row) for row in wave_rows), key=lambda item: item["wave_id"]
            )
            if _canonical_sha256(current_control) != wave_control_sha256:
                raise WaveStateError(
                    "stale_version",
                    "wave state changed after coordination verification",
                )
            latest = tx.run(
                """MATCH (w:DCMWave {session_id:$sid})
                   RETURN properties(w) AS wave
                   ORDER BY w.prompt_revision DESC, w.created DESC LIMIT 1""",
                sid=session_id,
            ).single()
            if (
                latest is None
                or latest["wave"]["status"] != "closed"
                or latest["wave"].get("close_outcome") != "complete"
                or latest["wave"]["phase"] != "critique"
            ):
                raise WaveStateError(
                    "incomplete_round",
                    "latest prompt revision lacks a complete critique wave",
                )
            latest_revision = latest["wave"]["prompt_revision"]
            contribution_rows = tx.run(
                """MATCH (c:DCMContribution)-[:IN_WAVE]->
                         (:DCMWave {session_id:$sid, prompt_revision:$prompt_revision,
                                    status:'closed', close_outcome:'complete'})
                   RETURN properties(c) AS contribution ORDER BY c.created, c.contrib_id""",
                sid=session_id,
                prompt_revision=latest_revision,
            )
        else:
            if current.get("active_wave_id") is not None:
                raise WaveStateError(
                    "coordination_mode_conflict",
                    "linear final cannot close an active wave",
                )
            contribution_rows = tx.run(
                """MATCH (c:DCMContribution)-[:IN]->
                         (:DCMSession {session_id:$sid})
                   RETURN properties(c) AS contribution ORDER BY c.seq, c.created""",
                sid=session_id,
            )

        contributions = [dict(row["contribution"]) for row in contribution_rows]
        clearance_projection = _build_clearance_projection(contributions)
        open_ids = clearance_projection["open_blocking_concern_ids"]
        if open_ids:
            raise UnresolvedConcernsError(open_ids)
        clearance_frontier = sorted(item["contrib_id"] for item in contributions)
        clearance_frontier_sha256 = _canonical_sha256(clearance_frontier)
        if (
            clearance_projection["clearance_frontier_sha256"]
            != clearance_frontier_sha256
        ):
            raise WaveIdentityConflictError(
                "clearance projection differs from the terminal contribution frontier"
            )
        clearance_projection_json = _canonical_json(clearance_projection)
        clearance_projection_sha256 = _canonical_sha256(clearance_projection)
        updated = tx.run(
            """MATCH (x:DCMSession {session_id:$sid})
               WHERE x.status='open' AND x.final IS NULL
               SET x.status='closed', x.final=$final, x.final_sha256=$final_sha256,
                   x.clearance_frontier=$clearance_frontier,
                   x.clearance_frontier_sha256=$clearance_frontier_sha256,
                   x.clearance_projection_json=$clearance_projection_json,
                   x.clearance_projection_sha256=$clearance_projection_sha256,
                   x.finalized_coordination_mode=$coordination_mode, x.closed=$now
               RETURN x.session_id AS session_id""",
            sid=session_id,
            final=final,
            final_sha256=_text_sha256(final),
            clearance_frontier=clearance_frontier,
            clearance_frontier_sha256=clearance_frontier_sha256,
            clearance_projection_json=clearance_projection_json,
            clearance_projection_sha256=clearance_projection_sha256,
            coordination_mode=coordination_mode,
            now=time.time(),
        ).single()
        if updated is None:
            raise WaveStateError(
                "closed_session", f"session {session_id} became terminal"
            )

    with _db().session(database=DCM_NEO4J_DATABASE) as session:
        session.execute_write(finalize)


if __name__ == "__main__":
    import sys

    print(json.dumps(verify_coordination(sys.argv[1]), indent=2))
