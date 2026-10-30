from __future__ import annotations

import calendar
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from agents_cli import PROTOCOL
from agents_cli.home import bus_dir
from agents_cli.parse import ParseError
from agents_cli.procs import pid_alive

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
LOCK_TIMEOUT_SEC = 10.0
QUEUED_GRACE_SEC = 30
LIVE_STATUSES = ("queued", "running")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_iso() -> str:
    return _now()


def parse_ts(value) -> float | None:
    """Epoch seconds for a bus timestamp, or None."""
    if not isinstance(value, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def new_id(prefix: str = "") -> str:
    value = uuid.uuid4().hex[:12]
    return f"{prefix}{value}" if prefix else value


def validate_id(value, kind: str = "id") -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ParseError(f"{kind} invalido: {value!r}")
    return value


# --- cross-process lock -----------------------------------------------------

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def file_lock(target: Path, timeout: float = LOCK_TIMEOUT_SEC):
    """Exclusive lock on a sidecar `<target>.lock` (not re-entrant)."""
    lock_path = target.with_name(target.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o666)
    try:
        deadline = time.monotonic() + timeout
        delay = 0.005
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"nao consegui o lock de {target.name} em {timeout:.0f}s") from None
                time.sleep(delay)
                delay = min(delay * 2, 0.1)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# --- paths ------------------------------------------------------------------


def ensure_bus() -> Path:
    root = bus_dir()
    for name in ("conversations", "jobs", "inbox"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def conversation_dir(cid: str, create: bool = True) -> Path:
    validate_id(cid, "id de conversa")
    path = ensure_bus() / "conversations" / cid
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(jid: str, create: bool = True) -> Path:
    validate_id(jid, "id de job")
    path = ensure_bus() / "jobs" / jid
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _inbox_path(agent: str) -> Path:
    validate_id(agent, "nome de agente")
    return ensure_bus() / "inbox" / f"{agent}.jsonl"


# --- json helpers -----------------------------------------------------------


def load_json(path: Path, default):
    text = None
    for attempt in range(3):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return default
        except UnicodeDecodeError:
            return default
        except OSError:
            # Windows: file briefly held by os.replace in another process.
            if attempt == 2:
                return default
            time.sleep(0.01)
    try:
        data = json.loads(text)
    except ValueError:
        return default
    if isinstance(default, dict) and not isinstance(data, dict):
        return default
    return data


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.02)


def _read_jsonl(path: Path) -> list[dict]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _append_jsonl(path: Path, payload: dict) -> None:
    """Append one line. Caller must hold the lock for `path`."""
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with path.open("rb") as probe:
            probe.seek(0, os.SEEK_END)
            if probe.tell():
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    data = b"\n" + data
    except OSError:
        pass
    with path.open("ab") as handle:
        handle.write(data)


# --- conversations ----------------------------------------------------------


def _new_meta(cid: str, cwd: str, starter: str) -> dict:
    return {
        "id": cid,
        "protocol": PROTOCOL,
        "cwd": cwd,
        "created_at": _now(),
        "starter": starter,
        "sessions": {},
        "participants": [starter] if starter else [],
    }


def _meta_path(cid: str) -> Path:
    return conversation_dir(cid) / "meta.json"


def load_conversation(cid: str) -> dict | None:
    """Meta of an existing conversation, or None. Never creates anything."""
    path = conversation_dir(cid, create=False) / "meta.json"
    if not path.is_file():
        return None
    meta = load_json(path, None)
    return meta if isinstance(meta, dict) and meta else None


def ensure_conversation(cid: str | None, cwd: str, starter: str) -> tuple[dict, bool]:
    """Return (meta, created). Creates the conversation if missing or corrupt."""
    ensure_bus()
    if cid:
        validate_id(cid, "id de conversa")
    else:
        cid = new_id("c")
    meta_path = _meta_path(cid)
    with file_lock(meta_path):
        existing = load_json(meta_path, None) if meta_path.is_file() else None
        if isinstance(existing, dict) and existing:
            existing.setdefault("id", cid)
            return existing, False
        meta = _new_meta(cid, cwd, starter)
        write_json(meta_path, meta)
        return meta, True


def get_or_create_conversation(cid: str | None, cwd: str, starter: str) -> dict:
    return ensure_conversation(cid, cwd, starter)[0]


def _merge_participant(meta: dict, agent: str) -> None:
    participants = list(meta.get("participants") or [])
    if agent and agent not in participants:
        participants.append(agent)
    meta["participants"] = participants


def add_participant(cid: str, agent: str) -> dict | None:
    """Add `agent` to participants, re-reading meta under the lock."""
    meta_path = _meta_path(cid)
    with file_lock(meta_path):
        disk = load_json(meta_path, None)
        if not isinstance(disk, dict) or not disk:
            return None
        _merge_participant(disk, agent)
        write_json(meta_path, disk)
        return disk


def append_message(cid: str, payload: dict) -> dict:
    payload = dict(payload)
    payload.setdefault("id", new_id("m"))
    payload.setdefault("ts", _now())
    path = conversation_dir(cid) / "messages.jsonl"
    with file_lock(path):
        _append_jsonl(path, payload)
    return payload


def read_messages(cid: str) -> list[dict]:
    path = conversation_dir(cid, create=False) / "messages.jsonl"
    if not path.is_file():
        return []
    return _read_jsonl(path)


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
    """Record the harness session. Re-reads meta from disk and only touches
    `sessions[agent]` and `participants`, so a stale `meta` cannot clobber
    other agents' sessions."""
    if not session_id:
        return meta
    cid = meta["id"]
    meta_path = _meta_path(cid)
    with file_lock(meta_path):
        disk = load_json(meta_path, None)
        if not isinstance(disk, dict) or not disk:
            disk = dict(meta)
        rec = session_record(disk, agent)
        rec["id"] = session_id
        if model:
            rec["model"] = model
        if effort:
            rec["effort"] = effort
        sessions = dict(disk.get("sessions") or {})
        sessions[agent] = rec
        disk["sessions"] = sessions
        _merge_participant(disk, agent)
        write_json(meta_path, disk)
    meta["sessions"] = disk["sessions"]
    meta["participants"] = disk["participants"]
    return meta


def forget_session(cid: str, agent: str, session_id: str) -> bool:
    """Drop `agent`'s session record, only if it still holds `session_id`.
    Returns True when a record was removed."""
    meta_path = _meta_path(cid)
    with file_lock(meta_path):
        disk = load_json(meta_path, None)
        if not isinstance(disk, dict) or not disk:
            return False
        if session_record(disk, agent).get("id") != session_id:
            return False
        sessions = dict(disk.get("sessions") or {})
        sessions.pop(agent, None)
        disk["sessions"] = sessions
        write_json(meta_path, disk)
        return True


# --- jobs -------------------------------------------------------------------


def create_job(payload: dict) -> dict:
    jid = payload.get("id") or new_id("j")
    validate_id(jid, "id de job")
    payload["id"] = jid
    payload.setdefault("status", "queued")
    payload.setdefault("created_at", _now())
    write_json(job_dir(jid) / "meta.json", payload)
    return payload


def save_job(payload: dict) -> None:
    write_json(job_dir(payload["id"]) / "meta.json", payload)


def update_job(jid: str, fields: dict, only_if_status: tuple[str, ...] | None = None) -> dict | None:
    """Merge `fields` into the job meta under the lock. Returns the new meta, or
    None when `only_if_status` is given and the current status is not in it."""
    path = job_dir(jid) / "meta.json"
    with file_lock(path):
        current = load_json(path, {})
        if only_if_status is not None and current.get("status") not in only_if_status:
            return None
        current.update(fields)
        current["id"] = jid
        write_json(path, current)
        return current


def load_job(jid: str) -> dict:
    path = job_dir(jid, create=False) / "meta.json"
    if not path.is_file():
        raise FileNotFoundError(f"job nao encontrado: {jid}")
    return load_json(path, {})


def job_age_sec(job: dict, now: float | None = None) -> float | None:
    stamp = parse_ts(job.get("created_at")) or parse_ts(job.get("started_at"))
    if stamp is None:
        return None
    return max(0.0, (now if now is not None else time.time()) - stamp)


def job_is_stale(job: dict, now: float | None = None) -> bool:
    """A live-looking job whose process is gone."""
    status = job.get("status")
    if status not in LIVE_STATUSES or pid_alive(job.get("pid")):
        return False
    if status == "queued":
        launcher = job.get("launcher_pid")
        if launcher:
            # The spawned launcher is the only thing that can still claim the job.
            return not pid_alive(launcher)
        age = job_age_sec(job, now)
        return age is not None and age > QUEUED_GRACE_SEC
    return True


def reap_job(job: dict, message: str = "processo do agente morreu") -> dict:
    """Mark a stale job as error. Returns the (possibly updated) job meta."""
    if not job_is_stale(job):
        return job
    updated = update_job(
        job["id"],
        {"status": "error", "error": message, "returncode": job.get("returncode") or 1, "finished_at": _now()},
        only_if_status=LIVE_STATUSES,
    )
    if updated is None:
        return load_job(job["id"])
    conversation = updated.get("conversation")
    if conversation and load_conversation(conversation) is not None:
        append_message(
            conversation,
            {
                "from": updated.get("agent") or "unknown",
                "to": updated.get("from") or "unknown",
                "kind": "error",
                "job": updated["id"],
                "text": message,
            },
        )
    return updated


def list_jobs() -> list[dict]:
    """Newest first. Jobs without meta are skipped; corrupt meta is reported."""
    root = ensure_bus() / "jobs"
    rows = []
    entries = []
    for path in root.iterdir():
        try:
            entries.append((path.stat().st_mtime, path))
        except OSError:
            continue
    entries.sort(key=lambda item: item[0], reverse=True)
    for _, path in entries:
        meta = path / "meta.json"
        if not meta.is_file():
            continue
        data = load_json(meta, None)
        if not isinstance(data, dict) or not data:
            data = {"id": path.name, "status": "corrupt"}
        data.setdefault("id", path.name)
        rows.append(data)
    return rows


def running_jobs(conversation: str, agent: str, exclude: str | None = None) -> list[dict]:
    return [
        job
        for job in list_jobs()
        if job.get("conversation") == conversation
        and job.get("agent") == agent
        and job.get("status") == "running"
        and job.get("id") != exclude
        and pid_alive(job.get("pid"))
    ]


# --- inbox ------------------------------------------------------------------


def enqueue_inbox(agent: str, payload: dict) -> dict:
    path = _inbox_path(agent)
    payload = dict(payload)
    payload.setdefault("id", new_id("i"))
    payload.setdefault("ts", _now())
    with file_lock(path):
        _append_jsonl(path, payload)
    return payload


def read_inbox(agent: str, consume: bool = False) -> list[dict]:
    path = _inbox_path(agent)
    with file_lock(path):
        if not path.is_file():
            return []
        rows = _read_jsonl(path)
        if consume:
            path.write_bytes(b"")
    return rows


# --- listings ---------------------------------------------------------------


def list_conversations() -> list[dict]:
    """Oldest first (by created_at, falling back to meta mtime)."""
    root = ensure_bus() / "conversations"
    keyed = []
    for path in root.iterdir():
        meta_path = path / "meta.json"
        if not meta_path.is_file():
            continue
        meta = load_json(meta_path, None)
        if not isinstance(meta, dict) or not meta:
            continue
        meta.setdefault("id", path.name)
        try:
            mtime = meta_path.stat().st_mtime_ns
        except OSError:
            mtime = 0
        stamp = parse_ts(meta.get("created_at"))
        if stamp is None:
            stamp = mtime / 1e9
        keyed.append((stamp, mtime, meta["id"], meta))
    keyed.sort(key=lambda item: item[:3])
    return [item[3] for item in keyed]
