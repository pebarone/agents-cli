from __future__ import annotations

import json
from pathlib import Path

from agents_cli import DEFAULTS
from agents_cli.home import config_path


DEFAULT_CONFIG = {
    "timeout_sec": 900,
    "max_depth": 4,
    "auto_approve": True,
    "binaries": {
        "grok": None,
        "claude": None,
        "gemini": None,
        "codex": None,
    },
    "defaults": DEFAULTS,
}


def load_config() -> dict:
    path = config_path()
    data = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"config invalido em {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"config invalido em {path}: esperado objeto")
        merge(data, raw)
    return data


def save_config(data: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def default_model(agent: str, config: dict | None = None) -> str:
    cfg = config or load_config()
    return str(cfg.get("defaults", {}).get(agent, {}).get("model") or DEFAULTS[agent]["model"])


def default_effort(agent: str, config: dict | None = None) -> str:
    cfg = config or load_config()
    return str(cfg.get("defaults", {}).get(agent, {}).get("effort") or DEFAULTS[agent]["effort"])
