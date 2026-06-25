"""DCM council runner — spin up N differently-prompted "experts" that deliberate on the
mesh (Grok-Heavy-style: many agents, in parallel, forced to read + build on each other).

Thin layer over mesh.py: open_council() stages a session; EXPERT_CONTRACT is the standard
participation contract every expert prompt embeds (read -> think -> contribute(read_version);
on StaleReadError, re-read + incorporate the peers who arrived + retry). That staleness
loop is what turns "N agents in parallel" into "N agents talking to each other" — a later
committer cannot land until it has incorporated earlier peers. council_report() prints the
full deliberation transcript + the coordination verdict + the synthesized final.

The experts themselves are spawned via the Agent/Task tool by the conducting session (that
tool isn't callable from here); this module provides the bookends + the contract text so
every council is consistent and the read-discipline is never optional.
"""
from __future__ import annotations
import sys, json
import mesh

# The contract every expert embeds. {sid} filled per session.
EXPERT_CONTRACT = """You are one expert in a DCM (Distributed Cognitive Mesh) council. Peers
work the SAME session in parallel. The mesh ENFORCES that you read peers before you commit.

Mesh: from the dcm/ directory, python3 with `import mesh`. Session: {sid}

Protocol (do exactly this, looping until your contribution lands):
1. ctx = mesh.read_session('{sid}')  -> note ctx['version'] and ctx['contributions'] (peers).
2. Form YOUR expert view of ctx['payload'] THROUGH YOUR LENS (below). Read every peer
   contribution; build on / sharpen / respectfully disagree with them — do not just restate.
   GROUNDED form (mandatory — kills sycophancy + same-answer-different-reasoning collapse):
   state each CLAIM with its GROUND (the evidence/reason it rests on), and for each peer you
   engage give an explicit STANCE — Agree / Disagree / Extend — WITH justification. Never adopt
   a peer's conclusion with no reasoning of your own; never agree just to converge.
3. peers = [c['contrib_id'] for c in ctx['contributions']]   # the peers you ACTUALLY read+used
   try: cid = mesh.contribute('{sid}', '<your_role>', '<your contribution>', peers_read=peers, read_version=ctx['version'])
   except mesh.StaleReadError: GOTO 1 (a peer took your slot — re-read, incorporate them, retry).
4. Return ONLY: your contrib_id, peers_read count, and your contribution text (concise, dense).

read_version=ctx['version'] is your compare-and-set token (the opener reads an empty session
-> version 0 -> contributes read_version=0). peers_read is your read-CLAIM: verify_coordination
flags you if you omit a peer that was present to you, so cite what you genuinely read.
"""

def open_council(topic: str, payload: str, roles: list[str]) -> str:
    return mesh.start_session(topic, payload, roles=roles)

def council_report(session_id: str) -> dict:
    s = mesh.read_session(session_id)
    v = mesh.verify_coordination(session_id)
    out = {"session_id": session_id, "topic": s["topic"], "status": s["status"],
           "verdict": v, "final": s.get("final"),
           "transcript": [{"role": c["role"], "claimed_reads": len(c.get("claimed_peers") or []),
                           "content": c["content"]} for c in s["contributions"]]}
    return out

if __name__ == "__main__":
    print(json.dumps(council_report(sys.argv[1]), indent=2))
