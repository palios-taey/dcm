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
import os, re, shutil, subprocess, tempfile
import mesh


class CliRunError(RuntimeError):
    """A CLI expert could not produce a usable contribution this attempt — the binary is missing,
    exited non-zero (down / rate-limited), timed out, or returned empty. Distinct from StaleReadError
    (mesh CAS, retry same CLI): a CliRunError means try a DIFFERENT CLI for this seat, or degrade."""


def available_clis() -> list[str]:
    """Which expert CLIs are actually installed on PATH (in _RUNNERS preference order). A council
    seats from these; a missing CLI is not a crash, it's just not in the fallback pool."""
    return [c for c in _RUNNERS if shutil.which(c)]

# Prompts are fed via stdin / --prompt-file, NEVER as an argv string: a coordinated
# mesh prompt embeds all peer contributions and routinely exceeds Linux MAX_ARG_STRLEN
# (128KB per arg) -> "OSError: Argument list too long". stdin / file have no such cap.


def _raise_on_failure(cli: str, proc: subprocess.CompletedProcess[str]) -> None:
    if proc.returncode == 0:
        return
    out = ((proc.stdout or "") + (("\n[STDERR]\n" + proc.stderr) if proc.stderr else "")).strip()
    raise CliRunError(f"{cli} exited {proc.returncode}:\n{out[-2000:]}")


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
               return_record: bool = False, timeout: int = 400,
               fallbacks: tuple[str, ...] = ()) -> str | dict:
    """Run one CLI expert and commit its output to the mesh.

    Tries `cli` first, then each distinct entry in `fallbacks` if the CLI is unavailable / down /
    rate-limited / times out / returns empty (a CliRunError) — one CLI being down DEGRADES to
    another, it does not crash the council. StaleReadError (mesh CAS) retries the SAME CLI. The
    returned record includes "cli" = the CLI that actually produced the contribution.

    peers_visible=False is a sealed blind round: no peer content in the prompt, no claimed reads.
    """
    candidates = [cli] + [c for c in fallbacks if c and c != cli]
    attempts: list[str] = []
    for current in candidates:
        run = _RUNNERS.get(current)
        if run is None or shutil.which(current) is None:
            attempts.append(f"{current}=unavailable")
            continue
        try:
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
                try:
                    content = run(prompt, timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    raise CliRunError(f"{current} timed out after {timeout}s") from exc
                # Fail-closed on an EMPTY model call (e.g. a headless refusal that exits 0): committing
                # it would be a silent model-call failure. Treat as a CLI failure → try the next CLI.
                if not content or not content.strip():
                    raise CliRunError(
                        f"{current!r} (role {role!r}) returned EMPTY output — silent model-call failure "
                        f"(headless refusal / exit-0 no stdout). Not committing empty.")
                typed = parse_contribution(content, ctx) if parse_contribution else {}
                if not isinstance(typed, dict):
                    raise TypeError("parse_contribution must return a dict of mesh.contribute keyword arguments")
                peers = [c["contrib_id"] for c in visible_peers]
                try:
                    cid = mesh.contribute(session_id, role, content, peers_read=peers,
                                          read_version=ctx["version"], **typed)
                    if return_record:
                        return {"contrib_id": cid, "content": content, "peers_read": peers,
                                "read_version": ctx["version"], "typed": typed, "cli": current}
                    return cid
                except mesh.StaleReadError:
                    continue
            raise CliRunError(f"{current}: could not land after {max_retry} CAS retries")
        except (CliRunError, FileNotFoundError) as exc:
            attempts.append(f"{current}={type(exc).__name__}")
            continue
    raise CliRunError(f"role {role!r}: no CLI produced a contribution; tried [{', '.join(attempts)}]")

if __name__ == "__main__":
    import sys
    print(cli_expert(sys.argv[1], sys.argv[2], sys.argv[3], cli=sys.argv[4] if len(sys.argv) > 4 else "codex"))
