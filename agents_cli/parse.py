from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Iterable
from dataclasses import dataclass

from agents_cli import ALIASES, DEFAULTS, EFFORTS


@dataclass(frozen=True)
class DelegateRequest:
    agent: str
    model: str | None
    effort: str | None
    prompt: str


class ParseError(ValueError):
    pass


def normalize_agent(name: str) -> str:
    key = name.strip().lower()
    if key not in ALIASES:
        known = ", ".join(sorted(set(ALIASES.values())))
        raise ParseError(f"agente desconhecido: {name!r}. use um de: {known}")
    return ALIASES[key]


def normalize_effort(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    key = value.strip().lower()
    if key not in EFFORTS:
        raise ParseError(f"esforco desconhecido: {value!r}. use um de: {', '.join(EFFORTS)}")
    return key


def looks_like_effort(token: str) -> bool:
    return token.strip().lower() in EFFORTS


MODEL_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
KNOWN_MODEL_NAMES = frozenset({"sonnet", "opus", "haiku", "fable", "default"})


FILE_EXTENSIONS = frozenset(
    ".py .md .txt .json .js .ts .tsx .jsx .toml .yaml .yml .ps1 .cmd .bat .sh .html .css .rs .go .java "
    ".c .cpp .h .cs .rb .php .sql .csv .lock .cfg .ini .log".split()
)


def _looks_like_file(token: str) -> bool:
    if Path(token).suffix.lower() in FILE_EXTENSIONS:
        return True
    try:
        return Path(token).exists()
    except (OSError, ValueError):
        return False


def looks_like_model(token: str, known: Iterable[str] = ()) -> bool:
    """Heuristic for free-text tasks: is this first word a model name, not prose or a file?"""
    if not MODEL_SHAPE.fullmatch(token):
        return False
    if _looks_like_file(token):
        return False
    if any(ch.isdigit() or ch in "-./:" for ch in token):
        return True
    lowered = token.lower()
    if lowered in KNOWN_MODEL_NAMES:
        return True
    configured = {str(item).lower() for item in known} | {
        str(conf["model"]).lower() for conf in DEFAULTS.values()
    }
    return lowered in configured


def parse_slash(text: str) -> DelegateRequest:
    raw = text.strip()
    if raw.startswith("/"):
        raw = raw[1:]
    parts = raw.split()
    if not parts:
        raise ParseError("comando vazio")
    if parts[0].lower() == "delegate":
        parts = parts[1:]
    return parse_tokens(parts)


def parse_tokens(
    tokens: list[str],
    prompt_given: bool = False,
    known_models: Iterable[str] = (),
) -> DelegateRequest:
    """`prompt_given`: the prompt comes from --prompt/--prompt-file, so the first
    non-effort token is always the model. Otherwise it is only a model when it
    looks like one (see looks_like_model)."""
    if not tokens:
        raise ParseError("falta o nome do agente")
    agent = normalize_agent(tokens[0])
    rest = tokens[1:]
    model: str | None = None
    effort: str | None = None
    i = 0
    if i < len(rest) and looks_like_effort(rest[i]):
        effort = normalize_effort(rest[i])
        i += 1
    elif i < len(rest) and not rest[i].startswith("-"):
        is_model = prompt_given or looks_like_model(rest[i], known_models)
        if is_model:
            model = rest[i]
            i += 1
            if i < len(rest) and looks_like_effort(rest[i]):
                effort = normalize_effort(rest[i])
                i += 1
    prompt = " ".join(rest[i:]).strip()
    return DelegateRequest(agent=agent, model=model, effort=effort, prompt=prompt)
