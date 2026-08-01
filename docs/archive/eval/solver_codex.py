"""
Codex-exec solver backend for the DCM SWE-bench eval.

No metered API key exists on this fleet (Observed 2026-06-25); the strong model
available is the ChatGPT-OAuth `codex exec` CLI. This drives it as a controlled
"instance -> patch" solver:
  1. extract the repo (at base_commit, correct env layout) from the swebench
     instance image to a throwaway host workdir
  2. run `codex exec` non-interactively in that workdir on the problem statement
  3. capture `git diff` as the model_patch

Grading is still done ONLY by the official harness against hidden tests.
This is the S-arm substrate; V/I/D wrap it. Model+version frozen per run.

NOTE: the solver runs on the HOST checkout, so codex cannot run the project's
own conda test env mid-solve (it can still read/reason/edit + run plain python).
That is a documented baseline limitation, surfaced for the Family review (Q5).
"""
from __future__ import annotations
import shutil, subprocess, tempfile, time
from pathlib import Path
CODEX_MODEL = "gpt-5.5"  # frozen per run; the ChatGPT-account-supported model (gpt-5-codex is rejected on this OAuth account). Recorded in the ledger/notes.


def extract_repo(instance: dict, dest: Path) -> Path:
    """Clone the repo at base_commit into dest.

    Source state is fully determined by (repo, base_commit) — no dependency on
    swebench image naming or a pre-pulled instance image. Grading still happens
    in the official harness image; this is only the solver's working copy.
    """
    repo = instance["repo"]          # e.g. "pylint-dev/pylint"
    base = instance["base_commit"]
    url = f"https://github.com/{repo}.git"
    dest.mkdir(parents=True, exist_ok=True)
    # autocrlf=false: some repos' smudge filters / .gitattributes make a fresh clone
    # appear dirty, which makes a plain `checkout` refuse. --force discards that.
    subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "--quiet", url, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", "--force", base], check=True)
    # purely the solver's edits show up in `git diff` against base
    return dest


SOLVE_PROMPT = """You are fixing a real bug in this repository. The repository is checked out in the current working directory at the exact commit where the bug exists.

Issue to resolve:
---
{problem}
---

Make the minimal source-code changes that resolve this issue. Edit files directly in the working directory. Do NOT write new test files or modify the test suite — only change the library/source code so the project's own (hidden) tests will pass. When done, stop."""


def run_codex(workdir: Path, problem: str, model: str = CODEX_MODEL,
              timeout_s: int = 900) -> str:
    """Run codex exec non-interactively in workdir; return its stdout (for logging)."""
    prompt = SOLVE_PROMPT.format(problem=problem)
    cmd = ["codex", "exec", "-C", str(workdir), "-m", model,
           "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", prompt]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    return (p.stdout or "") + ("\n[STDERR]\n" + p.stderr if p.stderr else "")


def capture_patch(workdir: Path, base_ref: str = "HEAD") -> str:
    """Diff against base_ref (a commit SHA), NOT plain `git diff` — robust if the
    agent committed its edits (plain `git diff` would then return empty)."""
    return subprocess.check_output(
        ["git", "-C", str(workdir), "diff", "--no-color", base_ref], text=True)


def solve_instance(instance: dict, model: str = CODEX_MODEL, keep: bool = False) -> dict:
    """Full S-arm solve for one instance. Returns {model_patch, wall_clock_s, log, model}."""
    t0 = time.time()
    workdir = Path(tempfile.mkdtemp(prefix=f"solve_{instance['instance_id']}_"))
    try:
        extract_repo(instance, workdir)
        log = run_codex(workdir, instance["problem_statement"], model=model)
        patch = capture_patch(workdir, instance["base_commit"])
        return {"instance_id": instance["instance_id"], "model_patch": patch,
                "wall_clock_s": time.time() - t0, "log": log[-4000:], "model": model}
    finally:
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    import sys, json
    from harness import load_instances
    iid = sys.argv[1] if len(sys.argv) > 1 else "pylint-dev__pylint-7080"
    inst = load_instances([iid])[iid]
    print(f"[smoke] solving {iid} with {CODEX_MODEL} ...", flush=True)
    r = solve_instance(inst)
    print(f"[smoke] wall={r['wall_clock_s']:.0f}s  patch_lines={len(r['model_patch'].splitlines())}")
    print("[smoke] --- patch head ---")
    print("\n".join(r["model_patch"].splitlines()[:40]))
    Path(f"smoke_{iid}.patch").write_text(r["model_patch"])
    Path(f"smoke_{iid}.log").write_text(r["log"])
