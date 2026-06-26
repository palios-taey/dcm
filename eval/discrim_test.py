"""Reviewer-discrimination check: does the B-baseline reviewer BLOCK a known-bad patch
and APPROVE the gold patch? Discriminates iff block-bad AND approve-good. A reviewer that
approves the no-op is a rubber-stamp (theater) -> root-cause before trusting the cell."""
import subprocess, tempfile, sys
from pathlib import Path
from datasets import load_dataset
from solver_codex import extract_repo
from review_cell import review

iid = "pylint-dev__pylint-7080"
inst = {r["instance_id"]: r for r in load_dataset("princeton-nlp/SWE-bench_Verified", split="test")}[iid]

# build a VALID-applying no-op "fix" (a comment) = clearly does NOT fix the issue
wd = Path(tempfile.mkdtemp(prefix="bad_"))
extract_repo(inst, wd)
f = wd / "pylint/lint/expand_modules.py"
f.write_text("# attempted fix for ignore-paths (no-op placeholder)\n" + f.read_text())
bad_patch = subprocess.check_output(["git", "-C", str(wd), "diff", "--no-color", inst["base_commit"]], text=True)
print("bad_patch_lines:", len(bad_patch.splitlines()), flush=True)

print("=== REVIEW no-op bad patch (expect BLOCK) ===", flush=True)
v_bad = review(inst, bad_patch, "claude")
print(f"BAD verdict={v_bad['verdict']} parsed={v_bad['parsed']} concern={v_bad['concern'][:200]!r}", flush=True)

print("=== REVIEW gold patch (expect APPROVE) ===", flush=True)
v_good = review(inst, inst["patch"], "claude")
print(f"GOLD verdict={v_good['verdict']} parsed={v_good['parsed']} concern={v_good['concern'][:120]!r}", flush=True)

discriminates = v_bad["verdict"] == "block" and v_good["verdict"] == "approve"
print("=== DISCRIMINATION:", "PASS (blocks bad, approves good)" if discriminates
      else f"FAIL — bad={v_bad['verdict']} good={v_good['verdict']} (reviewer does NOT discriminate)", flush=True)
