"""docs_coherence_check — fail if CLAUDE.md drifts from the code (keeps the AI-native docs current).

AI-native: docs are current AT ALL TIMES, enforced by a gate, not willpower. Run on change / pre-merge.
Checks the operating guide's load-bearing claims against reality; exits non-zero + names each drift.
"""
import os, re, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
def rd(p):
    fp = os.path.join(ROOT, p)
    return open(fp).read() if os.path.exists(fp) else None
claude = rd("CLAUDE.md") or ""
fail = []
# 1. every file named in the code-map table must exist
for f in re.findall(r"`([a-z_]+\.py)`", claude):
    if not os.path.exists(os.path.join(ROOT, f)): fail.append(f"code-map names `{f}` but it does not exist")
# 2. the one-command setup must exist + be referenced
if "./setup.sh" in claude and not os.path.exists(os.path.join(ROOT, "setup.sh")): fail.append("CLAUDE.md says ./setup.sh but setup.sh missing")
# 3. documented mesh_cli subcommands must be real argparse subparsers
mc = rd("mesh_cli.py") or ""
real_sub = set(re.findall(r'add_parser\("(\w+)"', mc))
for cmd in re.findall(r"mesh_cli\.py (\w+)", claude):
    if real_sub and cmd not in real_sub: fail.append(f"CLAUDE.md uses `mesh_cli.py {cmd}` but it's not a real subcommand {sorted(real_sub)}")
# 4. documented platform_dcm subcommands real
pd = rd("platform_dcm.py") or ""
pd_sub = set(re.findall(r'add_parser\("(\w+)"', pd))
for cmd in re.findall(r"platform_dcm\.py (\w+)", claude):
    if pd_sub and cmd not in pd_sub: fail.append(f"CLAUDE.md uses `platform_dcm.py {cmd}` but it's not a real subcommand {sorted(pd_sub)}")
# 5. documented env vars must appear in mesh.py
mesh = rd("mesh.py") or ""
for env in set(re.findall(r"`(DCM_NEO4J_[A-Z]+)`", claude)):
    if env not in mesh: fail.append(f"CLAUDE.md documents env `{env}` but mesh.py never reads it")
# 6. the AI operating guides must be tracked in git (not gitignored)
gi = rd(".gitignore") or ""
if re.search(r"(?m)^CLAUDE\.md$", gi): fail.append("CLAUDE.md is gitignored — the AI operating guide MUST be in the public repo")
if re.search(r"(?m)^AGENTS\.md$", gi): fail.append("AGENTS.md is gitignored — the codex/gemini operating guide MUST be in the public repo")
# 7. AGENTS.md (codex/gemini entry point) must exist + point at CLAUDE.md, never revert to orphaned tooling boilerplate
agents = rd("AGENTS.md")
if agents is None: fail.append("AGENTS.md missing — codex/gemini read it as their entry point")
elif "CLAUDE.md" not in agents: fail.append("AGENTS.md does not reference CLAUDE.md — it must point CLI agents at the canonical operating guide")
elif "gitnexus:start" in agents: fail.append("AGENTS.md is auto-generated tooling boilerplate, not the DCM operating doc")
import subprocess
tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "--error-unmatch", "AGENTS.md"],
                         capture_output=True).returncode == 0
if agents is not None and not tracked: fail.append("AGENTS.md is untracked — every doc is a committed public artifact (nothing local)")
# 8. SKILL.md (the run-a-council skill) must exist, be tracked, and use real council_cli subcommands
cc_sub = set(re.findall(r'add_parser\("(\w+)"', rd("council_cli.py") or ""))
skill = rd("SKILL.md")
if skill is None: fail.append("SKILL.md missing — the run-a-council skill must exist (callers must not improvise)")
else:
    for cmd in re.findall(r"council_cli\.py (\w+)", skill):
        if cc_sub and cmd not in cc_sub: fail.append(f"SKILL.md uses `council_cli.py {cmd}` but it's not a real subcommand {sorted(cc_sub)}")
if re.search(r"(?m)^SKILL\.md$", gi): fail.append("SKILL.md is gitignored — the skill MUST be in the public repo")
if skill is not None and subprocess.run(["git", "-C", ROOT, "ls-files", "--error-unmatch", "SKILL.md"],
                                        capture_output=True).returncode != 0:
    fail.append("SKILL.md is untracked — every doc is a committed public artifact")
# 9. README.md (the public front door) must exist, name real .py files, point at the invocation,
#    and NOT carry the reverted sandbox claim (we run full-access; "sandbox before seating" was wrong)
readme = rd("README.md")
if readme is None:
    fail.append("README.md missing")
else:
    for f in re.findall(r"`([a-z_]+\.py)`", readme):
        if not os.path.exists(os.path.join(ROOT, f)): fail.append(f"README names `{f}` but it does not exist")
    if re.search(r"sandbox before seating|sandbox the CLI|sandbox before", readme, re.I):
        fail.append("README still implies sandboxing — we run full-access, no sandbox (reverted); state trusted-content-only")
    if "council_cli.py" not in readme: fail.append("README does not point at council_cli.py (the invocation) — a reader can't run it")
# 10. THE FLOOR INVARIANT (mechanical — a tiny council can NEVER come back): the canonical roster and
#     every scaling tier must seat >= 8 reviewers. A 3- or 4-seat "council" is the rejected stub.
try:
    import importlib, council as _c, scaling as _s
    importlib.reload(_c); importlib.reload(_s)
    n = len(_c.canonical_reviewer_roster())
    if n < 8: fail.append(f"canonical roster seats only {n} reviewers — floor is 8 (no tiny council; 8–12 by blast radius)")
    for t in _s._TIERS:
        m = len(_s.reviewer_roster_for_tier(t))
        if m < 8: fail.append(f"scaling tier {t!r} seats only {m} reviewers — floor is 8 (no compress/tiny tier)")
except Exception as e:
    fail.append(f"could not verify the roster floor invariant: {type(e).__name__}: {e}")
if fail:
    print("DOCS-COHERENCE: FAIL — docs drifted from code:")
    for f in fail: print(f"  - {f}")
    print("FIX: update CLAUDE.md (or the code) so they match, then re-run docs_coherence_check.py")
    sys.exit(1)
print("DOCS-COHERENCE: PASS — CLAUDE.md matches the code (files, commands, env, setup, tracked).")
