"""mesh_cli — the DCM coordination interface for agents (Claude subagents / Codex / any shell).

The mesh (Neo4j) is the team's PERSISTENT shared brain. Every agent coordinates through it:
  1. READ the session  -> peers' contributions so far + the `version` token (read-before-write).
  2. reason, building on / arguing with peers (never work alone).
  3. CONTRIBUTE with that version -> commits only if no peer arrived since (compare-and-set);
     on STALE you get the fresh state back and MUST re-read + redo (someone built while you thought).

Because state lives in the mesh, a respawned agent just READs and continues where the team is.

Commands:
  start  "<topic>" "<payload>" [--roles a,b,c]                 -> session_id
  read   <session_id>                                          -> JSON {version, final, status, contributions[]}
  contribute <session_id> <role> <read_version> [--kind ...] [--content - | "text"]
             [--peers id1,id2] [--severity block|warn] [--about cid]
             [--disposition FIX-VERIFIED|FALSE-POSITIVE|OUT-OF-SCOPE|ACCEPTED-RISK|ESCALATE] [--evidence-ref s]
             -> {ok, contrib_id} OR {ok:false, stale:true, fresh_version, new_peers[]} (re-read + redo)
  status <session_id>                                          -> {open_concerns[], final, status}
  publish <session_id> [--content - | "text"]                  -> publishes final (FAILS CLOSED on open block-concerns)
"""
import os, sys, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh


def _content(arg):
    if arg == "-" or arg is None:
        return sys.stdin.read()
    return arg


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("start"); ps.add_argument("topic"); ps.add_argument("payload"); ps.add_argument("--roles", default="")
    pr = sub.add_parser("read"); pr.add_argument("session_id")
    pc = sub.add_parser("contribute")
    pc.add_argument("session_id"); pc.add_argument("role"); pc.add_argument("read_version", type=int)
    pc.add_argument("--kind", default="contribution")
    pc.add_argument("--content", default="-")
    pc.add_argument("--peers", default="")
    pc.add_argument("--severity", default=None)
    pc.add_argument("--about", default=None)
    pc.add_argument("--disposition", default=None)
    pc.add_argument("--evidence-ref", default=None)
    pst = sub.add_parser("status"); pst.add_argument("session_id")
    pp = sub.add_parser("publish"); pp.add_argument("session_id"); pp.add_argument("--content", default="-")
    a = p.parse_args()

    if a.cmd == "start":
        roles = [r for r in a.roles.split(",") if r]
        print(json.dumps({"session_id": mesh.start_session(a.topic, a.payload, roles=roles)}))
    elif a.cmd == "read":
        print(json.dumps(mesh.read_session(a.session_id), default=str))
    elif a.cmd == "contribute":
        peers = [x for x in a.peers.split(",") if x]
        kw = {"kind": a.kind}
        if a.severity: kw["severity"] = a.severity
        if a.about: kw["about"] = a.about
        if a.disposition: kw["disposition"] = a.disposition
        if a.evidence_ref: kw["evidence_ref"] = a.evidence_ref
        try:
            cid = mesh.contribute(a.session_id, a.role, _content(a.content), peers, a.read_version, **kw)
            print(json.dumps({"ok": True, "contrib_id": cid}))
        except mesh.StaleReadError as e:
            # someone contributed since you read — re-read + redo (the whole point of read-before-write)
            print(json.dumps({"ok": False, "stale": True, "fresh_version": e.current_version,
                              "your_read_version": e.your_version, "new_peer_ids": e.new_peer_ids}))
            sys.exit(2)
    elif a.cmd == "status":
        oc = mesh.open_concerns(a.session_id)
        sess = mesh.read_session(a.session_id)
        print(json.dumps({"open_concerns": oc, "final": sess.get("final"), "status": sess.get("status")}, default=str))
    elif a.cmd == "publish":
        try:
            mesh.publish_final(a.session_id, _content(a.content)); print(json.dumps({"ok": True, "published": True}))
        except mesh.UnresolvedConcernsError as e:
            print(json.dumps({"ok": False, "blocked": True, "open_concern_ids": list(e.open_concern_ids)})); sys.exit(3)


if __name__ == "__main__":
    main()
