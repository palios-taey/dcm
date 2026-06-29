"""council_cli — run a DCM council with ZERO improvisation. The canonical invocation.

    python council_cli.py plan   --problem-file P --rules-file R
    python council_cli.py review --task "<t>" --artifact-file A --rules-file R

`plan`   → the council produces a consensus implementation PLAN from a problem (council_plan).
`review` → the council reviews an ARTIFACT and returns a verdict, fail-closed on open block
           concerns (council_review): Foundation pre-flight grounding → cite-or-block citation
           gate → destructive-ops floor scan → blind round → reveal/resolution → publish.

Every council seats the FULL defined-role library (9 reviewers + synthesizer/clerk = a 10–12-seat
council). There is no smaller option and no panel knob — a tiny council is the rejected stub.

Inputs are FILES (operator content is a runtime arg, never committed). This fires the REAL CLIs
through the mesh — production is the oracle, there are no synthetic tests. Prints the final
consensus/verdict + the honest coordination record; exit 1 if the council BLOCKED (review) or a
CLI failed.
"""
import argparse
import sys
import json
import council


def _emit(status: str, body: str, ledger: dict) -> int:
    print("=== STATUS:", status, "===\n")
    print(body)
    coord = (ledger or {}).get("coordination")
    if coord is not None:
        print("\n=== COORDINATION (honest) ===")
        print(json.dumps(coord, indent=2, default=str))
    # not-published is a non-zero exit so a caller's pipeline halts on it.
    return 0 if status == "published" else 1


def _plan(args) -> int:
    problem = open(args.problem_file).read()
    rules = open(args.rules_file).read()
    r = council.council_plan(problem, rules)          # -> {"plan", "per_role", "ledger"}
    ledger = r.get("ledger", {})
    status = (ledger.get("publish") or {}).get("status", "unknown")
    return _emit(status, r.get("plan") or "(no consensus plan produced)", ledger)


def _review(args) -> int:
    artifact = open(args.artifact_file).read()
    rules = open(args.rules_file).read()
    r = council.council_review(args.task, artifact, rules)  # full roster -> {"verdict", ...}
    v = r["verdict"]
    body = v.get("final") or v.get("final_candidate") or json.dumps(v, indent=2, default=str)
    return _emit(v["status"], body, r.get("ledger", {}))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan", help="produce a consensus implementation plan from a problem")
    pp.add_argument("--problem-file", required=True)
    pp.add_argument("--rules-file", required=True)
    pp.set_defaults(fn=_plan)
    pr = sub.add_parser("review", help="review an artifact; verdict fail-closed on open block concerns")
    pr.add_argument("--task", required=True)
    pr.add_argument("--artifact-file", required=True)
    pr.add_argument("--rules-file", required=True)
    pr.set_defaults(fn=_review)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
