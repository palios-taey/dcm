"""
B-baseline Review Cell (per research/DCM_DESIGN.md): 1 producer + 1 DIFFERENT-MODEL
execution-grounded reviewer + memory. The null-to-beat in the 5-arm eval.

- Producer = the multi-CLI solver (solver.solve).
- Reviewer = a DIFFERENT base model (decorrelation is mandatory — same-model review is
  non-identifying under correlated errors). It RUNS the code in a throwaway worktree
  (execution-grounded, not read-judge) and returns a verdict. We keep ONLY its verdict,
  so any edits it makes in its throwaway worktree are discarded — it reviews, never writes
  the solution.
- On BLOCK, the producer revises addressing the specific concern; re-review; up to k rounds.
- The cell's final patch is graded by the OFFICIAL hidden harness (the eval oracle) — the
  reviewer's verdict is NEVER the success signal (that would be self-report).

Firewall: the reviewer may run the repo's own/visible tests, never the hidden grading suite.
"""
from __future__ import annotations
import os, re, shutil, subprocess, tempfile, time
from pathlib import Path
from solver import _cmd, solve
from solver_codex import extract_repo, SOLVE_PROMPT

REVIEW_PROMPT = """You are an INDEPENDENT code reviewer. You did NOT write this patch. The repository is in your working directory at the buggy commit WITH the producer's patch already applied.

Issue the patch is meant to resolve:
---
{problem}
---
The patch under review (already applied to your working copy):
---
{patch}
---
Review it — do NOT rewrite the solution yourself:
1. RUN the relevant code / existing tests to verify the fix actually works. Judge by execution, not by reading.
2. Check specifically for: hidden fallbacks / swallowed exceptions / silent workarounds; a symptom-patch instead of the root cause; hardcoded values that should be general; whether it genuinely satisfies the issue.
End your output with EXACTLY one line:
  VERDICT: APPROVE
or
  VERDICT: BLOCK - <one specific, addressable concern + the evidence you observed>
"""

REVISE_PROMPT = """A reviewer BLOCKED your patch for this issue. Address the concern with a minimal, root-cause fix. Edit the source directly. Do not write new tests or game them.

Issue:
---
{problem}
---
Reviewer's blocking concern:
---
{concern}
---
Fix the concern and stop."""


def review(instance: dict, patch: str, reviewer_cli: str, timeout_s: int = 900) -> dict:
    """Different-model execution-grounded review. Returns {verdict: approve|block, concern, parsed, log}."""
    wd = Path(tempfile.mkdtemp(prefix=f"review_{reviewer_cli}_{instance['instance_id']}_"))
    env = os.environ.copy(); env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    try:
        extract_repo(instance, wd)
        if patch.strip():
            subprocess.run(["git", "-C", str(wd), "apply", "--3way", "-"], input=patch, text=True,
                           capture_output=True) or subprocess.run(
                ["patch", "-d", str(wd), "-p1", "--forward"], input=patch, text=True, capture_output=True)
        prompt = REVIEW_PROMPT.format(problem=instance["problem_statement"], patch=patch[:6000])
        p = subprocess.run(_cmd(reviewer_cli, wd, prompt), cwd=str(wd), env=env,
                           capture_output=True, text=True, timeout=timeout_s)
        out = (p.stdout or "") + (p.stderr or "")
        m = re.search(r"VERDICT:\s*(APPROVE|BLOCK)\s*[-—:]?\s*(.*)", out, re.I | re.S)
        if not m:
            # no parseable verdict -> conservative BLOCK, flagged unparsed (logged, audited)
            return {"verdict": "block", "concern": "Reviewer output did not contain a parseable VERDICT line.",
                    "parsed": False, "log": out[-1500:]}
        return {"verdict": m.group(1).lower(), "concern": (m.group(2) or "").strip()[:600],
                "parsed": True, "log": out[-1500:]}
    finally:
        shutil.rmtree(wd, ignore_errors=True)


def revise(instance: dict, patch: str, concern: str, producer_cli: str, timeout_s: int = 1200) -> str:
    """Producer revises the patch to address the reviewer's concern; return the complete diff vs base."""
    wd = Path(tempfile.mkdtemp(prefix=f"revise_{producer_cli}_{instance['instance_id']}_"))
    env = os.environ.copy(); env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    try:
        extract_repo(instance, wd)
        if patch.strip():
            subprocess.run(["git", "-C", str(wd), "apply", "--3way", "-"], input=patch, text=True, capture_output=True)
        prompt = REVISE_PROMPT.format(problem=instance["problem_statement"], concern=concern or "(unspecified)")
        subprocess.run(_cmd(producer_cli, wd, prompt), cwd=str(wd), env=env,
                       capture_output=True, text=True, timeout=timeout_s)
        return subprocess.check_output(
            ["git", "-C", str(wd), "diff", "--no-color", instance["base_commit"]], text=True)
    finally:
        shutil.rmtree(wd, ignore_errors=True)


class BaselineCell:
    """B = producer + 1 different-model execution-grounded reviewer + memory (null-to-beat)."""
    name = "B"
    def __init__(self, producer_cli: str = "codex", reviewer_cli: str = "claude", max_rounds: int = 2):
        assert reviewer_cli != producer_cli, "reviewer MUST be a different base model (decorrelation)"
        self.producer_cli, self.reviewer_cli, self.max_rounds = producer_cli, reviewer_cli, max_rounds

    def __call__(self, instance: dict):
        from harness import ArmResult
        t0 = time.time()
        patch = solve(instance, self.producer_cli)["model_patch"]
        rounds = []
        for rnd in range(self.max_rounds):
            v = review(instance, patch, self.reviewer_cli)
            rounds.append({"round": rnd, "verdict": v["verdict"], "parsed": v["parsed"], "concern": v["concern"][:160]})
            if v["verdict"] == "approve":
                break
            if patch.strip():
                patch = revise(instance, patch, v["concern"], self.producer_cli)
        return ArmResult(instance_id=instance["instance_id"], model_patch=patch, wall_clock_s=time.time() - t0,
                         notes=f"B prod={self.producer_cli} rev={self.reviewer_cli} rounds={rounds}")


if __name__ == "__main__":
    import sys, json
    from harness import load_instances
    iid = sys.argv[1] if len(sys.argv) > 1 else "pylint-dev__pylint-7080"
    prod = sys.argv[2] if len(sys.argv) > 2 else "codex"
    rev = sys.argv[3] if len(sys.argv) > 3 else "claude"
    inst = load_instances([iid])[iid]
    print(f"[B-smoke] producer={prod} reviewer={rev} on {iid}")
    res = BaselineCell(prod, rev)(inst)
    print(res.notes)
    print(f"final patch lines: {len(res.model_patch.splitlines())}")
