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
# 6. the AI operating guide must be tracked in git (not gitignored)
gi = rd(".gitignore") or ""
if re.search(r"(?m)^CLAUDE\.md$", gi): fail.append("CLAUDE.md is gitignored — the AI operating guide MUST be in the public repo")
if fail:
    print("DOCS-COHERENCE: FAIL — docs drifted from code:")
    for f in fail: print(f"  - {f}")
    print("FIX: update CLAUDE.md (or the code) so they match, then re-run docs_coherence_check.py")
    sys.exit(1)
print("DOCS-COHERENCE: PASS — CLAUDE.md matches the code (files, commands, env, setup, tracked).")
