#!/bin/bash
###############################################################################
# setup-services.sh
# Phase 2: Deploy files, configure services, and install cron jobs.
#
# Usage:
#   sudo ./setup-services.sh                          # Deploy + enable all services
#   sudo ./setup-services.sh --restart-all             # Deploy + enable + restart ALL services
#   sudo ./setup-services.sh network-watchdog go2rtc   # Deploy + enable + restart specific services
###############################################################################

set -euo pipefail

SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# Optional file log (opt-in). Default: do not write logs to /tmp to avoid disk growth.
LOG_FILE="${LOG_FILE:-}"

# Resolve the "real" non-root user for user services (audio-autostart).
# - Normal manual runs: SUDO_USER is set → use it
# - OTA runs: executed as root via systemd with no SUDO_USER → use repo owner
if [ -n "${ACTUAL_USER:-}" ]; then
    _resolved_user="$ACTUAL_USER"
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    _resolved_user="$SUDO_USER"
elif [ -f /etc/device/repo-path ]; then
    _rp="$(cat /etc/device/repo-path 2>/dev/null || true)"
    _repo_root="$(cd "${_rp%/}/.." 2>/dev/null && pwd || true)"
    if [ -n "$_repo_root" ] && [ -d "$_repo_root" ]; then
        _resolved_user="$(stat -c '%U' "$_repo_root" 2>/dev/null || true)"
    fi
fi
ACTUAL_USER="${_resolved_user:-$(logname 2>/dev/null || echo $USER)}"
ACTUAL_HOME="${ACTUAL_HOME:-$(eval echo "~$ACTUAL_USER")}"
ACTUAL_UID="${ACTUAL_UID:-$(id -u "$ACTUAL_USER")}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

_emit() {
    if [ -n "${LOG_FILE:-}" ]; then
        tee -a "$LOG_FILE"
    else
        cat
    fi
}

log()  { echo -e "${GREEN}[✓]${NC} $*" | _emit; }
warn() { echo -e "${YELLOW}[!]${NC} $*" | _emit; }
err()  { echo -e "${RED}[✗]${NC} $*" | _emit; }
step() { echo -e "\n${BLUE}━━━ $* ━━━${NC}" | _emit; }

run_stream() {
    # Stream command output to terminal (and optional log file) in realtime.
    if command -v stdbuf >/dev/null 2>&1; then
        stdbuf -oL -eL "$@" 2>&1 | _emit
    else
        "$@" 2>&1 | _emit
    fi
}

if [ "$EUID" -ne 0 ]; then
    err "It needs to be run with sudo/root"
    exit 1
fi

# ---------------------------------------------------------------------------
# Restart mode: no args = restart all, <name> = restart one service
# ---------------------------------------------------------------------------
SERVICES_DIR="$SCRIPT_DIR/services"

# Build list of valid service names from the services/ directory + system aliases
get_valid_services() {
    local f name
    for f in "$SERVICES_DIR"/*.service; do
        [ -f "$f" ] || continue
        name=$(basename "$f" .service)
        echo "$name"
    done
    echo "nginx"
}

restart_service() {
    local name="$1"

    # Allow callers (OTA) to skip restarting specific services.
    # Example: SKIP_RESTART_SERVICES="device-update-server" to avoid self-termination.
    if [ -n "${SKIP_RESTART_SERVICES:-}" ]; then
        for _skip in $SKIP_RESTART_SERVICES; do
            if [ "$name" = "$_skip" ]; then
                log "Skipping $name (SKIP_RESTART_SERVICES)"
                return 0
            fi
        done
    fi

    # Skip oneshot services — they run on boot or via timers, not via restart
    case "$name" in
        sim7600-4g)
            log "Skipping $name (oneshot — runs on boot only)"
            return 0
            ;;
        cleanup-detections)
            log "Skipping $name (oneshot — managed by cleanup-detections.timer)"
            return 0
            ;;
    esac
    if [ "$name" = "nginx" ]; then
        log "Reloading nginx (test config first)..."
        if run_stream nginx -t; then
            run_stream systemctl reload nginx \
                && log "nginx reloaded" \
                || err "Failed to reload nginx"
        else
            err "nginx config test failed — skipping reload"
        fi
    elif [ "$name" = "audio-autostart" ]; then
        log "Restarting $name (user service for $ACTUAL_USER)..."
        su - "$ACTUAL_USER" -c \
            "export XDG_RUNTIME_DIR=/run/user/$ACTUAL_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$ACTUAL_UID/bus && systemctl --user restart $name.service" 2>&1 | _emit \
            && log "$name restarted" \
            || err "Failed to restart $name"
    else
        log "Restarting $name..."
        run_stream systemctl restart "$name.service" \
            && log "$name restarted" \
            || err "Failed to restart $name"
    fi
}

# ---------------------------------------------------------------------------
# Determine restart mode (validate args BEFORE deploy to fail early)
# ---------------------------------------------------------------------------
RESTART_MODE="none"  # none | all | specific
RESTART_TARGETS=()

if [ "${1:-}" = "--restart-all" ]; then
    RESTART_MODE="all"
elif [ $# -gt 0 ]; then
    RESTART_MODE="specific"
    VALID_LIST=$(get_valid_services)
    for TARGET in "$@"; do
        FOUND=0
        while IFS= read -r svc; do
            if [ "$svc" = "$TARGET" ]; then
                FOUND=1
                break
            fi
        done <<< "$VALID_LIST"

        if [ "$FOUND" -eq 0 ]; then
            err "Unknown service '$TARGET'. Valid services:"
            get_valid_services | sed 's/^/  - /'
            exit 1
        fi
    done
    RESTART_TARGETS=("$@")
fi

# Recompute data paths for deployment summary
if [ -d /data ]; then
    DATA_DIR="/data/mini-pc"
else
    DATA_DIR="$ACTUAL_HOME/data"
fi

step "Install Python dependencies (global)"
REQ_FILE="$SCRIPT_DIR/../src/requirements.lock.txt"
if [ -f "$REQ_FILE" ]; then
    run_stream pip install -r "$REQ_FILE"
    log "Python deps installed from $REQ_FILE (global)"
else
    warn "Missing $REQ_FILE — skipping pip install"
fi

step "Phase 1/11: go2rtc stream services"
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

step "Phase 2/11: device identity and sync scripts"
DEVICE_ID="${DEVICE_ID:-}"
BACKEND_URL="${BACKEND_URL:-}"
SECRET_KEY="${SECRET_KEY:-}"
FORCE_DEVICE_ENV="${FORCE_DEVICE_ENV:-0}"

mkdir -p /etc/device /opt/device
mkdir -p /data/mini-pc/db

# Detection results directory (served by nginx at /detection/)
# Use a shared-dir permission model so any service user can write images.
mkdir -p /detection
# 1777: world-writable with sticky bit (like /tmp) so services running as
# different users can create files while preventing cross-user deletion.
chmod 1777 -R /detection/

if [ "$FORCE_DEVICE_ENV" = "1" ]; then
    if [ -z "$DEVICE_ID" ] || [ -z "$BACKEND_URL" ] || [ -z "$SECRET_KEY" ]; then
        err "FORCE_DEVICE_ENV=1 but DEVICE_ID/BACKEND_URL/SECRET_KEY is empty"
        exit 1
    fi
    cat > /etc/device/device.env << ENVEOF
DEVICE_ID=$DEVICE_ID
BACKEND_URL=$BACKEND_URL
SECRET_KEY=$SECRET_KEY
ENVEOF
    log "Device identity written (forced) → /etc/device/device.env"
    source /etc/device/device.env
else
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
fi
chmod 600 /etc/device/device.env
chmod a+r /etc/device/device.env
chmod -R 777 /data/mini-pc/db

cp "$SCRIPT_DIR/scripts/sync-config.py" /opt/device/sync-config.py
cp "$SCRIPT_DIR/scripts/device-update.py" /opt/device/device-update.py
cp "$SCRIPT_DIR/scripts/cleanup-detections.sh" /opt/device/cleanup-detections.sh
chmod +x /opt/device/sync-config.py /opt/device/device-update.py /opt/device/cleanup-detections.sh

cp "$SCRIPT_DIR/services/cleanup-detections.service" /etc/systemd/system/cleanup-detections.service
cp "$SCRIPT_DIR/services/cleanup-detections.timer" /etc/systemd/system/cleanup-detections.timer
systemctl daemon-reload
systemctl enable cleanup-detections.timer
systemctl start cleanup-detections.timer
log "sync-config + device-update + cleanup-detections deployed"

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
    python3 /opt/device/sync-config.py 2>&1 | _emit || warn "First sync failed (cron will retry)"

    log "Running first update device info..."
    python3 /opt/device/device-update.py 2>&1 | _emit || warn "First update device failed (cron will retry)"
fi

step "Phase 3/11: cloudflared service check"
if systemctl is-active cloudflared >/dev/null 2>&1; then
    log "Cloudflared tunnel already running"
else
    warn "Cloudflared installed — tunnel will be configured after sync-config runs"
fi

step "Phase 4/11: backchannel, person-count ws, and stream-auth"
mkdir -p /opt/backchannel /opt/person_count_ws /opt/stream_auth
cp "$SCRIPT_DIR/backchannel/server.py" /opt/backchannel/server.py
cp "$SCRIPT_DIR/backchannel/start.sh" /opt/backchannel/start.sh
chmod +x /opt/backchannel/start.sh

cp "$SCRIPT_DIR/person_count_ws/server.py" /opt/person_count_ws/server.py
cp "$SCRIPT_DIR/person_count_ws/start.sh" /opt/person_count_ws/start.sh
chmod +x /opt/person_count_ws/start.sh

cp "$SCRIPT_DIR/stream_auth/server.py" /opt/stream_auth/server.py

sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/backchannel.service" \
    > /etc/systemd/system/backchannel.service
sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/person-count-ws.service" \
    > /etc/systemd/system/person-count-ws.service
cp "$SCRIPT_DIR/services/stream-auth.service" /etc/systemd/system/stream-auth.service
systemctl daemon-reload
systemctl enable backchannel.service person-count-ws.service stream-auth.service
log "Backchannel + person-count ws + stream-auth configured"

step "Phase 5/11: device OTA update server"
mkdir -p /opt/device_update
cp "$SCRIPT_DIR/device_update/server.py" /opt/device_update/server.py
cp "$SCRIPT_DIR/scripts/run-update.sh" /opt/device/run-update.sh
chmod +x /opt/device/run-update.sh

# Save repo path so run-update.sh and device-update.py can find the git repo
echo "$SCRIPT_DIR" > /etc/device/repo-path

cp "$SCRIPT_DIR/services/device-update-server.service" /etc/systemd/system/device-update-server.service
systemctl daemon-reload
systemctl enable device-update-server.service
log "Device OTA update server configured"

step "Phase 6/11: nginx reverse proxy"
cp "$SCRIPT_DIR/config/nginx.conf" /etc/nginx/sites-available/go2rtc
ln -sf /etc/nginx/sites-available/go2rtc /etc/nginx/sites-enabled/go2rtc
rm -f /etc/nginx/sites-enabled/default
run_stream nginx -t
systemctl enable nginx
log "Nginx configured"

step "Phase 7/11: audio autostart"
mkdir -p /opt/audio
cp "$SCRIPT_DIR/scripts/setup-audio-autostart.sh" /opt/audio/setup-audio-autostart.sh
chmod +x /opt/audio/setup-audio-autostart.sh

SYSTEMD_USER_DIR="$ACTUAL_HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
cp "$SCRIPT_DIR/services/audio-autostart.service" "$SYSTEMD_USER_DIR/audio-autostart.service"
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$ACTUAL_HOME/.config/systemd"

su - "$ACTUAL_USER" -c "export XDG_RUNTIME_DIR=/run/user/$ACTUAL_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$ACTUAL_UID/bus && systemctl --user daemon-reload && systemctl --user enable audio-autostart.service" 2>&1 | _emit
loginctl enable-linger "$ACTUAL_USER"
log "Audio autostart service enabled"

step "Phase 8/11: SIM7600 scripts/services"
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

step "Phase 9/11: OOBE BLE setup"
OOBE_SRC="$SCRIPT_DIR/../src/oobe/jetson_backend"
OOBE_DST="/opt/oobe-setup"
mkdir -p "$OOBE_DST"
if [ -d "$OOBE_SRC" ]; then
    cp "$OOBE_SRC/ble_wifi_setup.py" "$OOBE_DST/"
    cp "$OOBE_SRC/config.py"         "$OOBE_DST/"
    cp "$OOBE_SRC/wifi_manager.py"   "$OOBE_DST/"
    cp "$OOBE_SRC/gpio_handler.py"   "$OOBE_DST/"
    cp "$OOBE_SRC/mode_selector.py"  "$OOBE_DST/"
    chmod +x "$OOBE_DST/ble_wifi_setup.py"
    cp "$SCRIPT_DIR/services/oobe-setup.service" /etc/systemd/system/oobe-setup.service
    systemctl daemon-reload
    systemctl enable oobe-setup.service
    log "OOBE BLE setup deployed → $OOBE_DST"
else
    warn "OOBE source not found at $OOBE_SRC — skipping"
fi

step "Phase 10/11: Logic Service (ZMQ + FastAPI)"
LOGIC_SRC="$(cd "$SCRIPT_DIR/../src/logic_service" 2>/dev/null && pwd)"
if [ -d "$LOGIC_SRC" ]; then
    # Ensure .env exists (sync-config.py will manage SQS values)
    if [ ! -f "$LOGIC_SRC/.env" ] && [ -f "$LOGIC_SRC/.env.example" ]; then
        cp "$LOGIC_SRC/.env.example" "$LOGIC_SRC/.env"
        warn "Logic Service .env copied from .env.example — sync-config will fill SQS values"
    fi

    sed -e "s|__LOGIC_DIR__|$LOGIC_SRC|" \
        "$SCRIPT_DIR/services/logic-service.service" > /etc/systemd/system/logic-service.service
    systemctl daemon-reload
    systemctl enable logic-service.service
    log "Logic Service runs in-place → $LOGIC_SRC"
else
    warn "Logic Service source not found at $LOGIC_SRC — skipping"
fi

step "Phase 11/11: AI Core detection pipeline"
AI_SRC="$(cd "$SCRIPT_DIR/../src/ai_core" 2>/dev/null && pwd)"
if [ -d "$AI_SRC" ]; then
    # Create .env from example if not present (sync-config.py will manage it)
    if [ ! -f "$AI_SRC/.env" ] && [ -f "$AI_SRC/.env.example" ]; then
        cp "$AI_SRC/.env.example" "$AI_SRC/.env"
        warn "AI Core .env copied from .env.example — sync-config will set PIPELINE_TYPE"
    fi
    # Generate service file with actual source path
    sed "s|__AI_CORE_DIR__|$AI_SRC|" "$SCRIPT_DIR/services/ai-core.service" \
        > /etc/systemd/system/ai-core.service
    systemctl daemon-reload
    systemctl enable ai-core.service
    log "AI Core runs in-place → $AI_SRC"
else
    warn "AI Core source not found — skipping"
fi

echo ""
echo "  Storage:"
echo "    Data dir:    $DATA_DIR"
echo "    Device env:  /etc/device/device.env"
echo "    Config:      /etc/device/config.json"
echo ""
echo "  Services:"
echo "    go2rtc          → sudo systemctl status go2rtc"
echo "    backchannel     → sudo systemctl status backchannel"
echo "    person-count-ws → sudo systemctl status person-count-ws"
echo "    stream-auth     → sudo systemctl status stream-auth"
echo "    cloudflared     → sudo systemctl status cloudflared"
echo "    nginx           → sudo systemctl status nginx"
echo "    oobe-setup      → sudo systemctl status oobe-setup"
echo "    audio-auto      → systemctl --user status audio-autostart"
echo "    sync-config     → crontab -l (every 5 min)"
echo "    device-update   → crontab -l (every 5 min)"
echo "    update-server   → sudo systemctl status device-update-server"
echo "    logic-service   → sudo systemctl status logic-service"
echo "    ai-core         → sudo systemctl status ai-core"
echo "    sim7600-4g      → sudo systemctl status sim7600-4g"
echo "    net-watchdog    → sudo systemctl status network-watchdog"
echo ""
log "Phase 2 complete: files and services configured"

# ---------------------------------------------------------------------------
# Post-deploy: restart services if requested
# ---------------------------------------------------------------------------
if [ "$RESTART_MODE" = "all" ]; then
    step "Restarting all services"
    while IFS= read -r svc; do
        restart_service "$svc"
    done < <(get_valid_services)
    for f in "$SERVICES_DIR"/*.timer; do
        [ -f "$f" ] || continue
        _timer=$(basename "$f")
        log "Restarting timer $_timer..."
        run_stream systemctl restart "$_timer" \
            && log "$_timer restarted" \
            || err "Failed to restart $_timer"
    done
    log "All services restarted"
elif [ "$RESTART_MODE" = "specific" ]; then
    step "Restarting requested services"
    for TARGET in "${RESTART_TARGETS[@]}"; do
        restart_service "$TARGET"
    done
fi
