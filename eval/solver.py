"""
Multi-CLI solver — drives any of the four fleet model CLIs as an "instance -> patch"
solver. All four verified (2026-06-26) to edit a repo headlessly:
  codex  : codex exec -C <wd> -m gpt-5.5 --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check
  claude : claude -p <prompt> --dangerously-skip-permissions            (cwd=wd)
  gemini : gemini -p <prompt> --yolo --skip-trust  (GEMINI_CLI_TRUST_WORKSPACE=true, cwd=wd)
  grok   : grok -p <prompt> --always-approve --permission-mode bypassPermissions (cwd=wd)

Source state = clone @ base_commit; patch = git diff vs base_commit (robust to the
agent committing). Grading is always the official hidden harness — never the model.
Exact model version per CLI is recorded in the result for the run log / pre-registration.
"""
from __future__ import annotations
import os, shutil, subprocess, tempfile, time
from pathlib import Path
from solver_codex import extract_repo, capture_patch, SOLVE_PROMPT

# the model each CLI runs (pinned where the CLI exposes it; else the CLI's authed default)
CLI_MODEL = {"codex": "gpt-5.5", "claude": "cli-default", "gemini": "cli-default", "grok": "cli-default"}


def _cmd(cli: str, wd: Path, prompt: str) -> list[str]:
    if cli == "codex":
        return ["codex", "exec", "-C", str(wd), "-m", "gpt-5.5",
                "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", prompt]
    if cli == "claude":
        return ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    if cli == "gemini":
        return ["gemini", "-p", prompt, "--yolo", "--skip-trust"]
    if cli == "grok":
        return ["grok", "-p", prompt, "--always-approve", "--permission-mode", "bypassPermissions"]
    raise ValueError(f"unknown cli: {cli}")


# markers that mean "infra/provider failure", NOT "model failed to solve" -> must retry, never count as a real empty
_ERR_MARKERS = ("rate limit", "rate_limit", "429", "quota", "usage limit", "overloaded",
                "503", "502", "service unavailable", "econnreset", "etimedout", "unauthorized",
                "authentication", "please run /login", "network error", "stream error")


def _is_provider_error(patch: str, log: str) -> bool:
    return (not patch.strip()) and any(m in log.lower() for m in _ERR_MARKERS)


def solve(instance: dict, cli: str, timeout_s: int = 1200, retries: int = 3) -> dict:
    """Run one model CLI as a solver on one instance. Returns {instance_id, cli, model_patch, wall_clock_s, log, errored}.

    An EMPTY patch caused by a provider error (rate-limit/auth/outage) is retried with
    backoff and flagged `errored` if it never clears — so callers never miscount a
    provider failure as the model failing to solve (which would corrupt the oracle union).
    A genuine empty patch (model ran fine, produced no edit) returns errored=False.
    """
    t0 = time.time()
    prompt = SOLVE_PROMPT.format(problem=instance["problem_statement"])
    env = os.environ.copy(); env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    last_log = ""
    for attempt in range(retries):
        wd = Path(tempfile.mkdtemp(prefix=f"solve_{cli}_{instance['instance_id']}_"))
        try:
            extract_repo(instance, wd)
            p = subprocess.run(_cmd(cli, wd, prompt), cwd=str(wd), env=env,
                               capture_output=True, text=True, timeout=timeout_s)
            patch = capture_patch(wd, instance["base_commit"])
            last_log = ((p.stdout or "") + (p.stderr or ""))[-3000:]
            if not _is_provider_error(patch, last_log):
                return {"instance_id": instance["instance_id"], "cli": cli, "model_patch": patch,
                        "wall_clock_s": time.time() - t0, "log": last_log, "errored": False}
        except subprocess.TimeoutExpired:
            last_log = "TIMEOUT"
        except Exception as e:
            # repo/clone/checkout/infra failure — never let one instance crash the run (ITT)
            last_log = f"EXEC-ERROR: {type(e).__name__}: {e}"
        finally:
            shutil.rmtree(wd, ignore_errors=True)
        time.sleep(min(30, 5 * (attempt + 1)))  # backoff (in a worker thread; not the foreground shell)
    return {"instance_id": instance["instance_id"], "cli": cli, "model_patch": "",
            "wall_clock_s": time.time() - t0, "log": last_log, "errored": True}


class SingleModelArm:
    """S_<cli> — one model, solo, full tools. Used as an arm in the oracle-union pre-test."""
    def __init__(self, cli: str):
        self.cli = cli
        self.name = f"S_{cli}"
    def __call__(self, instance: dict):
        from harness import ArmResult
        r = solve(instance, self.cli)
        return ArmResult(instance_id=instance["instance_id"], model_patch=r["model_patch"],
                         wall_clock_s=r["wall_clock_s"], notes=f"S_{self.cli}")


if __name__ == "__main__":
    # quick 1-instance sanity: does each CLI produce a non-empty patch on a real instance?
    import sys
    from harness import load_instances
    iid = sys.argv[1] if len(sys.argv) > 1 else "pylint-dev__pylint-7080"
    clis = sys.argv[2].split(",") if len(sys.argv) > 2 else ["codex", "claude", "gemini", "grok"]
    inst = load_instances([iid])[iid]
    for cli in clis:
        r = solve(inst, cli)
        n = len(r["model_patch"].splitlines())
        print(f"{cli}: patch_lines={n} wall={r['wall_clock_s']:.0f}s {'OK' if n else 'EMPTY: '+r['log'][-150:]}")
