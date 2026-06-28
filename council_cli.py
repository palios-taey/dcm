"""council_cli — run a DCM council with ZERO improvisation. The canonical invocation.

    python council_cli.py plan   --problem-file P --rules-file R
    python council_cli.py review --task "<t>" --artifact-file A --rules-file R [--tier standard]

`plan`   → the council produces a consensus implementation PLAN from a problem (council_plan).
`review` → the council reviews an ARTIFACT and returns a verdict, fail-closed on open block
           concerns (council_review): Foundation pre-flight grounding → cite-or-block citation
           gate → destructive-ops floor scan → blind round → reveal/resolution → publish.

--tier (review only) scales the roster by blast radius: compress(3) / standard(4) / expand(9)
reviewers. Use expand for high-blast-radius artifacts (secrets / migration / release / cross-repo
/ gitnexus_impact HIGH-CRITICAL); standard is the default.

Inputs are FILES (operator content is a runtime arg, never committed). This fires the REAL CLIs
through the mesh — production is the oracle, there are no synthetic tests. Prints the final
consensus/verdict + the honest coordination record; exit 1 if the council BLOCKED (review) or a
CLI failed.
"""
import argparse
import sys
import json
import council


def _emit(result: dict) -> int:
    verdict = result["verdict"]
    final = verdict.get("final") or verdict.get("final_candidate")
    print("=== STATUS:", verdict["status"], "===\n")
    print(final or json.dumps(verdict, indent=2, default=str))
    coord = result.get("ledger", {}).get("coordination")
    if coord is not None:
        print("\n=== COORDINATION (honest) ===")
        print(json.dumps(coord, indent=2, default=str))
    # blocked review / unpublished plan is a non-zero exit so a caller's pipeline halts on it.
    return 0 if verdict["status"] == "published" else 1


def _plan(args) -> int:
    problem = open(args.problem_file).read()
    rules = open(args.rules_file).read()
    return _emit(council.council_plan(problem, rules))


def _review(args) -> int:
    artifact = open(args.artifact_file).read()
    rules = open(args.rules_file).read()
    return _emit(council.council_review(args.task, artifact, rules, tier=args.tier))


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
    pr.add_argument("--tier", default="standard", choices=["compress", "standard", "expand"],
                    help="blast-radius roster: compress(3)/standard(4)/expand(9) reviewers")
    pr.set_defaults(fn=_review)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
