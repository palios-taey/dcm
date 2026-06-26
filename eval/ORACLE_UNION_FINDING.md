# Oracle-union pre-test — honest finding (2026-06-26)

**Run:** 4 models solo (codex/claude/grok/gemini), k=1, N=23 balanced (2/repo), hidden-test graded. 1 provider error during run (retried). Data: runs/oracle_pilot/ORACLE_UNION.json.

## Result
| model | resolved | rate |
|---|---|---|
| grok | 21/23 | **91.3%** (best) |
| claude | 19/23 | 82.6% |
| codex (gpt-5.5) | 15/23 | 65.2% |
| gemini | 14/23 | 60.9% |
| **union (any of 4)** | 22/23 | **95.7%** |

- union − best = **+4.3pp = ONE instance** (22 vs 21 of 23).
- rescue among best-failures = 1 of 2 (n=2, meaningless).
- unique solves: grok 1, everyone else 0.

## Honest interpretation (correcting my own automated verdict_hint)
The script's `verdict_hint` said "complementarity present → build the study." **That is wrong, and I'm overriding it.** The threshold (union−best > 0.03 OR rescue > 0.05) is below the noise floor at N=23 — one instance trips it. The real story is:
- **The standard set is SATURATED.** Best single (grok) = 91%; only 2 misses exist. There is essentially no headroom for any coordination scheme — generic OR identity-matched — to capture. The "+4.3pp union" is 1 instance = noise, not signal.
- This **empirically confirms Gaia's benchmark concern**: SWE-bench Verified standard instances are too easy/saturated to detect a coordination effect; the effect would be smaller than the benchmark's own noise.
- [Observed] So on standard Verified instances: **no exploitable complementarity.** A study here cannot detect anything.

## What this does NOT say (cannot-lie scoping)
- It does NOT say diversity/identity is delusional in general. It says the *standard Verified set has no headroom to test it.*
- The numbers are k=1, N=23, and confound model with each CLI's agent scaffold (codex exec vs claude -p vs grok -p vs gemini) — "deployed-stack" rates, not pure-model. Noisy. Grok leading (91%) is notable but not robust at this N/k.
- The strongest single is **grok**, not codex/claude — so any "best homogeneous / best single" baseline is grok, and codex (the Level-1 matched base, 65%) is mid-tier.

## The real next step
To measure coordination at all, the study must run on the **HARD subset** — instances where the best single model fails — because that is the only place any headroom exists. Build it by screening the best model(s) over a large pool and taking the failures. If, on the hard subset, other models rescue the best model's failures at a rate above noise → complementarity is real and the identity-vs-counterbalanced study is warranted. If not → honest null, and the rigorous move is a harder/contamination-resistant benchmark (SWE-bench Pro/Live, per Gaia/Horizon) or an honest "headroom too small on available benchmarks" conclusion.

**Discipline note:** my own automation over-called this; reading the actual numbers (1 instance, 91% saturation) corrected it. If I'd trusted the green "complementarity present," I'd have built a 14-arm study on a set that cannot detect the effect — exactly the false-foundation failure to avoid.

---

# Hard-subset oracle-union (N=45, human difficulty 1-4h/>4h) — 2026-06-26

**Data-quality caveat (cannot-lie):** 34 of 180 cells were GEMINI provider errors — gemini hit a hard daily quota (`retryDelayMs ~63,000,000` ≈ 17h). codex/claude/grok had ZERO errors. So gemini's 11% is INVALID (quota-capped, not capability) and is EXCLUDED. Gemini needs a clean re-run on quota reset for a complete 4-model union; the conclusion below is robust without it (gemini was weakest regardless).

## Clean 3-model result (codex/claude/grok), hidden-graded
| model | resolved | rate |
|---|---|---|
| grok | 40/45 | **88.9%** (best) |
| claude | 32/45 | 71.1% |
| codex (gpt-5.5) | 23/45 | 51.1% |
| **oracle union (3)** | 42/45 | **93.3%** |

- union − best = **+4.4pp = 2 instances** (oracle ceiling — knowing which model to pick).
- grok failures = 5; rescued by another model = 2. grok-ONLY solves = 9 (single-model strength, not complementarity).

## Honest gating conclusion
Even on the HARDEST human-rated Verified instances, the best single model (grok-class) is **near-saturated at ~89%**. The oracle ceiling for any multi-model scheme is **+2 instances (~+4pp)** — and that is the best case (a real, non-oracle coordination scheme gets less), mostly attributable to grok's raw strength (9 unique) rather than error-decorrelation (rescue = 2 of 5). More seeds (k=3) would raise best-single further, SHRINKING the headroom — so +4pp overstates it.

**This is below the study's detectability (~5pp MDE).** Building the 14-arm study on SWE-bench Verified (standard OR hard) would return INCONCLUSIVE-by-saturation, not a real answer. The pre-test did its job: it prevented an uninterpretable expensive study.

**The honest options now (decision for Jesse, via Conductor):**
1. **Harder / contamination-resistant benchmark** (SWE-bench Pro, SWE-bench-Live, or the globally-unsolved tail) where the best single model has real headroom to fail — the only surface on which a coordination/diversity (or identity-matched) effect could be DETECTED. Per Gaia/Horizon, the rigorous path.
2. **Honest conclusion as-is:** "On SWE-bench Verified, the best single model is near-saturated even on the hardest instances; the oracle ceiling for multi-model coordination is ~+2 instances, dominated by single-model strength, not complementarity. No detectable headroom for a coordination/diversity benefit on this benchmark." A real, publishable finding converging with Self-MoA + benchmark-saturation literature.

Either way: do NOT build the 14-arm study on Verified. [Observed, k=1, CLI-deployed-stacks, gemini-incomplete.]

---
# FRESH / uncontaminated (SWE-bench-Live, 2025 post-cutoff) — N=30, k=1, 0 provider errors (2026-06-26)
Per Jesse's correction (89% = contamination artifact, not capability). Clean 3-model (gemini quota-capped, excluded):
| model | Verified-std | Verified-hard | FRESH Live |
|---|---|---|---|
| grok (best) | 91% | 89% | **73%** |
| claude | 83% | 71% | **53%** |
| codex | 65% | 51% | **50%** |
- union(3) = 80% = +6.7pp (2 instances) over best. rescue-among-best-failures = 2/8 = 25%. grok unique = 5.

## Honest conclusions
1. **CONTAMINATION CONFIRMED [Observed]:** best-single drops ~16pp (89%→73%) on uncontaminated code; others ~50%. The Verified 89% was substantially memorization. Jesse's correction empirically validated; the model is far from saturated on novel code.
2. **HEADROOM EXISTS on fresh code:** best-single fails 27% (vs 11% on saturated Verified-hard) → there IS room to measure coordination — the earlier "no headroom" conclusion was a benchmark artifact, exactly as Jesse said.
3. **Modest complementarity signal:** rescue 25% (2 of grok's 8 failures solved by another model), union +6.7pp — decorrelated errors are REAL on fresh code (absent on saturated Verified). Small/noisy (N=30,k=1), suggestive not conclusive.
4. **Scoping:** this is the GENERATION question + the oracle ceiling. The signal is modest, consistent with "diversity-for-generation is marginal; the value is in REVIEW (guardian)." If a generation-coordination study is ever run, Live (fresh) is the correct surface, NOT Verified.
