"""
DCM eval arms — shared primitives + the firewalled in-container verifier.

Build order (ARMS_BUILD_SPEC.md): primitives + verify FIRST (every arm needs
them), then BoN, then I, then D. The verifier runs ONLY agent-authored tests
inside the instance image's real env — NEVER the hidden grading suite (that
runs once, later, via the official harness).
"""
from __future__ import annotations
import subprocess, tempfile, time, uuid, json, os
from pathlib import Path

# Every arm writes its full internal provenance here so no run is a black box.
# Set by run wiring; defaults to ./runs/provenance.
PROV_DIR = Path(os.environ.get("DCM_PROV_DIR", "runs/provenance"))


def _record(arm: str, instance_id: str, prov: dict) -> None:
    PROV_DIR.mkdir(parents=True, exist_ok=True)
    (PROV_DIR / f"prov_{arm}_{instance_id}.json").write_text(json.dumps(prov, indent=2))


def instance_image(instance: dict) -> str:
    """Docker Hub name of the prebuilt swebench instance image.

    Observed: instance_id `a__b` -> image `swebench/sweb.eval.x86_64.a_1776_b:latest`
    (Docker repos can't contain `__`; swebench substitutes `_1776_`). Lowercased.
    """
    iid = instance["instance_id"].lower().replace("__", "_1776_")
    return f"swebench/sweb.eval.x86_64.{iid}:latest"


def ensure_image(image: str) -> None:
    have = subprocess.run(["docker", "image", "inspect", image],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if have.returncode != 0:
        subprocess.run(["docker", "pull", image], check=True)


# conda env in swebench images is named `testbed`; repo is at /testbed
_RUN = ("source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && "
        "cd /testbed && {body}")


def verify_in_image(instance: dict, patch_str: str, test_rel_path: str, test_body: str,
                    timeout_s: int = 300) -> tuple[bool, str]:
    """Apply patch (may be empty) + write an agent-authored test into the image's
    /testbed, run ONLY that test in the repo env. Return (passed, output).

    Firewall: only `test_rel_path` is executed; the hidden grading suite is never run.
    """
    image = instance_image(instance)
    ensure_image(image)
    cid = "dcmv_" + uuid.uuid4().hex[:10]
    subprocess.run(["docker", "run", "-d", "--name", cid, image, "tail", "-f", "/dev/null"],
                   check=True, stdout=subprocess.DEVNULL)
    try:
        # write the agent-authored test
        subprocess.run(["docker", "exec", "-i", cid, "bash", "-c",
                        f"mkdir -p $(dirname /testbed/{test_rel_path}) && cat > /testbed/{test_rel_path}"],
                       input=test_body, text=True, check=True)
        # apply the candidate patch (skip if empty/base run)
        if patch_str.strip():
            ap = subprocess.run(["docker", "exec", "-i", cid, "bash", "-c",
                                 "cd /testbed && git apply -v - 2>&1 || patch -p1 --forward 2>&1"],
                                input=patch_str, text=True, capture_output=True)
            if ap.returncode != 0:
                return False, "PATCH_APPLY_FAILED:\n" + ap.stdout + ap.stderr
        body = f"python -m pytest {test_rel_path} -q --no-header -p no:cacheprovider"
        run = subprocess.run(["docker", "exec", cid, "bash", "-lc", _RUN.format(body=body)],
                             capture_output=True, text=True, timeout=timeout_s)
        out = (run.stdout or "")[-3000:] + (("\n[ERR]\n" + run.stderr[-1500:]) if run.stderr else "")
        return run.returncode == 0, out
    finally:
        subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reproduces(instance: dict, patch_str: str, test_rel_path: str, test_body: str) -> dict:
    """A valid repro test FAILS on base and PASSES on the patched tree."""
    base_pass, base_out = verify_in_image(instance, "", test_rel_path, test_body)
    patched_pass, patch_out = verify_in_image(instance, patch_str, test_rel_path, test_body)
    return {"valid_repro": (not base_pass) and patched_pass,
            "fails_on_base": not base_pass, "passes_on_patch": patched_pass,
            "base_out": base_out, "patch_out": patch_out}


# ---------------------------------------------------------------------------
# BoN — cheapest floor: k independent solver samples, select by an agent-authored
# repro test (firewalled). No roles, no peers. The bar D/I must clear cheaply.
# ---------------------------------------------------------------------------
import re
from solver_codex import solve_instance, CODEX_MODEL, extract_repo, run_codex, capture_patch
import shutil, tempfile as _tf

TEST_PROMPT = """A bug is described below for the repository in the current working directory (checked out at the buggy commit). Write ONE pytest regression test, in a NEW file `test_dcm_repro.py` at the repo root, that FAILS on this buggy code and would PASS once the bug is fixed. Do NOT fix the bug. Do NOT edit any other file. Only create test_dcm_repro.py.

Issue:
---
{problem}
---
Write only the test file and stop."""


def author_repro_test(instance: dict, model: str = CODEX_MODEL, timeout_s: int = 600) -> tuple[str, str]:
    """codex writes a failing regression test. Returns (repo_rel_path, body) or ('','').

    Detects whatever test file codex actually created (robust to path choice and to
    codex committing), rather than assuming an exact filename at the repo root.
    """
    wd = Path(_tf.mkdtemp(prefix=f"test_{instance['instance_id']}_"))
    try:
        extract_repo(instance, wd)
        run_codex(wd, TEST_PROMPT.format(problem=instance["problem_statement"]),
                  model=model, timeout_s=timeout_s)
        subprocess.run(["git", "-C", str(wd), "add", "-A"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # --name-status: only ADDED (A) files — a NEW agent-authored test, never a
        # MODIFIED existing repo test (which could overlap the hidden grading suite => leak).
        ns = subprocess.check_output(
            ["git", "-C", str(wd), "diff", "--name-status", instance["base_commit"]], text=True).splitlines()
        added = [line.split("\t", 1)[1] for line in ns if line.startswith("A\t")]
        tests = [p for p in added if p.endswith(".py") and "test" in Path(p).name.lower()]
        if not tests:
            return "", ""
        pick = next((p for p in tests if Path(p).name == "test_dcm_repro.py"), tests[0])
        return pick, (wd / pick).read_text()
    finally:
        shutil.rmtree(wd, ignore_errors=True)


class SArm:
    """S — single agent, full tools, self-check. The baseline."""
    name = "S"
    def __init__(self, model: str = CODEX_MODEL):
        self.model = model
    def __call__(self, instance: dict):
        from harness import ArmResult
        r = solve_instance(instance, model=self.model)
        _record("S", instance["instance_id"],
                {"arm": "S", "instance_id": instance["instance_id"],
                 "model": self.model, "patch_lines": len(r["model_patch"].splitlines())})
        return ArmResult(instance_id=instance["instance_id"], model_patch=r["model_patch"],
                         wall_clock_s=r["wall_clock_s"], notes="S | tokens:codex-exec-not-exposed")


class BoNArm:
    name = "BoN"
    def __init__(self, k_b: int = 5, model: str = CODEX_MODEL):
        self.k_b, self.model = k_b, model

    def __call__(self, instance: dict):
        from harness import ArmResult
        iid = instance["instance_id"]; t0 = time.time()
        prov = {"arm": "BoN", "instance_id": iid, "k_b": self.k_b, "model": self.model}
        # one agent-authored repro test, validated to fail on base (firewalled: never the hidden suite)
        test_path, test_body = author_repro_test(instance, self.model)
        base_pass = verify_in_image(instance, "", test_path, test_body)[0] if test_body else True
        valid = bool(test_body) and not base_pass
        prov["repro_test"] = {"path": test_path, "fails_on_base": (not base_pass),
                              "valid": valid, "body": test_body[:4000]}
        # k independent solver samples (codex is stochastic)
        def difflen(p): return len(p.splitlines())
        cands, cand_prov = [], []
        for j in range(self.k_b):
            patch = solve_instance(instance, model=self.model)["model_patch"]
            repro_pass = bool(patch.strip()) and valid and verify_in_image(instance, patch, test_path, test_body)[0]
            cands.append(patch)
            cand_prov.append({"idx": j, "patch_lines": difflen(patch), "empty": not patch.strip(),
                              "repro_pass": repro_pass})
        prov["candidates"] = cand_prov
        # select: among patches passing the VALID repro test, smallest diff; else smallest non-empty
        chosen, note = "", "no-candidate"
        if valid:
            passing = [c for c in cands if c.strip() and verify_in_image(instance, c, test_path, test_body)[0]]
            if passing:
                chosen, note = min(passing, key=difflen), f"selected-by-repro ({len(passing)}/{self.k_b} pass)"
        if not chosen:
            nonempty = [c for c in cands if c.strip()]
            if nonempty:
                chosen, note = min(nonempty, key=difflen), (note if valid else "repro-invalid") + "; fallback-smallest-diff"
        prov["selection"] = {"reason": note, "chosen_lines": difflen(chosen)}
        _record("BoN", iid, prov)
        return ArmResult(instance_id=iid, model_patch=chosen, wall_clock_s=time.time() - t0,
                         notes=f"BoN k={self.k_b} repro_valid={valid} {note} | tokens:codex-exec-not-exposed")


# ---------------------------------------------------------------------------
# I / D — role council. I and D are byte-identical EXCEPT: in the revision round
# D's roles see all peers' first-round critiques (forced read/revise); I's see
# only their own. That single delta IS the coordination primitive (D-I primary).
# ---------------------------------------------------------------------------
ROLES = {
    "correctness": "You are a CORRECTNESS reviewer. Find where the candidate fix is wrong, incomplete, or misses edge cases of the issue.",
    "security":    "You are a SECURITY ADVERSARY. Find how the candidate fix could introduce a vulnerability, unsafe input handling, or regression.",
    "rootcause":   "You are a 6SIGMA ROOT-CAUSE challenger. Decide whether the candidate fix treats the true upstream cause or just patches a symptom; demand the simpler root-cause shape.",
    "testwriter":  "You are a TEST-WRITER. Identify the behaviour the fix must satisfy and whether the candidate actually achieves it.",
}

CRITIQUE_PROMPT = """{role}

The repository is in the working directory at the buggy commit, with a CANDIDATE fix already applied. Do NOT edit files. Read the issue and the candidate fix, and write a short, specific critique: what is wrong or missing, and concretely what should change.

Issue:
---
{problem}
---
Candidate fix (already applied):
---
{patch}
---
Write only your critique and stop."""

REVISE_PROMPT = """{role}

The repository is in the working directory at the buggy commit with a candidate fix applied. Revise the source to best resolve the issue, incorporating the critique(s) below. Edit files directly; do not touch the test suite.

Issue:
---
{problem}
---
Critique(s) to address:
---
{critiques}
---
Make your edits and stop."""

SYNTH_PROMPT = """You are the SYNTHESIZER. Several reviewers each revised the fix for the issue below. Their combined diffs are shown. Produce the single best final fix in the working directory (repo at the buggy commit), reconciling them into minimal correct source changes. Do not edit tests.

Issue:
---
{problem}
---
Reviewer revisions:
---
{revisions}
---
Make the final edits and stop."""


def _apply_to_worktree(wd: Path, patch: str) -> bool:
    """Apply patch to the working tree (uncommitted) so `git diff` later shows
    base_patch + any further edits as ONE complete diff vs the clean base_commit."""
    if not patch.strip():
        return True
    a = subprocess.run(["git", "-C", str(wd), "apply", "--3way", "-"], input=patch, text=True,
                       capture_output=True)
    if a.returncode == 0:
        return True
    b = subprocess.run(["patch", "-d", str(wd), "-p1", "--forward"], input=patch, text=True,
                       capture_output=True)
    return b.returncode == 0


def _codex_on_patched(instance, base_patch, prompt, read_only=False, timeout_s=900):
    """Clone@base_commit, apply base_patch to the WORKING TREE (uncommitted), run
    codex; return (complete_diff_vs_clean_base, stdout). The diff includes base_patch
    + codex's edits, which is exactly what the harness needs (it applies to clean base)."""
    wd = Path(_tf.mkdtemp(prefix=f"role_{instance['instance_id']}_"))
    try:
        extract_repo(instance, wd)
        _apply_to_worktree(wd, base_patch)
        sandbox = ["-s", "read-only"] if read_only else ["--dangerously-bypass-approvals-and-sandbox"]
        cmd = ["codex", "exec", "-C", str(wd), "-m", CODEX_MODEL, *sandbox, "--skip-git-repo-check", prompt]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        # diff vs base_commit (robust to codex committing its own edits)
        diff = subprocess.check_output(
            ["git", "-C", str(wd), "diff", "--no-color", instance["base_commit"]], text=True)
        return diff, (p.stdout or "")
    finally:
        shutil.rmtree(wd, ignore_errors=True)


class CouncilArm:
    """I (peer_visible=False) or D (peer_visible=True)."""
    def __init__(self, peer_visible: bool):
        self.peer_visible = peer_visible
        self.name = "D" if peer_visible else "I"

    def __call__(self, instance: dict):
        from harness import ArmResult
        iid = instance["instance_id"]; t0 = time.time()
        prov = {"arm": self.name, "instance_id": iid, "peer_visible": self.peer_visible,
                "roles": list(ROLES)}
        base = solve_instance(instance, model=CODEX_MODEL)["model_patch"]
        prov["base_patch_lines"] = len(base.splitlines())
        # Round 1: blind critiques (IDENTICAL for I and D)
        crit = {}
        for role, desc in ROLES.items():
            _, c = _codex_on_patched(instance, base,
                CRITIQUE_PROMPT.format(role=desc, problem=instance["problem_statement"], patch=base),
                read_only=True)
            crit[role] = c.strip()[-1500:]
        prov["round1_critiques"] = {r: crit[r] for r in ROLES}
        # Round 2: revise. The ONLY I/D delta: I sees own critique; D sees ALL peers' critiques.
        revisions = {}
        for role, desc in ROLES.items():
            seen = crit[role] if not self.peer_visible else \
                "\n\n".join(f"[{r}]\n{crit[r]}" for r in ROLES)
            rev, _ = _codex_on_patched(instance, base,
                REVISE_PROMPT.format(role=desc, problem=instance["problem_statement"], critiques=seen))
            revisions[role] = rev
        prov["round2_revisions"] = {r: {"lines": len(revisions[r].splitlines()),
                                        "saw_peers": self.peer_visible} for r in ROLES}
        # Synthesizer (byte-identical for I and D) reconciles the revised diffs
        revblock = "\n\n".join(f"=== {r} ===\n{revisions[r]}" for r in ROLES if revisions[r].strip())
        final, _ = _codex_on_patched(instance, base,
            SYNTH_PROMPT.format(problem=instance["problem_statement"], revisions=revblock or "(no revisions)"))
        final = final if final.strip() else base  # fall back to base if synth produced nothing
        prov["final_patch_lines"] = len(final.splitlines())
        prov["synth_fell_back_to_base"] = (not final.strip()) or (final == base)
        _record(self.name, iid, prov)
        return ArmResult(instance_id=iid, model_patch=final, wall_clock_s=time.time() - t0,
                         notes=f"{self.name} peer_visible={self.peer_visible} roles={len(ROLES)} | tokens:codex-exec-not-exposed")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "verify-smoke":
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
        inst = {r["instance_id"]: r for r in ds}["pylint-dev__pylint-7080"]
        ok, out = verify_in_image(inst, inst["patch"], "test_dcm_smoke.py",
                                  "def test_dcm_verify_mechanic():\n    assert True\n")
        print(f"[verify-smoke] passed={ok}\n{out[-200:]}")
    elif len(sys.argv) > 2 and sys.argv[1] == "council":
        # python3 arms.py council I|D <iid>  -> run one council arm + grade, provenance recorded
        from harness import run_arm
        which, iid = sys.argv[2], sys.argv[3]
        arm = CouncilArm(peer_visible=(which == "D"))
        led = run_arm(arm, [iid], Path(f"runs/{which}_smoke"), max_workers=1)
        print(f"[{which}-smoke] {iid} resolved={iid in led.resolved_ids}")
    else:
        # BoN smoke on one instance -> patch -> grade
        from harness import run_arm
        iid = sys.argv[1] if len(sys.argv) > 1 else "pylint-dev__pylint-7080"
        led = run_arm(BoNArm(k_b=3), [iid], Path("runs/bon_smoke"), max_workers=1)
        print(f"[BoN-smoke] {iid} resolved={iid in led.resolved_ids} rate={led.resolved_rate:.0%}")
