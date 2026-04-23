#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quản lý kết nối WiFi sử dụng nmcli
===================================
Module này cung cấp các hàm để:
- Kiểm tra trạng thái kết nối internet
- Kết nối WiFi sử dụng SSID và Password
- Quét danh sách các mạng WiFi khả dụng

Tác giả: Jetson AI Kit Team
"""

import os
import re
import subprocess
import socket
import time
import logging
from typing import Tuple, List, Optional

from config import (
    WIFI_INTERFACE,
    WIFI_CONNECT_TIMEOUT,
    WIFI_RETRY_COUNT,
    INTERNET_CHECK_HOST,
    INTERNET_CHECK_PORT,
    INTERNET_CHECK_TIMEOUT,
    LAN_CONNECT_TIMEOUT,
    LTE_CONNECT_TIMEOUT,
)

# Thiết lập logger
logger = logging.getLogger(__name__)

# --- Network watchdog integration (mirror switch-network.py) ---
NETWORK_CONF = "/etc/device/network.conf"

_IFACE_RE = {
    "lan":  r"^(eth|enp|enP|eno|enx|end)",
    "4g":   r"^(usb|wwan|wwp)",
}


def check_internet_connection() -> bool:
    """
    Kiểm tra xem hệ thống có kết nối internet hay không.

    Phương pháp: Thử kết nối TCP đến Google DNS (8.8.8.8:53)

    Returns:
        bool: True nếu có internet, False nếu không

    Example:
        >>> if check_internet_connection():
        ...     print("Đã có internet!")
        ... else:
        ...     print("Chưa có internet, cần thiết lập WiFi")
    """
    try:
        # Tạo socket TCP
        socket_obj = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socket_obj.settimeout(INTERNET_CHECK_TIMEOUT)

        # Thử kết nối đến Google DNS
        result = socket_obj.connect_ex((INTERNET_CHECK_HOST, INTERNET_CHECK_PORT))
        socket_obj.close()

        # Nếu result = 0 nghĩa là kết nối thành công
        if result == 0:
            logger.info("✓ Đã có kết nối internet")
            return True
        else:
            logger.info("✗ Không có kết nối internet")
            return False

    except socket.error as e:
        logger.warning(f"Lỗi khi kiểm tra internet: {e}")
        return False


def check_internet_via_interface(iface: str) -> bool:
    """
    Kiểm tra internet qua đúng interface chỉ định, bỏ qua routing table.

    Dùng SO_BINDTODEVICE để buộc kernel gửi packet qua interface đó,
    tránh trường hợp OS dùng interface khác (WiFi/LAN) khi check.

    Yêu cầu: process cần chạy với quyền root (CAP_NET_RAW).

    Args:
        iface: Tên interface cần kiểm tra, ví dụ "eth0", "wwan0", "wlan0"

    Returns:
        bool: True nếu interface đó có internet thực sự
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode())
        sock.settimeout(INTERNET_CHECK_TIMEOUT)
        result = sock.connect_ex((INTERNET_CHECK_HOST, INTERNET_CHECK_PORT))
        sock.close()
        if result == 0:
            logger.info(f"✓ Interface {iface} có kết nối internet")
            return True
        else:
            logger.info(f"✗ Interface {iface} không có kết nối internet (errno={result})")
            return False
    except PermissionError:
        logger.error(f"SO_BINDTODEVICE yêu cầu quyền root, không thể kiểm tra interface {iface}")
        return False
    except OSError as e:
        logger.warning(f"Lỗi khi kiểm tra internet qua {iface}: {e}")
        return False


_NET_TYPE_TO_NMCLI = {
    "wifi": "wifi",
    "ethernet": "ethernet",
}

# Pattern nhận diện LTE/cellular interface qua USB RNDIS hoặc WWAN
_CELLULAR_DEVICE_PREFIXES = ("usb", "wwan")


def _detect_cellular_via_ip() -> list:
    """
    Fallback: dùng 'ip' command để tìm interface cellular (usb*, wwan*)
    có IPv4 address — dành cho trường hợp nmcli không quản lý device này.

    Returns:
        list: Danh sách dict tương thích format active_connections
    """
    candidates = []
    try:
        lines = subprocess.run(
            ["ip", "-o", "link", "show"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip().split('\n')

        for line in lines:
            m = re.match(r'^\d+:\s+(\S+?)[@:]', line)
            if not m:
                continue
            dev = m.group(1)
            if not any(dev.startswith(p) for p in _CELLULAR_DEVICE_PREFIXES):
                continue

            # Kiểm tra interface có IPv4 address không
            addr_out = subprocess.run(
                ["ip", "-4", "addr", "show", dev],
                capture_output=True, text=True, timeout=5
            ).stdout
            ip_match = re.search(r'inet\s+(\S+)', addr_out)
            if not ip_match:
                continue

            ip_addr = ip_match.group(1).split('/')[0]

            # Bỏ qua link-local address (169.254.x.x) — không có kết nối thật
            if ip_addr.startswith("169.254."):
                logger.debug(f"Bỏ qua {dev}: link-local IP {ip_addr}")
                continue

            candidates.append({
                "device": dev,
                "type": "cellular",
                "connection": f"LTE ({dev})"
            })

    except Exception as e:
        logger.warning(f"Fallback detect cellular via ip failed: {e}")

    if candidates:
        logger.info(f"Phát hiện cellular qua ip command: {[c['device'] for c in candidates]}")
    return candidates


def get_network_status(net_type: str = None) -> dict:
    """
    Kiểm tra trạng thái kết nối mạng hiện tại của hệ thống.

    Phát hiện:
    - Có kết nối internet không (TCP check to 8.8.8.8:53)
    - Loại kết nối: wifi, ethernet, cellular, none
    - Interface đang sử dụng
    - Chi tiết kết nối (SSID nếu WiFi, tên connection nếu khác)

    Args:
        net_type: Lọc theo loại mạng ("wifi", "ethernet", "cellular").
                  None = kiểm tra tất cả, ưu tiên ethernet > wifi > cellular.

    Returns:
        dict: {"connected": bool, "type": str, "interface": str, "details": str}
    """
    result = {
        "connected": False,
        "type": "none",
        "interface": "",
        "details": ""
    }

    try:
        # Dùng nmcli để lấy danh sách thiết bị mạng và trạng thái
        cmd = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if proc.returncode != 0:
            logger.error(f"nmcli device error: {proc.stderr}")
            return result

        # Parse các kết nối đang active
        active_connections = []
        for line in proc.stdout.strip().split('\n'):
            parts = line.split(':')
            if len(parts) >= 4 and parts[2] == "connected":
                active_connections.append({
                    "device": parts[0],
                    "type": parts[1],       # wifi, ethernet, gsm, etc.
                    "connection": parts[3]
                })

        # Lọc theo loại mạng yêu cầu nếu có
        if net_type:
            if net_type == "cellular":
                # LTE/cellular: lọc theo device name (usb*, wwan*) hoặc nmcli type gsm
                active_connections = [
                    c for c in active_connections
                    if c["type"] == "gsm"
                    or any(c["device"].startswith(p) for p in _CELLULAR_DEVICE_PREFIXES)
                ]
                # Fallback: nmcli có thể không quản lý USB RNDIS,
                # dùng ip command để tìm interface cellular có IP
                if not active_connections:
                    active_connections = _detect_cellular_via_ip()
            else:
                nmcli_type = _NET_TYPE_TO_NMCLI.get(net_type)
                if nmcli_type:
                    active_connections = [c for c in active_connections if c["type"] == nmcli_type]

        if not active_connections:
            logger.info(f"Không có kết nối {net_type or 'mạng'} nào đang active")
            return result

        # Map nmcli types sang ConnectionType và sắp xếp theo ưu tiên
        type_priority = {"ethernet": 1, "wifi": 2, "gsm": 3}
        type_map = {"ethernet": "ethernet", "wifi": "wifi", "gsm": "cellular"}

        # Ưu tiên: ethernet > wifi > cellular
        active_connections.sort(
            key=lambda c: type_priority.get(c["type"], 99)
        )

        primary = active_connections[0]
        conn_type = type_map.get(primary["type"], primary["type"])

        # Nếu device là USB/WWAN thì đánh dấu là cellular
        if any(primary["device"].startswith(p) for p in _CELLULAR_DEVICE_PREFIXES):
            conn_type = "cellular"

        result["type"] = conn_type
        result["interface"] = primary["device"]
        result["details"] = primary["connection"]

        # Kiểm tra internet qua đúng interface
        result["connected"] = check_internet_via_interface(primary["device"])

        logger.info(f"Network status: type={result['type']}, "
                     f"interface={result['interface']}, "
                     f"connected={result['connected']}, "
                     f"details={result['details']}")

    except subprocess.TimeoutExpired:
        logger.error("Timeout khi kiểm tra trạng thái mạng")
    except Exception as e:
        logger.error(f"Lỗi khi kiểm tra trạng thái mạng: {e}")

    return result


def is_open_network(ssid: str) -> bool:
    """
    Kiểm tra nhanh xem mạng WiFi có phải open (không mật khẩu) không.

    Dùng kết quả scan cache của nmcli (không rescan) để tránh chậm.

    Args:
        ssid: Tên mạng WiFi cần kiểm tra

    Returns:
        bool: True nếu mạng không có mật khẩu
    """
    try:
        cmd = [
            "nmcli", "-t", "-f", "SSID,SECURITY",
            "device", "wifi", "list"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and parts[0] == ssid:
                    security = parts[1].strip()
                    is_open = security == '' or security.lower() == 'open'
                    logger.info(f"Kiểm tra security '{ssid}': '{security}' → {'open' if is_open else 'protected'}")
                    return is_open
    except Exception as e:
        logger.warning(f"Lỗi khi kiểm tra security của '{ssid}': {e}")
    return False


def scan_wifi_networks() -> List[dict]:
    """
    Quét và trả về danh sách các mạng WiFi khả dụng.
    
    Returns:
        List[dict]: Danh sách các mạng WiFi, mỗi mạng là một dict với các key:
            - ssid: Tên mạng
            - signal: Cường độ tín hiệu (%)
            - security: Loại bảo mật (WPA2, WPA3, Open, etc.)
            
    Example:
        >>> networks = scan_wifi_networks()
        >>> for net in networks:
        ...     print(f"{net['ssid']} - {net['signal']}%")
    """
    networks = []
    
    try:
        # Sử dụng nmcli để quét WiFi
        # -t: Output dạng tabular (dễ parse)
        # -f: Chỉ định các field cần lấy
        cmd = [
            "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
            "device", "wifi", "list",
            "--rescan", "yes"
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line:
                    parts = line.split(':')
                    if len(parts) >= 3 and parts[0]:  # Bỏ qua mạng không có SSID
                        networks.append({
                            'ssid': parts[0],
                            'signal': int(parts[1]) if parts[1].isdigit() else 0,
                            'security': parts[2] if parts[2] else 'Open'
                        })
            
            # Deduplicate: giữ SSID có signal cao nhất
            seen = {}
            for net in networks:
                ssid = net['ssid']
                if ssid not in seen or net['signal'] > seen[ssid]['signal']:
                    seen[ssid] = net
            networks = list(seen.values())

            logger.info(f"Tìm thấy {len(networks)} mạng WiFi (sau dedup)")
        else:
            logger.error(f"Lỗi khi quét WiFi: {result.stderr}")
            
    except subprocess.TimeoutExpired:
        logger.error("Timeout khi quét mạng WiFi")
    except Exception as e:
        logger.error(f"Lỗi không xác định khi quét WiFi: {e}")
    
    return networks


def get_wifi_interface() -> str:
    """
    Tự động phát hiện interface WiFi khả dụng.
    
    Returns:
        str: Tên interface WiFi (ví dụ: wlan0, wlan1), hoặc WIFI_INTERFACE mặc định
    """
    try:
        # Cách 1: Sử dụng nmcli device
        cmd = ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and parts[1] == "wifi":
                    # Ưu tiên interface đang connected hoặc available
                    logger.info(f"Phát hiện WiFi interface: {parts[0]}")
                    return parts[0]
                    
    except Exception as e:
        logger.warning(f"Lỗi khi tự động phát hiện interface: {e}")
        
    # Fallback về cấu hình mặc định (wlan0)
    logger.info(f"Sử dụng WiFi interface mặc định: {WIFI_INTERFACE}")
    return WIFI_INTERFACE


def get_ethernet_interface() -> Optional[str]:
    """
    Tự động phát hiện interface Ethernet (LAN) khả dụng.

    Sử dụng nmcli để liệt kê thiết bị, lọc type == "ethernet".
    Tương thích với cả chuẩn đặt tên Linux (eth*, eno*, enp*) và
    Jetson PCIe (enP*).

    Returns:
        str: Tên interface (ví dụ: 'eth0', 'enP3p1s0'), hoặc None nếu không tìm thấy.
    """
    try:
        cmd = ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and parts[1] == "ethernet":
                    logger.info(f"Phát hiện Ethernet interface: {parts[0]}")
                    return parts[0]
    except Exception as e:
        logger.warning(f"Lỗi khi phát hiện Ethernet interface: {e}")
    return None


# =============================================================================
# NETWORK WATCHDOG HELPERS (mirror switch-network.py)
# =============================================================================

def _update_network_conf(mode: str):
    """Ghi NETWORK_MODE=<mode> vào /etc/device/network.conf."""
    try:
        os.makedirs(os.path.dirname(NETWORK_CONF), exist_ok=True)
        try:
            text = open(NETWORK_CONF).read()
            if re.search(r"^NETWORK_MODE=", text, re.M):
                text = re.sub(r"^NETWORK_MODE=.*", f"NETWORK_MODE={mode}", text, flags=re.M)
            else:
                text += f"\nNETWORK_MODE={mode}\n"
        except FileNotFoundError:
            text = f"NETWORK_MODE={mode}\n"
        open(NETWORK_CONF, "w").write(text)
        logger.info(f"Đã ghi NETWORK_MODE={mode} vào {NETWORK_CONF}")
    except Exception as e:
        logger.error(f"Không thể ghi {NETWORK_CONF}: {e}")


def _reload_watchdog() -> bool:
    """Reload hoặc start network-watchdog service."""
    try:
        active = subprocess.run(
            ["systemctl", "is-active", "network-watchdog"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()

        if active == "active":
            subprocess.run(["systemctl", "reload", "network-watchdog"],
                           capture_output=True, text=True, timeout=5)
            logger.info("Đã reload network-watchdog")
            return True

        subprocess.run(["systemctl", "start", "network-watchdog"],
                       capture_output=True, text=True, timeout=10)
        for _ in range(20):
            st = subprocess.run(
                ["systemctl", "is-active", "network-watchdog"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if st == "active":
                subprocess.run(["systemctl", "reload", "network-watchdog"],
                               capture_output=True, text=True, timeout=5)
                logger.info("Đã start + reload network-watchdog")
                return True
            time.sleep(0.3)

        logger.error("Không thể start network-watchdog")
        return False
    except Exception as e:
        logger.error(f"Lỗi khi reload watchdog: {e}")
        return False


def _route_dev() -> str:
    """Lấy device hiện tại từ default route (ip route get 8.8.8.8)."""
    try:
        out = subprocess.run(
            ["ip", "route", "get", INTERNET_CHECK_HOST],
            capture_output=True, text=True, timeout=5
        ).stdout
        m = re.search(r"dev (\S+)", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _matches_mode(dev: str, mode: str) -> bool:
    """Kiểm tra device name có match với pattern của mode không."""
    if not dev:
        return False
    pat = _IFACE_RE.get(mode)
    return bool(pat and re.match(pat, dev))


def setup_network_connection(net_type: str) -> Tuple[bool, str]:
    """
    Kết nối device vào mạng LAN (Ethernet) hoặc LTE (cellular modem).

    Args:
        net_type: "lan" hoặc "lte"

    Returns:
        Tuple[bool, str]: (thành_công, thông_báo)
    """
    if net_type == "lan":
        return _setup_lan(LAN_CONNECT_TIMEOUT)
    elif net_type == "lte":
        return _setup_lte(LTE_CONNECT_TIMEOUT)
    else:
        return False, f"Loại mạng không hợp lệ: {net_type}"


def _setup_network_via_watchdog(mode: str, timeout: int) -> Tuple[bool, str]:
    """
    Setup mạng bằng cách ghi config và delegate cho network-watchdog.
    Logic mirror từ switch-network.py.

    Args:
        mode: "lan" hoặc "4g" (tên mode trong network.conf)
        timeout: Thời gian chờ tối đa (giây)
    """
    label = "LTE" if mode == "4g" else "LAN"
    before = _route_dev()
    logger.info(f"[{label}] Bắt đầu setup, mode={mode}, current dev={before}")

    _update_network_conf(mode)

    if not _reload_watchdog():
        return False, "Không thể start network-watchdog service"

    # Nếu đã đúng interface → trả về luôn
    if _matches_mode(before, mode):
        logger.info(f"[{label}] Đã đúng interface {before}")
        return True, f"{label} đã kết nối qua {before}"

    # Poll chờ route chuyển sang đúng interface
    for waited in range(0, timeout, 2):
        time.sleep(2)
        after = _route_dev()
        if _matches_mode(after, mode):
            logger.info(f"[{label}] Route chuyển sang {after} sau {waited + 2}s")
            return True, f"{label} đã kết nối qua {after}"

    if mode == "4g":
        return False, f"LTE chưa sẵn sàng sau {timeout}s, mode đã lưu cho watchdog"

    return False, f"{label} interface không tìm thấy sau {timeout}s"


def _setup_lan(timeout: int) -> Tuple[bool, str]:
    """Kết nối LAN qua network-watchdog."""
    return _setup_network_via_watchdog("lan", timeout)


def _setup_lte(timeout: int) -> Tuple[bool, str]:
    """Kết nối LTE qua network-watchdog."""
    return _setup_network_via_watchdog("4g", timeout)


def connect_wifi(ssid: str, password: str) -> Tuple[bool, str]:
    """
    Kết nối đến mạng WiFi với SSID và mật khẩu được cung cấp.
    
    Sử dụng nmcli để:
    1. Xóa connection cũ nếu có (để tránh conflict)
    2. Tạo connection mới với SSID và password
    3. Kích hoạt connection
    
    Args:
        ssid: Tên mạng WiFi cần kết nối
        password: Mật khẩu WiFi
        
    Returns:
        Tuple[bool, str]: (success, message)
            - success: True nếu kết nối thành công
            - message: Thông báo chi tiết
            
    Example:
        >>> success, msg = connect_wifi("MyWiFi", "password123")
        >>> if success:
        ...     print("Kết nối thành công!")
        ... else:
        ...     print(f"Lỗi: {msg}")
    """
    logger.info(f"Bắt đầu kết nối đến mạng WiFi: {ssid}")
    
    # Bước 0: Xác định interface
    interface = get_wifi_interface()
    
    # Bước 1: Xóa connection cũ nếu có (tránh trùng lặp)
    try:
        delete_cmd = ["nmcli", "connection", "delete", ssid]
        subprocess.run(delete_cmd, capture_output=True, timeout=10)
        logger.debug(f"Đã xóa connection cũ: {ssid}")
    except Exception:
        # Không sao nếu không có connection cũ để xóa
        pass
    
    # Bước 2: Thử kết nối với số lần retry được cấu hình
    for attempt in range(WIFI_RETRY_COUNT):
        logger.info(f"Lần thử {attempt + 1}/{WIFI_RETRY_COUNT}")
        
        try:
            # Sử dụng nmcli để kết nối
            # device wifi connect: Lệnh kết nối WiFi
            # password: Mật khẩu
            # ifname: Interface (wlan0)
            if password:
                connect_cmd = [
                    "nmcli", "device", "wifi", "connect", ssid,
                    "password", password,
                    "ifname", interface
                ]
            else:
                connect_cmd = [
                    "nmcli", "device", "wifi", "connect", ssid,
                    "ifname", interface
                ]
            
            result = subprocess.run(
                connect_cmd,
                capture_output=True,
                text=True,
                timeout=WIFI_CONNECT_TIMEOUT
            )
            
            if result.returncode == 0:
                # nmcli trả về 0 nhưng WPA handshake có thể chưa hoàn tất.
                # Chờ rồi xác minh WiFi thực sự đã kết nối đúng SSID.
                time.sleep(3)

                # Kiểm tra xem WiFi có thực sự connected với đúng SSID không
                current_ssid = get_current_connection()
                if current_ssid != ssid:
                    logger.warning(
                        f"nmcli trả về OK nhưng WiFi không connected đúng SSID "
                        f"(expected={ssid}, actual={current_ssid})"
                    )
                    # Xóa connection profile bị lỗi
                    try:
                        subprocess.run(
                            ["nmcli", "connection", "delete", ssid],
                            capture_output=True, timeout=10
                        )
                    except Exception:
                        pass
                    return False, "Sai mật khẩu WiFi"

                # WiFi đã connected, kiểm tra internet qua đúng WiFi interface
                if check_internet_via_interface(interface):
                    logger.info(f"✓ Kết nối thành công đến {ssid}")
                    return True, f"Kết nối thành công đến {ssid}"
                else:
                    logger.warning("Kết nối WiFi OK nhưng chưa có internet")
                    # Vẫn coi là thành công vì có thể mạng nội bộ
                    return True, f"Kết nối đến {ssid} (không có internet)"
            else:
                error_msg = result.stderr.strip()
                logger.warning(f"Lỗi kết nối: {error_msg}")
                
                # Phân tích lỗi để trả về thông báo phù hợp
                if "Secrets were required" in error_msg or "password" in error_msg.lower():
                    return False, "Sai mật khẩu WiFi"
                elif "No network with SSID" in error_msg:
                    return False, f"Không tìm thấy mạng {ssid}"
                    
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout khi kết nối (lần thử {attempt + 1})")
        except Exception as e:
            logger.error(f"Lỗi không xác định: {e}")
        
        # Đợi trước khi thử lại
        if attempt < WIFI_RETRY_COUNT - 1:
            time.sleep(2)
    
    return False, f"Không thể kết nối đến {ssid} sau {WIFI_RETRY_COUNT} lần thử"


def get_current_connection() -> Optional[str]:
    """
    Lấy tên mạng WiFi hiện đang kết nối.
    
    Returns:
        Optional[str]: Tên SSID đang kết nối, hoặc None nếu không kết nối
        
    Example:
        >>> ssid = get_current_connection()
        >>> if ssid:
        ...     print(f"Đang kết nối: {ssid}")
    """
    try:
        cmd = [
            "nmcli", "-t", "-f", "ACTIVE,SSID",
            "device", "wifi"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) >= 2 and parts[0] == "yes":
                    return parts[1]
                    
    except Exception as e:
        logger.error(f"Lỗi khi lấy thông tin kết nối: {e}")
    
    return None


def disconnect_wifi() -> bool:
    """
    Ngắt kết nối WiFi hiện tại.
    
    Returns:
        bool: True nếu ngắt thành công
    """
    interface = get_wifi_interface()
    try:
        cmd = ["nmcli", "device", "disconnect", interface]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            logger.info("Đã ngắt kết nối WiFi")
            return True
            
    except Exception as e:
        logger.error(f"Lỗi khi ngắt kết nối: {e}")
    
    return False


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    # Thiết lập logging cho testing
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 50)
    print("WiFi Manager - Test Mode")
    print("=" * 50)
    
    # Test 1: Kiểm tra internet
    print("\n[Test 1] Kiểm tra kết nối internet...")
    has_internet = check_internet_connection()
    print(f"Kết quả: {'Có internet' if has_internet else 'Không có internet'}")
    
    # Test 2: Lấy kết nối hiện tại
    print("\n[Test 2] Lấy thông tin kết nối hiện tại...")
    current_ssid = get_current_connection()
    print(f"SSID hiện tại: {current_ssid or 'Không kết nối'}")
    
    # Test 3: Quét mạng WiFi
    print("\n[Test 3] Quét mạng WiFi...")
    networks = scan_wifi_networks()
    for net in networks[:5]:  # Chỉ hiển thị 5 mạng đầu
        print(f"  - {net['ssid']}: {net['signal']}% ({net['security']})")
    
    print("\n" + "=" * 50)
    print("Hoàn thành test!")
