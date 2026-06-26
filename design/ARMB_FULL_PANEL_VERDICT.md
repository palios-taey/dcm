# Arm B — full 9-expert DCM panel review of the implementation (verdict)

Blind, independent review of the DCM producer's Perplexity DR extract (branch `ab/perplexity-dcm`, `caa6d897`) by 9 expert hats run as subagents (robust — no platform-CLI dependency). Clerk synthesis (deterministic, non-voting).

## Panel tally
| Expert | Verdict | Note |
|---|---|---|
| scope-sentinel | PASS / CONFORMANT | diff confined to perplexity.py + perplexity.yaml only |
| blast-shield | PASS / CONFORMANT | no destructive/secret/injection; viewport-gate not coord-click; metadata-only (minor: /tmp screenshot disk-residue) |
| evasive-repair | PASS / CONFORMANT | no surviving fallback; single click+read; scroll-into-view not scroll-to-bottom; echo-guarded |
| dependency-reality | PASS / CONFORMANT | every API resolves; no hallucinated method; worktree YAML matches the 3-step assertion |
| test-integrity | PASS / CONFORMANT | gates on real clipboard end-state, not a flag; linters genuinely CLEAN (non-vacuous). CAVEAT: linter must run from worktree root w/ relative path or it vacuously "passes" |
| foundation | CONCERN / warn | CONFORMANT, but re-derives viewport proof via bespoke scrot/xdotool/raw-Atspi binaries instead of runtime primitives |
| ground-runner | CONCERN / warn | verifies by execution + fail-closed, BUT the pre-copy screenshot is **captured-yet-never-compared** (decorative); visibility oracle is the AT-SPI tree (a lagging cache), not the screen — against §6 |
| **git-historian** | **CONCERN / BLOCK** | deleted `_read_clipboard_until_nonempty` — a **deliberate race-fix** from commit `e00046b6` the day before — reverting to a single fixed-settle read = **regression-reintroduction** (re-opens the racy empty/partial clipboard read) |
| red-team | CONCERN / warn | the break: a **partially-hydrated** DR report (monitor gates on Stop-gone, not DOM-stable length) is copied + accepted (non-empty + not-echo) → **a truncated report ships silently as success**; no completeness/stability check |

## The convergence (the real value)
Four experts, blind and from different lenses, circled the **same deep defect**: the implementation can **silently ship a partial/truncated DR report.**
- git-historian: removed the bounded clipboard re-read that fixed the populate-race.
- red-team: no completeness/stability check + monitor completes on Stop-gone not report-stable.
- ground-runner + foundation: the screenshot — the one artifact that could catch a partial — is captured but **never compared**; visibility is "proven" off the lagging AT-SPI tree, not the screen.

The producer + the conformance linters + the plan all PASSED this. The blind multi-lens panel caught it. A single instance has no such independent cross-check.

## Conflict resolution (evidence-gated, not vote)
**evasive-repair** said `_read_clipboard_until_nonempty` was a banned settle-poll (remove it = PASS); **git-historian** said removing it = regression (BLOCK). Resolved on the contract: **re-READING the clipboard is observation, not re-performing a failed ACTION.** §4a bans re-ACTING (re-click/re-send) and polling a *completion signal*; it ALLOWS re-scanning/observation. A bounded clipboard re-read after ONE copy click (no re-click) is allowed observation — same class as the allowed tree re-scan. → **git-historian is correct; the removal was an over-application of the ban that re-opened the race.** The BLOCK stands.

## VERDICT: BLOCKED — real defect, do not ship as-is.
Required fix (root-cause shape):
1. **Restore a BOUNDED clipboard re-read** (one copy click → re-read until populated-or-timeout; timeout → STOP+screenshot). It's allowed observation, not a settle-poll. (resolves git-historian + red-team's partial)
2. **Add a completeness/stability gate** — read until length stabilizes, and/or compare against the on-screen report; don't accept on non-empty alone. (red-team)
3. **Make the screenshot a real oracle or drop the claim** — either compare it / assert against screen pixels, or stop calling captured-but-uncompared a visibility proof. (ground-runner + foundation)
4. Reuse runtime primitives for viewport verification rather than bespoke scrot/xdotool. (foundation)

## What this proves for the A/B
The DCM expert mix demonstrably **adds value where it matters for the 7-month problem**: not by the producer writing perfect code (its producer had the same silent-partial bug a single instance would), but by the **blind, converging, independent review catching the "works-once-breaks-later" drift before it ships.** That recurring drift — passing once, breaking later — is exactly what a single instance ships unnoticed. (Open: Arm A's passing run may carry the same silent-partial risk, unflagged — worth checking.)
