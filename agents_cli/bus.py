from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from agents_cli import PROTOCOL
from agents_cli.home import bus_dir


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str = "") -> str:
    value = uuid.uuid4().hex[:12]
    return f"{prefix}{value}" if prefix else value


def ensure_bus() -> Path:
    root = bus_dir()
    for name in ("conversations", "jobs", "inbox"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def conversation_dir(cid: str) -> Path:
    path = ensure_bus() / "conversations" / cid
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(jid: str) -> Path:
    path = ensure_bus() / "jobs" / jid
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def get_or_create_conversation(cid: str | None, cwd: str, starter: str) -> dict:
    ensure_bus()
    if cid:
        meta_path = conversation_dir(cid) / "meta.json"
        if meta_path.is_file():
            return load_json(meta_path, {})
        meta = {
            "id": cid,
            "protocol": PROTOCOL,
            "cwd": cwd,
            "created_at": _now(),
            "starter": starter,
            "sessions": {},
            "participants": [starter] if starter else [],
        }
        write_json(meta_path, meta)
        return meta
    cid = new_id("c")
    meta = {
        "id": cid,
        "protocol": PROTOCOL,
        "cwd": cwd,
        "created_at": _now(),
        "starter": starter,
        "sessions": {},
        "participants": [starter] if starter else [],
    }
    write_json(conversation_dir(cid) / "meta.json", meta)
    return meta


def save_conversation(meta: dict) -> None:
    write_json(conversation_dir(meta["id"]) / "meta.json", meta)


def append_message(cid: str, payload: dict) -> dict:
    payload = dict(payload)
    payload.setdefault("id", new_id("m"))
    payload.setdefault("ts", _now())
    path = conversation_dir(cid) / "messages.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def read_messages(cid: str) -> list[dict]:
    path = conversation_dir(cid) / "messages.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def session_record(meta: dict, agent: str) -> dict:
    raw = (meta.get("sessions") or {}).get(agent)
    if isinstance(raw, str) and raw:
        return {"id": raw}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def remember_session(
    meta: dict,
    agent: str,
    session_id: str | None,
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    if not session_id:
        return meta
    sessions = dict(meta.get("sessions") or {})
    rec = session_record(meta, agent)
    rec["id"] = session_id
    if model:
        rec["model"] = model
    if effort:
        rec["effort"] = effort
    sessions[agent] = rec
    meta["sessions"] = sessions
    participants = list(meta.get("participants") or [])
    if agent not in participants:
        participants.append(agent)
    meta["participants"] = participants
    save_conversation(meta)
    return meta


def create_job(payload: dict) -> dict:
    jid = payload.get("id") or new_id("j")
    payload["id"] = jid
    payload.setdefault("status", "queued")
    payload.setdefault("created_at", _now())
    write_json(job_dir(jid) / "meta.json", payload)
    return payload


def save_job(payload: dict) -> None:
    write_json(job_dir(payload["id"]) / "meta.json", payload)


def load_job(jid: str) -> dict:
    path = job_dir(jid) / "meta.json"
    if not path.is_file():
        raise FileNotFoundError(f"job nao encontrado: {jid}")
    return load_json(path, {})


def enqueue_inbox(agent: str, payload: dict) -> None:
    path = ensure_bus() / "inbox" / f"{agent}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_inbox(agent: str, consume: bool = False) -> list[dict]:
    path = ensure_bus() / "inbox" / f"{agent}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    if consume:
        path.write_text("", encoding="utf-8")
    return rows


def list_conversations() -> list[dict]:
    root = ensure_bus() / "conversations"
    rows = []
    if not root.is_dir():
        return rows
    for path in sorted(root.iterdir()):
        meta = path / "meta.json"
        if meta.is_file():
            rows.append(load_json(meta, {}))
    return rows


def list_jobs() -> list[dict]:
    root = ensure_bus() / "jobs"
    rows = []
    if not root.is_dir():
        return rows
    for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        meta = path / "meta.json"
        if meta.is_file():
            rows.append(load_json(meta, {}))
    return rows
