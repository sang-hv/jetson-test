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

if [ "$EUID" -ne 0 ]; then
    err "It needs to be run with sudo/root ./master-setup.sh"
    exit 1
fi

ACTUAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo $USER)}"
ACTUAL_HOME=$(eval echo "~$ACTUAL_USER")
ACTUAL_UID=$(id -u "$ACTUAL_USER")

export SCRIPT_DIR LOG_FILE ACTUAL_USER ACTUAL_HOME ACTUAL_UID

if [ ! -f "$SCRIPT_DIR/setup-services.sh" ]; then
    err "Missing file: $SCRIPT_DIR/setup-services.sh"
    exit 1
fi
chmod +x "$SCRIPT_DIR/setup-services.sh"

# ---------------------------------------------------------------------------
# Restart mode: delegate to setup-services.sh
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--restart-all" ] || [ $# -gt 0 ]; then
    bash "$SCRIPT_DIR/setup-services.sh" "$@"
    exit $?
fi

# ---------------------------------------------------------------------------
# Full setup mode (no args)
# ---------------------------------------------------------------------------
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
chmod +x "$SCRIPT_DIR/install-software.sh"

step "Phase 1/2: Install software"
bash "$SCRIPT_DIR/install-software.sh"

step "Phase 2/2: Setup files and services"
bash "$SCRIPT_DIR/setup-services.sh"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   ✅ Master Setup Complete!                   ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Log saved: $LOG_FILE"
echo ""
log "Done"
