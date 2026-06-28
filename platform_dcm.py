"""platform_dcm — generalized DCM orchestration for fixing one consult/UI driver end-to-end.

Reusable, parameter-driven (NO operator paths/displays/repo names baked in — all runtime args).
Captures the loop used to take a driver from broken→verified: a producer (codex) implements the
assessed fix in the target worktree + verifies on REAL runs, then a blind N-seat diff audit
(real CLIs through the mesh) checks it for banned shapes / blast-radius / root-cause-vs-patch.

Subcommands:
  produce  --target-repo <wt> --prompt-file <f> [--model gpt-5.5]
             run a producer (codex) in the target worktree from a prompt file (no synthetic tests;
             the producer verifies on real production runs — production is the oracle).
  audit    --diff-file <patch> --topic <str> [--seats role:cli,... (scoped override)]
             seat a blind diff audit through the mesh on the CANONICAL §4 reviewer roster
             (council.canonical_reviewer_roster) + consult-driver banned-shape emphasis;
             prints one VERDICT line per seat. --seats overrides to a deliberate subset.

Substrate: mesh.py + cli_adapter.py (this repo). The producer edits the TARGET repo (its driver code
legitimately lives there); this orchestration + its record live HERE, in the public dcm repo.
"""
import os, sys, argparse, subprocess, re, json, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh, cli_adapter, council, scaling

# Seats come from council.canonical_reviewer_roster() (the §4 roster — single source of truth);
# this audit never redefines the roster. It ADDS consult-driver banned-shape emphasis per canonical
# role so the diff audit keeps its domain teeth. --seats overrides only for a deliberate scoped run.
# ROUND2_SYNTHESIS.md §4 + §5 item 2 (reconcile the harness to the design roster).
_AUDIT_ADDENDUM = {
    "evasive-repair": " CONSULT-DRIVER BANNED SHAPES: silent fallback, settle-poll-until-present, "
                      "action-retry, swallowed error, fabricated/seeded state, positive-completion-marker, "
                      "coord-click. The fix must OBSERVE, never fabricate or re-ACT; root-cause (SIMPLIFIES "
                      "the upstream shape), never a patch (adds a branch to bypass).",
    "scope-blast":    " CONSULT-DRIVER SCOPE/BLAST: does the diff stay in declared scope? Do shared-file "
                      "edits risk regressing OTHER consumers? Flag merge-reconciliation risk, destructive "
                      "ops, secrets, injection.",
}
_VERDICT_INSTR = (" End with EXACTLY ONE line: 'VERDICT: PASS' | 'VERDICT: CONCERN-WARN <x>' | "
                  "'VERDICT: CONCERN-BLOCK <x>'.")

def produce(args):
    prompt = open(args.prompt_file).read()
    # codex reads the prompt from stdin (`-`); argv can exceed MAX_ARG_STRLEN on big prompts.
    p = subprocess.run(["codex", "exec", "-C", args.target_repo, "-m", args.model,
                        "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-"],
                       input=prompt, text=True)
    sys.exit(p.returncode)

def _seat(sid, role, cli, lens):
    try:
        r = cli_adapter.cli_expert(sid, role, lens, cli=cli, peers_visible=False, return_record=True, timeout=args_timeout)
        return (role, cli, "ok", r["contrib_id"])
    except Exception as e:
        return (role, cli, "FAIL", f"{type(e).__name__}: {e}")

def _audit_seats(seats_override: str | None, tier: str = "standard") -> dict:
    """Return {role: (cli, full_lens)} seated for the blast-radius TIER (compress/standard/expand;
    §4 scaling), each lens = reviewer lens + consult-driver addendum + the verdict line.
    seats_override ('role:cli,role:cli') is a deliberate scoped subset, still lensed from the pool."""
    roster = scaling.reviewer_roster_for_tier(tier)
    def full_lens(role, base):
        return base + _AUDIT_ADDENDUM.get(role, "") + _VERDICT_INSTR
    if seats_override:
        pool = scaling._role_pool()
        out = {}
        for s in seats_override.split(","):
            if not s:
                continue
            role, cli = s.split(":", 1)
            base = pool.get(role, {}).get("lens", f"You are the {role} reviewer.")
            out[role] = (cli, full_lens(role, base))
        return out
    return {role: (spec["cli"], full_lens(role, spec["lens"])) for role, spec in roster.items()}

def audit(args):
    global args_timeout; args_timeout = args.timeout
    diff = open(args.diff_file).read()
    seats = _audit_seats(args.seats, args.tier)
    payload = (f"Blind diff audit — topic: {args.topic}. Verify the change below on the diff itself.\n\n=== DIFF ===\n" + diff)
    sid = mesh.start_session(f"AUDIT::{args.topic}", payload, roles=list(seats))
    print("audit session:", sid)
    print(f"tier: {args.tier} ({len(seats)} seats)")
    print("roster:", ", ".join(f"{r}:{c}" for r, (c, _) in seats.items()))
    with cf.ThreadPoolExecutor(max_workers=len(seats)) as ex:
        for f in [ex.submit(_seat, sid, r, c, lens) for r, (c, lens) in seats.items()]:
            print(f.result())
    d = mesh.read_session(sid)
    print("=== VERDICTS ===")
    for c in d["contributions"]:
        m = re.search(r"VERDICT:\s*(.+)", c.get("content") or "")
        print(f"[{c['role']}] {m.group(1).strip()[:160] if m else '(none)'}")

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("produce"); pp.add_argument("--target-repo", required=True); pp.add_argument("--prompt-file", required=True); pp.add_argument("--model", default="gpt-5.5"); pp.set_defaults(fn=produce)
    pa = sub.add_parser("audit"); pa.add_argument("--diff-file", required=True); pa.add_argument("--topic", required=True); pa.add_argument("--tier", default="standard", choices=["compress", "standard", "expand"], help="blast-radius tier: compress(3)/standard(4)/expand(9) reviewers (§4 scaling)"); pa.add_argument("--seats", default=None, help="optional scoped override 'role:cli,...'; else the tier roster"); pa.add_argument("--timeout", type=int, default=400); pa.set_defaults(fn=audit)
    a = p.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
