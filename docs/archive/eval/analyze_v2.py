"""
DCM SWE-bench eval — v2 verdict machinery (post Family review).

Implements the consensus fixes (SYNTHESIS.md / dcm_swebench_eval_DESIGN_v2.md):
  - primary contrast D - I (isolates the coordination primitive)
  - three-verdict rule on a CONFIDENCE INTERVAL, never "archive on non-significance"
  - repo-clustered bootstrap CI (item-level McNemar assumes pair independence;
    repos cluster) + k-seed aggregation (agents are stochastic)
  - minimum-discordant-events gate -> INCONCLUSIVE
  - Django/non-Django + per-repo descriptive splits
  - cost is recorded (Pareto), NEVER gates the verdict

Resolved labels come ONLY from the official harness. Inputs: per-arm ledgers
holding per (instance, seed) resolved booleans + repo + cost.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from collections import defaultdict

HERE = Path(__file__).resolve().parent
MATERIAL_MARGIN = 0.05   # +5pp practical-significance threshold (pre-registered)
MIN_DISCORDANT = 8       # below this, verdict is INCONCLUSIVE (not enough signal)
SEED_RNG = 6252026       # bootstrap seed (frozen; no Math.random in this env anyway)


def _rate_by_instance(arm_seed_resolved: dict[str, list[bool]]) -> dict[str, float]:
    """instance_id -> mean resolved over its k seeds (in [0,1])."""
    return {i: (sum(v) / len(v) if v else 0.0) for i, v in arm_seed_resolved.items()}


def paired_diff(a: dict[str, float], b: dict[str, float], ids: list[str]) -> float:
    return sum(a[i] - b[i] for i in ids) / len(ids)


def _lcg(seed: int):
    x = seed & 0xFFFFFFFF
    while True:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        yield x / 0x7FFFFFFF


def clustered_bootstrap_ci(a: dict[str, float], b: dict[str, float],
                           repo: dict[str, str], ids: list[str],
                           iters: int = 5000, seed: int = SEED_RNG) -> tuple[float, float, float]:
    """Repo-clustered bootstrap 95% CI for mean paired diff (a-b).

    Resamples REPOS with replacement (cluster bootstrap) so repo dependence is
    respected. Returns (point, lo, hi) for a two-sided 95% interval.
    """
    by_repo: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        by_repo[repo[i]].append(i)
    repos = list(by_repo)
    point = paired_diff(a, b, ids)
    rng = _lcg(seed)
    diffs = []
    for _ in range(iters):
        sample_ids = []
        for _ in range(len(repos)):
            r = repos[int(next(rng) * len(repos)) % len(repos)]
            sample_ids.extend(by_repo[r])
        diffs.append(paired_diff(a, b, sample_ids))
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[int(0.975 * len(diffs)) - 1]
    return point, lo, hi


def discordant_count(a_res: dict[str, list[bool]], b_res: dict[str, list[bool]],
                     ids: list[str]) -> int:
    """Instances where the majority-resolved label differs between arms."""
    def maj(v): return sum(v) > len(v) / 2
    return sum(1 for i in ids if maj(a_res[i]) != maj(b_res[i]))


def three_verdict(point: float, lo: float, hi: float, discordant: int,
                  margin: float = MATERIAL_MARGIN) -> str:
    if discordant < MIN_DISCORDANT:
        return f"INCONCLUSIVE (only {discordant} discordant < {MIN_DISCORDANT}-event gate)"
    if lo > margin:
        return f"JUSTIFIED (95% CI lower bound {lo:+.3f} > +{margin:.0%})"
    if hi < margin:
        return f"BENEFIT RULED OUT (95% CI upper bound {hi:+.3f} < +{margin:.0%})"
    return f"INCONCLUSIVE (CI [{lo:+.3f}, {hi:+.3f}] straddles +{margin:.0%})"


def verdict_v2(ledgers: dict[str, dict], ids: list[str], repo: dict[str, str]) -> dict:
    """ledgers[arm] = {'seed_resolved': {iid: [bool,...]}, 'cost': {...}}. D-I primary."""
    res = {arm: ledgers[arm]["seed_resolved"] for arm in ledgers}
    rate = {arm: _rate_by_instance(res[arm]) for arm in ledgers}

    def contrast(x, y):
        pt, lo, hi = clustered_bootstrap_ci(rate[x], rate[y], repo, ids)
        disc = discordant_count(res[x], res[y], ids)
        return {"point": pt, "ci95": [lo, hi], "discordant": disc,
                "verdict": three_verdict(pt, lo, hi, disc)}

    out = {
        "N": len(ids), "material_margin": MATERIAL_MARGIN,
        "mean_resolved_rate": {arm: sum(rate[arm].values()) / len(ids) for arm in ledgers},
        "PRIMARY__D_minus_I": contrast("D", "I") if "D" in ledgers and "I" in ledgers else None,
        "secondary": {
            name: contrast(x, y) for name, (x, y) in {
                "D_minus_BoN": ("D", "BoN"), "I_minus_BoN": ("I", "BoN"),
                "BoN_minus_S": ("BoN", "S"), "D_minus_S": ("D", "S"),
            }.items() if x in ledgers and y in ledgers},
        "cost_pareto_nongate": {arm: ledgers[arm].get("cost", {}) for arm in ledgers},
    }
    # Django / non-Django descriptive split on the primary contrast
    if "D" in ledgers and "I" in ledgers:
        for label, sub in (("django", [i for i in ids if repo[i] == "django/django"]),
                           ("non_django", [i for i in ids if repo[i] != "django/django"])):
            if sub:
                pt = paired_diff(rate["D"], rate["I"], sub)
                out.setdefault("split_D_minus_I", {})[label] = {"n": len(sub), "point": pt}
    return out


if __name__ == "__main__":
    # self-check the stats on synthetic data (verifying the function, not the eval)
    ids = [f"r{r}__{j}" for r in range(5) for j in range(20)]
    repo = {i: i.split("__")[0] for i in ids}
    rng = _lcg(1)
    # D truly +10pp over I
    D = {i: (1.0 if next(rng) < 0.55 else 0.0) for i in ids}
    I = {i: (1.0 if next(rng) < 0.45 else 0.0) for i in ids}
    pt, lo, hi = clustered_bootstrap_ci(D, I, repo, ids)
    disc = sum(1 for i in ids if (D[i] > .5) != (I[i] > .5))
    print(f"synthetic D-I: point={pt:+.3f} ci=[{lo:+.3f},{hi:+.3f}] discordant={disc}")
    print("verdict:", three_verdict(pt, lo, hi, disc))
