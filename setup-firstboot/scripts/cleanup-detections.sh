#!/bin/bash
###############################################################################
#  cleanup-detections.sh — Remove old detection images & free disk space
#
#  Runs as a systemd timer (daily). Two cleanup strategies:
#    1. Age-based: remove date dirs older than image_retention_days (from DB)
#    2. Disk-based: if usage > 80%, remove oldest date dirs until under 80%
#
#  Detection image structure:
#    {DETECTION_DIR}/{event_type}/{YYYY-MM-DD}/*.webp
#
#  Usage:
#    sudo /opt/device/cleanup-detections.sh
###############################################################################

set -euo pipefail

DEVICE_ENV="/etc/device/device.env"
DEFAULT_RETENTION_DAYS=15
DISK_THRESHOLD=80

LOG_TAG="cleanup-detections"

log()  { echo "[$(date '+%H:%M:%S')] $*"; logger -t "$LOG_TAG" "$*"; }
warn() { echo "[$(date '+%H:%M:%S')] WARN: $*"; logger -t "$LOG_TAG" "WARN: $*"; }
err()  { echo "[$(date '+%H:%M:%S')] ERROR: $*" >&2; logger -t "$LOG_TAG" "ERROR: $*"; }

# ---------------------------------------------------------------------------
# Resolve data/detection directory
# ---------------------------------------------------------------------------
if [ -d /data/mini-pc ]; then
    DATA_DIR="/data/mini-pc"
else
    # Fallback to home dir of first non-root user with data/
    DATA_DIR=""
    for home in /home/*/data; do
        if [ -d "$home" ]; then
            DATA_DIR="$home"
            break
        fi
    done
    [ -z "$DATA_DIR" ] && DATA_DIR="/home/avis/data"
fi

DETECTION_DIR="${DETECTION_DIR:-$DATA_DIR/media/detection}"
DB_PATH="$DATA_DIR/db/logic_service.db"
LOG_DIR="$DATA_DIR/logs"

# ---------------------------------------------------------------------------
# Read image_retention_days from SQLite camera_settings
# ---------------------------------------------------------------------------
get_retention_days() {
    if [ ! -f "$DB_PATH" ]; then
        echo "$DEFAULT_RETENTION_DAYS"
        return
    fi

    local val
    val=$(sqlite3 "$DB_PATH" \
        "SELECT value FROM camera_settings WHERE key='image_retention_days' LIMIT 1;" \
        2>/dev/null || echo "")

    if [ -n "$val" ] && [ "$val" -ge 7 ] && [ "$val" -le 100 ] 2>/dev/null; then
        echo "$val"
    else
        echo "$DEFAULT_RETENTION_DAYS"
    fi
}

# ---------------------------------------------------------------------------
# 1. Age-based cleanup: remove date dirs older than retention_days
# ---------------------------------------------------------------------------
cleanup_old_images() {
    local retention_days
    retention_days=$(get_retention_days)
    local cutoff_date
    cutoff_date=$(date -d "-${retention_days} days" '+%Y-%m-%d' 2>/dev/null \
        || date -v-${retention_days}d '+%Y-%m-%d')

    log "Retention: ${retention_days} days (cutoff: $cutoff_date)"

    if [ ! -d "$DETECTION_DIR" ]; then
        log "Detection dir not found: $DETECTION_DIR — skipping"
        return
    fi

    local removed=0
    for event_dir in "$DETECTION_DIR"/*/; do
        [ -d "$event_dir" ] || continue
        for date_dir in "$event_dir"*/; do
            [ -d "$date_dir" ] || continue
            local dir_name
            dir_name=$(basename "$date_dir")
            # Only process YYYY-MM-DD format dirs
            if [[ "$dir_name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && [[ "$dir_name" < "$cutoff_date" ]]; then
                rm -rf "$date_dir"
                removed=$((removed + 1))
            fi
        done
    done

    [ "$removed" -gt 0 ] && log "Removed $removed old date directories"
}

# ---------------------------------------------------------------------------
# 2. Disk-based cleanup: if usage > 80%, remove oldest dirs first
# ---------------------------------------------------------------------------
cleanup_disk_space() {
    local usage
    usage=$(df "$DETECTION_DIR" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')

    if [ -z "$usage" ] || [ "$usage" -le "$DISK_THRESHOLD" ]; then
        return
    fi

    warn "Disk usage ${usage}% > ${DISK_THRESHOLD}% — cleaning up"

    # Collect all date dirs sorted oldest first
    local dirs=()
    for event_dir in "$DETECTION_DIR"/*/; do
        [ -d "$event_dir" ] || continue
        for date_dir in "$event_dir"*/; do
            [ -d "$date_dir" ] || continue
            local dir_name
            dir_name=$(basename "$date_dir")
            [[ "$dir_name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && dirs+=("$dir_name|$date_dir")
        done
    done

    # Sort oldest first
    IFS=$'\n' sorted=($(printf '%s\n' "${dirs[@]}" | sort)); unset IFS

    for entry in "${sorted[@]}"; do
        local dir_path="${entry#*|}"
        rm -rf "$dir_path"
        log "Disk pressure: removed $dir_path"

        usage=$(df "$DETECTION_DIR" 2>/dev/null | awk 'NR==2 {gsub(/%/,""); print $5}')
        if [ "$usage" -le "$DISK_THRESHOLD" ]; then
            log "Disk usage now ${usage}%"
            break
        fi
    done
}

# ---------------------------------------------------------------------------
# 3. Log cleanup: remove log files older than retention_days
# ---------------------------------------------------------------------------
cleanup_old_logs() {
    if [ ! -d "$LOG_DIR" ]; then
        return
    fi

    local retention_days
    retention_days=$(get_retention_days)

    local removed=0
    while IFS= read -r -d '' logfile; do
        rm -f "$logfile"
        removed=$((removed + 1))
    done < <(find "$LOG_DIR" -type f -name "*.log" -mtime "+${retention_days}" -print0 2>/dev/null)

    [ "$removed" -gt 0 ] && log "Removed $removed old log files"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log "=== Cleanup started ==="
log "Detection dir: $DETECTION_DIR"
log "DB path: $DB_PATH"

cleanup_old_images
cleanup_old_logs
cleanup_disk_space

log "=== Cleanup complete ==="
