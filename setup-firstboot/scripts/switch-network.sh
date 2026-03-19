#!/bin/bash
###############################################################################
#  switch-network.sh — Change Network Priority dynamically
#
#  Usage: sudo ./switch-network.sh [auto|4g|lan|wifi]
#  Description: Updates /etc/device/network.conf, signals the running watchdog
#               to reload, and verifies that the default route actually changed.
###############################################################################

set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Usage: $0 [auto|4g|lan|wifi]"
    exit 1
fi

MODE="$1"
CONF_FILE="/etc/device/network.conf"
PIDFILE="/run/network-watchdog.pid"
PING_HOST="8.8.8.8"

log()  { echo "[switch-network] $*"; }
err()  { echo "[switch-network] ERROR: $*" >&2; }

get_current_route_dev() {
    ip route get "$PING_HOST" 2>/dev/null | grep -oP 'dev \K\S+' | head -1
}

case "$MODE" in
    auto|4g|lan|wifi) ;;
    *)
        err "Invalid mode '$MODE'. Use: auto, 4g, lan, wifi."
        exit 1
        ;;
esac

log "Switching network mode to: $MODE"

BEFORE_DEV=$(get_current_route_dev)
log "Current default route → dev $BEFORE_DEV"

# --- Step 1: Update config file ---
if grep -q "^NETWORK_MODE=" "$CONF_FILE" 2>/dev/null; then
    sed -i "s/^NETWORK_MODE=.*/NETWORK_MODE=$MODE/" "$CONF_FILE"
else
    echo "NETWORK_MODE=$MODE" >> "$CONF_FILE"
fi

# --- Step 2: Signal the running watchdog to reload (SIGHUP) ---
SIGNALLED=0
if [ -f "$PIDFILE" ]; then
    WD_PID=$(cat "$PIDFILE")
    if kill -0 "$WD_PID" 2>/dev/null; then
        log "Signalling watchdog (PID $WD_PID) to reload..."
        kill -HUP "$WD_PID"
        SIGNALLED=1
    fi
fi

if [ "$SIGNALLED" -eq 0 ]; then
    log "Watchdog not running — restarting service..."
    systemctl restart network-watchdog 2>/dev/null || true
fi

# --- Step 3: Wait and verify the route actually changed ---
# 4G modem thường cần thêm thời gian; watchdog chỉ chuyển default khi ping 8.8.8.8 OK.
MAX_WAIT=15
[ "$MODE" = "4g" ] && MAX_WAIT=45

WAITED=0
VERIFIED=0

while [ $WAITED -lt $MAX_WAIT ]; do
    sleep 2
    WAITED=$((WAITED + 2))
    AFTER_DEV=$(get_current_route_dev)

    if [ "$MODE" = "auto" ]; then
        if [ "$AFTER_DEV" != "$BEFORE_DEV" ] || [ $WAITED -ge 6 ]; then
            VERIFIED=1
            break
        fi
    else
        if [ "$AFTER_DEV" != "$BEFORE_DEV" ]; then
            VERIFIED=1
            break
        fi
    fi
done

AFTER_DEV=$(get_current_route_dev)

# --- Step 4: Report result ---
echo ""
echo "========================================="
if [ "$VERIFIED" -eq 1 ] || [ "$AFTER_DEV" != "$BEFORE_DEV" ]; then
    log "SUCCESS: Network switched to $MODE"
    log "  Route: $BEFORE_DEV → $AFTER_DEV"
else
    err "ROUTE DID NOT CHANGE after ${MAX_WAIT}s"
    err "  Expected change from '$BEFORE_DEV' but still on '$AFTER_DEV'"
    echo ""
    log "Attempting direct route fix..."

    # Force one more reload + re-apply
    if [ -f "$PIDFILE" ]; then
        WD_PID=$(cat "$PIDFILE")
        kill -HUP "$WD_PID" 2>/dev/null || true
    fi
    sleep 3

    FINAL_DEV=$(get_current_route_dev)
    if [ "$FINAL_DEV" != "$BEFORE_DEV" ]; then
        log "SUCCESS after retry: $BEFORE_DEV → $FINAL_DEV"
    elif [ "$MODE" = "4g" ]; then
        log "MODE=4g is saved; route still on '$FINAL_DEV' until 4G can ping $PING_HOST (by design — avoids SSH drop)."
        log "Check: journalctl -u network-watchdog -n 20  &&  ip addr show usb0"
        exit 0
    else
        err "FAILED: Route still on '$FINAL_DEV'. Debug with:"
        err "  ip route show default"
        err "  journalctl -u network-watchdog --no-pager -n 30"
        exit 1
    fi
fi

echo ""
log "Verification:"
ip route get "$PING_HOST" 2>/dev/null | head -1
echo "========================================="
