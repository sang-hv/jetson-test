#!/usr/bin/env python3
###############################################################################
#  device-update.py - Update device last_seen to backend API
#
#  Runs as cronjob every 5 minutes.
#  API: PATCH {BACKEND_URL}/api/v1/cameras/{DEVICE_ID}/device-update
#
#  Auth headers (HMAC SHA256):
#    - X-Device-ID
#    - X-Timestamp
#    - X-Signature = HMAC(secret_key, "{device_id}|{timestamp}")
#
#  Usage:
#    sudo python3 /opt/device/device-update.py
###############################################################################

import hashlib
import hmac
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LOG_PREFIX = f"[device-update {datetime.now().strftime('%H:%M:%S')}]"
DEVICE_ENV = Path("/etc/device/device.env")


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}")


def err(msg: str) -> None:
    print(f"{LOG_PREFIX} ERROR: {msg}", file=sys.stderr)


def load_env(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE file."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def patch_device_update(device_id: str, backend_url: str, secret_key: str) -> bool:
    """Call device-update endpoint to refresh last_seen."""
    ts = str(int(time.time()))
    sig = hmac.new(
        secret_key.encode(),
        f"{device_id}|{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()

    url = f"{backend_url}/api/v1/cameras/{device_id}/device-update"
    # Use RFC3339/ISO8601 UTC timestamp with `Z` suffix.
    last_seen = (
        datetime.utcnow()
        .replace(microsecond=0)
        .isoformat(timespec="seconds")
        + "Z"
    )
    payload = {"last_seen": last_seen}
    result = subprocess.run(
        [
            "curl",
            "-s",
            "-w",
            "\n%{http_code}",
            "-X",
            "PATCH",
            "-H",
            f"X-Device-ID: {device_id}",
            "-H",
            f"X-Timestamp: {ts}",
            "-H",
            f"X-Signature: {sig}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            "--connect-timeout",
            "10",
            "--max-time",
            "30",
            url,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        err(f"curl failed: {result.stderr}")
        return False

    output_lines = result.stdout.rsplit("\n", 1)
    raw_body = output_lines[0] if len(output_lines) > 1 else result.stdout
    http_code = output_lines[1].strip() if len(output_lines) > 1 else "000"

    if http_code != "200":
        err(f"HTTP {http_code}: {raw_body[:500]}")
        return False

    if raw_body.strip():
        try:
            resp = json.loads(raw_body)
            if isinstance(resp, dict) and not resp.get("success", True):
                err(f"success=false: {resp.get('message', '')}")
                return False
        except json.JSONDecodeError:
            # Accept empty/non-JSON body if HTTP status is successful
            pass

    return True


def main() -> int:
    if not DEVICE_ENV.exists():
        err(f"{DEVICE_ENV} not found")
        return 1

    env = load_env(DEVICE_ENV)
    device_id = env.get("DEVICE_ID", "")
    backend_url = env.get("BACKEND_URL", "")
    secret_key = env.get("SECRET_KEY", "")

    for var_name, var_val in [
        ("DEVICE_ID", device_id),
        ("BACKEND_URL", backend_url),
        ("SECRET_KEY", secret_key),
    ]:
        if not var_val:
            err(f"{var_name} not set in {DEVICE_ENV}")
            return 1

    ok = patch_device_update(device_id, backend_url, secret_key)
    if ok:
        log("last_seen updated")
        return 0

    err("device-update failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
