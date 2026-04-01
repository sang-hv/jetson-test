#!/bin/bash
###############################################################################
# install-software.sh
# Phase 1: Install OS/software dependencies and runtime binaries.
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
    err "It needs to be run with sudo/root"
    exit 1
fi

step "Phase 1/7: System packages"
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
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-alsa \
    gstreamer1.0-pulseaudio \
    ffmpeg \
    modemmanager \
    libmm-glib-dev \
    usb-modeswitch \
    usb-modeswitch-data \
    minicom \
    2>&1 | tail -8 | tee -a "$LOG_FILE"
log "Base software packages installed"

# Remove unnecessary services that waste CPU/RAM on edge devices
apt-get remove -y -qq tracker-miner-fs apport 2>&1 | tail -3 | tee -a "$LOG_FILE" || true
apt-get autoremove -y -qq 2>&1 | tail -1 | tee -a "$LOG_FILE" || true
log "Removed tracker-miner-fs (file indexer) and apport (crash reporter)"

step "Phase 2/7: Storage and directories"
if [ -d /data ]; then
    DATA_DIR="/data/mini-pc"
    VENV_DIR="/data/venv/mini-pc"
    log "SSD detected at /data — using SSD"
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

step "Phase 3/7: Swap and performance"
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

nvpmodel -m 0 2>/dev/null && log "Performance mode: MAX" || warn "nvpmodel not available"
jetson_clocks 2>/dev/null && log "jetson_clocks enabled" || warn "jetson_clocks not available"
usermod -aG video,audio,docker,i2c,dialout "$ACTUAL_USER" 2>/dev/null || true
log "User $ACTUAL_USER added to required groups"

step "Phase 4/7: Python environment"
pip3 install jetson-stats 2>&1 | tail -1 | tee -a "$LOG_FILE" || warn "jetson-stats install failed"
sudo -u "$ACTUAL_USER" python3 -m venv "$VENV_DIR" 2>/dev/null || true
if [ -f "$VENV_DIR/bin/pip" ]; then
    sudo -u "$ACTUAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip 2>&1 | tail -1 | tee -a "$LOG_FILE"
    log "Python venv created: $VENV_DIR"
else
    warn "Python venv creation failed"
fi

step "Phase 5/7: GStreamer validation"
for pkg_name in libopenal-data libzvbi-common; do
    installed_ver=$(dpkg-query -W -f='${Version}' "$pkg_name" 2>/dev/null || echo "")
    if echo "$installed_ver" | grep -q "sav0"; then
        warn "Downgrading $pkg_name from savoury1 PPA..."
        apt-get install -y --allow-downgrades "$pkg_name" 2>&1 | tee -a "$LOG_FILE"
    fi
done

for plugin in h264parse voaacenc mpegtsmux jpegdec x264enc; do
    if su - "$ACTUAL_USER" -c "gst-inspect-1.0 $plugin" >/dev/null 2>&1; then
        log "GStreamer plugin: $plugin ✓"
    else
        warn "GStreamer plugin: $plugin NOT FOUND"
    fi
done

step "Phase 6/7: go2rtc"
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

step "Phase 7/7: cloudflared and ModemManager"
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

systemctl enable ModemManager 2>/dev/null || true
log "ModemManager service enabled"
log "Phase 1 complete: software installation done"
