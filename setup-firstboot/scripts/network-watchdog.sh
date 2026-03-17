#!/bin/bash
###############################################################################
#  network-watchdog.sh — Network Self-Healing Watchdog
#
#  Chạy liên tục bởi systemd network-watchdog.service.
#  Monitors connectivity và:
#    - Áp dụng routing theo NETWORK_MODE (auto/wifi/lan/4g)
#    - Tự reconnect 4G khi mất kết nối
#    - Self-heal: restart sim7600-4g service nếu cần
#
#  Signals:
#    SIGHUP  — reload config and re-apply routing immediately
#    SIGUSR1 — one-shot: apply routing once (used by switch-network.sh)
#
#  Log: journalctl -u network-watchdog
###############################################################################

NETWORK_CONF="/etc/device/network.conf"
IFACE_4G_CACHE="/run/4g-interface"
PIDFILE="/run/network-watchdog.pid"

# Default config
NETWORK_MODE="auto"
PING_HOST="8.8.8.8"
MAX_RETRIES=3
CHECK_INTERVAL=30

LOG_TAG="net-watchdog"

# Signal flags (set by trap handlers, consumed in main loop)
RELOAD_REQUESTED=0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local TS
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TS] [INFO]  $*"
    logger -t "$LOG_TAG" "$*"
}

warn() {
    local TS
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TS] [WARN]  $*"
    logger -t "$LOG_TAG" "WARN: $*"
}

err() {
    local TS
    TS=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$TS] [ERROR] $*" >&2
    logger -t "$LOG_TAG" "ERROR: $*"
}

# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------
on_sighup() {
    log "SIGHUP received — will reload config and re-apply routing"
    RELOAD_REQUESTED=1
}

on_cleanup() {
    rm -f "$PIDFILE"
    log "=== Network Watchdog Stopped ==="
    exit 0
}

trap on_sighup HUP USR1
trap on_cleanup TERM INT

# ---------------------------------------------------------------------------
# Load / reload network config (hotreload on each cycle)
# ---------------------------------------------------------------------------
load_config() {
    if [ -f "$NETWORK_CONF" ]; then
        # shellcheck disable=SC1090
        source "$NETWORK_CONF"
    fi
}

# ---------------------------------------------------------------------------
# Detect available interfaces
# ---------------------------------------------------------------------------
get_iface_4g() {
    # Check cached value first
    if [ -f "$IFACE_4G_CACHE" ]; then
        local CACHED
        CACHED=$(cat "$IFACE_4G_CACHE")
        if ip link show "$CACHED" &>/dev/null; then
            echo "$CACHED"
            return 0
        fi
    fi
    # Auto-detect
    for candidate in usb0 usb1 usb2 wwan0 wwp0s21u1i4 wwan0u1i4; do
        if ip link show "$candidate" &>/dev/null; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

get_iface_lan() {
    for candidate in eth0 enp3s0 eno1 enx$(cat /sys/class/net/eth0/address 2>/dev/null | tr -d ':'); do
        if ip link show "$candidate" &>/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    # Generic: first wired interface not lo/usb*/ww*
    local IFACE
    IFACE=$(ls /sys/class/net 2>/dev/null | grep -E '^(eth|enp|eno|enx)' | head -1)
    [ -n "$IFACE" ] && echo "$IFACE" || return 1
}

get_iface_wifi() {
    local IFACE
    # Add support for wlP* (PCIe interfaces) alongside standard wlan/wlx/wlp
    IFACE=$(ls /sys/class/net 2>/dev/null | grep -E '^(wlan|wlp|wlx|wlP)' | head -1)
    [ -n "$IFACE" ] && echo "$IFACE" || return 1
}

# ---------------------------------------------------------------------------
# Check if interface has IP and can ping
# ---------------------------------------------------------------------------
iface_has_ip() {
    local IFACE="$1"
    ip addr show "$IFACE" 2>/dev/null | grep -q "inet "
}

iface_can_ping() {
    local IFACE="$1"
    local HOST="${2:-$PING_HOST}"
    ping -I "$IFACE" -c 2 -W 5 -q "$HOST" &>/dev/null || \
        ping6 -I "$IFACE" -c 2 -W 5 -q 2001:4860:4860::8888 &>/dev/null
}

# ---------------------------------------------------------------------------
# Suppress NetworkManager from overwriting our route metrics.
# NM re-adds default routes on every DHCP renewal, so we must tell NM itself
# to use the metric we want.
# ---------------------------------------------------------------------------
nm_set_route_metric() {
    local IFACE="$1"
    local METRIC="$2"
    command -v nmcli &>/dev/null || return 0

    local CON_NAME
    CON_NAME=$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null \
        | grep ":${IFACE}$" | head -1 | cut -d: -f1)
    [ -z "$CON_NAME" ] && return 0

    local CUR_METRIC
    CUR_METRIC=$(nmcli -t -f ipv4.route-metric connection show "$CON_NAME" 2>/dev/null \
        | cut -d: -f2)

    if [ "$CUR_METRIC" != "$METRIC" ]; then
        nmcli connection modify "$CON_NAME" ipv4.route-metric "$METRIC" 2>/dev/null || true
        nmcli connection modify "$CON_NAME" ipv6.route-metric "$METRIC" 2>/dev/null || true
        nmcli device reapply "$IFACE" 2>/dev/null || true
        log "NM route-metric for $CON_NAME ($IFACE) → $METRIC"
    fi
}

# ---------------------------------------------------------------------------
# Set default route priority (metric) per network mode
#
# Lower metric = higher priority.
# Priority interface gets metric 50; others get 700+.
# ---------------------------------------------------------------------------
apply_routing() {
    local MODE="$1"
    local IFACE_4G IFACE_LAN IFACE_WIFI

    IFACE_4G=$(get_iface_4g 2>/dev/null) || IFACE_4G=""
    IFACE_LAN=$(get_iface_lan 2>/dev/null) || IFACE_LAN=""
    IFACE_WIFI=$(get_iface_wifi 2>/dev/null) || IFACE_WIFI=""

    case "$MODE" in
        4g)
            [ -n "$IFACE_4G" ]   && set_metric "$IFACE_4G"   50
            [ -n "$IFACE_LAN" ]  && set_metric "$IFACE_LAN"  700
            [ -n "$IFACE_WIFI" ] && set_metric "$IFACE_WIFI" 800
            ;;
        lan)
            [ -n "$IFACE_LAN" ]  && set_metric "$IFACE_LAN"  50
            [ -n "$IFACE_4G" ]   && set_metric "$IFACE_4G"   700
            [ -n "$IFACE_WIFI" ] && set_metric "$IFACE_WIFI" 800
            ;;
        wifi)
            [ -n "$IFACE_WIFI" ] && set_metric "$IFACE_WIFI" 50
            [ -n "$IFACE_4G" ]   && set_metric "$IFACE_4G"   700
            [ -n "$IFACE_LAN" ]  && set_metric "$IFACE_LAN"  800
            ;;
        auto|*)
            [ -n "$IFACE_LAN" ]  && set_metric "$IFACE_LAN"  50
            [ -n "$IFACE_WIFI" ] && set_metric "$IFACE_WIFI" 60
            [ -n "$IFACE_4G" ]   && set_metric "$IFACE_4G"   70
            ;;
    esac

    ip route flush cache 2>/dev/null || true

    log "Routing applied [mode=$MODE]: 4G=$IFACE_4G LAN=$IFACE_LAN WiFi=$IFACE_WIFI"

    local ACTUAL_DEV
    ACTUAL_DEV=$(ip route get "$PING_HOST" 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
    log "Verify: ip route get $PING_HOST → dev $ACTUAL_DEV"
}

set_metric() {
    local IFACE="$1"
    local METRIC="$2"

    # 1) Tell NetworkManager to use this metric (prevents NM from overwriting)
    nm_set_route_metric "$IFACE" "$METRIC"

    # 2) Capture gateway before deleting routes
    local GW
    GW=$(ip route show dev "$IFACE" 2>/dev/null | grep "^default" | grep -oP 'via \K\S+' | head -1)

    if [ -z "$GW" ]; then
        local IP
        IP=$(ip addr show "$IFACE" 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1 | head -1)
        if [ -n "$IP" ]; then
            GW=$(echo "$IP" | awk -F. '{print $1"."$2"."$3".1"}')
        fi
    fi

    # 3) Remove ALL default routes for this interface (not just known metrics)
    while ip route del default dev "$IFACE" 2>/dev/null; do :; done

    # 4) Re-add with the desired metric
    if [ -n "$GW" ]; then
        ip route replace default via "$GW" dev "$IFACE" metric "$METRIC" 2>/dev/null || true
    else
        ip route replace default dev "$IFACE" metric "$METRIC" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Get current primary interface (lowest metric default route)
# ---------------------------------------------------------------------------
get_primary_iface() {
    ip route show default 2>/dev/null \
        | awk '{
            dev=""; metric=9999
            for(i=1;i<=NF;i++) {
                if($i=="dev") dev=$(i+1)
                if($i=="metric") metric=$(i+1)+0
            }
            if(dev!="") print metric, dev
        }' \
        | sort -n | head -1 | awk '{print $2}'
}

# ---------------------------------------------------------------------------
# Heal 4G: restart systemd service
# ---------------------------------------------------------------------------
heal_4g() {
    warn "Self-healing: restarting sim7600-4g service..."
    systemctl restart sim7600-4g 2>/dev/null && {
        log "sim7600-4g restarted — waiting 20s for dial-up..."
        sleep 20
    } || err "Failed to restart sim7600-4g"
}

# ---------------------------------------------------------------------------
# Check connectivity for current priority interface
# Returns: 0=ok, 1=fail
# ---------------------------------------------------------------------------
check_connectivity() {
    local MODE="$1"
    local IFACE_4G IFACE_LAN IFACE_WIFI
    IFACE_4G=$(get_iface_4g 2>/dev/null) || IFACE_4G=""
    IFACE_LAN=$(get_iface_lan 2>/dev/null) || IFACE_LAN=""
    IFACE_WIFI=$(get_iface_wifi 2>/dev/null) || IFACE_WIFI=""

    # Determine primary interface per mode
    local PRIMARY=""
    case "$MODE" in
        4g)   PRIMARY="$IFACE_4G" ;;
        lan)  PRIMARY="$IFACE_LAN" ;;
        wifi) PRIMARY="$IFACE_WIFI" ;;
        auto|*) PRIMARY="$IFACE_4G" ;;
    esac

    if [ -n "$PRIMARY" ] && iface_has_ip "$PRIMARY" && iface_can_ping "$PRIMARY"; then
        return 0
    fi

    # Primary failed — try fallbacks
    for FALLBACK in "$IFACE_LAN" "$IFACE_WIFI" "$IFACE_4G"; do
        [ "$FALLBACK" = "$PRIMARY" ] && continue
        [ -z "$FALLBACK" ] && continue
        if iface_has_ip "$FALLBACK" && iface_can_ping "$FALLBACK"; then
            warn "Primary $PRIMARY down — using fallback $FALLBACK"
            return 0
        fi
    done

    return 1
}

# ---------------------------------------------------------------------------
# Main watchdog loop
# ---------------------------------------------------------------------------
main() {
    local FORCED_MODE=""
    if [ $# -gt 0 ]; then
        case "$1" in
            4g|lan|wifi|auto)
                FORCED_MODE="$1"
                ;;
            *)
                echo "Usage: $0 [auto|4g|lan|wifi]"
                exit 1
                ;;
        esac
    fi

    echo $$ > "$PIDFILE"
    log "=== Network Watchdog Started (PID $$) ==="

    local LAST_MODE=""
    local FAIL_COUNT=0

    while true; do
        load_config

        if [ -n "$FORCED_MODE" ]; then
            NETWORK_MODE="$FORCED_MODE"
        fi

        # SIGHUP/SIGUSR1 → force re-apply even if mode hasn't changed
        if [ "$RELOAD_REQUESTED" -eq 1 ]; then
            log "Reload requested — forcing routing re-apply for mode '$NETWORK_MODE'"
            RELOAD_REQUESTED=0
            apply_routing "$NETWORK_MODE"
            LAST_MODE="$NETWORK_MODE"
            FAIL_COUNT=0
            sleep 2
            continue
        fi

        if [ "$NETWORK_MODE" != "$LAST_MODE" ]; then
            log "Network mode changed: '$LAST_MODE' → '$NETWORK_MODE'"
            apply_routing "$NETWORK_MODE"
            LAST_MODE="$NETWORK_MODE"
            FAIL_COUNT=0
        fi

        if check_connectivity "$NETWORK_MODE"; then
            if [ $FAIL_COUNT -gt 0 ]; then
                log "Connectivity restored (was down for ${FAIL_COUNT} checks)"
            fi
            FAIL_COUNT=0
        else
            FAIL_COUNT=$((FAIL_COUNT + 1))
            warn "Connectivity check failed (attempt $FAIL_COUNT/$MAX_RETRIES)"

            if [ $FAIL_COUNT -ge "$MAX_RETRIES" ]; then
                err "Network down after $MAX_RETRIES checks — triggering self-heal"
                heal_4g
                sleep 5
                apply_routing "$NETWORK_MODE"
                FAIL_COUNT=0
            fi
        fi

        # Use sleep in a way that can be interrupted by signals
        sleep "$CHECK_INTERVAL" &
        wait $! 2>/dev/null || true
    done
}

main "$@"
