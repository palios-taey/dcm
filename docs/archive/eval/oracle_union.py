"""
Oracle-union pre-test (Horizon's cheap decisive check, 2026-06-26).

Runs the four models SOLO (codex/claude/gemini/grok) on a subset and measures
whether their COMBINED solve-set exceeds the best single model. This is the
NECESSARY CONDITION for ANY diversity scheme — generic OR Jesse's identity-matched
KERNEL version — to help: if the four models' errors don't decorrelate, no
coordination can manufacture a benefit, and we report an early honest null instead
of building the 14-arm study.

Key metric — RESCUE RATE: among instances the BEST single model FAILS, the fraction
that at least one OTHER model SOLVES. That is the ceiling on diversity's benefit.
  rescue_rate ~ 0   -> no exploitable complementarity (likely null; stop the build)
  rescue_rate >> 0  -> real headroom; the identity-vs-counterbalanced study is worth it

Grading is the official hidden harness only. A model "solves" an instance if it
resolves on ANY of its k seeds (oracle over seeds — an upper bound on solo skill,
which makes the null conclusion conservative: we credit each model its best shot).
"""
from __future__ import annotations
import json, os, time
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIS = ["codex", "claude", "grok", "gemini"]


def run_oracle(subset_ids: list[str], k: int = 1, concurrency: int = 6, run: str = "oracle_pilot",
               dataset_name: str = "princeton-nlp/SWE-bench_Verified", namespace: str | None = None) -> dict:
    from harness import load_instances, write_predictions, grade, ArmResult
    from solver import solve
    rundir = HERE / "runs" / run
    rundir.mkdir(parents=True, exist_ok=True)
    os.environ["DCM_PROV_DIR"] = str(rundir / "provenance")
    instances = load_instances(subset_ids, dataset_name=dataset_name)
    solved = {cli: {i: False for i in subset_ids} for cli in CLIS}  # solved if ANY seed resolves

    def _log(m):
        line = f"[{int(time.time())}] {m}"; print(line, flush=True)
        (rundir / "progress.log").open("a").write(line + "\n")

    for cli in CLIS:
        for seed in range(k):
            tag = f"{cli}_{seed}"
            pdir = rundir / "patches" / tag
            patches: dict[str, str] = {}
            def cell(iid):
                pp = pdir / f"{iid}.patch"
                if pp.exists():
                    return iid, pp.read_text()
                r = solve(instances[iid], cli)  # solve() retries provider errors internally
                p = r["model_patch"] or ""
                pp.parent.mkdir(parents=True, exist_ok=True)
                if r.get("errored"):
                    # provider failure after retries -> do NOT cache (resume retries), count empty THIS run
                    _log(f"  {tag} {iid} PROVIDER-ERROR after retries (not cached): {r['log'][-120:]}")
                else:
                    pp.write_text(p)  # genuine result (solved or honestly-empty) -> cache
                return iid, p
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                for f in as_completed([ex.submit(cell, i) for i in subset_ids]):
                    iid, p = f.result(); patches[iid] = p
            preds = write_predictions(tag, [ArmResult(i, patches[i]) for i in subset_ids],
                                      rundir / f"preds_{tag}.json")
            rep = grade(preds, f"{run}_{tag}", subset_ids, max_workers=4, cache_level="env",
                        dataset_name=dataset_name, namespace=namespace)
            res = set(rep.get("resolved_ids", []))
            for iid in subset_ids:
                if iid in res:
                    solved[cli][iid] = True
            _log(f"  {tag} resolved {len(res)}/{len(subset_ids)}")

    result = analyze(solved, subset_ids)
    (rundir / "ORACLE_UNION.json").write_text(json.dumps({**result, "solved": solved}, indent=2))
    _log("ORACLE-UNION: " + json.dumps(result["headline"]))
    return result


def analyze(solved: dict, ids: list[str]) -> dict:
    n = len(ids)
    per_model = {cli: sum(solved[cli][i] for i in ids) / n for cli in CLIS}
    best_cli = max(per_model, key=per_model.get)
    best_rate = per_model[best_cli]
    union = {i: any(solved[cli][i] for cli in CLIS) for i in ids}
    union_rate = sum(union.values()) / n
    # rescue: among instances the BEST single model fails, fraction some OTHER model solves
    best_fail = [i for i in ids if not solved[best_cli][i]]
    rescued = [i for i in best_fail if any(solved[cli][i] for cli in CLIS if cli != best_cli)]
    rescue_rate = (len(rescued) / len(best_fail)) if best_fail else 0.0
    # unique solves per model (only this model solves)
    unique = {cli: [i for i in ids if solved[cli][i] and not any(solved[o][i] for o in CLIS if o != cli)]
              for cli in CLIS}
    # pairwise agreement (both solve or both fail) — high agreement = correlated = little diversity
    pair_agree = {f"{a}~{b}": sum((solved[a][i] == solved[b][i]) for i in ids) / n
                  for a, b in combinations(CLIS, 2)}
    return {
        "headline": {
            "N": n, "per_model_rate": per_model, "best_model": best_cli, "best_rate": round(best_rate, 3),
            "union_rate": round(union_rate, 3),
            "union_minus_best": round(union_rate - best_rate, 3),
            "rescue_rate_among_best_failures": round(rescue_rate, 3),
            "n_best_failures": len(best_fail), "n_rescued": len(rescued),
        },
        "unique_solves": {cli: len(unique[cli]) for cli in CLIS},
        "unique_solve_ids": unique,
        "pairwise_agreement": pair_agree,
        # calibrated: a few-instance union gap at high best_rate is SATURATION/noise, not signal.
        # Require real headroom (best not saturated) AND a non-trivial rescue count.
        "verdict_hint": (
            f"SATURATED / no headroom (best={best_rate:.0%}, only {len(best_fail)} misses) -> "
            "cannot detect coordination here; need the HARD subset"
            if best_rate >= 0.85 or len(best_fail) < 6 else
            f"NO exploitable complementarity (union-best={union_rate-best_rate:+.0%}, rescued {len(rescued)}/{len(best_fail)}) -> likely null"
            if len(rescued) < 3 else
            f"complementarity signal (rescued {len(rescued)}/{len(best_fail)} of best-model failures) -> warrants the study; confirm at larger N"),
    }


if __name__ == "__main__":
    import sys
    sub = json.loads((HERE / "oracle_subset.json").read_text())["instance_ids"]
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    conc = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    print(json.dumps(run_oracle(sub, k=k, concurrency=conc)["headline"], indent=2))
