from __future__ import annotations

from dataclasses import dataclass

from agents_cli import ALIASES, EFFORTS


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


def parse_tokens(tokens: list[str]) -> DelegateRequest:
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
        model = rest[i]
        i += 1
        if i < len(rest) and looks_like_effort(rest[i]):
            effort = normalize_effort(rest[i])
            i += 1
    prompt = " ".join(rest[i:]).strip()
    return DelegateRequest(agent=agent, model=model, effort=effort, prompt=prompt)
