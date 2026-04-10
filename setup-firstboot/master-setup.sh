#!/bin/bash
###############################################################################
#  Jetson Nano - Master Setup Script (orchestrator)
#
#  Usage:
#    sudo ./master-setup.sh                          # Full setup (install + deploy + enable)
#    sudo ./master-setup.sh --restart-all             # Restart ALL services
#    sudo ./master-setup.sh network-watchdog go2rtc   # Restart specific services
###############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/jetson-setup-$(date +%Y%m%d_%H%M%S).log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[✗]${NC} $*" | tee -a "$LOG_FILE"; }
step() { echo -e "\n${BLUE}━━━ $* ━━━${NC}" | tee -a "$LOG_FILE"; }

usage() {
    cat <<'EOF'
Usage:
  sudo ./master-setup.sh [--prompt-device-env] [setup-services args...]

Options:
  --prompt-device-env   Prompt for DEVICE_ID, BACKEND_URL, SECRET_KEY and write to /etc/device/device.env
  -h, --help            Show this help
EOF
}

if [ "$EUID" -ne 0 ]; then
    err "It needs to be run with sudo/root ./master-setup.sh"
    exit 1
fi

PROMPT_DEVICE_ENV=0
SETUP_SERVICES_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --prompt-device-env)
            PROMPT_DEVICE_ENV=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            SETUP_SERVICES_ARGS+=("$1")
            shift
            ;;
    esac
done

ACTUAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
ACTUAL_HOME=$(eval echo "~$ACTUAL_USER")
ACTUAL_UID=$(id -u "$ACTUAL_USER")

export SCRIPT_DIR LOG_FILE ACTUAL_USER ACTUAL_HOME ACTUAL_UID

if [ "$PROMPT_DEVICE_ENV" -eq 1 ]; then
    step "Prompt: device identity (/etc/device/device.env)"

    # Use /dev/tty to stay interactive even when piped.
    read -r -p "DEVICE_ID: " DEVICE_ID </dev/tty
    read -r -p "BACKEND_URL: " BACKEND_URL </dev/tty
    read -r -s -p "SECRET_KEY (hidden): " SECRET_KEY </dev/tty
    echo "" </dev/tty

    if [ -z "${DEVICE_ID:-}" ] || [ -z "${BACKEND_URL:-}" ] || [ -z "${SECRET_KEY:-}" ]; then
        err "DEVICE_ID/BACKEND_URL/SECRET_KEY must be non-empty"
        exit 1
    fi

    export DEVICE_ID BACKEND_URL SECRET_KEY
    export FORCE_DEVICE_ENV=1
fi

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Jetson Nano - Master Setup                 ║"
echo "║   System + Livestream + Backchannel + 4G     ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  User:     $ACTUAL_USER"
echo "  Home:     $ACTUAL_HOME"
echo "  Log:      $LOG_FILE"
echo ""

if [ ! -f "$SCRIPT_DIR/install-software.sh" ]; then
    err "Missing file: $SCRIPT_DIR/install-software.sh"
    exit 1
fi
if [ ! -f "$SCRIPT_DIR/setup-services.sh" ]; then
    err "Missing file: $SCRIPT_DIR/setup-services.sh"
    exit 1
fi
chmod +x "$SCRIPT_DIR/install-software.sh" "$SCRIPT_DIR/setup-services.sh"

step "Phase 1/2: Install software"
bash "$SCRIPT_DIR/install-software.sh"

step "Phase 2/2: Setup files and services"
bash "$SCRIPT_DIR/setup-services.sh" "${SETUP_SERVICES_ARGS[@]}"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅ Master Setup Complete!                   ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Log saved: $LOG_FILE"
echo ""
log "Done"
