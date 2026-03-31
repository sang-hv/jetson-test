# Jetson Nano Setup - Production Package

Auto-provisioning toolkit for Jetson Nano edge AI devices.
Includes: CSI camera streaming, two-way audio, AI detection bridge, 4G failover, token-based auth, and remote config sync.

## Hardware Requirements

| Component | Description |
|-----------|-------------|
| Jetson Nano | Main compute board |
| CSI Camera | IMX219 (default), connected via CSI ribbon |
| USB Speaker/Mic | Jabra or generic USB audio (echo cancel supported) |
| SIM7600 LTE Module | USB 4G modem for cellular fallback |
| SSD (optional) | Mounted at `/data` for storage |

## Directory Structure

```
setup-firstboot/
├── master-setup.sh                  # Entry point - runs install + services
├── install-software.sh              # Phase 1: OS packages, swap, go2rtc, cloudflared
├── setup-services.sh                # Phase 2: Deploy files, enable services, cronjobs
├── config/
│   ├── go2rtc.yaml                  # go2rtc streaming config
│   ├── nginx.conf                   # Reverse proxy + auth_request
│   └── network.conf                 # Network mode/APN/watchdog settings
├── scripts/
│   ├── start-stream.py              # GStreamer pipeline (CSI → H.264 + AI SHM)
│   ├── setup-audio-autostart.sh     # PulseAudio + echo cancel on boot
│   ├── sync-config.py               # Cronjob: sync config from backend API
│   ├── device-update.py             # Cronjob: heartbeat (last_seen)
│   ├── setup-4g.sh                  # SIM7600 modem init via ModemManager
│   ├── network-watchdog.sh          # Connectivity monitor + failover routing
│   └── switch-network.sh            # CLI: switch network mode (auto/4g/lan/wifi)
├── services/
│   ├── camera-stream.service        # GStreamer pipeline (WatchdogSec=60)
│   ├── go2rtc.service               # Streaming server
│   ├── backchannel.service          # Audio backchannel WebSocket
│   ├── person-count-ws.service      # ZMQ → WebSocket bridge
│   ├── stream-auth.service          # Nginx token validator
│   ├── audio-autostart.service      # PulseAudio user service
│   ├── sim7600-4g.service           # 4G modem init
│   ├── network-watchdog.service     # Network failover daemon
│   └── cloudflared.service          # Cloudflare tunnel
├── backchannel/
│   ├── server.py                    # WebSocket audio server (WebM/Opus + PCMU/G.711)
│   └── start.sh                     # PulseAudio env wrapper
├── person_count_ws/
│   ├── server.py                    # ZMQ SUB → WebSocket broadcast
│   └── start.sh                     # Exec wrapper
└── stream_auth/
    ├── server.py                    # HMAC-SHA256 token validation (Nginx auth_request)
    └── check_token.py               # CLI token checker
```

## Installation

```bash
# 1. Copy to Jetson
scp -r setup-firstboot/ user@jetson-ip:/home/user/setup/

# 2. Run master setup (installs everything + enables services)
ssh user@jetson-ip
cd /home/user/setup
sudo ./master-setup.sh

# 3. Reboot
sudo reboot
```

`master-setup.sh` runs two phases:
1. **install-software.sh** - apt packages, swap (8GB), go2rtc, cloudflared, GStreamer plugins, Python venv
2. **setup-services.sh** - deploys configs/scripts to system paths, creates `/etc/device/device.env`, enables all services, installs cronjobs

## Service Management

Both `master-setup.sh` and `setup-services.sh` support restart flags. All commands **always deploy + enable first**, then restart based on arguments.

### setup-services.sh

```bash
# Deploy + enable only (no restart)
sudo ./setup-services.sh

# Deploy + enable + restart ALL services
sudo ./setup-services.sh --restart-all

# Deploy + enable + restart specific services
sudo ./setup-services.sh network-watchdog go2rtc

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
                        ┌─────────────────────────────────────────────┐
                        │              Jetson Nano                    │
                        │                                             │
  CSI Camera ──────────►│ start-stream.py (GStreamer)                 │
  (IMX219)              │   ├─ Video: nvarguscamerasrc → H.264 → ─┐   │
                        │   ├─ Audio: echocancel_source → AAC →   ├──►│ go2rtc :1984
                        │   │                              mpegtsmux  │   ├─ MSE
                        │   └─ AI: 5fps BGR → /dev/shm/ ────────────► │   ├─ WebRTC
                        │         (shared memory for ai_core)     │   │   └─ RTMP
                        │                                         │   │
  USB Mic/Speaker ─────►│ PulseAudio + echo cancel                │   │
  (Jabra)               │   ├─ echocancel_source (mic in)         │   │
                        │   └─ echocancel_sink (speaker out) ◄────┤   │
                        │                                         │   │
  Browser/App ─────────►│ nginx :80 (reverse proxy)               │   │
                        │   ├─ /api/*       → go2rtc :1984    [auth]  │
                        │   ├─ /backchannel → backchannel :8080[auth] │
                        │   ├─ /detections  → person-count :8090[auth]│
                        │   ├─ /detection/* → saved images     [auth] │
                        │   └─ auth_request → stream-auth :8091       │
                        │                                             │
   ai_core (ZMQ) ──────►│ person-count-ws :8090                       │
  (tcp://127.0.0.1:5555)│   └─ ZMQ SUB → WebSocket broadcast          │
                        │                                             │
  Backend API ◄────────►│ sync-config.py (cron 5min)                  │
                        │   ├─ camera_settings, ai_rules              │
                        │   ├─ detection_zones, face_embeddings       │
                        │   └─ cloudflare tunnel token                │
                        │                                             │
  SIM7600 4G ──────────►│ network-watchdog.sh                         │
  LAN / WiFi            │   └─ auto failover: LAN > WiFi > 4G         │
                        └─────────────────────────────────────────────┘
```

## Services Reference

| Service | Port | Type | Purpose |
|---------|------|------|---------|
| camera-stream | - | notify | CSI camera → MPEG-TS stream + AI shared memory |
| go2rtc | 1984 | simple | Video/audio streaming (MSE, WebRTC, RTMP) |
| backchannel | 8080 | simple | Client audio → speaker (WebSocket) |
| person-count-ws | 8090 | simple | AI person count → WebSocket broadcast |
| stream-auth | 8091 | simple | Token validation for nginx auth_request |
| nginx | 80 | - | Reverse proxy + auth routing |
| sim7600-4g | - | oneshot | 4G modem initialization |
| network-watchdog | - | simple | Connectivity monitor + failover |
| audio-autostart | - | oneshot (user) | PulseAudio + echo cancel setup |
| cloudflared | - | simple | Cloudflare tunnel (token from backend) |

### Service Dependencies

```
camera-stream
  ├─► go2rtc (After, Wants)
  ├─► ExecStartPre: setup-audio-autostart.sh (creates fresh echo cancel)
  └─► start-stream.py connects to echocancel_source

backchannel (independent, Restart=always)
  ├─► ExecStartPre: check echocancel_sink exists, create only if missing
  └─► pacat --device echocancel_sink

sim7600-4g ──► network-watchdog (Wants, parallel start)
```

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

## Authentication

All nginx-proxied routes (except static pages) require a token via `?token=<base64url>`.

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

## Backend Sync (Cronjobs)

Two scripts run every 5 minutes via crontab:

| Script | API | Purpose |
|--------|-----|---------|
| `sync-config.py` | `GET /api/v1/cameras/{id}/config` | Sync settings, rules, zones, face embeddings |
| `device-update.py` | `PATCH /api/v1/cameras/{id}/device-update` | Send heartbeat (last_seen timestamp) |

Auth headers: `X-Device-ID`, `X-Timestamp`, `X-Signature` (HMAC-SHA256).

Device identity: `/etc/device/device.env` (DEVICE_ID, BACKEND_URL, SECRET_KEY).

### Synced Data (SQLite)

| Table | Content |
|-------|---------|
| `camera_settings` | stream_secret_key, stream_view_duration, bluetooth_password, facility |
| `ai_rules` | Detection rules: name, code, member_ids, time/weekday constraints |
| `detection_zones` | Polygons with coordinates (JSON), direction points |
| `face_embeddings` | User face vectors (paginated sync) |

## File System Layout (After Install)

```
/etc/device/
├── device.env           # DEVICE_ID, BACKEND_URL, SECRET_KEY
├── network.conf         # NETWORK_MODE, APN, PING_HOST, CHECK_INTERVAL
├── config.json          # Last synced backend config
└── config.prev.json     # Previous config (for diff)

/opt/
├── stream/              # Camera pipeline
├── backchannel/         # Audio backchannel
├── person_count_ws/     # ZMQ→WS bridge
├── stream_auth/         # Token validator
├── device/              # sync-config.py, device-update.py
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

## Diagnostics

```bash
# All services status
sudo systemctl status camera-stream go2rtc backchannel person-count-ws stream-auth nginx sim7600-4g network-watchdog cloudflared
systemctl --user status audio-autostart

# Logs (follow)
sudo journalctl -u camera-stream -f
sudo journalctl -u go2rtc -f
sudo journalctl -u backchannel -f
sudo journalctl -u network-watchdog -f

# Audio check
pactl list short sinks | grep -i "jabra\|echocancel"
pactl list short sources | grep -i "jabra\|echocancel"

# Network check
ip route show                          # Current routing table
cat /etc/device/network.conf           # Network mode
cat /run/4g-interface                  # Active 4G interface
mmcli -L                               # Modem status

# AI shared memory
ls -la /dev/shm/mini_pc_ai_frames.bin

# Backend sync
cat /etc/device/device.env             # Device identity
sudo journalctl -u cron --grep="sync-config\|device-update" --since="1 hour ago"

# Token validation
python3 /opt/stream_auth/check_token.py <token>
```

## Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| No video stream | `journalctl -u camera-stream` | Check CSI ribbon, restart `nvargus-daemon` |
| No audio | `pactl list short sinks` | Reconnect USB audio, restart `audio-autostart` |
| 4G not connecting | `mmcli -L`, `journalctl -u sim7600-4g` | Check SIM, verify APN in `network.conf` |
| Auth 401 on stream | `check_token.py <token>` | Verify token expiry, check `stream_secret_key` in DB |
| No person count | `journalctl -u person-count-ws` | Verify ai_core is publishing on ZMQ :5555 |
| Backchannel no sound | `journalctl -u backchannel` | Check `echocancel_sink` exists, restart camera-stream |
| Config not syncing | `cat /etc/device/device.env` | Verify BACKEND_URL reachable, check SECRET_KEY |
| Network keeps switching | `journalctl -u network-watchdog` | Adjust `CHECK_INTERVAL` / `MAX_RETRIES` in network.conf |
