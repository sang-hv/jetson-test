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
    INTERNET_CHECK_TIMEOUT
)

# Thiết lập logger
logger = logging.getLogger(__name__)


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


def scan_wifi_networks(interface: str = "wlan0") -> List[dict]:
    """
    Quét và trả về danh sách các mạng WiFi khả dụng.
    
    Args:
        interface: Interface để scan (mặc định wlan0 - physical interface)
    
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
        # ifname: Chỉ định interface để scan
        cmd = [
            "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
            "device", "wifi", "list",
            "ifname", interface,
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
            
            logger.info(f"Tìm thấy {len(networks)} mạng WiFi trên {interface}")
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
            connect_cmd = [
                "nmcli", "device", "wifi", "connect", ssid,
                "password", password,
                "ifname", interface
            ]
            
            result = subprocess.run(
                connect_cmd,
                capture_output=True,
                text=True,
                timeout=WIFI_CONNECT_TIMEOUT
            )
            
            if result.returncode == 0:
                # Kết nối thành công, đợi một chút để ổn định
                time.sleep(2)
                
                # Kiểm tra xem thực sự có internet chưa
                if check_internet_connection():
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
