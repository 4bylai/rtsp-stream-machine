from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config_loader import LoadedConfig, ResolvedStream, load_config
from ffmpeg_runner import start_stream
from utils import (
    binary_exists,
    check_tcp,
    ensure_runtime_dirs,
    get_paths,
    is_process_running,
    log_file,
    pid_file,
    read_pid,
    remove_pid,
)

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

try:
    from croniter import croniter
except ModuleNotFoundError:  # pragma: no cover
    croniter = None


paths = get_paths()
ensure_runtime_dirs(paths)

CONFIG_PATH = Path(os.environ.get("STREAM_CONFIG", "config/streams.yaml"))
if not CONFIG_PATH.is_absolute():
    CONFIG_PATH = (paths.root / CONFIG_PATH).resolve()

VIDEO_FLAGS_PATH = paths.config_dir / "video_flags.json"
VIDEO_META_PATH = paths.config_dir / "video_meta.json"
SCHEDULES_PATH = paths.config_dir / "schedules.json"
STOPPED_STREAMS_STATE_PATH = paths.config_dir / "stopped_streams_state.json"
DESIRED_STREAMS_STATE_PATH = paths.config_dir / "desired_streams_state.json"
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".ts", ".m4v"}
SCHEDULE_POLL_SECONDS = 10
STOPPED_STREAM_RETENTION_DAYS = 7
VIDEO_RETENTION_DAYS = max(1, int(os.environ.get("VIDEO_RETENTION_DAYS", "30")))
VIDEO_MAX_UPLOAD_BYTES = int(float(os.environ.get("VIDEO_MAX_UPLOAD_GB", "2")) * 1024 * 1024 * 1024)
SCHEDULE_EXECUTED_RETENTION_SECONDS = 24 * 60 * 60
LOG_RETENTION_DAYS = max(1, int(os.environ.get("LOG_RETENTION_DAYS", "7")))
AUTO_RECOVER_STREAMS = os.environ.get("AUTO_RECOVER_STREAMS", "true").strip().lower() not in {"0", "false", "no", "off"}

_schedule_lock = threading.Lock()
_streams_lock = threading.Lock()
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None
_watchdog_last_result: dict[str, Any] = {"at": None, "recovered": [], "failed": {}}


class VideoStateUpdate(BaseModel):
    enabled: bool


class ScheduleCreate(BaseModel):
    stream: str
    action: str
    cron: str | None = None
    start_at: str | None = None
    timezone: str = "Asia/Almaty"
    enabled: bool = True


class ScheduleStateUpdate(BaseModel):
    enabled: bool


class StreamsFromVideosCreate(BaseModel):
    mode: str = "single"  # single | playlist
    name_prefix: str = "cam_auto_"
    codec_mode: str = "auto"
    start_after_create: bool = True
    include_disabled_videos: bool = False


class StreamCreateFromVideos(BaseModel):
    name: str
    mode: str = "single"  # single | playlist
    files: list[str]
    codec_mode: str = "auto"
    start_after_create: bool = True


class PlaylistVideoAdd(BaseModel):
    video_name: str


class PlaylistVideoRemove(BaseModel):
    video_name: str


app = FastAPI(title="RTSP Stream Machine API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_config() -> LoadedConfig:
    loaded, errors = load_config(CONFIG_PATH, paths)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    assert loaded is not None
    return loaded


def _load_raw_streams_config() -> dict[str, Any]:
    if yaml is None:
        raise HTTPException(status_code=500, detail="PyYAML is not installed")

    if CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid streams config: {exc}") from exc
    else:
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    raw.setdefault(
        "server",
        {
            "rtsp_host": "127.0.0.1",
            "rtsp_port": 8554,
            "mediamtx_publish_host": "127.0.0.1",
        },
    )
    raw.setdefault(
        "defaults",
        {
            "loop": True,
            "codec_mode": "auto",
            "audio": False,
            "fps": 25,
            "width": 1280,
            "height": 720,
        },
    )
    if not isinstance(raw.get("streams"), list):
        raw["streams"] = []

    return raw


def _save_raw_streams_config(raw: dict[str, Any]) -> None:
    if yaml is None:
        raise HTTPException(status_code=500, detail="PyYAML is not installed")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    CONFIG_PATH.write_text(dumped, encoding="utf-8")


def _raw_stream_row_by_name(raw: dict[str, Any], stream_name: str) -> dict[str, Any] | None:
    streams = raw.get("streams", [])
    if not isinstance(streams, list):
        return None
    for row in streams:
        if not isinstance(row, dict):
            continue
        if str(row.get("name", "")).strip() == stream_name:
            return row
    return None


def _to_ts(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _next_stream_name(prefix: str, used_names: set[str]) -> str:
    idx = 1
    while True:
        candidate = f"{prefix}{idx:03d}"
        if candidate not in used_names:
            return candidate
        idx += 1


def _cleanup_stale_pid(stream_name: str) -> None:
    p_path = pid_file(paths, stream_name)
    pid = read_pid(p_path)
    if pid is None:
        remove_pid(p_path)
        return
    if not is_process_running(pid):
        remove_pid(p_path)


def _cleanup_expired_stopped_streams() -> list[str]:
    now_ts = int(time.time())
    retention_sec = STOPPED_STREAM_RETENTION_DAYS * 24 * 60 * 60
    state = _load_stopped_stream_state()
    raw = _load_raw_streams_config()
    streams = raw.get("streams", [])
    if not isinstance(streams, list):
        streams = []
        raw["streams"] = streams

    stream_names: list[str] = []
    running_names: set[str] = set()
    for row in streams:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        stream_names.append(name)
        _cleanup_stale_pid(name)
        pid = read_pid(pid_file(paths, name))
        if pid and is_process_running(pid):
            running_names.add(name)

    deleted_names: list[str] = []
    for name in stream_names:
        if name in running_names:
            state.pop(name, None)
            continue
        first_stopped_at = state.get(name)
        if first_stopped_at is None:
            state[name] = now_ts
            continue
        if now_ts - first_stopped_at >= retention_sec:
            deleted_names.append(name)

    if deleted_names:
        deleted_set = set(deleted_names)
        raw["streams"] = [
            row
            for row in streams
            if not (isinstance(row, dict) and str(row.get("name", "")).strip() in deleted_set)
        ]
        _save_raw_streams_config(raw)
        for name in deleted_names:
            state.pop(name, None)
            remove_pid(pid_file(paths, name))
            try:
                log_file(paths, name).unlink(missing_ok=True)
            except OSError:
                pass
            try:
                (paths.playlists_dir / f"{name}.txt").unlink(missing_ok=True)
            except OSError:
                pass

    for name in list(state.keys()):
        if name not in stream_names and name not in running_names:
            state.pop(name, None)

    _save_stopped_stream_state(state)
    return deleted_names


def _load_video_flags() -> set[str]:
    if not VIDEO_FLAGS_PATH.exists():
        return set()
    try:
        payload = json.loads(VIDEO_FLAGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    disabled = payload.get("disabled", [])
    if not isinstance(disabled, list):
        return set()
    return {str(x) for x in disabled}


def _save_video_flags(disabled: set[str]) -> None:
    payload = {"disabled": sorted(disabled)}
    VIDEO_FLAGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_video_meta() -> dict[str, int]:
    if not VIDEO_META_PATH.exists():
        return {}
    try:
        payload = json.loads(VIDEO_META_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    source: dict[str, Any]
    if isinstance(payload, dict) and isinstance(payload.get("uploaded_at"), dict):
        source = payload.get("uploaded_at", {})
    elif isinstance(payload, dict):
        source = payload
    else:
        return {}

    result: dict[str, int] = {}
    for key, value in source.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            result[name] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _save_video_meta(meta: dict[str, int]) -> None:
    payload = {"uploaded_at": {k: int(v) for k, v in sorted(meta.items())}}
    VIDEO_META_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_video_meta(video_files: list[Path]) -> dict[str, int]:
    meta = _load_video_meta()
    known = {x.name for x in video_files}
    changed = False
    for video in video_files:
        if video.name in meta:
            continue
        meta[video.name] = int(video.stat().st_mtime)
        changed = True
    for name in list(meta.keys()):
        if name not in known:
            meta.pop(name, None)
            changed = True
    if changed:
        _save_video_meta(meta)
    return meta


def _load_schedules_unlocked() -> list[dict[str, Any]]:
    if not SCHEDULES_PATH.exists():
        return []
    try:
        payload = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return payload


def _save_schedules_unlocked(schedules: list[dict[str, Any]]) -> None:
    SCHEDULES_PATH.write_text(json.dumps(schedules, ensure_ascii=False, indent=2), encoding="utf-8")


def _prune_expired_executed_schedules_unlocked(
    schedules: list[dict[str, Any]],
    now_ts: int | None = None,
) -> tuple[list[dict[str, Any]], int]:
    current_ts = int(time.time()) if now_ts is None else int(now_ts)
    kept: list[dict[str, Any]] = []
    removed = 0

    for item in schedules:
        if not isinstance(item, dict):
            kept.append(item)
            continue

        is_one_time = bool(item.get("start_at")) and not bool(item.get("cron"))
        if not is_one_time:
            kept.append(item)
            continue

        executed_at = item.get("executed_at")
        if executed_at is None:
            kept.append(item)
            continue

        try:
            fired_ts = int(executed_at)
        except (TypeError, ValueError):
            kept.append(item)
            continue

        if current_ts - fired_ts >= SCHEDULE_EXECUTED_RETENTION_SECONDS:
            removed += 1
            continue

        kept.append(item)

    return kept, removed


def _load_stopped_stream_state() -> dict[str, int]:
    if not STOPPED_STREAMS_STATE_PATH.exists():
        return {}
    try:
        payload = json.loads(STOPPED_STREAMS_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, int] = {}
    for key, value in payload.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            result[name] = int(value)
        except (TypeError, ValueError):
            continue
    return result


def _save_stopped_stream_state(state: dict[str, int]) -> None:
    STOPPED_STREAMS_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _load_desired_streams_state() -> set[str]:
    if not DESIRED_STREAMS_STATE_PATH.exists():
        return set()
    try:
        payload = json.loads(DESIRED_STREAMS_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()

    if isinstance(payload, dict):
        source = payload.get("desired_running", [])
    else:
        source = payload

    if not isinstance(source, list):
        return set()

    result: set[str] = set()
    for item in source:
        name = str(item).strip()
        if name:
            result.add(name)
    return result


def _save_desired_streams_state(desired_running: set[str]) -> None:
    payload = {"desired_running": sorted(desired_running)}
    DESIRED_STREAMS_STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _set_desired_running(stream_name: str, should_run: bool) -> None:
    desired = _load_desired_streams_state()
    if should_run:
        desired.add(stream_name)
    else:
        desired.discard(stream_name)
    _save_desired_streams_state(desired)


def _sync_desired_streams_state_with_config() -> None:
    desired = _load_desired_streams_state()
    if not desired:
        return
    loaded = _load_config()
    valid_names = {stream.name for stream in loaded.streams}
    pruned = {name for name in desired if name in valid_names}
    if pruned != desired:
        _save_desired_streams_state(pruned)


def _seed_desired_from_running_processes() -> None:
    loaded = _load_config()
    desired = _load_desired_streams_state()
    changed = False
    for stream in loaded.streams:
        _cleanup_stale_pid(stream.name)
        pid = read_pid(pid_file(paths, stream.name))
        if pid and is_process_running(pid) and stream.name not in desired:
            desired.add(stream.name)
            changed = True
    if changed:
        _save_desired_streams_state(desired)


def _stream_by_name(loaded: LoadedConfig, name: str) -> ResolvedStream:
    for stream in loaded.streams:
        if stream.name == name:
            return stream
    raise HTTPException(status_code=404, detail=f"stream '{name}' not found")


def _internal_stream_by_name(name: str) -> ResolvedStream:
    loaded = _load_config()
    return _stream_by_name(loaded, name)


def _stop_pid(pid: int, timeout_sec: float = 5.0) -> bool:
    if not is_process_running(pid):
        return True

    sent = False
    try:
        os.killpg(pid, signal.SIGTERM)
        sent = True
    except Exception:
        pass

    if not sent:
        try:
            os.kill(pid, signal.SIGTERM)
            sent = True
        except Exception:
            return False

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(0.2)

    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            return False

    return not is_process_running(pid)


def _probe_video(file_path: Path) -> dict[str, Any]:
    if not binary_exists("ffprobe"):
        return {}

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError:
        return {}

    if result.returncode != 0:
        return {}

    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}

    video_stream = next((s for s in parsed.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        return {}

    return {
        "codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": video_stream.get("r_frame_rate"),
        "duration": parsed.get("format", {}).get("duration"),
    }


def _referencing_streams(video_name: str) -> list[str]:
    if yaml is None or not CONFIG_PATH.exists():
        return []

    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []

    streams = raw.get("streams", [])
    if not isinstance(streams, list):
        return []

    result: list[str] = []
    for row in streams:
        if not isinstance(row, dict):
            continue
        stream_name = str(row.get("name", "")).strip()
        files = row.get("files", [])
        if not stream_name or not isinstance(files, list):
            continue
        for raw_path in files:
            abs_file = (paths.root / str(raw_path)).resolve()
            if abs_file.name == video_name:
                result.append(stream_name)
                break

    return sorted(set(result))


def _enabled_video_files(include_disabled: bool = False) -> list[Path]:
    disabled = _load_video_flags()
    files = [x for x in sorted(paths.videos_dir.glob("*")) if x.is_file()]
    if include_disabled:
        return files
    return [x for x in files if x.name not in disabled]


def _parse_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _parse_start_at(raw: str, tz_name: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    tz = _parse_tz(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _compute_next_run(item: dict[str, Any]) -> str | None:
    if not item.get("enabled", True):
        return None

    tz_name = str(item.get("timezone", "UTC"))
    tz = _parse_tz(tz_name)
    now_local = datetime.now(timezone.utc).astimezone(tz)

    cron_expr = item.get("cron")
    if cron_expr:
        if croniter is None:
            return None
        base = now_local
        try:
            next_dt = croniter(str(cron_expr), base).get_next(datetime)
            return next_dt.isoformat()
        except Exception:
            return None

    start_at = item.get("start_at")
    if start_at and not item.get("executed_at"):
        dt = _parse_start_at(str(start_at), tz_name)
        if dt and dt >= now_local:
            return dt.isoformat()

    return None


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    lines = text.splitlines()
    if max_lines <= 0:
        return []
    return lines[-max_lines:]


def _ts_iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except Exception:
        return None


def _cleanup_expired_videos() -> list[str]:
    now_ts = int(time.time())
    retention_sec = VIDEO_RETENTION_DAYS * 24 * 60 * 60
    video_files = [x for x in sorted(paths.videos_dir.glob("*")) if x.is_file()]
    meta = _sync_video_meta(video_files)
    disabled = _load_video_flags()
    deleted: list[str] = []

    for video in video_files:
        uploaded_at = int(meta.get(video.name, int(video.stat().st_mtime)))
        if now_ts - uploaded_at < retention_sec:
            continue

        refs = _referencing_streams(video.name)
        if refs:
            continue

        try:
            video.unlink()
        except OSError:
            continue

        deleted.append(video.name)
        meta.pop(video.name, None)
        disabled.discard(video.name)

    if deleted:
        _save_video_meta(meta)
        _save_video_flags(disabled)
        print(
            f"[retention] auto-deleted expired videos ({VIDEO_RETENTION_DAYS}d): {', '.join(deleted)}",
            flush=True,
        )

    return deleted


def _cleanup_old_logs() -> list[str]:
    now_ts = int(time.time())
    retention_sec = LOG_RETENTION_DAYS * 24 * 60 * 60
    deleted: list[str] = []

    for log_path in paths.logs_dir.glob("*.log"):
        if not log_path.is_file():
            continue
        # Keep current API server log file intact while service is running.
        if log_path.name == "api_server.log":
            continue
        try:
            mtime = int(log_path.stat().st_mtime)
        except OSError:
            continue
        if now_ts - mtime < retention_sec:
            continue
        try:
            log_path.unlink()
            deleted.append(log_path.name)
        except OSError:
            continue

    if deleted:
        print(f"[retention] auto-deleted logs older than {LOG_RETENTION_DAYS}d: {', '.join(deleted)}", flush=True)

    return deleted


def _mount_points() -> list[str]:
    proc_mounts = Path("/proc/mounts")
    if not proc_mounts.exists():
        return ["/"]

    skip_prefixes = ("/proc", "/sys", "/dev", "/run")
    points: list[str] = []
    seen: set[str] = set()
    for line in proc_mounts.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mount_point = parts[1]
        if mount_point.startswith(skip_prefixes):
            continue
        if mount_point in seen:
            continue
        seen.add(mount_point)
        points.append(mount_point)

    if "/" not in seen:
        points.insert(0, "/")
    return points or ["/"]


def _disk_snapshot() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for mount_point in _mount_points():
        try:
            du = shutil.disk_usage(mount_point)
        except OSError:
            continue
        total = int(du.total)
        used = int(du.used)
        free = int(du.free)
        used_pct = round((used / total) * 100, 2) if total > 0 else 0.0
        free_pct = round((free / total) * 100, 2) if total > 0 else 0.0
        items.append(
            {
                "mount_point": mount_point,
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "used_percent": used_pct,
                "free_percent": free_pct,
            }
        )
    return sorted(items, key=lambda x: x["mount_point"])


def _internal_start_stream(name: str, update_desired: bool = True) -> dict[str, Any]:
    if not binary_exists("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH")

    stream = _internal_stream_by_name(name)
    missing = [str(f) for f in stream.files if not f.exists()]
    if missing:
        raise RuntimeError(f"missing files: {missing}")

    p_path = pid_file(paths, stream.name)
    _cleanup_stale_pid(stream.name)
    pid = read_pid(p_path)
    if pid and is_process_running(pid):
        if update_desired:
            _set_desired_running(name, True)
        return {"ok": True, "message": "already running", "pid": pid, "status": "running"}

    result = start_stream(paths, stream)
    if not result.started:
        raise RuntimeError(result.message)

    if update_desired:
        _set_desired_running(name, True)
    return {"ok": True, "message": result.message, "pid": result.pid, "status": "running"}


def _internal_stop_stream(name: str, update_desired: bool = True) -> dict[str, Any]:
    _internal_stream_by_name(name)

    p_path = pid_file(paths, name)
    _cleanup_stale_pid(name)
    pid = read_pid(p_path)
    if pid is None:
        if update_desired:
            _set_desired_running(name, False)
        return {"ok": True, "message": "already stopped", "status": "stopped"}

    if not is_process_running(pid):
        remove_pid(p_path)
        if update_desired:
            _set_desired_running(name, False)
        return {"ok": True, "message": "stale pid removed", "status": "stopped"}

    stopped = _stop_pid(pid)
    if not stopped:
        raise RuntimeError(f"failed to stop stream '{name}'")

    remove_pid(p_path)
    if update_desired:
        _set_desired_running(name, False)
    return {"ok": True, "message": f"stopped pid={pid}", "status": "stopped"}


def _schedule_execute(item: dict[str, Any]) -> str:
    name = str(item.get("stream", "")).strip()
    action = str(item.get("action", "")).strip().lower()
    if not name or action not in {"start", "stop", "delete"}:
        raise RuntimeError("invalid schedule item")

    if action == "start":
        result = _internal_start_stream(name)
        return result.get("message", "started")

    if action == "stop":
        result = _internal_stop_stream(name)
        return result.get("message", "stopped")

    # delete in timer context = stop + mark disabled in schedule (soft delete behavior)
    result = _internal_stop_stream(name)
    return f"soft-deleted (archived): {result.get('message', 'stopped')}"


def _is_schedule_due(item: dict[str, Any], now_utc: datetime) -> tuple[bool, str | None]:
    if not item.get("enabled", True):
        return False, None

    tz_name = str(item.get("timezone", "UTC"))
    tz = _parse_tz(tz_name)
    now_local = now_utc.astimezone(tz)

    start_at = item.get("start_at")
    cron_expr = item.get("cron")

    if start_at and not cron_expr:
        if item.get("executed_at"):
            return False, None
        start_dt = _parse_start_at(str(start_at), tz_name)
        if not start_dt:
            return False, "invalid start_at"
        return now_local >= start_dt, None

    if cron_expr:
        if croniter is None:
            return False, "croniter is not installed"

        last_run_at = item.get("last_run_at")
        if isinstance(last_run_at, (int, float)) and last_run_at > 0:
            base = datetime.fromtimestamp(float(last_run_at), tz)
        else:
            base = now_local - timedelta(minutes=1)

        try:
            next_run = croniter(str(cron_expr), base).get_next(datetime)
        except Exception:
            return False, "invalid cron expression"

        return now_local >= next_run, None

    return False, "schedule has neither cron nor start_at"


def _recover_desired_streams() -> None:
    global _watchdog_last_result
    if not AUTO_RECOVER_STREAMS:
        return

    desired = _load_desired_streams_state()
    if not desired:
        _watchdog_last_result = {"at": int(time.time()), "recovered": [], "failed": {}}
        return

    loaded = _load_config()
    available = {stream.name for stream in loaded.streams}
    unknown = {name for name in desired if name not in available}
    if unknown:
        desired -= unknown
        _save_desired_streams_state(desired)

    recovered: list[str] = []
    failed: dict[str, str] = {}

    for name in sorted(desired):
        _cleanup_stale_pid(name)
        pid = read_pid(pid_file(paths, name))
        if pid and is_process_running(pid):
            continue
        try:
            _internal_start_stream(name, update_desired=False)
            recovered.append(name)
        except Exception as exc:  # noqa: BLE001
            failed[name] = str(exc)

    _watchdog_last_result = {
        "at": int(time.time()),
        "recovered": recovered,
        "failed": failed,
    }


def _scheduler_loop() -> None:
    while not _scheduler_stop.wait(SCHEDULE_POLL_SECONDS):
        try:
            with _streams_lock:
                _cleanup_expired_stopped_streams()
                _cleanup_expired_videos()
                _cleanup_old_logs()
                _sync_desired_streams_state_with_config()
                _recover_desired_streams()

            now_utc = datetime.now(timezone.utc)
            now_ts = int(now_utc.timestamp())
            changed = False

            with _schedule_lock:
                schedules = _load_schedules_unlocked()

                for item in schedules:
                    due, reason = _is_schedule_due(item, now_utc)
                    if reason and not due and item.get("enabled", True):
                        item["last_error"] = reason
                        changed = True

                    if not due:
                        continue

                    try:
                        result = _schedule_execute(item)
                        item["last_error"] = None
                        item["last_result"] = result
                        item["last_run_at"] = int(now_utc.timestamp())

                        # one-time schedule executes once.
                        if item.get("start_at") and not item.get("cron"):
                            item["enabled"] = False
                            item["executed_at"] = int(now_utc.timestamp())
                    except Exception as exc:  # noqa: BLE001
                        item["last_error"] = str(exc)
                        item["last_run_at"] = int(now_utc.timestamp())

                        # Avoid tight error loops for one-time schedule.
                        if item.get("start_at") and not item.get("cron"):
                            item["enabled"] = False
                            item["executed_at"] = int(now_utc.timestamp())

                    changed = True

                schedules, pruned_count = _prune_expired_executed_schedules_unlocked(schedules, now_ts=now_ts)
                if pruned_count > 0:
                    changed = True

                if changed:
                    _save_schedules_unlocked(schedules)
        except Exception:
            # Keep background scheduler alive even if one iteration fails.
            continue


def _start_scheduler() -> None:
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return

    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, name="schedule-runner", daemon=True)
    _scheduler_thread.start()


def _stop_scheduler() -> None:
    _scheduler_stop.set()


@app.on_event("startup")
def _on_startup() -> None:
    with _streams_lock:
        _cleanup_expired_stopped_streams()
        _cleanup_expired_videos()
        _cleanup_old_logs()
        _sync_desired_streams_state_with_config()
        _seed_desired_from_running_processes()
        _recover_desired_streams()
    _start_scheduler()


@app.on_event("shutdown")
def _on_shutdown() -> None:
    _stop_scheduler()


@app.get("/ui")
def ui_short() -> RedirectResponse:
    return RedirectResponse(url="/")


@app.head("/ui")
def ui_short_head() -> RedirectResponse:
    return RedirectResponse(url="/")


@app.get("/ui/")
def ui_slash() -> RedirectResponse:
    return RedirectResponse(url="/")


@app.head("/ui/")
def ui_slash_head() -> RedirectResponse:
    return RedirectResponse(url="/")


@app.get("/backend/health")
def api_health() -> dict[str, Any]:
    mediamtx_ok = False
    try:
        loaded = _load_config()
        mediamtx_ok = check_tcp(loaded.server.mediamtx_publish_host, loaded.server.rtsp_port)
    except HTTPException:
        pass

    scheduler_alive = _scheduler_thread is not None and _scheduler_thread.is_alive()

    return {
        "ok": True,
        "mediamtx_reachable": mediamtx_ok,
        "scheduler_running": scheduler_alive,
        "watchdog_enabled": AUTO_RECOVER_STREAMS,
        "watchdog_last": _watchdog_last_result,
        "video_retention_days": VIDEO_RETENTION_DAYS,
        "video_max_upload_bytes": VIDEO_MAX_UPLOAD_BYTES,
        "log_retention_days": LOG_RETENTION_DAYS,
    }


@app.get("/backend/storage")
def storage_info() -> dict[str, Any]:
    return {"items": _disk_snapshot()}


@app.get("/backend/streams")
def list_streams() -> dict[str, Any]:
    with _streams_lock:
        _cleanup_expired_stopped_streams()
        raw = _load_raw_streams_config()
    loaded = _load_config()
    config_mtime_ts = _to_ts(CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None)

    items: list[dict[str, Any]] = []
    for stream in loaded.streams:
        _cleanup_stale_pid(stream.name)
        p_path = pid_file(paths, stream.name)
        pid = read_pid(p_path)
        running = bool(pid and is_process_running(pid))
        lookup_name = stream.source_group or stream.name
        row = _raw_stream_row_by_name(raw, lookup_name) if isinstance(raw, dict) else None
        created_at_ts = _to_ts(row.get("created_at")) if isinstance(row, dict) else None
        added_at_ts = created_at_ts or config_mtime_ts or int(time.time())

        items.append(
            {
                "name": stream.name,
                "mode": "separate" if stream.source_group else stream.mode,
                "source_group": stream.source_group,
                "codec_mode": stream.options.codec_mode,
                "files": [str(x.relative_to(paths.root)) for x in stream.files],
                "public_url": stream.public_url,
                "publish_url": stream.publish_url,
                "status": "running" if running else "stopped",
                "archived": not running,
                "pid": pid if running else None,
                "log_path": str(log_file(paths, stream.name)),
                "added_at": _ts_iso(added_at_ts),
            }
        )

    return {"items": items}


@app.post("/backend/streams/create-from-videos")
def create_stream_from_videos(payload: StreamCreateFromVideos) -> dict[str, Any]:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="stream name is required")

    mode = payload.mode.strip().lower()
    if mode not in {"single", "playlist"}:
        raise HTTPException(status_code=400, detail="mode must be 'single' or 'playlist'")

    codec_mode = payload.codec_mode.strip().lower()
    if codec_mode not in {"copy", "transcode", "auto"}:
        raise HTTPException(status_code=400, detail="codec_mode must be copy/transcode/auto")

    selected = [str(x).strip() for x in payload.files if str(x).strip()]
    if not selected:
        raise HTTPException(status_code=400, detail="select at least one video")
    if mode == "single" and len(selected) != 1:
        raise HTTPException(status_code=400, detail="single mode requires exactly one video")
    if mode == "playlist" and len(selected) < 2:
        raise HTTPException(status_code=400, detail="playlist mode requires at least two videos")

    unique_selected = list(dict.fromkeys(selected))
    missing: list[str] = []
    rel_files: list[str] = []
    for video_name in unique_selected:
        target = paths.videos_dir / video_name
        if not target.exists() or not target.is_file():
            missing.append(video_name)
            continue
        rel_files.append(f"videos/{target.name}")

    if missing:
        raise HTTPException(status_code=400, detail={"message": "some videos are missing", "files": missing})

    with _streams_lock:
        raw = _load_raw_streams_config()
        streams = raw.get("streams", [])
        if not isinstance(streams, list):
            streams = []
            raw["streams"] = streams

        used_names = {
            str(item.get("name", "")).strip()
            for item in streams
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
        if name in used_names:
            raise HTTPException(status_code=409, detail=f"stream '{name}' already exists")

        entry = {
            "name": name,
            "mode": mode,
            "files": rel_files,
            "codec_mode": codec_mode,
            "manual_created": True,
            "created_at": int(time.time()),
        }
        streams.append(entry)
        _save_raw_streams_config(raw)

    started = False
    start_error: str | None = None
    if payload.start_after_create:
        try:
            _internal_start_stream(name)
            started = True
        except Exception as exc:  # noqa: BLE001
            start_error = str(exc)

    loaded = _load_config()
    resolved = _stream_by_name(loaded, name)
    return {
        "ok": True,
        "created": name,
        "started": started,
        "start_error": start_error,
        "stream": {
            "name": resolved.name,
            "mode": resolved.mode,
            "files": [str(x.relative_to(paths.root)) for x in resolved.files],
            "public_url": resolved.public_url,
            "publish_url": resolved.publish_url,
            "codec_mode": resolved.options.codec_mode,
        },
    }


@app.post("/backend/streams/bootstrap-from-videos")
def bootstrap_streams_from_videos(payload: StreamsFromVideosCreate) -> dict[str, Any]:
    mode = payload.mode.strip().lower()
    if mode not in {"single", "playlist"}:
        raise HTTPException(status_code=400, detail="mode must be 'single' or 'playlist'")

    codec_mode = payload.codec_mode.strip().lower()
    if codec_mode not in {"copy", "transcode", "auto"}:
        raise HTTPException(status_code=400, detail="codec_mode must be copy/transcode/auto")

    enabled_files = _enabled_video_files(include_disabled=payload.include_disabled_videos)
    if not enabled_files:
        raise HTTPException(status_code=400, detail="no videos found for stream generation")

    with _streams_lock:
        raw = _load_raw_streams_config()
        streams = raw.get("streams", [])
        if not isinstance(streams, list):
            streams = []
            raw["streams"] = streams

        used_names = {
            str(item.get("name", "")).strip()
            for item in streams
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }

        existing_files_flat = set()
        for item in streams:
            if not isinstance(item, dict):
                continue
            files = item.get("files", [])
            if not isinstance(files, list):
                continue
            for f in files:
                existing_files_flat.add(str(f))

        created: list[str] = []
        rel_files = [f"videos/{f.name}" for f in enabled_files]

        if mode == "single":
            for rel_file in rel_files:
                if rel_file in existing_files_flat:
                    continue
                stream_name = _next_stream_name(payload.name_prefix, used_names)
                used_names.add(stream_name)
                entry = {
                    "name": stream_name,
                    "mode": "single",
                    "files": [rel_file],
                    "codec_mode": codec_mode,
                    "auto_generated": True,
                    "created_at": int(time.time()),
                }
                streams.append(entry)
                created.append(stream_name)
        else:
            # playlist mode => one stream from all videos (if same exact playlist does not exist yet)
            same_playlist_exists = False
            for item in streams:
                if not isinstance(item, dict):
                    continue
                if str(item.get("mode", "")).strip().lower() != "playlist":
                    continue
                files = item.get("files", [])
                if isinstance(files, list) and files == rel_files:
                    same_playlist_exists = True
                    break

            if not same_playlist_exists:
                stream_name = _next_stream_name(payload.name_prefix, used_names)
                entry = {
                    "name": stream_name,
                    "mode": "playlist",
                    "files": rel_files,
                    "codec_mode": codec_mode,
                    "auto_generated": True,
                    "created_at": int(time.time()),
                }
                streams.append(entry)
                created.append(stream_name)

        if created:
            _save_raw_streams_config(raw)

    started: list[str] = []
    start_errors: dict[str, str] = {}
    if payload.start_after_create:
        for stream_name in created:
            try:
                _internal_start_stream(stream_name)
                started.append(stream_name)
            except Exception as exc:  # noqa: BLE001
                start_errors[stream_name] = str(exc)

    return {
        "ok": True,
        "mode": mode,
        "created": created,
        "started": started,
        "start_errors": start_errors,
        "message": "streams generated from videos",
    }


@app.post("/backend/streams/{name}/playlist/add-video")
def playlist_add_video(name: str, payload: PlaylistVideoAdd) -> dict[str, Any]:
    video_name = Path(payload.video_name).name.strip()
    if not video_name:
        raise HTTPException(status_code=400, detail="video_name is required")

    target = paths.videos_dir / video_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"video '{video_name}' not found")
    rel_video = f"videos/{target.name}"

    with _streams_lock:
        raw = _load_raw_streams_config()
        row = _raw_stream_row_by_name(raw, name)
        if not row:
            raise HTTPException(status_code=404, detail=f"stream '{name}' not found")
        mode = str(row.get("mode", "")).strip().lower()
        if mode != "playlist":
            raise HTTPException(status_code=400, detail="only playlist streams support add/remove videos")

        files = row.get("files", [])
        if not isinstance(files, list):
            files = []
        if rel_video in files:
            raise HTTPException(status_code=409, detail=f"video '{video_name}' already in playlist")

        files.append(rel_video)
        row["files"] = files
        _save_raw_streams_config(raw)

    restart_error: str | None = None
    restarted = False
    _cleanup_stale_pid(name)
    pid = read_pid(pid_file(paths, name))
    if pid and is_process_running(pid):
        try:
            _internal_stop_stream(name, update_desired=False)
            _internal_start_stream(name)
            restarted = True
        except Exception as exc:  # noqa: BLE001
            restart_error = str(exc)

    loaded = _load_config()
    resolved = _stream_by_name(loaded, name)
    return {
        "ok": restart_error is None,
        "stream": {
            "name": resolved.name,
            "mode": resolved.mode,
            "files": [str(x.relative_to(paths.root)) for x in resolved.files],
        },
        "restarted": restarted,
        "restart_error": restart_error,
    }


@app.post("/backend/streams/{name}/playlist/remove-video")
def playlist_remove_video(name: str, payload: PlaylistVideoRemove) -> dict[str, Any]:
    video_name = Path(payload.video_name).name.strip()
    if not video_name:
        raise HTTPException(status_code=400, detail="video_name is required")

    with _streams_lock:
        raw = _load_raw_streams_config()
        row = _raw_stream_row_by_name(raw, name)
        if not row:
            raise HTTPException(status_code=404, detail=f"stream '{name}' not found")
        mode = str(row.get("mode", "")).strip().lower()
        if mode != "playlist":
            raise HTTPException(status_code=400, detail="only playlist streams support add/remove videos")

        files = row.get("files", [])
        if not isinstance(files, list) or not files:
            raise HTTPException(status_code=400, detail="playlist has no files")

        idx_to_remove = -1
        for idx, raw_path in enumerate(files):
            if Path(str(raw_path)).name == video_name:
                idx_to_remove = idx
                break

        if idx_to_remove < 0:
            raise HTTPException(status_code=404, detail=f"video '{video_name}' not found in playlist")

        if len(files) <= 1:
            raise HTTPException(status_code=400, detail="playlist must contain at least one video")

        files.pop(idx_to_remove)
        row["files"] = files
        _save_raw_streams_config(raw)

    restart_error: str | None = None
    restarted = False
    _cleanup_stale_pid(name)
    pid = read_pid(pid_file(paths, name))
    if pid and is_process_running(pid):
        try:
            _internal_stop_stream(name, update_desired=False)
            _internal_start_stream(name)
            restarted = True
        except Exception as exc:  # noqa: BLE001
            restart_error = str(exc)

    loaded = _load_config()
    resolved = _stream_by_name(loaded, name)
    return {
        "ok": restart_error is None,
        "stream": {
            "name": resolved.name,
            "mode": resolved.mode,
            "files": [str(x.relative_to(paths.root)) for x in resolved.files],
        },
        "restarted": restarted,
        "restart_error": restart_error,
    }


@app.post("/backend/streams/{name}/start")
def start_stream_api(name: str) -> dict[str, Any]:
    try:
        result = _internal_start_stream(name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    warning: str | None = None
    try:
        loaded = _load_config()
        if not check_tcp(loaded.server.mediamtx_publish_host, loaded.server.rtsp_port):
            warning = (
                "MediaMTX is not reachable at "
                f"{loaded.server.mediamtx_publish_host}:{loaded.server.rtsp_port}."
            )
    except Exception:
        pass

    result["warning"] = warning
    return result


@app.post("/backend/streams/{name}/stop")
def stop_stream_api(name: str) -> dict[str, Any]:
    try:
        return _internal_stop_stream(name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/backend/streams/{name}/restart")
def restart_stream_api(name: str) -> dict[str, Any]:
    try:
        _internal_stop_stream(name, update_desired=False)
        return _internal_start_stream(name)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/backend/streams/clear")
def clear_streams_api() -> dict[str, Any]:
    with _streams_lock:
        loaded = _load_config()
        stopped = 0
        stop_errors: dict[str, str] = {}

        for stream in loaded.streams:
            try:
                _internal_stop_stream(stream.name, update_desired=False)
                stopped += 1
            except Exception as exc:  # noqa: BLE001
                stop_errors[stream.name] = str(exc)

        raw = _load_raw_streams_config()
        raw["streams"] = []
        _save_raw_streams_config(raw)

        _save_desired_streams_state(set())
        _save_stopped_stream_state({})

        for pid_path in paths.pids_dir.glob("*.pid"):
            remove_pid(pid_path)

        for playlist_path in paths.playlists_dir.glob("*.txt"):
            try:
                playlist_path.unlink(missing_ok=True)
            except OSError:
                pass

    return {
        "ok": not bool(stop_errors),
        "stopped_count": stopped,
        "stop_errors": stop_errors,
        "message": "all streams removed from config",
    }


@app.get("/backend/videos")
def list_videos(with_probe: bool = Query(default=True)) -> dict[str, Any]:
    disabled = _load_video_flags()
    video_files = [x for x in sorted(paths.videos_dir.glob("*")) if x.is_file()]
    meta_by_name = _sync_video_meta(video_files)
    retention_sec = VIDEO_RETENTION_DAYS * 24 * 60 * 60
    now_ts = int(time.time())

    items: list[dict[str, Any]] = []
    for video in video_files:
        refs = _referencing_streams(video.name)
        meta = _probe_video(video) if with_probe else {}
        uploaded_at_ts = int(meta_by_name.get(video.name, int(video.stat().st_mtime)))
        expires_at_ts = uploaded_at_ts + retention_sec
        remains = max(0, expires_at_ts - now_ts)
        expires_in_days = int((remains + 86399) // 86400) if remains > 0 else 0
        items.append(
            {
                "name": video.name,
                "path": str(video.relative_to(paths.root)),
                "size_bytes": video.stat().st_size,
                "uploaded_at": _ts_iso(uploaded_at_ts),
                "expires_at": _ts_iso(expires_at_ts),
                "expires_in_days": expires_in_days,
                "enabled": video.name not in disabled,
                "in_use_by": refs,
                "metadata": meta,
            }
        )

    return {
        "supported_formats": sorted(ALLOWED_VIDEO_EXTENSIONS),
        "policy": {
            "retention_days": VIDEO_RETENTION_DAYS,
            "max_upload_bytes": VIDEO_MAX_UPLOAD_BYTES,
        },
        "items": items,
    }


@app.post("/backend/videos")
async def upload_video(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail="empty filename")

    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported format '{ext}'. allowed: {sorted(ALLOWED_VIDEO_EXTENSIONS)}",
        )

    target = paths.videos_dir / filename
    if target.exists():
        raise HTTPException(status_code=409, detail="file already exists")

    size_written = 0
    chunk_size = 1024 * 1024
    with target.open("wb") as f:
        while True:
            chunk = file.file.read(chunk_size)
            if not chunk:
                break
            size_written += len(chunk)
            if size_written > VIDEO_MAX_UPLOAD_BYTES:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                max_gb = VIDEO_MAX_UPLOAD_BYTES / (1024 * 1024 * 1024)
                raise HTTPException(
                    status_code=413,
                    detail=f"file too large: limit is {max_gb:.1f} GB",
                )
            f.write(chunk)

    meta = _load_video_meta()
    meta[filename] = int(time.time())
    _save_video_meta(meta)

    return {
        "ok": True,
        "message": "uploaded",
        "video": {
            "name": filename,
            "path": str(target.relative_to(paths.root)),
            "size_bytes": int(size_written),
        },
    }


@app.patch("/backend/videos/{video_name}")
def set_video_state(video_name: str, payload: VideoStateUpdate) -> dict[str, Any]:
    target = paths.videos_dir / video_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="video not found")

    disabled = _load_video_flags()
    if payload.enabled:
        disabled.discard(video_name)
    else:
        disabled.add(video_name)

    _save_video_flags(disabled)
    return {"ok": True, "video": video_name, "enabled": payload.enabled}


@app.delete("/backend/videos/{video_name}")
def delete_video(video_name: str) -> dict[str, Any]:
    target = paths.videos_dir / video_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="video not found")

    refs = _referencing_streams(video_name)
    if refs:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "video is referenced by streams",
                "streams": refs,
            },
        )

    target.unlink()

    disabled = _load_video_flags()
    if video_name in disabled:
        disabled.remove(video_name)
        _save_video_flags(disabled)

    meta = _load_video_meta()
    if video_name in meta:
        meta.pop(video_name, None)
        _save_video_meta(meta)

    return {"ok": True, "deleted": video_name}


@app.get("/backend/schedules")
def list_schedules() -> dict[str, Any]:
    with _schedule_lock:
        schedules = _load_schedules_unlocked()
        schedules, pruned_count = _prune_expired_executed_schedules_unlocked(schedules)
        if pruned_count > 0:
            _save_schedules_unlocked(schedules)

    items = []
    for item in schedules:
        copy_item = dict(item)
        copy_item["next_run_at"] = _compute_next_run(item)
        copy_item["is_fired"] = bool(item.get("last_run_at"))
        copy_item["fired_at"] = _ts_iso(item.get("last_run_at"))
        items.append(copy_item)

    return {"items": items}


@app.get("/backend/logs/important")
def important_logs(
    limit: int = Query(default=120, ge=20, le=1000),
    files: int = Query(default=8, ge=1, le=30),
) -> dict[str, Any]:
    keywords = (
        "error",
        "failed",
        "fail",
        "warning",
        "warn",
        "traceback",
        "invalid",
        "ошиб",
        "недоступ",
    )
    max_per_file = max(20, min(260, limit))
    items: list[dict[str, Any]] = []

    log_files = sorted(
        [x for x in paths.logs_dir.glob("*.log") if x.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:files]

    for log_path in log_files:
        for raw_line in reversed(_tail_lines(log_path, max_per_file)):
            line = raw_line.strip()
            if not line:
                continue
            line_lc = line.lower()
            if not any(token in line_lc for token in keywords):
                continue
            level = "warn"
            if any(token in line_lc for token in ("error", "failed", "traceback", "ошиб")):
                level = "err"
            items.append(
                {
                    "source": log_path.stem,
                    "level": level,
                    "message": line[:600],
                    "at": datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc).isoformat(),
                }
            )
            if len(items) >= limit:
                return {"items": items}

    with _schedule_lock:
        schedules = _load_schedules_unlocked()
    for item in schedules:
        err = str(item.get("last_error") or "").strip()
        if not err:
            continue
        items.append(
            {
                "source": f"schedule:{item.get('stream', '-')}",
                "level": "err",
                "message": f"{item.get('action', 'action')}: {err}",
                "at": _ts_iso(item.get("last_run_at")) or _ts_iso(item.get("created_at")),
            }
        )
        if len(items) >= limit:
            return {"items": items}

    if items:
        return {"items": items}

    for log_path in log_files:
        last_line = next((x.strip() for x in reversed(_tail_lines(log_path, 30)) if x.strip()), "")
        if not last_line:
            continue
        items.append(
            {
                "source": log_path.stem,
                "level": "info",
                "message": last_line[:600],
                "at": datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc).isoformat(),
            }
        )
        if len(items) >= min(limit, 40):
            break

    return {"items": items}


@app.post("/backend/schedules")
def create_schedule(payload: ScheduleCreate) -> dict[str, Any]:
    action = payload.action.lower().strip()
    if action not in {"start", "stop", "delete"}:
        raise HTTPException(status_code=400, detail="action must be one of: start, stop, delete")
    if not payload.cron and not payload.start_at:
        raise HTTPException(status_code=400, detail="set either cron or start_at")

    loaded = _load_config()
    _stream_by_name(loaded, payload.stream)

    item = payload.model_dump()
    item["action"] = action
    item["id"] = str(uuid4())
    item["created_at"] = int(time.time())
    item["last_run_at"] = None
    item["last_error"] = None
    item["executed_at"] = None

    with _schedule_lock:
        schedules = _load_schedules_unlocked()
        schedules.append(item)
        _save_schedules_unlocked(schedules)

    return {"ok": True, "item": item}


@app.patch("/backend/schedules/{schedule_id}")
def update_schedule_state(schedule_id: str, payload: ScheduleStateUpdate) -> dict[str, Any]:
    with _schedule_lock:
        schedules = _load_schedules_unlocked()
        for item in schedules:
            if str(item.get("id")) == schedule_id:
                item["enabled"] = payload.enabled
                _save_schedules_unlocked(schedules)
                return {"ok": True, "item": item}

    raise HTTPException(status_code=404, detail="schedule not found")


@app.delete("/backend/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict[str, Any]:
    with _schedule_lock:
        schedules = _load_schedules_unlocked()
        filtered = [x for x in schedules if str(x.get("id")) != schedule_id]
        if len(filtered) == len(schedules):
            raise HTTPException(status_code=404, detail="schedule not found")
        _save_schedules_unlocked(filtered)

    return {"ok": True, "deleted": schedule_id}


app.mount("/", StaticFiles(directory=str(paths.root / "web"), html=True), name="ui-root")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090, reload=False)
