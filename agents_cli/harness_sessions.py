"""Read the sessions that already exist in each harness's own store (read-only).

Used by `agents sessions` and `agents resume` to continue a session the user
started (and maybe named) outside the bus. Nothing here writes to those stores.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from agents_cli import harness
from agents_cli.parse import ParseError

NAME_MAX = 120
MAX_CANDIDATES = 10
EXTENDED_PREFIX = "\\\\?\\"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CODEX_STATE_RE = re.compile(r"state_(\d+)\.sqlite")
CODEX_ROLLOUT_RE = re.compile(r"rollout-.+\.jsonl(\.zst)?")  # older rollouts are compressed to .zst
# An id goes into argv and into the bus: no leading dash, spaces or control characters.
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
ID_PREFIX_RE = re.compile(r"[0-9a-fA-F-]{6,}")  # only hex-like text is tried as an id prefix
BUS_PROMPT_START = "[agents-bus protocol="
BUS_MESSAGE_MARK = "--- message ---"
BUS_HISTORY_END = "--- end of earlier messages ---"
NO_TITLE = "(sem titulo)"


class SessionLookupError(ParseError):
    """No session, or more than one, matches the name/id the user gave."""


@dataclass
class HarnessSession:
    agent: str
    id: str
    name: str
    cwd: str | None
    updated: float  # epoch seconds
    resumable: bool
    note: str | None = None
    archived: bool = False


def _home() -> Path:
    return Path.home()


# --- helpers ----------------------------------------------------------------


def _clean_name(*candidates) -> str:
    """First non-empty candidate as one line, at most NAME_MAX chars."""
    for value in candidates:
        if isinstance(value, str):
            text = " ".join(value.split())
            if text:
                return text[:NAME_MAX].rstrip()
    return NO_TITLE


def _prompt_name(value):
    """A first user prompt as a name candidate. A bus message begins with the protocol header,
    so it is named by its own first line: the text after the earlier-messages block if there is one."""
    if not isinstance(value, str) or not value.lstrip().startswith(BUS_PROMPT_START):
        return value
    end = value.rfind(BUS_HISTORY_END)
    if end >= 0:
        body = value[end + len(BUS_HISTORY_END) :]
    else:
        mark = value.find(BUS_MESSAGE_MARK)
        body = value[mark + len(BUS_MESSAGE_MARK) :] if mark >= 0 else ""
    for line in body.splitlines():
        if line.strip():
            return f"[bus] {line}"
    return None


def _strip_extended(path) -> str | None:
    if not isinstance(path, str):
        return None
    text = path.strip()
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[len("\\\\?\\UNC\\") :]
    elif text.startswith(EXTENDED_PREFIX):
        text = text[len(EXTENDED_PREFIX) :]
    return text or None


def _parse_iso(value) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip())
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        epoch = stamp.timestamp()
    except (ValueError, OverflowError, OSError):  # e.g. agy's year-0001 "never" placeholder
        return None
    return epoch if epoch > 0 else None


def _epoch(value) -> float | None:
    """Epoch seconds from an int/float given in seconds or milliseconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value) / 1000.0 if value > 1e11 else float(value)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _table_rows(path: Path, table: str, wanted: tuple[str, ...]) -> list[dict]:
    """Rows of `table` with the `wanted` columns that exist (read-only)."""
    with closing(_ro_connect(path)) as conn:
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        cols = [col for col in wanted if col in have]
        if not cols:
            return []
        cursor = conn.execute(f"SELECT {', '.join(cols)} FROM {table}")
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _file_uri_to_path(uri) -> str | None:
    if not isinstance(uri, str) or not uri.lower().startswith("file:"):
        return None
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if re.match(r"^/[A-Za-z]:", path):
        return path[1].upper() + path[2:].replace("/", "\\")
    if parsed.netloc and parsed.netloc != "localhost":
        return "\\\\" + parsed.netloc + path.replace("/", "\\")
    return path or None


# --- codex ------------------------------------------------------------------


def _codex_state_db(root: Path) -> Path | None:
    best: tuple[int, Path] | None = None
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        match = CODEX_STATE_RE.fullmatch(entry.name)
        if match and entry.is_file() and (best is None or int(match.group(1)) > best[0]):
            best = (int(match.group(1)), entry)
    return best[1] if best else None


def _codex_rollout_note(has_rollout: bool) -> str | None:
    return None if has_rollout else "o Codex nao tem o historico (rollout) desta conversa neste PC"


def _rollout_exists(path: str | None) -> bool:
    """A Codex rollout counts when its file is there, or when it was compressed to `<file>.zst`."""
    if not path:
        return False
    for candidate in (path, path + ".zst"):
        try:
            if Path(candidate).is_file():
                return True
        except OSError:
            continue
    return False


def _codex_from_db(db: Path) -> list[HarnessSession]:
    wanted = (
        "id",
        "name",
        "title",
        "first_user_message",
        "preview",
        "cwd",
        "updated_at_ms",
        "updated_at",
        "archived",
        "rollout_path",
        "thread_source",
    )
    out = []
    for row in _table_rows(db, "threads", wanted):
        sid = row.get("id")
        if not isinstance(sid, str) or not sid:
            continue
        if row.get("thread_source") == "subagent":
            continue
        has_rollout = _rollout_exists(_strip_extended(row.get("rollout_path")))
        updated = _epoch(row.get("updated_at_ms")) or _epoch(row.get("updated_at")) or 0.0
        out.append(
            HarnessSession(
                agent="codex",
                id=sid,
                name=_clean_name(
                    row.get("name"),
                    _prompt_name(row.get("title")),
                    _prompt_name(row.get("first_user_message")),
                    _prompt_name(row.get("preview")),
                ),
                cwd=_strip_extended(row.get("cwd")),
                updated=updated,
                resumable=has_rollout,
                note=_codex_rollout_note(has_rollout),
                archived=bool(row.get("archived")),
            )
        )
    return out


def _codex_rollout_ids(root: Path) -> set[str]:
    ids = set()
    for path in (root / "sessions").rglob("rollout-*"):
        if not CODEX_ROLLOUT_RE.fullmatch(path.name):
            continue
        match = UUID_RE.search(path.name.split(".jsonl", 1)[0][-36:])
        if match:
            ids.add(match.group(0).lower())
    return ids


def _codex_from_index(root: Path) -> list[HarnessSession]:
    index = root / "session_index.jsonl"
    latest: dict[str, dict] = {}
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
            latest[row["id"]] = row
    if not latest:
        return []
    rollouts = _codex_rollout_ids(root)
    out = []
    for sid, row in latest.items():
        has_rollout = sid.lower() in rollouts
        out.append(
            HarnessSession(
                agent="codex",
                id=sid,
                name=_clean_name(row.get("thread_name")),
                cwd=None,
                updated=_parse_iso(row.get("updated_at")) or 0.0,
                resumable=has_rollout,
                note=_codex_rollout_note(has_rollout),
            )
        )
    return out


def _read_codex() -> list[HarnessSession]:
    root = _home() / ".codex"
    db = _codex_state_db(root)
    if db is not None:
        try:
            return _codex_from_db(db)
        except (sqlite3.Error, OSError, ValueError):
            pass
    if (root / "session_index.jsonl").is_file():
        return _codex_from_index(root)
    return []


# --- claude -----------------------------------------------------------------


def _claude_text(obj: dict) -> str:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return " ".join(parts)
    return ""


def _scan_claude_file(path: Path) -> dict:
    """Titles, cwd and first prompt of one transcript, streaming line by line.
    Only lines that can matter are parsed."""
    custom = ai = cwd = prompt_good = prompt_any = None
    has_user = False
    with path.open("rb") as handle:
        for line in handle:
            is_title = b'"custom-title"' in line or b'"ai-title"' in line
            need_head = cwd is None or prompt_good is None
            if not is_title and not (need_head and (b'"cwd"' in line or b'"user"' in line)):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            if kind == "custom-title" and isinstance(obj.get("customTitle"), str):
                custom = obj["customTitle"]
            elif kind == "ai-title" and isinstance(obj.get("aiTitle"), str):
                ai = obj["aiTitle"]
            if cwd is None and isinstance(obj.get("cwd"), str) and obj["cwd"]:
                cwd = obj["cwd"]
            if kind == "user" and not obj.get("isMeta"):
                has_user = True
                if prompt_good is None:
                    text = " ".join((_prompt_name(_claude_text(obj)) or "").split())
                    if text:
                        prompt_any = prompt_any or text
                        if not text.startswith("<"):
                            prompt_good = text
    return {"custom": custom, "ai": ai, "cwd": cwd, "prompt": prompt_good or prompt_any, "has_user": has_user}


def _read_claude() -> list[HarnessSession]:
    root = _home() / ".claude" / "projects"
    out = []
    for path in root.glob("*/*.jsonl"):
        if not UUID_RE.fullmatch(path.stem):
            continue
        try:
            info = _scan_claude_file(path)
        except OSError:
            continue
        has_content = info["has_user"] or bool(info["custom"] or info["ai"])
        out.append(
            HarnessSession(
                agent="claude",
                id=path.stem,
                name=_clean_name(info["custom"], info["ai"], info["prompt"]),
                cwd=_strip_extended(info["cwd"]),
                updated=_mtime(path),
                resumable=has_content,
                note=None if has_content else "sessao vazia, sem mensagens",
            )
        )
    return out


# --- grok -------------------------------------------------------------------


def _read_grok() -> list[HarnessSession]:
    root = _home() / ".grok" / "sessions"
    out = []
    for path in root.glob("*/*/summary.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        info = data.get("info") if isinstance(data.get("info"), dict) else {}
        sid = info.get("id") if isinstance(info.get("id"), str) and info.get("id") else path.parent.name
        updated = (
            _parse_iso(data.get("last_active_at"))
            or _parse_iso(data.get("updated_at"))
            or _parse_iso(data.get("created_at"))
            or _mtime(path)
        )
        out.append(
            HarnessSession(
                agent="grok",
                id=sid,
                name=_clean_name(data.get("generated_title"), data.get("session_summary")),
                cwd=_strip_extended(info.get("cwd")),
                updated=updated,
                resumable=True,
            )
        )
    return out


# --- agy (gemini) -----------------------------------------------------------


def _first_workspace(raw) -> str | None:
    try:
        uris = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if isinstance(uris, list):
        for uri in uris:
            path = _file_uri_to_path(uri)
            if path:
                return path
    return None


def _read_agy() -> list[HarnessSession]:
    root = _home() / ".gemini" / "antigravity-cli"
    db = root / "conversation_summaries.db"
    if not db.is_file():
        return []
    wanted = ("conversation_id", "title", "preview", "last_modified_time", "workspace_uris")
    out = []
    for row in _table_rows(db, "conversation_summaries", wanted):
        cid = row.get("conversation_id")
        if not isinstance(cid, str) or not cid:
            continue
        has_db = (root / "conversations" / f"{cid}.db").is_file()
        out.append(
            HarnessSession(
                agent="gemini",
                id=cid,
                name=_clean_name(row.get("title"), row.get("preview")),
                cwd=_first_workspace(row.get("workspace_uris")),
                updated=_parse_iso(row.get("last_modified_time")) or 0.0,
                resumable=has_db,
                note=None if has_db else "o agy nao tem o banco desta conversa neste PC",
            )
        )
    return out


# --- public api -------------------------------------------------------------


def _gemini_reader(config: dict | None):
    path = harness.which("gemini", config)
    if not path:
        raise ParseError("harness de gemini nao encontrado. rode: agents doctor")
    if harness.binary_name(path) != "agy":
        raise ParseError("o Gemini CLI nao permite retomar sessao por id; use o agy")
    return _read_agy


def list_sessions(agent: str, *, include_archived: bool = False, config: dict | None = None) -> list[HarnessSession]:
    """Sessions of `agent` found in its own store, newest first. An unreadable or
    missing store yields []; only an unsupported agent raises ParseError."""
    if agent == "gemini":
        reader = _gemini_reader(config)
    elif agent == "codex":
        reader = _read_codex
    elif agent == "claude":
        reader = _read_claude
    elif agent == "grok":
        reader = _read_grok
    else:
        raise ParseError(f"agente sem sessoes para listar: {agent}")
    try:
        sessions = reader()
    except Exception:  # noqa: BLE001 - a broken third-party store must not break the CLI
        return []
    sessions = [item for item in sessions if ID_RE.fullmatch(item.id)]  # unsafe ids are not listed
    if not include_archived:
        sessions = [item for item in sessions if not item.archived]
    sessions.sort(key=lambda item: (item.updated, item.id), reverse=True)
    return sessions


def normalize(text: str) -> str:
    """Case- and accent-insensitive form used to compare names."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def format_when(epoch: float) -> str:
    if not epoch:
        return "----------------"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def candidate_line(item: HarnessSession) -> str:
    return f"  {item.id[:8]}  {format_when(item.updated)}  {item.name}  ({item.cwd or '-'})"


def _ambiguous(agent: str, query: str, matches: list[HarnessSession]) -> SessionLookupError:
    lines = [candidate_line(item) for item in matches[:MAX_CANDIDATES]]
    if len(matches) > MAX_CANDIDATES:
        lines.append(f"  ... e mais {len(matches) - MAX_CANDIDATES}")
    return SessionLookupError(
        f'"{query}" corresponde a {len(matches)} sessoes do {agent}; passe o id:\n' + "\n".join(lines)
    )


def _pick_one(agent: str, raw: str, matches: list[HarnessSession]) -> HarnessSession:
    """The only match, or the only resumable one among several; otherwise the choice is ambiguous."""
    if len(matches) == 1:
        return matches[0]
    usable = [item for item in matches if item.resumable]
    if len(usable) == 1:
        return usable[0]
    raise _ambiguous(agent, raw, matches)


def find_session(
    agent: str, query: str, *, include_archived: bool = True, config: dict | None = None
) -> HarnessSession:
    """Resolve an id, a name or (part of a name) to one session, or raise SessionLookupError.
    Order: exact id, exact name, id prefix (hex-like text only), part of the name."""
    raw = (query or "").strip()
    wanted = normalize(raw)
    if not wanted:
        raise ParseError("falta o nome ou o id da sessao")
    sessions = list_sessions(agent, include_archived=include_archived, config=config)
    lowered = raw.lower()

    for item in sessions:
        if item.id.lower() == lowered:
            return item

    exact = [item for item in sessions if normalize(item.name) == wanted]
    if exact:
        return _pick_one(agent, raw, exact)

    if ID_PREFIX_RE.fullmatch(raw):
        prefixed = [item for item in sessions if item.id.lower().startswith(lowered)]
        if len(prefixed) == 1:
            return prefixed[0]
        if len(prefixed) > 1:
            raise _ambiguous(agent, raw, prefixed)

    partial = [item for item in sessions if wanted in normalize(item.name)]
    if partial:
        return _pick_one(agent, raw, partial)

    raise SessionLookupError(
        f'nenhuma sessao do {agent} corresponde a "{raw}". veja as sessoes com: agents sessions {agent} <filtro>'
    )


def matches_filter(item: HarnessSession, query: str) -> bool:
    """`agents sessions` filter: normalized substring over name, id and cwd."""
    wanted = normalize(query)
    if not wanted:
        return True
    return any(wanted in normalize(value) for value in (item.name, item.id, item.cwd or ""))
