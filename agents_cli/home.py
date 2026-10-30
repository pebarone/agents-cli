from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def agents_home() -> Path:
    raw = os.environ.get("AGENTS_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".agents"


def bus_dir() -> Path:
    return agents_home() / "bus"


def config_path() -> Path:
    return agents_home() / "config.json"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def launcher_path() -> Path:
    return repo_root() / "agents.py"


def is_checkout() -> bool:
    """True when running from a dev checkout; False when installed as a package."""
    return launcher_path().is_file()


def cli_command() -> str:
    if is_checkout():
        return f'python -u "{launcher_path()}"'
    if shutil.which("agents"):
        return "agents"
    exe = sys.executable
    if any(ch.isspace() for ch in exe):
        return f'"{exe}" -u -m agents_cli'
    return f"{exe} -u -m agents_cli"


def bin_dirs() -> list[Path]:
    return [
        Path.home() / ".local" / "bin",
        agents_home() / "bin",
    ]


def prepend_bin_dirs(env: dict[str, str]) -> dict[str, str]:
    extra = [str(path) for path in bin_dirs()]
    current = env.get("PATH") or os.environ.get("PATH") or ""
    parts = extra + [p for p in current.split(os.pathsep) if p and p not in extra]
    env["PATH"] = os.pathsep.join(parts)
    return env
