# DCM core-code audit findings (Chat seats: grok/gemini/claude, find-bugs-not-endorse)

Audit of mesh.py / cli_adapter.py / council.py. Real defects to fix (chat-audit-fixes). Scrubbed.


## [root-cause]
Auditing the DCM core diff for root-cause fixes vs patches. Reading the full artifact and source files.
Checking a few specific edge cases in the diff:
**ROOT-CAUSE/6SIGMA audit (diff-grounded)**

**CLAIM: CAS constraint is root-cause, not patch** | **GROUND:** `contribute()` derives `seq` server-side from live `cnt`, gates `WHERE cnt=$rv`, and serializes same-version races via `(session_id,seq)` uniqueness → `ConstraintError` → `StaleReadError`; replaces racy read-committed count-only gate (audit Finding A). | **STANCE:** Agree — upstream shape fix (single-writer slot), not a bypass branch.

**CLAIM: `claimed_peers` honesty gate is root-cause** | **GROUND:** `verify_coordination()` diffs `claimed_peers` against earlier `contrib_id`s, not circular `peers_present` auto-stamp (Finding B). | **STANCE:** Agree — corrects the tested register; docs honestly scope semantic incorporation as unprovable.

**CLAIM: `cli_adapter` StaleReadError loop is allowed CAS retry, not settle-poll** | **GROUND:** Bounded `max_retry`, explicit `except mesh.StaleReadError: continue`, fresh `read_session()` each iteration; no success polling on external state. | **STANCE:** Agree on classification. **Extend:** each retry re-invokes the CLI — output can drift without deterministic re-incorporation; observational honesty only.

**CLAIM: council resolution path cannot close veto concerns via `ACCEPTED-RISK`** | **GROUND:** `mesh._VETO_CLOSING_DISPOSITIONS` includes `ACCEPTED-RISK`; `council._RESOLUTION_DISPOSITIONS` and `_resolution_prompt_extra()` omit it; parser maps unknown dispositions → `ESCALATE` (non-closing). Scope-blast `safety_veto` block concerns are therefore council-closable only via `FIX-VERIFIED`. | **STANCE:** Disagree with integration completeness — mesh/council contract drift is a **BLOCK** closure hole, not a simplification.

**CLAIM: `contribute()` lacks session-open guard** | **GROUND:** Diff shows no `status='open'` check before `CREATE`; `publish_final()` only sets `close


## [fallback-hunter]
## FALLBACK-HUNTER contribution

**CLAIM: `_run_codex` contains an undisclosed silent fallback that can substitute unparsed raw output for the contribution. GROUND:** `cli_adapter.py` →
```python
tail = out.rsplit("tokens used", 1)[-1]
tail = re.sub(r"^\s*\d[\d,]*\s*", "", tail).strip()
return tail or out.strip()
```
The `tail or out.strip()` is a banned **silent-fallback** shape: when the footer parse yields empty (footer text changes, multiple "tokens used" lines, truncated stdout), it silently swaps in the FULL raw stdout — reasoning trace, footers, banner — as the contribution. Downstream `_extract_json_object` then greedily grabs `text.find("{")..rfind("}")`, so it can lock onto a JSON-looking blob embedded in the reasoning trace rather than the real answer, and `_parse_review_output` will treat that as a parsed verdict. This is fail-*open*, not fail-closed: a parse miss should surface (raise / mark unparsed), not silently degrade to raw. The other three runners (`_run_gemini/_run_claude/_run_grok`) do NOT do this — they return `(stdout or "").strip()` straight, so an empty there fails closed into a block concern. The codex path is the asymmetric hole. OBSERVE-correct fix: if the footer marker is absent, do not invent a tail — return a sentinel/empty that the parser rejects.

**CLAIM: the publish_final "fail-closed" gate is satisfiable by a self-issued, unverified completion marker. GROUND:** `council_review` resolves each open concern by re-running **the same role's CLI** (`f"{role}:resolver"`) which can return `FIX-VERIFIED` + any non-empty `evidence_ref`; `_resolution_closes`/`open_concerns` check only `_has_evidence()` (non-empty string), never that the evidence is real. So an expert raises a block concern, then re-ACTS to close its own concern with `evidence_ref="see above"`, and `publish_final` emits `EVIDENCE_GATE: NO_OPEN_BLOCK_CONCERNS` + `VERDICT:`. That is a **positive-completion-marker** standing in for an observation. STANCE on the code's own defen


## [scope-blast]
An audit of the Distributed Cognitive Mesh (`dcm/`) core codebase (`mesh.py`, `cli_adapter.py`, `council.py`) reveals several critical block-severity implementation bugs, enum mismatches, and security/crash vulnerabilities. 

Below is the structured analysis through the **SCOPE+BLAST** lens:

---

### 1. Scope & Blast Radius
* **In-Scope Boundary:** Yes. The diff is strictly confined to `mesh.py`, `cli_adapter.py`, and `council.py`, representing the core cognitive coordination substrate. No extraneous workspace files are modified.
* **Shared-File Risks:** High. Modifying `mesh.py` directly affects validation runners (e.g., `/tmp/dcm_validate.py`) and staging components. The bugs identified below will trigger runtime failures across all downstream consumers.

---

### 2. High-Severity Concerns (Bugs Found)

#### A. [CRASH] `NoneType` AttributeError in `council.py`
In `council.py` (lines ~111 and ~339):
```python
per_role = {role: {**_role_public(spec), "blind": None, "resolutions": []}
            for role, spec in roles.items()}
```
If a CLI expert fails, times out, or is skipped during the blind round, `per_role[role]["blind"]` remains `None`. 
Later, `_classification_counts` (line ~133) and `_final_text` (line ~353) attempt to access:
```python
blind = data.get("blind", {})
parsed = blind.get("parsed", {})
```
Because the key `"blind"` exists but holds `None`, `data.get("blind", {})` returns `None` (the default dictionary behavior does *not* override an explicit `None` value). Calling `.get("parsed")` on `None` raises a catastrophic **`AttributeError: 'NoneType' object has no attribute 'get'`**, crashing the entire council runner.

#### B. [LOGIC/ENUM MISMATCH] Broken `"ACCEPTED-RISK"` Resolution Path
In `mesh.py`, the closing logic for a safety veto concern is defined as:
```python
_VETO_CLOSING_DISPOSITIONS = {"FIX-VERIFIED", "ACCEPTED-RISK"}
```
However, in `council.py` (line ~80), the resolution parser restricts valid dispositions:
```python
_RESOLUTION_DISPOS
