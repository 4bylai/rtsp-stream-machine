from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - runtime environment dependent
    yaml = None

from utils import Paths, resolve_path


@dataclass(frozen=True)
class ServerConfig:
    rtsp_host: str
    rtsp_port: int
    mediamtx_publish_host: str


@dataclass(frozen=True)
class StreamOptions:
    loop: bool
    codec_mode: str
    audio: bool
    fps: int
    width: int
    height: int


@dataclass(frozen=True)
class ResolvedStream:
    name: str
    mode: str
    files: list[Path]
    playlist_path: Path | None
    options: StreamOptions
    publish_url: str
    public_url: str
    source_group: str | None = None


@dataclass(frozen=True)
class LoadedConfig:
    server: ServerConfig
    streams: list[ResolvedStream]


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def load_config(config_path: Path, paths: Paths) -> tuple[LoadedConfig | None, list[str]]:
    errors: list[str] = []
    if yaml is None:
        return None, ["PyYAML is not installed. Run: pip install -r requirements.txt"]
    if not config_path.exists():
        return None, [f"Config file not found: {config_path}"]

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return None, [f"Invalid YAML in {config_path}: {exc}"]

    server_raw = raw.get("server", {})
    defaults_raw = raw.get("defaults", {})
    streams_raw = raw.get("streams", [])

    publish_host_override = os.environ.get("STREAM_MEDIAMTX_PUBLISH_HOST", "").strip()
    mediamtx_publish_host = publish_host_override or str(server_raw.get("mediamtx_publish_host", "127.0.0.1"))

    server = ServerConfig(
        rtsp_host=str(server_raw.get("rtsp_host", "127.0.0.1")),
        rtsp_port=int(server_raw.get("rtsp_port", 8554)),
        mediamtx_publish_host=mediamtx_publish_host,
    )

    default_opts = StreamOptions(
        loop=_as_bool(defaults_raw.get("loop"), True),
        codec_mode=str(defaults_raw.get("codec_mode", "auto")).lower(),
        audio=_as_bool(defaults_raw.get("audio"), False),
        fps=_as_int(defaults_raw.get("fps"), 25),
        width=_as_int(defaults_raw.get("width"), 1280),
        height=_as_int(defaults_raw.get("height"), 720),
    )

    if not isinstance(streams_raw, list):
        return None, ["config.streams must be a list"]

    resolved: list[ResolvedStream] = []

    for idx, row in enumerate(streams_raw, start=1):
        if not isinstance(row, dict):
            errors.append(f"streams[{idx}] must be a mapping")
            continue

        base_name = str(row.get("name", "")).strip()
        mode = str(row.get("mode", "single")).strip().lower()
        raw_files = row.get("files", [])

        if not base_name:
            errors.append(f"streams[{idx}] has empty name")
            continue

        if mode not in {"single", "playlist", "separate", "multi"}:
            errors.append(f"streams[{idx}] invalid mode '{mode}' for stream '{base_name}'")
            continue

        if not isinstance(raw_files, list) or not raw_files:
            errors.append(f"stream '{base_name}' has empty or invalid files list")
            continue

        files = [resolve_path(paths.root, str(item)) for item in raw_files]

        stream_opts = StreamOptions(
            loop=_as_bool(row.get("loop"), default_opts.loop),
            codec_mode=str(row.get("codec_mode", default_opts.codec_mode)).lower(),
            audio=_as_bool(row.get("audio"), default_opts.audio),
            fps=_as_int(row.get("fps"), default_opts.fps),
            width=_as_int(row.get("width"), default_opts.width),
            height=_as_int(row.get("height"), default_opts.height),
        )

        if stream_opts.codec_mode not in {"copy", "transcode", "auto"}:
            errors.append(
                f"stream '{base_name}' has invalid codec_mode '{stream_opts.codec_mode}'"
            )
            continue

        if mode == "single":
            one_file = [files[0]]
            resolved.append(
                _build_stream(
                    name=base_name,
                    mode="single",
                    files=one_file,
                    options=stream_opts,
                    server=server,
                    playlist_path=None,
                    source_group=None,
                )
            )
            continue

        if mode == "playlist":
            playlist_path = paths.playlists_dir / f"{base_name}.txt"
            resolved.append(
                _build_stream(
                    name=base_name,
                    mode="playlist",
                    files=files,
                    options=stream_opts,
                    server=server,
                    playlist_path=playlist_path,
                    source_group=None,
                )
            )
            continue

        # separate / multi => expand each file into independent single stream
        for sidx, file_path in enumerate(files, start=1):
            expanded_name = f"{base_name}_{sidx:03d}"
            resolved.append(
                _build_stream(
                    name=expanded_name,
                    mode="single",
                    files=[file_path],
                    options=stream_opts,
                    server=server,
                    playlist_path=None,
                    source_group=base_name,
                )
            )

    # Validate duplicate names after expansion
    name_set: set[str] = set()
    for stream in resolved:
        if stream.name in name_set:
            errors.append(f"duplicate stream name detected: {stream.name}")
        name_set.add(stream.name)

    if errors:
        return None, errors

    return LoadedConfig(server=server, streams=resolved), []


def _build_stream(
    name: str,
    mode: str,
    files: list[Path],
    options: StreamOptions,
    server: ServerConfig,
    playlist_path: Path | None,
    source_group: str | None,
) -> ResolvedStream:
    publish_url = f"rtsp://{server.mediamtx_publish_host}:{server.rtsp_port}/{name}"
    public_url = f"rtsp://{server.rtsp_host}:{server.rtsp_port}/{name}"
    return ResolvedStream(
        name=name,
        mode=mode,
        files=files,
        playlist_path=playlist_path,
        options=options,
        publish_url=publish_url,
        public_url=public_url,
        source_group=source_group,
    )
