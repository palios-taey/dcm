# SKILL: run a DCM council

For an AI operator (or fleet peer like treasurer) that needs a multi-expert deliberation and must
NOT improvise the invocation. This is the canonical, copy-paste way to run a council. Full repo
guide: [`CLAUDE.md`](./CLAUDE.md) / [`AGENTS.md`](./AGENTS.md).

## When to use
- **plan** — you have a problem and want a *consensus implementation plan* (ordered steps, caps,
  decision rules) that several differentiated experts converged on.
- **review** — you have an *artifact* (a diff, a draft) and want a verdict that fails closed on any
  unresolved blocking concern, with the prior-art + safety gates enforced.

## Setup (once)
```bash
./setup.sh        # checks deps (neo4j) + a reachable DCM_NEO4J_URI, prints the next step
```

## Run it (the only two commands you need)
```bash
# CONSENSUS PLAN from a problem:
python council_cli.py plan   --problem-file problem.txt --rules-file rules.txt

# REVIEW an artifact (verdict fail-closed on open block-concerns):
python council_cli.py review --task "<what>" --artifact-file change.diff --rules-file rules.txt [--tier expand]
```
- Inputs are FILES — write your problem / rules / artifact to files (operator content is a runtime
  arg, never committed to this public repo).
- Output: the council's final consensus/verdict + the honest coordination record. Exit code is 0
  only when it **published** (review: no open block concern; plan: consensus reached); non-zero
  otherwise so your pipeline halts.

## Pick a tier (review only) — the council seats N by blast radius, never a fixed count
| tier | reviewers | use when |
|---|---|---|
| `compress` | 3 | trivial / doc / low-surface change |
| `standard` | 4 | default |
| `expand` | 9 | high-blast-radius: secrets, migration, gate-change, cross-repo, release, prior-failure, or gitnexus_impact HIGH/CRITICAL |

## What the council does internally (so you trust the output)
1. **Roster** — differentiated reviewers off the producer base (decorrelation), each a real CLI:
   Foundation (gemini/claude) · Ground-runner (≠producer) · Evasive-repair (grok) · Scope+Safety-veto
   (grok/gemini) + a non-voting Provenance Clerk. `expand` splits Foundation→Memory+Git and
   Scope→Sentinel+Blast-Shield and adds Test-Integrity / Dependency-API / Red-Team.
2. **Foundation pre-flight** — the grounding seat runs first and emits a manifest (prior solutions,
   reusable utilities, regression risks) that grounds every reviewer.
3. **Citation gate** (floor) — a named prior-art the artifact neither cites nor supersedes BLOCKS.
4. **Destructive-ops gate** (floor) — a destructive op in the change routes to the safety seat to
   adjudicate (ACCEPTED-RISK if scoped, else blocks).
5. **Blind round → reveal/resolution → publish** — concerns close only on evidence; safety-veto is
   unilateral; you cannot out-vote a failing check.

## Non-negotiables (read before relying on a run)
- **Trusted content only.** Councils run CLIs FULL-ACCESS — there is no sandbox. Do NOT feed a
  council attacker-influenceable / untrusted content; that is an accepted, unmitigated risk.
- **Production is the oracle.** A council "passed" when its review caught a real defect and the fix
  re-verified on a real run — measured, not asserted. No synthetic tests.
- **Report Observed / Inferred / Unknown.** "The council ran" = Observed; "DCM beats one agent" =
  Unknown until the §7 ablation says so.
