"""
DCM I/D contrast + role-ablation analysis harness.

Observed:
  - I/D resolved labels come from run_eval ledgers written after official
    SWE-bench grading; this module does not grade patches and does not run CLIs.
  - Fixture ownership comes from sabotage_fixtures.json `owning_role`.
  - Per-role fixture catches must be supplied as an explicit matrix.

Inferred:
  - The legacy fixture owner labels are collapsed onto the current four
    CouncilArm seats using ROLE_ALIASES, matching ROUND2_SYNTHESIS.md section 7.
  - "D>I", "I>D", or "neither" is the sign of the 95% CI around D minus I.

Unknown:
  - Real SWE-bench Live outcomes remain unknown until the operator runs the
    heavy eval. The smoke path below uses only synthetic data.

CLI examples:
  python eval/analyze_ablation.py --smoke
  python eval/analyze_ablation.py --run-dir eval/runs/live_full \
    --role-outputs eval/runs/live_full/role_outputs.json
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from analyze_v2 import SEED_RNG, clustered_bootstrap_ci, discordant_count, three_verdict

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURES = HERE / "sabotage_fixtures.json"
DEFAULT_SURFACE = "live"
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

COUNCIL_ROLES = ("foundation", "ground-runner", "evasive-repair", "scope-blast")
ROLE_ALIASES = {
    "foundation": "foundation",
    "memory-scout": "foundation",
    "git-historian": "foundation",
    "ground-runner": "ground-runner",
    "test-integrity": "ground-runner",
    "dependency-api-reality": "ground-runner",
    "evasive-repair": "evasive-repair",
    "fallback-hunter": "evasive-repair",
    "root-cause-6sigma": "evasive-repair",
    "scope-blast": "scope-blast",
    "scope-sentinel": "scope-blast",
    "blast-shield": "scope-blast",
}
NON_COUNCIL_OWNERS = {
    "provenance": "clerk",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _read_ids(spec: str | None, surface: str = DEFAULT_SURFACE) -> list[str]:
    if spec is None:
        defaults = SURFACE_DEFAULTS[surface]
        return _read_json(defaults["ids_path"])["instance_ids"]
    p = Path(spec)
    if p.exists():
        data = _read_json(p) if p.suffix == ".json" else None
        return data["instance_ids"] if data is not None else p.read_text().split()
    return [s for s in spec.split(",") if s]


def _ledger_dir(run_dir: Path) -> Path:
    ledgers = run_dir / "ledgers"
    return ledgers if ledgers.exists() else run_dir


def load_ledgers(run_dir: Path, arms: tuple[str, ...] = ("I", "D")) -> dict[str, dict]:
    """Load required arm ledgers with no best-effort substitution.

    Observed: each ledger must exist on disk.
    Inferred: none; missing files are fatal.
    Unknown: whether the ledgers are from a complete heavy run unless run_meta
    says so.
    """
    base = _ledger_dir(run_dir)
    ledgers = {}
    for arm in arms:
        path = base / f"ledger_{arm}.json"
        if not path.exists():
            raise FileNotFoundError(f"missing required ledger: {path}")
        ledgers[arm] = _read_json(path)
    return ledgers


def _seed_resolved(ledger: dict, arm: str) -> dict[str, list[bool]]:
    if "seed_resolved" not in ledger:
        raise KeyError(f"ledger_{arm} lacks seed_resolved; cannot compute per-instance resolved-rate")
    out = {}
    for iid, values in ledger["seed_resolved"].items():
        if not isinstance(values, list) or not all(isinstance(v, bool) for v in values):
            raise TypeError(f"ledger_{arm} seed_resolved[{iid!r}] must be list[bool]")
        if not values:
            raise ValueError(f"ledger_{arm} seed_resolved[{iid!r}] is empty")
        out[iid] = values
    return out


def _rate_by_instance(seed_resolved: dict[str, list[bool]]) -> dict[str, float]:
    return {iid: sum(values) / len(values) for iid, values in seed_resolved.items()}


def _ids_from_ledgers(ledgers: dict[str, dict]) -> list[str]:
    ids = list(_seed_resolved(ledgers["I"], "I"))
    expected = set(ids)
    for arm, ledger in ledgers.items():
        got = set(_seed_resolved(ledger, arm))
        if got != expected:
            raise ValueError(f"ledger_{arm} instance ids differ from ledger_I")
    return ids


def _repo_map_from_sources(
    run_dir: Path,
    ids: list[str],
    ledgers: dict[str, dict],
    repo_map_path: Path | None,
    repo_map: dict[str, str] | None,
    dataset_name: str | None,
) -> tuple[dict[str, str], str]:
    if repo_map is not None:
        source = "argument"
        mapping = repo_map
    elif repo_map_path is not None:
        raw = _read_json(repo_map_path)
        mapping = raw.get("repo", raw)
        source = str(repo_map_path)
    elif (run_dir / "run_meta.json").exists():
        raw = _read_json(run_dir / "run_meta.json")
        mapping = raw.get("repo", {})
        source = str(run_dir / "run_meta.json")
    else:
        mapping = {}
        source = ""
        for arm, ledger in ledgers.items():
            if ledger.get("repo"):
                mapping = ledger["repo"]
                source = f"ledger_{arm}.json"
                break
        if not mapping and dataset_name:
            from harness import load_instances

            rows = load_instances(ids, dataset_name=dataset_name)
            mapping = {iid: rows[iid]["repo"] for iid in ids}
            source = f"dataset:{dataset_name}"
    missing = [iid for iid in ids if iid not in mapping or not mapping[iid]]
    if missing:
        raise KeyError(f"repo map missing {len(missing)} ids: {missing[:5]}")
    return {iid: mapping[iid] for iid in ids}, source


def compute_id_contrast(
    ledgers: dict[str, dict],
    ids: list[str],
    repo: dict[str, str],
    bootstrap_iters: int = 5000,
    seed: int = SEED_RNG,
) -> dict:
    """Compute the primary D minus I paired contrast.

    Observed: I and D seed vectors are read directly from ledgers.
    Inferred: the directional label summarizes the bootstrap CI against zero.
    Unknown: external validity beyond the supplied ids.
    """
    if not ids:
        raise ValueError("ids must be non-empty")
    I_seeds = _seed_resolved(ledgers["I"], "I")
    D_seeds = _seed_resolved(ledgers["D"], "D")
    missing = [iid for iid in ids if iid not in I_seeds or iid not in D_seeds]
    if missing:
        raise KeyError(f"I/D ledgers missing ids: {missing[:5]}")
    I_rate = _rate_by_instance(I_seeds)
    D_rate = _rate_by_instance(D_seeds)
    point, lo, hi = clustered_bootstrap_ci(D_rate, I_rate, repo, ids, iters=bootstrap_iters, seed=seed)
    discordant = discordant_count(D_seeds, I_seeds, ids)
    if lo > 0:
        direction = "D>I"
    elif hi < 0:
        direction = "I>D"
    else:
        direction = "neither"
    return {
        "question": "Does real-time peer visibility help (D>I), hurt (I>D), or neither?",
        "N": len(ids),
        "bootstrap": {"cluster": "repo", "iters": bootstrap_iters, "seed": seed},
        "per_instance_resolved_rate": {
            iid: {"I_peer_blind": I_rate[iid], "D_peer_visible": D_rate[iid], "repo": repo[iid]}
            for iid in ids
        },
        "mean_resolved_rate": {
            "I_peer_blind": sum(I_rate[iid] for iid in ids) / len(ids),
            "D_peer_visible": sum(D_rate[iid] for iid in ids) / len(ids),
        },
        "paired_delta_D_minus_I": point,
        "ci95_D_minus_I": [lo, hi],
        "direction": direction,
        "discordant_majority_count": discordant,
        "material_verdict_v2": three_verdict(point, lo, hi, discordant),
    }


def _clean_marker(fixture: dict) -> bool:
    owner_clean = fixture["owning_role"].startswith("CLEAN:")
    mode_clean = fixture.get("target_mode", "").upper().startswith("CLEAN")
    verdict_clean = fixture.get("expected_verdict", "").upper().startswith("PASS:")
    markers = (owner_clean, mode_clean, verdict_clean)
    if any(markers) and not all(markers):
        raise ValueError(f"fixture {fixture['id']} has inconsistent CLEAN markers: {markers}")
    return owner_clean


def _owner_base(raw: str) -> str:
    return raw.split(":", 1)[1] if raw.startswith("CLEAN:") else raw


def _normalize_owner(raw: str) -> tuple[str, bool]:
    base = _owner_base(raw)
    if base in ROLE_ALIASES:
        return ROLE_ALIASES[base], True
    if base in NON_COUNCIL_OWNERS:
        return NON_COUNCIL_OWNERS[base], False
    raise KeyError(f"unknown fixture owning_role {raw!r}")


def _normalize_output_role(raw: str) -> str:
    base = _owner_base(raw)
    if base not in ROLE_ALIASES:
        raise KeyError(f"unknown role output key {raw!r}")
    return ROLE_ALIASES[base]


def load_fixtures(path: Path = DEFAULT_FIXTURES) -> list[dict]:
    """Load sabotage fixtures and normalize their owner metadata.

    Observed: owner labels are read from `owning_role`.
    Inferred: legacy owner labels are fused to the four current council roles.
    Unknown: whether a future fixture belongs to a new seat; unknown labels fail.
    """
    raw = _read_json(path)
    fixtures = raw["fixtures"] if isinstance(raw, dict) and "fixtures" in raw else raw
    out = []
    seen = set()
    for fixture in fixtures:
        fid = fixture["id"]
        if fid in seen:
            raise ValueError(f"duplicate fixture id: {fid}")
        seen.add(fid)
        role, council_owned = _normalize_owner(fixture["owning_role"])
        out.append({
            **fixture,
            "clean_control": _clean_marker(fixture),
            "normalized_owner": role,
            "council_owned": council_owned,
        })
    return out


def _flag_from_cell(cell: Any, fixture_id: str, role: str) -> bool:
    if isinstance(cell, bool):
        return cell
    if isinstance(cell, dict):
        for key in ("caught", "flagged", "blocked"):
            if key in cell:
                if not isinstance(cell[key], bool):
                    raise TypeError(f"{fixture_id}/{role}/{key} must be bool")
                return cell[key]
        if "verdict" in cell:
            verdict = str(cell["verdict"]).strip().upper()
            if verdict in {"PASS", "APPROVE", "NONE"} or verdict.startswith("PASS:"):
                return False
            if verdict in {"BLOCK", "UNVERIFIED"} or verdict.startswith(("BLOCK:", "UNVERIFIED:")):
                return True
            raise ValueError(f"{fixture_id}/{role} has unparseable verdict {cell['verdict']!r}")
    raise TypeError(f"{fixture_id}/{role} must be bool or dict with caught/flagged/blocked/verdict")


def load_role_outputs(path: Path, fixture_ids: set[str]) -> dict[str, dict[str, bool]]:
    """Load explicit per-fixture, per-role catch outputs.

    Observed: every fixture must have every council role represented.
    Inferred: verdict strings starting PASS are non-flags; BLOCK/UNVERIFIED are flags.
    Unknown: no absent role is treated as negative; absence is fatal.
    """
    raw = _read_json(path)
    matrix = raw.get("role_outputs", raw)
    if not isinstance(matrix, dict):
        raise TypeError("role outputs must be a dict or {'role_outputs': dict}")
    missing_fixtures = sorted(fixture_ids - set(matrix))
    extra_fixtures = sorted(set(matrix) - fixture_ids)
    if missing_fixtures:
        raise KeyError(f"role outputs missing fixtures: {missing_fixtures[:5]}")
    if extra_fixtures:
        raise KeyError(f"role outputs contain unknown fixtures: {extra_fixtures[:5]}")
    out = {}
    for fid, role_cells in matrix.items():
        if not isinstance(role_cells, dict):
            raise TypeError(f"role_outputs[{fid!r}] must be a role dict")
        normalized = {}
        for raw_role, cell in role_cells.items():
            role = _normalize_output_role(raw_role)
            flag = _flag_from_cell(cell, fid, role)
            if role in normalized and normalized[role] != flag:
                raise ValueError(f"{fid} has conflicting outputs for role alias {role}")
            normalized[role] = flag
        missing_roles = [role for role in COUNCIL_ROLES if role not in normalized]
        if missing_roles:
            raise KeyError(f"{fid} missing role outputs: {missing_roles}")
        out[fid] = {role: normalized[role] for role in COUNCIL_ROLES}
    return out


def compute_role_ablation(fixtures: list[dict], role_outputs: dict[str, dict[str, bool]]) -> dict:
    """Compute unique-catch loss and clean-control false positives.

    Observed: catches are the explicit boolean flags in role_outputs.
    Inferred: dropping a role loses a fixture iff that role was its only flagger.
    Unknown: fixture recall inside a mode; this measures the supplied fixture set.
    """
    fixture_ids = {fixture["id"] for fixture in fixtures}
    if set(role_outputs) != fixture_ids:
        raise KeyError("role output fixture ids must exactly match fixtures")
    by_id = {fixture["id"]: fixture for fixture in fixtures}
    sabotage = [fixture for fixture in fixtures if not fixture["clean_control"]]
    clean = [fixture for fixture in fixtures if fixture["clean_control"] and fixture["council_owned"]]
    caught_by = {
        fid: [role for role in COUNCIL_ROLES if role_outputs[fid][role]]
        for fid in fixture_ids
    }
    roles = {}
    for role in COUNCIL_ROLES:
        lost_all = [
            fixture["id"] for fixture in sabotage
            if role in caught_by[fixture["id"]]
            and not any(other != role for other in caught_by[fixture["id"]])
        ]
        owned_bad = [
            fixture["id"] for fixture in sabotage
            if fixture["council_owned"] and fixture["normalized_owner"] == role
        ]
        owned_unique = [
            fid for fid in lost_all
            if by_id[fid]["council_owned"] and by_id[fid]["normalized_owner"] == role
        ]
        clean_false_positives = [
            fixture["id"] for fixture in clean
            if role_outputs[fixture["id"]][role]
        ]
        owned_clean = [
            fixture["id"] for fixture in clean
            if fixture["normalized_owner"] == role
        ]
        owned_clean_false_positives = [
            fid for fid in owned_clean if role_outputs[fid][role]
        ]
        roles[role] = {
            "owned_sabotage_fixture_ids": owned_bad,
            "lost_catches_if_dropped": lost_all,
            "owned_unique_catches": owned_unique,
            "seat_earned": bool(owned_unique),
            "owned_missed": [fid for fid in owned_bad if role not in caught_by[fid]],
            "clean_false_positive_ids": clean_false_positives,
            "clean_false_positive_rate_all_controls": (
                len(clean_false_positives) / len(clean) if clean else None
            ),
            "owned_clean_control_ids": owned_clean,
            "owned_clean_false_positive_ids": owned_clean_false_positives,
            "owned_clean_false_positive_rate": (
                len(owned_clean_false_positives) / len(owned_clean) if owned_clean else None
            ),
        }
    non_council = [
        fixture["id"] for fixture in sabotage
        if not fixture["council_owned"]
    ]
    return {
        "rule": "A council role earns its seat iff owned_unique_catches is non-empty.",
        "roles": roles,
        "clean_control_ok": all(not role_outputs[fixture["id"]][role]
                                for fixture in clean for role in COUNCIL_ROLES),
        "total_clean_false_positive_count": sum(
            1 for fixture in clean for role in COUNCIL_ROLES
            if role_outputs[fixture["id"]][role]
        ),
        "fixture_counts": {
            "sabotage_total": len(sabotage),
            "sabotage_council_owned": sum(1 for fixture in sabotage if fixture["council_owned"]),
            "clean_controls": len(clean),
            "non_council_sabotage_excluded_from_seat_earning": len(non_council),
        },
        "non_council_sabotage_fixture_ids": non_council,
        "caught_by": caught_by,
        "role_aliases": ROLE_ALIASES,
        "non_council_owners": NON_COUNCIL_OWNERS,
    }


def analyze_run(
    run_dir: Path,
    ids: list[str] | None = None,
    repo_map: dict[str, str] | None = None,
    repo_map_path: Path | None = None,
    fixtures_path: Path = DEFAULT_FIXTURES,
    role_outputs_path: Path | None = None,
    surface: str = DEFAULT_SURFACE,
    dataset_name: str | None = None,
    bootstrap_iters: int = 5000,
    contrast_only: bool = False,
) -> dict:
    """Analyze a completed eval run without running the heavy eval.

    Observed: reads ledgers, metadata, fixtures, and explicit role outputs.
    Inferred: Live is the default surface because oracle_union found headroom there.
    Unknown: ablation is unknown unless role_outputs_path is provided.
    """
    ledgers = load_ledgers(run_dir)
    ids_source = "argument"
    if ids is None:
        if (run_dir / "run_meta.json").exists():
            meta = _read_json(run_dir / "run_meta.json")
            ids = meta["instance_ids"]
            ids_source = str(run_dir / "run_meta.json")
        else:
            ids = _ids_from_ledgers(ledgers)
            ids_source = "ledger_I.seed_resolved keys"
    if dataset_name is None:
        dataset_name = SURFACE_DEFAULTS[surface]["dataset_name"]
    repo, repo_source = _repo_map_from_sources(
        run_dir, ids, ledgers, repo_map_path, repo_map, dataset_name
    )
    out = {
        "target_surface": {
            "default": DEFAULT_SURFACE,
            "selected": surface,
            "dataset_name": dataset_name,
            "selection": SURFACE_DEFAULTS[surface]["selection"],
        },
        "truth_register": {
            "observed": [
                "I/D resolved labels read from run ledgers.",
                f"Instance ids source: {ids_source}.",
                f"Repo clusters source: {repo_source}.",
            ],
            "inferred": [
                "Legacy sabotage owning_role labels are fused into current CouncilArm roles via ROLE_ALIASES.",
                "D>I/I>D/neither is inferred from whether the D-I CI is wholly above, below, or across zero.",
            ],
            "unknown": [
                "No real SWE-bench Live result exists until the heavy eval is run.",
            ],
        },
        "id_contrast": compute_id_contrast(ledgers, ids, repo, bootstrap_iters=bootstrap_iters),
    }
    if contrast_only:
        out["role_ablation"] = {"status": "not_run", "reason": "contrast_only=True"}
        return out
    if role_outputs_path is None:
        raise ValueError("role_outputs_path is required unless contrast_only=True")
    fixtures = load_fixtures(fixtures_path)
    role_outputs = load_role_outputs(role_outputs_path, {fixture["id"] for fixture in fixtures})
    out["role_ablation"] = compute_role_ablation(fixtures, role_outputs)
    return out


def smoke() -> dict:
    """Run a synthetic structural smoke; never touches docker, datasets, or CLIs.

    Observed: temp ledgers and fixture-output JSON are created locally.
    Inferred: function correctness is checked on known answers.
    Unknown: no real model or benchmark performance is measured.
    """
    ids = ["repo-a__1", "repo-a__2", "repo-b__1", "repo-b__2"]
    repo = {"repo-a__1": "repo/a", "repo-a__2": "repo/a", "repo-b__1": "repo/b", "repo-b__2": "repo/b"}
    fixtures = {
        "fixtures": [
            {"id": "sab-foundation", "owning_role": "memory-scout", "target_mode": "bad",
             "known_bad_input": "", "expected_verdict": "BLOCK: foundation", "source": "smoke"},
            {"id": "sab-ground", "owning_role": "ground-runner", "target_mode": "bad",
             "known_bad_input": "", "expected_verdict": "BLOCK: ground", "source": "smoke"},
            {"id": "sab-evasive", "owning_role": "fallback-hunter", "target_mode": "bad",
             "known_bad_input": "", "expected_verdict": "BLOCK: evasive", "source": "smoke"},
            {"id": "sab-shared", "owning_role": "root-cause-6sigma", "target_mode": "bad",
             "known_bad_input": "", "expected_verdict": "BLOCK: shared", "source": "smoke"},
            {"id": "sab-scope", "owning_role": "blast-shield", "target_mode": "bad",
             "known_bad_input": "", "expected_verdict": "BLOCK: scope", "source": "smoke"},
            {"id": "clean-foundation", "owning_role": "CLEAN:memory-scout", "target_mode": "CLEAN",
             "known_bad_input": "", "expected_verdict": "PASS: clean foundation", "source": "smoke"},
            {"id": "clean-ground", "owning_role": "CLEAN:ground-runner", "target_mode": "CLEAN",
             "known_bad_input": "", "expected_verdict": "PASS: clean ground", "source": "smoke"},
            {"id": "clean-evasive", "owning_role": "CLEAN:fallback-hunter", "target_mode": "CLEAN",
             "known_bad_input": "", "expected_verdict": "PASS: clean evasive", "source": "smoke"},
            {"id": "clean-scope", "owning_role": "CLEAN:blast-shield", "target_mode": "CLEAN",
             "known_bad_input": "", "expected_verdict": "PASS: clean scope", "source": "smoke"},
        ]
    }
    all_false = {role: False for role in COUNCIL_ROLES}
    role_outputs = {"role_outputs": {}}
    for fixture in fixtures["fixtures"]:
        role_outputs["role_outputs"][fixture["id"]] = dict(all_false)
    role_outputs["role_outputs"]["sab-foundation"]["foundation"] = True
    role_outputs["role_outputs"]["sab-ground"]["ground-runner"] = True
    role_outputs["role_outputs"]["sab-evasive"]["evasive-repair"] = True
    role_outputs["role_outputs"]["sab-shared"]["evasive-repair"] = True
    role_outputs["role_outputs"]["sab-shared"]["ground-runner"] = True
    role_outputs["role_outputs"]["sab-scope"]["scope-blast"] = True
    role_outputs["role_outputs"]["clean-ground"]["ground-runner"] = True

    with tempfile.TemporaryDirectory(prefix="dcm_ablation_smoke_") as tmp:
        run_dir = Path(tmp)
        (run_dir / "ledgers").mkdir()
        (run_dir / "ledgers" / "ledger_I.json").write_text(json.dumps({
            "arm_name": "I",
            "seed_resolved": {
                "repo-a__1": [True],
                "repo-a__2": [False],
                "repo-b__1": [False],
                "repo-b__2": [False],
            },
            "repo": repo,
        }))
        (run_dir / "ledgers" / "ledger_D.json").write_text(json.dumps({
            "arm_name": "D",
            "seed_resolved": {
                "repo-a__1": [True],
                "repo-a__2": [True],
                "repo-b__1": [False],
                "repo-b__2": [True],
            },
            "repo": repo,
        }))
        fixtures_path = run_dir / "fixtures.json"
        outputs_path = run_dir / "role_outputs.json"
        fixtures_path.write_text(json.dumps(fixtures))
        outputs_path.write_text(json.dumps(role_outputs))
        result = analyze_run(
            run_dir,
            ids=ids,
            repo_map=repo,
            fixtures_path=fixtures_path,
            role_outputs_path=outputs_path,
            surface="live",
            bootstrap_iters=200,
        )
    contrast = result["id_contrast"]
    ablation = result["role_ablation"]
    assert contrast["direction"] == "D>I"
    assert contrast["paired_delta_D_minus_I"] == 0.5
    assert ablation["roles"]["foundation"]["owned_unique_catches"] == ["sab-foundation"]
    assert ablation["roles"]["ground-runner"]["owned_unique_catches"] == ["sab-ground"]
    assert ablation["roles"]["evasive-repair"]["owned_unique_catches"] == ["sab-evasive"]
    assert "sab-shared" not in ablation["roles"]["evasive-repair"]["lost_catches_if_dropped"]
    assert ablation["roles"]["scope-blast"]["owned_unique_catches"] == ["sab-scope"]
    assert ablation["roles"]["ground-runner"]["clean_false_positive_ids"] == ["clean-ground"]
    return {
        "smoke": "PASS",
        "id_direction": contrast["direction"],
        "delta_D_minus_I": contrast["paired_delta_D_minus_I"],
        "seat_earned": {role: ablation["roles"][role]["seat_earned"] for role in COUNCIL_ROLES},
        "ground_runner_clean_fp_ids": ablation["roles"]["ground-runner"]["clean_false_positive_ids"],
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Analyze DCM I/D contrast and per-role ablation.")
    ap.add_argument("--smoke", action="store_true", help="run synthetic structural smoke only")
    ap.add_argument("--run-dir", type=Path, help="eval run dir, e.g. eval/runs/live_full")
    ap.add_argument("--ids", help="comma ids, .txt ids, or .json with instance_ids")
    ap.add_argument("--repo-map", type=Path, help="json mapping iid->repo or {'repo': mapping}")
    ap.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    ap.add_argument("--role-outputs", type=Path, help="explicit fixture id -> role -> verdict/flag matrix")
    ap.add_argument("--surface", choices=sorted(SURFACE_DEFAULTS), default=DEFAULT_SURFACE)
    ap.add_argument("--dataset-name", help="override dataset used only to derive repo clusters if metadata is absent")
    ap.add_argument("--bootstrap-iters", type=int, default=5000)
    ap.add_argument("--contrast-only", action="store_true", help="skip role ablation")
    ap.add_argument("--out", type=Path, help="write result json here; default <run-dir>/ANALYZE_ABLATION.json")
    args = ap.parse_args(argv)

    if args.smoke:
        print(json.dumps(smoke(), indent=2))
        return
    if args.run_dir is None:
        ap.error("--run-dir is required unless --smoke is set")
    ids = _read_ids(args.ids, surface=args.surface) if args.ids else None
    role_outputs = args.role_outputs
    if role_outputs is None and not args.contrast_only:
        default_role_outputs = args.run_dir / "role_outputs.json"
        if default_role_outputs.exists():
            role_outputs = default_role_outputs
        else:
            ap.error("--role-outputs is required unless --contrast-only is set")
    result = analyze_run(
        args.run_dir,
        ids=ids,
        repo_map_path=args.repo_map,
        fixtures_path=args.fixtures,
        role_outputs_path=role_outputs,
        surface=args.surface,
        dataset_name=args.dataset_name,
        bootstrap_iters=args.bootstrap_iters,
        contrast_only=args.contrast_only,
    )
    out_path = args.out or (args.run_dir / "ANALYZE_ABLATION.json")
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps({"wrote": str(out_path), "direction": result["id_contrast"]["direction"]}, indent=2))


if __name__ == "__main__":
    main()
