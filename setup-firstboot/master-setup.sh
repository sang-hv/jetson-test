#!/bin/bash
###############################################################################
#  Jetson Nano - Master Setup Script
#  Chạy 1 lần trên Jetson mới để cài đặt toàn bộ:
#    - System setup (SSD, swap, performance mode, Python venv)
#    - go2rtc + GStreamer livestream
#    - Audio backchannel (FFmpeg→pacat)
#    - PulseAudio auto-config + echo cancel
#    - Nginx reverse proxy
#    - Cloudflare tunnel
#
#  Usage:
#    chmod +x master-setup.sh
#    sudo ./master-setup.sh
###############################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/jetson-setup-$(date +%Y%m%d_%H%M%S).log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $*" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[✗]${NC} $*" | tee -a "$LOG_FILE"; }
step() { echo -e "\n${BLUE}━━━ $* ━━━${NC}" | tee -a "$LOG_FILE"; }

# Check root
if [ "$EUID" -ne 0 ]; then
    err "Cần chạy với sudo: sudo ./master-setup.sh"
    exit 1
fi

# Detect actual user (not root)
ACTUAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
ACTUAL_HOME=$(eval echo "~$ACTUAL_USER")
ACTUAL_UID=$(id -u "$ACTUAL_USER")

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Jetson Nano - Master Setup                 ║"
echo "║   System + Livestream + Backchannel + Audio   ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  User:     $ACTUAL_USER"
echo "  Home:     $ACTUAL_HOME"
echo "  Log:      $LOG_FILE"
echo ""

###############################################################################
# STEP 1: System packages (base + build tools)
###############################################################################
step "Step 1/12: System packages"

apt-get update -qq 2>&1 | tail -1 | tee -a "$LOG_FILE"
apt-get upgrade -y -qq 2>&1 | tail -3 | tee -a "$LOG_FILE"

apt-get install -y -qq \
    build-essential cmake pkg-config \
    curl wget git nano vim htop net-tools \
    python3 python3-pip python3-venv python3-dev \
    v4l-utils alsa-utils \
    nginx \
    libjpeg-dev libpng-dev libtiff-dev \
    libavcodec-dev libavformat-dev libswscale-dev libv4l-dev \
    libasound2-dev portaudio19-dev \
    2>&1 | tail -5 | tee -a "$LOG_FILE"
log "System packages installed"

###############################################################################
# STEP 2: SSD detection + Data directories
###############################################################################
step "Step 2/12: Storage & Data directories"

if [ -d /data ]; then
    DATA_DIR="/data/mini-pc"
    VENV_DIR="/data/venv/mini-pc"
    log "SSD detected at /data — using SSD for data storage"
    chown -R "$ACTUAL_USER:$ACTUAL_USER" /data
    sudo -u "$ACTUAL_USER" mkdir -p "$DATA_DIR"/{db,media,faces,logs,models}
    sudo -u "$ACTUAL_USER" mkdir -p /data/venv
else
    DATA_DIR="$ACTUAL_HOME/data"
    VENV_DIR="$ACTUAL_HOME/.venv"
    warn "No SSD at /data — using home directory"
    sudo -u "$ACTUAL_USER" mkdir -p "$DATA_DIR"/{db,media,faces,logs,models}
fi
log "Data dir: $DATA_DIR"
log "Venv dir: $VENV_DIR"

###############################################################################
# STEP 3: Swap + Performance mode
###############################################################################
step "Step 3/12: Swap & Performance"

# Swap (4GB)
if [ ! -f /swapfile ]; then
    log "Creating 8GB swap file..."
    fallocate -l 8G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile swap swap defaults 0 0' | tee -a /etc/fstab
    log "Swap created"
else
    log "Swap already exists"
fi

# Performance mode
nvpmodel -m 0 2>/dev/null && log "Performance mode: MAX (nvpmodel -m 0)" || warn "nvpmodel not available"
jetson_clocks 2>/dev/null && log "jetson_clocks enabled" || warn "jetson_clocks not available"

# User groups
usermod -aG video,audio,docker,i2c "$ACTUAL_USER" 2>/dev/null || true
log "User $ACTUAL_USER added to video,audio,docker,i2c groups"

###############################################################################
# STEP 4: Python venv + jetson-stats
###############################################################################
step "Step 4/12: Python environment"

# jetson-stats
pip3 install jetson-stats 2>&1 | tail -1 | tee -a "$LOG_FILE" || warn "jetson-stats install failed"

# Virtual environment
sudo -u "$ACTUAL_USER" python3 -m venv "$VENV_DIR" 2>/dev/null || true
if [ -f "$VENV_DIR/bin/pip" ]; then
    sudo -u "$ACTUAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip 2>&1 | tail -1 | tee -a "$LOG_FILE"
    log "Python venv created: $VENV_DIR"
else
    warn "Python venv creation failed"
fi

###############################################################################
# STEP 5: GStreamer
###############################################################################
step "Step 5/12: GStreamer"

# Fix broken savoury1 PPA packages if needed
for pkg_name in libopenal-data libzvbi-common; do
    installed_ver=$(dpkg-query -W -f='${Version}' "$pkg_name" 2>/dev/null || echo "")
    if echo "$installed_ver" | grep -q "sav0"; then
        warn "Downgrading $pkg_name from savoury1 PPA..."
        apt-get install -y --allow-downgrades "$pkg_name" 2>&1 | tee -a "$LOG_FILE"
    fi
done

apt-get install -y -qq \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-alsa \
    gstreamer1.0-pulseaudio \
    2>&1 | tail -5 | tee -a "$LOG_FILE"

# Verify critical plugins
for plugin in h264parse voaacenc mpegtsmux jpegdec x264enc; do
    if su - "$ACTUAL_USER" -c "gst-inspect-1.0 $plugin" >/dev/null 2>&1; then
        log "GStreamer plugin: $plugin ✓"
    else
        warn "GStreamer plugin: $plugin NOT FOUND"
    fi
done

###############################################################################
# STEP 6: FFmpeg
###############################################################################
step "Step 6/13: FFmpeg"

apt-get install -y -qq ffmpeg 2>&1 | tail -2 | tee -a "$LOG_FILE"
log "FFmpeg: $(ffmpeg -version 2>&1 | head -1)"

###############################################################################
# STEP 7: go2rtc
###############################################################################
step "Step 7/12: go2rtc"

if [ -f /usr/local/bin/go2rtc ]; then
    log "go2rtc already installed"
else
    log "Downloading go2rtc..."
    cd /tmp
    wget -q --show-progress https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_arm64
    chmod +x go2rtc_linux_arm64
    mv go2rtc_linux_arm64 /usr/local/bin/go2rtc
    log "go2rtc installed"
fi

# Config
mkdir -p /etc/go2rtc
cp "$SCRIPT_DIR/config/go2rtc.yaml" /etc/go2rtc/go2rtc.yaml
log "go2rtc config → /etc/go2rtc/go2rtc.yaml"

# Stream script (tee pipeline: stream stdout + AI ZMQ)
mkdir -p /opt/stream
cp "$SCRIPT_DIR/scripts/start-stream.py" /opt/stream/start-stream.py
chmod +x /opt/stream/start-stream.py
log "Stream script → /opt/stream/start-stream.py"

# Systemd services — fix User to actual user
sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/camera-stream.service" \
    > /etc/systemd/system/camera-stream.service
sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/go2rtc.service" \
    > /etc/systemd/system/go2rtc.service
systemctl daemon-reload
systemctl enable camera-stream.service go2rtc.service
log "camera-stream + go2rtc services enabled (User=$ACTUAL_USER)"

###############################################################################
# STEP 8: Device Identity + Sync Config
###############################################################################
step "Step 8/13: Device Identity & Config Sync"

# Read device info from env vars or prompt
DEVICE_ID="${DEVICE_ID:-}"
BACKEND_URL="${BACKEND_URL:-}"
SECRET_KEY="${SECRET_KEY:-}"

mkdir -p /etc/device

if [ -f /etc/device/device.env ]; then
    log "Device identity already configured"
    source /etc/device/device.env
else
    if [ -z "$DEVICE_ID" ] || [ -z "$BACKEND_URL" ] || [ -z "$SECRET_KEY" ]; then
        warn "Device identity not provided via env vars"
        warn "Set later: edit /etc/device/device.env"
        # Create template
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

# Install sync script
mkdir -p /opt/device
cp "$SCRIPT_DIR/scripts/sync-config.py" /opt/device/sync-config.py
chmod +x /opt/device/sync-config.py
log "Sync script → /opt/device/sync-config.py"

# Setup cronjob (every 5 minutes)
CRON_LINE="*/5 * * * * /usr/bin/python3 /opt/device/sync-config.py >> /var/log/sync-config.log 2>&1"
if crontab -l 2>/dev/null | grep -q "sync-config"; then
    log "Cronjob already exists"
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    log "Cronjob installed: every 5 minutes"
fi

# Run first sync if device.env is configured
if [ -n "$DEVICE_ID" ] && [ -n "$BACKEND_URL" ] && [ -n "$SECRET_KEY" ]; then
    log "Running first config sync..."
    python3 /opt/device/sync-config.py 2>&1 | tee -a "$LOG_FILE" || warn "First sync failed (will retry via cronjob)"
fi

###############################################################################
# STEP 9: Cloudflared
###############################################################################
step "Step 9/13: Cloudflared"

if [ -f /usr/local/bin/cloudflared ]; then
    log "Cloudflared already installed: $(cloudflared --version 2>&1 | head -1)"
else
    log "Downloading cloudflared..."
    cd /tmp
    wget -q --show-progress https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
    chmod +x cloudflared-linux-arm64
    mv cloudflared-linux-arm64 /usr/local/bin/cloudflared
    log "Cloudflared installed: $(cloudflared --version 2>&1 | head -1)"
fi

# Tunnel token is managed by sync-config.py (from API response)
# If first sync succeeded, tunnel should already be configured
if systemctl is-active cloudflared >/dev/null 2>&1; then
    log "Cloudflared tunnel already running"
else
    warn "Cloudflared installed — tunnel will be configured after sync-config runs"
fi

###############################################################################
# STEP 9: Audio Backchannel
###############################################################################
step "Step 10/13: Audio Backchannel"

mkdir -p /opt/backchannel
cp "$SCRIPT_DIR/backchannel/server.py" /opt/backchannel/server.py
cp "$SCRIPT_DIR/backchannel/start.sh" /opt/backchannel/start.sh
cp "$SCRIPT_DIR/backchannel/demo.html" /opt/backchannel/demo.html
chmod +x /opt/backchannel/start.sh

# Python dependencies
pip3 install websockets 2>&1 | tail -2 | tee -a "$LOG_FILE"

# Systemd service — fix User to actual user
sed "s/__USER__/$ACTUAL_USER/" "$SCRIPT_DIR/services/backchannel.service" \
    > /etc/systemd/system/backchannel.service
systemctl daemon-reload
systemctl enable backchannel.service
log "Backchannel server → /opt/backchannel/"

###############################################################################
# STEP 10: Nginx reverse proxy
###############################################################################
step "Step 11/13: Nginx"

cp "$SCRIPT_DIR/config/nginx.conf" /etc/nginx/sites-available/go2rtc
ln -sf /etc/nginx/sites-available/go2rtc /etc/nginx/sites-enabled/go2rtc
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>&1 | tee -a "$LOG_FILE"
systemctl enable nginx
log "Nginx configured"

###############################################################################
# STEP 11: Audio Autostart (PulseAudio + Echo Cancel)
###############################################################################
step "Step 12/13: Audio Autostart"

mkdir -p /opt/audio
cp "$SCRIPT_DIR/scripts/setup-audio-autostart.sh" /opt/audio/setup-audio-autostart.sh
chmod +x /opt/audio/setup-audio-autostart.sh

# Create systemd USER service
SYSTEMD_USER_DIR="$ACTUAL_HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
cp "$SCRIPT_DIR/services/audio-autostart.service" "$SYSTEMD_USER_DIR/audio-autostart.service"
chown -R "$ACTUAL_USER:$ACTUAL_USER" "$ACTUAL_HOME/.config/systemd"

# Enable user service (need D-Bus env vars for systemctl --user under sudo)
su - "$ACTUAL_USER" -c "export XDG_RUNTIME_DIR=/run/user/$ACTUAL_UID DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$ACTUAL_UID/bus && systemctl --user daemon-reload && systemctl --user enable audio-autostart.service" 2>&1 | tee -a "$LOG_FILE"

# Enable lingering so user services start without login
loginctl enable-linger "$ACTUAL_USER"

log "Audio autostart service enabled"

###############################################################################
# STEP 12: Final verification
###############################################################################
step "Step 13/13: Verification"

echo "" | tee -a "$LOG_FILE"
echo "  Versions:" | tee -a "$LOG_FILE"
echo "    go2rtc:      $(go2rtc --version 2>&1 | head -1 || echo 'N/A')" | tee -a "$LOG_FILE"
echo "    ffmpeg:      $(ffmpeg -version 2>&1 | head -1 || echo 'N/A')" | tee -a "$LOG_FILE"
echo "    cloudflared: $(cloudflared --version 2>&1 | head -1 || echo 'N/A')" | tee -a "$LOG_FILE"
echo "    python3:     $(python3 --version 2>&1)" | tee -a "$LOG_FILE"
echo "    nginx:       $(nginx -v 2>&1)" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Verify services are enabled
for svc in go2rtc backchannel cloudflared nginx; do
    if systemctl is-enabled "$svc" >/dev/null 2>&1; then
        log "Service $svc: enabled ✓"
    else
        warn "Service $svc: NOT enabled"
    fi
done

###############################################################################
# SUMMARY
###############################################################################
echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅ Master Setup Complete!                   ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Storage:"
echo "    Data dir:    $DATA_DIR"
echo "    Python venv: $VENV_DIR"
echo "    Device env:  /etc/device/device.env"
echo "    Config:      /etc/device/config.json"
echo "    Faces:       $DATA_DIR/faces/embeddings.json"
echo ""
echo "  Services:"
echo "    go2rtc       → sudo systemctl status go2rtc"
echo "    backchannel  → sudo systemctl status backchannel"
echo "    cloudflared  → sudo systemctl status cloudflared"
echo "    nginx        → sudo systemctl status nginx"
echo "    audio-auto   → systemctl --user status audio-autostart"
echo "    sync-config  → crontab -l (every 5 min)"
echo ""
echo "  ⚠️  Action required:"
if [ -z "$DEVICE_ID" ] || [ -z "$BACKEND_URL" ]; then
echo "    1. Edit /etc/device/device.env → set device identity"
echo "    2. Run: python3 /opt/device/sync-config.py (first sync)"
echo "    3. Reboot: sudo reboot"
else
echo "    1. Reboot: sudo reboot"
fi
echo ""
echo "  Log saved: $LOG_FILE"
echo ""
