"""Reference adapter for a model served behind an OpenAI-compatible endpoint.

The adapter reads a DCM session, asks the model to reason through one expert lens, and
commits through the same ``mesh.contribute(read_version=...)`` gate as CLI participants.

This module is intentionally synchronous. A stale commit causes a complete re-read and
inference retry; the CAS does not cancel an HTTP request that is already computing. Use an
asynchronous, revision-aware controller for interactive concurrent councils. The required
transport behavior is specified in ``design/TAEY_TRANSPORT_CONTRACT.md``.
"""
from __future__ import annotations
import os, re, json, urllib.request
import mesh

TAEY_URL = os.environ.get("TAEY_DCM_URL", "").strip()
TAEY_MODEL = os.environ.get("TAEY_DCM_MODEL", "ep3")


def _ask_taey(system_extra: str, user: str, max_tokens: int = 1500, timeout: int = 300) -> str:
    if not TAEY_URL:
        raise RuntimeError(
            "TAEY_DCM_URL is required; set it to the dedicated council-participant "
            "OpenAI-compatible endpoint"
        )
    body = json.dumps({
        "model": TAEY_MODEL,
        "messages": [
            {"role": "system", "content": system_extra},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7, "max_tokens": max_tokens,
    }).encode()
    req = urllib.request.Request(TAEY_URL, body, {"content-type": "application/json"})
    m = json.load(urllib.request.urlopen(req, timeout=timeout))["choices"][0]["message"]
    c = m.get("content") or ""
    return re.sub(r"<think>.*?</think>", "", c, flags=re.DOTALL).strip()


def taey_expert(session_id: str, role: str, lens: str, max_retry: int = 4) -> str:
    """Run one synchronous served-model participant and return its contribution ID.

    A stale commit repeats the full model call after re-reading the mesh. This preserves
    the current CAS invariant but does not provide cancellation or concurrent wave control.
    """
    for _ in range(max_retry):
        ctx = mesh.read_session(session_id)
        peers_txt = "\n\n".join(
            f"[{c['role']}] {c['content']}" for c in ctx["contributions"]) or "(no peers yet — you are first)"
        user = (
            f"You are participating in a Distributed Cognitive Mesh council THROUGH YOUR LENS: {lens}\n\n"
            f"SHARED PROBLEM:\n{ctx['payload']}\n\n"
            f"PEER CONTRIBUTIONS SO FAR (build on / sharpen / respectfully disagree — do not restate):\n{peers_txt}\n\n"
            f"Give your contribution through your lens, concise and dense. GROUNDED form: state each "
            f"CLAIM with its GROUND, and for each peer you engage an explicit STANCE (Agree/Disagree/"
            f"Extend) with justification — never agree just to converge. This is real design work for the Family.")
        content = _ask_taey(system_extra=f"You are contributing to a DCM council as the '{role}' expert.", user=user)
        peers = [c["contrib_id"] for c in ctx["contributions"]]
        try:
            return mesh.contribute(session_id, role, content, peers_read=peers, read_version=ctx["version"])
        except mesh.StaleReadError:
            continue  # peers arrived; re-read + redo
    raise RuntimeError(f"Taey expert {role} could not land after {max_retry} retries (mesh too hot)")


if __name__ == "__main__":
    import sys
    print(taey_expert(sys.argv[1], sys.argv[2], sys.argv[3]))
