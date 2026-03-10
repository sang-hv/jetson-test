#!/usr/bin/env python3
###############################################################################
#  sync-config.py - Sync device configuration from backend API
#
#  Runs as cronjob every 5 minutes. Syncs:
#    - Tokens & credentials → /etc/device/config.json
#    - Face embeddings      → /data/mini-pc/faces/embeddings.json
#    - Camera info          → /data/mini-pc/camera/infor_camera.json
#
#  Also triggers service restarts when critical config changes.
#
#  Usage:
#    sudo python3 /opt/device/sync-config.py
###############################################################################

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_PREFIX = f"[sync {datetime.now().strftime('%H:%M:%S')}]"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}")


def warn(msg: str) -> None:
    print(f"{LOG_PREFIX} WARNING: {msg}")


def err(msg: str) -> None:
    print(f"{LOG_PREFIX} ERROR: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Load device identity from /etc/device/device.env
# ---------------------------------------------------------------------------
DEVICE_ENV = Path("/etc/device/device.env")

if not DEVICE_ENV.exists():
    err(f"{DEVICE_ENV} not found. Run master-setup.sh first.")
    sys.exit(1)


def load_env(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file (ignores comments & blanks)."""
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


device_env = load_env(DEVICE_ENV)

DEVICE_ID = device_env.get("DEVICE_ID", "")
BACKEND_URL = device_env.get("BACKEND_URL", "")
SECRET_KEY = device_env.get("SECRET_KEY", "")

for var_name, var_val in [
    ("DEVICE_ID", DEVICE_ID),
    ("BACKEND_URL", BACKEND_URL),
    ("SECRET_KEY", SECRET_KEY),
]:
    if not var_val:
        err(f"{var_name} not set in {DEVICE_ENV}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CONFIG_DIR = Path("/etc/device")
CONFIG_FILE = CONFIG_DIR / "config.json"
CONFIG_PREV = CONFIG_DIR / "config.prev.json"

# Data dir (SSD or fallback)
DATA_DIR = (
    Path("/data/mini-pc")
    if Path("/data/mini-pc").is_dir()
    else Path.home() / "data"
)

FACES_DIR = DATA_DIR / "faces"
CAMERA_DIR = DATA_DIR / "camera"

FACES_DIR.mkdir(parents=True, exist_ok=True)
CAMERA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Call backend API (HMAC signature — key never sent)
# ---------------------------------------------------------------------------
log(f"Syncing from {BACKEND_URL} (device: {DEVICE_ID})")

api_url = f"{BACKEND_URL}/api/v1/devices/{DEVICE_ID}/config"
timestamp = str(int(time.time()))
signature = hmac.new(
    SECRET_KEY.encode(),
    f"{DEVICE_ID}|{timestamp}".encode(),
    hashlib.sha256,
).hexdigest()

req = Request(
    api_url,
    headers={
        "X-Device-ID": DEVICE_ID,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json",
    },
)

try:
    with urlopen(req, timeout=30) as resp:
        if resp.status != 200:
            err(f"API returned HTTP {resp.status}")
            sys.exit(1)
        raw_body = resp.read()
except HTTPError as exc:
    err(f"API returned HTTP {exc.code}")
    sys.exit(1)
except (URLError, OSError) as exc:
    err(f"API request failed: {exc}")
    sys.exit(1)

# Validate JSON
try:
    data: dict = json.loads(raw_body)
except json.JSONDecodeError:
    err("Invalid JSON response")
    sys.exit(1)

log("API response OK")

# ---------------------------------------------------------------------------
# Save previous config for diff
# ---------------------------------------------------------------------------
if CONFIG_FILE.exists():
    shutil.copy2(CONFIG_FILE, CONFIG_PREV)

# ---------------------------------------------------------------------------
# Extract and save data
# ---------------------------------------------------------------------------

# 1. Config (tokens & credentials)
config_data = {
    "token_cloudflare": data.get("token_cloudflare", ""),
    "domain_tunnel": data.get("domain_tunnel", ""),
    "token_stream": data.get("token_stream", ""),
    "time_expire_stream_minute": data.get("time_expire_stream_minute", 30),
    "s3_credential": data.get("s3_credential", {}),
    "sqs_credential": data.get("sqs_credential", {}),
    "_last_synced": datetime.now().isoformat(),
}
CONFIG_FILE.write_text(json.dumps(config_data, indent=2))
log(f"Config saved → {CONFIG_FILE}")

# 2. Face embeddings
face_data = data.get("face_embed", {})
if face_data:
    face_file = FACES_DIR / "embeddings.json"
    face_file.write_text(json.dumps(face_data, indent=2))
    log(f"Face embeddings saved → {face_file}")

# 3. Camera info
camera_data = data.get("infor_camera", {})
if camera_data:
    camera_file = CAMERA_DIR / "infor_camera.json"
    camera_file.write_text(json.dumps(camera_data, indent=2))
    log(f"Camera info saved → {camera_file}")

# ---------------------------------------------------------------------------
# Detect changes & restart services if needed
# ---------------------------------------------------------------------------


def read_json_key(path: Path, key: str) -> str:
    """Safely read a key from a JSON file, return '' on any error."""
    try:
        return json.loads(path.read_text()).get(key, "")
    except Exception:
        return ""


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, suppressing errors by default."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


restart_needed: list[str] = []

if CONFIG_PREV.exists():
    # --- Cloudflare token change ---
    old_cf = read_json_key(CONFIG_PREV, "token_cloudflare")
    new_cf = read_json_key(CONFIG_FILE, "token_cloudflare")

    if old_cf != new_cf and new_cf:
        log("Cloudflare token changed — updating tunnel")
        cf_service = Path("/etc/systemd/system/cloudflared.service")

        if cf_service.exists():
            # Update token in existing service file
            content = cf_service.read_text()
            import re
            content = re.sub(r"--token\s+\S+", f"--token {new_cf}", content)
            cf_service.write_text(content)
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "restart", "cloudflared"])
            log("Cloudflared token updated via service file + restart")
        else:
            # First install
            run(["cloudflared", "service", "install", new_cf])
            run(["systemctl", "restart", "cloudflared"])
            log("Cloudflared tunnel installed (first time)")

        restart_needed.append("cloudflared")

    # --- Stream token change ---
    old_st = read_json_key(CONFIG_PREV, "token_stream")
    new_st = read_json_key(CONFIG_FILE, "token_stream")

    if old_st != new_st:
        log("Stream token changed")
        restart_needed.append("stream_token")

else:
    # First run — setup cloudflare tunnel if token present
    new_cf = read_json_key(CONFIG_FILE, "token_cloudflare")
    if new_cf:
        log("First run — installing cloudflare tunnel")
        run(["cloudflared", "service", "install", new_cf])
        run(["systemctl", "restart", "cloudflared"])
        restart_needed.append("cloudflared (first run)")

if restart_needed:
    log(f"Services affected: {', '.join(restart_needed)}")
else:
    log("No changes detected — all services up to date")

log("Sync complete")
