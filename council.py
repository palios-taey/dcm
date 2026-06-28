"""DCM council runtime.

The public entry points are council_review(task, artifact, rules, roster=None) and
council_plan(problem, rules, roster=None). They seat the converged expert mix from eval/arms.py
on the mesh, preserve blind-then-reveal discipline, and publish finals only through
mesh.publish_final(). The clerk here is deterministic and non-voting: it can route, parse,
summarize, and fail closed, but it cannot approve or overrule an open block concern.
"""
from __future__ import annotations
import ast
from collections import Counter
from pathlib import Path
import copy
import json
import re
import sys
import cli_adapter
import mesh

# The contract every expert embeds. {sid} filled per session.
EXPERT_CONTRACT = """You are one expert in a DCM (Distributed Cognitive Mesh) council. Peers
work the SAME session in parallel. The mesh ENFORCES that you read peers before you commit.

Mesh: from the dcm/ directory, python3 with `import mesh`. Session: {sid}

Protocol (do exactly this, looping until your contribution lands):
1. ctx = mesh.read_session('{sid}')  -> note ctx['version'] and ctx['contributions'] (peers).
2. Form YOUR expert view of ctx['payload'] THROUGH YOUR LENS (below). Read every peer
   contribution; build on / sharpen / respectfully disagree with them — do not just restate.
   GROUNDED form (mandatory — kills sycophancy + same-answer-different-reasoning collapse):
   state each CLAIM with its GROUND (the evidence/reason it rests on), and for each peer you
   engage give an explicit STANCE — Agree / Disagree / Extend — WITH justification. Never adopt
   a peer's conclusion with no reasoning of your own; never agree just to converge.
3. peers = [c['contrib_id'] for c in ctx['contributions']]   # the peers you ACTUALLY read+used
   try: cid = mesh.contribute('{sid}', '<your_role>', '<your contribution>', peers_read=peers, read_version=ctx['version'])
   except mesh.StaleReadError: GOTO 1 (a peer took your slot — re-read, incorporate them, retry).
4. Return ONLY: your contrib_id, peers_read count, and your contribution text (concise, dense).

read_version=ctx['version'] is your compare-and-set token (the opener reads an empty session
-> version 0 -> contributes read_version=0). peers_read is your read-CLAIM: verify_coordination
flags you if you omit a peer that was present to you, so cite what you genuinely read.
"""


_EXPECTED_ROLES = ("foundation", "ground-runner", "evasive-repair", "scope-blast")
_ROLE_ALLOWED_CLIS = {
    "foundation": {"gemini", "claude"},
    "ground-runner": {"gemini", "claude", "grok"},
    "evasive-repair": {"grok"},
    "scope-blast": {"gemini", "grok"},
}
_RESOLUTION_DISPOSITIONS = {"FIX-VERIFIED", "FALSE-POSITIVE", "OUT-OF-SCOPE", "ACCEPTED-RISK", "ESCALATE"}
_EVIDENCE_REQUIRED = {"FIX-VERIFIED", "FALSE-POSITIVE"}
_CLASSIFICATIONS = {"ALLOWED_RESCAN", "BANNED_SETTLE_POLL", "UNKNOWN"}
_PLAN_ROLE_CONTRACTS = {
    "foundation": (
        "FOUNDATION PLANNING DUTY: identify prior solutions, docs, repo history, and existing "
        "utilities to reuse. Recover-don't-re-derive; name the concrete files/docs/history the "
        "implementation should start from."
    ),
    "ground-runner": (
        "GROUND-RUNNER PLANNING DUTY: define how the implementation will be proven against "
        "reality, including real-consult execution, raw screenshot evidence, clipboard/report "
        "capture, syntax gates, and the exact failure stop conditions."
    ),
    "evasive-repair": (
        "EVASIVE-REPAIR PLANNING DUTY: name the workaround traps to avoid, especially silent "
        "fallbacks, action retries, settle-polls, tree-only claims, and symptom patches. State "
        "the simpler root-cause shape the implementation should take."
    ),
    "scope-blast": (
        "SCOPE+BLAST PLANNING DUTY: define scope boundaries, destructive-operation limits, "
        "secret/data safety, display isolation, exact cleanup scope, and what must be escalated "
        "instead of broadened."
    ),
}


def open_council(topic: str, payload: str, roles: list[str]) -> str:
    return mesh.start_session(topic, payload, roles=roles)


def council_report(session_id: str) -> dict:
    s = mesh.read_session(session_id)
    v = mesh.verify_coordination(session_id)
    out = {"session_id": session_id, "topic": s["topic"], "status": s["status"],
           "verdict": v, "final": s.get("final"),
           "transcript": [{"role": c["role"], "claimed_reads": len(c.get("claimed_peers") or []),
                           "content": c["content"]} for c in s["contributions"]]}
    return out


def _literal_from_arms(name: str) -> dict:
    """Read literal role constants from eval/arms.py without importing its solver stack."""
    arms_path = Path(__file__).resolve().parent / "eval" / "arms.py"
    tree = ast.parse(arms_path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise RuntimeError(f"eval/arms.py does not define literal {name}")


def _default_roster() -> tuple[dict, dict]:
    return copy.deepcopy(_literal_from_arms("ROLES")), copy.deepcopy(_literal_from_arms("CLERK"))


def _normalize_roster(roster: dict | None) -> tuple[dict, dict]:
    roles, clerk = _default_roster()
    if roster is not None:
        roles = copy.deepcopy(roster)
    missing = [role for role in _EXPECTED_ROLES if role not in roles]
    if missing:
        raise ValueError(f"roster missing required role(s): {', '.join(missing)}")
    for role, spec in roles.items():
        for field in ("seat", "cli", "lens"):
            if not spec.get(field):
                raise ValueError(f"roster role {role!r} missing {field!r}")
        if spec["cli"] not in cli_adapter._RUNNERS:
            raise ValueError(f"roster role {role!r} uses unknown cli {spec['cli']!r}")
        allowed = _ROLE_ALLOWED_CLIS.get(role)
        if allowed and spec["cli"] not in allowed:
            raise ValueError(f"roster role {role!r} must use one of {sorted(allowed)}, got {spec['cli']!r}")
        if role == "ground-runner" and spec["cli"] == "codex":
            raise ValueError("ground-runner must use a non-producer base model")
    return roles, clerk


def _extract_json_object(text: str) -> tuple[dict | None, str | None]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            raw = text[start:end + 1]
    if raw is None:
        return None, "no JSON object found"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(obj, dict):
        return None, "JSON output must be an object"
    return obj, None


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value) -> list[str]:
    if isinstance(value, list):
        return [_text(v) for v in value if _text(v)]
    single = _text(value)
    return [single] if single else []


def _normalize_classification(value) -> str:
    raw = _text(value)
    token = raw.upper().replace("-", "_").replace(" ", "_")
    if token in _CLASSIFICATIONS:
        return token
    lower = raw.lower()
    if "allowed" in lower and ("dominant" in lower or "not a banned" in lower or "not the block" in lower):
        return "ALLOWED_RESCAN"
    if "not banned" in lower or "not a banned" in lower:
        return "ALLOWED_RESCAN"
    if "banned" in lower or "retry-until" in lower:
        return "BANNED_SETTLE_POLL"
    if "allowed" in lower and ("re-scan" in lower or "rescan" in lower):
        return "ALLOWED_RESCAN"
    if "settle-poll" in lower:
        return "BANNED_SETTLE_POLL"
    return "UNKNOWN"


def _parse_review_output(content: str) -> dict:
    obj, error = _extract_json_object(content)
    if error:
        return {"parsed": False, "error": error, "raw": content}
    status = _text(obj.get("status")).upper()
    if status in {"OK", "NONE", "NO CONCERN"}:
        status = "PASS"
    if status not in {"PASS", "CONCERN"}:
        return {"parsed": False, "error": f"invalid status {status!r}", "raw": content, "json": obj}
    severity = _text(obj.get("severity")).lower()
    if status == "CONCERN" and severity not in {"block", "warn"}:
        return {"parsed": False, "error": f"invalid concern severity {severity!r}",
                "raw": content, "json": obj}
    about = _text(obj.get("about"))
    if status == "CONCERN" and not about:
        return {"parsed": False, "error": "concern missing about", "raw": content, "json": obj}
    classification_raw = _text(obj.get("classification"))
    return {"parsed": True, "status": status, "severity": severity if status == "CONCERN" else None,
            "about": about or None, "classification": _normalize_classification(classification_raw),
            "classification_raw": classification_raw,
            "claim": _text(obj.get("claim")), "ground": _text(obj.get("ground")),
            "evidence": _text_list(obj.get("evidence")),
            "safety_veto": bool(obj.get("safety_veto")), "json": obj, "raw": content}


def _review_contribution_props(role: str):
    def parse(content: str, _ctx: dict) -> dict:
        parsed = _parse_review_output(content)
        if not parsed.get("parsed"):
            return {"kind": "concern", "severity": "block", "about": f"unparsed:{role}"}
        if parsed["status"] == "PASS":
            return {"kind": "contribution"}
        return {"kind": "concern", "severity": parsed["severity"], "about": parsed["about"],
                "veto": bool(parsed.get("safety_veto") and role == "scope-blast")}
    return parse


def _parse_resolution_output(content: str) -> dict:
    obj, error = _extract_json_object(content)
    if error:
        return {"parsed": False, "error": error, "disposition": "ESCALATE", "raw": content}
    disposition = _text(obj.get("disposition")).upper()
    if disposition not in _RESOLUTION_DISPOSITIONS:
        return {"parsed": False, "error": f"invalid disposition {disposition!r}",
                "disposition": "ESCALATE", "raw": content, "json": obj}
    evidence_ref = _text(obj.get("evidence_ref"))
    if disposition in _EVIDENCE_REQUIRED and not evidence_ref:
        return {"parsed": False, "error": f"{disposition} requires evidence_ref",
                "disposition": "ESCALATE", "raw": content, "json": obj}
    classification_raw = _text(obj.get("classification"))
    return {"parsed": True, "disposition": disposition, "evidence_ref": evidence_ref or None,
            "reason": _text(obj.get("reason")),
            "classification": _normalize_classification(classification_raw),
            "classification_raw": classification_raw,
            "raw": content, "json": obj}


def _resolution_contribution_props(concern: dict):
    def parse(content: str, _ctx: dict) -> dict:
        parsed = _parse_resolution_output(content)
        props = {"kind": "resolution", "about": concern["contrib_id"],
                 "disposition": parsed["disposition"]}
        if parsed.get("evidence_ref"):
            props["evidence_ref"] = parsed["evidence_ref"]
        return props
    return parse


def _lens_with_rules(spec: dict, task: str, rules: str) -> str:
    return (
        f"{spec['lens']}\n\n"
        f"REVIEW TASK:\n{task}\n\n"
        f"RULES / CONTRACT TO JUDGE AGAINST:\n{rules}")


def _plan_lens_with_rules(role: str, spec: dict, problem: str, rules: str) -> str:
    role_contract = _PLAN_ROLE_CONTRACTS[role]
    return (
        f"{spec['lens']}\n\n"
        f"{role_contract}\n\n"
        f"RULES / AUTHORITATIVE DOCS / NON-NEGOTIABLES:\n{rules}")


def _blind_prompt_extra() -> str:
    return """BLIND ROUND-0 CONTRACT:
You must decide independently from only the session topic, artifact, rules, and your lens.
Do not infer or reference peer views; none are visible in this round.

Return exactly one JSON object and no markdown:
{
  "status": "PASS" or "CONCERN",
  "severity": null or "block" or "warn",
  "about": null or "<specific artifact/rule claim>",
  "classification": "ALLOWED_RESCAN or BANNED_SETTLE_POLL",
  "claim": "<one concise claim>",
  "ground": "<the observed evidence/reasoning for that claim>",
  "evidence": ["<line/rule evidence>", "..."],
  "safety_veto": false
}

Use status PASS only when your lens finds no issue that should gate the verdict. Use severity
block only when the final verdict must not publish until the concern is resolved. Put mixed
evidence in claim/ground, but classification must choose the governing contract outcome."""


def _plan_blind_prompt_extra(role: str) -> str:
    return f"""BLIND PLANNING ROUND-0 CONTRACT:
You are producing an implementation PLAN PROPOSAL, not reviewing an already-written artifact.
No peer proposals are visible in this round. Work only from the shared problem, the rules/docs,
and your assigned planning duty.

Return a concrete proposal with these sections:
CLAIMS_AND_GROUNDS:
- <planning claim> | Ground: <specific rule/doc/problem evidence>
ORDERED_STEPS:
1. <implementation or investigation step>
2. <next step>
VERIFY:
- <how this plan proves the result against the real target>
AVOID:
- <fallback/workaround/scope trap to avoid>
UNKNOWN_OR_ESCALATE:
- <real uncertainty or stop condition, or NONE>

Be specific enough for a producer to implement without inventing a second plan. Do not include
general encouragement, review verdicts, or artifact critique language. Keep the proposal under
900 words."""


def _plan_proposal_props(_role: str):
    def parse(_content: str, _ctx: dict) -> dict:
        return {"kind": "plan_proposal"}
    return parse


def _consensus_plan_props(_content: str, _ctx: dict) -> dict:
    return {"kind": "consensus_plan"}


def _choose_plan_synthesizer(roles: dict, clerk: dict) -> str:
    owns = str(clerk.get("owns") or "").lower()
    if "dissent" in owns and "foundation" in roles:
        return "foundation"
    for role in ("foundation", "ground-runner", "scope-blast", "evasive-repair"):
        if role in roles:
            return role
    raise RuntimeError("no roster role available to synthesize the consensus plan")


def _consensus_prompt_extra() -> str:
    return """REVEAL PLANNING ROUND CONTRACT:
All blind plan proposals are now visible. Read every proposal and produce the CONSENSUS PLAN:
the merged, de-conflicted, ordered implementation plan for the producer.

Requirements:
- Preserve concrete grounded steps from the role proposals; do not average away dissent.
- Resolve ordering conflicts explicitly.
- Include verification against the real target, screenshot/clipboard evidence, and stop conditions.
- Include the banned fallback/workaround traps and scope boundaries.
- Cite which roles you are adopting, extending, or rejecting when resolving conflicts.
- Keep the consensus plan under 1400 words.

Return only the consensus plan text with these sections:
CONSENSUS_PLAN
GROUNDING_TO_REUSE
ORDERED_IMPLEMENTATION_STEPS
VERIFICATION_GATES
BANNED_TRAPS_AND_ROOT_CAUSE_SHAPE
SCOPE_BOUNDARIES_AND_ESCALATIONS
OPEN_UNKNOWNS"""


def _resolution_prompt_extra(concern: dict) -> str:
    return f"""REVEAL + RESOLVE CONTRACT:
All prior mesh contributions are now visible. Read the peer concerns before resolving the block
concern below. Resolve only this concern; do not approve or overrule unrelated concerns.

CONCERN TO RESOLVE:
contrib_id: {concern['contrib_id']}
role: {concern['role']}
about: {concern.get('about')}
content:
{concern['content']}

Return exactly one JSON object and no markdown:
{{
  "disposition": "FIX-VERIFIED" or "FALSE-POSITIVE" or "OUT-OF-SCOPE" or "ESCALATE",
  "evidence_ref": "<required for FIX-VERIFIED or FALSE-POSITIVE; cite artifact/rule/peer evidence>",
  "classification": "ALLOWED_RESCAN or BANNED_SETTLE_POLL",
  "reason": "<why this disposition is justified>"
}}

Use ESCALATE if the concern remains unresolved. FIX-VERIFIED means the final verdict can
incorporate the concern with cited evidence; FALSE-POSITIVE means cited evidence defeats it."""


def _role_public(spec: dict) -> dict:
    return {"seat": spec["seat"], "cli": spec["cli"]}


def _contribution_by_id(session: dict, contrib_id: str) -> dict:
    for contrib in session["contributions"]:
        if contrib["contrib_id"] == contrib_id:
            return contrib
    raise RuntimeError(f"contribution {contrib_id} disappeared from session")


def _classification_counts(per_role: dict) -> Counter:
    counts = Counter()
    for data in per_role.values():
        blind = (data or {}).get("blind") or {}   # incomplete role -> data may be None
        parsed = blind.get("parsed") or {}
        cls = parsed.get("classification")        # may be absent on an incomplete parse
        if parsed.get("parsed") and cls and cls != "UNKNOWN":
            counts[cls] += 1
    return counts


def _review_verdict(per_role: dict, open_concerns: list[dict]) -> str:
    """The actual review verdict (NOT the content-pattern classification).
    Fail-closed on open block-concerns; else the consensus of expert statuses."""
    if open_concerns:
        return "BLOCKED"
    statuses = [((data or {}).get("blind") or {}).get("parsed", {}).get("status")
                for data in per_role.values()]
    statuses = [s for s in statuses if s in ("PASS", "CONCERN")]
    if not statuses:
        return "UNKNOWN"           # nothing parsed cleanly
    if any(s == "CONCERN" for s in statuses):
        return "CONCERN"
    return "PASS"                  # all experts PASS + no open block-concerns


def _choose_classification(counts: Counter) -> str:
    if not counts:
        return "UNKNOWN"
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return "UNKNOWN"
    return top[0][0]


def _final_text(task: str, rules: str, per_role: dict, open_concerns: list[dict],
                counts: Counter) -> str:
    classification = _choose_classification(counts)
    lines = [
        # VERDICT is the review outcome (PASS/CONCERN/BLOCKED/UNKNOWN), NOT the
        # content-pattern classification — an all-PASS review must read PASS, never
        # inherit "UNKNOWN" from an absent content classification.
        f"VERDICT: {_review_verdict(per_role, open_concerns)}",
        f"CLASSIFICATION: {classification}",
        f"TASK: {task}",
        f"EVIDENCE_GATE: {'FAIL_CLOSED_OPEN_BLOCKS' if open_concerns else 'NO_OPEN_BLOCK_CONCERNS'}",
        f"RULES: {rules}",
        "ROLE_FINDINGS:",
    ]
    for role, data in per_role.items():
        parsed = ((data or {}).get("blind") or {}).get("parsed") or {}
        if parsed.get("parsed"):
            status = parsed["status"]
            severity = f"/{parsed['severity']}" if parsed.get("severity") else ""
            lines.append(
                f"- {role}: {status}{severity}; classification={parsed.get('classification')}; "
                f"raw_classification={parsed.get('classification_raw')}; "
                f"claim={parsed.get('claim')}; ground={parsed.get('ground')}")
        else:
            lines.append(f"- {role}: UNPARSED; error={parsed.get('error')}")
    if open_concerns:
        lines.append("OPEN_BLOCK_CONCERNS:")
        for concern in open_concerns:
            lines.append(f"- {concern['contrib_id']} role={concern['role']} about={concern.get('about')}")
    if counts:
        lines.append("CLASSIFICATION_COUNTS:")
        for label, count in counts.most_common():
            lines.append(f"- {label}: {count}")
    return "\n".join(lines)


def council_review(task: str, artifact: str, rules: str, roster: dict | None = None) -> dict:
    """Run the DCM expert mix over an artifact and publish only if concerns close.

    The session payload is exactly the artifact. The rules and task are injected into each
    expert's lens so the blind round sees task + artifact + rules without peer text. Blind
    contributions honestly claim no peers_read; the reveal/resolution round uses peer-visible
    mesh reads and typed resolution records. Any unparsed expert output becomes a block concern.
    """
    roles, clerk = _normalize_roster(roster)
    session_id = mesh.start_session(topic=task, payload=artifact, roles=list(roles))
    per_role = {role: {**_role_public(spec), "blind": None, "resolutions": []}
                for role, spec in roles.items()}
    ledger = {"session_id": session_id,
              "clerk": {"seat": clerk["seat"], "voting": False,
                        "owns": clerk.get("owns"),
                        "actions": []},
              "roster": {role: _role_public(spec) for role, spec in roles.items()},
              "blind_round": [], "resolution_round": []}

    for role, spec in roles.items():
        record = cli_adapter.cli_expert(
            session_id, role, _lens_with_rules(spec, task, rules), cli=spec["cli"],
            peers_visible=False, prompt_extra=_blind_prompt_extra(),
            parse_contribution=_review_contribution_props(role), return_record=True)
        session = mesh.read_session(session_id)
        contrib = _contribution_by_id(session, record["contrib_id"])
        parsed = _parse_review_output(record["content"])
        blind = {"contrib_id": record["contrib_id"], "kind": contrib["kind"],
                 "severity": contrib.get("severity"), "about": contrib.get("about"),
                 "veto": contrib.get("veto"), "peers_read": record["peers_read"],
                 "parsed": parsed}
        per_role[role]["blind"] = blind
        ledger["blind_round"].append({"role": role, **blind})
        ledger["clerk"]["actions"].append(
            f"recorded blind {contrib['kind']} from {role} as {record['contrib_id']}")

    for concern in list(mesh.open_concerns(session_id)):
        role = concern["role"]
        spec = roles[role]
        record = cli_adapter.cli_expert(
            session_id, f"{role}:resolver", _lens_with_rules(spec, task, rules),
            cli=spec["cli"], peers_visible=True, prompt_extra=_resolution_prompt_extra(concern),
            parse_contribution=_resolution_contribution_props(concern), return_record=True)
        session = mesh.read_session(session_id)
        contrib = _contribution_by_id(session, record["contrib_id"])
        parsed = _parse_resolution_output(record["content"])
        resolution = {"contrib_id": record["contrib_id"], "about": contrib.get("about"),
                      "disposition": contrib.get("disposition"),
                      "evidence_ref": contrib.get("evidence_ref"),
                      "peers_read": record["peers_read"], "parsed": parsed}
        per_role[role]["resolutions"].append(resolution)
        ledger["resolution_round"].append({"role": role, **resolution})
        ledger["clerk"]["actions"].append(
            f"recorded {contrib.get('disposition')} resolution {record['contrib_id']} for {concern['contrib_id']}")

    open_concerns = mesh.open_concerns(session_id)
    counts = _classification_counts(per_role)
    final = _final_text(task, rules, per_role, open_concerns, counts)
    try:
        mesh.publish_final(session_id, final)
        verdict = {"status": "published", "classification": _choose_classification(counts),
                   "session_id": session_id, "final": final}
        ledger["publish"] = {"status": "published"}
    except mesh.UnresolvedConcernsError as exc:
        verdict = {"status": "blocked", "classification": _choose_classification(counts),
                   "session_id": session_id, "error": str(exc), "final_candidate": final}
        ledger["publish"] = {"status": "blocked", "error": str(exc)}
    ledger["coordination"] = {"blind_round_claims_empty_by_design": True,
                              "mesh_verify": mesh.verify_coordination(session_id)}
    ledger["classification_counts"] = dict(counts)
    return {"verdict": verdict, "open_concerns": open_concerns,
            "ledger": ledger, "per_role": per_role}


def council_plan(problem: str, rules: str, roster: dict | None = None) -> dict:
    """Run a DCM planning council and publish a consensus implementation plan.

    The session topic is always "PLAN" and the payload is exactly the problem. Rules/docs are
    injected into each expert lens, not hidden in the artifact. The blind round records typed
    plan_proposal contributions with no claimed peer reads by design. The reveal round seats one
    explicit synthesizer, makes all proposals visible, records a typed consensus_plan contribution,
    and publishes that text through mesh.publish_final(). CLI or mesh failures propagate rather
    than falling back to a local summary.
    """
    roles, clerk = _normalize_roster(roster)
    unsupported = [role for role in roles if role not in _PLAN_ROLE_CONTRACTS]
    if unsupported:
        raise ValueError(f"council_plan does not define planning duties for role(s): {', '.join(unsupported)}")

    session_id = mesh.start_session(topic="PLAN", payload=problem, roles=list(roles))
    per_role = {}
    ledger = {"session_id": session_id,
              "clerk": {"seat": clerk["seat"], "voting": False,
                        "owns": clerk.get("owns"),
                        "actions": []},
              "roster": {role: _role_public(spec) for role, spec in roles.items()},
              "blind_round": [], "reveal_round": []}

    for role, spec in roles.items():
        record = cli_adapter.cli_expert(
            session_id, role, _plan_lens_with_rules(role, spec, problem, rules),
            cli=spec["cli"], peers_visible=False, prompt_extra=_plan_blind_prompt_extra(role),
            parse_contribution=_plan_proposal_props(role), return_record=True)
        session = mesh.read_session(session_id)
        contrib = _contribution_by_id(session, record["contrib_id"])
        if contrib["kind"] != "plan_proposal":
            raise RuntimeError(
                f"plan proposal {record['contrib_id']} from {role} recorded kind {contrib['kind']!r}")
        proposal = record["content"].strip()
        if not proposal:
            raise RuntimeError(f"plan proposal {record['contrib_id']} from {role} was empty")
        per_role[role] = proposal
        blind = {"role": role, "contrib_id": record["contrib_id"], "kind": contrib["kind"],
                 "peers_read": record["peers_read"], "content": proposal}
        ledger["blind_round"].append(blind)
        ledger["clerk"]["actions"].append(
            f"recorded blind plan_proposal from {role} as {record['contrib_id']}")

    synth_role = _choose_plan_synthesizer(roles, clerk)
    synth_spec = roles[synth_role]
    synth_lens = (
        f"{synth_spec['lens']}\n\n"
        f"You are acting as the {clerk['seat']} synthesizer, using the {synth_role} seat's "
        f"CLI and lens only to merge the council's blind proposals into one ordered plan. "
        f"You are non-voting: preserve grounded dissent and open unknowns instead of hiding them.\n\n"
        f"RULES / AUTHORITATIVE DOCS / NON-NEGOTIABLES:\n{rules}")
    record = cli_adapter.cli_expert(
        session_id, "clerk:synthesizer", synth_lens, cli=synth_spec["cli"],
        peers_visible=True, prompt_extra=_consensus_prompt_extra(),
        parse_contribution=_consensus_plan_props, return_record=True)
    session = mesh.read_session(session_id)
    contrib = _contribution_by_id(session, record["contrib_id"])
    if contrib["kind"] != "consensus_plan":
        raise RuntimeError(
            f"consensus contribution {record['contrib_id']} recorded kind {contrib['kind']!r}")
    consensus_plan = record["content"].strip()
    if not consensus_plan:
        raise RuntimeError(f"consensus contribution {record['contrib_id']} was empty")

    reveal = {"role": "clerk:synthesizer", "synthesizer_role": synth_role,
              "contrib_id": record["contrib_id"], "kind": contrib["kind"],
              "peers_read": record["peers_read"], "content": consensus_plan}
    ledger["reveal_round"].append(reveal)
    ledger["clerk"]["actions"].append(
        f"recorded consensus_plan {record['contrib_id']} synthesized via {synth_role}")
    mesh.publish_final(session_id, consensus_plan)
    ledger["publish"] = {"status": "published", "final_contrib_id": record["contrib_id"]}
    ledger["coordination"] = {"blind_round_claims_empty_by_design": True,
                              "reveal_round_peers_visible": True,
                              "mesh_verify": mesh.verify_coordination(session_id)}
    return {"plan": consensus_plan, "per_role": per_role, "ledger": ledger}


if __name__ == "__main__":
    print(json.dumps(council_report(sys.argv[1]), indent=2))
