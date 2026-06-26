"""End-to-end validation that the DCM substrate WORKS against live Neo4j.
Throwaway session, scoped cleanup (deletes ONLY this test session by id). Validates:
1. start/read/contribute sequential build (read-before-write)
2. CAS serialization: N concurrent writers at the same version -> exactly ONE commits, rest StaleReadError
3. verify_coordination: passes honest claims, FLAGS a non-claiming silo
4. evidence-gated publish: unresolved block concern refuses, FIX-VERIFIED evidence closes
"""
import os, sys, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh

PASS = True
def check(name, cond):
    global PASS
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    PASS = PASS and cond

sid = mesh.start_session("VALIDATION (throwaway)", "test payload", roles=["a", "b", "c"])
print(f"session: {sid}")
try:
    # 1. sequential build
    r0 = mesh.read_session(sid); check("fresh session version==0", r0["version"] == 0)
    a = mesh.contribute(sid, "a", "A's contribution", peers_read=[], read_version=0)
    r1 = mesh.read_session(sid); check("after A version==1", r1["version"] == 1)
    b = mesh.contribute(sid, "b", "B builds on A", peers_read=[a], read_version=1)
    r2 = mesh.read_session(sid); check("after B version==2", r2["version"] == 2)
    check("B claimed A as peer", a in [c["claimed_peers"] for c in r2["contributions"] if c["contrib_id"] == b][0])

    # 2. CAS serialization: 6 concurrent writers ALL at version==2 -> exactly one wins
    def race_write(i):
        try:
            mesh.contribute(sid, f"racer{i}", f"racer {i}", peers_read=[a, b], read_version=2)
            return "won"
        except mesh.StaleReadError:
            return "stale"
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(race_write, range(6)))
    wins = results.count("won"); stales = results.count("stale")
    check(f"6 concurrent writers @v2: exactly 1 won (got {wins} won, {stales} stale)", wins == 1 and stales == 5)
    r3 = mesh.read_session(sid); check("version advanced by exactly 1 (==3)", r3["version"] == 3)

    # 3. verify_coordination: honest so far -> should be clean; then inject a silo
    v_clean = mesh.verify_coordination(sid)
    print(f"  verify_coordination (honest): {v_clean}")
    # inject a silo: contribute at the current version but CLAIM no peers despite peers present
    rN = mesh.read_session(sid)
    mesh.contribute(sid, "silo", "ignored everyone", peers_read=[], read_version=rN["version"])
    v_silo = mesh.verify_coordination(sid)
    flagged = bool(v_silo.get("silo_violations")) and v_silo.get("coordinated") is False
    check("verify_coordination clean BEFORE silo (coordinated==True)", v_clean.get("coordinated") is True)
    print(f"  verify_coordination (after silo): {v_silo}")
    check("verify_coordination FLAGS the non-claiming silo", flagged)

    # 4. evidence-gated publish: block concern refuses; evidence-backed resolution closes it.
    rC = mesh.read_session(sid)
    all_peers = [c["contrib_id"] for c in rC["contributions"]]
    concern = mesh.contribute(sid, "safety", "blocking concern: publish must wait for evidence",
                              peers_read=all_peers, read_version=rC["version"], kind="concern",
                              severity="block", about=b, veto=True)
    open_before = mesh.open_concerns(sid)
    check("open_concerns includes the block concern",
          [c["contrib_id"] for c in open_before] == [concern])
    refused = False
    try:
        mesh.publish_final(sid, "should not publish")
    except mesh.UnresolvedConcernsError as e:
        refused = concern in e.open_concern_ids
        print(f"  publish_final refused unresolved concern ids: {e.open_concern_ids}")
    check("publish_final REFUSES unresolved block concern", refused)

    rR = mesh.read_session(sid)
    all_peers = [c["contrib_id"] for c in rR["contributions"]]
    mesh.contribute(sid, "runner", "FIX-VERIFIED: live validation artifact attached",
                    peers_read=all_peers, read_version=rR["version"], kind="resolution",
                    about=concern, disposition="FIX-VERIFIED",
                    evidence_ref="validate_substrate.py live Neo4j run")
    check("open_concerns empty after FIX-VERIFIED evidence", mesh.open_concerns(sid) == [])
    published = False
    try:
        mesh.publish_final(sid, "validated final")
        closed = mesh.read_session(sid)
        published = closed["status"] == "closed" and closed["final"] == "validated final"
    except mesh.UnresolvedConcernsError as e:
        print(f"  unexpected publish refusal after resolution: {e.open_concern_ids}")
    check("publish_final SUCCEEDS after valid resolution", published)
finally:
    # scoped cleanup: ONLY this test session + its contributions, by id (never unscoped)
    with mesh._db().session() as s:
        s.run("""MATCH (x:DCMSession {session_id:$sid}) OPTIONAL MATCH (c:DCMContribution)-[:IN]->(x)
                 DETACH DELETE x, c""", sid=sid)
    print(f"cleaned up {sid}")

print(f"\n=== DCM SUBSTRATE VALIDATION: {'PASS' if PASS else 'FAIL'} ===")
sys.exit(0 if PASS else 1)
