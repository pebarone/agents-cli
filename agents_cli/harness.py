from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_cli import AGENTS
from agents_cli.home import agents_home, prepend_bin_dirs


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
    return builder(
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
        kw["prompt"],
        "--model",
        kw["model"],
        "--effort",
        kw["effort"],
        "--output-format",
        "json",
    ]
    if kw["auto_approve"]:
        argv.append("--dangerously-skip-permissions")
    return Launch("claude", argv, kw["cwd"], kw["env"], kw["prompt_file"])


def _gemini(**kw) -> Launch:
    name = binary_name(kw["binary"])
    argv = [kw["binary"]]
    if name == "agy":
        if kw["session_id"]:
            argv += ["--conversation", kw["session_id"]]
        argv += [
            "-p",
            kw["prompt"],
            "--model",
            kw["model"],
            "--effort",
            kw["effort"],
            "--output-format",
            "json",
        ]
        if kw["auto_approve"]:
            argv.append("--dangerously-skip-permissions")
    else:
        if kw["session_id"]:
            argv += ["--resume", kw["session_id"]]
        argv += [
            "-p",
            kw["prompt"],
            "-m",
            kw["model"],
            "-o",
            "json",
        ]
        if kw["auto_approve"]:
            argv.append("-y")
    return Launch("gemini", argv, kw["cwd"], kw["env"], kw["prompt_file"])


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
        "-C",
        kw["cwd"],
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


def output_is_error(stdout: str) -> bool:
    data = parse_payload(stdout)
    if not data:
        return False
    if data.get("is_error") is True:
        return True
    if data.get("terminal_reason") == "api_error":
        return True
    return False


def extract_text(
    agent: str,
    stdout: str,
    last_message_file: Path | None = None,
    stderr: str | None = None,
) -> tuple[str, str | None]:
    session = _session_from_stdout(stdout) or _session_from_banner(stderr or "")
    if last_message_file and last_message_file.is_file():
        text = last_message_file.read_text(encoding="utf-8").strip()
        if text:
            return text, session
    raw = (stdout or "").strip()
    if not raw:
        return "", session
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
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("session id:"):
            value = stripped.split(":", 1)[1].strip()
            return value or None
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
    return subprocess.run(
        launch.argv,
        cwd=launch.cwd,
        env=launch.env,
        input=launch.stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=None if timeout == 0 else timeout,
        check=False,
    )
