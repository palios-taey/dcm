#!/usr/bin/env bash
# DCM one-command setup check (AI-native: prints what you have + the next step).
set -uo pipefail
URI="${DCM_NEO4J_URI:-bolt://localhost:7687}"
ok=1
python3 -c "import neo4j" 2>/dev/null && echo "[ok] python neo4j driver present" || { echo "[MISSING] python neo4j driver -> pip install neo4j"; ok=0; }
python3 - <<PY 2>/dev/null && echo "[ok] mesh reachable at $URI" || { echo "[BLOCKED] mesh not reachable at DCM_NEO4J_URI=$URI -> start Neo4j or set DCM_NEO4J_URI (+ DCM_NEO4J_USER/PASSWORD if non-loopback)"; ok=0; }
import os,sys; sys.path.insert(0,os.path.dirname(os.path.abspath('mesh.py')))
import mesh; mesh._db()
PY
for cli in codex claude gemini grok; do command -v $cli >/dev/null 2>&1 && echo "[ok] CLI seat available: $cli" || echo "[note] CLI seat missing: $cli (seats using it will be unavailable)"; done
if [ "$ok" = 1 ]; then echo "NEXT: python validate_substrate.py  (proves the CAS serializes), then platform_dcm.py audit/produce — see CLAUDE.md"; else echo "NEXT: resolve the [MISSING]/[BLOCKED] lines above, then re-run ./setup.sh"; fi
