from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agents_cli.home import agents_home, bin_dirs, is_checkout, launcher_path, repo_root


SKILL_NAME = "delegate"
PACKAGE_INSTALL_HINT = (
    "CLI instalada como pacote (sem checkout). Instale a skill com: npx skills add pebarone/agents-cli -g"
)
PACKAGE_UNINSTALL_HINT = (
    "CLI instalada como pacote (sem checkout): nada para remover aqui. "
    "A skill /delegate foi instalada pelo npx skills; remova-a com: npx skills remove delegate -g"
)


def skill_source() -> Path:
    return repo_root() / "skills" / SKILL_NAME


def skill_destinations() -> list[Path]:
    home = Path.home()
    return [
        agents_home() / "skills" / SKILL_NAME,
        home / ".claude" / "skills" / SKILL_NAME,
        home / ".codex" / "skills" / SKILL_NAME,
        home / ".gemini" / "config" / "skills" / SKILL_NAME,
        home / ".gemini" / "skills" / SKILL_NAME,
        home / ".grok" / "skills" / SKILL_NAME,
    ]


def wrapper_destinations() -> list[Path]:
    paths = []
    for folder in bin_dirs():
        paths.append(folder / "agents.cmd")
        paths.append(folder / "agents")
    return paths


def wrapper_body() -> str:
    root = repo_root()
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        f'set "PYTHONPATH={root};%PYTHONPATH%"\r\n'
        f'python -u "{launcher_path()}" %*\r\n'
    )


def unix_wrapper_body() -> str:
    root = repo_root().as_posix()
    script = launcher_path().as_posix()
    return (
        "#!/bin/sh\n"
        f'export PYTHONPATH="{root}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'exec python -u "{script}" "$@"\n'
    )


def _present(path: Path) -> bool:
    """True when something is at `path`, including a junction or symlink whose target is gone."""
    return os.path.lexists(path)


def link_skill(dest: Path, source: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _present(dest):
        if _same_junction(dest, source):
            return "ok"
        _remove(dest)
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dest), str(source)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"nao consegui criar junction {dest} -> {source}: {completed.stderr or completed.stdout}"
            )
        return "created"
    dest.symlink_to(source, target_is_directory=True)
    return "created"


def _norm(path: Path) -> str:
    text = str(path)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def _same_junction(dest: Path, source: Path) -> bool:
    try:
        target = Path(os.readlink(dest))
        if not target.is_absolute():
            target = dest.parent / target
        return _norm(target) == _norm(source)
    except OSError:
        pass
    try:
        return _norm(dest.resolve()) == _norm(source.resolve())
    except OSError:
        return False


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(path)], check=False)
        if _present(path):
            subprocess.run(["cmd", "/c", "rmdir", "/S", "/Q", str(path)], check=False)
        return
    path.rmdir()


def write_wrapper(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "agents.cmd":
        body = wrapper_body()
        encoded = body.encode("utf-8")
    else:
        body = unix_wrapper_body()
        encoded = body.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return "ok"
    path.write_bytes(encoded)
    return "created"


def install() -> dict:
    if not is_checkout():
        return {"skill": None, "skills": [], "wrappers": [], "hint": PACKAGE_INSTALL_HINT}
    source = skill_source()
    if not (source / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill nao encontrada em {source}")
    skills = []
    for dest in skill_destinations():
        action = link_skill(dest, source)
        skills.append({"path": str(dest), "action": action})
    wrappers = []
    for dest in wrapper_destinations():
        action = write_wrapper(dest)
        wrappers.append({"path": str(dest), "action": action})
    return {"skill": str(source), "skills": skills, "wrappers": wrappers}


def uninstall() -> dict:
    if not is_checkout():
        return {"removed": [], "hint": PACKAGE_UNINSTALL_HINT}
    removed = []
    for dest in skill_destinations() + wrapper_destinations():
        if _present(dest):
            _remove(dest)
            removed.append(str(dest))
    return {"removed": removed}


def doctor_links() -> list[dict]:
    if not is_checkout():
        rows = []
        for dest in skill_destinations():
            present = (dest / "SKILL.md").is_file()
            rows.append({"path": str(dest), "exists": present, "linked": present})
        return rows
    source = skill_source()
    rows = []
    for dest in skill_destinations():
        exists = _present(dest)
        rows.append(
            {
                "path": str(dest),
                "exists": exists,
                "linked": _same_junction(dest, source) if exists else False,
            }
        )
    for dest in wrapper_destinations():
        rows.append(
            {
                "path": str(dest),
                "exists": dest.is_file(),
                "linked": dest.is_file(),
            }
        )
    return rows
