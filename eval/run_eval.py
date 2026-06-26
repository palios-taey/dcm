"""
DCM eval RUN DRIVER (Level 1, homogeneous codex) — resumable + failure-tolerant.

Runs each arm × k seeds over the frozen instance set, caching every produced
patch to disk so a crash / rate-limit / kill never loses completed solving work
(resume re-reads the cache and skips done cells). Grading is the official hidden
harness, done per (arm, seed) over all instances; re-grading is idempotent.

Failure discipline (ITT): if an arm errors on an instance, that cell is recorded
as an empty patch (-> graded unresolved) with the error noted, NOT dropped and
NOT retried differently than any other arm. No cell is silently skipped.

Usage:
  python run_eval.py --run pilot   --ids <comma|file>   [--k 3] [--concurrency 1]
  python run_eval.py --run full                          (uses frozen_subset_v2.json minus excluded)
Outputs under runs/<run>/:
  patches/<arm>_<seed>/<iid>.patch     cached patches (resume source)
  provenance/prov_<arm>_<iid>.json     per-arm internals (from arms.py)
  ledgers/ledger_<arm>.json            analyze_v2 input (seed_resolved + cost)
  progress.log                          human-readable running log
"""
from __future__ import annotations
import argparse, json, os, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _log(run_dir: Path, msg: str):
    line = f"[{int(time.time())}] {msg}"
    print(line, flush=True)
    with open(run_dir / "progress.log", "a") as f:
        f.write(line + "\n")


def _arms(k_b: int):
    from arms import SArm, BoNArm, CouncilArm
    return {"S": SArm(), "BoN": BoNArm(k_b=k_b), "I": CouncilArm(False), "D": CouncilArm(True)}


def _solve_cell(arm, instance: dict, patch_path: Path, run_dir: Path) -> str:
    """Produce + cache one (arm,seed,instance) patch. ITT: error -> empty patch, recorded."""
    if patch_path.exists():
        return patch_path.read_text()
    try:
        res = arm(instance)
        patch = res.model_patch or ""
    except Exception as e:
        _log(run_dir, f"  !! {arm.name} {instance['instance_id']} ERRORED (ITT->empty): {e}")
        (patch_path.with_suffix(".error")).write_text(traceback.format_exc())
        patch = ""
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(patch)
    return patch


def run(run_name: str, instance_ids: list[str], k: int, k_b: int, concurrency: int = 1):
    from harness import load_instances, write_predictions, grade, ArmResult
    run_dir = HERE / "runs" / run_name
    (run_dir / "ledgers").mkdir(parents=True, exist_ok=True)
    os.environ["DCM_PROV_DIR"] = str(run_dir / "provenance")
    instances = load_instances(instance_ids)
    arms = _arms(k_b)
    _log(run_dir, f"START run={run_name} N={len(instance_ids)} arms={list(arms)} k={k} k_b={k_b} conc={concurrency}")

    for arm_name, arm in arms.items():
        seed_resolved: dict[str, list[bool]] = {i: [] for i in instance_ids}
        for seed in range(k):
            tag = f"{arm_name}_{seed}"
            pdir = run_dir / "patches" / tag
            # solve all instances for this (arm,seed) concurrently (codex calls are subprocesses);
            # each cell is cached to disk so a crash/kill resumes without re-solving done cells.
            patches: dict[str, str] = {}
            def _cell(iid):
                p = _solve_cell(arm, instances[iid], pdir / f"{iid}.patch", run_dir)
                _log(run_dir, f"  {tag} {iid}: patch={'yes' if p.strip() else 'EMPTY'}")
                return iid, p
            with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                for fut in as_completed([ex.submit(_cell, i) for i in instance_ids]):
                    iid, p = fut.result()
                    patches[iid] = p
            results = [ArmResult(instance_id=i, model_patch=patches[i]) for i in instance_ids]
            preds = write_predictions(tag, results, run_dir / f"preds_{tag}.json")
            report = grade(preds, f"{run_name}_{tag}", instance_ids, max_workers=4, cache_level="env")
            resolved = set(report.get("resolved_ids", []))
            for iid in instance_ids:
                seed_resolved[iid].append(iid in resolved)
            _log(run_dir, f"  {tag} GRADED resolved={len(resolved)}/{len(instance_ids)}")
        # write ledger in analyze_v2 shape
        led = {"arm_name": arm_name,
               "seed_resolved": seed_resolved,
               "per_instance": {i: {"resolved": sum(seed_resolved[i]) > k / 2,
                                    "seed_resolved": seed_resolved[i]} for i in instance_ids},
               "total_tokens": 0}  # codex-exec does not expose tokens; wall-clock tracked in progress.log
        (run_dir / "ledgers" / f"ledger_{arm_name}.json").write_text(json.dumps(led, indent=2))
        rate = sum(1 for i in instance_ids if sum(seed_resolved[i]) > k / 2) / len(instance_ids)
        _log(run_dir, f"DONE arm={arm_name} majority-resolved-rate={rate:.1%}")
    _log(run_dir, f"RUN COMPLETE run={run_name}")


def _parse_ids(spec: str) -> list[str]:
    p = Path(spec)
    if p.exists():
        return json.loads(p.read_text())["instance_ids"] if spec.endswith(".json") else p.read_text().split()
    return [s for s in spec.split(",") if s]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--ids", help="comma list, a .txt of ids, or a .json with instance_ids; default=frozen_subset_v2")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--k_b", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=1)
    a = ap.parse_args()
    ids = _parse_ids(a.ids) if a.ids else json.loads((HERE / "frozen_subset_v2.json").read_text())["instance_ids"]
    run(a.run, ids, a.k, a.k_b, a.concurrency)
