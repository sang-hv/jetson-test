# Jetson Nano/Orin Nano Setup - Production Package

Auto-provisioning toolkit for Jetson Nano/Orin Nano edge AI devices.
Includes: CSI camera streaming, AI detection pipeline, two-way audio, BLE OOBE, logic service (ZMQ + SQS), 4G failover, token-based auth, and remote config sync.

## Hardware Requirements

| Component | Description |
|-----------|-------------|
| Jetson Nano / Orin Nano | Main compute board |
| CSI Camera | IMX219 (default), connected via CSI ribbon |
| USB Speaker/Mic | Jabra or generic USB audio (echo cancel supported) |
| SIM7600 LTE Module | USB 4G modem for cellular failover |
| SSD (optional) | NVMe or SD card mounted at `/data` for storage |

## Directory Structure

```
setup-firstboot/
├── master-setup.sh                  # Entry point - runs install + services
├── install-software.sh              # Phase 1: OS packages, swap, go2rtc, cloudflared
├── setup-services.sh                # Phase 2: Deploy files, enable services, cronjobs
├── config/
│   ├── go2rtc.yaml                  # go2rtc streaming config
│   ├── nginx.conf                   # Reverse proxy + auth_request + cookie token
│   └── network.conf                 # Network mode/APN/watchdog settings
├── scripts/
│   ├── start-stream.py              # GStreamer pipeline (CSI → H.264 + AI SHM)
│   ├── setup-audio-autostart.sh     # PulseAudio + echo cancel on boot
│   ├── sync-config.py               # Cronjob: sync config, SQS creds, PIPELINE_TYPE, face embeddings
│   ├── device-update.py             # Cronjob: heartbeat (last_seen + software_version)
│   ├── run-update.sh                # OTA: git fetch + checkout + deploy + callback
│   ├── setup-4g.sh                  # SIM7600 modem init via ModemManager
│   ├── network-watchdog.sh          # Connectivity monitor + failover routing
│   ├── switch-network.sh            # CLI: switch network mode (auto/4g/lan/wifi)
│   └── cleanup-detections.sh        # Remove old detection images/logs
├── services/
│   ├── camera-stream.service        # GStreamer pipeline (WatchdogSec=60)
│   ├── go2rtc.service               # Streaming server
│   ├── backchannel.service          # Audio backchannel WebSocket
│   ├── person-count-ws.service      # ZMQ → WebSocket bridge
│   ├── stream-auth.service          # Nginx token validator
│   ├── device-update-server.service # OTA update server
│   ├── audio-autostart.service      # PulseAudio user service
│   ├── sim7600-4g.service           # 4G modem init
│   ├── network-watchdog.service     # Network failover daemon
│   ├── oobe-setup.service           # BLE WiFi/Network OOBE setup
│   ├── logic-service.service        # Logic Service (ZMQ subscriber + FastAPI)
│   ├── ai-core.service              # AI detection pipeline (CUDA)
│   ├── cleanup-detections.service   # Detection image cleanup (oneshot)
│   └── cleanup-detections.timer     # Cleanup timer
├── backchannel/
│   ├── server.py                    # WebSocket audio server (WebM/Opus + PCMU/G.711)
│   └── start.sh                     # PulseAudio env wrapper
├── device_update/
│   └── server.py                    # OTA update HTTP server (HMAC auth, port 8092)
├── person_count_ws/
│   ├── server.py                    # ZMQ SUB → WebSocket broadcast
│   └── start.sh                     # Exec wrapper
└── stream_auth/
    ├── server.py                    # HMAC-SHA256 token validation (Nginx auth_request)
    └── check_token.py               # CLI token checker

src/
├── ai_core/                         # AI detection pipeline
│   ├── main.py                      # Entry point (--device cuda)
│   ├── .env.example                 # Pipeline config template
│   ├── requirements.txt             # Python dependencies
│   ├── *.engine                     # TensorRT models
│   └── src/                         # Pipeline modules (detector, recognizer, tracker, etc.)
├── logic_service/                   # ZMQ event consumer + SQS sender + FastAPI
│   ├── main.py                      # FastAPI app (uvicorn :8095)
│   ├── .env.example                 # SQS config template
│   ├── requirements.txt             # Python dependencies
│   ├── database/                    # SQLite access
│   ├── schemas/                     # Pydantic models
│   └── services/                    # SQS sender, event handlers
└── oobe/
    └── jetson_backend/              # BLE GATT server for WiFi/Network OOBE
        ├── ble_wifi_setup.py        # Main BLE server (D-Bus/BlueZ)
        ├── config.py                # BLE config (PIN from DB, UUIDs)
        ├── wifi_manager.py          # NetworkManager WiFi control
        ├── gpio_handler.py          # GPIO button handler
        └── mode_selector.py         # Network mode selector
```

## Installation

```bash
# 1. Copy repo to Jetson
scp -r . user@jetson-ip:/home/user/mini-pc/

# 2. Run master setup (installs everything + enables services)
ssh user@jetson-ip
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh

# 3. With device identity prompt (for fresh/cloned devices)
sudo ./master-setup.sh --prompt-device-env

# 4. Reboot
sudo reboot
```

`master-setup.sh` runs two phases:
1. **install-software.sh** - apt packages, swap (8GB), go2rtc, cloudflared, GStreamer plugins
2. **setup-services.sh** - deploys configs/scripts to system paths, creates `/etc/device/device.env`, enables all services, installs cronjobs

### setup-services.sh Phases

| Phase | Description |
|-------|-------------|
| 1/11 | go2rtc stream services |
| 2/11 | Device identity and sync scripts |
| 3/11 | Cloudflared service check |
| 4/11 | Backchannel, person-count WS, and stream-auth |
| 5/11 | Device OTA update server |
| 6/11 | Nginx reverse proxy |
| 7/11 | Audio autostart |
| 8/11 | SIM7600 scripts/services |
| 9/11 | OOBE BLE setup |
| 10/11 | Logic Service (ZMQ + FastAPI) |
| 11/11 | AI Core detection pipeline |

### Device Identity (`--prompt-device-env`)

When cloning or provisioning a new device, use the `--prompt-device-env` flag:

```bash
sudo ./master-setup.sh --prompt-device-env
```

This interactively prompts for:
- `DEVICE_ID` — unique camera UUID from backend
- `BACKEND_URL` — API base URL
- `SECRET_KEY` — HMAC signing key

Values are saved to `/etc/device/device.env` and used by `sync-config.py` and `device-update.py`.

## Service Management

Both `master-setup.sh` and `setup-services.sh` support restart flags. All commands **always deploy + enable first**, then restart based on arguments.

### setup-services.sh

```bash
# Deploy + enable only (no restart)
sudo ./setup-services.sh

# Deploy + enable + restart ALL services
sudo ./setup-services.sh --restart-all

# Deploy + enable + restart specific services
sudo ./setup-services.sh network-watchdog go2rtc nginx

# Invalid service name → error + list valid services
sudo ./setup-services.sh invalid-name
```

### master-setup.sh

Same arguments, but also runs `install-software.sh` before `setup-services.sh`:

```bash
# Full setup (install + deploy + enable)
sudo ./master-setup.sh

# Full setup + restart ALL services
sudo ./master-setup.sh --restart-all

# Full setup + restart specific services
sudo ./master-setup.sh network-watchdog go2rtc
```

## Architecture Overview

```
                        ┌──────────────────────────────────────────────────┐
                        │              Jetson Nano / Orin Nano              │
                        │                                                  │
  CSI Camera ──────────►│ start-stream.py (GStreamer)                      │
  (IMX219)              │   ├─ Video: nvarguscamerasrc → H.264 → ─┐        │
                        │   ├─ Audio: echocancel_source → AAC →   ├───────►│ go2rtc :1984
                        │   │                              mpegtsmux       │   ├─ MSE
                        │   └─ AI: 5fps BGR → /dev/shm/ ─────────────────►│   ├─ WebRTC
                        │         (shared memory for ai_core)              │   └─ RTMP
                        │                                                  │
                        │ ai-core (systemd)                                │
                        │   ├─ YOLO detection + InsightFace recognition    │
                        │   ├─ Pipeline: home / shop / enterprise          │
                        │   ├─ ZMQ PUB tcp://127.0.0.1:5555 ─────────────►│ logic-service :8095
                        │   └─ .env: PIPELINE_TYPE set by sync-config      │   ├─ ZMQ SUB → events
                        │                                                  │   └─ AWS SQS sender
                        │                                                  │
  USB Mic/Speaker ─────►│ PulseAudio + echo cancel                        │
  (Jabra)               │   ├─ echocancel_source (mic in)                  │
                        │   └─ echocancel_sink (speaker out) ◄─────────────│
                        │                                                  │
  Browser/App ─────────►│ nginx :80 (reverse proxy)                        │
                        │   ├─ /api/*       → go2rtc :1984         [auth]  │
                        │   ├─ /backchannel → backchannel :8080    [auth]  │
                        │   ├─ /detections  → person-count :8090   [auth]  │
                        │   ├─ /detection/* → saved images         [auth]  │
                        │   └─ auth_request → stream-auth :8091            │
                        │       (query param token + cookie fallback)      │
                        │                                                  │
  Mobile (BLE) ────────►│ oobe-setup (systemd)                             │
                        │   ├─ BLE GATT server (BlueZ/D-Bus)              │
                        │   ├─ WiFi provisioning, network config           │
                        │   └─ PIN from DB (bluetooth_password)            │
                        │                                                  │
  Backend API ◄────────►│ sync-config.py (cron 5min)                       │
                        │   ├─ camera_settings, ai_rules                   │
                        │   ├─ detection_zones, face_embeddings            │
                        │   ├─ cloudflare tunnel token                     │
                        │   ├─ SQS credentials → logic-service .env        │
                        │   ├─ PIPELINE_TYPE → ai-core .env                │
                        │   └─ restart ai-core on facility/face change     │
                        │                                                  │
  Backend API ─────────►│ device-update-server :8092 (OTA)                 │
  (via tunnel)          │   └─ POST /update → run-update.sh                │
                        │       ├─ git fetch + checkout <tag>               │
                        │       ├─ setup-services.sh --restart-all          │
                        │       └─ callback ACK → backend API               │
                        │                                                  │
  SIM7600 4G ──────────►│ network-watchdog.sh                              │
  LAN / WiFi            │   └─ auto failover: LAN > WiFi > 4G              │
                        └──────────────────────────────────────────────────┘
```

## Services Reference

| Service | Port | Type | Purpose |
|---------|------|------|---------|
| camera-stream | - | notify | CSI camera → MPEG-TS stream + AI shared memory |
| go2rtc | 1984 | simple | Video/audio streaming (MSE, WebRTC, RTMP) |
| ai-core | - | simple | AI detection pipeline (YOLO + InsightFace, CUDA) |
| logic-service | 8095 | simple | ZMQ event subscriber + SQS sender + FastAPI |
| oobe-setup | - | simple | BLE GATT server for WiFi/network OOBE |
| backchannel | 8080 | simple | Client audio → speaker (WebSocket) |
| person-count-ws | 8090 | simple | AI person count → WebSocket broadcast |
| stream-auth | 8091 | simple | Token validation for nginx auth_request |
| device-update-server | 8092 | simple | OTA update endpoint (backend → device) |
| nginx | 80 | - | Reverse proxy + auth routing |
| sim7600-4g | - | oneshot | 4G modem initialization |
| network-watchdog | - | simple | Connectivity monitor + failover |
| audio-autostart | - | oneshot (user) | PulseAudio + echo cancel setup |
| cloudflared | - | simple | Cloudflare tunnel (token from backend) |
| cleanup-detections | - | oneshot (timer) | Remove old detection images/logs |

### Service Dependencies

```
camera-stream
  ├─► go2rtc (After, Wants)
  ├─► ExecStartPre: setup-audio-autostart.sh (creates fresh echo cancel)
  └─► start-stream.py connects to echocancel_source

ai-core (After: camera-stream)
  ├─► Reads BGR frames from /dev/shm (shared memory)
  ├─► YOLO detection + InsightFace recognition
  └─► ZMQ PUB tcp://127.0.0.1:5555

logic-service (After: network.target)
  ├─► ZMQ SUB from ai-core :5555
  ├─► FastAPI on 127.0.0.1:8095
  └─► Sends detection events to AWS SQS

oobe-setup (After: bluetooth.target)
  ├─► BLE GATT server with idle watchdog (10min)
  └─► PIN loaded from SQLite (bluetooth_password)

backchannel (independent, Restart=always)
  ├─► ExecStartPre: check echocancel_sink exists, create only if missing
  └─► pacat --device echocancel_sink

device-update-server (independent, Restart=always)
  └─► stream-auth (After, Wants — ensures auth service is ready)

sim7600-4g ──► network-watchdog (Wants, parallel start)
```

## Backend Sync (sync-config.py)

Runs every 5 minutes via crontab. Handles multiple sync responsibilities:

### API Calls

| API | Purpose |
|-----|---------|
| `GET /api/v1/cameras/{id}/config` | Sync settings, rules, zones, cloudflare token, SQS config |
| `GET /api/v1/cameras/{id}/face-embeddings` | Paginated face embedding sync |

Auth headers: `X-Device-ID`, `X-Timestamp`, `X-Signature` (HMAC-SHA256).
Device identity: `/etc/device/device.env` (DEVICE_ID, BACKEND_URL, SECRET_KEY).

### Synced Data (SQLite → `/data/mini-pc/db/logic_service.db`)

| Table | Content |
|-------|---------|
| `camera_settings` | stream_secret_key, stream_view_duration, bluetooth_password, facility, ai_threshold, image_retention_days |
| `ai_rules` | Detection rules: name, code, member_ids, time/weekday constraints |
| `detection_zones` | Polygons with coordinates (JSON), direction points |
| `face_embeddings` | User face vectors (paginated sync with updated_at tracking) |

### Environment File Sync

| Target | Key | Source |
|--------|-----|--------|
| `/opt/logic_service/.env` | AWS_SQS_REGION, AWS_SQS_QUEUE_URL, AWS_SQS_ACCESS_KEY_ID, AWS_SQS_SECRET_ACCESS_KEY | API response |
| `/opt/ai_core/.env` | PIPELINE_TYPE | Mapped from `facility`: Family→home, Store→shop, Enterprise→enterprise |

### Service Restart Triggers

| Service | Restart When |
|---------|-------------|
| `logic-service` | SQS credentials in `.env` changed |
| `ai-core` | `PIPELINE_TYPE` changed (facility changed) OR `face_embeddings.updated_at` changed |
| `cloudflared` | Tunnel token changed |

## AI Core Pipeline

The AI detection pipeline (`ai-core.service`) runs on CUDA and supports three pipeline types based on the facility:

| PIPELINE_TYPE | Facility | Features |
|---------------|----------|----------|
| `home` | Family | Zone counting, stranger/animal alerts, passerby detection |
| `shop` | Store | Basic detection + recognition |
| `enterprise` | Enterprise | Full detection + recognition + PPE/mask violation alerts |

Configuration is in `/opt/ai_core/.env` (synced from `.env.example`, `PIPELINE_TYPE` managed by `sync-config.py`).

### Key Settings (.env)

| Setting | Default | Description |
|---------|---------|-------------|
| PIPELINE_TYPE | home | Pipeline mode (home/shop/enterprise) |
| PERSON_CONFIDENCE_THRESHOLD | 0.4 | YOLO person detection confidence |
| MASK_DETECTION_ENABLED | true | Enable mask detection |
| PPE_DETECTION_ENABLED | true | Enable helmet/glove detection |
| COUNTING_ENABLED | false | Enable zone-based people counting |
| FACE_DB_SOURCE | folder | Face DB source (folder/sqlite) |
| VIDEO_SOURCE_TYPE | opencv | Video source (opencv/zmq/shm) |
| DISPLAY_ENABLED | true | GUI window (false for systemd) |

## BLE OOBE Setup

The OOBE (Out-of-Box Experience) service provides BLE-based WiFi/network provisioning from a mobile app.

- **Service**: `oobe-setup.service`
- **PIN**: Loaded from SQLite (`camera_settings.bluetooth_password`), fallback `123456`
- **Idle watchdog**: Auto-shutdown after 10 minutes of no BLE activity
- **Bluetooth reset**: Power-cycles adapter on startup to clear stale states
- **Restart**: `on-failure`, `RestartSec=10`

## Networking & 4G

### Network Modes

Config: `/etc/device/network.conf`

| Mode | Priority | Description |
|------|----------|-------------|
| `auto` | LAN > WiFi > 4G | Default. Uses best available |
| `lan` | LAN > 4G > WiFi | Prefer wired |
| `wifi` | WiFi > 4G > LAN | Prefer wireless |
| `4g` | 4G > LAN > WiFi | Force cellular |

### Switch Network Mode

```bash
sudo /opt/4g/switch-network.sh auto   # or: 4g, lan, wifi
```

### Watchdog Behavior

- Pings `PING_HOST` (default: 8.8.8.8) every `CHECK_INTERVAL` seconds (default: 30)
- After `MAX_RETRIES` failures (default: 3), restarts `sim7600-4g` service
- Sends SIGHUP on config reload to re-apply routing metrics
- Interface detection: eth0/enp* (LAN), wlan*/wlp* (WiFi), usb*/wwan* (4G)

## OTA Software Update

Devices can be updated remotely via backend API, triggered from mobile app.

### Flow

```
Mobile App                    Backend API                      Jetson Nano
    │                              │                                │
    ├─ POST /cameras/{id}/update ─►│                                │
    │   { version, run_install }   │                                │
    │                              ├─ POST /update (via tunnel) ───►│
    │                              │   (HMAC auth)                  ├─ Return 200 immediately
    │                              │                                ├─ git fetch origin
    │                              │◄─ 200 { status: accepted } ───┤  git checkout <tag>
    │                              │                                ├─ setup-services.sh --restart-all
    │                              ├─ update_logs.status =          │  (or master-setup.sh if run_install)
    │                              │    "in_progress"               │
    │                              │                                ├─ Health check services
    │                              │◄─ PATCH /update-logs/{id}/ack ┤
    │                              │   { status, version, error }   │
    │◄── query update_logs ────────┤                                │
    │    (success / failed)        │                                │
```

### Backend triggers update

Backend calls device directly through Cloudflare tunnel:

```
POST https://<device-tunnel-url>/update
Headers:
  X-Device-ID: <device_id>
  X-Timestamp: <unix_ts>
  X-Signature: HMAC-SHA256(secret_key, "{device_id}|{timestamp}")
Body:
  {
    "version": "v1.2.0",
    "run_install": false,
    "update_log_id": "uuid"
  }
```

- `version` — git tag or branch to checkout
- `run_install` — `true` runs `master-setup.sh` (apt install + deploy), `false` runs `setup-services.sh` only
- `update_log_id` — backend UUID for the device to ACK results

Device responds `200 { status: "accepted" }` immediately, then runs the update in background.

### Device callback

After update completes (or fails), device calls backend:

```
PATCH /api/v1/update-logs/{update_log_id}/ack
Headers: X-Device-ID, X-Timestamp, X-Signature (same HMAC scheme)
Body:
  { "status": "success", "software_version": "v1.2.0" }
  or
  { "status": "failed", "software_version": "v1.1.0", "error": "..." }
```

### Timeout handling

If device doesn't ACK within 1 hour, backend marks `update_logs.status = "failed"` on next query.

### Version tracking

- Device reports `software_version` (from `git describe --tags`) in every heartbeat (`device-update.py`, every 5 min)
- Backend stores in `cameras.software_version`
- Health check: `GET /update/health` returns `{ "status": "idle" | "updating" }`

### Update process (on device)

1. Acquire lock (`/tmp/device-update.lock`) — prevents concurrent updates
2. `git fetch origin`
3. `git checkout <version>`
4. Run `setup-services.sh --restart-all` (or `master-setup.sh --restart-all`)
5. Health check: verify `go2rtc`, `camera-stream`, `stream-auth`, `nginx` are running
6. ACK backend with result
7. Release lock

Logs: via `journalctl -u device-update-server` (and `journalctl -t device-update` if using logger). File logs are optional via `LOG_FILE=...`.

## Authentication

All nginx-proxied routes (except static pages) require a token via `?token=<base64url>` or `stream_token` cookie.

### Token Format

```json
{
  "payload": {
    "camera_id": "<DEVICE_ID>",
    "time_exp": "2026-03-27T15:30:00Z"
  },
  "signature": "<hex(HMAC-SHA256(camera_id, secret_key))>"
}
```

Base64url-encoded, validated by `stream-auth` on port 8091.
Secret key stored in SQLite table `camera_settings` (key: `stream_secret_key`).

### iOS HLS Cookie Fallback

iOS HLS players often drop query parameters for subsequent segment requests.
Nginx sets a `stream_token` cookie when `?token=` is first provided:

```
Set-Cookie: stream_token=<token>; Path=/; Max-Age=3600; SameSite=Lax
```

`stream-auth` checks both query param and cookie for token validation.

### Validate a Token (CLI)

```bash
python3 /opt/stream_auth/check_token.py <token> --device-id <UUID>
```

## AI Integration (Shared Memory)

`start-stream.py` writes raw BGR frames to shared memory for `ai_core` consumption:

| Path | Format |
|------|--------|
| `/dev/shm/mini_pc_ai_frames.bin` | 64-byte header + double-buffered BGR frames |

**SHM Header (64 bytes):**
- MAGIC: `MPAI`, version: 1
- width, height, stride, format (BGR=0)
- seq (frame counter), active_slot (0 or 1)

Consumer: `ai_core/src/shm_video_source.py` reads frames via matching protocol.

## File System Layout (After Install)

```
/etc/device/
├── device.env           # DEVICE_ID, BACKEND_URL, SECRET_KEY
├── network.conf         # NETWORK_MODE, APN, PING_HOST, CHECK_INTERVAL
├── repo-path            # Path to setup-firstboot git repo (for OTA)
├── config.json          # Last synced backend config
└── config.prev.json     # Previous config (for diff)

/opt/
├── stream/              # Camera pipeline
├── ai_core/             # AI detection pipeline (main.py, src/, .env, *.engine)
├── logic_service/       # Logic Service (main.py, database/, services/, .env)
├── oobe-setup/          # BLE OOBE (ble_wifi_setup.py, config.py, wifi_manager.py)
├── backchannel/         # Audio backchannel
├── person_count_ws/     # ZMQ→WS bridge
├── stream_auth/         # Token validator
├── device_update/       # OTA update server (server.py)
├── device/              # sync-config.py, device-update.py, run-update.sh
├── 4g/                  # setup-4g.sh, network-watchdog.sh, switch-network.sh
└── audio/               # setup-audio-autostart.sh

/data/mini-pc/           # (or ~/data if no SSD)
├── db/                  # SQLite: logic_service.db
├── media/               # Detection images
├── faces/               # Face crops
├── logs/                # App logs
└── models/              # ML models

/etc/go2rtc/go2rtc.yaml  # Streaming config
/etc/nginx/sites-available/go2rtc  # Reverse proxy
/dev/shm/mini_pc_ai_frames.bin     # AI shared memory (runtime)
```

## System Cloning

To clone a fully configured device to a new Jetson:

### 1. Create disk image (on running device)

```bash
# Full NVMe clone to USB drive
sudo dd if=/dev/nvme0n1 of=/media/usb/jetson-image.img bs=4M status=progress

# Compress (optional, saves ~60-70% space)
sudo dd if=/dev/nvme0n1 bs=4M status=progress | gzip > /media/usb/jetson-image.img.gz
```

### 2. Restore to new device

```bash
# From raw image
sudo dd if=jetson-image.img of=/dev/nvme0n1 bs=4M status=progress

# From compressed image
gunzip -c jetson-image.img.gz | sudo dd of=/dev/nvme0n1 bs=4M status=progress
```

### 3. Re-provision device identity

```bash
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh --prompt-device-env
# Enter new DEVICE_ID, BACKEND_URL, SECRET_KEY
sudo reboot
```

After reboot, `sync-config.py` will automatically pull the new device's configuration from the backend.

## Diagnostics

```bash
# All services status
sudo systemctl status camera-stream go2rtc ai-core logic-service oobe-setup \
  backchannel person-count-ws stream-auth device-update-server nginx \
  sim7600-4g network-watchdog cloudflared
systemctl --user status audio-autostart

# Logs (follow)
sudo journalctl -u camera-stream -f
sudo journalctl -u go2rtc -f
sudo journalctl -u ai-core -f
sudo journalctl -u logic-service -f
sudo journalctl -u oobe-setup -f
sudo journalctl -u backchannel -f
sudo journalctl -u network-watchdog -f

# Audio check
pactl list short sinks | grep -i "jabra\|echocancel"
pactl list short sources | grep -i "jabra\|echocancel"

# If USB mic/speaker is unplugged and replugged, re-run echo-cancel setup (user service)
systemctl --user restart audio-autostart

# Network check
ip route show                          # Current routing table
cat /etc/device/network.conf           # Network mode
cat /run/4g-interface                  # Active 4G interface
mmcli -L                               # Modem status

# AI shared memory
ls -la /dev/shm/mini_pc_ai_frames.bin

# AI Core .env
cat /opt/ai_core/.env | grep PIPELINE_TYPE

# Logic Service .env
cat /opt/logic_service/.env

# Backend sync
cat /etc/device/device.env             # Device identity
sudo journalctl -u cron --grep="sync-config\|device-update" --since="1 hour ago"

# OTA update
sudo systemctl status device-update-server   # Update server status
curl http://127.0.0.1:8092/health            # Update health check
sudo journalctl -u device-update-server -n 200 --no-pager
sudo journalctl -t device-update -n 200 --no-pager
cat /etc/device/repo-path                    # Git repo path
cd $(cat /etc/device/repo-path) && git describe --tags --always  # Current version

# Token validation
python3 /opt/stream_auth/check_token.py <token>
```

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| No video stream | `journalctl -u camera-stream` | Check CSI ribbon, restart `nvargus-daemon` |
| No audio | `pactl list short sinks` | Reconnect USB audio, restart `audio-autostart` |
| AI not detecting | `journalctl -u ai-core` | Check `.env` PIPELINE_TYPE, verify TensorRT engines exist |
| Logic service errors | `journalctl -u logic-service` | Check SQS credentials in `.env`, verify ZMQ port 5555 |
| BLE OOBE not working | `journalctl -u oobe-setup` | Check `bluetoothctl show`, restart oobe-setup |
| BLE OOBE hanging | `journalctl -u oobe-setup` | Service auto-recovers (idle watchdog); restart if needed |
| 4G not connecting | `mmcli -L`, `journalctl -u sim7600-4g` | Check SIM, verify APN in `network.conf` |
| Auth 401 on stream | `check_token.py <token>` | Verify token expiry, check `stream_secret_key` in DB |
| iOS stream fails with auth | Check `stream_token` cookie | Ensure nginx sets cookie, stream-auth reads it |
| No person count | `journalctl -u person-count-ws` | Verify ai_core is publishing on ZMQ :5555 |
| Backchannel no sound | `journalctl -u backchannel` | Check `echocancel_sink` exists, restart camera-stream |
| Config not syncing | `cat /etc/device/device.env` | Verify BACKEND_URL reachable, check SECRET_KEY |
| PIPELINE_TYPE wrong | `cat /opt/ai_core/.env` | Check facility in DB, verify sync-config ran |
| OTA update not responding | `systemctl status device-update-server` | Restart service, check `/etc/device/device.env` |
| OTA update failed | `journalctl -u device-update-server` | Check git access, disk space, service health |
| Version not reporting | `python3 /opt/device/device-update.py` | Check `repo-path`, git tags |
| Network keeps switching | `journalctl -u network-watchdog` | Adjust `CHECK_INTERVAL` / `MAX_RETRIES` in network.conf |
