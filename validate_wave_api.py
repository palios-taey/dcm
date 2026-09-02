"""Live-Neo4j contract validation for the additive concurrent-wave DCM API."""

import concurrent.futures as cf
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh


PASS = True
ROLES = [f"role-{index}" for index in range(1, 8)]
MEMBERS = [
    {"seat_id": f"taey-council-{index}", "role": role}
    for index, role in enumerate(ROLES, start=1)
]
PROMPT_MESSAGES = [{"role": "user", "content": "wave validation"}]
V2_REQUEST_CONTRACT = "taey-native-dcm-request/v2"
V2_PROMPT_CONTRACT_SHA256 = mesh._canonical_sha256(
    {"shared": "presence shared contract", "role": "v2-role contract"}
)
V2_MODEL_IDENTITY_RECEIPT_SHA256 = mesh._canonical_sha256(
    {"model_identity_receipt": "validation"}
)
V2_MEMBERS = [
    {
        "seat_id": "taey-council-v2",
        "role": "v2-role",
        "prompt_contract_sha256": V2_PROMPT_CONTRACT_SHA256,
        "model_identity_receipt_sha256": V2_MODEL_IDENTITY_RECEIPT_SHA256,
    }
]


def check(name, condition):
    global PASS
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    PASS = PASS and condition


def digest(value):
    return mesh._canonical_sha256(value)


def request_identity(session_id, wave, role, seat, *, generation="generation-1"):
    return {
        "session_id": session_id,
        "wave_id": wave["wave_id"],
        "round": wave["round"],
        "phase": wave["phase"],
        "prompt_id": wave["prompt_id"],
        "prompt_revision": wave["prompt_revision"],
        "prompt_sha256": wave["prompt_sha256"],
        "seat_id": seat,
        "role": role,
        "request_revision": wave["request_revision"],
        "parent_frontier_sha256": wave["parent_frontier_sha256"],
        "process_generation_expected": generation,
        "model_endpoint": "http://127.0.0.1:8767/v1/chat/completions",
        "requested_alias": "ep3",
        "model_manifest_sha256": digest({"manifest": "validation"}),
        "model_content_sha256": digest({"model": "validation"}),
        "serving_container_digest": digest({"container": "validation"}),
    }


def observed(identity):
    return {
        "process_generation_observed": identity["process_generation_expected"],
        "model_endpoint": identity["model_endpoint"],
        "served_alias": identity["requested_alias"],
        "model_manifest_sha256": identity["model_manifest_sha256"],
        "model_content_sha256": identity["model_content_sha256"],
        "serving_container_digest": identity["serving_container_digest"],
    }


def claim_observation(identity):
    return {"seat_id": identity["seat_id"], **observed(identity)}


def emitter(identity):
    return {
        "component": "taey-council-seat",
        "process_generation": identity["process_generation_expected"],
    }


def reserve(session_id, wave, role, seat):
    identity = request_identity(session_id, wave, role, seat)
    reservation = mesh.reserve_wave_request(
        session_id,
        wave["wave_id"],
        role=role,
        request_revision=wave["request_revision"],
        request_identity=identity,
        parent_contribution_ids=wave["parent_contribution_ids"],
    )
    return identity, reservation["request_id"]


def claim(session_id, wave, role, identity, request_id):
    return mesh.claim_wave_request(
        session_id,
        wave["wave_id"],
        role=role,
        request_revision=wave["request_revision"],
        request_id=request_id,
        parent_contribution_ids=wave["parent_contribution_ids"],
        claim_observation=claim_observation(identity),
    )


def contribute(session_id, wave, role, identity, request_id, answer, **typed_fields):
    return mesh.contribute_wave(
        session_id,
        wave["wave_id"],
        role=role,
        request_revision=wave["request_revision"],
        request_id=request_id,
        structured_content={"role": role, "answer": answer},
        claimed_peers=wave["parent_contribution_ids"],
        observed_execution=observed(identity),
        emitter=emitter(identity),
        **typed_fields,
    )


def open_first_wave(session_id, *, prompt_id="prompt-validation"):
    return mesh.open_wave(
        session_id,
        round=1,
        phase="independent",
        prompt_id=prompt_id,
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=MEMBERS,
    )


def v2_request_identity(session_id, wave, member):
    identity = request_identity(
        session_id, wave, member["role"], member["seat_id"]
    )
    identity.update(
        {
            "request_contract": V2_REQUEST_CONTRACT,
            "prompt_contract_sha256": member["prompt_contract_sha256"],
            "model_identity_receipt_sha256": member[
                "model_identity_receipt_sha256"
            ],
        }
    )
    return identity


def v2_claim_observation(identity):
    return {
        **claim_observation(identity),
        "prompt_contract_sha256": identity["prompt_contract_sha256"],
        "model_identity_receipt_sha256": identity[
            "model_identity_receipt_sha256"
        ],
    }


def cleanup(session_ids):
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as session:
        for session_id in session_ids:
            session.run(
                """MATCH (x:DCMSession {session_id:$sid})
                   OPTIONAL MATCH (w:DCMWave {session_id:$sid})
                   OPTIONAL MATCH (z:DCMWaveSlot {session_id:$sid})
                   OPTIONAL MATCH (c:DCMContribution {session_id:$sid})
                   DETACH DELETE x, w, z, c""",
                sid=session_id,
            ).consume()


session_ids = []

try:
    with cf.ThreadPoolExecutor(max_workers=16) as executor:
        cold_start_drivers = list(
            executor.map(lambda _: mesh._ensure_wave_schema(), range(16))
        )
    check(
        "16 concurrent cold starts publish one fully initialized driver",
        len({id(driver) for driver in cold_start_drivers}) == 1,
    )
    session_id = mesh.start_session(
        "WAVE VALIDATION (throwaway)", "scoped cleanup", roles=ROLES
    )
    session_ids.append(session_id)

    with cf.ThreadPoolExecutor(max_workers=16) as executor:
        opened_ids = list(
            executor.map(lambda _: open_first_wave(session_id)["wave_id"], range(16))
        )
    check("16 identical concurrent opens return one wave", len(set(opened_ids)) == 1)
    wave = mesh.read_wave(session_id, opened_ids[0])
    check(
        "one immutable seat/role slot exists for every member",
        [{"seat_id": slot["seat_id"], "role": slot["role"]} for slot in wave["slots"]]
        == MEMBERS,
    )
    check(
        "prompt digest is derived from ordered prompt material",
        wave["prompt_sha256"] == mesh.canonical_prompt_sha256(PROMPT_MESSAGES, []),
    )
    linear_bypass_rejected = False
    try:
        mesh.contribute(
            session_id, "linear-bypass", "must not land", [], read_version=0
        )
    except mesh.WaveStateError as error:
        linear_bypass_rejected = error.outcome == "coordination_mode_conflict"
    check(
        "linear contribution cannot enter a wave-mode session", linear_bypass_rejected
    )

    role = ROLES[0]
    identity, request_id = reserve(session_id, wave, role, "taey-council-1")

    with cf.ThreadPoolExecutor(max_workers=16) as executor:
        claims = list(
            executor.map(
                lambda _: claim(session_id, wave, role, identity, request_id), range(16)
            )
        )
    check(
        "16 duplicate deliveries issue exactly one graph inference authorization",
        sum(item["inference_authorized"] for item in claims) == 1,
    )
    check(
        "duplicate in-flight deliveries are explicit",
        sum(item["outcome"] == "duplicate_inflight" for item in claims) == 15,
    )
    claimed_epoch = next(
        slot
        for slot in mesh.read_wave(session_id, wave["wave_id"])["slots"]
        if slot["role"] == role
    )["slot_tx_epoch"]
    claim(session_id, wave, role, identity, request_id)
    check(
        "settled duplicate claim performs no graph write",
        next(
            slot
            for slot in mesh.read_wave(session_id, wave["wave_id"])["slots"]
            if slot["role"] == role
        )["slot_tx_epoch"]
        == claimed_epoch,
    )

    conflicting = dict(identity)
    conflicting["process_generation_expected"] = "generation-conflict"
    conflict_before_inference = False
    try:
        mesh.reserve_wave_request(
            session_id,
            wave["wave_id"],
            role=role,
            request_revision=1,
            request_identity=conflicting,
            parent_contribution_ids=[],
        )
    except mesh.WaveIdentityConflictError:
        conflict_before_inference = True
    check(
        "different identity cannot occupy an already reserved role slot",
        conflict_before_inference,
    )

    first = contribute(session_id, wave, role, identity, request_id, "first")
    contributed_epoch = next(
        slot
        for slot in mesh.read_wave(session_id, wave["wave_id"])["slots"]
        if slot["role"] == role
    )["slot_tx_epoch"]
    duplicate = contribute(session_id, wave, role, identity, request_id, "first")
    check(
        "lost-ack replay returns the byte-equivalent canonical contribution receipt",
        duplicate["duplicate"]
        and duplicate["outcome"] == "contributed"
        and duplicate["contrib_id"] == first["contrib_id"]
        and duplicate["contribution_receipt"] == first["contribution_receipt"],
    )
    check(
        "settled contribution replay performs no graph write",
        next(
            slot
            for slot in mesh.read_wave(session_id, wave["wave_id"])["slots"]
            if slot["role"] == role
        )["slot_tx_epoch"]
        == contributed_epoch,
    )
    receipt = dict(first["contribution_receipt"])
    receipt_sha256 = receipt.pop("receipt_sha256")
    check(
        "canonical contribution receipt digest verifies",
        receipt_sha256 == digest(receipt),
    )
    recovered = claim(session_id, wave, role, identity, request_id)
    check(
        "duplicate delivery after lost acknowledgement returns original graph receipt",
        not recovered["inference_authorized"]
        and recovered["contribution_receipt"] == first["contribution_receipt"],
    )
    changed_generation_observation = claim_observation(identity)
    changed_generation_observation["process_generation_observed"] = (
        "recovery-generation"
    )
    recovered_by_new_process = mesh.claim_wave_request(
        session_id,
        wave["wave_id"],
        role=role,
        request_revision=1,
        request_id=request_id,
        parent_contribution_ids=[],
        claim_observation=changed_generation_observation,
    )
    check(
        "terminal lost-ack recovery does not authorize or impersonate prior inference",
        not recovered_by_new_process["inference_authorized"]
        and recovered_by_new_process["contribution_receipt"]
        == first["contribution_receipt"],
    )

    def sibling(role_index):
        sibling_role = ROLES[role_index]
        sibling_identity, sibling_request_id = reserve(
            session_id, wave, sibling_role, f"taey-council-{role_index + 1}"
        )
        authorization = claim(
            session_id, wave, sibling_role, sibling_identity, sibling_request_id
        )
        if not authorization["inference_authorized"]:
            return None
        return contribute(
            session_id,
            wave,
            sibling_role,
            sibling_identity,
            sibling_request_id,
            "sibling",
        )

    with cf.ThreadPoolExecutor(max_workers=6) as executor:
        sibling_results = list(executor.map(sibling, range(1, 7)))
    check(
        "six sibling roles commit concurrently without linear regeneration",
        all(
            result and result["outcome"] == "contributed" for result in sibling_results
        ),
    )
    check(
        "wave contributions do not change the legacy linear version",
        mesh.read_session(session_id)["version"] == 0,
    )

    closed = mesh.close_wave(session_id, wave["wave_id"])
    verification = mesh.verify_wave_coordination(session_id, wave["wave_id"])
    check("seven-role wave closes complete", closed["close_outcome"] == "complete")
    check(
        "wave-aware relationship and receipt audit passes",
        verification["coordinated"] is True,
    )
    check(
        "implicit v1 contribution receipt remains byte-shape compatible",
        first["contribution_receipt"]["contract"]
        == "taey-native-dcm-receipt/v1"
        and "request_contract" not in first["contribution_receipt"]
        and "prompt_contract_sha256" not in first["contribution_receipt"]
        and "model_identity_receipt_sha256"
        not in first["contribution_receipt"],
    )

    v2_session = mesh.start_session(
        "V2 NATIVE CONTRACT VALIDATION (throwaway)",
        "scoped cleanup",
        roles=["v2-role"],
    )
    session_ids.append(v2_session)
    implicit_v2_membership_rejected = False
    try:
        mesh.open_wave(
            v2_session,
            round=1,
            phase="independent",
            prompt_id="v2-contract",
            prompt_revision=1,
            prompt_messages=PROMPT_MESSAGES,
            attachment_evidence_digests=[],
            request_revision=1,
            required_members=V2_MEMBERS,
        )
    except ValueError:
        implicit_v2_membership_rejected = True
    check(
        "v2 member fields require the explicit v2 request contract",
        implicit_v2_membership_rejected,
    )
    v2_wave = mesh.open_wave(
        v2_session,
        round=1,
        phase="independent",
        prompt_id="v2-contract",
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=V2_MEMBERS,
        request_contract=V2_REQUEST_CONTRACT,
    )
    v2_slot = v2_wave["slots"][0]
    check(
        "v2 wave membership freezes both per-slot contract digests",
        v2_wave["request_contract"] == V2_REQUEST_CONTRACT
        and v2_wave["membership_sha256"] == digest(V2_MEMBERS)
        and v2_slot["prompt_contract_sha256"] == V2_PROMPT_CONTRACT_SHA256
        and v2_slot["model_identity_receipt_sha256"]
        == V2_MODEL_IDENTITY_RECEIPT_SHA256,
    )
    v2_identity = v2_request_identity(v2_session, v2_wave, V2_MEMBERS[0])
    missing_v2_identity_field = dict(v2_identity)
    missing_v2_identity_field.pop("model_identity_receipt_sha256")
    exact_v2_request_fields_enforced = False
    try:
        mesh.canonical_wave_request_id(missing_v2_identity_field)
    except ValueError:
        exact_v2_request_fields_enforced = True
    check(
        "v2 request identity field set is exact",
        exact_v2_request_fields_enforced,
    )
    v2_reservation = mesh.reserve_wave_request(
        v2_session,
        v2_wave["wave_id"],
        role="v2-role",
        request_revision=1,
        request_identity=v2_identity,
        parent_contribution_ids=[],
    )
    check(
        "v2 request ID binds both contract digests",
        v2_reservation["request_id"] == digest(v2_identity),
    )
    incomplete_v2_claim = v2_claim_observation(v2_identity)
    incomplete_v2_claim.pop("prompt_contract_sha256")
    exact_v2_claim_fields_enforced = False
    try:
        mesh.claim_wave_request(
            v2_session,
            v2_wave["wave_id"],
            role="v2-role",
            request_revision=1,
            request_id=v2_reservation["request_id"],
            parent_contribution_ids=[],
            claim_observation=incomplete_v2_claim,
        )
    except mesh.WaveStateError as error:
        exact_v2_claim_fields_enforced = error.outcome == "model_identity_unproven"
    check(
        "v2 claim observation field set is exact before inference authorization",
        exact_v2_claim_fields_enforced,
    )
    v2_claim = mesh.claim_wave_request(
        v2_session,
        v2_wave["wave_id"],
        role="v2-role",
        request_revision=1,
        request_id=v2_reservation["request_id"],
        parent_contribution_ids=[],
        claim_observation=v2_claim_observation(v2_identity),
    )
    check(
        "matching v2 contract observations authorize inference once",
        v2_claim["inference_authorized"] is True,
    )
    v2_contribution = mesh.contribute_wave(
        v2_session,
        v2_wave["wave_id"],
        role="v2-role",
        request_revision=1,
        request_id=v2_reservation["request_id"],
        structured_content={"role": "v2-role", "answer": "v2"},
        claimed_peers=[],
        observed_execution=observed(v2_identity),
        emitter=emitter(v2_identity),
    )
    v2_receipt = dict(v2_contribution["contribution_receipt"])
    v2_receipt_sha256 = v2_receipt.pop("receipt_sha256")
    check(
        "v2 contribution receipt binds request and contract evidence",
        v2_receipt["contract"] == "taey-native-dcm-receipt/v2"
        and v2_receipt["request_contract"] == V2_REQUEST_CONTRACT
        and v2_receipt["prompt_contract_sha256"] == V2_PROMPT_CONTRACT_SHA256
        and v2_receipt["model_identity_receipt_sha256"]
        == V2_MODEL_IDENTITY_RECEIPT_SHA256
        and v2_receipt_sha256 == digest(v2_receipt),
    )
    v2_closed = mesh.close_wave(v2_session, v2_wave["wave_id"])
    check(
        "v2 wave closes only with receipt-valid contract-bound membership",
        v2_closed["close_outcome"] == "complete"
        and mesh.verify_wave_coordination(v2_session, v2_wave["wave_id"])[
            "coordinated"
        ],
    )
    changed_v2_members = [dict(V2_MEMBERS[0])]
    changed_v2_members[0]["prompt_contract_sha256"] = digest(
        {"changed": "presence contract"}
    )
    v2_membership_drift_rejected = False
    try:
        mesh.open_wave(
            v2_session,
            round=1,
            phase="critique",
            prompt_id="v2-contract-critique",
            prompt_revision=1,
            prompt_messages=PROMPT_MESSAGES,
            attachment_evidence_digests=[],
            request_revision=1,
            required_members=changed_v2_members,
            request_contract=V2_REQUEST_CONTRACT,
            parent_wave_id=v2_wave["wave_id"],
        )
    except mesh.WaveIdentityConflictError:
        v2_membership_drift_rejected = True
    check(
        "per-slot v2 contract digests cannot drift between waves",
        v2_membership_drift_rejected,
    )

    remapped_members = [dict(member) for member in MEMBERS]
    remapped_members[0]["seat_id"], remapped_members[1]["seat_id"] = (
        remapped_members[1]["seat_id"],
        remapped_members[0]["seat_id"],
    )
    remap_rejected = False
    try:
        mesh.open_wave(
            session_id,
            round=1,
            phase="critique",
            prompt_id="prompt-critique",
            prompt_revision=1,
            prompt_messages=[
                {"role": "user", "content": "critique the prior frontier"}
            ],
            attachment_evidence_digests=[],
            request_revision=1,
            required_members=remapped_members,
            parent_wave_id=wave["wave_id"],
        )
    except mesh.WaveIdentityConflictError:
        remap_rejected = True
    check("seat-to-role membership cannot drift between waves", remap_rejected)

    next_wave = mesh.open_wave(
        session_id,
        round=1,
        phase="critique",
        prompt_id="prompt-critique",
        prompt_revision=1,
        prompt_messages=[{"role": "user", "content": "critique the prior frontier"}],
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=MEMBERS,
        parent_wave_id=wave["wave_id"],
    )
    check(
        "critique wave derives all seven graph-bound siblings as parents",
        next_wave["parent_contribution_ids"] == closed["completion_frontier"]
        and next_wave["parent_edge_contribution_ids"] == closed["completion_frontier"]
        and len(next_wave["parent_contribution_ids"]) == 7,
    )

    critique_identity, critique_request_id = reserve(
        session_id, next_wave, ROLES[0], "taey-council-1"
    )
    reservation_frontier_rejected = False
    try:
        mesh.reserve_wave_request(
            session_id,
            next_wave["wave_id"],
            role=ROLES[0],
            request_revision=1,
            request_identity=critique_identity,
            parent_contribution_ids=next_wave["parent_contribution_ids"][:-1],
        )
    except mesh.WaveFrontierMismatchError:
        reservation_frontier_rejected = True
    check(
        "idempotent reservation still rejects a changed dispatch frontier",
        reservation_frontier_rejected,
    )
    claim(session_id, next_wave, ROLES[0], critique_identity, critique_request_id)
    frontier_rejected = False
    try:
        mesh.contribute_wave(
            session_id,
            next_wave["wave_id"],
            role=ROLES[0],
            request_revision=1,
            request_id=critique_request_id,
            structured_content={"role": ROLES[0], "answer": "bad frontier"},
            claimed_peers=next_wave["parent_contribution_ids"][:-1],
            observed_execution=observed(critique_identity),
            emitter=emitter(critique_identity),
        )
    except mesh.WaveFrontierMismatchError:
        frontier_rejected = True
    check("a reduced parent frontier is terminally rejected", frontier_rejected)
    failed = mesh.record_wave_outcome(
        session_id,
        next_wave["wave_id"],
        role=ROLES[0],
        request_revision=1,
        request_id=critique_request_id,
        terminal_outcome="frontier_mismatch",
        inference_performed=True,
        failure_stage="post_inference_frontier",
        failure_detail_sha256=digest({"missing": 1}),
    )
    check(
        "failure record is graph-derived and digest-bound",
        failed["outcome_record"]["outcome_record_sha256"]
        == digest(
            {
                key: value
                for key, value in failed["outcome_record"].items()
                if key != "outcome_record_sha256"
            }
        ),
    )
    failed_claim_replay = claim(
        session_id, next_wave, ROLES[0], critique_identity, critique_request_id
    )
    check(
        "failed terminal delivery returns the original canonical outcome record",
        not failed_claim_replay["inference_authorized"]
        and failed_claim_replay["outcome_record"] == failed["outcome_record"]
        and failed_claim_replay["inference_performed"] is True,
    )
    pending_identity, pending_request_id = reserve(
        session_id, next_wave, ROLES[1], "taey-council-2"
    )
    pending_inference_claim_rejected = False
    try:
        mesh.record_wave_outcome(
            session_id,
            next_wave["wave_id"],
            role=ROLES[1],
            request_revision=1,
            request_id=pending_request_id,
            terminal_outcome="inference_failed",
            inference_performed=True,
            failure_stage="model_request",
            failure_detail_sha256=digest({"must": "not land"}),
        )
    except mesh.WaveStateError as error:
        pending_inference_claim_rejected = error.outcome == "inference_not_authorized"
    check(
        "pending request cannot claim a post-inference terminal outcome",
        pending_inference_claim_rejected,
    )
    incomplete = mesh.close_wave(session_id, next_wave["wave_id"])
    check(
        "failed and missing roles close as explicit incomplete_round",
        incomplete["close_outcome"] == "incomplete_round"
        and {slot["state"] for slot in incomplete["slots"]} == {"failed", "missing"}
        and all(slot.get("outcome_record") for slot in incomplete["slots"]),
    )
    blocked_advance = False
    try:
        mesh.open_wave(
            session_id,
            round=2,
            phase="independent",
            prompt_id="prompt-next",
            prompt_revision=1,
            prompt_messages=PROMPT_MESSAGES,
            attachment_evidence_digests=[],
            request_revision=1,
            required_members=MEMBERS,
            parent_wave_id=next_wave["wave_id"],
        )
    except mesh.WaveStateError as error:
        blocked_advance = error.outcome == "incomplete_round"
    check("an incomplete wave cannot authorize a next wave", blocked_advance)

    race_session = mesh.start_session(
        "WAVE OPEN RACE VALIDATION (throwaway)", "scoped cleanup", roles=ROLES
    )
    session_ids.append(race_session)

    def divergent_open(index):
        try:
            return {
                "wave_id": mesh.open_wave(
                    race_session,
                    round=1,
                    phase="independent",
                    prompt_id=f"prompt-{index}",
                    prompt_revision=1,
                    prompt_messages=[{"role": "user", "content": f"candidate {index}"}],
                    attachment_evidence_digests=[],
                    request_revision=1,
                    required_members=MEMBERS,
                )["wave_id"]
            }
        except mesh.WaveIdentityConflictError:
            return {"error": "identity_conflict"}

    with cf.ThreadPoolExecutor(max_workers=16) as executor:
        divergent = list(executor.map(divergent_open, range(16)))
    winners = [item["wave_id"] for item in divergent if "wave_id" in item]
    check("16 divergent opens produce exactly one active wave", len(winners) == 1)
    check(
        "all divergent losers stop explicitly",
        sum(item.get("error") == "identity_conflict" for item in divergent) == 15,
    )

    close_race_session = mesh.start_session(
        "WAVE CLOSE RACE VALIDATION (throwaway)", "scoped cleanup", roles=["only"]
    )
    session_ids.append(close_race_session)
    one_member = [{"seat_id": "taey-council-1", "role": "only"}]
    close_race_wave = mesh.open_wave(
        close_race_session,
        round=1,
        phase="independent",
        prompt_id="close-race",
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=one_member,
    )
    close_identity = request_identity(
        close_race_session, close_race_wave, "only", "taey-council-1"
    )
    close_reservation = mesh.reserve_wave_request(
        close_race_session,
        close_race_wave["wave_id"],
        role="only",
        request_revision=1,
        request_identity=close_identity,
        parent_contribution_ids=[],
    )
    claim(
        close_race_session,
        close_race_wave,
        "only",
        close_identity,
        close_reservation["request_id"],
    )

    def racing_close():
        try:
            return mesh.close_wave(close_race_session, close_race_wave["wave_id"])
        except mesh.WaveStateError as error:
            return {"error": error.outcome}

    with cf.ThreadPoolExecutor(max_workers=2) as executor:
        close_future = executor.submit(racing_close)
        contribute_future = executor.submit(
            contribute,
            close_race_session,
            close_race_wave,
            "only",
            close_identity,
            close_reservation["request_id"],
            "close race",
        )
        close_result = close_future.result()
        contribute_result = contribute_future.result()
    if close_result.get("error") == "inflight_requests":
        close_result = mesh.close_wave(close_race_session, close_race_wave["wave_id"])
    check(
        "close-versus-contribute race never loses the committed role",
        contribute_result["outcome"] == "contributed"
        and close_result["close_outcome"] == "complete"
        and mesh.verify_wave_coordination(
            close_race_session, close_race_wave["wave_id"]
        )["coordinated"],
    )

    lineage_session = mesh.start_session(
        "RESOLUTION LINEAGE VALIDATION (throwaway)",
        "scoped cleanup",
        roles=["concern-author", "resolution-author"],
    )
    session_ids.append(lineage_session)
    lineage_members = [
        {"seat_id": "taey-council-1", "role": "concern-author"},
        {"seat_id": "taey-council-2", "role": "resolution-author"},
    ]
    lineage_wave = mesh.open_wave(
        lineage_session,
        round=1,
        phase="independent",
        prompt_id="lineage",
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=lineage_members,
    )
    concern_identity, concern_request_id = reserve(
        lineage_session, lineage_wave, "concern-author", "taey-council-1"
    )
    resolution_identity, resolution_request_id = reserve(
        lineage_session, lineage_wave, "resolution-author", "taey-council-2"
    )
    claim(
        lineage_session,
        lineage_wave,
        "concern-author",
        concern_identity,
        concern_request_id,
    )
    claim(
        lineage_session,
        lineage_wave,
        "resolution-author",
        resolution_identity,
        resolution_request_id,
    )
    unseen_concern = contribute(
        lineage_session,
        lineage_wave,
        "concern-author",
        concern_identity,
        concern_request_id,
        "same-wave concern",
        kind="concern",
        severity="block",
    )
    same_wave_resolution_rejected = False
    try:
        contribute(
            lineage_session,
            lineage_wave,
            "resolution-author",
            resolution_identity,
            resolution_request_id,
            "must not clear an unseen sibling",
            kind="resolution",
            about=unseen_concern["contrib_id"],
            disposition="FIX-VERIFIED",
            evidence_ref="neo4j://validation/unseen",
        )
    except mesh.WaveFrontierMismatchError:
        same_wave_resolution_rejected = True
    check(
        "resolution must name a concern in its immutable parent frontier",
        same_wave_resolution_rejected,
    )

    tamper_session = mesh.start_session(
        "CANONICAL ARTIFACT VALIDATION (throwaway)",
        "scoped cleanup",
        roles=[
            "request-artifact",
            "receipt-artifact",
            "outcome-artifact",
            "contribution-claim-artifact",
            "outcome-claim-artifact",
            "precommit-contribution-claim",
            "precommit-outcome-claim",
        ],
    )
    session_ids.append(tamper_session)
    tamper_members = [
        {"seat_id": "taey-council-1", "role": "request-artifact"},
        {"seat_id": "taey-council-2", "role": "receipt-artifact"},
        {"seat_id": "taey-council-3", "role": "outcome-artifact"},
        {"seat_id": "taey-council-4", "role": "contribution-claim-artifact"},
        {"seat_id": "taey-council-5", "role": "outcome-claim-artifact"},
        {"seat_id": "taey-council-6", "role": "precommit-contribution-claim"},
        {"seat_id": "taey-council-7", "role": "precommit-outcome-claim"},
    ]
    tamper_wave = mesh.open_wave(
        tamper_session,
        round=1,
        phase="independent",
        prompt_id="artifact-integrity",
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=tamper_members,
    )

    tampered_identity, tampered_request_id = reserve(
        tamper_session, tamper_wave, "request-artifact", "taey-council-1"
    )
    altered_identity = dict(tampered_identity)
    altered_identity["requested_alias"] = "altered-alias"
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                       role:'request-artifact'})
               SET z.request_identity_json=$identity""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            identity=mesh._canonical_json(altered_identity),
        ).consume()
    corrupt_request_rejected = False
    try:
        claim(
            tamper_session,
            tamper_wave,
            "request-artifact",
            tampered_identity,
            tampered_request_id,
        )
    except mesh.WaveIdentityConflictError:
        corrupt_request_rejected = True
    check(
        "claim recomputes and rejects a corrupted frozen request identity",
        corrupt_request_rejected,
    )
    corrupt_pending_outcome_rejected = False
    try:
        mesh.record_wave_outcome(
            tamper_session,
            tamper_wave["wave_id"],
            role="request-artifact",
            request_revision=1,
            request_id=tampered_request_id,
            terminal_outcome="dead_seat",
            inference_performed=False,
            failure_stage="readiness",
            failure_detail_sha256=digest({"request": "must not terminalize"}),
        )
    except mesh.WaveIdentityConflictError:
        corrupt_pending_outcome_rejected = True
    corrupt_pending_slot = next(
        slot
        for slot in mesh.read_wave(tamper_session, tamper_wave["wave_id"])["slots"]
        if slot["role"] == "request-artifact"
    )
    check(
        "corrupted pending request identity blocks terminalization before mutation",
        corrupt_pending_outcome_rejected
        and corrupt_pending_slot["state"] == "pending"
        and corrupt_pending_slot.get("outcome_record") is None,
    )

    receipt_identity, receipt_request_id = reserve(
        tamper_session, tamper_wave, "receipt-artifact", "taey-council-2"
    )
    claim(
        tamper_session,
        tamper_wave,
        "receipt-artifact",
        receipt_identity,
        receipt_request_id,
    )
    contribute(
        tamper_session,
        tamper_wave,
        "receipt-artifact",
        receipt_identity,
        receipt_request_id,
        "canonical content",
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (c:DCMContribution {session_id:$sid, wave_id:$wid,
                                          role:'receipt-artifact'})
               SET c.structured_content_json=$content""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            content=mesh._canonical_json(
                {"role": "receipt-artifact", "answer": "altered content"}
            ),
        ).consume()
    corrupt_receipt_rejected = False
    try:
        claim(
            tamper_session,
            tamper_wave,
            "receipt-artifact",
            receipt_identity,
            receipt_request_id,
        )
    except mesh.WaveIdentityConflictError:
        corrupt_receipt_rejected = True
    check(
        "lost-ack replay reconstructs and rejects corrupted contribution content",
        corrupt_receipt_rejected,
    )

    outcome_identity, outcome_request_id = reserve(
        tamper_session, tamper_wave, "outcome-artifact", "taey-council-3"
    )
    terminal = mesh.record_wave_outcome(
        tamper_session,
        tamper_wave["wave_id"],
        role="outcome-artifact",
        request_revision=1,
        request_id=outcome_request_id,
        terminal_outcome="dead_seat",
        inference_performed=False,
        failure_stage="readiness",
        failure_detail_sha256=digest({"seat": "not live"}),
    )
    altered_outcome = dict(terminal["outcome_record"])
    altered_outcome["failure_stage"] = "altered-stage"
    altered_outcome["outcome_record_sha256"] = digest(
        {
            key: value
            for key, value in altered_outcome.items()
            if key != "outcome_record_sha256"
        }
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                       role:'outcome-artifact'})
               SET z.outcome_record_json=$record,
                   z.outcome_record_sha256=$record_sha256""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            record=mesh._canonical_json(altered_outcome),
            record_sha256=altered_outcome["outcome_record_sha256"],
        ).consume()
    corrupt_outcome_rejected = False
    try:
        claim(
            tamper_session,
            tamper_wave,
            "outcome-artifact",
            outcome_identity,
            outcome_request_id,
        )
    except mesh.WaveIdentityConflictError:
        corrupt_outcome_rejected = True
    check(
        "terminal claim redelivery reconstructs and rejects a corrupted outcome record",
        corrupt_outcome_rejected,
    )

    contribution_claim_identity, contribution_claim_request_id = reserve(
        tamper_session,
        tamper_wave,
        "contribution-claim-artifact",
        "taey-council-4",
    )
    claim(
        tamper_session,
        tamper_wave,
        "contribution-claim-artifact",
        contribution_claim_identity,
        contribution_claim_request_id,
    )
    contribute(
        tamper_session,
        tamper_wave,
        "contribution-claim-artifact",
        contribution_claim_identity,
        contribution_claim_request_id,
        "claim-bound contribution",
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                       role:'contribution-claim-artifact'})
               SET z.claim_observation_json=$observation""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            observation=mesh._canonical_json({}),
        ).consume()
    corrupt_contribution_claim_rejected = False
    try:
        claim(
            tamper_session,
            tamper_wave,
            "contribution-claim-artifact",
            contribution_claim_identity,
            contribution_claim_request_id,
        )
    except mesh.WaveIdentityConflictError:
        corrupt_contribution_claim_rejected = True
    check(
        "contribution replay rejects a corrupted claim-time observation",
        corrupt_contribution_claim_rejected,
    )

    outcome_claim_identity, outcome_claim_request_id = reserve(
        tamper_session,
        tamper_wave,
        "outcome-claim-artifact",
        "taey-council-5",
    )
    claim(
        tamper_session,
        tamper_wave,
        "outcome-claim-artifact",
        outcome_claim_identity,
        outcome_claim_request_id,
    )
    mesh.record_wave_outcome(
        tamper_session,
        tamper_wave["wave_id"],
        role="outcome-claim-artifact",
        request_revision=1,
        request_id=outcome_claim_request_id,
        terminal_outcome="inference_failed",
        inference_performed=True,
        failure_stage="model_request",
        failure_detail_sha256=digest({"model": "failed"}),
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                       role:'outcome-claim-artifact'})
               SET z.claim_observation_json=$observation""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            observation=mesh._canonical_json({}),
        ).consume()
    corrupt_outcome_claim_rejected = False
    try:
        mesh.record_wave_outcome(
            tamper_session,
            tamper_wave["wave_id"],
            role="outcome-claim-artifact",
            request_revision=1,
            request_id=outcome_claim_request_id,
            terminal_outcome="inference_failed",
            inference_performed=True,
            failure_stage="model_request",
            failure_detail_sha256=digest({"model": "failed"}),
        )
    except mesh.WaveIdentityConflictError:
        corrupt_outcome_claim_rejected = True
    check(
        "terminal replay rejects a corrupted claim-time observation",
        corrupt_outcome_claim_rejected,
    )

    precommit_identity, precommit_request_id = reserve(
        tamper_session,
        tamper_wave,
        "precommit-contribution-claim",
        "taey-council-6",
    )
    claim(
        tamper_session,
        tamper_wave,
        "precommit-contribution-claim",
        precommit_identity,
        precommit_request_id,
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                       role:'precommit-contribution-claim'})
               SET z.claim_observation_json=$observation""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            observation=mesh._canonical_json({}),
        ).consume()
    precommit_contribution_rejected = False
    try:
        contribute(
            tamper_session,
            tamper_wave,
            "precommit-contribution-claim",
            precommit_identity,
            precommit_request_id,
            "must not commit",
        )
    except mesh.WaveStateError:
        precommit_contribution_rejected = True
    precommit_contribution_slot = next(
        slot
        for slot in mesh.read_wave(tamper_session, tamper_wave["wave_id"])["slots"]
        if slot["role"] == "precommit-contribution-claim"
    )
    check(
        "corrupted claim proof blocks the first contribution before graph mutation",
        precommit_contribution_rejected
        and precommit_contribution_slot["state"] == "claimed"
        and precommit_contribution_slot.get("contrib_id") is None,
    )

    precommit_outcome_identity, precommit_outcome_request_id = reserve(
        tamper_session,
        tamper_wave,
        "precommit-outcome-claim",
        "taey-council-7",
    )
    claim(
        tamper_session,
        tamper_wave,
        "precommit-outcome-claim",
        precommit_outcome_identity,
        precommit_outcome_request_id,
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid,
                                       role:'precommit-outcome-claim'})
               SET z.claim_observation_json=$observation""",
            sid=tamper_session,
            wid=tamper_wave["wave_id"],
            observation=mesh._canonical_json({}),
        ).consume()
    precommit_outcome_rejected = False
    try:
        mesh.record_wave_outcome(
            tamper_session,
            tamper_wave["wave_id"],
            role="precommit-outcome-claim",
            request_revision=1,
            request_id=precommit_outcome_request_id,
            terminal_outcome="inference_failed",
            inference_performed=True,
            failure_stage="model_request",
            failure_detail_sha256=digest({"must": "not terminalize"}),
        )
    except mesh.WaveStateError:
        precommit_outcome_rejected = True
    precommit_outcome_slot = next(
        slot
        for slot in mesh.read_wave(tamper_session, tamper_wave["wave_id"])["slots"]
        if slot["role"] == "precommit-outcome-claim"
    )
    check(
        "corrupted claim proof blocks the first outcome before graph mutation",
        precommit_outcome_rejected
        and precommit_outcome_slot["state"] == "claimed"
        and precommit_outcome_slot.get("outcome_record") is None,
    )

    close_tamper_session = mesh.start_session(
        "CLOSE RESERVED IDENTITY VALIDATION (throwaway)",
        "scoped cleanup",
        roles=["only"],
    )
    session_ids.append(close_tamper_session)
    close_tamper_wave = mesh.open_wave(
        close_tamper_session,
        round=1,
        phase="independent",
        prompt_id="close-reserved-identity",
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=[{"seat_id": "taey-council-1", "role": "only"}],
    )
    close_tamper_identity, _ = reserve(
        close_tamper_session,
        close_tamper_wave,
        "only",
        "taey-council-1",
    )
    altered_close_identity = dict(close_tamper_identity)
    altered_close_identity["requested_alias"] = "altered-close-alias"
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:'only'})
               SET z.request_identity_json=$identity""",
            sid=close_tamper_session,
            wid=close_tamper_wave["wave_id"],
            identity=mesh._canonical_json(altered_close_identity),
        ).consume()
    corrupt_close_identity_rejected = False
    try:
        mesh.close_wave(close_tamper_session, close_tamper_wave["wave_id"])
    except mesh.WaveIdentityConflictError:
        corrupt_close_identity_rejected = True
    close_tamper_after = mesh.read_wave(
        close_tamper_session, close_tamper_wave["wave_id"]
    )
    check(
        "corrupted reserved pending identity blocks wave close before mutation",
        corrupt_close_identity_rejected
        and close_tamper_after["status"] == "open"
        and close_tamper_after["slots"][0]["state"] == "pending"
        and close_tamper_after["slots"][0].get("outcome_record") is None,
    )
    with mesh._db().session(database=mesh.DCM_NEO4J_DATABASE) as graph:
        graph.run(
            """MATCH (z:DCMWaveSlot {session_id:$sid, wave_id:$wid, role:'only'})
               SET z.request_identity_json=$identity, z.request_id=null""",
            sid=close_tamper_session,
            wid=close_tamper_wave["wave_id"],
            identity=mesh._canonical_json(close_tamper_identity),
        ).consume()
    orphan_close_identity_rejected = False
    try:
        mesh.close_wave(close_tamper_session, close_tamper_wave["wave_id"])
    except mesh.WaveIdentityConflictError:
        orphan_close_identity_rejected = True
    orphan_close_after = mesh.read_wave(
        close_tamper_session, close_tamper_wave["wave_id"]
    )
    check(
        "asymmetric pending request identity blocks wave close before mutation",
        orphan_close_identity_rejected
        and orphan_close_after["status"] == "open"
        and orphan_close_after["slots"][0]["state"] == "pending"
        and orphan_close_after["slots"][0].get("outcome_record") is None,
    )

    amendment_session = mesh.start_session(
        "PROMPT AMENDMENT AND FINAL VALIDATION (throwaway)",
        "scoped cleanup",
        roles=["only"],
    )
    session_ids.append(amendment_session)
    amended_members = [{"seat_id": "taey-council-1", "role": "only"}]
    superseded_wave = mesh.open_wave(
        amendment_session,
        round=1,
        phase="independent",
        prompt_id="revision-1",
        prompt_revision=1,
        prompt_messages=PROMPT_MESSAGES,
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=amended_members,
    )
    superseded_wave = mesh.close_wave(
        amendment_session, superseded_wave["wave_id"], superseded_by_prompt_revision=2
    )
    check(
        "new user prompt revision durably supersedes and accounts for the prior wave",
        superseded_wave["close_outcome"] == "superseded_revision"
        and superseded_wave["slots"][0]["state"] == "superseded"
        and mesh.verify_wave_coordination(
            amendment_session, superseded_wave["wave_id"]
        )["coordinated"],
    )

    amended_wave = mesh.open_wave(
        amendment_session,
        round=1,
        phase="independent",
        prompt_id="revision-2",
        prompt_revision=2,
        prompt_messages=[{"role": "user", "content": "amended wave validation"}],
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=amended_members,
        parent_wave_id=superseded_wave["wave_id"],
    )
    check(
        "amended independent wave carries no stale prior-revision parent",
        amended_wave["transition"] == "prompt_amendment"
        and amended_wave["parent_contribution_ids"] == [],
    )
    amended_identity, amended_request_id = reserve(
        amendment_session, amended_wave, "only", "taey-council-1"
    )
    claim(amendment_session, amended_wave, "only", amended_identity, amended_request_id)
    concern = mesh.contribute_wave(
        amendment_session,
        amended_wave["wave_id"],
        role="only",
        request_revision=1,
        request_id=amended_request_id,
        structured_content={"role": "only", "answer": "blocking concern"},
        claimed_peers=[],
        observed_execution=observed(amended_identity),
        emitter={
            "component": "dcm-adapter",
            "process_generation": "adapter-generation",
        },
        kind="concern",
        severity="block",
        veto=True,
    )
    amended_wave = mesh.close_wave(amendment_session, amended_wave["wave_id"])
    check(
        "adapter and inference process generations remain separately receipted",
        concern["contribution_receipt"]["emitter"]["process_generation"]
        == "adapter-generation"
        and concern["contribution_receipt"]["execution"]["process_generation_observed"]
        == "generation-1",
    )

    amendment_critique = mesh.open_wave(
        amendment_session,
        round=1,
        phase="critique",
        prompt_id="revision-2-critique",
        prompt_revision=2,
        prompt_messages=[{"role": "user", "content": "resolve amended concern"}],
        attachment_evidence_digests=[],
        request_revision=1,
        required_members=amended_members,
        parent_wave_id=amended_wave["wave_id"],
    )
    resolution_identity, resolution_request_id = reserve(
        amendment_session, amendment_critique, "only", "taey-council-1"
    )
    claim(
        amendment_session,
        amendment_critique,
        "only",
        resolution_identity,
        resolution_request_id,
    )
    mesh.contribute_wave(
        amendment_session,
        amendment_critique["wave_id"],
        role="only",
        request_revision=1,
        request_id=resolution_request_id,
        structured_content={"role": "only", "answer": "verified resolution"},
        claimed_peers=amendment_critique["parent_contribution_ids"],
        observed_execution=observed(resolution_identity),
        emitter=emitter(resolution_identity),
        kind="resolution",
        about=concern["contrib_id"],
        disposition="FIX-VERIFIED",
        evidence_ref="neo4j://validation/evidence",
    )
    amendment_critique = mesh.close_wave(
        amendment_session, amendment_critique["wave_id"]
    )
    check(
        "latest revision concern clearance includes independent and critique waves",
        amendment_critique["close_outcome"] == "complete"
        and mesh.open_concerns(amendment_session) == [],
    )
    mesh.publish_final(amendment_session, "validated wave final")
    finalized = mesh.read_session(amendment_session)
    clearance = finalized["clearance_projection"]
    check(
        "wave final binds one terminal session after complete critique",
        finalized["status"] == "closed",
    )
    check(
        "wave final persists the exact closed-concern clearance projection",
        clearance["open_blocking_concern_ids"] == []
        and clearance["closed_concerns"][0]["concern_id"] == concern["contrib_id"]
        and finalized["clearance_projection_sha256"] == digest(clearance),
    )
    second_final_rejected = False
    try:
        mesh.publish_final(amendment_session, "different final")
    except mesh.WaveStateError as error:
        second_final_rejected = error.outcome == "closed_session"
    check("final publication is one immutable transition", second_final_rejected)
    post_final_linear_rejected = False
    try:
        mesh.contribute(
            amendment_session, "late-linear", "must not land", [], read_version=0
        )
    except mesh.WaveStateError as error:
        post_final_linear_rejected = error.outcome == "closed_session"
    check(
        "post-final linear write is structurally rejected", post_final_linear_rejected
    )

    linear_session = mesh.start_session(
        "LINEAR MODE EXCLUSIVITY (throwaway)", "scoped cleanup", roles=["only"]
    )
    session_ids.append(linear_session)
    mesh.contribute(linear_session, "only", "linear", [], read_version=0)
    wave_bypass_rejected = False
    try:
        mesh.open_wave(
            linear_session,
            round=1,
            phase="independent",
            prompt_id="must-not-open",
            prompt_revision=1,
            prompt_messages=PROMPT_MESSAGES,
            attachment_evidence_digests=[],
            request_revision=1,
            required_members=amended_members,
        )
    except mesh.WaveStateError as error:
        wave_bypass_rejected = error.outcome == "coordination_mode_conflict"
    check("wave cannot enter a linear-mode session", wave_bypass_rejected)
finally:
    cleanup(session_ids)
    print(f"cleaned up {len(session_ids)} validation session(s)")

print(f"\n=== DCM WAVE API VALIDATION: {'PASS' if PASS else 'FAIL'} ===")
sys.exit(0 if PASS else 1)
