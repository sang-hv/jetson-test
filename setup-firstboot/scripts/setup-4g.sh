#!/bin/bash
###############################################################################
#  setup-4g.sh — SIM7600G-H 4G Modem Setup & Dial-up
#
#  Khởi tạo và kết nối 4G modem qua ModemManager (mmcli).
#  Chạy bởi systemd sim7600-4g.service khi boot.
#
#  Interface: usb0 (RNDIS) hoặc wwan0 (MBIM/QMI)
#  Log: journalctl -u sim7600-4g
###############################################################################

set -euo pipefail

NETWORK_CONF="/etc/device/network.conf"
LOG_TAG="4g-setup"

log()  { echo "[$(date '+%H:%M:%S')] [INFO]  $*"; logger -t "$LOG_TAG" "$*"; }
warn() { echo "[$(date '+%H:%M:%S')] [WARN]  $*"; logger -t "$LOG_TAG" "WARN: $*"; }
err()  { echo "[$(date '+%H:%M:%S')] [ERROR] $*" >&2; logger -t "$LOG_TAG" "ERROR: $*"; }

# ---------------------------------------------------------------------------
# Load network config
# ---------------------------------------------------------------------------
APN="internet"
if [ -f "$NETWORK_CONF" ]; then
    # shellcheck disable=SC1090
    source "$NETWORK_CONF"
fi
log "APN: $APN"

# ---------------------------------------------------------------------------
# Wait for modem USB device to appear
# ---------------------------------------------------------------------------
wait_for_modem() {
    local TIMEOUT=60
    local ELAPSED=0
    log "Waiting for SIM7600 USB device..."
    while [ $ELAPSED -lt $TIMEOUT ]; do
        if ls /dev/ttyUSB* &>/dev/null; then
            log "Modem detected: $(ls /dev/ttyUSB* | tr '\n' ' ')"
            return 0
        fi
        sleep 2
        ELAPSED=$((ELAPSED + 2))
    done
    err "Modem not found after ${TIMEOUT}s — check USB cable and jumpers"
    return 1
}

# ---------------------------------------------------------------------------
# Ensure ModemManager is running
# ---------------------------------------------------------------------------
ensure_modemmanager() {
    if ! systemctl is-active --quiet ModemManager; then
        log "Starting ModemManager..."
        systemctl start ModemManager
        sleep 3
    fi
    log "ModemManager: active"
}

# ---------------------------------------------------------------------------
# Get modem index from mmcli
# ---------------------------------------------------------------------------
get_modem_index() {
    local TRIES=0
    local MAX_TRIES=10
    local idx
    while [ $TRIES -lt $MAX_TRIES ]; do
        idx=$(mmcli -L 2>/dev/null | grep -oP '/org/freedesktop/ModemManager1/Modem/\K[0-9]+' | head -1)
        if [ -n "$idx" ]; then
            echo "$idx"
            return 0
        fi
        TRIES=$((TRIES + 1))
        sleep 3
    done
    return 1
}

# ---------------------------------------------------------------------------
# Configure modem: unlock SIM, set preferred mode
# ---------------------------------------------------------------------------
configure_modem() {
    local MODEM_IDX="$1"
    log "Configuring modem $MODEM_IDX..."

    # Enable modem (if disabled)
    mmcli -m "$MODEM_IDX" -e 2>/dev/null || true

    # Set preferred mode: 4G LTE first, fallback 3G
    mmcli -m "$MODEM_IDX" --set-preferred-mode=4g 2>/dev/null || \
        mmcli -m "$MODEM_IDX" --set-preferred-mode=any 2>/dev/null || true

    # Wait for SIM to be ready
    local TRIES=0
    while [ $TRIES -lt 15 ]; do
        local SIM_STATUS
        SIM_STATUS=$(mmcli -m "$MODEM_IDX" 2>/dev/null | grep -i "state\|sim-status" | head -3)
        log "Modem state: $(echo "$SIM_STATUS" | tr '\n' '|')"
        if mmcli -m "$MODEM_IDX" 2>/dev/null | grep -q "state.*registered\|state.*connected"; then
            break
        fi
        TRIES=$((TRIES + 1))
        sleep 2
    done

    log "Modem configured"
}

# ---------------------------------------------------------------------------
# Connect via ModemManager simple connect
# ---------------------------------------------------------------------------
connect_4g() {
    local MODEM_IDX="$1"
    log "Connecting to 4G (APN: $APN)..."

    # Simple connect — MM handles bearer creation + IP setup via DHCP
    if mmcli -m "$MODEM_IDX" \
        --simple-connect="apn=$APN" \
        2>&1 | tee -a /tmp/4g-connect.log; then
        log "4G connected via ModemManager"
        return 0
    else
        warn "mmcli simple-connect failed — trying NetworkManager nmcli fallback..."
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Bring up IP on 4G interface (usb0 or wwan0)
# ---------------------------------------------------------------------------
bring_up_interface() {
    # ModemManager with DHCP
    local IFACE=""
    
    # Try getting the exact data interface from ModemManager first
    local BEARER_IFACE
    BEARER_IFACE=$(mmcli -m "$MODEM_IDX" 2>/dev/null | grep -i "\/Bearer\/" | awk -F'/' '{print $NF}' | head -1)
    if [ -n "$BEARER_IFACE" ]; then
        IFACE=$(mmcli -b "$BEARER_IFACE" 2>/dev/null | grep -oP 'interface: \K\S+' | tr -d "'" || true)
    fi

    # Fallback if mmcli doesn't show it
    if [ -z "$IFACE" ] || ! ip link show "$IFACE" &>/dev/null; then
        for candidate in usb2 usb1 usb0 wwan0 wwp0s21u1i4 wwan0u1i4; do
            if ip link show "$candidate" &>/dev/null; then
                IFACE="$candidate"
                break
            fi
        done
    fi

    if [ -z "$IFACE" ]; then
        warn "No 4G network interface found (usb0/wwan0). Trying NetworkManager..."
        # Try creating NM connection
        if command -v nmcli &>/dev/null; then
            nmcli connection delete 4g-modem 2>/dev/null || true
            nmcli connection add type gsm \
                ifname '*' \
                con-name 4g-modem \
                apn "$APN" \
                autoconnect yes 2>/dev/null || true
            nmcli connection up 4g-modem 2>/dev/null || true
        fi
        return 1
    fi

    log "4G interface: $IFACE"

    # Bring up and get IP via DHCP (MM already set up normally)
    ip link set "$IFACE" up 2>/dev/null || true
    if ! ip addr show "$IFACE" | grep -q "inet "; then
        log "Running DHCP on $IFACE..."
        dhclient "$IFACE" -timeout 30 2>/dev/null || \
            udhcpc -i "$IFACE" -T 30 -t 5 2>/dev/null || true
    fi

    # Check we got an IP
    if ip addr show "$IFACE" | grep -q "inet "; then
        local IP
        IP=$(ip addr show "$IFACE" | grep "inet " | awk '{print $2}')
        log "4G IP: $IP on $IFACE"
        echo "$IFACE" > /run/4g-interface
        return 0
    else
        err "DHCP failed on $IFACE — no IP acquired"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "=== SIM7600G-H 4G Setup Start ==="

    wait_for_modem || exit 1
    ensure_modemmanager

    local MODEM_IDX
    if ! MODEM_IDX=$(get_modem_index); then
        err "ModemManager cannot find modem — trying AT command fallback"
        # Fallback: send AT commands directly to check modem is alive
        if command -v minicom &>/dev/null; then
            echo -e "AT\r" | timeout 5 minicom -D /dev/ttyUSB2 -b 115200 -S /dev/null 2>/dev/null | grep -q "OK" && \
                log "Modem responds to AT commands (OK)" || \
                warn "Modem not responding to AT"
        fi
        exit 1
    fi

    log "Modem index: $MODEM_IDX"
    configure_modem "$MODEM_IDX"
    connect_4g "$MODEM_IDX" || true
    bring_up_interface || exit 1

    log "=== 4G Setup Complete ==="
}

main "$@"
