#!/usr/bin/env python3
###############################################################################
#  sync-config.py - Sync device configuration from backend API
#
#  Runs as cronjob every 5 minutes. Syncs:
#    - Cloudflare tunnel token  → systemd service restart
#    - Face embeddings          → SQLite face_embeddings table
#    - Camera settings          → SQLite camera_settings table
#    - AI rules                 → SQLite ai_rules table
#
#  API: GET {BACKEND_URL}/api/v1/cameras/{DEVICE_ID}/config
#
#  Usage:
#    sudo python3 /opt/device/sync-config.py
###############################################################################

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


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
DB_DIR = DATA_DIR / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "logic_service.db"

# ---------------------------------------------------------------------------
# Call backend API (HMAC signature — key never sent)
# Use curl to bypass Cloudflare TLS fingerprinting (urllib gets blocked)
# ---------------------------------------------------------------------------
log(f"Syncing from {BACKEND_URL} (device: {DEVICE_ID})")

api_url = f"{BACKEND_URL}/api/v1/cameras/{DEVICE_ID}/config"
timestamp = str(int(time.time()))
signature = hmac.new(
    SECRET_KEY.encode(),
    f"{DEVICE_ID}|{timestamp}".encode(),
    hashlib.sha256,
).hexdigest()

# Debug: log request details
log(f"  URL:       GET {api_url}")
log(f"  DEVICE_ID: {DEVICE_ID}")
log(f"  TIMESTAMP: {timestamp}")
log(f"  SECRET_KEY:{SECRET_KEY}")
log(f"  SIGNATURE: {signature}")

result = subprocess.run(
    [
        "curl", "-s", "-w", "\n%{http_code}",
        "-H", f"X-Device-ID: {DEVICE_ID}",
        "-H", f"X-Timestamp: {timestamp}",
        "-H", f"X-Signature: {signature}",
        "-H", "Content-Type: application/json",
        "--connect-timeout", "10",
        "--max-time", "30",
        api_url,
    ],
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    err(f"curl failed: {result.stderr}")
    sys.exit(1)

# Split body and HTTP status code
output_lines = result.stdout.rsplit("\n", 1)
raw_body = output_lines[0] if len(output_lines) > 1 else result.stdout
http_code = output_lines[1].strip() if len(output_lines) > 1 else "000"

log(f"  HTTP: {http_code}")

if http_code != "200":
    err(f"API returned HTTP {http_code}")
    err(f"Response body: {raw_body[:500]}")
    sys.exit(1)

# Validate JSON
try:
    response: dict = json.loads(raw_body)
except json.JSONDecodeError:
    err("Invalid JSON response")
    sys.exit(1)

if not response.get("success"):
    err(f"API returned success=false: {response.get('message', '')}")
    sys.exit(1)

data: dict = response.get("data", {})
log("API response OK")

# ---------------------------------------------------------------------------
# Save previous config for diff (cloudflare token change detection)
# ---------------------------------------------------------------------------
if CONFIG_FILE.exists():
    shutil.copy2(CONFIG_FILE, CONFIG_PREV)

# Save minimal config for change detection
config_data = {
    "cloudflare_tunnel_token": data.get("cloudflare_tunnel_token", ""),
    "_last_synced": datetime.now().isoformat(),
}
CONFIG_FILE.write_text(json.dumps(config_data, indent=2))
log(f"Config saved → {CONFIG_FILE}")

# ---------------------------------------------------------------------------
# SQLite — create tables + save data
# ---------------------------------------------------------------------------
db = sqlite3.connect(str(DB_PATH))
db.execute("PRAGMA journal_mode=WAL;")

# --- Create tables ---
db.execute("""
    CREATE TABLE IF NOT EXISTS face_embeddings (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   TEXT NOT NULL,
        vector    TEXT NOT NULL
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS camera_settings (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        key   TEXT NOT NULL UNIQUE,
        value TEXT NOT NULL
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS ai_rules (
        id              TEXT PRIMARY KEY,
        camera_id       TEXT,
        user_id         TEXT,
        rules_master_id TEXT,
        facility_id     TEXT,
        name            TEXT,
        code            TEXT,
        member_ids      TEXT,
        start_time      TEXT,
        end_time        TEXT,
        weekdays        TEXT,
        is_active       INTEGER DEFAULT 0,
        created_at      TEXT,
        updated_at      TEXT
    )
""")

db.commit()

# --- 1. Face embeddings (full replace) ---
face_data: dict = data.get("face_embeddings", {})
if face_data:
    db.execute("DELETE FROM face_embeddings")
    count = 0
    for user_id, vectors in face_data.items():
        for vector in vectors:
            db.execute(
                "INSERT INTO face_embeddings (user_id, vector) VALUES (?, ?)",
                (user_id, json.dumps(vector)),
            )
            count += 1
    db.commit()
    log(f"Face embeddings saved → {count} vectors for {len(face_data)} users")

# --- 2. Camera settings (upsert) ---
settings_map = {
    "stream_secret_key": data.get("stream_secret_key", ""),
    "stream_view_duration_minutes": str(data.get("stream_view_duration_minutes", "")),
}
# Also extract from information block
info: dict = data.get("information", {})
if info:
    if info.get("bluetooth_password"):
        settings_map["bluetooth_password"] = info["bluetooth_password"]

for key, value in settings_map.items():
    if value:
        db.execute(
            "INSERT OR REPLACE INTO camera_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
db.commit()
log(f"Camera settings saved → {len(settings_map)} keys")

# --- 3. AI rules (full replace) ---
rules: list = data.get("rules", [])
if rules:
    db.execute("DELETE FROM ai_rules")
    for rule in rules:
        member_ids = rule.get("member_ids")
        weekdays = rule.get("weekdays")
        db.execute(
            """INSERT INTO ai_rules
               (id, camera_id, user_id, rules_master_id, facility_id,
                name, code, member_ids, start_time, end_time, weekdays,
                is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rule.get("id"),
                rule.get("camera_id"),
                rule.get("user_id"),
                rule.get("rules_master_id"),
                rule.get("facility_id"),
                rule.get("name"),
                rule.get("code"),
                json.dumps(member_ids) if member_ids is not None else None,
                rule.get("start_time"),
                rule.get("end_time"),
                json.dumps(weekdays) if weekdays is not None else None,
                1 if rule.get("is_active") else 0,
                rule.get("created_at"),
                rule.get("updated_at"),
            ),
        )
    db.commit()
    log(f"AI rules saved → {len(rules)} rules")

db.close()

# ---------------------------------------------------------------------------
# Detect cloudflare token changes & restart service
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
new_cf = data.get("cloudflare_tunnel_token", "")

# if CONFIG_PREV.exists():
#     old_cf = read_json_key(CONFIG_PREV, "cloudflare_tunnel_token")

#     if old_cf != new_cf and new_cf:
#         log("Cloudflare token changed — updating tunnel")
#         cf_service = Path("/etc/systemd/system/cloudflared.service")

#         if cf_service.exists():
#             content = cf_service.read_text()
#             content = re.sub(r"--token\s+\S+", f"--token {new_cf}", content)
#             cf_service.write_text(content)
#             run(["systemctl", "daemon-reload"])
#             run(["systemctl", "restart", "cloudflared"])
#             log("Cloudflared token updated via service file + restart")
#         else:
#             run(["cloudflared", "service", "install", new_cf])
#             run(["systemctl", "restart", "cloudflared"])
#             log("Cloudflared tunnel installed (first time)")

#         restart_needed.append("cloudflared")
# else:
#     # First run — setup cloudflare tunnel if token present
#     if new_cf:
#         log("First run — installing cloudflare tunnel")
#         run(["cloudflared", "service", "install", new_cf])
#         run(["systemctl", "restart", "cloudflared"])
#         restart_needed.append("cloudflared (first run)")

if restart_needed:
    log(f"Services affected: {', '.join(restart_needed)}")
else:
    log("No config changes detected — all services up to date")

log("Sync complete")
