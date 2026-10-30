from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_cli import AGENTS, procs
from agents_cli.home import agents_home, prepend_bin_dirs
from agents_cli.parse import ParseError


CANDIDATES = {
    "grok": [
        Path.home() / ".grok" / "bin" / "grok.exe",
        "grok",
    ],
    "claude": [
        Path.home() / ".local" / "bin" / "claude.exe",
        "claude",
    ],
    "gemini": [
        Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe",
        "agy",
        "gemini",
    ],
    "codex": [
        "codex",
    ],
}


@dataclass
class Launch:
    agent: str
    argv: list[str]
    cwd: str
    env: dict[str, str]
    prompt_file: Path
    last_message_file: Path | None = None
    stdin: str | None = None
    kind: str = "print"


@dataclass
class Probe:
    agent: str
    found: bool
    path: str | None
    error: str | None = None


def which(agent: str, config: dict | None = None) -> str | None:
    override = None
    if config:
        override = (config.get("binaries") or {}).get(agent)
    if override:
        path = Path(str(override)).expanduser()
        if path.exists():
            return str(path)
        found = shutil.which(str(override))
        return found
    for candidate in CANDIDATES[agent]:
        if isinstance(candidate, Path):
            if candidate.exists():
                return str(candidate)
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def probe(config: dict | None = None) -> list[Probe]:
    rows = []
    for agent in AGENTS:
        path = which(agent, config)
        rows.append(Probe(agent=agent, found=bool(path), path=path, error=None if path else "nao encontrado no PATH"))
    return rows


def binary_name(path: str) -> str:
    return Path(path).stem.lower()


def _windows() -> bool:
    return os.name == "nt"


# Characters cmd.exe interprets even inside the arguments of a .cmd/.bat shim.
CMD_UNSAFE_CHARS = frozenset('&|<>^%!"\r\n')


def check_cmd_shim_argv(binary: str, argv: list[str]) -> None:
    """npm shims (codex.CMD) run through cmd.exe: refuse arguments it would reinterpret."""
    if not _windows() or Path(binary).suffix.lower() not in (".cmd", ".bat"):
        return
    for arg in argv[1:]:
        if any(ch in CMD_UNSAFE_CHARS for ch in arg):
            raise ParseError(f"argumento inseguro para shim .cmd: {arg}")


def build_launch(
    *,
    agent: str,
    model: str,
    effort: str,
    prompt: str,
    cwd: str,
    job_dir: Path,
    config: dict,
    from_agent: str,
    conversation: str,
    depth: int,
    session_id: str | None,
    auto_approve: bool,
    allow_missing: bool = False,
) -> Launch:
    binary = which(agent, config)
    if not binary:
        override = (config.get("binaries") or {}).get(agent) if config else None
        if allow_missing:
            binary = str(override) if override else agent
        else:
            raise FileNotFoundError(f"binario do agente {agent!r} nao encontrado")
    job_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = job_dir / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    last_message_file = job_dir / "last_message.txt"
    env = os.environ.copy()
    env["AGENTS_SELF"] = agent
    env["AGENTS_FROM"] = from_agent
    env["AGENTS_CONVERSATION"] = conversation
    env["AGENTS_DEPTH"] = str(depth)
    env["AGENTS_HOME"] = str(agents_home())
    env["PYTHONIOENCODING"] = "utf-8"
    prepend_bin_dirs(env)
    builder = {
        "grok": _grok,
        "claude": _claude,
        "gemini": _gemini,
        "codex": _codex,
    }[agent]
    launch = builder(
        binary=binary,
        model=model,
        effort=effort,
        prompt=prompt,
        prompt_file=prompt_file,
        last_message_file=last_message_file,
        cwd=cwd,
        env=env,
        session_id=session_id,
        auto_approve=auto_approve,
    )
    check_cmd_shim_argv(binary, launch.argv)
    return launch


def _grok(**kw) -> Launch:
    argv = [kw["binary"]]
    if kw["session_id"]:
        argv += ["--resume", kw["session_id"]]
    argv += [
        "--prompt-file",
        str(kw["prompt_file"]),
        "--model",
        kw["model"],
        "--reasoning-effort",
        kw["effort"],
        "--output-format",
        "json",
        "--cwd",
        kw["cwd"],
    ]
    if kw["auto_approve"]:
        argv.append("--always-approve")
    return Launch("grok", argv, kw["cwd"], kw["env"], kw["prompt_file"])


def _claude(**kw) -> Launch:
    argv = [kw["binary"]]
    if kw["session_id"]:
        argv += ["--resume", kw["session_id"]]
    argv += [
        "-p",
        "--model",
        kw["model"],
        "--effort",
        kw["effort"],
        "--output-format",
        "json",
    ]
    if kw["auto_approve"]:
        argv.append("--dangerously-skip-permissions")
    return Launch("claude", argv, kw["cwd"], kw["env"], kw["prompt_file"], stdin=kw["prompt"])


AGY_MAX_ARGV_CHARS = 30000  # Windows CreateProcess limit is 32767 chars for the whole command line


def _agy_argv(kw: dict, prompt: str) -> list[str]:
    argv = [kw["binary"]]
    if kw["session_id"]:
        argv += ["--conversation", kw["session_id"]]
    argv += [
        "-p",
        prompt,
        "--model",
        kw["model"],
        "--effort",
        kw["effort"],
        "--output-format",
        "json",
    ]
    if kw["auto_approve"]:
        argv.append("--dangerously-skip-permissions")
    return argv


def _gemini(**kw) -> Launch:
    name = binary_name(kw["binary"])
    stdin = None
    if name == "agy":
        argv = _agy_argv(kw, kw["prompt"])
        # Never put prompt text in the argv of a .cmd/.bat/.ps1 (cmd.exe would parse it).
        unsafe_binary = _windows() and Path(kw["binary"]).suffix.lower() != ".exe"
        if unsafe_binary or len(subprocess.list2cmdline(argv)) > AGY_MAX_ARGV_CHARS:
            pointer = (
                "Leia o arquivo abaixo e siga integralmente as instrucoes contidas nele "
                f"(ele e a tarefa completa): {Path(kw['prompt_file']).resolve()}"
            )
            argv = _agy_argv(kw, pointer)
    else:
        argv = [kw["binary"]]
        if kw["session_id"]:
            argv += ["--resume", kw["session_id"]]
        argv += [
            "-m",
            kw["model"],
            "-o",
            "json",
        ]
        if kw["auto_approve"]:
            argv.append("-y")
        stdin = kw["prompt"]
    return Launch("gemini", argv, kw["cwd"], kw["env"], kw["prompt_file"], stdin=stdin)


def _codex(**kw) -> Launch:
    last = kw["last_message_file"]
    kw["env"]["CODEX_INTERNAL_ORIGINATOR_OVERRIDE"] = "codex-app-server"
    argv = [kw["binary"], "exec"]
    if kw["session_id"]:
        argv += ["resume", kw["session_id"]]
    argv += [
        "-m",
        kw["model"],
        "-c",
        f"model_reasoning_effort={kw['effort']}",
    ]
    # No -C: the working directory comes from Launch.cwd (and `codex exec resume` rejects -C).
    argv += [
        "--skip-git-repo-check",
        "--thread-source",
        "user",
        "--json",
        "--output-last-message",
        str(last),
    ]
    if kw["auto_approve"]:
        argv.append("--dangerously-bypass-approvals-and-sandbox")
    argv.append("-")
    return Launch(
        "codex",
        argv,
        kw["cwd"],
        kw["env"],
        kw["prompt_file"],
        last_message_file=last,
        stdin=kw["prompt"],
    )


def parse_payload(stdout: str) -> dict | None:
    raw = (stdout or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _last_json_object(raw)
    return data if isinstance(data, dict) else None


CODEX_ERROR_TYPES = ("turn.failed", "error")
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_BANNER_RE = re.compile(r"session[ _]id\s*:\s*([A-Za-z0-9][A-Za-z0-9_\-]*)", re.IGNORECASE)


def output_is_error(stdout: str, agent: str | None = None) -> bool:
    data = parse_payload(stdout)
    if data:
        if data.get("is_error") is True:
            return True
        if data.get("terminal_reason") == "api_error":
            return True
    if agent is not None and agent != "codex":
        return False
    return _codex_stream_failed(_json_events(stdout))


def _codex_failure_message(events: list[dict]) -> str:
    for event in reversed(events):
        kind = _event_type(event)
        if kind == "turn.failed":
            err = event.get("error")
            message = err.get("message") if isinstance(err, dict) else err
        elif kind == "error":
            message = event.get("message")
        else:
            continue
        if isinstance(message, str) and message.strip():
            return message.strip()
    return ""


def failure_text(agent: str, stdout: str, stderr: str | None = None) -> str:
    """Best error message of a failed run. For codex, the stream's own
    turn.failed/error message; otherwise "" (callers fall back to text/stderr)."""
    if agent != "codex":
        return ""
    return _codex_failure_message(_json_events(stdout))


RESUME_MISSING_PATTERNS = (
    "no rollout found",
    "session not found",
    "no conversation found",
    "conversation not found",
    "could not find session",
    "invalid session",
    "thread not found",
)


def resume_missing(agent: str, stdout: str | None, stderr: str | None, text: str | None = None) -> bool:
    """True when a failed resume was rejected because the harness no longer has that session."""
    blob = "\n".join(part for part in (stdout, stderr, text) if part).lower()
    return any(pattern in blob for pattern in RESUME_MISSING_PATTERNS)


def _json_events(raw: str) -> list[dict]:
    events = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _event_type(event: dict) -> str:
    value = event.get("type")
    return value if isinstance(value, str) else ""


def _is_codex_stream(events: list[dict]) -> bool:
    for event in events:
        kind = _event_type(event)
        if kind == "error" or kind.startswith(("thread.", "turn.", "item.")):
            return True
    return False


def _codex_stream_failed(events: list[dict]) -> bool:
    failed = False
    for event in events:
        kind = _event_type(event)
        if kind == "turn.failed":
            return True
        if kind == "error":
            failed = True
        elif kind == "turn.completed":
            # An error followed by a completed turn was a transient (e.g. reconnect) notice.
            failed = False
    return failed


def _codex_stream_text(events: list[dict]) -> str:
    last = ""
    for event in events:
        if _event_type(event) != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                last = text.strip()
    if last:
        return last
    for event in reversed(events):
        kind = _event_type(event)
        if kind == "error":
            message = event.get("message")
        elif kind == "turn.failed":
            err = event.get("error")
            message = err.get("message") if isinstance(err, dict) else err
        else:
            continue
        if isinstance(message, str) and message.strip():
            return message.strip()
    return ""


def extract_text(
    agent: str,
    stdout: str,
    last_message_file: Path | None = None,
    stderr: str | None = None,
) -> tuple[str, str | None]:
    session = _session_from_stdout(stdout) or _session_from_banner(stderr or "")
    if last_message_file and last_message_file.is_file():
        text = last_message_file.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text, session
    raw = (stdout or "").strip()
    if not raw:
        return "", session
    events = _json_events(raw)
    if agent == "codex" and _is_codex_stream(events):
        return _codex_stream_text(events), session
    data = parse_payload(raw)
    if isinstance(data, dict):
        text = _text_from_obj(data)
        session = _session_from_obj(data) or session
        if text:
            return text, session
    return raw, session


def _text_from_obj(data: dict) -> str:
    for key in ("text", "result", "response", "message", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _session_from_obj(data: dict) -> str | None:
    for key in ("sessionId", "session_id", "conversationId", "conversation_id", "thread_id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = data.get("id")
    if isinstance(value, str) and _looks_like_session_id(value):
        return value.strip()
    return None


def _looks_like_session_id(value: str) -> bool:
    text = value.strip()
    if not text or text.startswith("msg_") or text.startswith("item_"):
        return False
    if text.startswith("01") and len(text) >= 20:
        return True
    return text.count("-") >= 4 and len(text) >= 32


def _session_from_stdout(stdout: str) -> str | None:
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            found = _session_from_obj(obj)
            if found:
                return found
            inner = obj.get("item") or obj.get("thread") or obj.get("payload") or {}
            if isinstance(inner, dict):
                found = _session_from_obj(inner)
                if found:
                    return found
    return _session_from_banner(stdout)


def _session_from_banner(text: str) -> str | None:
    for line in _ANSI_RE.sub("", text or "").splitlines():
        if line.lstrip().startswith("{"):
            continue
        match = _BANNER_RE.search(line)
        if match:
            return match.group(1)
    return None


def _last_json_object(raw: str) -> dict | None:
    last = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            last = obj
    if last is None:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start : end + 1])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                return None
    return last


def run_launch(launch: Launch, timeout: int) -> subprocess.CompletedProcess:
    popen_kwargs: dict = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    stdin_text = launch.stdin if launch.stdin else None
    proc = subprocess.Popen(
        launch.argv,
        cwd=launch.cwd,
        env=launch.env,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(
            input=stdin_text,
            timeout=None if timeout == 0 else timeout,
        )
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        procs.kill_tree(proc.pid)
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        raise
    return subprocess.CompletedProcess(launch.argv, proc.returncode, stdout, stderr)
