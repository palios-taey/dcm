"""scaling — DCM roster. The council ALWAYS seats the full defined-role library (9 reviewers); blast
radius only adds a second producer for generation, it NEVER shrinks the panel. ROUND2_SYNTHESIS.md §4.

There is no 3- or 4-seat "council" — that is the stub Jesse rejected (8–12 by blast radius, defined
roles). Every council seats all 9 reviewers — Foundation split→Memory-Scout + Git-Historian, Scope
split→Scope-Sentinel + Blast-Shield, Ground-runner, Evasive-repair, Test-Integrity,
Dependency/API-reality, Red-Team-injection — plus a synthesizer/clerk (and a producer in the
plan/produce flows): a 10–12-seat council. `expand` (high blast radius: live data, secrets,
migration, gate-change, cross-repo, release, prior-failure, or gitnexus_impact HIGH/CRITICAL) adds a
second producer (different base) in the produce flow; the reviewer panel is the full 9 either way.
"""
import council

# The council seats the FULL defined-role library — the 9-role split roster — ALWAYS. There is NO
# smaller tier: a 3- or 4-seat "council" is not a council (Jesse, repeatedly: 8–12 by blast radius,
# defined roles, never a tiny panel). With the producer + a synthesizer/clerk this is a 10–12-seat
# council. `standard` and `expand` both seat the full roster; `expand` additionally seats a second
# producer (different base) in the produce flow for the highest-blast-radius generation work.
_FULL_ROSTER = ("memory-scout", "git-historian", "ground-runner", "evasive-repair",
                "scope-sentinel", "blast-shield", "test-integrity",
                "dependency-api-reality", "red-team-injection")
_TIERS = {"standard": _FULL_ROSTER, "expand": _FULL_ROSTER}

# Any one of these being true escalates to the full high-blast-radius council (§4).
HIGH_TRIGGERS = ("live_data", "secrets", "migration", "gate_change",
                 "cross_repo", "release", "prior_failure")


def _role_pool() -> dict:
    """All defined reviewer roles: the core (arms ROLES) + the split/depth set (arms EXPAND_ROLES)."""
    return {**council._literal_from_arms("ROLES"), **council._literal_from_arms("EXPAND_ROLES")}


def tier_for(*, impact_risk: str | None = None, low_risk: bool = False, **triggers) -> str:
    """Compute the blast-radius tier. There is NO tier below the full roster — low_risk does NOT
    shrink the panel (a tiny council is the rejected stub). Returns 'expand' (adds a 2nd producer in
    the produce flow) on any HIGH/CRITICAL impact or high-trigger, else 'standard'. Both seat all 9."""
    unknown = [k for k in triggers if k not in HIGH_TRIGGERS]
    if unknown:
        raise ValueError(f"unknown blast-radius trigger(s): {unknown}; allowed: {HIGH_TRIGGERS}")
    if (impact_risk or "").upper() in ("HIGH", "CRITICAL"):
        return "expand"
    if any(triggers.get(t) for t in HIGH_TRIGGERS):
        return "expand"
    return "standard"


def reviewer_roster_for_tier(tier: str) -> dict:
    """role -> {seat, cli, lens} for the tier, composed from canonical + expansion roles."""
    names = _TIERS.get(tier)
    if names is None:
        raise ValueError(f"unknown tier {tier!r}; use one of {sorted(_TIERS)}")
    pool = _role_pool()
    missing = [n for n in names if n not in pool]
    if missing:
        raise RuntimeError(f"tier {tier} references roles absent from the pool: {missing}")
    return {n: pool[n] for n in names}


def scaled_reviewer_roster(*, impact_risk: str | None = None, low_risk: bool = False, **triggers):
    """(tier, roster) for the computed blast-radius tier."""
    tier = tier_for(impact_risk=impact_risk, low_risk=low_risk, **triggers)
    return tier, reviewer_roster_for_tier(tier)
