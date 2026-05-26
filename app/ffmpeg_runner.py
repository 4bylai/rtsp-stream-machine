from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config_loader import ResolvedStream
from utils import Paths, create_concat_playlist, log_file, pid_file, read_pid, write_pid


@dataclass(frozen=True)
class StartResult:
    stream_name: str
    started: bool
    message: str
    pid: int | None = None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fraction_to_float(value: str | None) -> float | None:
    if not value:
        return None
    if "/" not in value:
        return _safe_float(value)
    left, right = value.split("/", 1)
    num = _safe_float(left)
    den = _safe_float(right)
    if num is None or den in (None, 0):
        return None
    return num / den


def _probe_video(path: Path) -> tuple[bool, str]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "stream=codec_type,codec_name,pix_fmt,avg_frame_rate,r_frame_rate,width,height",
        "-show_entries",
        "format=format_name",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    except FileNotFoundError:
        return False, "ffprobe not found"
    except Exception as exc:  # noqa: BLE001
        return False, f"ffprobe failed: {exc}"

    if completed.returncode != 0:
        return False, completed.stderr.strip() or "ffprobe returned non-zero"

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return False, "ffprobe returned invalid JSON"

    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not isinstance(video, dict):
        return False, "no video stream"

    codec = str(video.get("codec_name", "")).lower()
    pix_fmt = str(video.get("pix_fmt", "")).lower()
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _fraction_to_float(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or ""))
    format_name = str((payload.get("format") or {}).get("format_name", "")).lower()

    if codec != "h264":
        return False, f"codec={codec or 'unknown'} (need h264)"
    if pix_fmt not in {"yuv420p", "nv12", "yuvj420p"}:
        return False, f"pix_fmt={pix_fmt or 'unknown'} (prefer yuv420p/nv12)"
    if width <= 0 or height <= 0:
        return False, "invalid resolution"
    if fps is None or fps <= 0:
        return False, "invalid fps"
    if format_name and not any(x in format_name for x in ("mov", "mp4", "matroska", "mpegts")):
        return False, f"container={format_name} (unsupported for safe copy)"

    return True, f"h264 {width}x{height} {fps:.3f}fps {pix_fmt}"


def choose_codec_mode(stream: ResolvedStream) -> tuple[str, str]:
    requested = stream.options.codec_mode
    if requested != "auto":
        return requested, f"codec_mode from config: {requested}"

    for file_path in stream.files:
        ok, details = _probe_video(file_path)
        if not ok:
            return "transcode", f"auto => transcode (file={file_path.name}: {details})"

    return "copy", "auto => copy (all files are RTSP-friendly H.264)"


def build_ffmpeg_command(stream: ResolvedStream, codec_mode: str) -> list[str]:
    args: list[str] = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-re"]

    if stream.options.loop:
        args += ["-stream_loop", "-1"]

    if stream.mode == "playlist":
        if stream.playlist_path is None:
            raise ValueError(f"playlist_path is missing for stream {stream.name}")
        args += ["-f", "concat", "-safe", "0", "-i", str(stream.playlist_path)]
    else:
        args += ["-i", str(stream.files[0])]

    if codec_mode == "copy":
        args += ["-c", "copy"]
    else:
        args += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "zerolatency",
            "-r",
            str(stream.options.fps),
            "-s",
            f"{stream.options.width}x{stream.options.height}",
        ]

    if stream.options.audio:
        if codec_mode == "copy":
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", "aac", "-b:a", "128k"]
    else:
        args += ["-an"]

    # Publish via RTSP-over-TCP for better reliability on localhost/firewalled hosts.
    args += ["-rtsp_transport", "tcp", "-f", "rtsp", stream.publish_url]
    return args


def write_playlist_if_needed(stream: ResolvedStream) -> None:
    if stream.mode != "playlist":
        return
    if stream.playlist_path is None:
        raise ValueError(f"playlist_path is missing for stream {stream.name}")
    create_concat_playlist(stream.playlist_path, stream.files)


def start_stream(paths: Paths, stream: ResolvedStream) -> StartResult:
    p_path = pid_file(paths, stream.name)
    existing_pid = read_pid(p_path)
    if existing_pid:
        return StartResult(
            stream_name=stream.name,
            started=False,
            message=f"already has PID file with pid={existing_pid}; stop it first",
        )

    write_playlist_if_needed(stream)
    codec_mode, codec_reason = choose_codec_mode(stream)
    command = build_ffmpeg_command(stream, codec_mode)
    log_path = log_file(paths, stream.name)
    log_handle = log_path.open("ab")
    log_handle.write(
        f"[codec-select] requested={stream.options.codec_mode}, effective={codec_mode}, reason={codec_reason}\n".encode(
            "utf-8", errors="replace"
        )
    )
    log_handle.flush()

    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=log_handle,
            stderr=log_handle,
            cwd=str(paths.root),
            preexec_fn=os.setsid,
        )
    except FileNotFoundError:
        log_handle.close()
        return StartResult(stream_name=stream.name, started=False, message="ffmpeg binary not found")
    except Exception as exc:  # noqa: BLE001
        log_handle.close()
        return StartResult(stream_name=stream.name, started=False, message=f"failed to start ffmpeg: {exc}")

    write_pid(p_path, process.pid)
    return StartResult(
        stream_name=stream.name,
        started=True,
        message=f"started (pid={process.pid}) mode={codec_mode} command={shlex.join(command)}",
        pid=process.pid,
    )


def stop_stream_by_pid(pid: int) -> bool:
    try:
        os.killpg(pid, 15)
    except ProcessLookupError:
        return True
    except Exception:
        try:
            os.kill(pid, 15)
        except Exception:
            return False
    return True
