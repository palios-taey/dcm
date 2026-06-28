"""CLI -> DCM adapter: runs fleet CLI peers (Codex, Claude, Gemini-CLI, Grok) as first-class mesh experts,
under the SAME staleness gate as Claude-Code, Taey, and (future) the Chats.

Per the fleet_integration council finding: CLIs join via hooks wrapping `codex exec`,
`claude -p`, `gemini -p`, or `grok -p`, and like every adapter funnel through mesh.contribute(read_version) — the
adapter owns the read+commit so the CLI can't bypass read-before-write. Closes the
fleet-capability gap (the four fleet CLIs are full peers, not subprocess tools).

SECURITY (honest, per gatekeeper audit): peer contributions are attacker-influenceable text
and they are injected into the CLI prompt below, while acting CLIs can take real actions on
the host. The "do NOT edit any files" line in the prompt is advisory ONLY — it is not an
enforced sandbox. Run CLI experts on councils whose participants you trust, and/or sandbox
the CLI (containerize, drop fs/network) before seating it on an untrusted-content mesh. This
is inherent to running an acting agent on shared deliberation; it is documented, not solved.
"""
from __future__ import annotations
from collections.abc import Callable
import os, re, subprocess, tempfile
import mesh

# Prompts are fed via stdin / --prompt-file, NEVER as an argv string: a coordinated
# mesh prompt embeds all peer contributions and routinely exceeds Linux MAX_ARG_STRLEN
# (128KB per arg) -> "OSError: Argument list too long". stdin / file have no such cap.


def _raise_on_failure(cli: str, proc: subprocess.CompletedProcess[str]) -> None:
    if proc.returncode == 0:
        return
    out = ((proc.stdout or "") + (("\n[STDERR]\n" + proc.stderr) if proc.stderr else "")).strip()
    raise RuntimeError(f"{cli} exited {proc.returncode}:\n{out[-2000:]}")


def _run_codex(prompt: str, timeout: int = 400) -> str:
    # codex exec - reads the prompt from stdin
    p = subprocess.run(["codex", "exec", "--skip-git-repo-check", "-"],
                       input=prompt, cwd="/tmp",
                       capture_output=True, text=True, timeout=timeout)
    _raise_on_failure("codex", p)
    out = p.stdout
    # codex echoes the final answer after the trailing "tokens used\n<n>" footer.
    # If the footer is present, return ONLY the post-footer answer (even if empty —
    # an empty answer is honest); do NOT silently substitute the full raw stdout
    # (reasoning trace + banner) when the post-footer is empty.
    if "tokens used" in out:
        tail = out.rsplit("tokens used", 1)[-1]
        return re.sub(r"^\s*\d[\d,]*\s*", "", tail).strip()  # drop the token-count line
    return out.strip()  # no footer -> nothing to strip

def _run_gemini(prompt: str, timeout: int = 400) -> str:
    # gemini reads stdin as input and APPENDS the -p arg; pass the full prompt on stdin (no argv
    # cap) + a minimal -p pointer. --skip-trust is REQUIRED: gemini refuses an untrusted workspace
    # headlessly, and depending on version either HANGS on the interactive trust prompt (stdin is
    # the prompt, so the y/n never comes → timeout) or returns EMPTY stdout exit-0. The
    # GEMINI_CLI_TRUST_WORKSPACE env var is NOT honored (verified: it hung). --approval-mode yolo
    # auto-approves so a tool-touch doesn't hang either. (Found by dogfooding the first real council.)
    p = subprocess.run(["gemini", "-p", "Follow the instructions in the input above and respond.",
                        "--approval-mode", "yolo", "--skip-trust"],
                       input=prompt, cwd="/tmp",
                       capture_output=True, text=True, timeout=timeout)
    _raise_on_failure("gemini", p)
    return (p.stdout or "").strip()

def _run_claude(prompt: str, timeout: int = 400) -> str:
    # bare `claude -p` reads the prompt from stdin
    p = subprocess.run(["claude", "-p", "--dangerously-skip-permissions"],
                       input=prompt, cwd="/tmp",
                       capture_output=True, text=True, timeout=timeout)
    _raise_on_failure("claude", p)
    return (p.stdout or "").strip()

def _run_grok(prompt: str, timeout: int = 400) -> str:
    # grok takes the prompt only as argv (-p) or from a file; use --prompt-file (no argv cap)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(prompt); path = f.name
    try:
        p = subprocess.run(["grok", "--prompt-file", path,
                            "--always-approve", "--permission-mode", "bypassPermissions"],
                           cwd="/tmp", stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        _raise_on_failure("grok", p)
        return (p.stdout or "").strip()
    finally:
        os.unlink(path)

_RUNNERS = {"codex": _run_codex, "gemini": _run_gemini, "claude": _run_claude, "grok": _run_grok}

def cli_expert(session_id: str, role: str, lens: str, cli: str = "codex", max_retry: int = 4,
               *, peers_visible: bool = True, prompt_extra: str | None = None,
               parse_contribution: Callable[[str, dict], dict] | None = None,
               return_record: bool = False, timeout: int = 400) -> str | dict:
    """Run one CLI expert and commit its output to the mesh.

    peers_visible=False is for sealed blind rounds: the adapter still reads the
    session version needed for CAS, but the CLI prompt receives no peer content
    and the mesh contribution honestly claims no peer reads.
    """
    run = _RUNNERS[cli]
    for _ in range(max_retry):
        ctx = mesh.read_session(session_id)
        visible_peers = ctx["contributions"] if peers_visible else []
        peers_txt = "\n\n".join(f"[{c['role']}] {c['content']}" for c in visible_peers) or "(none yet)"
        peer_header = "PEER CONTRIBUTIONS (build on / sharpen / disagree - do NOT restate, do NOT edit any files)"
        if not peers_visible:
            peer_header = "PEER CONTRIBUTIONS (sealed blind round - hidden from this expert)"
        prompt = (
            f"You are a DCM (Distributed Cognitive Mesh) council expert. LENS: {lens}\n\n"
            f"SESSION TOPIC:\n{ctx['topic']}\n\n"
            f"SHARED ARTIFACT:\n{ctx['payload']}\n\n"
            f"{peer_header}:\n{peers_txt}\n\n"
            f"Output ONLY your contribution text through your lens — concise, dense, additive. GROUNDED form: "
            f"each CLAIM with its GROUND, and an explicit STANCE (Agree/Disagree/Extend) with justification "
            f"for each peer you engage — never agree just to converge.")
        if prompt_extra:
            prompt = f"{prompt}\n\n{prompt_extra}"
        content = run(prompt, timeout=timeout)
        # Fail-closed on an EMPTY model call: a CLI that refuses headlessly and exits 0 (e.g. an
        # untrusted-dir refusal) returns empty stdout — committing it would be a SILENT model-call
        # failure (the README forbids silent fallbacks for failed model calls). Raise, never commit
        # an empty contribution. (Found by dogfooding the first real council — gemini exit-0 empty.)
        if not content or not content.strip():
            raise RuntimeError(
                f"CLI {cli!r} (role {role!r}) returned EMPTY output — a silent model-call failure "
                f"(headless refusal / exit-0 with no stdout). Refusing to commit an empty contribution.")
        typed = parse_contribution(content, ctx) if parse_contribution else {}
        if not isinstance(typed, dict):
            raise TypeError("parse_contribution must return a dict of mesh.contribute keyword arguments")
        peers = [c["contrib_id"] for c in visible_peers]
        try:
            cid = mesh.contribute(session_id, role, content, peers_read=peers,
                                  read_version=ctx["version"], **typed)
            if return_record:
                return {"contrib_id": cid, "content": content, "peers_read": peers,
                        "read_version": ctx["version"], "typed": typed}
            return cid
        except mesh.StaleReadError:
            continue
    raise RuntimeError(f"CLI expert {role} ({cli}) could not land after {max_retry} retries")

if __name__ == "__main__":
    import sys
    print(cli_expert(sys.argv[1], sys.argv[2], sys.argv[3], cli=sys.argv[4] if len(sys.argv) > 4 else "codex"))
