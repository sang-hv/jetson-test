# Setup

## Prerequisites

- NVIDIA Jetson Orin Nano with JetPack OS flashed
- CSI camera (IMX219) connected via ribbon cable
- Network access (LAN, WiFi, or SIM card for 4G)
- NVMe SSD (recommended) or SD card for `/data` storage

## Quick Start

```bash
# 1. Copy repo to Jetson
scp -r . user@jetson-ip:/home/user/mini-pc/

# 2. SSH into device
ssh user@jetson-ip

# 3. Run master setup
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh

# 4. Reboot
sudo reboot
```

For fresh or cloned devices, use `--prompt-device-env` to set device identity:

```bash
sudo ./master-setup.sh --prompt-device-env
```

## Installation Phases

`master-setup.sh` runs two phases sequentially:

### Phase 1: install-software.sh

Installs system-level dependencies:

- APT packages (GStreamer plugins, Nginx, Python, etc.)
- Swap configuration (8GB)
- go2rtc streaming server
- Cloudflared tunnel client

### Phase 2: setup-services.sh

Deploys application files and enables all services:

| Step | Description |
|------|-------------|
| 1/11 | go2rtc stream services |
| 2/11 | Device identity and sync scripts |
| 3/11 | Cloudflared service check |
| 4/11 | Backchannel, person-count WebSocket, and stream-auth |
| 5/11 | Device OTA update server |
| 6/11 | Nginx reverse proxy |
| 7/11 | Audio autostart |
| 8/11 | SIM7600 4G scripts/services |
| 9/11 | OOBE BLE setup |
| 10/11 | Logic Service (ZMQ + FastAPI) |
| 11/11 | AI Core detection pipeline |

## Device Identity

Device identity is stored in `/etc/device/device.env` and used by `sync-config.py` and `device-update.py` to communicate with the backend.

| Variable | Description |
|----------|-------------|
| `DEVICE_ID` | Unique camera UUID from backend |
| `BACKEND_URL` | API base URL |
| `SECRET_KEY` | HMAC signing key for API authentication |

Set interactively with `--prompt-device-env`, or edit `/etc/device/device.env` directly.

## Service Management

Both `master-setup.sh` and `setup-services.sh` support restart flags. All commands always deploy and enable first, then restart based on arguments.

### Deploy only (no restart)

```bash
sudo ./setup-services.sh
```

### Deploy + restart all services

```bash
sudo ./setup-services.sh --restart-all
```

### Deploy + restart specific services

```bash
sudo ./setup-services.sh network-watchdog go2rtc nginx
```

### Full setup (install + deploy) with restart

```bash
sudo ./master-setup.sh --restart-all
sudo ./master-setup.sh network-watchdog go2rtc
```

Invalid service names produce an error with a list of valid options.

## Network Configuration

### Network Modes

Configuration file: `/etc/device/network.conf`

| Mode | Priority | Description |
|------|----------|-------------|
| `auto` | LAN > WiFi > 4G | Default, uses best available |
| `lan` | LAN > 4G > WiFi | Prefer wired connection |
| `wifi` | WiFi > 4G > LAN | Prefer wireless |
| `4g` | 4G > LAN > WiFi | Force cellular |

### Switch Network Mode

```bash
sudo /opt/4g/switch-network.sh auto   # or: 4g, lan, wifi
```

### Watchdog Settings

Configured in `/etc/device/network.conf`:

| Setting | Default | Description |
|---------|---------|-------------|
| `PING_HOST` | 8.8.8.8 | Host to ping for connectivity check |
| `CHECK_INTERVAL` | 30 | Seconds between checks |
| `MAX_RETRIES` | 3 | Failures before failover |
| `APN` | (carrier) | APN for SIM7600 LTE module |

## OTA Software Update

Devices can be updated remotely via backend API.

### Update Flow

```
Mobile App                    Backend API                      Jetson
    │                              │                              │
    ├─ POST /cameras/{id}/update ─►│                              │
    │   { version, run_install }   │                              │
    │                              ├─ POST /update (tunnel) ─────►│
    │                              │   (HMAC auth)                ├─ 200 accepted
    │                              │                              ├─ git fetch + checkout
    │                              │                              ├─ Redeploy services
    │                              │◄─ PATCH /update-logs/ack ───┤
    │◄── query update_logs ────────┤                              │
```

### Update Parameters

| Parameter | Description |
|-----------|-------------|
| `version` | Git tag or branch to checkout |
| `run_install` | `true` = full install (`master-setup.sh`), `false` = deploy only (`setup-services.sh`) |
| `update_log_id` | Backend UUID for ACK tracking |

### Update Process (on device)

1. Acquire lock (`/tmp/device-update.lock`)
2. `git fetch origin`
3. `git checkout <version>`
4. Run `setup-services.sh --restart-all` (or `master-setup.sh --restart-all` if `run_install=true`)
5. Health check: verify core services are running
6. ACK backend with result (success/failed)
7. Release lock

### Version Tracking

Device reports `software_version` (from `git describe --tags`) in every heartbeat (`device-update.py`, every 5 minutes). Backend stores this in `cameras.software_version`.

## System Cloning

### 1. Create Disk Image (on running device)

```bash
# Full NVMe clone to USB drive
sudo dd if=/dev/nvme0n1 of=/media/usb/jetson-image.img bs=4M status=progress

# Compressed (saves ~60-70% space)
sudo dd if=/dev/nvme0n1 bs=4M status=progress | gzip > /media/usb/jetson-image.img.gz
```

### 2. Restore to New Device

```bash
# From raw image
sudo dd if=jetson-image.img of=/dev/nvme0n1 bs=4M status=progress

# From compressed image
gunzip -c jetson-image.img.gz | sudo dd of=/dev/nvme0n1 bs=4M status=progress
```

### 3. Re-provision Device Identity

```bash
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh --prompt-device-env
# Enter new DEVICE_ID, BACKEND_URL, SECRET_KEY
sudo reboot
```

After reboot, `sync-config.py` automatically pulls the new device's configuration from the backend.

## Backend Sync

`sync-config.py` runs every 5 minutes via crontab.

### API Calls

| API | Purpose |
|-----|---------|
| `GET /api/v1/cameras/{id}/config` | Sync settings, rules, zones, cloudflare token, SQS config |
| `GET /api/v1/cameras/{id}/face-embeddings` | Paginated face embedding sync |

Authentication headers: `X-Device-ID`, `X-Timestamp`, `X-Signature` (HMAC-SHA256).

### Synced Data (SQLite)

Database: `/data/mini-pc/db/logic_service.db`

| Table | Content |
|-------|---------|
| `camera_settings` | stream_secret_key, stream_view_duration, bluetooth_password, facility, ai_threshold, image_retention_days |
| `ai_rules` | Detection rules: name, code, member_ids, time/weekday constraints |
| `detection_zones` | Polygons with coordinates (JSON), direction points |
| `face_embeddings` | User face vectors (paginated sync with `updated_at` tracking) |

### Environment File Sync

| Target | Keys | Source |
|--------|------|--------|
| `/opt/logic_service/.env` | AWS_SQS_REGION, AWS_SQS_QUEUE_URL, AWS_SQS_ACCESS_KEY_ID, AWS_SQS_SECRET_ACCESS_KEY | API response |
| `/opt/ai_core/.env` | PIPELINE_TYPE | Mapped from facility: Family→home, Store→shop, Enterprise→enterprise |

### Auto-restart Triggers

| Service | Restart When |
|---------|-------------|
| `logic-service` | SQS credentials in `.env` changed |
| `ai-core` | `PIPELINE_TYPE` changed OR `face_embeddings.updated_at` changed |
| `cloudflared` | Tunnel token changed |

## Diagnostics

### Check All Services

```bash
sudo systemctl status camera-stream go2rtc ai-core logic-service oobe-setup \
  backchannel person-count-ws stream-auth device-update-server nginx \
  sim7600-4g network-watchdog cloudflared
systemctl --user status audio-autostart
```

### View Logs

```bash
sudo journalctl -u camera-stream -f
sudo journalctl -u ai-core -f
sudo journalctl -u logic-service -f
sudo journalctl -u network-watchdog -f
sudo journalctl -u device-update-server -f
```

### Audio Check

```bash
pactl list short sinks | grep -i "jabra\|echocancel"
pactl list short sources | grep -i "jabra\|echocancel"
# If USB audio is reconnected:
systemctl --user restart audio-autostart
```

### Network Check

```bash
ip route show
cat /etc/device/network.conf
cat /run/4g-interface
mmcli -L
```

### OTA Check

```bash
curl http://127.0.0.1:8092/health
cd $(cat /etc/device/repo-path) && git describe --tags --always
```

### Token Validation

```bash
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
| 4G not connecting | `mmcli -L`, `journalctl -u sim7600-4g` | Check SIM, verify APN in `network.conf` |
| Auth 401 on stream | `check_token.py <token>` | Verify token expiry, check `stream_secret_key` in DB |
| iOS stream fails | Check `stream_token` cookie | Ensure nginx sets cookie, stream-auth reads it |
| Config not syncing | `cat /etc/device/device.env` | Verify BACKEND_URL reachable, check SECRET_KEY |
| OTA update failed | `journalctl -u device-update-server` | Check git access, disk space, service health |
| Network keeps switching | `journalctl -u network-watchdog` | Adjust `CHECK_INTERVAL` / `MAX_RETRIES` in network.conf |
