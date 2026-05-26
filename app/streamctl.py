from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from config_loader import LoadedConfig, ResolvedStream, load_config
from ffmpeg_runner import start_stream
from utils import (
    binary_exists,
    check_tcp,
    cleanup_stale_pid,
    ensure_runtime_dirs,
    get_paths,
    is_process_running,
    log_file,
    pid_file,
    read_pid,
    remove_pid,
)


def eprint(text: str) -> None:
    print(text, file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RTSP Stream Machine controller")
    parser.add_argument(
        "command",
        choices=["start", "stop", "restart", "status", "urls", "probe"],
        help="Control command",
    )
    parser.add_argument(
        "--config",
        default="config/streams.yaml",
        help="Path to streams YAML config (default: config/streams.yaml)",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional files for probe command. If omitted, probe all files from config.",
    )
    return parser.parse_args()


def load_project_config(config_path: Path) -> tuple[LoadedConfig | None, list[str]]:
    paths = get_paths()
    return load_config(config_path, paths)


def cleanup_stale_pid_files(streams: list[ResolvedStream]) -> None:
    paths = get_paths()
    known = {s.name for s in streams}

    # Cleanup stale for known streams.
    for name in known:
        cleanup_stale_pid(pid_file(paths, name))

    # Cleanup stale orphan pid files too.
    for p in paths.pids_dir.glob("*.pid"):
        if p.stem in known:
            continue
        cleanup_stale_pid(p)


def validate_stream_files(stream: ResolvedStream) -> list[str]:
    errors: list[str] = []
    for file_path in stream.files:
        if not file_path.exists():
            errors.append(f"[{stream.name}] file not found: {file_path}")
    return errors


def stop_pid(pid: int, timeout_sec: float = 5.0) -> bool:
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

    # Force kill if needed.
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            return False

    return not is_process_running(pid)


def cmd_start(config_path: Path) -> int:
    paths = get_paths()
    ensure_runtime_dirs(paths)

    if not binary_exists("ffmpeg"):
        eprint("ERROR: ffmpeg not found in PATH")
        return 2

    loaded, errors = load_project_config(config_path)
    if errors:
        for item in errors:
            eprint(f"ERROR: {item}")
        return 2
    assert loaded is not None

    cleanup_stale_pid_files(loaded.streams)

    if not check_tcp(loaded.server.mediamtx_publish_host, loaded.server.rtsp_port):
        eprint(
            "WARNING: MediaMTX is not reachable at "
            f"{loaded.server.mediamtx_publish_host}:{loaded.server.rtsp_port}. "
            "FFmpeg streams may fail to publish until MediaMTX is up."
        )

    started_urls: list[str] = []
    had_errors = False

    for stream in loaded.streams:
        file_errors = validate_stream_files(stream)
        if file_errors:
            had_errors = True
            for msg in file_errors:
                eprint(f"ERROR: {msg}")
            continue

        p_path = pid_file(paths, stream.name)
        pid = read_pid(p_path)
        if pid and is_process_running(pid):
            print(f"SKIP: {stream.name} already running (pid={pid})")
            started_urls.append(stream.public_url)
            continue
        if pid:
            remove_pid(p_path)

        result = start_stream(paths, stream)
        if result.started:
            print(f"OK: {stream.name} {result.message}")
            started_urls.append(stream.public_url)
        else:
            had_errors = True
            eprint(f"ERROR: {stream.name}: {result.message}")

    if started_urls:
        print("\nRTSP URLs:")
        for url in started_urls:
            print(url)

    return 1 if had_errors else 0


def cmd_stop() -> int:
    paths = get_paths()
    ensure_runtime_dirs(paths)

    pid_files = sorted(paths.pids_dir.glob("*.pid"))
    if not pid_files:
        print("No active PID files found.")
        return 0

    had_errors = False
    for p_file in pid_files:
        stream_name = p_file.stem
        pid = read_pid(p_file)

        if pid is None:
            print(f"CLEAN: {stream_name} invalid PID file removed")
            remove_pid(p_file)
            continue

        if not is_process_running(pid):
            print(f"CLEAN: {stream_name} stale PID removed (pid={pid})")
            remove_pid(p_file)
            continue

        stopped = stop_pid(pid)
        if stopped:
            print(f"STOPPED: {stream_name} (pid={pid})")
            remove_pid(p_file)
        else:
            had_errors = True
            eprint(f"ERROR: failed to stop stream {stream_name} (pid={pid})")

    return 1 if had_errors else 0


def cmd_status(config_path: Path) -> int:
    paths = get_paths()
    ensure_runtime_dirs(paths)

    loaded, errors = load_project_config(config_path)
    if errors:
        for item in errors:
            eprint(f"ERROR: {item}")
        return 2
    assert loaded is not None

    cleanup_stale_pid_files(loaded.streams)

    print(
        "stream\tmode\tpid\tstatus\tlog\turl"
    )
    for stream in loaded.streams:
        p_path = pid_file(paths, stream.name)
        pid = read_pid(p_path)
        if pid and is_process_running(pid):
            status = "running"
        else:
            status = "stopped"
            if pid and not is_process_running(pid):
                remove_pid(p_path)
                pid = None

        mode = "separate" if stream.source_group else stream.mode
        log_path = log_file(paths, stream.name)
        pid_text = str(pid) if pid else "-"
        print(
            f"{stream.name}\t{mode}\t{pid_text}\t{status}\t{log_path}\t{stream.public_url}"
        )

    # Show orphan PID files that are still alive.
    known = {s.name for s in loaded.streams}
    orphan_shown = False
    for p_file in sorted(paths.pids_dir.glob("*.pid")):
        if p_file.stem in known:
            continue
        pid = read_pid(p_file)
        if pid and is_process_running(pid):
            if not orphan_shown:
                print("\nOrphan processes:")
                orphan_shown = True
            print(f"{p_file.stem}\torphan\t{pid}\trunning\t{log_file(paths, p_file.stem)}\t-")

    return 0


def cmd_urls(config_path: Path) -> int:
    loaded, errors = load_project_config(config_path)
    if errors:
        for item in errors:
            eprint(f"ERROR: {item}")
        return 2
    assert loaded is not None

    for stream in loaded.streams:
        print(stream.public_url)
    return 0


def probe_file(file_path: Path) -> tuple[bool, str]:
    command = [
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
        completed = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    except FileNotFoundError:
        return False, "ffprobe not found in PATH"

    if completed.returncode != 0:
        return False, completed.stderr.strip() or "ffprobe failed"

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return False, "ffprobe returned non-JSON output"

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )

    if not video_stream:
        return False, "no video stream found"

    codec = video_stream.get("codec_name", "unknown")
    width = video_stream.get("width", "?")
    height = video_stream.get("height", "?")
    fps_raw = video_stream.get("r_frame_rate", "0/0")
    duration = data.get("format", {}).get("duration", "?")
    return (
        True,
        f"codec={codec}, resolution={width}x{height}, fps={fps_raw}, duration={duration}s",
    )


def cmd_probe(config_path: Path, files: list[str]) -> int:
    if not binary_exists("ffprobe"):
        eprint("ERROR: ffprobe not found in PATH")
        return 2

    paths = get_paths()

    targets: list[Path] = []
    if files:
        targets = [Path(x).resolve() for x in files]
    else:
        loaded, errors = load_project_config(config_path)
        if errors:
            for item in errors:
                eprint(f"ERROR: {item}")
            return 2
        assert loaded is not None
        seen = set()
        for stream in loaded.streams:
            for fp in stream.files:
                if fp not in seen:
                    seen.add(fp)
                    targets.append(fp)

    if not targets:
        print("No files to probe.")
        return 0

    had_errors = False
    for target in targets:
        if not target.exists():
            had_errors = True
            eprint(f"ERROR: file not found: {target}")
            continue

        ok, details = probe_file(target)
        if ok:
            print(f"OK: {target} -> {details}")
        else:
            had_errors = True
            eprint(f"ERROR: {target} -> {details}")

    return 1 if had_errors else 0


def main() -> int:
    args = parse_args()
    paths = get_paths()
    config_path = (paths.root / args.config).resolve()

    if args.command == "start":
        return cmd_start(config_path)
    if args.command == "stop":
        return cmd_stop()
    if args.command == "restart":
        stop_code = cmd_stop()
        start_code = cmd_start(config_path)
        return 1 if (stop_code != 0 or start_code != 0) else 0
    if args.command == "status":
        return cmd_status(config_path)
    if args.command == "urls":
        return cmd_urls(config_path)
    if args.command == "probe":
        return cmd_probe(config_path, args.files)

    eprint(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
