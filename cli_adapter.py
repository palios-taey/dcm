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
import json, os, re, shutil, subprocess, tempfile, time
import mesh


class CliRunError(RuntimeError):
    """A CLI expert could not produce a usable contribution this attempt — the binary is missing,
    exited non-zero (down / rate-limited), timed out, or returned empty. Distinct from StaleReadError
    (mesh CAS, retry same CLI): a CliRunError means try a DIFFERENT CLI for this seat, or degrade."""


def _disabled_clis() -> set[str]:
    raw = os.environ.get("DCM_DISABLE_CLIS", "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def available_clis() -> list[str]:
    """Which expert CLIs are installed on PATH and not administratively disabled (in _RUNNERS
    preference order). A council seats from these; a missing/disabled CLI is not a crash, it's just
    not in the fallback pool."""
    disabled = _disabled_clis()
    # ep3 is an OpenAI-compatible ENDPOINT seat (the fine-tuned Taey serve), not a PATH binary — it
    # is available unless administratively disabled; the other experts must be on PATH. If the ep3
    # endpoint is down, _run_ep3 raises CliRunError and the seat degrades like any other. Runnability
    # is decided by _cli_runnable so this and cli_expert() cannot drift.
    return [c for c in _RUNNERS if c not in disabled and _cli_runnable(c)]

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

def _capture_dir() -> str:
    """Where per-call generation transcripts land. Operator-configurable; defaults under the system
    temp dir so nothing operator-specific is baked into this repo."""
    d = os.environ.get("DCM_CAPTURE_DIR") or os.path.join(tempfile.gettempdir(), "dcm-captures")
    os.makedirs(d, exist_ok=True)
    return d


def _parse_ep3_stream(raw: str) -> tuple[str, str, dict]:
    """Reconstruct (content, reasoning, meta) from however much of an OpenAI SSE stream arrived.

    Deliberately tolerant: a truncated final line, a stream cut mid-flight by a timeout, or a
    missing [DONE] all still yield every delta that DID arrive. That is the point — a partial
    transcript is evidence; a discarded one is not."""
    content, reasoning, meta = [], [], {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            chunk = json.loads(body)
        except ValueError:
            continue          # a torn final chunk loses that fragment, never the ones before it
        if chunk.get("usage"):
            meta["usage"] = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
            if delta.get("reasoning_content") or delta.get("reasoning"):
                reasoning.append(delta.get("reasoning_content") or delta.get("reasoning"))
            if choice.get("finish_reason"):
                meta["finish_reason"] = choice["finish_reason"]
    return "".join(content), "".join(reasoning), meta


def _run_ep3(prompt: str, timeout: int = 400) -> str:
    # ep3 = the fine-tuned Taey serve (OpenAI-compatible endpoint, NOT a PATH CLI). Seat contract:
    # NO constraints on Taey (no max_tokens / thinking toggle). The council prompt embeds peer
    # contributions and can be large, so POST the JSON payload from a file (same reason the CLI
    # runners feed via file/stdin, never argv).
    #
    # STREAMED + CAPTURED TO DISK, deliberately: this seat is the slowest in the council (a dense
    # contribution has measured ~13 minutes), so it is the one most likely to hit a timeout — and a
    # buffered request that times out returns NOTHING, destroying the whole generation. Streaming to
    # a capture file means a timeout, disconnect or crash still leaves the full partial transcript,
    # plus finish_reason / usage / wall-clock for the run. Capture happens BEFORE any raise, so a
    # failed call leaves more evidence than a silent one, not less.
    url = os.environ.get("EP3_URL", "http://localhost:8000/v1/chat/completions")
    payload = json.dumps({"model": os.environ.get("EP3_MODEL", "ep3"),
                          "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.4,
                          "stream": True,
                          "stream_options": {"include_usage": True}})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(payload); path = f.name
    cap = os.path.join(_capture_dir(), f"ep3-{os.getpid()}-{int(time.time()*1000)}")
    started = time.time()
    proc = None
    try:
        try:
            # --no-buffer so deltas land in the capture file as they arrive, not at completion.
            proc = subprocess.run(["curl", "-s", "--no-buffer", "-m", str(timeout), url,
                                   "-H", "Content-Type: application/json", "--data", "@" + path,
                                   "-o", cap + ".sse"],
                                  cwd="/tmp", stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout + 30)
        finally:
            elapsed = round(time.time() - started, 1)
            raw = ""
            try:
                with open(cap + ".sse") as fh:
                    raw = fh.read()
            except OSError:
                pass
            content, reasoning, meta = _parse_ep3_stream(raw)
            meta.update({"elapsed_s": elapsed, "url": url,
                         "content_chars": len(content), "reasoning_chars": len(reasoning),
                         "curl_rc": None if proc is None else proc.returncode})
            try:
                with open(cap + ".json", "w") as fh:
                    json.dump({"meta": meta, "content": content, "reasoning": reasoning}, fh)
            except OSError:
                pass          # capture is best-effort; never let recording break the call
        if proc.returncode != 0:
            raise CliRunError(f"ep3 curl exited {proc.returncode} after {elapsed}s "
                              f"(partial transcript preserved at {cap}.json: "
                              f"{len(content)} content / {len(reasoning)} reasoning chars)")
        out = (content or reasoning).strip()
        if not out:
            raise CliRunError(f"ep3 produced no text in {elapsed}s "
                              f"(transcript at {cap}.json, finish_reason={meta.get('finish_reason')})")
        return out
    finally:
        os.unlink(path)

_RUNNERS = {"codex": _run_codex, "gemini": _run_gemini, "claude": _run_claude, "grok": _run_grok, "ep3": _run_ep3}


def _cli_runnable(cli: str) -> bool:
    """Is this CLI actually invokable right now? SINGLE source of truth for available_clis() and
    cli_expert() — they previously duplicated this test and DRIFTED: available_clis() special-cased
    the ep3 endpoint but cli_expert()'s per-candidate guard used a bare `shutil.which(cli) is None`,
    so it rejected a SEATED ep3 as 'unavailable' before ever calling it (ep3 is an OpenAI-compatible
    ENDPOINT, not a PATH binary). ep3 is runnable if it has a runner; every other expert must be on
    PATH."""
    if cli not in _RUNNERS:
        return False
    return cli == "ep3" or shutil.which(cli) is not None


# Per-CLI minimum seat timeout (seconds). ep3 is the fine-tuned Taey serve on Jetson hardware: a
# thinking-ON decode wall of ~4.5 tok/s means a dense council contribution takes far longer than an
# agentic CLI — measured ~764s for a ~2.8KB scope-sentinel contribution. The default 400s seat cap
# (right for codex/claude/gemini/grok at ~60-180s) times ep3 out MID-GENERATION and forces a needless
# fallback. Floor ep3's seat timeout to the wall-clock its hardware needs; per the seat contract we
# never clamp ep3's OUTPUT to fit a shorter window, so the window must fit the output.
_CLI_MIN_TIMEOUT = {"ep3": 1200}

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
    attempts: list[str] = []
    disabled = _disabled_clis()
    candidates: list[str] = []
    for candidate in dict.fromkeys([cli, *fallbacks]):
        if not candidate:
            continue
        if candidate in disabled:
            attempts.append(f"{candidate}=disabled")
            continue
        candidates.append(candidate)
    for current in candidates:
        run = _RUNNERS.get(current)
        if not _cli_runnable(current):
            attempts.append(f"{current}=unavailable")
            continue
        eff_timeout = max(timeout, _CLI_MIN_TIMEOUT.get(current, 0))
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
                    content = run(prompt, timeout=eff_timeout)
                except subprocess.TimeoutExpired as exc:
                    raise CliRunError(f"{current} timed out after {eff_timeout}s") from exc
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
