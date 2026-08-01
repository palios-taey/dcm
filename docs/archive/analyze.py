"""
DCM SWE-bench eval — verdict computation (INVARIANT machinery).

Consumes the per-arm ledgers (resolved sets from the OFFICIAL harness) and
applies the pre-registered decision rule. The margin / test are parameters so
the Family's design review can tune them WITHOUT changing this code — the
verdict is mechanical, never a judgment call.

Decision rule (default, pre-registered; see dcm_swebench_eval_DESIGN.md):
  DCM justified  <=>  r(D)-r(V) >= +MARGIN  AND  McNemar p < ALPHA
                       AND  harmful_flips(D) <= harmful_flips(V)
  else -> archive the council; the value is the verifier/gate.
"""
from __future__ import annotations
import json, math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_ledger(arm: str, d: Path) -> dict:
    return json.loads((d / f"ledger_{arm}.json").read_text())


def _resolved_vec(ledger: dict, instance_ids: list[str]) -> dict[str, bool]:
    return {i: ledger["per_instance"][i]["resolved"] for i in instance_ids}


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value on discordant pairs (b,c).

    b = #(A solved, B failed), c = #(A failed, B solved). Under H0 each
    discordant pair is a fair coin; p = 2*P(X <= min(b,c)), X~Binom(b+c, .5).
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # cumulative binomial tail
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def harmful_flips(base: dict[str, bool], arm: dict[str, bool]) -> list[str]:
    """Instances the base (S) resolved that the arm BROKE (correct -> wrong)."""
    return [i for i in base if base[i] and not arm.get(i, False)]


def pairwise(a_name: str, a: dict[str, bool], b_name: str, b: dict[str, bool]) -> dict:
    ids = list(a)
    ra = sum(a.values()) / len(ids)
    rb = sum(b.values()) / len(ids)
    # discordant: a solved & b failed (b_only_fail), a failed & b solved (b_only_solve)
    only_a = sum(1 for i in ids if a[i] and not b[i])
    only_b = sum(1 for i in ids if b[i] and not a[i])
    return {"a": a_name, "b": b_name, "r_a": ra, "r_b": rb, "delta_a_minus_b": ra - rb,
            "discordant_a_only": only_a, "discordant_b_only": only_b,
            "mcnemar_p": mcnemar_exact(only_a, only_b)}


def verdict(out_dir: Path, instance_ids: list[str], margin: float = 0.08,
            alpha: float = 0.05) -> dict:
    led = {a: _load_ledger(a, out_dir) for a in ("S", "V", "I", "D")}
    vec = {a: _resolved_vec(led[a], instance_ids) for a in led}
    rates = {a: sum(vec[a].values()) / len(instance_ids) for a in led}

    d_vs_v = pairwise("D", vec["D"], "V", vec["V"])
    d_vs_i = pairwise("D", vec["D"], "I", vec["I"])
    v_vs_s = pairwise("V", vec["V"], "S", vec["S"])

    flips = {a: harmful_flips(vec["S"], vec[a]) for a in led}

    dcm_justified = (d_vs_v["delta_a_minus_b"] >= margin
                     and d_vs_v["mcnemar_p"] < alpha
                     and len(flips["D"]) <= len(flips["V"]))
    mutual_reading_needed = (d_vs_i["delta_a_minus_b"] >= margin
                             and d_vs_i["mcnemar_p"] < alpha)

    if dcm_justified:
        conclusion = "DCM JUSTIFIED — council beats the verifier materially; route winning task classes to it."
    elif not mutual_reading_needed and rates["I"] >= rates["V"]:
        conclusion = "FORCED MUTUAL-READING UNNECESSARY — independent reviews suffice; drop the coordination primitive."
    else:
        conclusion = "ARCHIVE THE COUNCIL — it adds nothing over a by-execution verifier; ship the verifier/gate."

    result = {
        "N": len(instance_ids), "margin": margin, "alpha": alpha,
        "resolved_rates": rates,
        "D_vs_V": d_vs_v, "D_vs_I": d_vs_i, "V_vs_S": v_vs_s,
        "harmful_flips": {a: flips[a] for a in led},
        "harmful_flip_counts": {a: len(flips[a]) for a in led},
        "dcm_justified": dcm_justified,
        "conclusion": conclusion,
        "cost_ledger_nongate": {a: {"tokens": led[a]["total_tokens"],
                                     "wall_clock_s": led[a]["total_wall_clock_s"]} for a in led},
    }
    (out_dir / "VERDICT.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "runs"
    ids = json.loads((HERE / "frozen_subset.json").read_text())["instance_ids"]
    print(json.dumps(verdict(out, ids), indent=2))
