#!/usr/bin/env python3
"""Switch network mode: sudo python3 switch-network.py [auto|4g|lan|wifi]"""

import re
import subprocess
import sys
import time

CONF = "/etc/device/network.conf"
PING_HOST = "8.8.8.8"
MODES = ("auto", "4g", "lan", "wifi")
IFACE_RE = {
    "lan":  r"^(eth|enp|enP|eno|enx|end)",
    "wifi": r"^(wlan|wlp|wlx|wlP)",
    "4g":   r"^(usb|wwan|wwp)",
}


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def route_dev():
    m = re.search(r"dev (\S+)", run(f"ip route get {PING_HOST}"))
    return m.group(1) if m else ""


def matches(dev, mode):
    if not dev or mode == "auto":
        return mode == "auto"
    pat = IFACE_RE.get(mode)
    return bool(pat and re.match(pat, dev))


def update_conf(mode):
    try:
        text = open(CONF).read()
        if re.search(r"^NETWORK_MODE=", text, re.M):
            text = re.sub(r"^NETWORK_MODE=.*", f"NETWORK_MODE={mode}", text, flags=re.M)
        else:
            text += f"\nNETWORK_MODE={mode}\n"
        open(CONF, "w").write(text)
    except FileNotFoundError:
        open(CONF, "w").write(f"NETWORK_MODE={mode}\n")


def reload_watchdog():
    if run("systemctl is-active network-watchdog") == "active":
        subprocess.run("systemctl reload network-watchdog", shell=True)
        return True
    subprocess.run("systemctl start network-watchdog", shell=True)
    for _ in range(20):
        if run("systemctl is-active network-watchdog") == "active":
            subprocess.run("systemctl reload network-watchdog", shell=True)
            return True
        time.sleep(0.3)
    return False


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        print(f"Usage: sudo {sys.argv[0]} [{'/'.join(MODES)}]")
        sys.exit(1)

    mode = sys.argv[1]
    before = route_dev()
    print(f"[switch] Mode → {mode}, current dev → {before}")

    update_conf(mode)

    if not reload_watchdog():
        print("[switch] ERROR: Could not start network-watchdog", file=sys.stderr)
        sys.exit(1)

    if matches(before, mode):
        print(f"[switch] OK: already on {before} for mode {mode}")
        return

    max_wait = 45 if mode == "4g" else 15
    for waited in range(0, max_wait, 2):
        time.sleep(2)
        after = route_dev()
        if matches(after, mode):
            print(f"[switch] OK: {before} → {after}")
            return
        if mode == "auto" and waited >= 6:
            print(f"[switch] OK: auto mode, dev → {after}")
            return

    after = route_dev()
    if after != before:
        print(f"[switch] Route changed: {before} → {after} (target: {mode})")
    elif mode == "4g":
        print(f"[switch] Mode=4g saved. Route stays on {after} until 4G connects.")
    else:
        print(f"[switch] ERROR: route unchanged on {after}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
