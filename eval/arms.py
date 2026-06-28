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
from solver import _cmd, CLI_MODEL
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
# D's roles see all peers' first-round concerns (forced read/revise); I's see
# only their own. That single delta IS the coordination primitive (D-I primary).
# ---------------------------------------------------------------------------
PRODUCER = {"seat": "Producer", "cli": "codex", "model": CODEX_MODEL}

ROLES = {
    "foundation": {
        "seat": "Foundation",
        "cli": "gemini",
        "lens": "You are the FOUNDATION reviewer. Use memory-shaped and Git-shaped grounding: look for ignored prior solutions, duplicated existing utilities, and regression reintroduction from history.",
    },
    "ground-runner": {
        "seat": "Execution/Ground-runner",
        "cli": "claude",
        "lens": "You are the EXECUTION/GROUND-RUNNER reviewer. Run relevant visible code or tests in this throwaway checkout when useful. Own hallucinated APIs, test tamper, deleted or neutered tests, and no-op fixes.",
    },
    "evasive-repair": {
        "seat": "Evasive-repair",
        "cli": "grok",
        "lens": "You are the EVASIVE-REPAIR reviewer. Own silent fallbacks, swallowed errors, stub guards, and symptom-not-cause patches. Demand the simpler 6SIGMA root-cause shape.",
    },
    "scope-blast": {
        "seat": "Scope + Safety-veto",
        "cli": "gemini",
        "lens": "You are the SCOPE+BLAST reviewer with unilateral safety-veto authority. Own scope violations, destructive operations, secrets, injection/exfiltration, and unsafe broad blast radius.",
    },
}

CLERK = {
    "seat": "Provenance Clerk",
    "kind": "deterministic state-machine",
    "voting": False,
    "owns": "concern ledger, parse status, peer-visibility provenance, dissent preservation",
}

# A second producer (different base from PRODUCER=codex) seated only on high-blast-radius councils
# for generation decorrelation. ROUND2_SYNTHESIS §4 "Scale to 12 … add Producer-2 (different base)".
PRODUCER_2 = {"seat": "Producer-2", "cli": "claude", "model": None}

# Expansion reviewer roles for high-blast-radius councils. Standard FUSES Memory+Git into Foundation
# and keeps one Scope+Safety-veto; expansion SPLITS them (§4 "split Foundation→Memory+Git, split
# Scope→Scope-Sentinel + Blast-Shield") and adds depth roles (Test-Integrity, Dependency/API-reality,
# Red-Team-injection). Staffed OFF the producer base (codex), spread evenly across gemini/claude/grok
# (3 each — tighter ≤2-per-base is unreachable with 9 reviewers on 3 non-producer bases; a real
# decorrelation limit of a 4-CLI fleet, noted not hidden). Both the eval C-ablation and the
# operational scaling engine compose tiers from ROLES + EXPAND_ROLES.
EXPAND_ROLES = {
    "memory-scout": {"seat": "Memory Scout", "cli": "gemini",
        "lens": "You are the MEMORY-SCOUT reviewer (Foundation split). Own ignored-prior-solution: "
                "search memory/ISMA and existing utilities for an answer the patch reinvents."},
    "git-historian": {"seat": "Git Historian", "cli": "claude",
        "lens": "You are the GIT-HISTORIAN reviewer (Foundation split). Own regression-reintroduction: "
                "check git history for a deliberate prior fix this change silently undoes."},
    "scope-sentinel": {"seat": "Scope Sentinel", "cli": "gemini",
        "lens": "You are the SCOPE-SENTINEL reviewer (Scope split). Own scope-semantics: does the diff "
                "stay within the declared change boundary; flag scope creep into unrelated surfaces."},
    "blast-shield": {"seat": "Blast Shield", "cli": "grok",
        "lens": "You are the BLAST-SHIELD reviewer (Scope split) with unilateral safety-veto. Own "
                "destructive operations, secrets, injection/exfil, and broad blast radius on shared consumers."},
    "test-integrity": {"seat": "Test Integrity", "cli": "claude",
        "lens": "You are the TEST-INTEGRITY reviewer. Own deleted/neutered/tampered tests and no-op "
                "assertions; a green suite must mean the bug is actually fixed, not silenced."},
    "dependency-api-reality": {"seat": "Dependency/API Reality", "cli": "gemini",
        "lens": "You are the DEPENDENCY/API-REALITY reviewer. Own hallucinated APIs, wrong signatures, "
                "and version/dependency mismatches against the real installed surface."},
    "red-team-injection": {"seat": "Red-Team Injection", "cli": "grok",
        "lens": "You are the RED-TEAM/INJECTION reviewer. Own prompt-injection, exfiltration, and "
                "untrusted-input → action paths this change opens."},
}

_ALL_REVIEW_ROLES = {**ROLES, **EXPAND_ROLES}
if ROLES["ground-runner"]["cli"] == PRODUCER["cli"]:
    raise RuntimeError("ground-runner MUST use a different base model than the producer")
if any(role["cli"] == PRODUCER["cli"] for role in _ALL_REVIEW_ROLES.values()):
    raise RuntimeError("semantic reviewer roles must be staffed off the producer base model")

FOUNDATION_PREFLIGHT_PROMPT = """{role}

The repository is in the working directory at the buggy commit. Do NOT edit files. Read the issue and ground the producer before it writes the first patch. Use Git history or existing code search where useful.

Issue:
---
{problem}
---
Output exactly two lines:
GROUNDING: <specific prior code/history/memory-shaped facts the producer must honor, or NONE>
CONCERN: <one ignored-prior-solution or regression risk to avoid, or NONE>"""

PRODUCER_PROMPT = """You are the PRODUCER. The repository is checked out in the current working directory at the exact buggy commit.

Issue:
---
{problem}
---
Foundation pre-flight grounding:
---
{grounding}
---
Make the minimal source-code changes that resolve the issue. Honor the grounding unless the code proves it obsolete. Do NOT write new test files or modify the test suite. Stop when the source fix is complete."""

CRITIQUE_PROMPT = """{role}

The repository is in the working directory at the buggy commit, with a CANDIDATE fix already applied. Do NOT edit files. Review only through your assigned seat. Produce a concern record, not general prose.

Issue:
---
{problem}
---
Candidate fix (already applied):
---
{patch}
---
Output exactly one line:
CONCERN: <NONE if this seat finds no issue; otherwise one specific, addressable concern with Observed/Inferred/Unknown evidence>"""

REVISE_PROMPT = """{role}

The repository is in the working directory at the buggy commit with a candidate fix applied. Revise the source to best resolve the issue, incorporating the concern(s) below through your assigned seat. Edit files directly; do not touch the test suite.

Issue:
---
{problem}
---
Concern(s) to address:
---
{concerns}
---
Make your edits and stop."""

FINAL_PROMPT = """You are the PRODUCER. Several semantic reviewers revised your candidate fix for the issue below. Their combined diffs are shown. Produce the single best final fix in the working directory (repo at the buggy commit), reconciling them into minimal correct source changes. Do not edit tests.

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


def _role_model(role: dict) -> str:
    return CLI_MODEL.get(role["cli"], "unknown")


def _extract_concern(output: str) -> dict:
    """Parse the reviewer concern line; unparsed output is recorded as Observed failure."""
    for line in output.splitlines():
        if line.strip().upper().startswith("CONCERN:"):
            concern = line.split(":", 1)[1].strip()
            return {"parsed": bool(concern), "concern": concern or "UNPARSED: empty CONCERN line",
                    "raw": output[-1500:]}
    return {"parsed": False,
            "concern": "UNPARSED: reviewer output did not contain a CONCERN line",
            "raw": output[-1500:]}


def _concern_block(concerns: dict) -> str:
    return "\n\n".join(f"[{role}]\nCONCERN: {data['concern']}" for role, data in concerns.items())


def _cli_on_patched(instance, base_patch, prompt, cli: str, read_only=False, timeout_s=900):
    """Clone@base_commit, apply base_patch, run one staffed CLI, and return
    (complete_diff_vs_clean_base, output). Observed CLI/apply failures raise."""
    wd = Path(_tf.mkdtemp(prefix=f"role_{cli}_{instance['instance_id']}_"))
    env = os.environ.copy()
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    try:
        extract_repo(instance, wd)
        if not _apply_to_worktree(wd, base_patch):
            raise RuntimeError(f"PATCH_APPLY_FAILED for {instance['instance_id']}")
        if cli == "codex":
            sandbox = ["-s", "read-only"] if read_only else ["--dangerously-bypass-approvals-and-sandbox"]
            cmd = ["codex", "exec", "-C", str(wd), "-m", CODEX_MODEL,
                   *sandbox, "--skip-git-repo-check", prompt]
        else:
            cmd = _cmd(cli, wd, prompt)
        p = subprocess.run(cmd, cwd=str(wd), env=env, capture_output=True, text=True, timeout=timeout_s)
        out = (p.stdout or "") + (("\n[STDERR]\n" + p.stderr) if p.stderr else "")
        if p.returncode != 0:
            raise RuntimeError(f"{cli} exited {p.returncode} on {instance['instance_id']}:\n{out[-1500:]}")
        # diff vs base_commit (robust to codex committing its own edits)
        diff = subprocess.check_output(
            ["git", "-C", str(wd), "diff", "--no-color", instance["base_commit"]], text=True)
        return diff, out
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
                "roles": list(ROLES),
                "roster": {"producer": PRODUCER,
                           "reviewers": {r: {**spec, "model": _role_model(spec)}
                                         for r, spec in ROLES.items()},
                           "clerk": CLERK}}
        foundation = ROLES["foundation"]
        _, grounding = _cli_on_patched(
            instance, "",
            FOUNDATION_PREFLIGHT_PROMPT.format(role=foundation["lens"],
                                               problem=instance["problem_statement"]),
            foundation["cli"], read_only=True)
        prov["foundation_preflight"] = {"cli": foundation["cli"],
                                        "model": _role_model(foundation),
                                        "output": grounding[-2000:],
                                        "concern": _extract_concern(grounding)}
        base, _ = _cli_on_patched(
            instance, "",
            PRODUCER_PROMPT.format(problem=instance["problem_statement"],
                                   grounding=grounding[-2000:]),
            PRODUCER["cli"])
        prov["base_patch_lines"] = len(base.splitlines())
        # Round 1: blind concerns (IDENTICAL for I and D)
        concerns = {}
        for role, spec in ROLES.items():
            _, c = _cli_on_patched(
                instance, base,
                CRITIQUE_PROMPT.format(role=spec["lens"],
                                       problem=instance["problem_statement"],
                                       patch=base),
                spec["cli"], read_only=True)
            concerns[role] = _extract_concern(c)
        prov["round1_concerns"] = {r: concerns[r] for r in ROLES}
        prov["clerk_ledger"] = {"seat": CLERK["seat"], "voting": False,
                                "open_concerns": {r: concerns[r]["concern"] for r in ROLES},
                                "unparsed": [r for r in ROLES if not concerns[r]["parsed"]]}
        # Round 2: revise. The ONLY I/D delta: I sees own concern; D sees ALL peers' concerns.
        revisions = {}
        for role, spec in ROLES.items():
            seen = f"[{role}]\nCONCERN: {concerns[role]['concern']}" if not self.peer_visible else \
                _concern_block(concerns)
            rev, _ = _cli_on_patched(
                instance, base,
                REVISE_PROMPT.format(role=spec["lens"],
                                     problem=instance["problem_statement"],
                                     concerns=seen),
                spec["cli"])
            revisions[role] = rev
        prov["round2_revisions"] = {r: {"lines": len(revisions[r].splitlines()),
                                        "saw_peers": self.peer_visible,
                                        "cli": ROLES[r]["cli"],
                                        "model": _role_model(ROLES[r])} for r in ROLES}
        # Producer finalization is byte-identical for I and D; the clerk does not vote.
        revblock = "\n\n".join(f"=== {r} ===\n{revisions[r]}" for r in ROLES if revisions[r].strip())
        final, _ = _cli_on_patched(
            instance, base,
            FINAL_PROMPT.format(problem=instance["problem_statement"],
                                revisions=revblock or "(no revisions)"),
            PRODUCER["cli"])
        prov["final_patch_lines"] = len(final.splitlines())
        prov["final_empty"] = not final.strip()
        prov["final_same_as_base"] = final == base
        _record(self.name, iid, prov)
        return ArmResult(instance_id=iid, model_patch=final, wall_clock_s=time.time() - t0,
                         notes=f"{self.name} peer_visible={self.peer_visible} reviewers={len(ROLES)} clerk=non-voting | tokens:cli-not-exposed")


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
