#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WiFi Access Point Manager
==========================
Module quản lý việc tạo và điều khiển WiFi Access Point trên Jetson
sử dụng NetworkManager (nmcli).

Chức năng chính:
- Tạo WiFi AP với SSID và password được cấu hình
- Tạm dừng và khởi động lại AP (cho WiFi scanning)
- Kiểm tra trạng thái AP
- Cleanup khi shutdown

Tác giả: Jetson AI Kit Team
"""

import subprocess
import logging
import time
from typing import Tuple, Optional, Dict

import config
from virtual_interface_manager import (
    create_virtual_interface,
    delete_virtual_interface,
    virtual_interface_exists,
    VIRTUAL_INTERFACE
)

logger = logging.getLogger(__name__)

# Tên connection trong NetworkManager
AP_CONNECTION_NAME = "Jetson_OOBE_AP"


def get_wifi_interface() -> Optional[str]:
    """
    Tự động phát hiện WiFi interface.

    Returns:
        str: Tên interface WiFi (vd: wlan0, wlP1p1s0), hoặc None nếu không tìm thấy
    """
    try:
        # Lấy danh sách devices từ nmcli
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            logger.error(f"Lỗi khi lấy danh sách devices: {result.stderr}")
            return None

        # Tìm device có TYPE là wifi
        for line in result.stdout.strip().split('\n'):
            if ':' in line:
                device, dev_type = line.split(':', 1)
                if dev_type.lower() == 'wifi':
                    logger.info(f"Phát hiện WiFi interface: {device}")
                    return device

        # Fallback về config nếu không tìm thấy
        logger.warning(f"Không tìm thấy WiFi interface, fallback về {config.WIFI_INTERFACE}")
        return config.WIFI_INTERFACE

    except subprocess.TimeoutExpired:
        logger.error("Timeout khi phát hiện WiFi interface")
        return config.WIFI_INTERFACE
    except Exception as e:
        logger.error(f"Lỗi khi phát hiện WiFi interface: {e}")
        return config.WIFI_INTERFACE


def _delete_existing_connection() -> bool:
    """
    Xóa connection AP hiện có (nếu tồn tại) để tránh xung đột.

    Returns:
        bool: True nếu xóa thành công hoặc không tồn tại, False nếu lỗi
    """
    try:
        # Kiểm tra xem connection đã tồn tại chưa
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME', 'connection', 'show'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if AP_CONNECTION_NAME in result.stdout:
            logger.info(f"Xóa connection cũ: {AP_CONNECTION_NAME}")
            delete_result = subprocess.run(
                ['nmcli', 'connection', 'delete', AP_CONNECTION_NAME],
                capture_output=True,
                text=True,
                timeout=10
            )

            if delete_result.returncode != 0:
                logger.warning(f"Không thể xóa connection cũ: {delete_result.stderr}")
                return False

        return True

    except Exception as e:
        logger.error(f"Lỗi khi xóa connection cũ: {e}")
        return False


def create_access_point(
    ssid: Optional[str] = None,
    password: Optional[str] = None,
    ip_address: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Tạo WiFi Access Point sử dụng nmcli.

    Args:
        ssid: SSID của AP (mặc định từ config.AP_SSID)
        password: Password của AP (mặc định từ config.AP_PASSWORD)
        ip_address: IP address của AP (mặc định từ config.AP_IP_ADDRESS)

    Returns:
        Tuple[bool, str]: (success, message)
    """
    ssid = ssid or config.AP_SSID
    password = password or config.AP_PASSWORD
    ip_address = ip_address or config.AP_IP_ADDRESS

    # Sử dụng WiFi interface từ config
    interface = get_wifi_interface()
    if not interface:
        msg = "Không tìm thấy WiFi interface"
        logger.error(msg)
        return False, msg
    
    logger.info(f"Sử dụng WiFi interface: {interface}")

    if not interface:
        msg = "Không tìm thấy WiFi interface"
        logger.error(msg)
        return False, msg

    logger.info(f"Đang tạo Access Point: SSID={ssid}, IP={ip_address} trên {interface}")

    try:
        # Xóa connection cũ nếu có
        _delete_existing_connection()

        # Tạo connection mới với mode AP
        # nmcli connection add type wifi ifname wlan0 con-name Jetson_OOBE_AP \
        #   autoconnect no ssid Jetson_Setup mode ap
        logger.info(f"Tạo connection AP trên interface {interface}")
        create_cmd = [
            'nmcli', 'connection', 'add',
            'type', 'wifi',
            'ifname', interface,
            'con-name', AP_CONNECTION_NAME,
            'autoconnect', 'no',
            'ssid', ssid,
            'mode', 'ap'
        ]

        result = subprocess.run(
            create_cmd,
            capture_output=True,
            text=True,
            timeout=15
        )

        if result.returncode != 0:
            msg = f"Không thể tạo connection: {result.stderr}"
            logger.error(msg)
            return False, msg

        # Cấu hình security (WPA2-PSK)
        logger.info("Cấu hình security WPA2-PSK")
        security_cmds = [
            ['nmcli', 'connection', 'modify', AP_CONNECTION_NAME, 'wifi-sec.key-mgmt', 'wpa-psk'],
            ['nmcli', 'connection', 'modify', AP_CONNECTION_NAME, 'wifi-sec.psk', password],
        ]

        for cmd in security_cmds:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                msg = f"Lỗi khi cấu hình security: {result.stderr}"
                logger.error(msg)
                return False, msg

        # Cấu hình IP với method 'shared' (bật DHCP server tự động)
        logger.info(f"Cấu hình IP {ip_address} với DHCP shared mode")
        ip_cmds = [
            ['nmcli', 'connection', 'modify', AP_CONNECTION_NAME, 'ipv4.method', 'shared'],
            ['nmcli', 'connection', 'modify', AP_CONNECTION_NAME, 'ipv4.addresses', f'{ip_address}/24'],
        ]

        for cmd in ip_cmds:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                msg = f"Lỗi khi cấu hình IP: {result.stderr}"
                logger.error(msg)
                return False, msg

        # Activate connection
        logger.info("Kích hoạt Access Point")
        activate_result = subprocess.run(
            ['nmcli', 'connection', 'up', AP_CONNECTION_NAME],
            capture_output=True,
            text=True,
            timeout=20
        )

        if activate_result.returncode != 0:
            msg = f"Không thể kích hoạt AP: {activate_result.stderr}"
            logger.error(msg)
            return False, msg

        # Đợi một chút để AP khởi động hoàn toàn
        time.sleep(2)

        msg = f"Access Point đã được tạo thành công: SSID={ssid}, Password={password}, IP={ip_address}"
        logger.info(msg)
        return True, msg

    except subprocess.TimeoutExpired:
        msg = "Timeout khi tạo Access Point"
        logger.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Lỗi không mong muốn: {str(e)}"
        logger.error(msg)
        return False, msg


def stop_access_point() -> Tuple[bool, str]:
    """
    Dừng và xóa Access Point.

    Returns:
        Tuple[bool, str]: (success, message)
    """
    logger.info(f"Đang dừng Access Point: {AP_CONNECTION_NAME}")

    try:
        # Deactivate connection
        down_result = subprocess.run(
            ['nmcli', 'connection', 'down', AP_CONNECTION_NAME],
            capture_output=True,
            text=True,
            timeout=10
        )

        # Không coi lỗi deactivate là nghiêm trọng (có thể đã down rồi)
        if down_result.returncode != 0:
            logger.warning(f"Connection có thể đã down: {down_result.stderr}")

        # Xóa connection
        if not _delete_existing_connection():
            msg = "Lỗi khi xóa connection"
            return False, msg

        msg = "Access Point đã được dừng thành công"
        logger.info(msg)
        return True, msg

    except subprocess.TimeoutExpired:
        msg = "Timeout khi dừng Access Point"
        logger.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Lỗi khi dừng Access Point: {str(e)}"
        logger.error(msg)
        return False, msg


def get_ap_status() -> Dict[str, any]:
    """
    Lấy trạng thái hiện tại của Access Point.

    Returns:
        Dict với các key:
        - is_active (bool): AP có đang chạy không
        - connection_name (str): Tên connection
        - interface (str): WiFi interface đang dùng
        - ssid (str): SSID của AP (nếu active)
        - ip_address (str): IP address (nếu active)
    """
    status = {
        'is_active': False,
        'connection_name': AP_CONNECTION_NAME,
        'interface': None,
        'ssid': None,
        'ip_address': None
    }

    try:
        # Kiểm tra xem connection có active không
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME,DEVICE', 'connection', 'show', '--active'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if ':' in line:
                    name, device = line.split(':', 1)
                    if name == AP_CONNECTION_NAME:
                        status['is_active'] = True
                        status['interface'] = device
                        logger.info(f"AP đang active trên interface {device}")
                        break

        # Nếu active, lấy thêm thông tin chi tiết
        if status['is_active']:
            detail_result = subprocess.run(
                ['nmcli', '-t', '-f', '802-11-wireless.ssid,ipv4.addresses',
                 'connection', 'show', AP_CONNECTION_NAME],
                capture_output=True,
                text=True,
                timeout=5
            )

            if detail_result.returncode == 0:
                for line in detail_result.stdout.strip().split('\n'):
                    if '802-11-wireless.ssid:' in line:
                        status['ssid'] = line.split(':', 1)[1]
                    elif 'ipv4.addresses:' in line:
                        # Format: 192.168.4.1/24
                        addr = line.split(':', 1)[1]
                        if '/' in addr:
                            status['ip_address'] = addr.split('/')[0]

        return status

    except Exception as e:
        logger.error(f"Lỗi khi lấy status AP: {e}")
        return status


def restart_ap_for_scan() -> Tuple[bool, str]:
    """
    Tạm dừng AP, thực hiện WiFi scan, sau đó bật lại AP.
    Function này được gọi khi cần scan networks trong khi AP đang chạy.

    Note: Clients sẽ bị disconnect khoảng 5-10 giây

    Returns:
        Tuple[bool, str]: (success, message)
    """
    logger.info("Chuẩn bị restart AP để scan WiFi networks")

    # Lấy thông tin AP hiện tại để restore sau
    current_status = get_ap_status()

    if not current_status['is_active']:
        msg = "AP không active, không cần restart"
        logger.warning(msg)
        return False, msg

    # Lưu thông tin để restore
    ssid = current_status.get('ssid') or config.AP_SSID
    ip = current_status.get('ip_address') or config.AP_IP_ADDRESS

    # Dừng AP
    success, msg = stop_access_point()
    if not success:
        return False, f"Không thể dừng AP: {msg}"

    logger.info("AP đã dừng, chờ interface sẵn sàng cho scan")
    time.sleep(2)  # Đợi interface ổn định

    # Note: WiFi scan sẽ được thực hiện bởi caller (http_server.py)
    # Function này chỉ lo việc dừng và khởi động lại AP

    return True, "AP đã dừng, sẵn sàng cho scan"


def restore_ap_after_scan(ssid: Optional[str] = None, password: Optional[str] = None,
                          ip_address: Optional[str] = None) -> Tuple[bool, str]:
    """
    Khởi động lại AP sau khi scan xong.

    Args:
        ssid: SSID để restore (mặc định từ config)
        password: Password để restore (mặc định từ config)
        ip_address: IP để restore (mặc định từ config)

    Returns:
        Tuple[bool, str]: (success, message)
    """
    logger.info("Khởi động lại AP sau khi scan WiFi")

    # Đợi một chút để scan hoàn tất
    time.sleep(1)

    # Tạo lại AP
    success, msg = create_access_point(ssid, password, ip_address)

    if success:
        logger.info("AP đã được khởi động lại thành công")
    else:
        logger.error(f"Lỗi khi khởi động lại AP: {msg}")

    return success, msg


# Test functions
if __name__ == "__main__":
    # Setup logging cho testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=== WiFi Access Point Manager Test ===\n")

    # Test 1: Detect WiFi interface
    print("1. Phát hiện WiFi interface:")
    interface = get_wifi_interface()
    print(f"   Interface: {interface}\n")

    # Test 2: Create AP
    print("2. Tạo Access Point:")
    success, msg = create_access_point()
    print(f"   {'✓' if success else '✗'} {msg}\n")

    if success:
        # Test 3: Get status
        print("3. Kiểm tra status:")
        status = get_ap_status()
        print(f"   Active: {status['is_active']}")
        print(f"   SSID: {status['ssid']}")
        print(f"   IP: {status['ip_address']}")
        print(f"   Interface: {status['interface']}\n")

        # Wait a bit
        print("4. Chờ 5 giây...")
        time.sleep(5)

        # Test 4: Stop AP
        print("\n5. Dừng Access Point:")
        success, msg = stop_access_point()
        print(f"   {'✓' if success else '✗'} {msg}\n")

    print("=== Test hoàn tất ===")
