"""Read-only checks for Codex: Chrome control through the ChatGPT extension, and session storage."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from agents_cli import procs

HOST_NAME = "com.openai.codexextension"
HOST_REG_KEY = "Software\\Google\\Chrome\\NativeMessagingHosts\\" + HOST_NAME
EXTENSION_ORIGIN = "chrome-extension://hehggadaopoacecdllhhajmbjkdcmajg/"
PIPE_DIR = "\\\\.\\pipe\\"
PIPE_PREFIX = "codex-browser-use"
NOTE = (
    "o script check-native-host-manifest.js do plugin da falso negativo em Windows pt-BR "
    "('(padrão)' != '(Default)'); ignore esse erro"
)


def _row(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def _safe(name: str, check) -> list[dict]:
    try:
        return check()
    except Exception as exc:  # diagnostics must never raise
        return [_row(name, "warn", f"falha ao verificar: {type(exc).__name__}: {exc}")]


def diagnose() -> list[dict]:
    """Return rows {"name": str, "status": "ok"|"warn"|"fail"|"info", "detail": str}.

    Never modifies files, registry or processes.
    """
    rows: list[dict] = []
    if os.name == "nt":
        rows += _chrome_rows()
    else:
        rows.append(_row("codex-chrome", "info", "somente Windows"))
    rows += _safe("sessoes do codex", _check_sessions)
    return rows


def _chrome_rows() -> list[dict]:
    rows: list[dict] = []
    table_holder: dict = {}

    def table() -> dict:
        if "t" not in table_holder:
            table_holder["t"] = procs.process_table()
        return table_holder["t"]

    rows += _safe("native host", _check_native_host)
    rows += _safe("extension-host", lambda: _check_extension_host(table()))
    rows += _safe("ponte do app (pipe)", _check_pipe)
    rows += _safe("processos codex", lambda: _check_codex_procs(table()))
    rows += _safe("registro de runtimes", _check_registry)
    rows.append(_row("nota", "info", NOTE))
    return rows


def _link_target(path: Path) -> str | None:
    """Target of a symlink or Windows junction/reparse point, else None."""
    is_link = os.path.islink(path)
    if not is_link and os.name == "nt":
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
        is_link = bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    if not is_link:
        return None
    try:
        target = os.readlink(path)
    except OSError:
        return "?"
    return target[4:] if target.startswith("\\\\?\\") else target


def _check_sessions() -> list[dict]:
    name = "sessoes do codex"
    root = Path.home() / ".codex"
    results: list[tuple[str, str]] = []
    for folder in ("sessions", "archived_sessions"):
        path = root / folder
        if not os.path.lexists(path):
            continue
        target = _link_target(path)
        if target is not None and not os.path.exists(path):
            results.append(
                (
                    "fail",
                    f"{folder} aponta para {target}, que esta inacessivel; o Codex nao grava sessoes e o resume "
                    "falha ('no rollout found'). Monte o drive ou aponte para uma pasta local",
                )
            )
        elif not os.path.isdir(path):
            results.append(("warn", f"{folder} existe mas nao e uma pasta"))
        elif not os.access(path, os.W_OK):
            results.append(("warn", f"{folder} nao e gravavel"))
        else:
            suffix = f" (-> {target})" if target is not None else ""
            results.append(("ok", f"{folder} ok{suffix}"))
    if not results:
        return [_row(name, "info", f"{root / 'sessions'} ainda nao existe (o Codex cria no primeiro uso)")]
    for level in ("fail", "warn"):
        if any(status == level for status, _ in results):
            return [_row(name, level, "; ".join(d for status, d in results if status == level))]
    return [_row(name, "ok", "; ".join(detail for _, detail in results))]


def _manifest_path() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HOST_REG_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        return None
    return str(value) if value else None


def _check_native_host() -> list[dict]:
    name = "native host"
    manifest = _manifest_path()
    if not manifest:
        return [_row(name, "fail", f"chave HKCU\\{HOST_REG_KEY} ausente; instale/abra a extensao do Codex no Chrome")]
    if not Path(manifest).is_file():
        return [_row(name, "fail", f"manifest registrado nao existe: {manifest}")]
    try:
        data = json.loads(Path(manifest).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [_row(name, "fail", f"manifest ilegivel: {exc}")]
    exe = data.get("path") if isinstance(data, dict) else None
    if not exe or not Path(str(exe)).is_file():
        return [_row(name, "fail", f"executavel do manifest nao existe: {exe}")]
    origins = data.get("allowed_origins") or []
    if EXTENSION_ORIGIN not in origins:
        return [_row(name, "fail", f"allowed_origins nao contem {EXTENSION_ORIGIN}")]
    return [_row(name, "ok", f"manifest {manifest}; exe e origin da extensao presentes")]


def _check_extension_host(table: dict) -> list[dict]:
    name = "extension-host"
    count = sum(1 for _, exe in table.values() if exe.lower() == "extension-host.exe")
    if count:
        return [_row(name, "ok", f"{count} processo(s) extension-host.exe")]
    return [_row(name, "warn", "Chrome nao abriu o native host (extension-host.exe nao esta rodando)")]


def _check_pipe() -> list[dict]:
    name = "ponte do app (pipe)"
    pipes = [p for p in os.listdir(PIPE_DIR) if p.startswith(PIPE_PREFIX)]
    if pipes:
        return [_row(name, "ok", f"{len(pipes)} pipe(s) {PIPE_PREFIX}*")]
    return [
        _row(
            name,
            "fail",
            "nenhum pipe codex-browser-use; abra o app desktop do Codex "
            "(o CLI sozinho nao expoe a ponte do Chrome) e reinicie o Chrome",
        )
    ]


def _check_codex_procs(table: dict) -> list[dict]:
    count = sum(1 for _, exe in table.values() if exe.lower().startswith("codex") and exe.lower().endswith(".exe"))
    return [_row("processos codex", "info", f"{count} processo(s) codex*.exe")]


def _path_values(paths: dict) -> list[str]:
    values: list[str] = []
    for value in paths.values():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(v for v in value if isinstance(v, str))
    return values


def _check_registry() -> list[dict]:
    name = "registro de runtimes"
    path = Path.home() / ".codex" / "chrome-native-hosts-v2.json"
    if not path.is_file():
        return [_row(name, "warn", "chrome-native-hosts-v2.json ausente")]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [_row(name, "warn", "formato inesperado (sem entries[])")]
    live = complete = both = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        presence = entry.get("presence")
        pid = presence.get("pid") if isinstance(presence, dict) else None
        is_live = isinstance(pid, int) and procs.pid_alive(pid)
        paths = entry.get("paths")
        is_complete = isinstance(paths, dict) and all(os.path.exists(v) for v in _path_values(paths))
        live += is_live
        complete += is_complete
        both += is_live and is_complete
    detail = f"{len(entries)} entrada(s); {live} com pid vivo; {complete} com paths completos; {both} vivas e completas"
    return [_row(name, "ok" if both else "warn", detail)]
