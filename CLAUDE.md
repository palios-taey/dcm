# CLAUDE.md — DCM operating guide (for the AI running this repo)

You are an AI operating the DCM (Distributed Cognitive Mesh). This doc is for you, not a human:
read it and you can run a multi-expert council with zero outside context. Every CLI here prints
what you have, why it's blocked or ready, and the next command — trust that output over guessing.

## What this is (product shape)
A council of differentiated experts (real CLIs: codex / claude / gemini / grok) deliberating on one
task **through a shared Neo4j mesh** with structural read-before-write (compare-and-set). The mesh is
the only coordination channel — no expert works in a silo or commits into the void. Layered on top:
an evidence-gated review/resolution flow. The point is to catch drift/defects a single agent ships.

## Setup (one command)
```bash
./setup.sh            # checks python deps (neo4j) + a reachable DCM_NEO4J_URI, prints the next step
```
Env (the only config): `DCM_NEO4J_URI` (default `bolt://localhost:7687`), `DCM_NEO4J_USER` /
`DCM_NEO4J_PASSWORD` (optional on loopback; a non-loopback URI with no auth is REFUSED, fail-closed).

## Code map
| File | What it is |
|---|---|
| `mesh.py` | the substrate: `start_session` / `read_session` / `contribute(read_version)` (real CAS — commit only if no peer arrived since your read; else `StaleReadError` → re-read+redo) / `publish_final` (fails closed on open block-concerns) / `verify_coordination`. Own `:DCMSession`/`:DCMContribution` namespace. |
| `mesh_cli.py` | the agent ⇄ mesh interface: `start` / `read` / `contribute` / `status` / `publish`. How a CLI expert (or you) joins a session. |
| `cli_adapter.py` | runs a real CLI as a mesh expert (`cli_expert`): reads the session, prompts the CLI, commits its output via CAS. Prompts go via stdin / `--prompt-file` (never argv — coordinated prompts exceed MAX_ARG_STRLEN). gemini needs `--approval-mode yolo`. |
| `council.py` | `council_plan` / `council_review`: seat the roster (validated bases; ground-runner ≠ producer), blind round → reveal/resolution, evidence-gated `publish_final`. |
| `platform_dcm.py` | orchestrator for fixing one driver: `produce` (a codex producer in a target worktree, verified on REAL runs — production is the oracle) + `audit` (blind diff audit through the mesh; `--tier` scales the roster). |
| `scaling.py` | blast-radius roster sizing (§4): `tier_for(...)` (triggers → compress/standard/expand) + `reviewer_roster_for_tier(tier)` (3/4/9 reviewers, composed from canonical + `arms.EXPAND_ROLES`). The council seats N by tier, never a fixed constant. |

## Common commands
```bash
# run a blind N-seat audit of a diff (real CLIs through the mesh)
python platform_dcm.py audit --diff-file <patch> --topic "<what>" [--tier expand]
#   --tier compress|standard|expand → 3|4|9 reviewers by blast radius (default standard).
#   use expand for high-blast-radius diffs (secrets/migration/release/cross-repo/HIGH impact).
#   --seats "role:cli,..." overrides to a deliberate scoped subset.
# run a producer that implements + verifies on real runs (no synthetic tests)
python platform_dcm.py produce --target-repo <worktree> --prompt-file <prompt>
# drive the mesh directly
python mesh_cli.py start "<topic>" "<payload>"        # -> session_id
python mesh_cli.py read <session_id>                  # peers + version (read before you write)
python mesh_cli.py contribute <session_id> <role> <read_version> --content -   # CAS commit
```

## Working rules (non-negotiable)
- **Read before write.** Always `read` for the live `version`, build on peers, `contribute` with that
  version. On `StaleReadError` re-read + redo — someone moved while you thought.
- **Production is the oracle. NO synthetic tests.** Verify a fix by running the real workload on the
  real target, not a fixture you wrote. A passing self-authored test is not evidence.
- **Evidence-gated.** A concern is a gate that closes only on evidence (a real green run, a trace).
  No vote-away on correctness; safety-veto is unilateral; deadlock escalates, never manufactures consensus.
- **Report Observed / Inferred / Unknown.** Never inflate process into outcome.
- **Public repo.** This is `palios-taey/dcm`, public. Every change is a scrubbed public commit pushed to
  origin — no operator paths, IPs, internal repo names, or secrets. `gitleaks` + an internal-term grep
  must be clean before push. Operator-specific run inputs are runtime args, never committed.

## Verification
```bash
python validate_substrate.py     # proves the CAS serializes (N-thread race → 1 win / N-1 stale)
```
A real council "passes" when its blind, independent review caught a real defect the producer shipped and
the fix re-verified on a real run — measured, not asserted.

## Reporting
State what ran + raw evidence, labeled Observed / Inferred / Unknown. "The mesh coordinates" = Observed;
"DCM beats a single agent" = Unknown until a real production run says so.
