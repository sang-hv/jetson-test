#!/usr/bin/env python3
"""
CLI tool to decode and verify a stream token.

Usage:
    python3 check_token.py <token>
    python3 check_token.py <token> --secret <key>
    python3 check_token.py <token> --device-id <uuid>

If --secret / --device-id are omitted, values are read from the server's
default locations (/data/mini-pc/db/logic_service.db and /etc/device/device.env).
"""

import argparse
import base64
import hashlib
import hmac
import json
import sqlite3
import sys
from datetime import datetime, timezone


# ── colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

ok   = lambda s: f"{GREEN}✓ {s}{RESET}"
fail = lambda s: f"{RED}✗ {s}{RESET}"
warn = lambda s: f"{YELLOW}⚠ {s}{RESET}"
info = lambda s: f"{CYAN}{s}{RESET}"


# ── loaders ───────────────────────────────────────────────────────────────────

def load_device_id(path="/etc/device/device.env") -> str:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEVICE_ID="):
                    return line.split("=", 1)[1].strip()
    except OSError as e:
        print(warn(f"Cannot read {path}: {e}"))
    return ""


def load_secret_key(db_path="/data/mini-pc/db/logic_service.db") -> str:
    try:
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT value FROM camera_settings WHERE key = 'stream_secret_key' LIMIT 1"
        ).fetchone()
        db.close()
        if row:
            return row[0]
    except Exception as e:
        print(warn(f"Cannot read secret key from DB: {e}"))
    return ""


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Decode and verify a stream token")
    parser.add_argument("token", help="base64url token string")
    parser.add_argument("--secret", default=None, help="HMAC secret key (overrides DB lookup)")
    parser.add_argument("--device-id", default=None, help="Expected camera_id / DEVICE_ID (overrides device.env)")
    args = parser.parse_args()

    token = args.token
    secret_key = args.secret or load_secret_key()
    device_id  = args.device_id or load_device_id()

    print()
    print(f"{BOLD}═══ Token Check ═══{RESET}")

    # ── 1. Decode ─────────────────────────────────────────────────────────────
    try:
        padding = 4 - len(token) % 4
        raw = base64.urlsafe_b64decode(token + "=" * (padding % 4))
        token_data = json.loads(raw)
    except Exception as e:
        print(fail(f"Decode failed: {e}"))
        sys.exit(1)

    payload   = token_data.get("payload", {})
    sig_b64   = token_data.get("signature", "")

    print()
    print(f"{BOLD}Payload:{RESET}")
    for k, v in payload.items():
        print(f"  {info(k):30s} {v}")

    print()
    print(f"{BOLD}Signature:{RESET}")
    print(f"  received (from token) : {sig_b64}")

    # ── 2. HMAC verify ────────────────────────────────────────────────────────
    print()
    print(f"{BOLD}Checks:{RESET}")
    print(f"  secret_key used       : {secret_key!r}")

    if not secret_key:
        print(warn("secret_key is empty — skipping HMAC check"))
    else:
        try:
            received_sig = base64.b64decode(sig_b64)
            message = json.dumps(payload, sort_keys=True).encode("utf-8")
            print(f"  message signed        : {message.decode()}")
            expected_sig = hmac.new(
                secret_key.encode("utf-8"),
                message,
                hashlib.sha256,
            ).digest()
            expected_b64 = base64.b64encode(expected_sig).decode()
            print(f"  expected (by device)  : {expected_b64}")
            print(f"  match                 : {'YES' if hmac.compare_digest(received_sig, expected_sig) else 'NO'}")
            if hmac.compare_digest(received_sig, expected_sig):
                print(ok("HMAC signature valid"))
            else:
                print(fail("HMAC signature INVALID"))
        except Exception as e:
            print(fail(f"HMAC check error: {e}"))

    # ── 3. camera_id ──────────────────────────────────────────────────────────
    token_camera_id = payload.get("camera_id", "")
    if not device_id:
        print(warn("DEVICE_ID is empty — skipping camera_id check"))
    elif token_camera_id == device_id:
        print(ok(f"camera_id matches: {token_camera_id}"))
    else:
        print(fail(f"camera_id MISMATCH"))
        print(f"  token    : {token_camera_id}")
        print(f"  device   : {device_id}")

    # ── 4. Expiry ─────────────────────────────────────────────────────────────
    time_exp_str = payload.get("time_exp", "")
    if not time_exp_str:
        print(warn("time_exp missing in payload"))
    else:
        try:
            time_exp = datetime.fromisoformat(time_exp_str)
            if time_exp.tzinfo is None:
                time_exp = time_exp.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            remaining = time_exp - now
            if now <= time_exp:
                mins = int(remaining.total_seconds() // 60)
                secs = int(remaining.total_seconds() % 60)
                print(ok(f"Token valid — expires in {mins}m {secs}s  ({time_exp_str})"))
            else:
                elapsed = now - time_exp
                print(fail(f"Token EXPIRED {int(elapsed.total_seconds())}s ago  ({time_exp_str})"))
        except ValueError as e:
            print(fail(f"time_exp parse error: {e}"))

    print()


if __name__ == "__main__":
    main()
