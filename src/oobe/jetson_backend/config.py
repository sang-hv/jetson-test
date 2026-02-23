#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cấu hình hệ thống cho OOBE BLE WiFi Setup
==========================================
File này chứa tất cả các hằng số và cấu hình cần thiết
cho việc thiết lập WiFi qua Bluetooth Low Energy.

Tác giả: Jetson AI Kit Team

Tương thích:
- Jetson Nano
- Jetson Orin Nano (JetPack 5.1.2+)
- Jetson Xavier series
- Các thiết bị có Bluetooth và WiFi
"""

# =============================================================================
# CẤU HÌNH BLE (Bluetooth Low Energy)
# =============================================================================

# Tên thiết bị BLE - Mobile App sẽ tìm kiếm thiết bị với tên này
BLE_DEVICE_NAME = "Jetson_AI_Kit"

# UUID cho BLE Service chính
# Format: 12345678-1234-5678-1234-56789abcdef0
SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"

# UUID cho các Characteristics
SSID_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef1"     # Nhận tên WiFi
PWD_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef2"      # Nhận mật khẩu WiFi
STATUS_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef3"   # Trạng thái kết nối

# UUID cho WiFi Scan (tính năng mới)
WIFI_SCAN_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef4"   # Write: trigger scan WiFi
WIFI_LIST_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef5"   # Read/Notify: danh sách WiFi (JSON)

# UUID cho PIN Verification (xác thực kết nối)
PIN_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef6"         # Write: gửi PIN code
AUTH_STATUS_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef7"  # Read/Notify: trạng thái xác thực

# UUID cho Network Status Check (kiểm tra trạng thái mạng hiện tại)
NET_CHECK_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef8"   # Write: trigger kiểm tra mạng
NET_STATUS_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef9"  # Read/Notify: trạng thái mạng (JSON)

# Mã PIN cố định cho xác thực kết nối BLE
PIN_CODE = "123456"

# =============================================================================
# TRẠNG THÁI KẾT NỐI WIFI
# =============================================================================

class WiFiStatus:
    """
    Enum cho các trạng thái kết nối WiFi
    Được gửi qua STATUS_CHAR để thông báo cho Mobile App
    """
    WAITING = 0          # Chờ nhập thông tin SSID và Password
    CONNECTING = 1       # Đang thực hiện kết nối WiFi
    SUCCESS = 2          # Kết nối thành công
    ERROR = 3            # Lỗi kết nối (sai mật khẩu, không tìm thấy mạng, etc.)


class WiFiScanStatus:
    """
    Enum cho các trạng thái scan WiFi
    Được gửi kèm với danh sách WiFi qua WIFI_LIST_CHAR
    """
    IDLE = 0             # Chưa scan
    SCANNING = 1         # Đang scan
    COMPLETED = 2        # Scan hoàn tất
    ERROR = 3            # Lỗi khi scan


class AuthStatus:
    """
    Enum cho các trạng thái xác thực PIN.
    Được gửi qua AUTH_STATUS_CHAR để thông báo cho Mobile App.
    """
    UNAUTHENTICATED = 0  # Chưa xác thực, chờ nhập PIN
    AUTHENTICATED = 1    # Xác thực thành công
    INVALID_PIN = 2      # PIN sai


class NetCheckStatus:
    """
    Enum cho trạng thái kiểm tra mạng.
    Được gửi kèm với thông tin mạng qua NET_STATUS_CHAR.
    """
    IDLE = 0             # Chưa kiểm tra
    CHECKING = 1         # Đang kiểm tra
    COMPLETED = 2        # Kiểm tra hoàn tất
    ERROR = 3            # Lỗi khi kiểm tra


class ConnectionType:
    """
    Enum cho loại kết nối mạng.
    """
    NONE = "none"
    WIFI = "wifi"
    ETHERNET = "ethernet"
    CELLULAR = "cellular"


# Cấu hình WiFi Scan
WIFI_SCAN_MAX_NETWORKS = 4  # Số mạng tối đa trả về (giới hạn kích thước BLE packet)

# =============================================================================
# CẤU HÌNH GPIO
# =============================================================================

# GPIO pin cho nút Reset (nhấn giữ để vào chế độ BLE Setup)
# Sử dụng đánh số BCM (Broadcom SOC channel)
#
# Pin mapping (40-pin header):
# - BCM 17 = Physical pin 11
# - Tương thích với: Jetson Nano, Jetson Orin Nano, RPi
#
# Kết nối phần cứng:
# - Một đầu nút nhấn nối với pin 11 (BCM 17)
# - Đầu kia nối với GND (ví dụ: pin 9, 14, 20, 25, 30, 34, 39)
# - Pull-up resistor được cấu hình trong code (không cần điện trở ngoài)
GPIO_RESET_BUTTON = 17

# Thời gian nhấn giữ nút để kích hoạt chế độ Reset (tính bằng giây)
BUTTON_HOLD_TIME = 3.0

# =============================================================================
# CẤU HÌNH WIFI
# =============================================================================

# Timeout khi kết nối WiFi (tính bằng giây)
WIFI_CONNECT_TIMEOUT = 30

# Interface WiFi mặc định
# - Jetson Nano/Orin Nano thường dùng: wlan0
# - Có thể kiểm tra bằng lệnh: ip link show hoặc nmcli device
WIFI_INTERFACE = "wlP1p1s0"

# Số lần thử lại kết nối WiFi nếu thất bại
WIFI_RETRY_COUNT = 3

# =============================================================================
# CẤU HÌNH KIỂM TRA INTERNET
# =============================================================================

# URL để kiểm tra kết nối internet
# Sử dụng Google DNS vì có độ tin cậy cao
INTERNET_CHECK_HOST = "8.8.8.8"
INTERNET_CHECK_PORT = 53
INTERNET_CHECK_TIMEOUT = 3

# =============================================================================
# CẤU HÌNH LOGGING
# =============================================================================

# Mức độ log: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL = "INFO"

# File log (để trống nếu chỉ muốn log ra console)
LOG_FILE = "/var/log/oobe-setup.log"

# =============================================================================
# CẤU HÌNH SYSTEMD
# =============================================================================

# Đường dẫn đến script chính
SCRIPT_PATH = "/opt/oobe-setup/ble_wifi_setup.py"

# User chạy service (cần quyền root để sử dụng Bluetooth và nmcli)
SERVICE_USER = "root"
