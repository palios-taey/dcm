"""
DCM SWE-bench eval harness — INVARIANT core.

This module holds only the parts that do not depend on the eval DESIGN under
Family review (the bar/N/model-cells may change; these mechanics do not):
  - load the frozen instance subset
  - an Arm protocol: arm(instance) -> unified-diff str (the model_patch)
  - predictions assembly
  - objective grading via the OFFICIAL swebench harness (no self-report)
  - a results ledger

Grading is done ONLY by swebench.harness.run_evaluation against the hidden
tests. No arm reports its own resolved status. Proven end-to-end 2026-06-25
(gold patch -> resolved 1/1, see gold.feasibility_gold.json).
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

HERE = Path(__file__).resolve().parent
DATASET = "princeton-nlp/SWE-bench_Verified"
FROZEN = HERE / "frozen_subset.json"


def load_frozen() -> dict:
    """The pre-registered, seed-frozen subset. Single source of which instances run."""
    return json.loads(FROZEN.read_text())


def load_instances(instance_ids: list[str], dataset_name: str = DATASET) -> dict[str, dict]:
    """Map instance_id -> full SWE-bench row (problem_statement, base_commit, repo, patch, test specs)."""
    from datasets import load_dataset
    ds = load_dataset(dataset_name, split="test")
    rows = {r["instance_id"]: r for r in ds}
    missing = [i for i in instance_ids if i not in rows]
    if missing:
        raise KeyError(f"instance_ids not in {DATASET}: {missing}")
    return {i: rows[i] for i in instance_ids}


class Arm(Protocol):
    """An arm turns an instance into a candidate patch. S/V/I/D differ ONLY here.

    Must return a unified git diff (model_patch) — the empty string means
    'no patch produced' (graded as unresolved, never as an error to hide).
    The arm MUST do all of its own work in throwaway/isolated state; it must
    never touch live fleet DB/Redis/checkouts (today's data-loss lesson).
    """
    name: str
    def __call__(self, instance: dict) -> "ArmResult": ...


@dataclass
class ArmResult:
    instance_id: str
    model_patch: str
    tokens: int = 0          # cost ledger — recorded, NEVER a gate
    wall_clock_s: float = 0.0
    coordination_incidents: int = 0   # D-only: diverged/stalled/lost-work rounds
    notes: str = ""


def write_predictions(arm_name: str, results: list[ArmResult], out: Path) -> Path:
    preds = [{"instance_id": r.instance_id,
              "model_name_or_path": arm_name,
              "model_patch": r.model_patch} for r in results]
    out.write_text(json.dumps(preds, indent=2))
    return out


def grade(predictions_path: Path, run_id: str, instance_ids: list[str],
          max_workers: int = 2, cache_level: str = "instance",
          dataset_name: str = DATASET, namespace: str | None = None) -> dict:
    """Objective grading via the official harness. Returns the report dict.

    cache_level 'instance' keeps per-instance images (faster re-runs, more disk);
    use 'none' when disk-constrained. The report is the ONLY source of resolved-rate.
    For SWE-bench-Live: dataset_name='SWE-bench-Live/SWE-bench-Live', namespace='starryzhang'.
    """
    cmd = [sys.executable, "-m", "swebench.harness.run_evaluation",
           "--predictions_path", str(predictions_path),
           "--run_id", run_id,
           "--instance_ids", *instance_ids,
           "--dataset_name", dataset_name,
           "--max_workers", str(max_workers),
           "--cache_level", cache_level]
    if namespace is not None:
        cmd += ["--namespace", namespace]
    subprocess.run(cmd, check=True, cwd=HERE)
    # report file is "<model_name_or_path>.<run_id>.json" written to cwd
    preds = json.loads(predictions_path.read_text())
    model_name = preds[0]["model_name_or_path"] if preds else "model"
    report = HERE / f"{model_name}.{run_id}.json"
    return json.loads(report.read_text())


@dataclass
class ArmLedger:
    """Per-arm record: the resolved set (from the harness) + the cost ledger."""
    arm_name: str
    run_id: str
    resolved_ids: list[str] = field(default_factory=list)
    unresolved_ids: list[str] = field(default_factory=list)
    error_ids: list[str] = field(default_factory=list)
    empty_patch_ids: list[str] = field(default_factory=list)
    per_instance: dict[str, dict] = field(default_factory=dict)  # id -> {resolved,tokens,wall_clock_s,incidents}
    total_tokens: int = 0
    total_wall_clock_s: float = 0.0

    @property
    def resolved_rate(self) -> float:
        n = len(self.per_instance)
        return len(self.resolved_ids) / n if n else 0.0

    def save(self, out_dir: Path) -> Path:
        p = out_dir / f"ledger_{self.arm_name}.json"
        p.write_text(json.dumps({
            "arm_name": self.arm_name, "run_id": self.run_id,
            "resolved_rate": self.resolved_rate,
            "resolved_ids": self.resolved_ids, "unresolved_ids": self.unresolved_ids,
            "error_ids": self.error_ids, "empty_patch_ids": self.empty_patch_ids,
            "total_tokens": self.total_tokens, "total_wall_clock_s": self.total_wall_clock_s,
            "per_instance": self.per_instance,
        }, indent=2))
        return p


def run_arm(arm: Arm, instance_ids: list[str], out_dir: Path,
            max_workers: int = 2, cache_level: str = "instance") -> ArmLedger:
    """Drive one arm over the frozen instance set and grade objectively.

    Same instances across all arms => paired comparison. The arm's self-reported
    cost goes in the ledger; the resolved set comes ONLY from the harness report.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    instances = load_instances(instance_ids)
    results: list[ArmResult] = []
    for iid in instance_ids:
        t0 = time.time()
        res = arm(instances[iid])
        if not res.wall_clock_s:
            res.wall_clock_s = time.time() - t0
        results.append(res)
        print(f"[{arm.name}] {iid}: patch={'yes' if res.model_patch.strip() else 'EMPTY'} "
              f"tok={res.tokens} t={res.wall_clock_s:.0f}s", flush=True)

    run_id = f"{arm.name}_{int(time.time())}"
    preds_path = write_predictions(arm.name, results, out_dir / f"preds_{arm.name}.json")
    report = grade(preds_path, run_id, instance_ids, max_workers, cache_level)

    led = ArmLedger(arm_name=arm.name, run_id=run_id,
                    resolved_ids=report.get("resolved_ids", []),
                    unresolved_ids=report.get("unresolved_ids", []),
                    error_ids=report.get("error_ids", []),
                    empty_patch_ids=report.get("empty_patch_ids", []))
    by_id = {r.instance_id: r for r in results}
    for iid in instance_ids:
        r = by_id[iid]
        led.per_instance[iid] = {
            "resolved": iid in led.resolved_ids,
            "tokens": r.tokens, "wall_clock_s": r.wall_clock_s,
            "coordination_incidents": r.coordination_incidents,
        }
        led.total_tokens += r.tokens
        led.total_wall_clock_s += r.wall_clock_s
    led.save(out_dir)
    print(f"[{arm.name}] resolved {len(led.resolved_ids)}/{len(instance_ids)} "
          f"= {led.resolved_rate:.1%}  (tokens={led.total_tokens})", flush=True)
    return led
