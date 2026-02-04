#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTTP REST API Server for WiFi Setup
====================================
HTTP server phục vụ REST API cho việc thiết lập WiFi qua WiFi Access Point mode.

API Endpoints:
- GET  /api/info     - Thông tin thiết bị
- GET  /api/status   - Trạng thái kết nối WiFi
- GET  /api/scan     - Scan và trả về danh sách WiFi networks
- POST /api/connect  - Kết nối đến WiFi với SSID và password

Tác giả: Jetson AI Kit Team
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional
import urllib.parse

import config
import wifi_manager
import ap_manager

logger = logging.getLogger(__name__)


class WiFiSetupAPIHandler(BaseHTTPRequestHandler):
    """
    HTTP Request Handler cho WiFi Setup API.

    Shared state giữa các request instances:
    - wifi_status: Trạng thái kết nối WiFi hiện tại
    - connection_thread: Thread đang thực hiện kết nối WiFi
    - last_scan_result: Kết quả scan WiFi gần nhất
    """

    # Shared state (class variables)
    wifi_status = config.WiFiStatus.WAITING
    connection_thread: Optional[threading.Thread] = None
    last_scan_result = {
        'status': config.WiFiScanStatus.IDLE,
        'networks': []
    }

    def _set_cors_headers(self):
        """Thiết lập CORS headers để cho phép web app từ domain khác truy cập."""
        if config.CORS_ENABLED:
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_json_response(self, status_code: int, data: dict):
        """
        Gửi JSON response.

        Args:
            status_code: HTTP status code
            data: Dictionary để convert sang JSON
        """
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self._set_cors_headers()
        self.end_headers()

        response_json = json.dumps(data, ensure_ascii=False)
        self.wfile.write(response_json.encode('utf-8'))

    def do_OPTIONS(self):
        """Handle preflight requests cho CORS."""
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        # Parse URL
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path

        if path == '/api/info':
            self._handle_info()
        elif path == '/api/status':
            self._handle_status()
        elif path == '/api/scan':
            self._handle_scan()
        else:
            self._send_json_response(404, {
                'error': 'Not Found',
                'message': f'Endpoint {path} không tồn tại'
            })

    def do_POST(self):
        """Handle POST requests."""
        # Parse URL
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path

        if path == '/api/connect':
            self._handle_connect()
        else:
            self._send_json_response(404, {
                'error': 'Not Found',
                'message': f'Endpoint {path} không tồn tại'
            })

    def _handle_info(self):
        """
        GET /api/info
        Trả về thông tin về thiết bị.
        """
        logger.info("API: GET /api/info")

        try:
            # Lấy thông tin AP nếu đang chạy
            ap_status = ap_manager.get_ap_status()

            info = {
                'device_name': config.BLE_DEVICE_NAME,
                'mode': 'ap',
                'version': '1.2.0',
                'ap_active': ap_status['is_active'],
                'ap_ssid': ap_status.get('ssid', config.AP_SSID),
                'ap_ip': ap_status.get('ip_address', config.AP_IP_ADDRESS)
            }

            self._send_json_response(200, info)

        except Exception as e:
            logger.error(f"Lỗi khi lấy info: {e}")
            self._send_json_response(500, {
                'error': 'Internal Server Error',
                'message': str(e)
            })

    def _handle_status(self):
        """
        GET /api/status
        Trả về trạng thái kết nối WiFi hiện tại.
        """
        logger.info(f"API: GET /api/status -> {WiFiSetupAPIHandler.wifi_status}")

        self._send_json_response(200, {
            'status': WiFiSetupAPIHandler.wifi_status
        })

    def _handle_scan(self):
        """
        GET /api/scan
        Scan và trả về danh sách WiFi networks.

        Note: Sẽ tạm dừng AP, scan, rồi bật lại AP.
        Client có thể bị disconnect trong 5-10 giây.
        """
        logger.info("API: GET /api/scan - Bắt đầu scan WiFi")

        try:
            # Update scan status
            WiFiSetupAPIHandler.last_scan_result = {
                'status': config.WiFiScanStatus.SCANNING,
                'networks': []
            }

            # Tạm dừng AP để scan
            logger.info("Tạm dừng AP để scan WiFi networks")
            success, msg = ap_manager.restart_ap_for_scan()

            if not success:
                logger.warning(f"Không thể tạm dừng AP: {msg}")
                # Vẫn thử scan nhưng kết quả có thể không chính xác
                pass

            # Scan networks
            logger.info("Đang scan WiFi networks...")
            networks = wifi_manager.scan_wifi_networks()

            # Giới hạn số networks (BLE compatibility)
            if len(networks) > config.WIFI_SCAN_MAX_NETWORKS:
                networks = networks[:config.WIFI_SCAN_MAX_NETWORKS]
                logger.info(f"Giới hạn kết quả scan xuống {config.WIFI_SCAN_MAX_NETWORKS} networks")

            # Sort theo signal strength
            networks.sort(key=lambda x: x['signal'], reverse=True)

            # Update scan result
            WiFiSetupAPIHandler.last_scan_result = {
                'status': config.WiFiScanStatus.COMPLETED,
                'networks': networks
            }

            # Khởi động lại AP
            logger.info("Khởi động lại AP sau khi scan")
            ap_manager.restore_ap_after_scan()

            # Response
            self._send_json_response(200, WiFiSetupAPIHandler.last_scan_result)

            logger.info(f"Scan hoàn tất: Tìm thấy {len(networks)} networks")

        except Exception as e:
            logger.error(f"Lỗi khi scan WiFi: {e}")

            # Update scan status to error
            WiFiSetupAPIHandler.last_scan_result = {
                'status': config.WiFiScanStatus.ERROR,
                'networks': []
            }

            # Cố gắng khởi động lại AP
            try:
                ap_manager.restore_ap_after_scan()
            except:
                pass

            self._send_json_response(500, {
                'status': config.WiFiScanStatus.ERROR,
                'error': 'Scan Error',
                'message': str(e),
                'networks': []
            })

    def _handle_connect(self):
        """
        POST /api/connect
        Kết nối đến WiFi với SSID và password.

        Request body (JSON):
        {
            "ssid": "WiFi_Name",
            "password": "wifi_password"
        }
        """
        logger.info("API: POST /api/connect")

        try:
            # Đọc request body
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self._send_json_response(400, {
                    'error': 'Bad Request',
                    'message': 'Missing request body'
                })
                return

            body = self.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))

            # Validate required fields
            ssid = data.get('ssid')
            password = data.get('password')

            if not ssid:
                self._send_json_response(400, {
                    'error': 'Bad Request',
                    'message': 'Missing required field: ssid'
                })
                return

            if not password:
                self._send_json_response(400, {
                    'error': 'Bad Request',
                    'message': 'Missing required field: password'
                })
                return

            logger.info(f"Yêu cầu kết nối WiFi: SSID={ssid}")

            # Kiểm tra xem có connection thread nào đang chạy không
            if (WiFiSetupAPIHandler.connection_thread and
                WiFiSetupAPIHandler.connection_thread.is_alive()):
                logger.warning("Đã có connection đang thực hiện")
                self._send_json_response(409, {
                    'error': 'Conflict',
                    'message': 'Đang thực hiện kết nối, vui lòng đợi'
                })
                return

            # Cập nhật status thành CONNECTING
            WiFiSetupAPIHandler.wifi_status = config.WiFiStatus.CONNECTING

            # Spawn thread để kết nối WiFi (non-blocking)
            WiFiSetupAPIHandler.connection_thread = threading.Thread(
                target=self._perform_wifi_connection,
                args=(ssid, password),
                daemon=True
            )
            WiFiSetupAPIHandler.connection_thread.start()

            # Response ngay lập tức
            self._send_json_response(202, {
                'message': 'Đang kết nối WiFi',
                'status': WiFiSetupAPIHandler.wifi_status
            })

        except json.JSONDecodeError:
            logger.error("Invalid JSON in request body")
            self._send_json_response(400, {
                'error': 'Bad Request',
                'message': 'Invalid JSON'
            })
        except Exception as e:
            logger.error(f"Lỗi khi xử lý connect request: {e}")
            self._send_json_response(500, {
                'error': 'Internal Server Error',
                'message': str(e)
            })

    @staticmethod
    def _perform_wifi_connection(ssid: str, password: str):
        """
        Thực hiện kết nối WiFi trong background thread.
        Cập nhật wifi_status khi hoàn thành.

        Args:
            ssid: WiFi SSID
            password: WiFi password
        """
        logger.info(f"Thread bắt đầu kết nối WiFi: {ssid}")

        try:
            # Thực hiện kết nối
            success, message = wifi_manager.connect_wifi(ssid, password)

            if success:
                logger.info(f"✓ Kết nối WiFi thành công: {message}")
                WiFiSetupAPIHandler.wifi_status = config.WiFiStatus.SUCCESS

                # TODO: Sau khi kết nối thành công, có thể:
                # 1. Dừng HTTP server
                # 2. Dừng AP
                # 3. Exit OOBE mode
                # (Sẽ được handle bởi ap_mode.py coordinator)

            else:
                logger.error(f"✗ Kết nối WiFi thất bại: {message}")
                WiFiSetupAPIHandler.wifi_status = config.WiFiStatus.ERROR

        except Exception as e:
            logger.error(f"Exception trong connection thread: {e}")
            WiFiSetupAPIHandler.wifi_status = config.WiFiStatus.ERROR

    def log_message(self, format, *args):
        """Override log_message để sử dụng logger thay vì print."""
        logger.debug(f"{self.address_string()} - {format % args}")


class OOBEHTTPServer:
    """
    Wrapper class cho HTTP Server với lifecycle management.
    """

    def __init__(self, host: str = None, port: int = None):
        """
        Initialize HTTP server.

        Args:
            host: Host address (default from config)
            port: Port number (default from config)
        """
        self.host = host or config.HTTP_SERVER_HOST
        self.port = port or config.HTTP_SERVER_PORT
        self.server: Optional[HTTPServer] = None
        self.server_thread: Optional[threading.Thread] = None

    def start(self):
        """Khởi động HTTP server trong background thread."""
        if self.server:
            logger.warning("HTTP server đã được khởi động")
            return

        try:
            # Tạo HTTP server
            self.server = HTTPServer((self.host, self.port), WiFiSetupAPIHandler)
            logger.info(f"HTTP Server khởi động tại http://{self.host}:{self.port}")

            # Chạy trong thread để không block
            self.server_thread = threading.Thread(
                target=self.server.serve_forever,
                daemon=True
            )
            self.server_thread.start()

            logger.info("HTTP Server thread đã khởi động")

        except Exception as e:
            logger.error(f"Lỗi khi khởi động HTTP server: {e}")
            raise

    def stop(self):
        """Dừng HTTP server."""
        if not self.server:
            logger.warning("HTTP server chưa được khởi động")
            return

        try:
            logger.info("Đang dừng HTTP server...")
            self.server.shutdown()
            self.server.server_close()
            self.server = None

            if self.server_thread:
                self.server_thread.join(timeout=5)
                self.server_thread = None

            logger.info("HTTP server đã dừng")

        except Exception as e:
            logger.error(f"Lỗi khi dừng HTTP server: {e}")

    def is_running(self) -> bool:
        """Kiểm tra xem server có đang chạy không."""
        return self.server is not None and self.server_thread and self.server_thread.is_alive()

    @staticmethod
    def reset_wifi_status():
        """Reset WiFi status về WAITING."""
        WiFiSetupAPIHandler.wifi_status = config.WiFiStatus.WAITING
        logger.info("WiFi status đã được reset về WAITING")

    @staticmethod
    def get_wifi_status() -> int:
        """Lấy WiFi status hiện tại."""
        return WiFiSetupAPIHandler.wifi_status


# Test function
if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=== HTTP Server Test ===\n")

    # Tạo và khởi động server
    server = OOBEHTTPServer()

    try:
        print(f"Khởi động HTTP server tại http://{server.host}:{server.port}")
        server.start()

        print("\nServer đang chạy. Test bằng các lệnh sau:\n")
        print("# Test API info:")
        print(f"  curl http://localhost:{server.port}/api/info\n")
        print("# Test API status:")
        print(f"  curl http://localhost:{server.port}/api/status\n")
        print("# Test API scan:")
        print(f"  curl http://localhost:{server.port}/api/scan\n")
        print("# Test API connect:")
        print(f"  curl -X POST http://localhost:{server.port}/api/connect \\")
        print(f'    -H "Content-Type: application/json" \\')
        print(f'    -d \'{{"ssid":"TestWiFi","password":"test123"}}\'\n')

        print("Nhấn Ctrl+C để dừng server...")

        # Keep server running
        import time
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nDừng server...")
        server.stop()
        print("Hoàn tất!")
