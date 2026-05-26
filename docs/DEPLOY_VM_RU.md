# RTSP Stream Machine: Инструкция По Деплою На VM

## 1. Требования

- Linux VM (проверено на Oracle Linux 9.x)
- Docker + Docker Compose plugin
- Python 3.10+
- `ffmpeg` и `ffprobe`
- Открытые порты:
  - `8090/tcp` — Web UI + API
  - `8554/tcp` — RTSP

## 2. Проверка конфликтов портов

Перед запуском проверьте, что порты свободны:

```bash
ss -lntp | egrep ':8090|:8554' || true
docker ps --format '{{.Names}}\t{{.Ports}}' | egrep '8554|rtsp-mediamtx' || true
```

Если порты заняты, либо освободите их, либо смените порты в конфиге.

## 3. Установка системных зависимостей (Oracle Linux 9)

```bash
sudo dnf install -y git python3 python3-pip ffmpeg docker
sudo systemctl enable --now docker
```

## 4. Копирование проекта на VM

### Вариант A: из Git

```bash
git clone <REPO_URL>
cd rtsp-stream-machine
```

### Вариант B: из архива

```bash
tar -xzf rtsp-stream-machine-launch-kit-*.tar.gz
cd rtsp-stream-machine
```

## 5. Python окружение

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 6. Запуск сервисов (backend + MediaMTX)

```bash
docker compose up -d --build
docker compose ps
```

Проверка API:

```bash
curl http://127.0.0.1:8090/backend/health
```

Открыть UI:

```text
http://<VM_IP>:8090/
```

## 8. Firewall

```bash
sudo firewall-cmd --permanent --add-port=8090/tcp
sudo firewall-cmd --permanent --add-port=8554/tcp
sudo firewall-cmd --reload
```

## 9. Проверка RTSP

```bash
ffprobe rtsp://<VM_IP>:8554/<stream_name>
```

## 10. Troubleshooting

### 404 на `/backend/streams/create-from-videos`

Запущен старый backend. Перезапустите:

```bash
docker compose down
docker compose up -d --build
```

### UI не открывается

Проверить:

```bash
curl http://127.0.0.1:8090/backend/health
docker compose logs --tail=120 backend
```

### Стрим создан, но не запущен

Проверить ffmpeg-логи конкретного стрима:

```bash
ls logs/*.log
tail -n 120 logs/<stream_name>.log
```

## 11. Автозапуск stack через systemd (опционально)

Если нужен автозапуск `docker compose` после reboot, создайте `/etc/systemd/system/rtsp-stream-machine-compose.service`:

```ini
[Unit]
Description=RTSP Stream Machine (Docker Compose)
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=developer
WorkingDirectory=/home/developer/rtsp-stream-machine
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
RemainAfterExit=yes
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Применить:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rtsp-stream-machine-compose
sudo systemctl status rtsp-stream-machine-compose --no-pager
```

Готовые шаблоны unit-файлов есть в проекте:
- `docs/systemd/rtsp-stream-machine-compose.service`
- `docs/systemd/rtsp-stream-machine-api.service`
