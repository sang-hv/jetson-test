#!/bin/bash
###############################################################################
#  sync-config.sh - Sync device configuration from backend API
#
#  Runs as cronjob every 5 minutes. Syncs:
#    - Tokens & credentials → /etc/device/config.json
#    - Face embeddings      → /data/mini-pc/faces/embeddings.json
#    - Camera info          → /data/mini-pc/camera/infor_camera.json
#
#  Also triggers service restarts when critical config changes.
###############################################################################

set -euo pipefail

# --- Load device identity ---
DEVICE_ENV="/etc/device/device.env"
if [ ! -f "$DEVICE_ENV" ]; then
    echo "[sync] ERROR: $DEVICE_ENV not found. Run master-setup.sh first."
    exit 1
fi
source "$DEVICE_ENV"

# Validate required vars
for var in DEVICE_ID BACKEND_URL SECRET_KEY; do
    if [ -z "${!var:-}" ]; then
        echo "[sync] ERROR: $var not set in $DEVICE_ENV"
        exit 1
    fi
done

# --- Paths ---
CONFIG_DIR="/etc/device"
CONFIG_FILE="$CONFIG_DIR/config.json"
CONFIG_PREV="$CONFIG_DIR/config.prev.json"

# Data dir (SSD or fallback)
if [ -d /data/mini-pc ]; then
    DATA_DIR="/data/mini-pc"
else
    DATA_DIR="$HOME/data"
fi

FACES_DIR="$DATA_DIR/faces"
CAMERA_DIR="$DATA_DIR/camera"

mkdir -p "$FACES_DIR" "$CAMERA_DIR"

LOG_PREFIX="[sync $(date '+%H:%M:%S')]"

log()  { echo "$LOG_PREFIX $*"; }
warn() { echo "$LOG_PREFIX WARNING: $*"; }
err()  { echo "$LOG_PREFIX ERROR: $*" >&2; }

# --- Call backend API (HMAC signature — key never sent) ---
log "Syncing from $BACKEND_URL (device: $DEVICE_ID)"

API_URL="${BACKEND_URL}/api/v1/devices/${DEVICE_ID}/config"
TIMESTAMP=$(date +%s)
SIGNATURE=$(echo -n "${DEVICE_ID}|${TIMESTAMP}" | openssl dgst -sha256 -hmac "$SECRET_KEY" | awk '{print $NF}')

HTTP_RESPONSE=$(mktemp)
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$HTTP_RESPONSE" \
    -H "X-Device-ID: $DEVICE_ID" \
    -H "X-Timestamp: $TIMESTAMP" \
    -H "X-Signature: $SIGNATURE" \
    -H "Content-Type: application/json" \
    --connect-timeout 10 \
    --max-time 30 \
    "$API_URL" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" != "200" ]; then
    err "API returned HTTP $HTTP_CODE"
    [ -f "$HTTP_RESPONSE" ] && cat "$HTTP_RESPONSE" >&2
    rm -f "$HTTP_RESPONSE"
    exit 1
fi

# Validate JSON
if ! python3 -c "import json; json.load(open('$HTTP_RESPONSE'))" 2>/dev/null; then
    err "Invalid JSON response"
    rm -f "$HTTP_RESPONSE"
    exit 1
fi

log "API response OK"

# --- Save previous config for diff ---
if [ -f "$CONFIG_FILE" ]; then
    cp "$CONFIG_FILE" "$CONFIG_PREV"
fi

export HTTP_RESPONSE CONFIG_FILE FACES_DIR CAMERA_DIR

# --- Extract and save data using Python ---
python3 << 'PYTHON_SCRIPT'
import json
import os
import sys

response_file = os.environ.get('HTTP_RESPONSE', '')
config_file = os.environ.get('CONFIG_FILE', '')
faces_dir = os.environ.get('FACES_DIR', '')
camera_dir = os.environ.get('CAMERA_DIR', '')

with open(response_file) as f:
    data = json.load(f)

# 1. Save tokens & credentials to config.json
config_data = {
    "token_cloudflare": data.get("token_cloudflare", ""),
    "domain_tunnel": data.get("domain_tunnel", ""),
    "token_stream": data.get("token_stream", ""),
    "time_expire_stream_minute": data.get("time_expire_stream_minute", 30),
    "s3_credential": data.get("s3_credential", {}),
    "sqs_credential": data.get("sqs_credential", {}),
    "_last_synced": __import__('datetime').datetime.now().isoformat()
}
with open(config_file, 'w') as f:
    json.dump(config_data, f, indent=2)
print(f"[sync] Config saved → {config_file}")

# 2. Save face embeddings
face_data = data.get("face_embed", {})
if face_data:
    face_file = os.path.join(faces_dir, "embeddings.json")
    with open(face_file, 'w') as f:
        json.dump(face_data, f, indent=2)
    print(f"[sync] Face embeddings saved → {face_file}")

# 3. Save camera info
camera_data = data.get("infor_camera", {})
if camera_data:
    camera_file = os.path.join(camera_dir, "infor_camera.json")
    with open(camera_file, 'w') as f:
        json.dump(camera_data, f, indent=2)
    print(f"[sync] Camera info saved → {camera_file}")

PYTHON_SCRIPT

rm -f "$HTTP_RESPONSE"

# --- Detect changes & restart services if needed ---

restart_needed=""

if [ -f "$CONFIG_PREV" ]; then
    # Check if cloudflare token changed
    OLD_CF=$(python3 -c "import json; print(json.load(open('$CONFIG_PREV')).get('token_cloudflare',''))" 2>/dev/null || echo "")
    NEW_CF=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('token_cloudflare',''))" 2>/dev/null || echo "")
    
    if [ "$OLD_CF" != "$NEW_CF" ] && [ -n "$NEW_CF" ]; then
        log "Cloudflare token changed — updating tunnel"
        CF_SERVICE="/etc/systemd/system/cloudflared.service"
        if [ -f "$CF_SERVICE" ]; then
            # Update token in existing service file (safe, no downtime)
            sed -i "s|--token .*|--token $NEW_CF|" "$CF_SERVICE"
            systemctl daemon-reload
            systemctl restart cloudflared
            log "Cloudflared token updated via sed + restart"
        else
            # Service not yet installed — first install
            cloudflared service install "$NEW_CF"
            systemctl restart cloudflared
            log "Cloudflared tunnel installed (first time)"
        fi
        restart_needed="cloudflared"
    fi

    # Check if stream token changed
    OLD_ST=$(python3 -c "import json; print(json.load(open('$CONFIG_PREV')).get('token_stream',''))" 2>/dev/null || echo "")
    NEW_ST=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('token_stream',''))" 2>/dev/null || echo "")
    
    if [ "$OLD_ST" != "$NEW_ST" ]; then
        log "Stream token changed"
        restart_needed="${restart_needed:+$restart_needed, }stream_token"
    fi
else
    # First run — setup cloudflare tunnel if token present
    NEW_CF=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('token_cloudflare',''))" 2>/dev/null || echo "")
    if [ -n "$NEW_CF" ]; then
        log "First run — installing cloudflare tunnel"
        cloudflared service install "$NEW_CF" 2>/dev/null || true
        systemctl restart cloudflared 2>/dev/null || true
        restart_needed="cloudflared (first run)"
    fi
fi

if [ -n "$restart_needed" ]; then
    log "Services affected: $restart_needed"
else
    log "No changes detected — all services up to date"
fi

log "Sync complete"
