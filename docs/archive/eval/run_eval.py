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
  python run_eval.py --run live_full                    (uses live_subset.json)
  python run_eval.py --run live_full --analyze-ablation --role-outputs runs/live_full/role_outputs.json
Outputs under runs/<run>/:
  patches/<arm>_<seed>/<iid>.patch     cached patches (resume source)
  provenance/prov_<arm>_<iid>.json     per-arm internals (from arms.py)
  ledgers/ledger_<arm>.json            analyze_v2 input (seed_resolved + cost)
  run_meta.json                         surface/dataset/ids/repo clusters for analysis
  progress.log                          human-readable running log

Default target surface is SWE-bench Live via live_subset.json (the oracle_union
headroom benchmark). Use --surface verified only for the saturated control.
"""
from __future__ import annotations
import argparse, json, os, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
SURFACE_DEFAULTS = {
    "live": {
        "ids_path": HERE / "live_subset.json",
        "dataset_name": "SWE-bench-Live/SWE-bench-Live",
        "namespace": "starryzhang",
        "selection": "oracle_union headroom benchmark; fresh/uncontaminated default",
    },
    "verified": {
        "ids_path": HERE / "frozen_subset_v2.json",
        "dataset_name": "princeton-nlp/SWE-bench_Verified",
        "namespace": None,
        "selection": "saturated control surface; not the default",
    },
}


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


def run(run_name: str, instance_ids: list[str], k: int, k_b: int, concurrency: int = 1,
        dataset_name: str = SURFACE_DEFAULTS["live"]["dataset_name"],
        namespace: str | None = SURFACE_DEFAULTS["live"]["namespace"],
        surface: str = "live") -> Path:
    from harness import load_instances, write_predictions, grade, ArmResult
    run_dir = HERE / "runs" / run_name
    (run_dir / "ledgers").mkdir(parents=True, exist_ok=True)
    os.environ["DCM_PROV_DIR"] = str(run_dir / "provenance")
    instances = load_instances(instance_ids, dataset_name=dataset_name)
    repo = {}
    for iid in instance_ids:
        if "repo" not in instances[iid] or not instances[iid]["repo"]:
            raise KeyError(f"instance {iid} lacks repo; cannot build repo-clustered analysis")
        repo[iid] = instances[iid]["repo"]
    (run_dir / "run_meta.json").write_text(json.dumps({
        "run": run_name,
        "surface": surface,
        "dataset_name": dataset_name,
        "namespace": namespace,
        "selection": SURFACE_DEFAULTS.get(surface, {}).get("selection", "custom"),
        "instance_ids": instance_ids,
        "repo": repo,
    }, indent=2))
    arms = _arms(k_b)
    _log(run_dir, f"START run={run_name} surface={surface} dataset={dataset_name} N={len(instance_ids)} arms={list(arms)} k={k} k_b={k_b} conc={concurrency}")

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
            report = grade(preds, f"{run_name}_{tag}", instance_ids, max_workers=4, cache_level="env",
                           dataset_name=dataset_name, namespace=namespace)
            resolved = set(report.get("resolved_ids", []))
            for iid in instance_ids:
                seed_resolved[iid].append(iid in resolved)
            _log(run_dir, f"  {tag} GRADED resolved={len(resolved)}/{len(instance_ids)}")
        # write ledger in analyze_v2 shape
        led = {"arm_name": arm_name,
               "surface": surface,
               "dataset_name": dataset_name,
               "namespace": namespace,
               "repo": repo,
               "seed_resolved": seed_resolved,
               "per_instance": {i: {"resolved": sum(seed_resolved[i]) > k / 2,
                                    "seed_resolved": seed_resolved[i],
                                    "repo": repo[i]} for i in instance_ids},
               "total_tokens": 0}  # codex-exec does not expose tokens; wall-clock tracked in progress.log
        (run_dir / "ledgers" / f"ledger_{arm_name}.json").write_text(json.dumps(led, indent=2))
        rate = sum(1 for i in instance_ids if sum(seed_resolved[i]) > k / 2) / len(instance_ids)
        _log(run_dir, f"DONE arm={arm_name} majority-resolved-rate={rate:.1%}")
    _log(run_dir, f"RUN COMPLETE run={run_name}")
    return run_dir


def _parse_ids(spec: str) -> list[str]:
    p = Path(spec)
    if p.exists():
        return json.loads(p.read_text())["instance_ids"] if spec.endswith(".json") else p.read_text().split()
    return [s for s in spec.split(",") if s]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--surface", choices=sorted(SURFACE_DEFAULTS), default="live",
                    help="default=live (SWE-bench Live headroom surface); verified is saturated control")
    ap.add_argument("--ids", help="comma list, a .txt of ids, or a .json with instance_ids; default=surface subset")
    ap.add_argument("--dataset_name", help="override dataset name; defaults from --surface")
    ap.add_argument("--namespace", help="override swebench namespace; use NONE for no namespace")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--k_b", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--analyze-ablation", action="store_true",
                    help="after the real run, write ANALYZE_ABLATION.json from produced ledgers")
    ap.add_argument("--role-outputs", help="fixture role-output matrix for ablation analysis")
    ap.add_argument("--contrast-only", action="store_true",
                    help="with --analyze-ablation, skip fixture ablation")
    ap.add_argument("--analysis-out", help="analysis output path")
    ap.add_argument("--bootstrap-iters", type=int, default=5000)
    a = ap.parse_args()
    defaults = SURFACE_DEFAULTS[a.surface]
    ids = _parse_ids(a.ids) if a.ids else json.loads(defaults["ids_path"].read_text())["instance_ids"]
    dataset_name = a.dataset_name or defaults["dataset_name"]
    namespace = defaults["namespace"] if a.namespace is None else (None if a.namespace == "NONE" else a.namespace)
    run_dir = run(a.run, ids, a.k, a.k_b, a.concurrency,
                  dataset_name=dataset_name, namespace=namespace, surface=a.surface)
    if a.analyze_ablation:
        from analyze_ablation import analyze_run
        role_outputs = Path(a.role_outputs) if a.role_outputs else None
        if role_outputs is None and not a.contrast_only:
            raise SystemExit("--role-outputs is required with --analyze-ablation unless --contrast-only is set")
        result = analyze_run(run_dir, ids=ids, role_outputs_path=role_outputs, surface=a.surface,
                             dataset_name=dataset_name, bootstrap_iters=a.bootstrap_iters,
                             contrast_only=a.contrast_only)
        out = Path(a.analysis_out) if a.analysis_out else run_dir / "ANALYZE_ABLATION.json"
        out.write_text(json.dumps(result, indent=2))
        print(f"[analysis] wrote {out}", flush=True)
