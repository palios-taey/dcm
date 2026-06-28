# AGENTS.md — DCM operating guide (codex / gemini / any CLI agent)

You are an AI operating the DCM (Distributed Cognitive Mesh). This file is the entry point for
CLI agents (codex reads `AGENTS.md`, gemini reads it too); the content is identical in intent to
[`CLAUDE.md`](./CLAUDE.md) — **read `CLAUDE.md` for the full operating guide.** This file is the
short form plus the non-negotiable rules, so an agent with zero outside context can run a council.

## What this is
A council of differentiated experts (real CLIs: codex / claude / gemini / grok) deliberating on one
task **through a shared Neo4j mesh** with structural read-before-write (compare-and-set). The mesh is
the only coordination channel. Layered on top: an evidence-gated review/resolution flow. The point is
to catch drift/defects a single agent ships.

## Setup (one command)
```bash
./setup.sh            # checks deps + a reachable DCM_NEO4J_URI, prints the next step
```
Config is one env group: `DCM_NEO4J_URI` (default `bolt://localhost:7687`), `DCM_NEO4J_USER` /
`DCM_NEO4J_PASSWORD` (optional on loopback; a non-loopback URI with no auth is REFUSED, fail-closed).

## Code map
| File | What it is |
|---|---|
| `mesh.py` | substrate: `start_session` / `read_session` / `contribute(read_version)` (real CAS) / `publish_final` (fails closed on open block-concerns) / `verify_coordination`. |
| `mesh_cli.py` | agent ⇄ mesh interface: `start` / `read` / `contribute` / `status` / `publish`. |
| `cli_adapter.py` | runs a real CLI as a mesh expert (`cli_expert`); prompts via stdin / `--prompt-file` (never argv). |
| `council.py` | `council_plan` / `council_review`: seat roster, blind round → reveal/resolution, evidence-gated publish. |
| `platform_dcm.py` | `produce` (a codex producer, verified on REAL runs) + `audit` (blind N-seat diff audit). |

## Common commands
```bash
python platform_dcm.py audit --diff-file <patch> --topic "<what>"   # seats the canonical §4 roster
#   add --seats "role:cli,..." ONLY for a deliberate scoped subset; default = the full roster
python platform_dcm.py produce --target-repo <worktree> --prompt-file <prompt>
python mesh_cli.py start "<topic>" "<payload>"        # -> session_id
python mesh_cli.py read <session_id>                  # peers + version (read before you write)
python mesh_cli.py contribute <session_id> <role> <read_version> --content -
```

## Working rules (non-negotiable)
- **Read before write.** `read` for the live `version`, build on peers, `contribute` with that version.
  On `StaleReadError` re-read + redo — someone moved while you thought.
- **Production is the oracle. NO synthetic tests.** Verify a fix by running the real workload on the
  real target, not a fixture you wrote.
- **Evidence-gated.** A concern is a gate that closes only on evidence. No vote-away on correctness;
  safety-veto is unilateral; deadlock escalates, never manufactures consensus.
- **Report Observed / Inferred / Unknown.** Never inflate process into outcome.
- **Public repo.** This is `palios-taey/dcm`, public. Every change is a scrubbed public commit — no
  operator paths, IPs, internal repo names, or secrets. Run inputs are runtime args, never committed.

## Verification
```bash
python validate_substrate.py        # proves the CAS serializes (N-thread race → 1 win / N-1 stale)
python docs_coherence_check.py       # fails if CLAUDE.md drifts from the code
```
