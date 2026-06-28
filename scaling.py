"""scaling — blast-radius-tiered DCM roster sizing. ROUND2_SYNTHESIS.md §4 ("6–12, scale by blast radius").

The council seats N reviewers by a computed blast-radius TIER, never a fixed constant:
- compress (low-risk): 3 core reviewers — trivial / doc / low-surface changes.
- standard: the 4 canonical converged reviewers — the default.
- expand (high-blast-radius): 9 reviewers — Foundation split→Memory+Git, Scope split→Sentinel+Blast-Shield,
  + Test-Integrity, Dependency/API-reality, Red-Team-injection (a second producer is also seated in the
  produce loop; see arms.PRODUCER_2).

HIGH-blast-radius triggers (§4): live data, secrets, migration, gate-change, cross-repo, release,
prior-failure, OR a gitnexus_impact HIGH/CRITICAL. Any one trigger → expand. Explicit low_risk → compress.
The EXACT counts are the C-ablation's job to tune (§7); this provides the tiered mechanism + the role sets,
not a frozen answer.
"""
import council

# Tier → ordered reviewer role names (composed from canonical ROLES + arms.EXPAND_ROLES).
_COMPRESS = ("foundation", "evasive-repair", "scope-blast")
_STANDARD = ("foundation", "ground-runner", "evasive-repair", "scope-blast")
_EXPAND = ("memory-scout", "git-historian", "ground-runner", "evasive-repair",
           "scope-sentinel", "blast-shield", "test-integrity",
           "dependency-api-reality", "red-team-injection")
_TIERS = {"compress": _COMPRESS, "standard": _STANDARD, "expand": _EXPAND}

# Any one of these being true escalates to the full high-blast-radius council (§4).
HIGH_TRIGGERS = ("live_data", "secrets", "migration", "gate_change",
                 "cross_repo", "release", "prior_failure")


def _role_pool() -> dict:
    """All seatable reviewer roles: canonical (council) + expansion (arms.EXPAND_ROLES)."""
    return {**council.canonical_reviewer_roster(), **council._literal_from_arms("EXPAND_ROLES")}


def tier_for(*, impact_risk: str | None = None, low_risk: bool = False, **triggers) -> str:
    """Compute the blast-radius tier.

    impact_risk: a gitnexus_impact level ('LOW'/'MEDIUM'/'HIGH'/'CRITICAL') or None.
    triggers: any of HIGH_TRIGGERS set True. Any HIGH/CRITICAL impact or any high-trigger → 'expand'.
    Explicit low_risk=True with no high-trigger → 'compress'. Otherwise 'standard'.
    """
    unknown = [k for k in triggers if k not in HIGH_TRIGGERS]
    if unknown:
        raise ValueError(f"unknown blast-radius trigger(s): {unknown}; allowed: {HIGH_TRIGGERS}")
    if (impact_risk or "").upper() in ("HIGH", "CRITICAL"):
        return "expand"
    if any(triggers.get(t) for t in HIGH_TRIGGERS):
        return "expand"
    if low_risk:
        return "compress"
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
