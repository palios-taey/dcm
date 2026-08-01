# DCM Role-Mix — Round 2 Synthesis (5/5)

**Method:** all five round-2 responses, against the pre-registered rubric. Repo-verified lanes (cloned + code-checked both public repos): **logos (Grok), clarity (Perplexity), gaia (Claude Opus MAX)**. Repo-limited (sandboxed/air-gapped, designed from the packet + first principles, build-claims `[Unknown]` to them): **cosmos (Gemini)** (couldn't reach `dcm` — index lag), **horizon (ChatGPT)** (git clone DNS-failed; verified via GitHub connector). The three verifiers independently confirmed the substrate claims against code; weight their build-findings highest.

---

## 1. Per-lane extraction

| Lane | Count | Roster (fusions) | Resolution | Memory/Git | Layering | Verified | Notable findings |
|---|---|---|---|---|---|---|---|
| **logos** (Grok) | **8** (scale 6–9) | Producer(codex+opt grok) · Foundation Anchor *(Memory+Git+regression fused)* · 6SIGMA Root-Cause *(fallback+root-cause fused)* · Scope+Ground · Blast-Shield · Regression Oracle · Resolver *(clerk+evidence-closer)* · opt Invariant Guardian | Evidence-Gated Provenance Lock; blind round-1 parallel; dissent slot mandatory; veto unilateral; no out-vote | Mandatory pre-flight oracle + provenance hash on every contrib or veto | mechanical→floor, semantic→council, council can't bypass floor | ✅ raw-clone, "zero discrepancies" | Cut pure Clerk (absorb into Resolver); max 2 per base-model |
| **clarity** (Perplexity) | **7** (6-end) | Producer(codex) · Memory-scout(gemini) · Git-historian(grok) · Fallback-hunter(claude) · Ground-runner(diff base) · Blast-shield(gemini) · Scope+6SIGMA *(fused)* | Two-round: **blind audit → evidence-gated reconciliation**; gate closure needs execution trace not assertion; veto | Pre-flight + **post-production memory-citation check** (brief becomes a read-contract) | gate-first, pass gate verdicts as payload to council; overlap cases (forged-done, halluc-API) = both layers | ✅ cloned both | **arms.py roles (correctness/security/rootcause/testwriter) ≠ EXPERIMENT.md roles → C-ablation can't run as-is**; review_cell unparsed→approve is a silent fallback; SWE-bench Live is the surface |
| **gaia** (Claude MAX) | **~4 semantic** +producer+opt clerk (6-end covers 12-end) | Producer(codex)[+P2 diff base] · Execution-runner(non-GPT, mandatory) · Evasive-repair *(fallback+root-cause)* · Foundation *(ISMA+Git, thin→mostly a citation gate)* · Veto/scope-semantics | Sealed **blind round-0 → reveal round-1** (grounded stance) → **evidence-typed concern/resolution projection + fail-closed publish_final** | Shared live ISMA+Git service + **citation/provenance gate** (the orphan `provenance` role = this gate) | "floor raises, council adjudicates"; **move work DOWN** — half the 22 fixtures are mechanical | ✅ deepest audit | see build/safety below — most substantive |
| **cosmos** (Gemini) | **6** (5 semantic + 1 deterministic coordinator) | Cartographer(gemini, pre-flight) · Mutator(claude/codex) · Root-Cause Logician(grok) · Boundary Sentinel(chatgpt) · Truth Verifier(perplexity/grok) · Council Chair *(deterministic Python, NOT LLM)* | Blind parallel fan-out + **binary state locks (APPROVE/VETO)**; mutator barred from arguing; evidence-gated | CANNOT_LIE_PROVENANCE gate: Cartographer emits `ARCHITECTURAL_ANCHOR` manifest (ISMA IDs/SHAs); citation gate rejects uncited reimplementation at floor | physics(mechanical)→floor O(1), intent(semantic)→council | ❌ couldn't reach `dcm` (unindexed); designed from first principles | "12-agent for Git mutation is an anti-pattern; 6-end covers 12-end" |
| **horizon** (ChatGPT) | **9 default** (6 compress / 12 expand **by blast radius**) | Producer · Memory Scout · Git Historian · Ground Runner · Fallback Hunter · Root-Cause/6SIGMA · Scope Sentinel · Blast Shield · **Provenance Clerk (non-voting)** | Blind Round-0 → mesh Round-1; **concern-ledger state machine** (6 states incl SUPERSEDED_BY_CONSTRAINT); no out-vote; veto unilateral; zero-concerns→forced adversarial pass | Pre-flight grounding bundle (10 items) + **provenance gate** (cite-or-declare-no-prior); reuse orchestrator refs as transport | floor=artifact predicates; council=semantic judgment; tripwire opens concern, evidence closes | ❌ DNS-failed clone; verified via connector | **Add explicit Provenance seat** ("cleanest role-mix gap"); fix unparsed-review fail-open; don't claim "6 mechanical checkers implemented" without a file/function map |

---

## 2. Convergence (≥3 lanes, independent)

1. **Function set is settled; count scales by blast radius.** logos(8), clarity(7), gaia(~4–6), cosmos(6), horizon(9-default/6-compress/12-expand) all describe the *same ~5 semantic jobs + producer(s) + a non-voting clerk* — the spread is how aggressively to fuse. **Net: ~6–8 standard, scale to 12 for high-risk (live data/secrets/migrations/cross-repo/release/prior-failure).** The exact count is the ablation's job, not an argument's.
2. **Fuse Memory+Git** into one pre-flight grounding function (logos, gaia, cosmos; horizon in 6-mode). Split only for high-risk (clarity, horizon-standard).
3. **Fuse Fallback-hunter + Root-cause/6SIGMA** — gaia read the two fixtures side-by-side and found them the *same shape* ("silently degrade vs fix the cause"); logos, clarity, horizon-6 concur. One "evasive-repair / symptom-not-cause" seat.
4. **Provenance is NOT a semantic detector seat — forged-done is mechanical → orchestrator floor** (clarity, gaia, logos). Keep a **non-voting Provenance *Clerk*** (ledger + evidence-closure); its *detection* of forged-done lives in the floor (the fixture says `[joins conductor evidence-truth-check gate]`).
5. **Resolution = evidence-gated, never a vote on correctness; safety veto unilateral; blind-first then reveal.** All 5 — the strongest convergence. "You cannot out-vote a failing test."
6. **Ground/Execution-runner MUST be a different base model than the Producer** (all 5; already enforced by `review_cell.py`'s `assert reviewer_cli != producer_cli`). The single most important decorrelation point.
7. **Memory/Git must be an enforced citation/provenance GATE, not a lossy one-shot brief** (all 5). Cite-or-supersede; a Producer that ignores the brief and re-derives must BLOCK.
8. **Deterministic floor carries mechanical modes; council carries semantic judgment; a tripwire opens a concern, evidence closes it** (all 5).
9. **Clerk/coordinator = deterministic state machine, not an LLM voting seat** (all 5).
10. **`review_cell.py`'s unparsed-verdict → `approve` is a silent fallback and must become BLOCK/UNVERIFIED** (clarity, gaia, horizon) — the reviewer embodies the exact mode it's meant to catch.

---

## 3. Divergence (genuine; mostly ablation-resolvable)

- **Exact count: 4 (gaia) / 6 (cosmos) / 7 (clarity) / 8 (logos) / 9 (horizon).** Not a disagreement on the *function set* — all agree on ~5 semantic jobs + producer(s) + non-voting clerk; the spread is fusion aggressiveness. **Resolvable by the C-ablation** (unique-catch per role). Horizon's "scale by blast radius" reconciles the band.
- **Does real-time peer-visible deliberation even help?** gaia's sharpest finding: **the CAS is anti-silo but *pro-conformity*** — it forces every committer to incorporate the first mover, which is the D-shape baked in *before* the I/D experiment has said D wins. If the blind I-arm beats the peer-visible D-arm, the mesh's central mechanism is net-negative for this task. **Only the I/D arm contrast settles this — do not assume D.**
- **Memory+Git fuse vs split** — resolvable by the env-specific eval.

---

## 4. Synthesized role mix (Observer directive honored: 6–12 real-time, multi-CLI; scale by blast radius)

**Standard council (~7 seats):**

| # | Seat | Function / owned modes | CLI (decorrelation) | Convergence basis |
|---|---|---|---|---|
| 1 | **Producer** | writes the patch; never approves own | codex | all |
| 2 | **Foundation** (Memory+Git, **pre-flight**) | ignored-prior-solution + regression-reintroduction; emits the grounding manifest | gemini or claude (long-context) | fuse: logos/gaia/cosmos |
| 3 | **Execution/Ground-runner** | runs repro+tests in throwaway; hallucinated-API, test-tamper, no-op | **non-GPT, ≠ producer** (claude/gemini) | all; highest-value, hardest-to-fake |
| 4 | **Evasive-repair** (Fallback+Root-cause/6SIGMA) | silent-fallback, symptom-not-cause | grok (LOGOS) or claude | fuse: gaia/logos/clarity/horizon-6 |
| 5 | **Scope + Safety-veto** (Blast-shield) | scope-semantics + unilateral veto + injection/exfil intent | grok or gemini + deterministic scanners | all (veto is a property, not a detector) |
| 6 | **Provenance Clerk** (non-voting state machine) | concern ledger, evidence-closure, dissent preservation, citation report; **cannot approve or overrule** | deterministic orchestrator + summarizer | all |

**Scale to 12 for high-blast-radius** (live data/secrets/migration/gate-change/cross-repo/release/prior-failure): split Foundation→Memory+Git, split Scope→Scope-Sentinel + Blast-Shield, add Producer-2 (different base), Test-Integrity, Dependency/API-reality, Red-Team-injection. **Compress to 6** for low-risk — *fixture obligations stay explicit even when fused* (horizon).

**CLI staffing:** Producer=codex; Ground-runner=non-GPT (mandatory); Evasive-repair=grok; Foundation=gemini/claude; Blast=grok/gemini. **Wire the Grok adapter** — `_RUNNERS={codex,gemini}` today; grok is named everywhere, wired nowhere.

**Honest caveat (gaia):** the 22 fixtures test *unique ownership* (≈1/mode), not *recall within a mode* — they justify the collapse *as built*; defending a finer split (the Grok-Heavy "narrow agent catches more in its lane" thesis) requires expanding to N-instances-per-mode and measuring per-role recall. Right now the data says fuse; the recall-per-mode eval can reopen it.

---

## 5. BUILD punch-list (code gaps the audits found — ordered)

1. **Evidence-gated resolution does not exist in code** (gaia; confirmed clarity/horizon). The mesh is **append-only** (no close path) and **`publish_final()` enforces nothing** — a session can publish with a blocking concern open. Build typed `{kind:concern}` / `{kind:resolution, disposition, evidence_ref}` contributions; a projection over the log; **fail-closed `publish_final`** (refuse while any block-severity concern lacks a valid resolution or a safety-veto is open); FIX-VERIFIED requires a runner-produced green artifact, not assertion; veto non-closable-by-peers. **#1 build item.**
2. **Reconcile `arms.py` (4 roles) with the design roster** (clarity, gaia). The eval measures `{correctness, security, rootcause, testwriter}+synthesizer`; **the C-ablation cannot run until the arms implement the synthesized roster.**
3. **Build the fixture-grading + ablation harness** (gaia): feed fixture → role → assert verdict; drop role → does its catch slip + do clean-control false-positives rise (clarity's two-sided ablation).
4. **`council.py` is a 56-line bookend** (gaia/clarity/horizon) — no role→CLI assignment, turn sequencing, or convergence. Build the actual conductor (or document honestly that an external Agent/Task conductor does it).
5. **Add a sealed blind round-0** to the substrate (gaia/clarity/cosmos/horizon); `verify_coordination` exempts round-0.
6. **Wire the Grok adapter** (`_RUNNERS`) or stop citing 4 CLIs.
7. **`review_cell.py` unparsed verdict → BLOCK/UNVERIFIED**, not `approve`.

---

## 6. SAFETY punch-list

1. **`cli_adapter.py` injection→host-action surface** (gaia "most urgent safety," cosmos, horizon, clarity). An acting `codex exec` agent is seated on attacker-influenceable peer text; "do not edit files" is advisory, not a sandbox. Run acting CLIs in throwaway, fs/network-dropped containers; the mesh must **refuse to seat an acting CLI on an untrusted/ISMA-derived payload**; read-only lenses for any council touching untrusted content.
2. **`destructive-ops` is `documented(feedback)`, not a wired gate** (clarity) — close it at the orchestrator floor so it never reaches the council; the veto is the net, not the primary defense.

---

## 7. Measurement plan

- **Benchmark: SWE-bench Live (fresh/uncontaminated), never saturated Verified** (all repo-verified lanes; Verified N=23 best-single 91% = no headroom; Live 73%, +6.7pp union = real signal). Headroom-to-fail is required or the result is inconclusive-by-saturation regardless of team size.
- **C-ablation (settles count):** unique-catch per role + false-positive rate on the 4 clean controls + cost/wall-clock + duplicate-concern rate + conformity rate + parse-fail rate + prior-foundation-used rate + regression-escape. Cut/fuse a role with no unique catch *unless* rare-but-high-severity (Blast/veto — expected-harm, not frequency).
- **I/D arm contrast (settles whether deliberation helps):** `arms.py` I (blind) vs D (peer-visible); D−I isolates the coordination primitive. If I≥D, real-time peer visibility is net-negative for this task. **Let this decide; don't assume D.**
- **Recall-per-mode (settles fuse-vs-split):** expand fixtures to N-per-mode, measure per-role recall — tests the Grok-Heavy "narrow beats broad in-lane" thesis the 1-per-mode fixtures can't adjudicate.
- **Bespoke history-rich eval (validity gap):** memory-scout + git-historian and the *ignored-prior-solution* mode **cannot be measured by SWE-bench** (fresh, no ISMA, no multi-commit history). Build seed tasks from our own repos/ISMA where the right answer requires citing/superseding a known prior tile or commit; until then that mode is **asserted, not measured**.

---

## Headline

The substrate is real and validated (mesh CAS = a correct serialization primitive; all three repo-verifying lanes confirmed it). The function set is converged (~5 semantic jobs + producer + non-voting clerk, on a fatter deterministic floor, scaling to 12 for high-blast-radius). The experiment is sound and the arms methodology is "publishable-shaped" (gaia). What's NOT built yet, surfaced by the audit: **evidence-gated resolution + fail-closed `publish_final`, the actual council conductor, the Grok runner, CLI sandboxing, and the arms reconciled to the roster so the ablation can run.** The open count + whether real-time deliberation earns its seat are **resolvable by the C-ablation + I/D contrast on a headroom benchmark** — the build-and-measure path.
