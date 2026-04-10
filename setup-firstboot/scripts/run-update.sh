#!/bin/bash
###############################################################################
#  run-update.sh — OTA software update for Jetson Nano
#
#  Called by device_update/server.py when backend triggers an update.
#  Runs in background: git fetch → checkout branch → deploy → callback.
#
#  Usage:
#    /opt/device/run-update.sh <branch>
#
#  Args:
#    branch — git branch to checkout (e.g. feature/camera-v2, main)
###############################################################################

set -uo pipefail

VERSION="${1:?Usage: run-update.sh <branch>}"

LOCK_FILE="/tmp/device-update.lock"
LOG_FILE="/tmp/device-update-$(echo "$VERSION" | tr '/' '-').log"
DEVICE_ENV="/etc/device/device.env"
REPO_DIR=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2; }

load_env() {
    local key val
    while IFS='=' read -r key val; do
        key=$(echo "$key" | xargs)
        val=$(echo "$val" | xargs)
        [[ -z "$key" || "$key" == \#* ]] && continue
        export "$key=$val"
    done < "$DEVICE_ENV"
}

find_repo_dir() {
    # Check repo-path marker first
    if [ -f /etc/device/repo-path ]; then
        local rp
        rp=$(cat /etc/device/repo-path)
        if [ -d "$rp" ] && [ -f "$rp/setup-services.sh" ]; then
            echo "$rp"
            return 0
        fi
    fi
    # Search common locations
    local candidates=(
        "/home/*/setup/setup-firstboot"
        "/home/*/setup-firstboot"
    )
    for pattern in "${candidates[@]}"; do
        for dir in $pattern; do
            if [ -f "$dir/setup-services.sh" ]; then
                echo "$dir"
                return 0
            fi
        done
    done
    return 1
}

generate_signature() {
    local ts="$1"
    echo -n "${DEVICE_ID}|${ts}" | openssl dgst -sha256 -hmac "$SECRET_KEY" | awk '{print $NF}'
}

ack_backend() {
    local error_msg="${1:-}"

    local ts
    ts=$(date +%s)
    local sig
    sig=$(generate_signature "$ts")

    local finished_at
    finished_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

    # Escape error message for JSON
    local safe_error=""
    if [ -n "$error_msg" ]; then
        safe_error=$(echo "$error_msg" | head -20 | tr '\n' ' ' | sed 's/"/\\"/g' | cut -c1-500)
    fi

    local payload
    payload=$(printf '{"finished_at":"%s","error_message":"%s"}' "$finished_at" "$safe_error")

    log "ACK backend: finished_at=$finished_at error_message=$safe_error"
    curl -s \
        -X PATCH \
        -H "Content-Type: application/json" \
        -H "X-Device-ID: ${DEVICE_ID}" \
        -H "X-Timestamp: ${ts}" \
        -H "X-Signature: ${sig}" \
        -d "$payload" \
        --connect-timeout 10 \
        --max-time 30 \
        "${BACKEND_URL}/api/v1/update-logs/${DEVICE_ID}/ack" \
        >> "$LOG_FILE" 2>&1 || err "Failed to ACK backend"
}

cleanup() {
    rm -f "$LOCK_FILE"
    log "Lock released"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    # Acquire lock
    if [ -f "$LOCK_FILE" ]; then
        local pid
        pid=$(cat "$LOCK_FILE" 2>/dev/null)
        if kill -0 "$pid" 2>/dev/null; then
            err "Another update is running (PID $pid)"
            exit 1
        fi
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
    trap cleanup EXIT

    log "=== OTA Update START ==="
    log "Branch: $VERSION"

    # Load device env
    if [ ! -f "$DEVICE_ENV" ]; then
        err "$DEVICE_ENV not found"
        exit 1
    fi
    load_env

    if [ -z "${DEVICE_ID:-}" ] || [ -z "${BACKEND_URL:-}" ] || [ -z "${SECRET_KEY:-}" ]; then
        err "Missing DEVICE_ID, BACKEND_URL, or SECRET_KEY in $DEVICE_ENV"
        exit 1
    fi

    # Find repo directory
    REPO_DIR=$(find_repo_dir) || {
        err "Cannot find setup-firstboot repo directory"
        ack_backend "Cannot find setup-firstboot repo directory"
        exit 1
    }
    log "Repo directory: $REPO_DIR"

    # Get current branch/version before update
    local current_version
    current_version=$(cd "$REPO_DIR" && git describe --tags --always 2>/dev/null || echo "unknown")
    log "Current version: $current_version"

    # --- Git fetch & checkout branch ---
    log "Fetching from origin..."
    cd "$REPO_DIR"
    if ! git fetch origin >> "$LOG_FILE" 2>&1; then
        err "git fetch failed"
        ack_backend "git fetch failed"
        exit 1
    fi

    log "Checking out branch $VERSION..."
    if ! git checkout "$VERSION" >> "$LOG_FILE" 2>&1; then
        err "git checkout $VERSION failed"
        ack_backend "git checkout $VERSION failed"
        exit 1
    fi

    log "Pulling latest from origin/$VERSION..."
    if ! git pull origin "$VERSION" >> "$LOG_FILE" 2>&1; then
        err "git pull origin $VERSION failed"
        ack_backend "git pull origin $VERSION failed"
        exit 1
    fi

    # Verify checkout
    local actual_version
    actual_version=$(git describe --tags --always 2>/dev/null || echo "$VERSION")
    log "Checked out: $actual_version"

    # --- Run deploy ---
    log "Running setup-services.sh --restart-all..."
    if ! bash "$REPO_DIR/setup-services.sh" --restart-all >> "$LOG_FILE" 2>&1; then
        err "setup-services.sh failed"
        ack_backend "setup-services.sh failed — check $LOG_FILE"
        exit 1
    fi

    # --- Health check: verify key services are running ---
    log "Health check..."
    local failed_services=""
    for svc in go2rtc camera-stream stream-auth nginx; do
        if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
            failed_services="$failed_services $svc"
        fi
    done

    if [ -n "$failed_services" ]; then
        err "Services not running after update:$failed_services"
        ack_backend "Services not running:$failed_services"
        exit 1
    fi

    # --- Success ---
    log "=== OTA Update SUCCESS: $actual_version ==="
    ack_backend ""
}

main "$@"
