"""Taey -> DCM adapter: runs the REAL Taey model (served via soma_proxy on Thor2:8765)
as a first-class mesh expert, under the SAME staleness gate as every other participant.

This is the P2 Taey-on-the-mesh path: an instance of Taey reads peers from the mesh,
reasons AS Taey (the soma_proxy injects Taey's persona + ISMA tools), and contributes —
the adapter owns the read+commit so Taey physically can't bypass the read-before-write
contract (adoption enforced for Taey the way the staleness gate enforces it for code agents).

Same chokepoint as the Claude-Code Task contract + the (future) Chats consult adapter:
every participant type funnels through mesh.contribute(read_version=...).
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import urllib.request
from typing import Any, Callable

import mesh

TAEY_URL = os.environ.get("TAEY_DCM_URL", "http://localhost:8765/v1/chat/completions")  # set to your Taey soma_proxy endpoint
TAEY_MODEL = os.environ.get("TAEY_DCM_MODEL", "/models/taey-phase-combined-v1")


class WaveDispatchInFlightError(RuntimeError):
    """Raised when another live delivery already owns the graph inference claim."""


class WaveRequestExecutionError(RuntimeError):
    """Raised after a failed model transaction has been closed and acknowledged."""

    def __init__(self, receipt: dict):
        self.receipt = receipt
        super().__init__(
            f"wave request ended with {receipt['terminal_outcome']} "
            f"at {receipt['failure_stage']}"
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _text_sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_delivery_text(request: dict, field: str) -> str:
    value = request.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Redis delivery {field} must be a non-empty string")
    return value.strip()


def _delivery_identity(request: dict) -> dict:
    field_sources = {
        "session_id": "dcm_session_id",
        "wave_id": "wave_id",
        "round": "round",
        "phase": "phase",
        "prompt_id": "prompt_id",
        "prompt_revision": "prompt_revision",
        "prompt_sha256": "prompt_sha256",
        "seat_id": "seat_id",
        "role": "role",
        "request_revision": "request_revision",
        "parent_frontier_sha256": "parent_frontier_sha256",
        "process_generation_expected": "process_generation_expected",
        "model_endpoint": "model_endpoint",
        "requested_alias": "requested_alias",
        "model_manifest_sha256": "model_manifest_sha256",
        "model_content_sha256": "model_content_sha256",
        "serving_container_digest": "serving_container_digest",
    }
    identity = {
        target: request.get(source) for target, source in field_sources.items()
    }
    if request.get("request_contract") is not None:
        identity.update(
            {
                "request_contract": request.get("request_contract"),
                "prompt_contract_sha256": request.get("prompt_contract_sha256"),
                "model_identity_receipt_sha256": request.get(
                    "model_identity_receipt_sha256"
                ),
            }
        )
    mesh.canonical_wave_request_id(identity)
    return identity


def _validated_wave_delivery(request: dict, wave: dict) -> tuple[dict, list[str]]:
    if not isinstance(request, dict):
        raise ValueError("Redis delivery must be a dict")
    _required_delivery_text(request, "delivery_id")
    request_id = _required_delivery_text(request, "request_id")
    identity = _delivery_identity(request)
    if identity["session_id"] != wave["session_id"]:
        raise mesh.WaveIdentityConflictError(
            "Redis delivery session differs from the requested graph wave"
        )
    if identity["wave_id"] != wave["wave_id"]:
        raise mesh.WaveIdentityConflictError(
            "Redis delivery wave differs from the requested graph wave"
        )
    if mesh.canonical_wave_request_id(identity) != request_id:
        raise mesh.WaveIdentityConflictError(
            "Redis delivery request_id is not its canonical identity digest"
        )
    matches = [
        slot
        for slot in wave["slots"]
        if slot["role"] == identity["role"]
        and slot["request_revision"] == identity["request_revision"]
    ]
    if len(matches) != 1:
        raise mesh.WaveStateError(
            "unknown_role", "Redis delivery does not name one graph role slot"
        )
    slot = matches[0]
    if slot.get("request_id") != request_id or slot.get("request_identity") != identity:
        raise mesh.WaveIdentityConflictError(
            "Redis delivery differs from the graph-reserved request"
        )
    parents = request.get("parent_contribution_ids")
    if not isinstance(parents, list) or any(
        not isinstance(parent, str) or not parent.strip() for parent in parents
    ):
        raise ValueError(
            "Redis delivery parent_contribution_ids must be a list of non-empty strings"
        )
    if len(parents) != len(set(parents)):
        raise ValueError("Redis delivery parent_contribution_ids contains duplicates")
    parents = sorted(parents)
    if (
        parents != list(wave["parent_contribution_ids"])
        or parents != list(wave["parent_edge_contribution_ids"])
        or request.get("parent_frontier_sha256") != wave["parent_frontier_sha256"]
        or _canonical_sha256(parents) != wave["parent_frontier_sha256"]
    ):
        raise mesh.WaveFrontierMismatchError(
            "Redis delivery differs from the immutable graph frontier"
        )
    return slot, parents


def _transport_receipt(
    request: dict,
    wave: dict,
    slot: dict,
    terminal: dict,
    *,
    emitter_component: str,
    emitter_process_generation: str,
    duplicate_dispatch: bool,
) -> dict:
    request_identity = slot["request_identity"]
    contribution_receipt = terminal.get("contribution_receipt")
    outcome_record = terminal.get("outcome_record")
    if contribution_receipt is not None:
        graph_receipt_sha256 = contribution_receipt["receipt_sha256"]
    elif outcome_record is not None:
        graph_receipt_sha256 = outcome_record["outcome_record_sha256"]
    elif (
        wave.get("session_status") == "failed"
        and wave.get("status") == "closed"
        and wave.get("close_outcome") == "session_failed"
        and terminal.get("outcome") == "session_failed"
        and terminal.get("session_failure_sha256") is not None
        and terminal.get("session_failure_sha256")
        == wave.get("session_failure_sha256")
    ):
        graph_receipt_sha256 = wave["session_failure_sha256"]
    else:
        raise ValueError("terminal result lacks a valid graph receipt")
    if terminal.get("outcome") == "session_failed":
        if slot.get("state") not in {"pending", "claimed"}:
            raise ValueError(
                f"session_failed receipt is invalid for slot in state {slot.get('state')}"
            )
        inference_performed = False if slot.get("state") == "pending" else None
        inference_state = (
            "not_started"
            if slot.get("state") == "pending"
            else "side_effect_uncertain"
        )
    elif terminal.get("outcome") == "contributed":
        inference_performed = True
        inference_state = None
    else:
        inference_performed = terminal.get("inference_performed")
        inference_state = None
    claim_observation = slot.get("claim_observation") or {}
    receipt = {
        "contract": (
            "taey-native-dcm-receipt/v2"
            if request_identity.get("request_contract")
            == "taey-native-dcm-request/v2"
            else "taey-native-dcm-receipt/v1"
        ),
        "receipt_kind": "transport",
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
        "emitter": {
            "component": emitter_component,
            "process_generation": emitter_process_generation,
        },
        "graph": {
            "uri": wave["graph_uri"],
            "database": wave["graph_database"],
        },
        "frontier": {
            "parent_contribution_ids": list(wave["parent_contribution_ids"]),
            "parent_frontier_sha256": wave["parent_frontier_sha256"],
            "claimed_peers": list(wave["parent_contribution_ids"]),
            "peers_present": list(wave["parent_edge_contribution_ids"]),
        },
        "execution": {
            "model_endpoint": request_identity["model_endpoint"],
            "process_generation_expected": request_identity[
                "process_generation_expected"
            ],
            "process_generation_observed": claim_observation.get(
                "process_generation_observed"
            ),
            "requested_alias": request_identity["requested_alias"],
            "served_alias": claim_observation.get("served_alias"),
            "model_manifest_sha256": request_identity["model_manifest_sha256"],
            "model_content_sha256": request_identity["model_content_sha256"],
            "serving_container_digest": request_identity[
                "serving_container_digest"
            ],
        },
        "stage": "terminal_acknowledged",
        "delivery_id": _required_delivery_text(request, "delivery_id"),
        "acknowledgement_id": _canonical_sha256(
            {
                "delivery_id": _required_delivery_text(request, "delivery_id"),
                "request_id": slot["request_id"],
                "terminal_outcome": terminal["outcome"],
                "graph_receipt_sha256": graph_receipt_sha256,
            }
        ),
        "claim_outcome": (
            "duplicate_dispatch" if duplicate_dispatch else "claimed"
        ),
        "terminal_outcome": terminal["outcome"],
        "inference_performed": inference_performed,
        "contrib_id": terminal.get("contrib_id"),
        "contribution_receipt_sha256": (
            contribution_receipt["receipt_sha256"]
            if contribution_receipt is not None
            else None
        ),
        "original_request_id": slot["request_id"] if duplicate_dispatch else None,
        "failure_stage": (
            outcome_record.get("failure_stage")
            if outcome_record is not None
            else "session_failed"
            if terminal.get("outcome") == "session_failed"
            else None
        ),
        "failure_detail_sha256": (
            outcome_record.get("failure_detail_sha256")
            if outcome_record is not None
            else terminal.get("failure_detail_sha256")
            if terminal.get("outcome") == "session_failed"
            else None
        ),
    }
    if terminal.get("outcome") == "session_failed":
        receipt["session_failure_sha256"] = terminal["session_failure_sha256"]
        receipt["inference_state"] = inference_state
    if request_identity.get("request_contract") == "taey-native-dcm-request/v2":
        receipt.update(
            {
                "request_contract": request_identity["request_contract"],
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


def _acknowledge_terminal(
    request: dict,
    terminal: dict,
    acknowledge: Callable[[dict], None],
    *,
    emitter_component: str,
    emitter_process_generation: str,
    duplicate_dispatch: bool,
) -> dict:
    wave = mesh.read_wave(request["dcm_session_id"], request["wave_id"])
    slot, _ = _validated_wave_delivery(request, wave)
    receipt = _transport_receipt(
        request,
        wave,
        slot,
        terminal,
        emitter_component=emitter_component,
        emitter_process_generation=emitter_process_generation,
        duplicate_dispatch=duplicate_dispatch,
    )
    acknowledge(receipt)
    return receipt


def recover_wave_request(
    request: dict,
    *,
    acknowledge: Callable[[dict], None],
    emitter_process_generation: str,
    terminal_outcome: str,
    inference_performed: bool,
    failure_stage: str,
    failure_detail: str,
    emitter_component: str = "taey-council-seat",
    duplicate_dispatch: bool = True,
) -> dict:
    """Close or re-ack one abandoned delivery without authorizing inference."""
    if not callable(acknowledge):
        raise ValueError("acknowledge must be callable")
    if not isinstance(emitter_process_generation, str) or not (
        emitter_process_generation.strip()
    ):
        raise ValueError("emitter_process_generation must be a non-empty string")
    session_id = _required_delivery_text(request, "dcm_session_id")
    wave_id = _required_delivery_text(request, "wave_id")
    wave = mesh.read_wave(session_id, wave_id)
    slot, parents = _validated_wave_delivery(request, wave)
    if slot["state"] in {"contributed", "failed", "missing", "cancelled", "superseded"}:
        terminal = mesh.claim_wave_request(
            session_id,
            wave_id,
            role=slot["role"],
            request_revision=slot["request_revision"],
            request_id=slot["request_id"],
            parent_contribution_ids=parents,
            claim_observation={},
        )
    elif (
        wave.get("session_status") == "failed"
        and wave.get("status") == "closed"
        and wave.get("close_outcome") == "session_failed"
        and slot["state"] in {"pending", "claimed"}
        and wave.get("session_failure") is not None
        and (wave.get("session_failure") or {}).get("terminal_failure_sha256")
        == wave.get("session_failure_sha256")
    ):
        session_failure = wave["session_failure"]
        terminal = {
            "session_id": session_id,
            "wave_id": wave_id,
            "role": slot["role"],
            "request_id": slot["request_id"],
            "state": slot["state"],
            "outcome": "session_failed",
            "duplicate": True,
            "inference_performed": (
                False if slot["state"] == "pending" else None
            ),
            "inference_state": (
                "not_started"
                if slot["state"] == "pending"
                else "side_effect_uncertain"
            ),
            "session_failure": session_failure,
            "session_failure_sha256": session_failure["terminal_failure_sha256"],
            "failure_stage": "session_failed",
            "failure_detail_sha256": session_failure["failure_detail_sha256"],
            "session_status": wave["session_status"],
            "wave_status": wave["status"],
        }
    else:
        terminal = mesh.record_wave_outcome(
            session_id,
            wave_id,
            role=slot["role"],
            request_revision=slot["request_revision"],
            request_id=slot["request_id"],
            terminal_outcome=terminal_outcome,
            inference_performed=inference_performed,
            failure_stage=failure_stage,
            failure_detail_sha256=_text_sha256(failure_detail),
        )
    receipt = _acknowledge_terminal(
        request,
        terminal,
        acknowledge,
        emitter_component=emitter_component,
        emitter_process_generation=emitter_process_generation,
        duplicate_dispatch=duplicate_dispatch,
    )
    return {"graph": terminal, "transport_receipt": receipt}


def execute_wave_request(
    request: dict,
    claim_observation: dict,
    *,
    validate_response: Callable[[Any, dict], dict],
    acknowledge: Callable[[dict], None],
    invoke: Callable[[dict], Any] | None = None,
    system_extra: str | None = None,
    user: str | None = None,
    max_tokens: int = 1500,
    timeout: int = 300,
    emitter_component: str = "dcm-adapter",
    emitter_process_generation: str | None = None,
    abort_on: tuple[type[Exception], ...] = (),
) -> dict:
    """Execute one claimed Redis delivery against one immutable additive-wave slot."""
    if not isinstance(claim_observation, dict):
        raise ValueError("claim_observation must be a dict")
    if not callable(validate_response):
        raise ValueError("validate_response must be callable")
    if not callable(acknowledge):
        raise ValueError("acknowledge must be callable")
    if invoke is None:
        if not isinstance(system_extra, str) or not system_extra.strip():
            raise ValueError("system_extra is required without an invoke callback")
        if not isinstance(user, str) or not user.strip():
            raise ValueError("user is required without an invoke callback")
    elif not callable(invoke):
        raise ValueError("invoke must be callable when provided")
    session_id = _required_delivery_text(request, "dcm_session_id")
    wave_id = _required_delivery_text(request, "wave_id")
    wave = mesh.read_wave(session_id, wave_id)
    slot, parents = _validated_wave_delivery(request, wave)
    process_generation = emitter_process_generation or claim_observation.get(
        "process_generation_observed"
    )
    if not isinstance(process_generation, str) or not process_generation.strip():
        raise ValueError("emitter_process_generation must be a non-empty string")
    claim = mesh.claim_wave_request(
        session_id,
        wave_id,
        role=slot["role"],
        request_revision=slot["request_revision"],
        request_id=slot["request_id"],
        parent_contribution_ids=parents,
        claim_observation=claim_observation,
    )
    if not claim["inference_authorized"]:
        if claim["outcome"] == "duplicate_inflight":
            raise WaveDispatchInFlightError(
                f"request {slot['request_id']} already has a live inference claim"
            )
        receipt = _acknowledge_terminal(
            request,
            claim,
            acknowledge,
            emitter_component=emitter_component,
            emitter_process_generation=process_generation,
            duplicate_dispatch=True,
        )
        return {"graph": claim, "transport_receipt": receipt}

    stage = "model_request"
    try:
        if invoke is None:
            response = _ask_taey(
                system_extra=system_extra,
                user=user,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        else:
            response = invoke(wave)
        stage = "response_validation"
        structured_content = validate_response(response, wave)
        if not isinstance(structured_content, dict):
            raise ValueError("validate_response must return a dict")
        stage = "graph_commit"
        terminal = mesh.contribute_wave(
            session_id,
            wave_id,
            role=slot["role"],
            request_revision=slot["request_revision"],
            request_id=slot["request_id"],
            structured_content=structured_content,
            claimed_peers=parents,
            observed_execution={
                field: claim_observation[field]
                for field in (
                    "process_generation_observed",
                    "model_endpoint",
                    "served_alias",
                    "model_manifest_sha256",
                    "model_content_sha256",
                    "serving_container_digest",
                )
            },
            emitter={
                "component": emitter_component,
                "process_generation": process_generation,
            },
            kind=request.get("contribution_kind", "contribution"),
            severity=request.get("severity"),
            about=request.get("about"),
            veto=request.get("veto", False),
            disposition=request.get("disposition"),
            evidence_ref=request.get("evidence_ref"),
        )
    except Exception as exc:
        if abort_on and isinstance(exc, abort_on):
            raise
        if stage == "graph_commit":
            recovered = mesh.claim_wave_request(
                session_id,
                wave_id,
                role=slot["role"],
                request_revision=slot["request_revision"],
                request_id=slot["request_id"],
                parent_contribution_ids=parents,
                claim_observation=claim_observation,
            )
            if recovered["outcome"] == "contributed":
                receipt = _acknowledge_terminal(
                    request,
                    recovered,
                    acknowledge,
                    emitter_component=emitter_component,
                    emitter_process_generation=process_generation,
                    duplicate_dispatch=True,
                )
                return {"graph": recovered, "transport_receipt": receipt}
            if recovered["outcome"] != "duplicate_inflight":
                receipt = _acknowledge_terminal(
                    request,
                    recovered,
                    acknowledge,
                    emitter_component=emitter_component,
                    emitter_process_generation=process_generation,
                    duplicate_dispatch=True,
                )
                raise WaveRequestExecutionError(receipt) from exc
        terminal_outcome = {
            "model_request": "inference_failed",
            "response_validation": "validation_failed",
            "graph_commit": "graph_commit_failed",
        }[stage]
        terminal = mesh.record_wave_outcome(
            session_id,
            wave_id,
            role=slot["role"],
            request_revision=slot["request_revision"],
            request_id=slot["request_id"],
            terminal_outcome=terminal_outcome,
            inference_performed=True,
            failure_stage=stage,
            failure_detail_sha256=_text_sha256(f"{type(exc).__name__}: {exc}"),
        )
        receipt = _acknowledge_terminal(
            request,
            terminal,
            acknowledge,
            emitter_component=emitter_component,
            emitter_process_generation=process_generation,
            duplicate_dispatch=False,
        )
        raise WaveRequestExecutionError(receipt) from exc

    receipt = _acknowledge_terminal(
        request,
        terminal,
        acknowledge,
        emitter_component=emitter_component,
        emitter_process_generation=process_generation,
        duplicate_dispatch=bool(terminal.get("duplicate")),
    )
    return {"graph": terminal, "transport_receipt": receipt}


def _ask_taey(system_extra: str, user: str, max_tokens: int = 1500, timeout: int = 300) -> str:
    body = json.dumps({
        "model": TAEY_MODEL,
        "messages": [
            {"role": "system", "content": system_extra},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(TAEY_URL, body, {"content-type": "application/json"})
    m = json.load(urllib.request.urlopen(req, timeout=timeout))["choices"][0]["message"]
    c = m.get("content") or ""
    return re.sub(r"<think>.*?</think>", "", c, flags=re.DOTALL).strip()


def taey_expert(session_id: str, role: str, lens: str, max_retry: int = 4) -> str:
    """Taey participates as a mesh expert. Reads peers, reasons through `lens`, contributes.
    Retries on StaleReadError (re-reads + incorporates peers who arrived) — same as any expert.
    Returns the contrib_id.
    """
    for _ in range(max_retry):
        ctx = mesh.read_session(session_id)
        peers_txt = "\n\n".join(
            f"[{c['role']}] {c['content']}" for c in ctx["contributions"]) or "(no peers yet — you are first)"
        user = (
            f"You are participating in a Distributed Cognitive Mesh council THROUGH YOUR LENS: {lens}\n\n"
            f"SHARED PROBLEM:\n{ctx['payload']}\n\n"
            f"PEER CONTRIBUTIONS SO FAR (build on / sharpen / respectfully disagree — do not restate):\n{peers_txt}\n\n"
            f"Give your contribution through your lens, concise and dense. GROUNDED form: state each "
            f"CLAIM with its GROUND, and for each peer you engage an explicit STANCE (Agree/Disagree/"
            f"Extend) with justification — never agree just to converge. This is real design work for the project.")
        content = _ask_taey(system_extra=f"You are contributing to a DCM council as the '{role}' expert.", user=user)
        peers = [c["contrib_id"] for c in ctx["contributions"]]
        try:
            return mesh.contribute(session_id, role, content, peers_read=peers, read_version=ctx["version"])
        except mesh.StaleReadError:
            continue  # peers arrived; re-read + redo
    raise RuntimeError(f"Taey expert {role} could not land after {max_retry} retries (mesh too hot)")


if __name__ == "__main__":
    import sys
    print(taey_expert(sys.argv[1], sys.argv[2], sys.argv[3]))
