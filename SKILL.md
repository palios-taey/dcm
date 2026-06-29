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

## Who deliberates (the FULL roster — every run, automatically)
Every council seats the **full 9-reviewer defined-role library** — there is no smaller option and no
panel knob. A 3- or 4-seat "council" is the rejected stub. With the producer + synthesizer this is a
**10–12-seat council**.

| seat | role | CLI |
|---|---|---|
| producer | writes the plan/patch (never approves its own; plan/produce flows) | **codex** |
| reviewer | Memory-Scout — ignored prior solutions / reusable utilities (pre-flight grounding) | **gemini** |
| reviewer | Git-Historian — regression-reintroduction from history (pre-flight grounding) | **claude** |
| reviewer | Ground-runner — runs code/tests, no-op/hallucination | **claude** |
| reviewer | **Evasive-repair** — silent fallbacks, symptom-not-cause, 6SIGMA root-cause | **grok** |
| reviewer | Scope-Sentinel — scope-creep into unrelated surfaces | **gemini** |
| reviewer | Blast-Shield — destructive ops, secrets, injection, blast radius (safety-veto) | **grok** |
| reviewer | Test-Integrity — deleted/neutered/no-op tests | **claude** |
| reviewer | Dependency/API-Reality — hallucinated APIs, version mismatches | **gemini** |
| reviewer | Red-Team-Injection — prompt-injection / exfil / untrusted-input→action | **grok** |
| clerk | Provenance Clerk / synthesizer (non-voting) — ledger, evidence-closure, terminal converge | deterministic |

**All four CLIs run every time — codex, claude, gemini, AND grok.** There is no `--tier` and no way
to seat fewer; high blast radius adds a second producer in the produce flow, it never shrinks the panel.

**What if a CLI is down, or you only have Claude Code?** The council degrades, it does not crash. A
seat whose CLI is down / rate-limited / times out / returns empty **falls back to another installed
CLI** (reviewers never fall back to the producer base, codex). If only `claude` is installed, every
seat runs on claude (role-differentiated, gates intact) — and the result's `ledger.decorrelation`
field says **`single-model`** with a note, because you've lost cross-model diversity, which is DCM's
main value. So a Claude-Code-only operator gets a working but honestly-degraded council, never a
traceback. Check `decorrelation.status` (`decorrelated` / `partial-decorrelation` / `single-model`)
to know what you actually got.

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
