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

# Check if interface name matches the requested mode
iface_matches_mode() {
    local DEV="$1"
    local MODE="$2"
    [ -z "$DEV" ] && return 1
    case "$MODE" in
        wifi) [[ "$DEV" =~ ^(wlan|wlp|wlx|wlP) ]] ;;
        lan)  [[ "$DEV" =~ ^(eth|enp|enP|eno|enx|end) ]] ;;
        4g)   [[ "$DEV" =~ ^(usb|wwan|wwp) ]] ;;
        auto) return 0 ;;  # any interface is valid for auto
        *)    return 1 ;;
    esac
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

# --- Step 2: Áp config qua systemd (ổn định sau reboot, không phụ thuộc PID file) ---
SIGNALLED=0
if systemctl is-active --quiet network-watchdog 2>/dev/null; then
    # ExecReload = kill -HUP $MAINPID — không cần /run/network-watchdog.pid
    if systemctl reload network-watchdog 2>/dev/null; then
        log "Reloaded network-watchdog (SIGHUP via systemd)."
        SIGNALLED=1
    else
        log "reload failed, trying HUP via PID file..."
        if [ -f "$PIDFILE" ]; then
            WD_PID=$(tr -d ' \n' <"$PIDFILE")
            if [ -n "$WD_PID" ] && kill -HUP "$WD_PID" 2>/dev/null; then
                SIGNALLED=1
            fi
        fi
    fi
fi

if [ "$SIGNALLED" -eq 0 ]; then
    # inactive/failed: start (không restart — tránh kill process đang khởi động)
    log "network-watchdog inactive — starting service (may wait for dependencies)..."
    if systemctl start network-watchdog 2>/dev/null; then
        # Chờ active ngắn (tránh race PID file ngay sau ExecStart)
        _sw_wait=0
        while [ "$_sw_wait" -lt 20 ]; do
            systemctl is-active --quiet network-watchdog && break
            sleep 0.3
            _sw_wait=$((_sw_wait + 1))
        done
        if systemctl is-active --quiet network-watchdog; then
            systemctl reload network-watchdog 2>/dev/null || true
            log "network-watchdog started and reloaded."
            SIGNALLED=1
        fi
    fi
fi

if [ "$SIGNALLED" -eq 0 ]; then
    err "Could not start or signal network-watchdog. Try: sudo systemctl status network-watchdog"
    exit 1
fi

# --- Step 3: Check if already on correct interface for requested mode ---
if iface_matches_mode "$BEFORE_DEV" "$MODE"; then
    log "Already on correct interface type for mode '$MODE' (dev $BEFORE_DEV)"
    echo ""
    echo "========================================="
    log "SUCCESS: Network mode set to $MODE (route unchanged — already on $BEFORE_DEV)"
    log "Verification:"
    ip route get "$PING_HOST" 2>/dev/null | head -1
    echo "========================================="
    exit 0
fi

# --- Step 4: Wait and verify the route actually changed ---
# 4G modem thường cần thêm thời gian; watchdog chỉ chuyển default khi ping 8.8.8.8 OK.
MAX_WAIT=15
[ "$MODE" = "4g" ] && MAX_WAIT=45

WAITED=0
VERIFIED=0

while [ $WAITED -lt $MAX_WAIT ]; do
    sleep 2
    WAITED=$((WAITED + 2))
    AFTER_DEV=$(get_current_route_dev)

    # Success if route changed to an interface matching the requested mode
    if iface_matches_mode "$AFTER_DEV" "$MODE"; then
        VERIFIED=1
        break
    fi
    # Also accept any change for auto mode
    if [ "$MODE" = "auto" ] && [ $WAITED -ge 6 ]; then
        VERIFIED=1
        break
    fi
done

AFTER_DEV=$(get_current_route_dev)

# --- Step 5: Report result ---
echo ""
echo "========================================="
if [ "$VERIFIED" -eq 1 ] || iface_matches_mode "$AFTER_DEV" "$MODE"; then
    log "SUCCESS: Network switched to $MODE"
    log "  Route: $BEFORE_DEV → $AFTER_DEV"
elif [ "$AFTER_DEV" != "$BEFORE_DEV" ]; then
    # Route changed but not to expected type — still report it
    log "Route changed: $BEFORE_DEV → $AFTER_DEV (target mode: $MODE — interface may not be available)"
else
    err "ROUTE DID NOT CHANGE after ${MAX_WAIT}s"
    err "  Expected change from '$BEFORE_DEV' but still on '$AFTER_DEV'"
    echo ""
    log "Attempting direct route fix..."

    systemctl reload network-watchdog 2>/dev/null || true
    sleep 3

    FINAL_DEV=$(get_current_route_dev)
    if iface_matches_mode "$FINAL_DEV" "$MODE"; then
        log "SUCCESS after retry: $BEFORE_DEV → $FINAL_DEV"
    elif [ "$FINAL_DEV" != "$BEFORE_DEV" ]; then
        log "Route changed after retry: $BEFORE_DEV → $FINAL_DEV (target mode: $MODE)"
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
