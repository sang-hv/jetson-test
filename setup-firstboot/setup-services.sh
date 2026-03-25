#!/bin/bash
###############################################################################
# setup-services.sh
# Phase 2: Deploy files, configure services, and install cron jobs.
###############################################################################

set -euo pipefail

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
LOG_FILE="${LOG_FILE:-/tmp/jetson-setup-$(date +%Y%m%d_%H%M%S).log}"
ACTUAL_USER="${ACTUAL_USER:-${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}}"
ACTUAL_HOME="${ACTUAL_HOME:-$(eval echo "~$ACTUAL_USER")}"
ACTUAL_UID="${ACTUAL_UID:-$(id -u "$ACTUAL_USER")}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[✗]${NC} $*" | tee -a "$LOG_FILE"; }
step() { echo -e "\n${BLUE}━━━ $* ━━━${NC}" | tee -a "$LOG_FILE"; }

if [ "$EUID" -ne 0 ]; then
    err "Cần chạy với sudo/root"
    exit 1
fi

# Recompute data paths for deployment summary
if [ -d /data ]; then
    DATA_DIR="/data/mini-pc"
    VENV_DIR="/data/venv/mini-pc"
else
    DATA_DIR="$ACTUAL_HOME/data"
    VENV_DIR="$ACTUAL_HOME/.venv"
fi

step "Phase 1/7: go2rtc stream services"
mkdir -p /etc/go2rtc /opt/stream
cp "$SCRIPT_DIR/config/go2rtc.yaml" /etc/go2rtc/go2rtc.yaml
cp "$SCRIPT_DIR/scripts/start-stream.py" /opt/stream/start-stream.py
chmod +x /opt/stream/start-stream.py

sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/camera-stream.service" \
    > /etc/systemd/system/camera-stream.service
sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/go2rtc.service" \
    > /etc/systemd/system/go2rtc.service
systemctl daemon-reload
systemctl enable camera-stream.service go2rtc.service
log "camera-stream + go2rtc configured"

step "Phase 2/7: device identity and sync scripts"
DEVICE_ID="${DEVICE_ID:-}"
BACKEND_URL="${BACKEND_URL:-}"
SECRET_KEY="${SECRET_KEY:-}"

mkdir -p /etc/device /opt/device

if [ -f /etc/device/device.env ]; then
    log "Device identity already configured"
    source /etc/device/device.env
else
    if [ -z "$DEVICE_ID" ] || [ -z "$BACKEND_URL" ] || [ -z "$SECRET_KEY" ]; then
        warn "Device identity not provided via env vars"
        cat > /etc/device/device.env << 'ENVEOF'
# Device Identity — edit these values
DEVICE_ID=
BACKEND_URL=
SECRET_KEY=
ENVEOF
    else
        cat > /etc/device/device.env << ENVEOF
DEVICE_ID=$DEVICE_ID
BACKEND_URL=$BACKEND_URL
SECRET_KEY=$SECRET_KEY
ENVEOF
        log "Device identity saved → /etc/device/device.env"
    fi
fi
chmod 600 /etc/device/device.env
chmod a+r /etc/device/device.env

cp "$SCRIPT_DIR/scripts/sync-config.py" /opt/device/sync-config.py
cp "$SCRIPT_DIR/scripts/device-update.py" /opt/device/device-update.py
chmod +x /opt/device/sync-config.py /opt/device/device-update.py
log "sync-config + device-update deployed"

SYNC_CRON_LINE="*/5 * * * * /usr/bin/python3 /opt/device/sync-config.py"
if crontab -l 2>/dev/null | grep -q "sync-config.py"; then
    log "sync-config cronjob already exists"
else
    (crontab -l 2>/dev/null; echo "$SYNC_CRON_LINE") | crontab -
    log "sync-config cronjob installed"
fi

DEVICE_UPDATE_CRON_LINE="*/5 * * * * /usr/bin/python3 /opt/device/device-update.py"
if crontab -l 2>/dev/null | grep -q "device-update.py"; then
    log "device-update cronjob already exists"
else
    (crontab -l 2>/dev/null; echo "$DEVICE_UPDATE_CRON_LINE") | crontab -
    log "device-update cronjob installed"
fi

if [ -n "$DEVICE_ID" ] && [ -n "$BACKEND_URL" ] && [ -n "$SECRET_KEY" ]; then
    log "Running first config sync..."
    python3 /opt/device/sync-config.py 2>&1 | tee -a "$LOG_FILE" || warn "First sync failed (cron will retry)"

    log "Running first update device info..."
    python3 /opt/device/device-update.py 2>&1 | tee -a "$LOG_FILE" || warn "First update device failed (cron will retry)"
fi

step "Phase 3/7: cloudflared service check"
if systemctl is-active cloudflared >/dev/null 2>&1; then
    log "Cloudflared tunnel already running"
else
    warn "Cloudflared installed — tunnel will be configured after sync-config runs"
fi

step "Phase 4/7: backchannel and person-count ws"
mkdir -p /opt/backchannel /opt/person_count_ws
cp "$SCRIPT_DIR/backchannel/server.py" /opt/backchannel/server.py
cp "$SCRIPT_DIR/backchannel/start.sh" /opt/backchannel/start.sh
chmod +x /opt/backchannel/start.sh

cp "$SCRIPT_DIR/person_count_ws/server.py" /opt/person_count_ws/server.py
cp "$SCRIPT_DIR/person_count_ws/start.sh" /opt/person_count_ws/start.sh
chmod +x /opt/person_count_ws/start.sh

pip3 install websockets pyzmq 2>&1 | tail -3 | tee -a "$LOG_FILE"

sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/backchannel.service" \
    > /etc/systemd/system/backchannel.service
sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/person-count-ws.service" \
    > /etc/systemd/system/person-count-ws.service
systemctl daemon-reload
systemctl enable backchannel.service person-count-ws.service
log "Backchannel + person-count ws configured"

step "Phase 5/7: nginx reverse proxy"
cp "$SCRIPT_DIR/config/nginx.conf" /etc/nginx/sites-available/go2rtc
ln -sf /etc/nginx/sites-available/go2rtc /etc/nginx/sites-enabled/go2rtc
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tee -a "$LOG_FILE"
systemctl enable nginx
log "Nginx configured"

step "Phase 6/7: audio autostart"
mkdir -p /opt/audio
cp "$SCRIPT_DIR/scripts/setup-audio-autostart.sh" /opt/audio/setup-audio-autostart.sh
chmod +x /opt/audio/setup-audio-autostart.sh

SYSTEMD_USER_DIR="$ACTUAL_HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
cp "$SCRIPT_DIR/services/audio-autostart.service" "$SYSTEMD_USER_DIR/audio-autostart.service"
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$ACTUAL_HOME/.config/systemd"

su - "$ACTUAL_USER" -c "export XDG_RUNTIME_DIR=/run/user/$ACTUAL_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$ACTUAL_UID/bus && systemctl --user daemon-reload && systemctl --user enable audio-autostart.service" 2>&1 | tee -a "$LOG_FILE"
loginctl enable-linger "$ACTUAL_USER"
log "Audio autostart service enabled"

step "Phase 7/7: SIM7600 scripts/services"
mkdir -p /opt/4g
cp "$SCRIPT_DIR/scripts/setup-4g.sh" /opt/4g/setup-4g.sh
cp "$SCRIPT_DIR/scripts/network-watchdog.sh" /opt/4g/network-watchdog.sh
cp "$SCRIPT_DIR/scripts/switch-network.sh" /opt/4g/switch-network.sh
chmod +x /opt/4g/setup-4g.sh /opt/4g/network-watchdog.sh /opt/4g/switch-network.sh

if [ ! -f /etc/device/network.conf ]; then
    cp "$SCRIPT_DIR/config/network.conf" /etc/device/network.conf
    log "Network config → /etc/device/network.conf"
else
    log "Network config already exists — skipping"
fi

cp "$SCRIPT_DIR/services/sim7600-4g.service" /etc/systemd/system/sim7600-4g.service
cp "$SCRIPT_DIR/services/network-watchdog.service" /etc/systemd/system/network-watchdog.service
systemctl daemon-reload
systemctl enable sim7600-4g.service network-watchdog.service
log "sim7600-4g + network-watchdog services enabled"

if ls /dev/ttyUSB* &>/dev/null; then
    log "SIM7600 USB device detected: $(ls /dev/ttyUSB* | tr '\n' ' ')"
elif lsusb | grep -iq "simcom\|sim7600\|qualcomm"; then
    log "SIM7600 visible in lsusb"
else
    warn "SIM7600 not detected yet — check cable and jumper"
fi

echo ""
echo "  Storage:"
echo "    Data dir:    $DATA_DIR"
echo "    Python venv: $VENV_DIR"
echo "    Device env:  /etc/device/device.env"
echo "    Config:      /etc/device/config.json"
echo ""
echo "  Services:"
echo "    go2rtc          → sudo systemctl status go2rtc"
echo "    backchannel     → sudo systemctl status backchannel"
echo "    person-count-ws → sudo systemctl status person-count-ws"
echo "    cloudflared     → sudo systemctl status cloudflared"
echo "    nginx           → sudo systemctl status nginx"
echo "    audio-auto      → systemctl --user status audio-autostart"
echo "    sync-config     → crontab -l (every 5 min)"
echo "    device-update   → crontab -l (every 5 min)"
echo "    sim7600-4g      → sudo systemctl status sim7600-4g"
echo "    net-watchdog    → sudo systemctl status network-watchdog"
echo ""
log "Phase 2 complete: files and services configured"
