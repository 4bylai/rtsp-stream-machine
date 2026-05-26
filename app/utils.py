from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Paths:
    root: Path
    config_dir: Path
    videos_dir: Path
    playlists_dir: Path
    logs_dir: Path
    pids_dir: Path


def get_paths() -> Paths:
    root = Path(__file__).resolve().parent.parent
    return Paths(
        root=root,
        config_dir=root / "config",
        videos_dir=root / "videos",
        playlists_dir=root / "playlists",
        logs_dir=root / "logs",
        pids_dir=root / "pids",
    )


def ensure_runtime_dirs(paths: Paths) -> None:
    paths.playlists_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    paths.pids_dir.mkdir(parents=True, exist_ok=True)


def resolve_path(root: Path, raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute():
        return p
    return (root / p).resolve()


def binary_exists(name: str) -> bool:
    return shutil.which(name) is not None


def pid_file(paths: Paths, stream_name: str) -> Path:
    return paths.pids_dir / f"{stream_name}.pid"


def log_file(paths: Paths, stream_name: str) -> Path:
    return paths.logs_dir / f"{stream_name}.log"


def read_pid(pid_path: Path) -> Optional[int]:
    if not pid_path.exists():
        return None
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
        return int(raw)
    except (ValueError, OSError):
        return None


def write_pid(pid_path: Path, pid: int) -> None:
    pid_path.write_text(str(pid), encoding="utf-8")


def remove_pid(pid_path: Path) -> None:
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    # Treat zombie/defunct processes as not running: they cannot perform stream work
    # and should not keep PID files "alive".
    try:
        check = subprocess.run(  # noqa: S603
            ["ps", "-o", "stat=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
        stat = (check.stdout or "").strip().upper()
        if stat.startswith("Z"):
            return False
    except Exception:
        # If ps is unavailable, keep conservative behavior from kill(0).
        pass

    return True


def terminate_process(pid: int, timeout_seconds: float = 5.0) -> bool:
    if not is_process_running(pid):
        return True

    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        return True
    except OSError:
        return False

    return not is_process_running(pid)


def cleanup_stale_pid(pid_path: Path) -> bool:
    pid = read_pid(pid_path)
    if pid is None:
        remove_pid(pid_path)
        return True
    if is_process_running(pid):
        return False
    remove_pid(pid_path)
    return True


def check_tcp(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def create_concat_playlist(playlist_path: Path, files: list[Path]) -> None:
    lines = []
    for item in files:
        escaped = str(item).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
