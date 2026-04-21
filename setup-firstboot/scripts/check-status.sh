#!/bin/bash
###############################################################################
#  check-status.sh — System health check
#
#  Reports status of all services and hardware devices:
#    - Systemd services (system + user)
#    - Cron jobs
#    - PulseAudio echo cancel
#    - USB microphone & speaker
#    - CSI camera
#    - LTE module (SIM7600)
#
#  Usage:
#    sudo ./check-status.sh
###############################################################################

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

OK="${GREEN}✓${NC}"
FAIL="${RED}✗${NC}"
WARN="${YELLOW}!${NC}"

ACTUAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo "$USER")}"
ACTUAL_UID=$(id -u "$ACTUAL_USER" 2>/dev/null || echo "")

section() { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

# Run a command as the real user (for PulseAudio)
as_user() {
    if [ -n "$ACTUAL_UID" ] && [ -d "/run/user/$ACTUAL_UID" ]; then
        sudo -u "$ACTUAL_USER" \
            XDG_RUNTIME_DIR="/run/user/$ACTUAL_UID" \
            PULSE_SERVER="unix:/run/user/$ACTUAL_UID/pulse/native" \
            "$@" 2>/dev/null
    else
        "$@" 2>/dev/null
    fi
}

###############################################################################
# Services
###############################################################################
section "Services"

SYSTEM_SERVICES=(
    camera-stream
    go2rtc
    ai-core
    logic-service
    oobe-setup
    backchannel
    person-count-ws
    stream-auth
    device-update-server
    nginx
    sim7600-4g
    network-watchdog
    cloudflared
)

USER_SERVICES=(
    audio-autostart
)

max_len=0
for s in "${SYSTEM_SERVICES[@]}" "${USER_SERVICES[@]}"; do
    (( ${#s} > max_len )) && max_len=${#s}
done

for svc in "${SYSTEM_SERVICES[@]}"; do
    state=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    enabled=$(systemctl is-enabled "$svc" 2>/dev/null || echo "unknown")
    case "$state" in
        active)   icon="$OK"   ;;
        inactive) icon="$WARN" ;;
        *)        icon="$FAIL" ;;
    esac
    printf "  %b  %-${max_len}s  %b%-10s%b  %b\n" "$icon" "$svc" \
        "$([ "$state" = "active" ] && echo "$GREEN" || echo "$RED")" "$state" "$NC" \
        "${DIM}(${enabled})${NC}"
done

for svc in "${USER_SERVICES[@]}"; do
    state=$(sudo -u "$ACTUAL_USER" XDG_RUNTIME_DIR="/run/user/$ACTUAL_UID" \
        systemctl --user is-active "$svc" 2>/dev/null || echo "unknown")
    enabled=$(sudo -u "$ACTUAL_USER" XDG_RUNTIME_DIR="/run/user/$ACTUAL_UID" \
        systemctl --user is-enabled "$svc" 2>/dev/null || echo "unknown")
    case "$state" in
        active)   icon="$OK"   ;;
        inactive) icon="$WARN" ;;
        *)        icon="$FAIL" ;;
    esac
    printf "  %b  %-${max_len}s  %b%-10s%b  %b\n" "$icon" "$svc [user]" \
        "$([ "$state" = "active" ] && echo "$GREEN" || echo "$RED")" "$state" "$NC" \
        "${DIM}(${enabled})${NC}"
done

###############################################################################
# Cron jobs
###############################################################################
section "Cron Jobs"

CRON_SCRIPTS=("sync-config.py" "device-update.py")
crontab_content=$(crontab -u "$ACTUAL_USER" -l 2>/dev/null || true)

for script in "${CRON_SCRIPTS[@]}"; do
    match=$(echo "$crontab_content" | grep "$script" | grep -v '^#' | head -1)
    if [ -n "$match" ]; then
        schedule=$(echo "$match" | awk '{print $1, $2, $3, $4, $5}')
        printf "  %b  %-25s  %b\n" "$OK" "$script" "${DIM}(${schedule})${NC}"
    else
        printf "  %b  %-25s  not in crontab\n" "$FAIL" "$script"
    fi
done

###############################################################################
# CSI Camera
###############################################################################
section "CSI Camera"

# Check nvargus-daemon
nvargus_state=$(systemctl is-active nvargus-daemon 2>/dev/null || echo "unknown")
if [ "$nvargus_state" = "active" ]; then
    printf "  %b  nvargus-daemon          active\n" "$OK"
else
    printf "  %b  nvargus-daemon          %s\n" "$FAIL" "$nvargus_state"
fi

# Check /dev/video* devices
video_devs=$(ls /dev/video* 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
if [ -n "$video_devs" ]; then
    printf "  %b  video devices           %s\n" "$OK" "$video_devs"
else
    printf "  %b  video devices           none found\n" "$FAIL"
fi

# Check AI shared memory
if [ -f /dev/shm/mini_pc_ai_frames.bin ]; then
    shm_size=$(stat -c%s /dev/shm/mini_pc_ai_frames.bin 2>/dev/null || stat -f%z /dev/shm/mini_pc_ai_frames.bin 2>/dev/null || echo "?")
    printf "  %b  AI shared memory        %s bytes\n" "$OK" "$shm_size"
else
    printf "  %b  AI shared memory        /dev/shm/mini_pc_ai_frames.bin not found\n" "$WARN"
fi

###############################################################################
# USB Audio (Microphone & Speaker)
###############################################################################
section "USB Audio"

# Check PulseAudio
if as_user pactl info >/dev/null 2>&1; then
    printf "  %b  PulseAudio              running\n" "$OK"
else
    printf "  %b  PulseAudio              not running\n" "$FAIL"
    echo -e "     ${DIM}(skipping audio checks)${NC}"
fi

if as_user pactl info >/dev/null 2>&1; then
    # Speaker (sink)
    usb_sink=$(as_user pactl list short sinks | grep -iv "echo\|monitor" | grep -i "jabra\|usb" | head -1)
    if [ -n "$usb_sink" ]; then
        sink_name=$(echo "$usb_sink" | awk '{print $2}')
        sink_state=$(echo "$usb_sink" | awk '{print $NF}')
        printf "  %b  Speaker                 %s %b(%s)%b\n" "$OK" "$sink_name" "$DIM" "$sink_state" "$NC"
    else
        printf "  %b  Speaker                 no USB sink found\n" "$FAIL"
    fi

    # Microphone (source)
    usb_source=$(as_user pactl list short sources | grep -iv "monitor\|echo" | grep -i "jabra\|usb" | head -1)
    if [ -n "$usb_source" ]; then
        source_name=$(echo "$usb_source" | awk '{print $2}')
        source_state=$(echo "$usb_source" | awk '{print $NF}')
        printf "  %b  Microphone              %s %b(%s)%b\n" "$OK" "$source_name" "$DIM" "$source_state" "$NC"
    else
        printf "  %b  Microphone              no USB source found\n" "$FAIL"
    fi

    # Echo Cancel
    ec_module=$(as_user pactl list short modules | grep "module-echo-cancel")
    if [ -n "$ec_module" ]; then
        ec_id=$(echo "$ec_module" | awk '{print $1}')

        # Verify echocancel_sink exists and check its source_master/sink_master
        ec_sink=$(as_user pactl list short sinks | grep "echocancel_sink" | head -1)
        ec_source=$(as_user pactl list short sources | grep "echocancel_source" | head -1)

        if [ -n "$ec_sink" ] && [ -n "$ec_source" ]; then
            ec_sink_state=$(echo "$ec_sink" | awk '{print $NF}')
            ec_source_state=$(echo "$ec_source" | awk '{print $NF}')

            # Check if echo cancel is wired to the correct USB device
            ec_ok=true
            if [ -n "$sink_name" ]; then
                ec_detail=$(as_user pactl list modules | grep -A 20 "module-echo-cancel" | grep "sink_master" || true)
                if [ -n "$ec_detail" ] && ! echo "$ec_detail" | grep -q "$sink_name"; then
                    ec_ok=false
                fi
            fi

            if [ "$ec_ok" = true ]; then
                printf "  %b  Echo Cancel             loaded (module %s)\n" "$OK" "$ec_id"
            else
                printf "  %b  Echo Cancel             loaded but sink_master mismatch — may need restart\n" "$WARN"
            fi
            printf "     %b echocancel_sink: %s, echocancel_source: %s%b\n" "$DIM" "$ec_sink_state" "$ec_source_state" "$NC"
        else
            printf "  %b  Echo Cancel             module loaded but sink/source missing\n" "$FAIL"
        fi
    else
        printf "  %b  Echo Cancel             not loaded\n" "$FAIL"
    fi
fi

###############################################################################
# LTE Module (SIM7600)
###############################################################################
section "LTE Module (SIM7600)"

# Check USB serial ports
tty_devs=$(ls /dev/ttyUSB* 2>/dev/null | tr '\n' ', ' | sed 's/,$//')
if [ -n "$tty_devs" ]; then
    printf "  %b  USB serial              %s\n" "$OK" "$tty_devs"
else
    printf "  %b  USB serial              no /dev/ttyUSB* found\n" "$FAIL"
    echo -e "     ${DIM}(modem not connected or not detected)${NC}"
fi

# Check lsusb for SIM7600
modem_usb=$(lsusb 2>/dev/null | grep -i "simcom\|sim7600\|qualcomm" | head -1)
if [ -n "$modem_usb" ]; then
    printf "  %b  USB device              %s\n" "$OK" "$modem_usb"
else
    printf "  %b  USB device              not found in lsusb\n" "$FAIL"
fi

# Check ModemManager
if systemctl is-active --quiet ModemManager 2>/dev/null; then
    modem_idx=$(mmcli -L 2>/dev/null | grep -oP '/org/freedesktop/ModemManager1/Modem/\K[0-9]+' | head -1)
    if [ -n "$modem_idx" ]; then
        modem_state=$(mmcli -m "$modem_idx" 2>/dev/null | grep -i "state" | head -1 | sed 's/.*state[^:]*://;s/^[ \t]*//' || echo "unknown")
        signal=$(mmcli -m "$modem_idx" --signal-get 2>/dev/null | grep -i "rssi\|rsrp" | head -1 | sed 's/.*://;s/^[ \t]*//' || true)
        printf "  %b  ModemManager           modem %s — %s\n" "$OK" "$modem_idx" "$modem_state"
        [ -n "$signal" ] && printf "     %b signal: %s%b\n" "$DIM" "$signal" "$NC"
    else
        printf "  %b  ModemManager           running but no modem registered\n" "$WARN"
    fi
else
    printf "  %b  ModemManager           not running\n" "$WARN"
fi

# Check 4G interface
iface_file="/run/4g-interface"
if [ -f "$iface_file" ]; then
    iface=$(cat "$iface_file")
    if ip addr show "$iface" 2>/dev/null | grep -q "inet "; then
        ip_addr=$(ip addr show "$iface" | grep "inet " | awk '{print $2}')
        printf "  %b  4G interface            %s — %s\n" "$OK" "$iface" "$ip_addr"
    else
        printf "  %b  4G interface            %s — no IP\n" "$WARN" "$iface"
    fi
else
    printf "  %b  4G interface            not established\n" "$WARN"
fi

###############################################################################
# Network
###############################################################################
section "Network"

net_mode="unknown"
if [ -f /etc/device/network.conf ]; then
    net_mode=$(grep "^NETWORK_MODE=" /etc/device/network.conf 2>/dev/null | cut -d= -f2 || echo "unknown")
fi
printf "  Mode: %s\n" "${net_mode:-auto}"

# Show active interfaces with IPs
while read -r line; do
    iface_name=$(echo "$line" | awk '{print $2}' | tr -d ':')
    ip_addr=$(ip addr show "$iface_name" 2>/dev/null | grep "inet " | awk '{print $2}' | head -1)
    if [ -n "$ip_addr" ]; then
        printf "  %b  %-22s %s\n" "$OK" "$iface_name" "$ip_addr"
    fi
done < <(ip link show up 2>/dev/null | grep "^[0-9]" | grep -v "lo:")

# Default route
default_gw=$(ip route show default 2>/dev/null | head -1)
if [ -n "$default_gw" ]; then
    printf "  Default route: %s\n" "$default_gw"
fi

###############################################################################
# Summary
###############################################################################
section "Device Info"

if [ -f /etc/device/device.env ]; then
    # shellcheck disable=SC1091
    source /etc/device/device.env 2>/dev/null
    printf "  Device ID:    %s\n" "${DEVICE_ID:-not set}"
    printf "  Backend URL:  %s\n" "${BACKEND_URL:-not set}"
fi

if [ -f /etc/device/repo-path ]; then
    repo=$(cat /etc/device/repo-path)
    if [ -d "$repo" ]; then
        version=$(cd "$repo" && git describe --tags --always 2>/dev/null || echo "unknown")
        printf "  Version:      %s\n" "$version"
    fi
fi

echo ""
