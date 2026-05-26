# RTSP Stream Machine

`RTSP Stream Machine` is a lightweight manager for test RTSP streams from local video files.

Documentation:
- VM deployment guide: `docs/DEPLOY_VM_RU.md`
- User guide: `docs/USER_GUIDE_RU.md`

Stack:
- MediaMTX (RTSP server)
- FFmpeg (stream publishing)
- Python CLI (stream control, PIDs, logs, playlists)

## Project layout

```text
rtsp-stream-machine/
├── docker-compose.yml
├── README.md
├── config/
│   ├── streams.example.yaml
│   └── mediamtx.yml
├── videos/
├── playlists/
├── logs/
├── pids/
├── app/
│   ├── streamctl.py
│   ├── ffmpeg_runner.py
│   ├── config_loader.py
│   └── utils.py
└── requirements.txt
```

## Requirements

- Docker + Docker Compose
- Python 3.10+
- FFmpeg and ffprobe on `PATH`

Check:

```bash
ffmpeg -version
ffprobe -version
```

## Installation

```bash
cd rtsp-stream-machine
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/streams.example.yaml config/streams.yaml
```

`config/streams.yaml` and `config/video_meta.json` are not committed — keep them local.

## Run with Docker Compose (recommended)

```bash
docker compose up -d --build
```

Check containers:

```bash
docker compose ps
```

After startup:
- UI/API: `http://127.0.0.1:8090/`
- RTSP: `rtsp://<SERVER_IP>:8554/<stream_name>`

## Stream configuration

Edit `config/streams.yaml`.

Key fields:
- `server.rtsp_host` — IP/host visible to external clients (public URL).
- `server.mediamtx_publish_host` — host FFmpeg uses to publish into MediaMTX locally.
  - for backend on the host, usually `127.0.0.1`;
  - in Docker Compose, `mediamtx` is used via `STREAM_MEDIAMTX_PUBLISH_HOST`.
- `defaults.codec_mode`: `copy`, `transcode`, `auto` (default `auto`).
- `streams[].mode`: `single`, `playlist`, `separate` (`multi` is supported as an alias).

## Modes

1. `single`
- One file → one RTSP stream.
- Example: `videos/entrance_01.mp4` → `.../cam001`.

2. `playlist`
- Multiple files → one looping stream.
- Creates `playlists/<stream>.txt` automatically.

3. `separate` / `multi`
- Multiple files → multiple independent streams.
- For group `group_test` you get:
  - `group_test_001`
  - `group_test_002`
  - `group_test_003`

## CLI control

```bash
python app/streamctl.py start
python app/streamctl.py stop
python app/streamctl.py restart
python app/streamctl.py status
python app/streamctl.py urls
```

Extra (probe videos with ffprobe):

```bash
python app/streamctl.py probe
python app/streamctl.py probe videos/entrance_01.mp4
```

## What `start` does

- Reads `config/streams.yaml`
- Expands `separate/multi` into individual cameras
- Creates playlist files for `playlist` mode
- Validates file presence
- Starts FFmpeg processes
- Writes PID files (`pids/<stream>.pid`)
- Writes per-stream logs (`logs/<stream>.log`)
- Prints RTSP URLs

## Errors and reliability

- Missing video file: stream is skipped with a clear error.
- Duplicate stream names: config is rejected.
- `ffmpeg` not found: `start` fails.
- MediaMTX unavailable: warning is logged.
- Stale PID file (dead process): PID file is removed automatically.

## Public URL vs publish URL

- FFmpeg publishes locally to MediaMTX:
  - `rtsp://127.0.0.1:8554/cam001`
- External client connects to the stream machine:
  - `rtsp://<YOUR_HOST>:8554/cam001`

## Verify streams

With ffprobe:

```bash
ffprobe rtsp://<YOUR_HOST>:8554/cam001
```

With VLC:

```text
rtsp://<YOUR_HOST>:8554/cam001
```

## Tips

- For lowest CPU load, use `codec_mode: copy`.
- For best VMS/analytics compatibility, use `codec_mode: transcode`.
- Recommended default: `codec_mode: auto`.
  - `auto` runs `ffprobe` on each file.
  - If the file is compatible (H.264 and safe parameters) → `copy`.
  - Otherwise → `transcode`.
- Before connecting from another host, check firewall and `8554/tcp` reachability.

## Web UI + Backend API

FastAPI backend and a UI that talks to the API:

- Backend: `app/api_server.py`
- UI: `web/index.html`, `web/styles.css`, `web/app.js`

Manual run (alternative to Docker backend):

```bash
cd rtsp-stream-machine
source .venv/bin/activate
pip install -r requirements.txt
python app/api_server.py
```

Open:

```text
http://127.0.0.1:8090/
```

Note:
- `http://127.0.0.1:8090/ui` and `.../ui/` redirect to `/`.

## Auto-recovery on failure

- `mediamtx` and `backend` in Docker Compose restart automatically (`restart: unless-stopped`).
- Backend stores desired stream state in `config/desired_streams_state.json`.
- If a stream should be running but FFmpeg died, a watchdog restarts it.
- Intentionally stopped streams are not restarted.

Control:
- `AUTO_RECOVER_STREAMS=true|false` (default `true`)

### Implemented APIs

- Streams API:
  - `GET /backend/streams`
  - `POST /backend/streams/{name}/start`
  - `POST /backend/streams/{name}/stop`
  - `POST /backend/streams/{name}/restart`
  - `POST /backend/streams/create-from-videos`
- Videos API:
  - `GET /backend/videos`
  - `POST /backend/videos` (upload)
  - `PATCH /backend/videos/{video_name}` (`enabled: true|false`)
  - `DELETE /backend/videos/{video_name}`
- Schedules API:
  - `GET /backend/schedules`
  - `POST /backend/schedules`
  - `PATCH /backend/schedules/{id}` (`enabled: true|false`)
  - `DELETE /backend/schedules/{id}`
- Service/infra API:
  - `GET /backend/health`
  - `GET /backend/storage`
  - `GET /backend/logs/important`

The UI shows streams/videos/schedules/storage/logs and supports start/stop/restart, upload/delete/enable/disable, and quick timers.
**Active streams** and **Video library** use a tile gallery sorted by upload date (newest first).
UI filters:
- Stream types (single / playlist / separate)
- Log types (info / warn / err)
The **Logs** section uses pagination (30 entries per page).

Header quick links:
- `/docs` (Swagger)
- `/redoc`
- `/openapi.json`
- `/backend/health`, `/backend/streams`, `/backend/videos`, `/backend/schedules`

### Auto-link videos to streams

In **Active streams**, use:

- **Start stream**

It:
- picks uploaded videos from `videos/`,
- creates stream entries in `config/streams.yaml`,
- starts the new streams immediately.

In the launch wizard:
- videos are listed vertically with numbering;
- search by filename is available;
- **Select all** applies to visible (filtered) items.

Selection mode:
- **OK** in the dialog: one shared `playlist` stream;
- **Cancel**: a separate `single` stream per video file.

### Schedule scheduler (runtime)

- `app/api_server.py` runs a background scheduler thread.
- Supports:
  - `cron` rules (`start/stop/delete`)
  - one-time `start_at` rules (`start/stop/delete`)
- For `delete` at runtime, soft-delete applies: the stream stops and moves to archived state in the UI.

### Archiving and retention

- The UI shows only active (`running`) streams.
- Stopped streams stay in config temporarily and are auto-cleaned after 7 days.
- Library videos are auto-deleted after `VIDEO_RETENTION_DAYS` (default 30).
- Stream logs are auto-deleted after `LOG_RETENTION_DAYS` (default 7).
