#!/bin/bash
###############################################################################
#  update-cloudflared.sh — Auto-update cloudflared binary
#
#  Runs weekly via crontab. Only upgrades if already installed.
#  Restarts the service after successful update.
###############################################################################

LOG_PREFIX="[cloudflared-update $(date '+%Y-%m-%d %H:%M:%S')]"
log()  { echo "$LOG_PREFIX $*"; }
err()  { echo "$LOG_PREFIX ERROR: $*" >&2; }

if ! command -v cloudflared >/dev/null 2>&1; then
    log "cloudflared not installed — skipping"
    exit 0
fi

OLD_VER=$(cloudflared --version 2>&1 | head -1 || echo "unknown")
log "Current: $OLD_VER"

log "Updating cloudflared..."
if apt-get update -qq -o Dir::Etc::sourcelist=/etc/apt/sources.list.d/cloudflared.list \
        -o Dir::Etc::sourceparts="-" 2>/dev/null \
   && apt-get install --only-upgrade -y -qq cloudflared 2>&1; then

    NEW_VER=$(cloudflared --version 2>&1 | head -1 || echo "unknown")

    if [ "$OLD_VER" = "$NEW_VER" ]; then
        log "Already up to date: $NEW_VER"
        exit 0
    fi

    log "Updated: $OLD_VER → $NEW_VER"

    if systemctl is-active --quiet cloudflared 2>/dev/null; then
        systemctl restart cloudflared
        log "cloudflared service restarted"
    fi
else
    err "apt-get upgrade failed"
    exit 1
fi
