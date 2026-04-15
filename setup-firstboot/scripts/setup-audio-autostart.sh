#!/bin/bash
# Auto-setup PulseAudio on boot: default sink + echo cancel
# This script waits for PulseAudio and USB devices, then configures everything.
# Designed to run as a systemd user service.

MAX_WAIT=30
WAIT_INTERVAL=2

log() { echo "[audio-autostart] $(date '+%H:%M:%S') $*"; }

# If this script is run with sudo/root, re-run it as the real user so we talk to
# the correct per-user PulseAudio instance (/run/user/<uid>/pulse).
if [ "${EUID:-$(id -u)}" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    _u="$SUDO_USER"
    _uid="$(id -u "$_u" 2>/dev/null || true)"
    if [ -n "$_uid" ] && [ -d "/run/user/$_uid" ]; then
        log "Running under root; re-exec as user=$_u (uid=$_uid)"
        exec su - "$_u" -c "XDG_RUNTIME_DIR=/run/user/$_uid PULSE_SERVER=unix:/run/user/$_uid/pulse/native /bin/bash /opt/audio/setup-audio-autostart.sh"
    fi
fi

# --- Wait for PulseAudio ---
log "Waiting for PulseAudio..."
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export PULSE_SERVER=unix:${XDG_RUNTIME_DIR}/pulse/native

waited=0
while ! pactl info >/dev/null 2>&1; do
    sleep $WAIT_INTERVAL
    waited=$((waited + WAIT_INTERVAL))
    if [ "$waited" -ge "$MAX_WAIT" ]; then
        log "PulseAudio not available after ${MAX_WAIT}s, starting manually..."
        if command -v systemctl >/dev/null 2>&1; then
            systemctl --user start pulseaudio.service 2>/dev/null || true
        fi
        pulseaudio --start 2>/dev/null || true
        sleep 2
        break
    fi
done

if ! pactl info >/dev/null 2>&1; then
    log "FATAL: PulseAudio not running!"
    exit 1
fi
log "PulseAudio is running"

# --- Wait for USB audio device ---
log "Waiting for USB audio device..."
waited=0
while ! pactl list short sinks 2>/dev/null | grep -qi "usb\|jabra"; do
    sleep $WAIT_INTERVAL
    waited=$((waited + WAIT_INTERVAL))
    if [ "$waited" -ge "$MAX_WAIT" ]; then
        log "WARNING: No USB audio device found after ${MAX_WAIT}s"
        break
    fi
done

# --- Set default sink to USB speaker (Jabra / any USB) ---
USB_SINK=$(pactl list short sinks | grep -iv "echo\|monitor" | grep -i "jabra\|speak" | head -1 | awk '{print $2}')
if [ -z "$USB_SINK" ]; then
    USB_SINK=$(pactl list short sinks | grep -iv "echo\|monitor" | grep -i "usb\|USB" | head -1 | awk '{print $2}')
fi

if [ -n "$USB_SINK" ]; then
    pactl set-default-sink "$USB_SINK"
    log "Default sink set: $USB_SINK"
else
    log "WARNING: No USB sink found, using system default"
fi

# --- Set default source to USB mic ---
USB_SOURCE=$(pactl list short sources | grep -iv "monitor\|echo" | grep -i "jabra\|speak" | head -1 | awk '{print $2}')
if [ -z "$USB_SOURCE" ]; then
    USB_SOURCE=$(pactl list short sources | grep -iv "monitor\|echo" | grep -i "usb\|USB" | head -1 | awk '{print $2}')
fi

if [ -n "$USB_SOURCE" ]; then
    pactl set-default-source "$USB_SOURCE"
    log "Default source set: $USB_SOURCE"
fi

# --- Load echo cancel module ---
log "Loading echo cancel module..."
# Unload any existing echo-cancel instances (by module name)
while read -r _mid; do
    [ -n "$_mid" ] && pactl unload-module "$_mid" 2>/dev/null || true
done < <(pactl list short modules 2>/dev/null | awk '$2=="module-echo-cancel"{print $1}')

SPEAKER_SINK="${USB_SINK:-$(pactl get-default-sink)}"
MIC_SOURCE="${USB_SOURCE:-$(pactl get-default-source)}"

load_success=false

# Method 1: WebRTC AEC (no aec_args — commas in aec_args break some pactl builds)
if [ "$load_success" = false ]; then
    if pactl load-module module-echo-cancel \
        sink_name=echocancel_sink \
        source_name=echocancel_source \
        source_master="$MIC_SOURCE" \
        sink_master="$SPEAKER_SINK" \
        aec_method=webrtc 2>/dev/null; then
        load_success=true
        log "Echo cancel loaded: webrtc"
    fi
fi

# Method 2: Default AEC
if [ "$load_success" = false ]; then
    if pactl load-module module-echo-cancel \
        sink_name=echocancel_sink \
        source_name=echocancel_source \
        source_master="$MIC_SOURCE" \
        sink_master="$SPEAKER_SINK" 2>/dev/null; then
        load_success=true
        log "Echo cancel loaded: default AEC"
    fi
fi

if [ "$load_success" = false ]; then
    log "WARNING: Echo cancel failed to load (mic: $MIC_SOURCE, speaker: $SPEAKER_SINK)"
else
    log "Echo cancel ready: echocancel_source / echocancel_sink"
    # Set initial volumes. These may be overridden by module-stream-restore when
    # a client (pulsesrc) connects; camera-stream ExecStartPost re-applies after connect.
    pactl set-source-volume "$MIC_SOURCE" 100% 2>/dev/null || true
    pactl set-source-volume echocancel_source 100% 2>/dev/null || true
    pactl set-sink-volume echocancel_sink 80% 2>/dev/null || true
fi

# --- Load switch-on-connect for hot-plug ---
if ! pactl list short modules 2>/dev/null | awk '$2=="module-switch-on-connect"{found=1} END{exit found?0:1}'; then
    pactl load-module module-switch-on-connect 2>/dev/null || true
fi

log "Audio autostart complete!"
