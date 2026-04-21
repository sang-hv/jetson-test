# Architecture

## Overview

Mini PC is an AI-powered edge computing system built on NVIDIA Jetson Orin Nano. It provides real-time security monitoring, access control, and analytics by processing video streams locally with CUDA-accelerated AI models.

The system runs entirely on-device — video capture, AI inference, event processing, and cloud sync all happen at the edge, minimizing latency and bandwidth usage.

## Hardware

| Component | Description |
|-----------|-------------|
| NVIDIA Jetson Orin Nano | Main compute board (8GB RAM, CUDA cores) |
| CSI Camera (IMX219) | Video input via CSI ribbon cable |
| USB Mic/Speaker (Jabra) | Two-way audio with echo cancellation |
| SIM7600 LTE Module | USB 4G modem for cellular failover |
| NVMe SSD (256GB) | Primary storage mounted at `/data` |

## System Architecture

```
                        ┌──────────────────────────────────────────────────┐
                        │              Jetson Nano / Orin Nano             │
                        │                                                  │
  CSI Camera ──────────►│ start-stream.py (GStreamer)                      │
  (IMX219)              │   ├─ Video: nvarguscamerasrc → H.264 → ─┐        │
                        │   ├─ Audio: echocancel_source → AAC →   ├───────►│ go2rtc :1984
                        │   │                              mpegtsmux       │   ├─ MSE
                        │   └─ AI: 5fps BGR → /dev/shm/ ─────────────────► │   ├─ WebRTC
                        │         (shared memory for ai_core)              │   └─ RTMP
                        │                                                  │
                        │ ai-core (systemd)                                │
                        │   ├─ YOLO detection + InsightFace recognition    │
                        │   ├─ Pipeline: home / shop / enterprise          │
                        │   ├─ ZMQ PUB tcp://127.0.0.1:5555 ─────────────► │ logic-service :8095
                        │   └─ .env: PIPELINE_TYPE set by sync-config      │   ├─ ZMQ SUB → events
                        │                                                  │   └─ AWS SQS sender
                        │                                                  │
  USB Mic/Speaker ─────►│ PulseAudio + echo cancel                         │
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
                        │   ├─ BLE GATT server (BlueZ/D-Bus)               │
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
                        │       ├─ git fetch + checkout <tag>              │
                        │       ├─ setup-services.sh --restart-all         │
                        │       └─ callback ACK → backend API              │
                        │                                                  │
  SIM7600 4G ──────────►│ network-watchdog.sh                              │
  LAN / WiFi            │   └─ auto failover: LAN > WiFi > 4G              │
                        └──────────────────────────────────────────────────┘
```

## Core Components

### 1. Video Pipeline

The video pipeline captures frames from the CSI camera and distributes them to two consumers:

- **Streaming**: GStreamer encodes H.264 video + AAC audio into MPEG-TS, forwarded to go2rtc for multi-protocol delivery (MSE, WebRTC, RTMP).
- **AI Processing**: Raw BGR frames are written to shared memory (`/dev/shm/mini_pc_ai_frames.bin`) at 10fps for the AI pipeline to consume.

The shared memory uses a double-buffered protocol with a 64-byte header (magic, dimensions, sequence counter, active slot) to allow lock-free producer-consumer communication.

### 2. AI Detection Pipeline (ai-core)

CUDA-accelerated detection pipeline supporting three modes based on facility type:

| PIPELINE_TYPE | Facility | Features |
|---------------|----------|----------|
| `home` | Family | Zone counting, stranger/animal alerts, passerby detection |
| `shop` | Store | Basic detection + face recognition |
| `enterprise` | Enterprise | Full detection + recognition + PPE/mask violation alerts |

Key capabilities:
- **Object Detection**: YOLO with TensorRT optimization
- **Face Recognition**: InsightFace for identity matching
- **PPE Detection**: Helmet, glove, and mask compliance (enterprise mode)
- **Zone Counting**: Polygon-based people counting with direction tracking

Detection events are published via ZMQ (PUB/SUB) on `tcp://127.0.0.1:5555`.

### 3. Logic Service

Event processing service that bridges AI detections to the cloud:

- **ZMQ Subscriber**: Consumes detection events from ai-core
- **Event Processing**: Applies rules (time/weekday constraints, member filters) from SQLite
- **SQS Sender**: Forwards qualified events to AWS SQS for backend processing
- **FastAPI Server**: Exposes REST API on port 8095 for internal queries

### 4. Streaming & Access Control

```
Client Request → nginx :80
                   │
                   ├─ auth_request → stream-auth :8091
                   │   (validates HMAC-SHA256 token via query param or cookie)
                   │
                   ├─ /api/* → go2rtc :1984 (video streaming)
                   ├─ /backchannel → backchannel :8080 (client → speaker audio)
                   ├─ /detections → person-count-ws :8090 (live detection feed)
                   └─ /detection/* → saved detection images
```

Token format: Base64url-encoded JSON with payload (`camera_id`, `time_exp`) and HMAC-SHA256 signature. iOS HLS uses cookie fallback for segment requests.

### 5. Backend Sync (sync-config.py)

Runs every 5 minutes via crontab. Synchronizes:

| Data | Storage |
|------|---------|
| Camera settings, AI rules, detection zones | SQLite (`/data/mini-pc/db/logic_service.db`) |
| Face embeddings | SQLite (paginated sync with `updated_at` tracking) |
| SQS credentials | `/opt/logic_service/.env` |
| Pipeline type | `/opt/ai_core/.env` |
| Cloudflare tunnel token | Cloudflared service config |

Automatically restarts affected services when configuration changes are detected.

### 6. Networking

The system supports multiple network interfaces with automatic failover:

| Mode | Priority |
|------|----------|
| `auto` (default) | LAN > WiFi > 4G |
| `lan` | LAN > 4G > WiFi |
| `wifi` | WiFi > 4G > LAN |
| `4g` | 4G > LAN > WiFi |

The network watchdog monitors connectivity by pinging a configurable host (default: 8.8.8.8) every 30 seconds. After 3 consecutive failures, it triggers failover to the next available interface.

### 7. OTA Updates

Remote software updates are triggered from the backend via Cloudflare tunnel:

1. Backend sends `POST /update` with target version and update mode
2. Device responds `200 accepted` immediately
3. Background process: `git fetch` → `git checkout <tag>` → redeploy services
4. Device ACKs result back to backend (success/failed with version info)
5. If no ACK within 1 hour, backend marks update as failed

### 8. BLE OOBE (Out-of-Box Experience)

BLE GATT server for initial device provisioning from mobile app:

- WiFi network configuration via NetworkManager
- Network mode selection
- PIN-protected pairing (PIN from SQLite, fallback: `123456`)
- Auto-shutdown after 10 minutes of inactivity

## Services

| Service | Port | Description |
|---------|------|-------------|
| camera-stream | - | CSI camera → MPEG-TS stream + AI shared memory |
| go2rtc | 1984 | Multi-protocol streaming server (MSE, WebRTC, RTMP) |
| ai-core | - | AI detection pipeline (YOLO + InsightFace, CUDA) |
| logic-service | 8095 | ZMQ event subscriber + SQS sender + FastAPI |
| oobe-setup | - | BLE GATT server for WiFi/network provisioning |
| backchannel | 8080 | Browser audio → device speaker (WebSocket) |
| person-count-ws | 8090 | AI detection → WebSocket broadcast |
| stream-auth | 8091 | HMAC token validation for nginx |
| device-update-server | 8092 | OTA update endpoint |
| nginx | 80 | Reverse proxy + auth routing |
| sim7600-4g | - | 4G modem initialization |
| network-watchdog | - | Connectivity monitor + failover |
| audio-autostart | - | PulseAudio + echo cancel setup |
| cloudflared | - | Cloudflare tunnel to backend |
| cleanup-detections | - | Scheduled cleanup of old detection images/logs |

### Service Dependencies

```
camera-stream
  ├─► go2rtc (After, Wants)
  ├─► ExecStartPre: setup-audio-autostart.sh
  └─► start-stream.py

ai-core (After: camera-stream)
  ├─► Reads BGR frames from /dev/shm
  └─► ZMQ PUB tcp://127.0.0.1:5555

logic-service (After: network.target)
  ├─► ZMQ SUB from ai-core :5555
  └─► Sends events to AWS SQS

device-update-server
  └─► stream-auth (After, Wants)

sim7600-4g ──► network-watchdog (Wants, parallel start)
```

## Data Flow

```
CSI Camera
    │
    ▼
start-stream.py (GStreamer)
    │
    ├──── H.264 + AAC (MPEG-TS) ──────► go2rtc ──────► Browser/App
    │                                                    (MSE/WebRTC)
    └──── BGR frames (5fps) ──────► /dev/shm
                                       │
                                       ▼
                                   ai-core (CUDA)
                                       │
                                       ├── YOLO detection
                                       ├── InsightFace recognition
                                       └── ZMQ PUB :5555
                                              │
                                              ▼
                                       logic-service
                                              │
                                              ├── Apply rules (time, zone, member)
                                              ├── Save detection images
                                              └── Send to AWS SQS
                                                      │
                                                      ▼
                                                  Backend API
```

## File System Layout

```
/etc/device/
├── device.env           # DEVICE_ID, BACKEND_URL, SECRET_KEY
├── network.conf         # NETWORK_MODE, APN, PING_HOST, CHECK_INTERVAL
├── repo-path            # Path to git repo (for OTA)
├── config.json          # Last synced backend config
└── config.prev.json     # Previous config (for diff)

/opt/
├── stream/              # Camera pipeline
├── ai_core/             # AI detection pipeline
├── logic_service/       # Logic service
├── oobe-setup/          # BLE OOBE
├── backchannel/         # Audio backchannel
├── person_count_ws/     # ZMQ → WebSocket bridge
├── stream_auth/         # Token validator
├── device_update/       # OTA update server
├── device/              # sync-config.py, device-update.py
├── 4g/                  # Network scripts
└── audio/               # Audio setup scripts

/data/mini-pc/
├── db/                  # SQLite database
├── media/               # Detection images
├── faces/               # Face crops
├── logs/                # Application logs
└── models/              # ML models
```

## Authentication

All nginx-proxied routes require token-based authentication:

- **Token**: Base64url-encoded JSON containing `camera_id`, `time_exp`, and HMAC-SHA256 signature
- **Delivery**: Query parameter (`?token=<base64url>`) or `stream_token` cookie
- **Validation**: stream-auth service on port 8091 (nginx `auth_request`)
- **iOS Support**: Cookie fallback for HLS segment requests that drop query parameters

## Diagnostics (check-status.sh)

A single command to check the health of all services and hardware:

```bash
sudo ./setup-firstboot/scripts/check-status.sh
```

Reports:

| Section | What it checks |
|---------|---------------|
| Services | All 13 systemd services + audio-autostart (user), with active/enabled state |
| Cron Jobs | sync-config.py, device-update.py presence in crontab |
| CSI Camera | nvargus-daemon status, /dev/video* devices, AI shared memory file |
| USB Audio | PulseAudio status, USB speaker (sink), USB microphone (source), echo cancel module (loaded, wired to correct device) |
| LTE Module | /dev/ttyUSB* ports, lsusb detection, ModemManager modem state + signal, 4G interface + IP |
| Network | Network mode, active interfaces with IPs, default route |
| Device Info | Device ID, backend URL, software version (git tag) |

