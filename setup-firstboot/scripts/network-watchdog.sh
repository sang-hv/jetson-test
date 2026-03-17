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
#  Log: journalctl -u network-watchdog
#       tail -f /var/log/network-watchdog.log
###############################################################################

NETWORK_CONF="/etc/device/network.conf"
IFACE_4G_CACHE="/run/4g-interface"

# Default config
NETWORK_MODE="auto"
PING_HOST="8.8.8.8"
MAX_RETRIES=3
CHECK_INTERVAL=30

LOG_TAG="net-watchdog"

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
# Set default route priority (metric) per network mode
#
# Lower metric = higher priority
# 4G=100, LAN=200, WiFi=300  (in auto mode: 4G wins)
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
            # auto: LAN primary → WiFi → 4G
            [ -n "$IFACE_LAN" ]  && set_metric "$IFACE_LAN"  50
            [ -n "$IFACE_WIFI" ] && set_metric "$IFACE_WIFI" 60
            [ -n "$IFACE_4G" ]   && set_metric "$IFACE_4G"   70
            ;;
    esac

    # Cực kỳ quan trọng: Xoá bộ nhớ đệm định tuyến của nhân Linux
    # Nếu không có lệnh này, các kết nối đang mở sẽ tiếp tục dùng đường cũ (cache)
    ip route flush cache 2>/dev/null || true

    log "Routing applied [mode=$MODE]: 4G=$IFACE_4G LAN=$IFACE_LAN WiFi=$IFACE_WIFI"
}

set_metric() {
    local IFACE="$1"
    local METRIC="$2"
    
    # Extract only the exact Gateway IP for this interface
    local GW
    GW=$(ip route show dev "$IFACE" 2>/dev/null | grep "^default" | grep -oP 'via \K\S+' | head -1)
    
    # Cứu hộ khẩn cấp: Nếu thẻ mạng có IP nhưng bị DHCP ngầm xoá mất Default Gateway
    if [ -z "$GW" ]; then
        local IP
        IP=$(ip addr show "$IFACE" 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1 | head -1)
        if [ -n "$IP" ]; then
            GW=$(echo "$IP" | awk -F. '{print $1"."$2"."$3".1"}')
        fi
    fi

    # Chỉ xoá bỏ các metric phụ trợ do chính nhánh gán ra (tránh cắn lầm metric mặc định của NM: 100, 600)
    for m in 50 60 70 100 200 300 500 600 700 800 900; do
        ip route del default dev "$IFACE" metric $m 2>/dev/null || true
    done
    
    # Với usb2 (4G), NM không quản lý, thỉnh thoảng sẽ bị đùn ra default route không có metric (metric 0)
    if [ "${IFACE:0:3}" = "usb" ]; then
         ip route del default dev "$IFACE" 2>/dev/null || true
    fi

    if [ -n "$GW" ]; then
        # Re-add purely with the known GW and new Metric
        ip route add default via "$GW" dev "$IFACE" metric "$METRIC" 2>/dev/null || true
    else
        # If it's a point-to-point without 'via' (like some wwan/usb setups)
        if ip route show dev "$IFACE" 2>/dev/null | grep -q "^default"; then
            ip route add default dev "$IFACE" metric "$METRIC" 2>/dev/null || true
        fi
    fi
}

# ---------------------------------------------------------------------------
# Get current primary interface (lowest metric default route)
# ---------------------------------------------------------------------------
get_primary_iface() {
    ip route show 2>/dev/null \
        | awk '/^default/ {print $NF, $(NF-2)}' \
        | sort -k2 -n \
        | head -1 \
        | awk '{print $1}'
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
    # Parse CLI argument to force mode
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

    log "=== Network Watchdog Started ==="

    local LAST_MODE=""
    local FAIL_COUNT=0

    while true; do
        load_config
        
        # Apply CLI override if provided
        if [ -n "$FORCED_MODE" ]; then
            NETWORK_MODE="$FORCED_MODE"
        fi

        # Apply routing if mode changed
        if [ "$NETWORK_MODE" != "$LAST_MODE" ]; then
            log "Network mode changed: '$LAST_MODE' → '$NETWORK_MODE'"
            apply_routing "$NETWORK_MODE"
            LAST_MODE="$NETWORK_MODE"
            FAIL_COUNT=0
        fi

        # Check connectivity
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
                # Re-apply routing after heal
                sleep 5
                apply_routing "$NETWORK_MODE"
                FAIL_COUNT=0
            fi
        fi

        sleep "$CHECK_INTERVAL"
    done
}

main "$@"
