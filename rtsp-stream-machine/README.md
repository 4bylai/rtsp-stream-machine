# RTSP Stream Machine

`RTSP Stream Machine` — простой менеджер тестовых RTSP-потоков из локальных видеофайлов.

Документация:
- Инструкция деплоя на VM: `docs/DEPLOY_VM_RU.md`
- Пользовательская инструкция: `docs/USER_GUIDE_RU.md`

Стек:
- MediaMTX (RTSP-сервер)
- FFmpeg (публикация потоков)
- Python CLI (управление стримами, PID, логи, плейлисты)

## Структура

```text
rtsp-stream-machine/
├── docker-compose.yml
├── README.md
├── config/
│   ├── streams.yaml
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

## Требования

- Docker + Docker Compose
- Python 3.10+
- FFmpeg и ffprobe в PATH

Проверка:

```bash
ffmpeg -version
ffprobe -version
```

## Установка

```bash
cd rtsp-stream-machine
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Запуск сервисов через Docker Compose (рекомендуется)

```bash
docker compose up -d --build
```

Проверка контейнеров:

```bash
docker compose ps
```

После запуска доступны:
- UI/API: `http://127.0.0.1:8090/`
- RTSP: `rtsp://<SERVER_IP>:8554/<stream_name>`

## Конфигурация стримов

Редактируйте `config/streams.yaml`.

Ключевые поля:
- `server.rtsp_host` — IP/хост, который увидит внешняя машина (публичный URL).
- `server.mediamtx_publish_host` — хост для локального publish из FFmpeg в MediaMTX.
  - при локальном запуске backend на хосте обычно `127.0.0.1`;
  - при запуске backend в Docker Compose автоматически используется `mediamtx` через env `STREAM_MEDIAMTX_PUBLISH_HOST`.
- `defaults.codec_mode`: `copy`, `transcode`, `auto` (по умолчанию `auto`).
- `streams[].mode`: `single`, `playlist`, `separate` (`multi` тоже поддерживается как alias).

## Режимы

1. `single`
- Один файл -> один RTSP stream.
- Пример: `videos/entrance_01.mp4` -> `.../cam001`.

2. `playlist`
- Несколько файлов -> один stream по кругу.
- Автоматически создается `playlists/<stream>.txt`.

3. `separate` / `multi`
- Несколько файлов -> несколько отдельных stream.
- Для группы `group_test` создаются:
  - `group_test_001`
  - `group_test_002`
  - `group_test_003`

## Управление

```bash
python app/streamctl.py start
python app/streamctl.py stop
python app/streamctl.py restart
python app/streamctl.py status
python app/streamctl.py urls
```

Дополнительно (проверка видео через ffprobe):

```bash
python app/streamctl.py probe
python app/streamctl.py probe videos/entrance_01.mp4
```

## Что делает `start`

- Читает `config/streams.yaml`
- Разворачивает `separate/multi` в отдельные камеры
- Создает playlist-файлы для `playlist`
- Проверяет наличие файлов
- Запускает FFmpeg процессы
- Создает PID-файлы (`pids/<stream>.pid`)
- Пишет лог каждого потока (`logs/<stream>.log`)
- Выводит RTSP URL

## Ошибки и надежность

- Если видеофайл не найден: stream пропускается с понятной ошибкой.
- Если имена stream дублируются: конфиг отклоняется.
- Если `ffmpeg` не найден: `start` завершится с ошибкой.
- Если MediaMTX недоступен: выводится warning.
- Если PID-файл stale (процесс мертв): PID-файл удаляется автоматически.

## Публичный URL и publish URL

- FFmpeg публикует локально в MediaMTX:
  - `rtsp://127.0.0.1:8554/cam001`
- Внешняя машина подключается к stream-machine:
  - `rtsp://192.168.1.50:8554/cam001`

## Проверка потоков

Через ffprobe:

```bash
ffprobe rtsp://192.168.1.50:8554/cam001
```

Через VLC:

```text
rtsp://192.168.1.50:8554/cam001
```

## Полезные замечания

- Для минимальной нагрузки CPU используйте `codec_mode: copy`.
- Для максимальной совместимости с VMS/аналитикой используйте `codec_mode: transcode`.
- Рекомендуемый режим по умолчанию: `codec_mode: auto`.
  - `auto` делает `ffprobe` каждого файла.
  - Если файл совместим (H.264 и безопасные параметры) -> `copy`.
  - Иначе -> `transcode`.
- Перед подключением с другой машины проверьте firewall и доступность порта `8554/tcp`.

## Web UI + Backend API

Реализован backend на FastAPI и UI, который работает через API:

- Backend: `app/api_server.py`
- UI: `web/index.html`, `web/styles.css`, `web/app.js`

Запуск вручную (альтернатива Docker backend):

```bash
cd rtsp-stream-machine
source .venv/bin/activate
pip install -r requirements.txt
python app/api_server.py
```

Открыть:

```text
http://127.0.0.1:8090/
```

Примечание:
- `http://127.0.0.1:8090/ui` и `.../ui/` редиректят на `/`.

## Автовосстановление при сбоях

- `mediamtx` и `backend` в Docker Compose поднимаются автоматически (`restart: unless-stopped`).
- Backend хранит "желаемое состояние" стримов в `config/desired_streams_state.json`.
- Если стрим должен быть запущен, но процесс FFmpeg упал, watchdog автоматически перезапустит его.
- Намеренно остановленные стримы не перезапускаются.

Управление:
- `AUTO_RECOVER_STREAMS=true|false` (по умолчанию `true`)

### Что уже работает

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

UI отображает streams/videos/schedules/storage/logs, умеет start/stop/restart, upload/delete/enable/disable, quick-timer.
В блоках `Активные стримы` и `Библиотека видео` используется плиточная галерея и сортировка по дате добавления (новые сверху).
Добавлены UI-фильтры:
- `Типы стримов` (single / playlist / separate)
- `Типы логов` (info / warn / err)
В блоке `Логи` используется пагинация по 30 записей на страницу.

Дополнительно в шапке UI есть быстрые ссылки:
- `/docs` (Swagger)
- `/redoc`
- `/openapi.json`
- `/backend/health`, `/backend/streams`, `/backend/videos`, `/backend/schedules`

### Автосвязка видео со стримами

В разделе `Активные стримы` есть кнопка:

- `Запустить стрим`

Она:
- берет загруженные видео из `videos/`,
- создает stream-записи в `config/streams.yaml`,
- сразу запускает новые стримы.

В мастере запуска:
- список видео отображается вертикально с нумерацией;
- доступен поиск по имени файла (`Поиск видео`);
- `Выбрать все` применяет выбор к видимым (отфильтрованным) элементам.

Режим выбора:
- `OK` в диалоге: один общий `playlist` stream;
- `Отмена`: отдельный `single` stream на каждый видеофайл.

### Планировщик расписаний (runtime)

- В `app/api_server.py` работает фоновый scheduler thread.
- Поддерживает:
  - `cron` правила (`start/stop/delete`)
  - one-time `start_at` правила (`start/stop/delete`)
- Для action `delete` в runtime выполняется soft-delete поведение: stream останавливается и уходит в archived-состояние в UI.

### Архивация и ретеншн

- В UI показываются только активные (`running`) стримы.
- Остановленные стримы хранятся в конфиге временно и автоочищаются через 7 дней.
- Видео в библиотеке автоудаляются через `VIDEO_RETENTION_DAYS` (по умолчанию 30 дней).
- Логи стримов автоочищаются через `LOG_RETENTION_DAYS` (по умолчанию 7 дней).
