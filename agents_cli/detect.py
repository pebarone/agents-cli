from __future__ import annotations

import os
from pathlib import Path

from agents_cli import ALIASES
from agents_cli import procs


PARENT_MAP = {
    "grok": "grok",
    "claude": "claude",
    "codex": "codex",
    "agy": "gemini",
    "antigravity": "gemini",
    "gemini": "gemini",
    "chatgpt": "codex",
}


def _agent_from_name(name: str) -> str | None:
    return PARENT_MAP.get(Path(name).stem.lower())


def whoami() -> str:
    explicit = os.environ.get("AGENTS_SELF", "").strip().lower()
    if explicit:
        return ALIASES.get(explicit, explicit)
    try:
        names = procs.ancestor_names()
    except Exception:
        names = []
    for name in names:
        agent = _agent_from_name(name)
        if agent:
            return agent
    if os.environ.get("CLAUDECODE"):
        return "claude"
    if os.environ.get("GEMINI_CLI"):
        return "gemini"
    return "unknown"


def parent_process_name() -> str | None:
    try:
        names = procs.ancestor_names(limit=1)
    except Exception:
        return None
    return names[0] if names else None
