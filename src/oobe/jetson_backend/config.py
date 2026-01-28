#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cấu hình hệ thống cho OOBE BLE WiFi Setup
==========================================
File này chứa tất cả các hằng số và cấu hình cần thiết
cho việc thiết lập WiFi qua Bluetooth Low Energy.

Tác giả: Jetson AI Kit Team
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

# =============================================================================
# CẤU HÌNH GPIO
# =============================================================================

# GPIO pin cho nút Reset (nhấn giữ để vào chế độ BLE Setup)
# Sử dụng đánh số BCM (Broadcom SOC channel)
GPIO_RESET_BUTTON = 17

# Thời gian nhấn giữ nút để kích hoạt chế độ Reset (tính bằng giây)
BUTTON_HOLD_TIME = 3.0

# =============================================================================
# CẤU HÌNH WIFI
# =============================================================================

# Timeout khi kết nối WiFi (tính bằng giây)
WIFI_CONNECT_TIMEOUT = 30

# Interface WiFi mặc định trên Jetson Nano
WIFI_INTERFACE = "wlan0"

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
