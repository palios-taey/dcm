# DCM platform-fix methodology — taking a broken UI/consult driver to verified end-to-end

A reusable DCM loop for fixing one driver across all its modes, with production as the only oracle
(NO synthetic tests). Generalized from a multi-platform run; operator paths/displays are runtime args
(see `platform_dcm.py`), never committed here.

## The loop (per platform)
1. **Assess** (read-only): for each declared mode, where does the pipeline break — navigate → display-readiness → mode-select → send → monitor → extract? Cite file:line. Produce a mode×status matrix.
2. **Produce**: a producer (codex) implements the assessed fix in the target worktree and **verifies on REAL runs** on a dedicated cold display — discover the real failure by running, root-cause, fix, rerun. `platform_dcm.py produce`.
3. **Audit**: a blind N-seat diff audit (real CLIs through the mesh) checks the diff for banned shapes, shared-file blast radius, and root-cause-vs-patch. `platform_dcm.py audit`.
4. **Resolve**: any BLOCK goes back to the producer; re-audit by the blocking seat confirms FIX-VERIFIED. Evidence-gated, never voted away.

## Acceptance (per mode)
Mode **verified ENGAGED** (the engaged/selected/checked/pressed state observed — not just "clicked"),
correct completion (the right terminal signal, no premature/positive-marker completion), and the **full**
response extracted hands-off (screenshot + clipboard), single read, zero action-retries. For race-prone
paths: ≥3 consecutive cold-green runs (one green doesn't prove a race fixed).

## Three cross-cutting root causes (recurred across every platform — not N separate problems)
1. **Mode-selection verifies the wrong thing** — it confirms the *click happened / menu closed / element present* instead of the mode being **actually engaged**. The real engaged-signal varies by UI (a checked radio item; a pressed toggle; an SVG-checkmark child with no AT-SPI selected state). Wrong verification → wrong completion-cycle count → premature/placeholder extraction. Fix: verify the genuine engaged-state; if it can't be confirmed, STOP (never silently downgrade).
2. **Completion fires before the answer is done** — the terminal signal (e.g. a Stop control gone) trips while the answer is still rendering (a "still-working" placeholder, a long thinking-pause, a report still hydrating). Fix: a **negative gate** — terminal AND not-still-generating — never a positive "answer complete" marker.
3. **Extraction grabs the wrong/partial surface** — the copy control is off-screen, or a different surface (inline answer vs report-card) exposes a different control. Fix: one unified geometry chain — select the right control → scroll-into-view → on-screen assert → one click → one bounded read; empty after a verified-on-screen click → STOP, never re-click.

## Anti-patterns the audit blocks (observed producers introduced these and the audit caught them)
- Fabricating/seeding state on a resume path instead of observing the live state.
- A geometry fork that reintroduces a regression a prior commit fixed on purpose.
- A retry loop around a failed ACTION (re-SCAN/observation is allowed; re-ACT is not).
- A positive-completion marker (control-present / "complete" label) instead of the negative terminal gate.

## Note on shared engine files
Per-platform fixes often need the same small engine change (e.g. binding the run to its target display
before UI imports). Independent lanes deriving the *same* fix confirms it's real — but it should be **one**
unified engine change at merge, not N per-platform copies. The audit flags this for reconciliation.
