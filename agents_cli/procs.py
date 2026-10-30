from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


def pid_alive(pid: int | None) -> bool:
    """True if a process with this pid is running. Never signals the process."""
    if not pid or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access denied means it exists but belongs to someone else.
        return ctypes.get_last_error() == 5
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


_TH32CS_SNAPPROCESS = 0x00000002


def process_table() -> dict[int, tuple[int, str]]:
    """Map pid -> (ppid, exe basename). Best effort; {} on failure."""
    try:
        if os.name == "nt":
            return _process_table_windows()
        return _process_table_posix()
    except Exception:
        return {}


def _process_table_windows() -> dict[int, tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W))
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot in (-1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return {}
    table: dict[int, tuple[int, str]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = (int(entry.th32ParentProcessID), entry.szExeFile)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return table


def _process_table_posix() -> dict[int, tuple[int, str]]:
    table: dict[int, tuple[int, str]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return table
    for child in proc.iterdir():
        if not child.name.isdigit():
            continue
        pid = int(child.name)
        try:
            stat = (child / "stat").read_text(encoding="utf-8", errors="replace")
            # "pid (comm) S ppid ..." ; comm may contain spaces/parens.
            rparen = stat.rfind(")")
            fields = stat[rparen + 2 :].split()
            ppid = int(fields[1])
            comm = stat[stat.find("(") + 1 : rparen]
        except (OSError, ValueError, IndexError):
            continue
        name = comm
        try:
            name = Path(os.readlink(child / "exe")).name or comm
        except OSError:
            pass
        table[pid] = (ppid, name)
    return table


def process_creation_time(pid: int) -> int | None:
    """Creation time (Windows FILETIME ticks) of a process, or None when unknown."""
    if os.name != "nt" or not pid or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def ancestor_names(pid: int | None = None, limit: int = 12) -> list[str]:
    """Exe names of the ancestors of pid (default: this process), nearest first.

    Stops when a "parent" was created after its child: that pid was reused and
    the real parent is gone. Unknown creation times never stop the walk."""
    start = os.getpid() if pid is None else pid
    table = process_table()
    names: list[str] = []
    seen = {start}
    entry = table.get(start)
    current = entry[0] if entry else (os.getppid() if pid is None else 0)
    child_time = process_creation_time(start)
    while current and current not in seen and len(names) < limit:
        seen.add(current)
        info = table.get(current)
        if not info:
            break
        parent, name = info
        parent_time = process_creation_time(current)
        if child_time is not None and parent_time is not None and parent_time > child_time:
            break
        if parent_time is not None:
            child_time = parent_time
        names.append(name)
        current = parent
    return names


def kill_tree(pid: int) -> None:
    """Kill a process and all its descendants. Never raises."""
    if not pid or pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
            return
        try:
            pgid = os.getpgid(pid)
            if pgid != os.getpgrp():
                os.killpg(pgid, signal.SIGKILL)
                return
        except (ProcessLookupError, PermissionError):
            pass
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
