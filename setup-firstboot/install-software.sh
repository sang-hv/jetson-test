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

run_stream() {
    # Stream command output to both terminal and log (realtime).
    # stdbuf helps avoid buffering when commands produce long output.
    if command -v stdbuf >/dev/null 2>&1; then
        stdbuf -oL -eL "$@" 2>&1 | tee -a "$LOG_FILE"
    else
        "$@" 2>&1 | tee -a "$LOG_FILE"
    fi
}

if [ "$EUID" -ne 0 ]; then
    err "It needs to be run with sudo/root"
    exit 1
fi

step "Phase 1/8: System packages"
run_stream apt-get update

# Hold all NVIDIA/L4T packages BEFORE upgrade to prevent them from being
# upgraded to incompatible versions that break the camera pipeline.
L4T_HOLD_EARLY=(
    nvidia-l4t-multimedia nvidia-l4t-gstreamer nvidia-l4t-camera
    nvidia-l4t-core nvidia-l4t-cuda nvidia-l4t-nvsci
    nvidia-l4t-multimedia-utils nvidia-l4t-init nvidia-l4t-3d-core
    nvidia-l4t-firmware
)
apt-mark hold "${L4T_HOLD_EARLY[@]}" 2>/dev/null || true
log "NVIDIA L4T packages held before upgrade"

run_stream apt-get upgrade -y

run_stream apt-get install -y \
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
    | cat
log "Base software packages installed"

# Remove unnecessary services that waste CPU/RAM on edge devices
run_stream apt-get remove -y tracker-miner-fs apport || true
# NOTE: apt autoremove is deferred to AFTER nvidia packages are held (Phase 5.5)
# Running it here would remove nvidia-l4t-* packages before they are protected.
log "Removed tracker-miner-fs (file indexer) and apport (crash reporter)"

step "Phase 2/8: Storage and directories"
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

step "Phase 3/8: Swap and performance"
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

step "Phase 4/8: Python environment"
run_stream pip3 install jetson-stats || warn "jetson-stats install failed"
sudo -u "$ACTUAL_USER" python3 -m venv "$VENV_DIR" 2>/dev/null || true
if [ -f "$VENV_DIR/bin/pip" ]; then
    run_stream sudo -u "$ACTUAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip
    log "Python venv created: $VENV_DIR"
else
    warn "Python venv creation failed"
fi

step "Phase 5/8: AI / TensorRT packages"
# Pin TensorRT to 10.3.0 — .engine files are NOT compatible across major versions.
# Upgrading TensorRT requires re-exporting all models (10-20 min per model on Jetson).
TRT_VERSION="10.3.0.30-1+cuda12.5"
TRT_PACKAGES=(
    "tensorrt=${TRT_VERSION}"
    "libnvinfer10=${TRT_VERSION}"
    "libnvinfer-dev=${TRT_VERSION}"
    "libnvinfer-bin=${TRT_VERSION}"
    "libnvinfer-plugin10=${TRT_VERSION}"
    "libnvinfer-plugin-dev=${TRT_VERSION}"
    "libnvinfer-dispatch10=${TRT_VERSION}"
    "libnvinfer-dispatch-dev=${TRT_VERSION}"
    "libnvinfer-lean10=${TRT_VERSION}"
    "libnvinfer-lean-dev=${TRT_VERSION}"
    "libnvinfer-vc-plugin10=${TRT_VERSION}"
    "libnvinfer-vc-plugin-dev=${TRT_VERSION}"
    "libnvinfer-headers-dev=${TRT_VERSION}"
    "libnvinfer-headers-plugin-dev=${TRT_VERSION}"
    "libnvinfer-samples=${TRT_VERSION}"
    "libnvonnxparsers10=${TRT_VERSION}"
    "libnvonnxparsers-dev=${TRT_VERSION}"
    "python3-libnvinfer=${TRT_VERSION}"
    "python3-libnvinfer-dev=${TRT_VERSION}"
    "python3-libnvinfer-dispatch=${TRT_VERSION}"
    "python3-libnvinfer-lean=${TRT_VERSION}"
)
HOLD_PACKAGES=(
    tensorrt
    libnvinfer10 libnvinfer-dev libnvinfer-bin
    libnvinfer-plugin10 libnvinfer-plugin-dev
    libnvinfer-dispatch10 libnvinfer-dispatch-dev
    libnvinfer-lean10 libnvinfer-lean-dev
    libnvinfer-vc-plugin10 libnvinfer-vc-plugin-dev
    libnvinfer-headers-dev libnvinfer-headers-plugin-dev
    libnvinfer-samples
    libnvonnxparsers10 libnvonnxparsers-dev
    python3-libnvinfer python3-libnvinfer-dev
    python3-libnvinfer-dispatch python3-libnvinfer-lean
    # L4T packages — apt autoremove will remove these and kill the camera pipeline
    nvidia-l4t-multimedia
    nvidia-l4t-gstreamer
    nvidia-l4t-camera
    nvidia-l4t-core
    nvidia-l4t-cuda
    nvidia-l4t-nvsci
    nvidia-l4t-multimedia-utils
    nvidia-l4t-init
    nvidia-l4t-3d-core
    nvidia-l4t-firmware
)

run_stream apt-get install -y --allow-downgrades "${TRT_PACKAGES[@]}" nvidia-l4t-multimedia \
    || warn "TensorRT install failed"
log "TensorRT ${TRT_VERSION} installed"

run_stream apt-mark hold "${HOLD_PACKAGES[@]}"
log "TensorRT + NVIDIA packages held (use 'apt-mark unhold' to release)"

# WARNING: Do NOT run apt autoremove on Jetson.
# apt-mark hold only protects held packages, NOT their dependencies.
# autoremove will remove libprotobuf-lite23, libavutil56, libswresample3, etc.
# which are indirect deps of nvargus-daemon and gstreamer1.0-libav.
# This kills the camera pipeline (Argus socket crash + black stream).

step "Phase 6/8: GStreamer validation"
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

# Fix NVIDIA CSI camera: nvarguscamerasrc needs both standard libjpeg (jpeg_set_defaults)
# and NVIDIA's libnvjpeg (jpeg_set_hardware_acceleration_parameters_enc).
# Without preload, apt autoremove can break the link and kill the camera pipeline.
PRELOAD_FILE="/etc/ld.so.preload"
PRELOAD_LIBS=(
    "/lib/aarch64-linux-gnu/libjpeg.so.8"
    "/usr/lib/aarch64-linux-gnu/nvidia/libnvjpeg.so"
)
if [ ! -f "/lib/aarch64-linux-gnu/libjpeg.so.8" ]; then
    log "Installing libjpeg-turbo8..."
    run_stream apt-get install -y libjpeg-turbo8
fi
if [ ! -f "/usr/lib/aarch64-linux-gnu/nvidia/libnvjpeg.so" ]; then
    log "Installing nvidia-l4t-multimedia..."
    run_stream apt-get install -y nvidia-l4t-multimedia
fi
for lib in "${PRELOAD_LIBS[@]}"; do
    if [ ! -f "$lib" ]; then
        warn "Missing $lib after install — nvarguscamerasrc (CSI camera) may not work"
        continue
    fi
    if ! grep -qF "$lib" "$PRELOAD_FILE" 2>/dev/null; then
        echo "$lib" | tee -a "$PRELOAD_FILE" >/dev/null
        log "Added to ld.so.preload: $lib"
    fi
done

step "Phase 7/8: go2rtc"
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

step "Phase 8/8: cloudflared and ModemManager"
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
