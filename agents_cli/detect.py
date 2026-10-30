from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agents_cli import ALIASES


PARENT_MAP = {
    "grok": "grok",
    "claude": "claude",
    "codex": "codex",
    "agy": "gemini",
    "antigravity": "gemini",
    "gemini": "gemini",
    "chatgpt": "codex",
}


def whoami() -> str:
    explicit = os.environ.get("AGENTS_SELF", "").strip().lower()
    if explicit:
        return ALIASES.get(explicit, explicit)
    parent = parent_process_name()
    if parent:
        stem = Path(parent).stem.lower()
        if stem in PARENT_MAP:
            return PARENT_MAP[stem]
    return "unknown"


def parent_process_name() -> str | None:
    ppid = os.getppid()
    if os.name != "nt":
        try:
            return Path(os.readlink(f"/proc/{ppid}/exe")).name
        except OSError:
            return None
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {ppid} -ErrorAction SilentlyContinue).ProcessName",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = (completed.stdout or "").strip()
    return name or None
