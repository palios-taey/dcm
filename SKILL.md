# SKILL: run a DCM council

You have a problem (want a plan) or an artifact (want a review). You want several differentiated AI
experts to deliberate on it through the mesh and hand you ONE answer. This is how. No improvisation.

## You do NOT orchestrate anything
You run **one command**. It runs all the experts for you — they are real CLIs (codex, claude,
gemini, grok), seated automatically and run through the mesh. You do **not** spawn task agents, you
do **not** call the CLIs yourself, you do **not** pick who's on the panel. One command, one answer.

## The two commands
```bash
./setup.sh                                                              # once: checks deps + Neo4j

# A) You have a PROBLEM → get a consensus PLAN:
python council_cli.py plan --problem-file problem.txt --rules-file rules.txt

# B) You have an ARTIFACT (a diff/draft) → get a REVIEW verdict (fails closed on a blocking concern):
python council_cli.py review --task "what to check" --artifact-file change.diff --rules-file rules.txt
```
Write your inputs to text files first (so big inputs and operator content stay runtime args, never
committed). You get back: `=== STATUS: published ===` + the final plan/verdict + an honest
coordination record. Exit code 0 = published; non-zero = it did not (e.g. a review blocked).

## Who deliberates (every run, automatically)
| seat | role | CLI |
|---|---|---|
| producer | writes the plan/patch (never approves its own) | **codex** |
| reviewer | Foundation — prior solutions, regression risk (pre-flight grounding) | **gemini** |
| reviewer | Ground-runner — runs code/tests, no-op/hallucination | **claude** |
| reviewer | **Evasive-repair** — silent fallbacks, symptom-not-cause, 6SIGMA root-cause | **grok** |
| reviewer | Scope + Safety-veto — scope, destructive ops, secrets, blast radius | **gemini** |
| clerk | Provenance Clerk (non-voting) — ledger, evidence-closure | deterministic |

**All four reviewer CLIs run every time — codex, claude, gemini, AND grok.** `review` can scale the
roster by blast radius with `--tier compress|standard|expand` (3 / 4 / 9 reviewers); `expand` adds
Memory-Scout, Git-Historian, Blast-Shield, Test-Integrity, Dependency/API, Red-Team. Default is the
4-reviewer standard panel above.

## What the council does to your input (so you trust the answer)
Foundation pre-flight grounding → cite-or-block citation gate → destructive-ops floor scan → blind
round (each expert alone) → reveal + evidence-gated resolution → publish. A concern closes only on
evidence; the safety veto is unilateral; you cannot out-vote a failing check.

## Put the real ground truth IN your rules file — the council can't see your environment
The council grounds ONLY on your problem + rules files (plus the experts' training). It does **not**
have access to your databases, schemas, connection strings, or architecture. If your rules omit the
real ones, the experts will assume plausible defaults and produce a plan that **fails on your actual
infra** (observed: a plan wrote to the wrong DB because the rules never named the right one). So your
rules file must spell out the exact connection strings, schema/labels, the wrapper commands to use,
and any hard architecture constraints (e.g. a required review gate). Garbage-in → confident-wrong-out.

## Three things to know before you rely on a run
- **Trusted content only.** The CLIs run full-access — there is no sandbox. Do not feed a council
  untrusted/attacker-influenceable content.
- **It fires real CLIs.** A `plan` over the 4-expert panel takes a few minutes. That is the real
  deliberation, not a stub.
- **Production is the oracle.** "It ran + published" = Observed. "DCM beats one agent" = Unknown
  until the §7 ablation measures it.
