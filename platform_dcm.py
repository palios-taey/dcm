"""platform_dcm — generalized DCM orchestration for fixing one consult/UI driver end-to-end.

Reusable, parameter-driven (NO operator paths/displays/repo names baked in — all runtime args).
Captures the loop used to take a driver from broken→verified: a producer (codex) implements the
assessed fix in the target worktree + verifies on REAL runs, then a blind N-seat diff audit
(real CLIs through the mesh) checks it for banned shapes / blast-radius / root-cause-vs-patch.

Subcommands:
  produce  --target-repo <wt> --prompt-file <f> [--model gpt-5.5]
             run a producer (codex) in the target worktree from a prompt file (no synthetic tests;
             the producer verifies on real production runs — production is the oracle).
  audit    --diff-file <patch> --topic <str> [--seats root-cause:grok,scope-blast:gemini,fallback-hunter:claude]
             seat a blind N-lens diff audit through the mesh; prints one VERDICT line per seat.

Substrate: mesh.py + cli_adapter.py (this repo). The producer edits the TARGET repo (its driver code
legitimately lives there); this orchestration + its record live HERE, in the public dcm repo.
"""
import os, sys, argparse, subprocess, re, json, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesh, cli_adapter

_DEFAULT_SEATS = "root-cause:grok,scope-blast:gemini,fallback-hunter:claude"
_LENS = {
    "root-cause":      "ROOT-CAUSE/6SIGMA: is the fix root-cause (SIMPLIFIES — corrects the upstream shape) or a patch (adds a branch to bypass)? Reject hidden retries/settle-polls disguised as observation.",
    "scope-blast":     "SCOPE+BLAST: does the diff stay in declared scope? Do shared-file edits risk regressing OTHER consumers? Flag merge-reconciliation risk, destructive ops, secrets, injection.",
    "fallback-hunter": "FALLBACK-HUNTER: any banned shape — silent fallback, settle-poll-until-present, action-retry, swallowed error, fabricated/seeded state, positive-completion-marker, coord-click? The fix must OBSERVE, never fabricate or re-ACT.",
}

def produce(args):
    prompt = open(args.prompt_file).read()
    # codex reads the prompt from stdin (`-`); argv can exceed MAX_ARG_STRLEN on big prompts.
    p = subprocess.run(["codex", "exec", "-C", args.target_repo, "-m", args.model,
                        "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-"],
                       input=prompt, text=True)
    sys.exit(p.returncode)

def _seat(sid, role, cli):
    lens = _LENS.get(role, f"{role} reviewer. End with exactly one VERDICT line.")
    lens += " End with EXACTLY ONE line: 'VERDICT: PASS' | 'VERDICT: CONCERN-WARN <x>' | 'VERDICT: CONCERN-BLOCK <x>'."
    try:
        r = cli_adapter.cli_expert(sid, role, lens, cli=cli, peers_visible=False, return_record=True, timeout=args_timeout)
        return (role, cli, "ok", r["contrib_id"])
    except Exception as e:
        return (role, cli, "FAIL", f"{type(e).__name__}: {e}")

def audit(args):
    global args_timeout; args_timeout = args.timeout
    diff = open(args.diff_file).read()
    seats = [s.split(":", 1) for s in args.seats.split(",") if s]
    payload = (f"Blind diff audit — topic: {args.topic}. Verify the change below on the diff itself.\n\n=== DIFF ===\n" + diff)
    sid = mesh.start_session(f"AUDIT::{args.topic}", payload, roles=[r for r, _ in seats])
    print("audit session:", sid)
    with cf.ThreadPoolExecutor(max_workers=len(seats)) as ex:
        for f in [ex.submit(_seat, sid, r, c) for r, c in seats]:
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
    pa = sub.add_parser("audit"); pa.add_argument("--diff-file", required=True); pa.add_argument("--topic", required=True); pa.add_argument("--seats", default=_DEFAULT_SEATS); pa.add_argument("--timeout", type=int, default=400); pa.set_defaults(fn=audit)
    a = p.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
