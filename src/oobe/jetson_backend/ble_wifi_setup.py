#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BLE WiFi Setup - Script chính cho OOBE
=======================================
Script này tạo một BLE Peripheral để nhận thông tin WiFi từ Mobile App
và tự động kết nối WiFi cho Jetson Nano.

Tác giả: Jetson AI Kit Team

Luồng hoạt động:
1. Khi khởi động, kiểm tra kết nối internet
2. Nếu không có internet HOẶC nút Reset được nhấn giữ:
   - Bật BLE Peripheral với tên "Jetson_AI_Kit"
   - Chờ nhận SSID và Password từ Mobile App
   - Kết nối WiFi và thông báo kết quả
3. Nếu đã có internet: Thoát script

Yêu cầu:
- BlueZ (Bluetooth stack cho Linux)
- D-Bus (IPC cho BlueZ)
- Python packages: dbus-python, pygobject, bluezero
"""

import sys
import os
import time
import signal
import logging
import threading
import json
from typing import Optional, List

# Thêm thư mục hiện tại vào path để import được các module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import cấu hình
from config import (
    BLE_DEVICE_NAME,
    SERVICE_UUID,
    SSID_CHAR_UUID,
    PWD_CHAR_UUID,
    STATUS_CHAR_UUID,
    WIFI_SCAN_CHAR_UUID,
    WIFI_LIST_CHAR_UUID,
    WiFiStatus,
    WiFiScanStatus,
    WIFI_SCAN_MAX_NETWORKS,
    LOG_LEVEL,
    LOG_FILE
)

# Import các module khác
from wifi_manager import check_internet_connection, connect_wifi, scan_wifi_networks
from gpio_handler import get_gpio_handler

# Thử import thư viện BLE
try:
    from gi.repository import GLib
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False
    GLib = None
    dbus = None

# =============================================================================
# THIẾT LẬP LOGGING
# =============================================================================

def setup_logging():
    """Thiết lập logging cho toàn bộ ứng dụng."""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    handlers = [logging.StreamHandler()]
    
    # Thêm file handler nếu được cấu hình
    if LOG_FILE:
        try:
            # Tạo thư mục nếu chưa có
            log_dir = os.path.dirname(LOG_FILE)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            handlers.append(logging.FileHandler(LOG_FILE))
        except Exception as e:
            print(f"Không thể tạo file log: {e}")
    
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format=log_format,
        handlers=handlers
    )

setup_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# BLUEZ DBUS CONSTANTS
# =============================================================================

BLUEZ_SERVICE_NAME = "org.bluez"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"

# =============================================================================
# BLE ADVERTISEMENT CLASS
# =============================================================================

if DBUS_AVAILABLE:
    class Advertisement(dbus.service.Object):
        """
        Class quảng cáo BLE (LE Advertisement).
        
        Định nghĩa thông tin quảng cáo mà thiết bị BLE sẽ broadcast,
        cho phép các thiết bị khác tìm thấy Jetson Nano.
        """
        PATH_BASE = "/org/bluez/oobe/advertisement"

        def __init__(self, bus, index, advertising_type):
            self.path = f"{self.PATH_BASE}{index}"
            self.bus = bus
            self.ad_type = advertising_type
            self.service_uuids = None
            self.manufacturer_data = None
            self.solicit_uuids = None
            self.service_data = None
            self.local_name = None
            self.include_tx_power = False
            dbus.service.Object.__init__(self, bus, self.path)

        def get_properties(self):
            """Trả về các thuộc tính của advertisement."""
            properties = dict()
            properties["Type"] = self.ad_type
            
            if self.service_uuids:
                properties["ServiceUUIDs"] = dbus.Array(self.service_uuids, signature='s')
            if self.solicit_uuids:
                properties["SolicitUUIDs"] = dbus.Array(self.solicit_uuids, signature='s')
            if self.manufacturer_data:
                properties["ManufacturerData"] = dbus.Dictionary(
                    self.manufacturer_data, signature='qv'
                )
            if self.service_data:
                properties["ServiceData"] = dbus.Dictionary(
                    self.service_data, signature='sv'
                )
            if self.local_name:
                properties["LocalName"] = dbus.String(self.local_name)
            if self.include_tx_power:
                properties["IncludeTxPower"] = dbus.Boolean(self.include_tx_power)
                
            return {LE_ADVERTISEMENT_IFACE: properties}

        def get_path(self):
            return dbus.ObjectPath(self.path)

        @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            if interface != LE_ADVERTISEMENT_IFACE:
                raise dbus.exceptions.DBusException(
                    "org.freedesktop.DBus.Error.InvalidArgs",
                    f"Unknown interface: {interface}"
                )
            return self.get_properties()[LE_ADVERTISEMENT_IFACE]

        @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature='', out_signature='')
        def Release(self):
            logger.info(f"Advertisement {self.path} released")


    class OOBEAdvertisement(Advertisement):
        """
        Advertisement cụ thể cho OOBE WiFi Setup.
        
        Quảng cáo với:
        - Tên: Jetson_AI_Kit
        - Service UUID: WiFi Setup Service
        """
        
        def __init__(self, bus, index):
            Advertisement.__init__(self, bus, index, "peripheral")
            
            # GIẢI PHÁP CHO LỖI "Failed to register advertisement":
            # Gói tin quảng cáo legacy bị giới hạn 31 bytes.
            # - Flags: 3 bytes
            # - Service UUID (128-bit): 18 bytes
            # - TX Power: 3 bytes
            # - Name: Còn lại rất ít (khoảng 7 bytes)
            #
            # Nếu tên > 7 ký tự + UUID 128-bit -> Tràn gói tin -> Lỗi.
            # Fix:
            # 1. Tắt TX Power (tiết kiệm 3 bytes)
            # 2. Nếu tên quá dài, dùng tên ngắn hơn cho quảng cáo (Scan Response sẽ chứa tên đầy đủ nếu hỗ trợ)
            
            self.include_tx_power = False  # Tắt để tiết kiệm space
            self.service_uuids = [SERVICE_UUID]
            
            # Tính toán độ dài khả dụng
            # Nếu dùng 128-bit UUID, ta còn khoảng: 31 - 3 (Flags) - 18 (UUID) = 10 bytes cho tên (bao gồm header)
            # Header tên tốn 2 bytes -> Tên tối đa ~8 ký tự.
            
            if len(BLE_DEVICE_NAME) > 8:
                # Dùng tên ngắn cho quảng cáo để đảm bảo packet hợp lệ
                # Mobile App vẫn sẽ thấy tên đầy đủ khi kết nối hoặc scan response
                self.local_name = BLE_DEVICE_NAME[:8]
                logger.info(f"Tên thiết bị quá dài, dùng tên rút gọn cho quảng cáo: {self.local_name}")
            else:
                self.local_name = BLE_DEVICE_NAME
                
            logger.info(f"Tạo advertisement với tên: {self.local_name}")

    # =============================================================================
    # GATT APPLICATION CLASSES
    # =============================================================================

    class Application(dbus.service.Object):
        """
        GATT Application - Container cho các Service.
        
        BlueZ yêu cầu một Application object để đăng ký các GATT Service.
        """
        
        def __init__(self, bus):
            self.path = "/org/bluez/oobe"
            self.services = []
            dbus.service.Object.__init__(self, bus, self.path)

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def add_service(self, service):
            self.services.append(service)

        @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
        def GetManagedObjects(self):
            """Trả về tất cả các objects được quản lý (Services, Characteristics)."""
            response = {}
            
            for service in self.services:
                response[service.get_path()] = service.get_properties()
                chrcs = service.get_characteristics()
                for chrc in chrcs:
                    response[chrc.get_path()] = chrc.get_properties()
                    
            return response


    class Service(dbus.service.Object):
        """
        GATT Service base class.
        
        Mỗi Service chứa các Characteristics và có một UUID duy nhất.
        """
        PATH_BASE = "/org/bluez/oobe/service"

        def __init__(self, bus, index, uuid, primary):
            self.path = f"{self.PATH_BASE}{index}"
            self.bus = bus
            self.uuid = uuid
            self.primary = primary
            self.characteristics = []
            dbus.service.Object.__init__(self, bus, self.path)

        def get_properties(self):
            return {
                GATT_SERVICE_IFACE: {
                    'UUID': self.uuid,
                    'Primary': self.primary,
                    'Characteristics': dbus.Array(
                        self.get_characteristic_paths(),
                        signature='o'
                    )
                }
            }

        def get_path(self):
            return dbus.ObjectPath(self.path)

        def add_characteristic(self, characteristic):
            self.characteristics.append(characteristic)

        def get_characteristic_paths(self):
            return [chrc.get_path() for chrc in self.characteristics]

        def get_characteristics(self):
            return self.characteristics

        @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            if interface != GATT_SERVICE_IFACE:
                raise dbus.exceptions.DBusException(
                    "org.freedesktop.DBus.Error.InvalidArgs",
                    f"Unknown interface: {interface}"
                )
            return self.get_properties()[GATT_SERVICE_IFACE]


    class Characteristic(dbus.service.Object):
        """
        GATT Characteristic base class.
        
        Mỗi Characteristic có:
        - UUID duy nhất
        - Các flags (read, write, notify, etc.)
        - Value (dữ liệu)
        """
        PATH_BASE = "/org/bluez/oobe/characteristic"

        def __init__(self, bus, index, uuid, flags, service):
            self.path = f"{service.get_path()}/char{index}"
            self.bus = bus
            self.uuid = uuid
            self.service = service
            self.flags = flags
            self.value = []
            self.notifying = False
            dbus.service.Object.__init__(self, bus, self.path)

        def get_properties(self):
            return {
                GATT_CHRC_IFACE: {
                    'Service': self.service.get_path(),
                    'UUID': self.uuid,
                    'Flags': self.flags,
                    'Value': dbus.Array(self.value, signature='y'),
                }
            }

        def get_path(self):
            return dbus.ObjectPath(self.path)

        @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            if interface != GATT_CHRC_IFACE:
                raise dbus.exceptions.DBusException(
                    "org.freedesktop.DBus.Error.InvalidArgs",
                    f"Unknown interface: {interface}"
                )
            return self.get_properties()[GATT_CHRC_IFACE]

        @dbus.service.method(GATT_CHRC_IFACE, in_signature='a{sv}', out_signature='ay')
        def ReadValue(self, options):
            logger.debug(f"Đọc characteristic: {self.uuid}")
            return self.value

        @dbus.service.method(GATT_CHRC_IFACE, in_signature='aya{sv}')
        def WriteValue(self, value, options):
            logger.debug(f"Ghi characteristic: {self.uuid}")
            self.value = value

        @dbus.service.method(GATT_CHRC_IFACE)
        def StartNotify(self):
            if self.notifying:
                return
            self.notifying = True
            logger.debug(f"Bắt đầu notify: {self.uuid}")

        @dbus.service.method(GATT_CHRC_IFACE)
        def StopNotify(self):
            if not self.notifying:
                return
            self.notifying = False
            logger.debug(f"Dừng notify: {self.uuid}")

        @dbus.service.signal(DBUS_PROP_IFACE, signature='sa{sv}as')
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        def notify_value(self, value):
            """Gửi notification với giá trị mới."""
            if not self.notifying:
                return
            self.value = value
            self.PropertiesChanged(
                GATT_CHRC_IFACE,
                {'Value': dbus.Array(value, signature='y')},
                []
            )

    # =============================================================================
    # OOBE SPECIFIC CHARACTERISTICS
    # =============================================================================

    class SSIDCharacteristic(Characteristic):
        """
        Characteristic để nhận SSID từ Mobile App.
        
        Flags: write, write-without-response
        """
        
        def __init__(self, bus, index, service, wifi_setup_handler):
            Characteristic.__init__(
                self, bus, index, SSID_CHAR_UUID,
                ['write', 'write-without-response'],
                service
            )
            self.wifi_setup_handler = wifi_setup_handler
            logger.info(f"Tạo SSID Characteristic: {SSID_CHAR_UUID}")

        def WriteValue(self, value, options):
            # Chuyển bytes thành string
            ssid = bytes(value).decode('utf-8')
            logger.info(f"Nhận SSID: {ssid}")
            self.wifi_setup_handler.set_ssid(ssid)


    class PasswordCharacteristic(Characteristic):
        """
        Characteristic để nhận Password từ Mobile App.
        
        Flags: write, write-without-response
        """
        
        def __init__(self, bus, index, service, wifi_setup_handler):
            Characteristic.__init__(
                self, bus, index, PWD_CHAR_UUID,
                ['write', 'write-without-response'],
                service
            )
            self.wifi_setup_handler = wifi_setup_handler
            logger.info(f"Tạo Password Characteristic: {PWD_CHAR_UUID}")

        def WriteValue(self, value, options):
            # Chuyển bytes thành string
            password = bytes(value).decode('utf-8')
            logger.info(f"Nhận Password: {'*' * len(password)}")  # Ẩn mật khẩu trong log
            self.wifi_setup_handler.set_password(password)


    class StatusCharacteristic(Characteristic):
        """
        Characteristic để thông báo trạng thái kết nối.
        
        Flags: read, notify
        
        Giá trị:
        - 0: Chờ nhập thông tin
        - 1: Đang kết nối
        - 2: Thành công
        - 3: Lỗi
        """
        
        def __init__(self, bus, index, service):
            Characteristic.__init__(
                self, bus, index, STATUS_CHAR_UUID,
                ['read', 'notify'],
                service
            )
            self.value = [WiFiStatus.WAITING]
            logger.info(f"Tạo Status Characteristic: {STATUS_CHAR_UUID}")

        def set_status(self, status: int):
            """
            Cập nhật trạng thái và gửi notification.
            
            Args:
                status: Một trong các giá trị WiFiStatus
            """
            logger.info(f"Cập nhật trạng thái: {status}")
            self.value = [status]
            self.notify_value(self.value)


    class WiFiScanCharacteristic(Characteristic):
        """
        Characteristic để nhận lệnh scan WiFi từ Mobile App.
        
        Flags: write, write-without-response
        
        Giá trị ghi:
        - 1: Bắt đầu scan WiFi
        - 0: Hủy scan (nếu đang scan)
        """
        
        def __init__(self, bus, index, service, wifi_scan_handler):
            Characteristic.__init__(
                self, bus, index, WIFI_SCAN_CHAR_UUID,
                ['write', 'write-without-response'],
                service
            )
            self.wifi_scan_handler = wifi_scan_handler
            logger.info(f"Tạo WiFi Scan Characteristic: {WIFI_SCAN_CHAR_UUID}")

        def WriteValue(self, value, options):
            # Đọc command từ app
            if len(value) > 0:
                command = value[0]
                logger.info(f"Nhận lệnh scan WiFi: {command}")
                
                if command == 1:
                    # Bắt đầu scan
                    self.wifi_scan_handler.start_scan()
                elif command == 0:
                    # Hủy scan
                    self.wifi_scan_handler.cancel_scan()


    class WiFiListCharacteristic(Characteristic):
        """
        Characteristic để gửi danh sách WiFi về Mobile App.
        
        Flags: read, notify
        
        Giá trị:
        - JSON string chứa danh sách WiFi networks
        - Format: {"status": 0-3, "networks": [{"ssid": "...", "signal": 85, "security": "WPA2"}, ...]}
        """
        
        def __init__(self, bus, index, service):
            Characteristic.__init__(
                self, bus, index, WIFI_LIST_CHAR_UUID,
                ['read', 'notify'],
                service
            )
            # Khởi tạo với trạng thái idle
            self._set_initial_value()
            logger.info(f"Tạo WiFi List Characteristic: {WIFI_LIST_CHAR_UUID}")
        
        def _set_initial_value(self):
            """Set giá trị khởi tạo."""
            initial_data = {
                "status": WiFiScanStatus.IDLE,
                "networks": []
            }
            self.value = list(json.dumps(initial_data).encode('utf-8'))

        def set_scan_status(self, status: int, networks: List[dict] = None):
            """
            Cập nhật trạng thái scan và danh sách mạng WiFi.
            
            Args:
                status: Một trong các giá trị WiFiScanStatus
                networks: Danh sách mạng WiFi (optional)
            """
            data = {
                "status": status,
                "networks": networks or []
            }
            json_str = json.dumps(data, ensure_ascii=False)
            logger.info(f"Cập nhật WiFi list: status={status}, networks={len(networks or [])}")
            
            self.value = list(json_str.encode('utf-8'))
            self.notify_value(self.value)
        
        def ReadValue(self, options):
            """Đọc giá trị hiện tại."""
            logger.debug(f"Đọc WiFi list characteristic")
            return dbus.Array(self.value, signature='y')


    class WiFiSetupService(Service):
        """
        GATT Service chính cho WiFi Setup.
        
        Chứa 5 characteristics:
        - SSID (write): Nhận tên WiFi từ app
        - Password (write): Nhận mật khẩu WiFi từ app
        - Status (read, notify): Trạng thái kết nối WiFi
        - WiFi Scan (write): Trigger scan WiFi
        - WiFi List (read, notify): Danh sách mạng WiFi
        """
        
        def __init__(self, bus, index, wifi_setup_handler, wifi_scan_handler):
            Service.__init__(self, bus, index, SERVICE_UUID, True)
            
            # Tạo các characteristics cũ
            self.ssid_chrc = SSIDCharacteristic(bus, 0, self, wifi_setup_handler)
            self.pwd_chrc = PasswordCharacteristic(bus, 1, self, wifi_setup_handler)
            self.status_chrc = StatusCharacteristic(bus, 2, self)
            
            # Tạo các characteristics mới cho WiFi Scan
            self.wifi_scan_chrc = WiFiScanCharacteristic(bus, 3, self, wifi_scan_handler)
            self.wifi_list_chrc = WiFiListCharacteristic(bus, 4, self)
            
            # Thêm tất cả vào service
            self.add_characteristic(self.ssid_chrc)
            self.add_characteristic(self.pwd_chrc)
            self.add_characteristic(self.status_chrc)
            self.add_characteristic(self.wifi_scan_chrc)
            self.add_characteristic(self.wifi_list_chrc)
            
            # Lưu reference để cập nhật status
            wifi_setup_handler.status_chrc = self.status_chrc
            wifi_scan_handler.wifi_list_chrc = self.wifi_list_chrc
            
            logger.info(f"Tạo WiFi Setup Service: {SERVICE_UUID}")


# =============================================================================
# WIFI SETUP HANDLER
# =============================================================================

class WiFiSetupHandler:
    """
    Handler xử lý logic kết nối WiFi.
    
    Nhận SSID và Password từ BLE, sau đó thực hiện kết nối WiFi
    và cập nhật trạng thái qua Status Characteristic.
    """
    
    def __init__(self, mainloop=None):
        self.ssid: Optional[str] = None
        self.password: Optional[str] = None
        self.status_chrc = None  # Sẽ được set bởi WiFiSetupService
        self.mainloop = mainloop
        self._connect_thread: Optional[threading.Thread] = None
        
    def set_ssid(self, ssid: str):
        """Nhận SSID từ BLE."""
        self.ssid = ssid
        self._try_connect()
        
    def set_password(self, password: str):
        """Nhận Password từ BLE."""
        self.password = password
        self._try_connect()
        
    def _try_connect(self):
        """
        Thử kết nối nếu đã có đủ SSID và Password.
        
        Chạy trong thread riêng để không block BLE.
        """
        if self.ssid and self.password:
            # Tránh chạy nhiều thread cùng lúc
            if self._connect_thread and self._connect_thread.is_alive():
                logger.warning("Đang có kết nối WiFi đang chạy, bỏ qua yêu cầu mới")
                return
                
            self._connect_thread = threading.Thread(
                target=self._perform_connection,
                daemon=True
            )
            self._connect_thread.start()
    
    def _perform_connection(self):
        """
        Thực hiện kết nối WiFi.
        
        Được chạy trong thread riêng.
        """
        logger.info(f"Bắt đầu kết nối WiFi: {self.ssid}")
        
        # Cập nhật trạng thái: Đang kết nối
        if self.status_chrc:
            GLib.idle_add(
                lambda: self.status_chrc.set_status(WiFiStatus.CONNECTING)
            )
        
        # Thực hiện kết nối
        success, message = connect_wifi(self.ssid, self.password)
        
        # Cập nhật trạng thái dựa trên kết quả
        if success:
            logger.info(f"Kết nối thành công: {message}")
            if self.status_chrc:
                GLib.idle_add(
                    lambda: self.status_chrc.set_status(WiFiStatus.SUCCESS)
                )
            
            # Đợi một chút rồi thoát
            time.sleep(3)
            logger.info("Kết nối WiFi thành công, đang thoát BLE setup...")
            if self.mainloop:
                GLib.idle_add(self.mainloop.quit)
        else:
            logger.error(f"Kết nối thất bại: {message}")
            if self.status_chrc:
                GLib.idle_add(
                    lambda: self.status_chrc.set_status(WiFiStatus.ERROR)
                )
            
            # Reset để người dùng có thể thử lại
            self.ssid = None
            self.password = None
            
            # Sau 5 giây, reset về trạng thái chờ
            time.sleep(5)
            if self.status_chrc:
                GLib.idle_add(
                    lambda: self.status_chrc.set_status(WiFiStatus.WAITING)
                )


# =============================================================================
# WIFI SCAN HANDLER
# =============================================================================

class WiFiScanHandler:
    """
    Handler xử lý việc scan WiFi.
    
    Nhận lệnh scan từ BLE, thực hiện quét mạng WiFi
    và gửi kết quả về qua WiFi List Characteristic.
    """
    
    def __init__(self):
        self.wifi_list_chrc = None  # Sẽ được set bởi WiFiSetupService
        self._scan_thread: Optional[threading.Thread] = None
        self._scanning = False
        
    def start_scan(self):
        """Bắt đầu scan WiFi."""
        if self._scanning:
            logger.warning("Đang scan WiFi, bỏ qua yêu cầu mới")
            return
        
        self._scanning = True
        self._scan_thread = threading.Thread(
            target=self._perform_scan,
            daemon=True
        )
        self._scan_thread.start()
    
    def cancel_scan(self):
        """Hủy scan (nếu có thể)."""
        self._scanning = False
        logger.info("Đã yêu cầu hủy scan WiFi")
    
    def _perform_scan(self):
        """
        Thực hiện scan WiFi.
        
        Được chạy trong thread riêng để không block BLE.
        """
        logger.info("Bắt đầu scan WiFi...")
        
        # Thông báo đang scan
        if self.wifi_list_chrc:
            GLib.idle_add(
                lambda: self.wifi_list_chrc.set_scan_status(WiFiScanStatus.SCANNING, [])
            )
        
        try:
            # Thực hiện scan
            networks = scan_wifi_networks()
            
            # Kiểm tra xem có bị hủy không
            if not self._scanning:
                logger.info("Scan WiFi đã bị hủy")
                return
            
            # Lọc chỉ lấy mạng bắt đầu bằng "DEHA"
            networks = [n for n in networks if n.get('ssid', '').startswith('DEHA')]
            
            # Sắp xếp theo signal strength (mạnh nhất trước)
            networks = sorted(networks, key=lambda x: x.get('signal', 0), reverse=True)
            
            # Giới hạn số mạng trả về
            networks = networks[:WIFI_SCAN_MAX_NETWORKS]
            
            logger.info(f"Scan hoàn tất: tìm thấy {len(networks)} mạng WiFi (chỉ SSID bắt đầu bằng 'DEHA')")
            
            # Gửi kết quả về app
            if self.wifi_list_chrc:
                GLib.idle_add(
                    lambda: self.wifi_list_chrc.set_scan_status(WiFiScanStatus.COMPLETED, networks)
                )
                
        except Exception as e:
            logger.error(f"Lỗi khi scan WiFi: {e}")
            if self.wifi_list_chrc:
                GLib.idle_add(
                    lambda: self.wifi_list_chrc.set_scan_status(WiFiScanStatus.ERROR, [])
                )
        finally:
            self._scanning = False


# =============================================================================
# BLE SERVER
# =============================================================================

class BLEServer:
    """
    BLE Server quản lý toàn bộ stack BLE.
    
    Bao gồm:
    - Advertisement (quảng cáo)
    - GATT Application (services và characteristics)
    - Main loop
    """
    
    def __init__(self):
        self.mainloop = None
        self.bus = None
        self.adapter = None
        self.advertisement = None
        self.app = None
        self.wifi_handler = None
        self.wifi_scan_handler = None
        
    def _find_adapter(self):
        """Tìm BLE adapter (hciX)."""
        obj_manager = dbus.Interface(
            self.bus.get_object(BLUEZ_SERVICE_NAME, "/"),
            DBUS_OM_IFACE
        )
        objects = obj_manager.GetManagedObjects()
        
        for path, interfaces in objects.items():
            if GATT_MANAGER_IFACE in interfaces:
                return path
                
        return None
        
    def start(self):
        """Khởi động BLE Server."""
        if not DBUS_AVAILABLE:
            logger.error("D-Bus không khả dụng. Không thể khởi động BLE Server.")
            return False
            
        try:
            # Khởi tạo D-Bus main loop
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            
            # Kết nối đến system bus
            self.bus = dbus.SystemBus()
            
            # Tìm adapter
            adapter_path = self._find_adapter()
            if not adapter_path:
                logger.error("Không tìm thấy BLE adapter")
                return False
                
            logger.info(f"Sử dụng adapter: {adapter_path}")
            
            # Bật adapter nếu chưa bật
            adapter_props = dbus.Interface(
                self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path),
                DBUS_PROP_IFACE
            )
            adapter_props.Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
            
            # Tạo main loop
            self.mainloop = GLib.MainLoop()
            
            # Tạo WiFi handler
            self.wifi_handler = WiFiSetupHandler(self.mainloop)
            
            # Tạo WiFi Scan handler
            self.wifi_scan_handler = WiFiScanHandler()
            
            # Tạo và đăng ký Application (GATT Services)
            self.app = Application(self.bus)
            wifi_service = WiFiSetupService(self.bus, 0, self.wifi_handler, self.wifi_scan_handler)
            self.app.add_service(wifi_service)
            
            # Đăng ký GATT
            gatt_manager = dbus.Interface(
                self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path),
                GATT_MANAGER_IFACE
            )
            gatt_manager.RegisterApplication(
                self.app.get_path(), {},
                reply_handler=lambda: logger.info("GATT Application đã đăng ký"),
                error_handler=lambda e: logger.error(f"Lỗi đăng ký GATT: {e}")
            )
            
            # Tạo và đăng ký Advertisement
            self.advertisement = OOBEAdvertisement(self.bus, 0)
            
            ad_manager = dbus.Interface(
                self.bus.get_object(BLUEZ_SERVICE_NAME, adapter_path),
                LE_ADVERTISING_MANAGER_IFACE
            )
            ad_manager.RegisterAdvertisement(
                self.advertisement.get_path(), {},
                reply_handler=lambda: logger.info("Advertisement đã đăng ký"),
                error_handler=lambda e: logger.error(f"Lỗi đăng ký advertisement: {e}")
            )
            
            logger.info("=" * 50)
            logger.info(f"BLE Server đã khởi động")
            logger.info(f"Tên thiết bị: {BLE_DEVICE_NAME}")
            logger.info(f"Service UUID: {SERVICE_UUID}")
            logger.info("Đang chờ kết nối từ Mobile App...")
            logger.info("=" * 50)
            
            # Chạy main loop
            self.mainloop.run()
            
            return True
            
        except Exception as e:
            logger.error(f"Lỗi khởi động BLE Server: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    def stop(self):
        """Dừng BLE Server."""
        logger.info("Đang dừng BLE Server...")
        if self.mainloop:
            self.mainloop.quit()


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def should_start_ble_setup() -> bool:
    """
    Kiểm tra xem có nên khởi động chế độ BLE Setup không.
    
    Điều kiện:
    1. Không có kết nối internet, HOẶC
    2. Nút Reset được nhấn giữ
    
    Returns:
        bool: True nếu cần khởi động BLE Setup
    """
    # Kiểm tra internet
    if not check_internet_connection():
        logger.info("Không có internet - Khởi động chế độ BLE Setup")
        return True
    
    # Kiểm tra nút GPIO (nếu có)
    gpio_handler = get_gpio_handler()
    if gpio_handler.is_button_pressed():
        logger.info("Phát hiện nút Reset được nhấn - Chờ nhả nút...")
        # Đợi nút được nhả
        gpio_handler.wait_for_release(timeout=10)
        
        logger.info("Nút Reset đã được nhấn - Khởi động chế độ BLE Setup")
        return True
        
    gpio_handler.stop_monitoring()
    
    return False


def signal_handler(signum, frame):
    """Xử lý signal để dừng gracefully."""
    logger.info(f"Nhận signal {signum}, đang thoát...")
    sys.exit(0)


def main():
    """Hàm main - Entry point của script."""
    # Đăng ký signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("=" * 60)
    logger.info("OOBE WiFi Setup - Jetson AI Kit")
    logger.info("=" * 60)
    
    # Kiểm tra xem có cần khởi động BLE Setup không
    if not should_start_ble_setup():
        logger.info("Đã có internet và không có yêu cầu Reset - Thoát")
        logger.info("Để force khởi động BLE Setup, chạy với tham số --force")
        
        # Cho phép force start với tham số --force
        if len(sys.argv) > 1 and sys.argv[1] == "--force":
            logger.info("Force start được kích hoạt")
        else:
            return
    
    # Kiểm tra D-Bus
    if not DBUS_AVAILABLE:
        logger.error("=" * 60)
        logger.error("D-Bus không khả dụng!")
        logger.error("")
        logger.error("Để chạy script này, cần cài đặt:")
        logger.error("  sudo apt-get install python3-dbus python3-gi")
        logger.error("=" * 60)
        
        # Chế độ mock cho development
        logger.info("")
        logger.info("Chạy ở chế độ MOCK cho development...")
        logger.info("BLE Server sẽ không thực sự hoạt động.")
        
        # Giữ script chạy để test
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return
    
    # Khởi động BLE Server
    server = BLEServer()
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        
    logger.info("OOBE Setup đã kết thúc")


if __name__ == "__main__":
    main()
