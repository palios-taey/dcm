"""DCM mesh — the substrate for fleet/Taey real-time cognitive coordination.

Design (2026-06-25, infra), informed by the prior DCM lessons (dcm/reference/ +
infra-soul/research/dcm_rebuild_foundation.md) AND the gatekeeper open-mandate audit of
the first cut (gatekeeper/reviews/dcm-open-mandate/verdict.md, BLOCK @10e5aad). The audit
defeated all three honesty mechanisms BY EXECUTION; this version is the root-cause fix.

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
import os, json, time, uuid
from urllib.parse import urlparse
from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError

DCM_NEO4J_URI = os.environ.get("DCM_NEO4J_URI", "bolt://localhost:7687")
_USER = os.environ.get("DCM_NEO4J_USER")
_PASSWORD = os.environ.get("DCM_NEO4J_PASSWORD")
_AUTH = (_USER, _PASSWORD) if _USER and _PASSWORD else None
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]", "", None}
_driver = None

_PLAIN_CONTRIBUTION_KINDS = {"contribution", "plan_proposal", "consensus_plan"}
_CONTRIBUTION_KINDS = _PLAIN_CONTRIBUTION_KINDS | {"concern", "resolution"}
_CONCERN_SEVERITIES = {"block", "warn"}
_RESOLUTION_DISPOSITIONS = {
    "FIX-VERIFIED", "FALSE-POSITIVE", "OUT-OF-SCOPE", "ACCEPTED-RISK", "ESCALATE"}
_CLOSING_DISPOSITIONS = {"FIX-VERIFIED", "FALSE-POSITIVE", "OUT-OF-SCOPE", "ACCEPTED-RISK"}
_EVIDENCE_REQUIRED_DISPOSITIONS = {"FIX-VERIFIED", "FALSE-POSITIVE"}
_VETO_CLOSING_DISPOSITIONS = {"FIX-VERIFIED", "ACCEPTED-RISK"}


class StaleReadError(Exception):
    """Raised when a contribution loses the compare-and-set for its slot — a peer took the
    version you were writing into, so committing would ignore them. The adoption contract,
    enforced structurally: re-read (read_session) and redo your turn on the fresh state.
    """
    def __init__(self, current_version: int, your_version: int, new_peer_ids: list[str]):
        self.current_version = current_version
        self.your_version = your_version
        self.new_peer_ids = new_peer_ids
        super().__init__(f"stale read: session at v{current_version}, you read v{your_version}; "
                         f"{len(new_peer_ids)} new peer(s) arrived — re-read and incorporate them")


class UnresolvedConcernsError(Exception):
    """Raised when publish_final would close over unresolved block-severity concerns."""
    def __init__(self, open_concern_ids: list[str]):
        self.open_concern_ids = open_concern_ids
        super().__init__(
            "cannot publish final; unresolved block concern(s): "
            f"{', '.join(open_concern_ids)}")


def _require_safe_uri(uri: str) -> None:
    host = urlparse(uri).hostname
    if host not in _LOOPBACK and _AUTH is None and os.environ.get("DCM_ALLOW_INSECURE") != "1":
        raise RuntimeError(
            f"refusing to connect to non-loopback Neo4j host {host!r} with no auth — a no-auth "
            f"bolt port beyond loopback exposes a full read/write graph (read all sessions, "
            f"forge/delete contributions, flip status/final). Set DCM_NEO4J_USER/DCM_NEO4J_PASSWORD, "
            f"or DCM_ALLOW_INSECURE=1 to override deliberately.")


def _db():
    global _driver
    if _driver is None:
        _require_safe_uri(DCM_NEO4J_URI)
        _driver = GraphDatabase.driver(DCM_NEO4J_URI, auth=_AUTH)
        _driver.verify_connectivity()
        with _driver.session() as s:
            s.run("CREATE CONSTRAINT dcm_session_id IF NOT EXISTS "
                  "FOR (x:DCMSession) REQUIRE x.session_id IS UNIQUE")
            s.run("CREATE CONSTRAINT dcm_contrib_id IF NOT EXISTS "
                  "FOR (c:DCMContribution) REQUIRE c.contrib_id IS UNIQUE")
            # The REAL compare-and-set. One contribution per (session, slot=seq). Two writers at
            # the same read_version both claim seq=read_version; the constraint's index lock lets
            # EXACTLY ONE commit — the rest hit ConstraintError -> StaleReadError. This is the
            # serialization the count-check could not give (Neo4j read-committed locks no count).
            s.run("CREATE CONSTRAINT dcm_contrib_slot IF NOT EXISTS "
                  "FOR (c:DCMContribution) REQUIRE (c.session_id, c.seq) IS UNIQUE")
    return _driver


def _clean_optional_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must be non-empty when provided")
    return value


def _typed_props(kind: str, severity: str | None, about: str | None, veto: bool,
                 disposition: str | None, evidence_ref: str | None) -> dict:
    if kind not in _CONTRIBUTION_KINDS:
        raise ValueError(f"kind must be one of {sorted(_CONTRIBUTION_KINDS)}")
    if not isinstance(veto, bool):
        raise ValueError("veto must be a bool")

    about = _clean_optional_text(about, "about")
    evidence_ref = _clean_optional_text(evidence_ref, "evidence_ref")
    props = {"kind": kind}

    if kind in _PLAIN_CONTRIBUTION_KINDS:
        if severity is not None or about is not None or veto or disposition is not None or evidence_ref is not None:
            raise ValueError("plain contribution kinds cannot carry concern/resolution fields")
        return props

    if kind == "concern":
        if severity not in _CONCERN_SEVERITIES:
            raise ValueError(f"concern severity must be one of {sorted(_CONCERN_SEVERITIES)}")
        if disposition is not None or evidence_ref is not None:
            raise ValueError("concerns cannot carry resolution disposition/evidence_ref")
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
        raise ValueError(f"resolution disposition must be one of {sorted(_RESOLUTION_DISPOSITIONS)}")
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
    if resolution.get("kind") != "resolution" or resolution.get("about") != concern["contrib_id"]:
        return False
    disposition = resolution.get("disposition")
    if disposition not in _CLOSING_DISPOSITIONS:
        return False
    if concern.get("veto") and disposition not in _VETO_CLOSING_DISPOSITIONS:
        return False
    if disposition in _EVIDENCE_REQUIRED_DISPOSITIONS and not _has_evidence(resolution.get("evidence_ref")):
        return False
    return True


def start_session(topic: str, payload: str, roles: list[str] | None = None, *,
                  trust: str = "trusted") -> str:
    """Open a coordination session. payload = the artifact under work (e.g. a draft response).

    trust='untrusted' marks the payload as attacker-influenceable / ISMA-derived; the CLI adapter
    then REFUSES to seat an acting CLI on it without a sandbox (ROUND2_SYNTHESIS §6 item 1)."""
    if trust not in ("trusted", "untrusted"):
        raise ValueError(f"trust must be 'trusted' or 'untrusted', got {trust!r}")
    sid = f"dcm_{uuid.uuid4().hex[:12]}"
    with _db().session() as s:
        s.run("""CREATE (x:DCMSession {session_id:$sid, topic:$topic, payload:$payload,
                 roles:$roles, status:'open', trust:$trust, created:$ts})""",
              sid=sid, topic=topic, payload=payload, roles=roles or [], trust=trust, ts=time.time())
    return sid


def read_session(session_id: str) -> dict:
    """One-read context bundle: topic + payload + ALL peer contributions so far (+ any
    published final). Call this BEFORE contributing — it returns peer work so you can build
    on it, and the `version` you pass back to contribute() as your compare-and-set token.

    Each contribution carries contrib_id (cite the ones you actually read as peers_read),
    plus claimed_peers (each author's own read-claim) and peers_present (who the server saw
    present when they committed).
    """
    with _db().session() as s:
        rec = s.run("MATCH (x:DCMSession {session_id:$sid}) RETURN x", sid=session_id).single()
        if not rec:
            raise ValueError(f"no DCM session {session_id}")
        x = rec["x"]
        # match by the [:IN] relationship so pre-`seq` historical contributions stay readable;
        # order by seq (authoritative for current contributions), created as the null-seq fallback.
        contribs = s.run("""MATCH (c:DCMContribution)-[:IN]->(:DCMSession {session_id:$sid})
                            RETURN c ORDER BY c.seq, c.created""", sid=session_id)
        cs = []
        for c in contribs:
            n = c["c"]
            cs.append({"contrib_id": n["contrib_id"], "role": n["role"],
                       "content": n["content"], "seq": n.get("seq"),
                       "kind": n.get("kind") or "contribution",
                       "severity": n.get("severity"),
                       "about": n.get("about"),
                       "veto": bool(n.get("veto")),
                       "disposition": n.get("disposition"),
                       "evidence_ref": n.get("evidence_ref"),
                       "claimed_peers": n.get("claimed_peers"),
                       "peers_present": n.get("peers_present"),
                       "created": n["created"]})
    return {"session_id": session_id, "topic": x["topic"], "payload": x["payload"],
            "status": x["status"], "final": x.get("final"),
            "trust": x.get("trust") or "trusted",   # default for pre-trust sessions
            "contributions": cs, "version": len(cs)}


def contribute(session_id: str, role: str, content: str, peers_read: list[str],
               read_version: int, *, kind: str = "contribution", severity: str | None = None,
               about: str | None = None, veto: bool = False, disposition: str | None = None,
               evidence_ref: str | None = None) -> str:
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
        raise ValueError("read_version must be the non-negative int 'version' from read_session "
                         "(open a fresh session with read_version=0)")
    typed_props = _typed_props(kind, severity, about, veto, disposition, evidence_ref)
    cid = f"contrib_{uuid.uuid4().hex[:12]}"
    with _db().session() as s:
        try:
            # gate: commit only at the version you read (cnt = $rv); slot is SERVER-derived
            # (seq = cnt), never the caller's value. The (session_id, seq) constraint serializes
            # concurrent same-version writers — exactly one wins the slot.
            rec = s.run("""MATCH (x:DCMSession {session_id:$sid})
                           OPTIONAL MATCH (c:DCMContribution)-[:IN]->(x)
                           WITH x, collect(c.contrib_id) AS present, count(c) AS cnt
                           WHERE cnt = $rv
                           CREATE (n:DCMContribution {contrib_id:$cid, session_id:$sid, seq:cnt,
                                   role:$role, content:$content,
                                   claimed_peers:$claimed, peers_present:present, created:$ts})
                           SET n += $typed_props
                           CREATE (n)-[:IN]->(x)
                           RETURN n.contrib_id AS cid""",
                        sid=session_id, cid=cid, role=role, content=content,
                        claimed=peers_read or [], rv=read_version, ts=time.time(),
                        typed_props=typed_props).single()
        except ConstraintError:
            # slot already taken -> a peer won this version concurrently. Re-read and redo.
            fresh = read_session(session_id)
            known = set(peers_read or [])
            new_ids = [c["contrib_id"] for c in fresh["contributions"] if c["contrib_id"] not in known]
            raise StaleReadError(fresh["version"], read_version, new_ids)
        if rec is None:
            # gate failed (cnt != read_version: a peer arrived, or read_version is stale/
            # fabricated) — read_session raises ValueError if the session truly doesn't exist.
            fresh = read_session(session_id)
            known = set(peers_read or [])
            new_ids = [c["contrib_id"] for c in fresh["contributions"] if c["contrib_id"] not in known]
            raise StaleReadError(fresh["version"], read_version, new_ids)
        return rec["cid"]


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
            silos.append({"role": c["role"], "seq": c.get("seq"), "ignored_count": len(ignored)})
    return {"contributions": len(cs),
            "opening": cs[0]["role"] if cs else None,
            "built_on_peers": [c["role"] for i, c in enumerate(cs)
                               if i > 0 and (set(c.get("claimed_peers") or []) & {p["contrib_id"] for p in cs[:i]})],
            "silo_violations": silos,
            "coordinated": len(cs) > 1 and not silos}


def open_concerns(session_id: str) -> list[dict]:
    """Return block-severity concerns that lack a valid closing resolution.

    Closure is a projection over the append log: a resolution closes only the concern it names
    in `about`, ESCALATE does not close, FIX-VERIFIED/FALSE-POSITIVE need non-empty evidence,
    and safety veto concerns can be closed only by FIX-VERIFIED or ACCEPTED-RISK. This is graph
    state, not a semantic claim that the evidence is true.
    """
    cs = read_session(session_id)["contributions"]
    resolutions = [c for c in cs if c.get("kind") == "resolution"]
    return [c for c in cs
            if c.get("kind") == "concern"
            and c.get("severity") == "block"
            and not any(_resolution_closes(c, r) for r in resolutions)]


def publish_final(session_id: str, final: str) -> None:
    """Close the session only when the append-log concern projection is clear.

    The DISTILLED final is what's eligible to flow to ISMA (not the sausage). read_session()
    surfaces it as `final`. Honest scope: this enforces that block concerns have valid typed
    resolutions with required evidence refs; it does not prove the external evidence itself.
    """
    open_ids = [c["contrib_id"] for c in open_concerns(session_id)]
    if open_ids:
        raise UnresolvedConcernsError(open_ids)
    with _db().session() as s:
        s.run("MATCH (x:DCMSession {session_id:$sid}) SET x.status='closed', x.final=$final, x.closed=$ts",
              sid=session_id, final=final, ts=time.time())


if __name__ == "__main__":
    import sys
    print(json.dumps(verify_coordination(sys.argv[1]), indent=2))
