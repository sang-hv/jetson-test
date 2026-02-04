#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Access Point Mode Coordinator
==============================
Coordinator cho AP mode - quản lý lifecycle của WiFi AP và HTTP server.

Workflow:
1. Kiểm tra điều kiện khởi động (no internet, GPIO button, --force flag)
2. Tạo WiFi Access Point
3. Khởi động HTTP server
4. Đợi kết nối WiFi thành công hoặc timeout
5. Cleanup: Dừng AP và HTTP server

Tác giả: Jetson AI Kit Team
"""

import logging
import time
import signal
import sys
from typing import Optional

import config
import ap_manager
import http_server
import wifi_manager
from gpio_handler import get_gpio_handler

logger = logging.getLogger(__name__)

# Global flag để handle graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle SIGINT và SIGTERM để cleanup gracefully."""
    global shutdown_requested
    logger.info(f"Nhận tín hiệu {signum}, chuẩn bị shutdown...")
    shutdown_requested = True


def should_start_ap_mode(force: bool = False) -> bool:
    """
    Kiểm tra xem có nên khởi động AP mode không.

    Args:
        force: Bỏ qua tất cả kiểm tra và luôn khởi động AP

    Returns:
        bool: True nếu nên khởi động AP mode
    """
    if force:
        logger.info("--force flag được bật, khởi động AP mode")
        return True

    # Kiểm tra 1: Không có internet
    logger.info("Kiểm tra kết nối internet...")
    if not wifi_manager.check_internet_connection():
        logger.info("Không có internet, cần khởi động AP mode")
        return True

    logger.info("Đã có kết nối internet, không cần AP mode")

    # Kiểm tra 2: GPIO reset button (optional)
    try:
        gpio = get_gpio_handler()
        if gpio.is_button_held(hold_time=config.BUTTON_HOLD_TIME):
            logger.info("GPIO reset button được nhấn giữ, khởi động AP mode")
            return True
    except Exception as e:
        logger.debug(f"Không thể kiểm tra GPIO button: {e}")

    return False


class APModeServer:
    """
    Server coordinator cho AP mode.
    Quản lý WiFi AP, HTTP server, và lifecycle.
    """

    def __init__(self):
        """Initialize AP mode server."""
        self.ap_active = False
        self.http_server: Optional[http_server.OOBEHTTPServer] = None
        self.start_time = None

    def start(self, force: bool = False):
        """
        Khởi động AP mode với đầy đủ workflow.

        Args:
            force: Bỏ qua kiểm tra điều kiện khởi động

        Returns:
            int: Exit code (0 = success, 1 = error)
        """
        global shutdown_requested

        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("=" * 60)
        logger.info("Jetson OOBE Setup - Access Point Mode")
        logger.info("=" * 60)

        # Kiểm tra điều kiện khởi động
        if not should_start_ap_mode(force=force):
            logger.info("Không cần khởi động AP mode")
            return 0

        try:
            # Bước 1: Tạo WiFi Access Point
            logger.info("\n[1/3] Tạo WiFi Access Point...")
            logger.info(f"SSID: {config.AP_SSID}")
            logger.info(f"Password: {config.AP_PASSWORD}")
            logger.info(f"IP Address: {config.AP_IP_ADDRESS}")

            success, msg = ap_manager.create_access_point()
            if not success:
                logger.error(f"Không thể tạo Access Point: {msg}")
                return 1

            self.ap_active = True
            logger.info(f"✓ Access Point đã được tạo thành công")

            # Bước 2: Khởi động HTTP server
            logger.info(f"\n[2/3] Khởi động HTTP Server...")
            logger.info(f"URL: http://{config.AP_IP_ADDRESS}:{config.HTTP_SERVER_PORT}")

            self.http_server = http_server.OOBEHTTPServer()
            self.http_server.start()

            if not self.http_server.is_running():
                logger.error("HTTP server không khởi động được")
                self.cleanup()
                return 1

            logger.info("✓ HTTP Server đã khởi động")

            # Bước 3: Log thông tin kết nối
            logger.info("\n" + "=" * 60)
            logger.info("AP MODE ĐANG HOẠT ĐỘNG")
            logger.info("=" * 60)
            logger.info(f"WiFi SSID: {config.AP_SSID}")
            logger.info(f"WiFi Password: {config.AP_PASSWORD}")
            logger.info(f"Web Interface: http://{config.AP_IP_ADDRESS}:{config.HTTP_SERVER_PORT}")
            logger.info(f"API Base URL: http://{config.AP_IP_ADDRESS}:{config.HTTP_SERVER_PORT}/api")
            logger.info("\nHƯỚNG DẪN:")
            logger.info(f"1. Kết nối thiết bị di động/laptop vào WiFi '{config.AP_SSID}'")
            logger.info(f"2. Mật khẩu: {config.AP_PASSWORD}")
            logger.info(f"3. Mở web app và chọn 'WiFi Access Point' mode")
            logger.info(f"4. Nhấn 'Kết nối với Jetson' để bắt đầu")
            logger.info("=" * 60 + "\n")

            # Bước 4: Main loop - đợi kết nối thành công hoặc timeout
            self.start_time = time.time()
            self._main_loop()

            # Cleanup khi hoàn thành
            logger.info("\nKết thúc AP mode, cleanup...")
            self.cleanup()

            return 0

        except Exception as e:
            logger.error(f"Lỗi không mong đợi trong AP mode: {e}", exc_info=True)
            self.cleanup()
            return 1

    def _main_loop(self):
        """
        Main loop - monitor WiFi connection status và timeout.
        Exit khi:
        - WiFi kết nối thành công
        - Timeout (30 phút)
        - Shutdown signal nhận được
        """
        global shutdown_requested

        logger.info("[3/3] Đang chờ kết nối WiFi...")

        check_interval = 2  # Kiểm tra mỗi 2 giây
        last_status = None

        while not shutdown_requested:
            try:
                # Kiểm tra timeout
                elapsed = time.time() - self.start_time
                if elapsed > config.AP_AUTO_SHUTDOWN_TIMEOUT:
                    logger.warning(f"Timeout sau {config.AP_AUTO_SHUTDOWN_TIMEOUT}s, dừng AP mode")
                    break

                # Kiểm tra WiFi status
                current_status = self.http_server.get_wifi_status()

                # Log status change
                if current_status != last_status:
                    status_names = {
                        config.WiFiStatus.WAITING: "WAITING",
                        config.WiFiStatus.CONNECTING: "CONNECTING",
                        config.WiFiStatus.SUCCESS: "SUCCESS",
                        config.WiFiStatus.ERROR: "ERROR"
                    }
                    logger.info(f"WiFi status: {status_names.get(current_status, current_status)}")
                    last_status = current_status

                # Kiểm tra xem có kết nối thành công chưa
                if current_status == config.WiFiStatus.SUCCESS:
                    logger.info("✓ Kết nối WiFi thành công!")

                    # Đợi một chút để đảm bảo kết nối ổn định
                    logger.info("Đợi 3 giây để kết nối ổn định...")
                    time.sleep(3)

                    # Verify internet connection
                    if wifi_manager.check_internet_connection():
                        logger.info("✓ Verified: Có kết nối internet")
                    else:
                        logger.warning("Kết nối WiFi OK nhưng chưa có internet")

                    # Exit main loop
                    break

                # Sleep trước khi check lại
                time.sleep(check_interval)

            except Exception as e:
                logger.error(f"Lỗi trong main loop: {e}")
                time.sleep(check_interval)

        if shutdown_requested:
            logger.info("Shutdown được yêu cầu")

    def cleanup(self):
        """Cleanup resources: stop HTTP server và AP."""
        logger.info("Cleanup resources...")

        # Stop HTTP server
        if self.http_server:
            try:
                logger.info("Đang dừng HTTP server...")
                self.http_server.stop()
            except Exception as e:
                logger.error(f"Lỗi khi dừng HTTP server: {e}")

        # Stop Access Point
        if self.ap_active:
            try:
                logger.info("Đang dừng Access Point...")
                success, msg = ap_manager.stop_access_point()
                if success:
                    logger.info("✓ Access Point đã dừng")
                else:
                    logger.warning(f"Lỗi khi dừng AP: {msg}")
            except Exception as e:
                logger.error(f"Lỗi khi dừng AP: {e}")

        logger.info("Cleanup hoàn tất")


def main():
    """Main entry point cho AP mode."""
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Jetson OOBE Setup - AP Mode')
    parser.add_argument(
        '--force',
        action='store_true',
        help='Bỏ qua kiểm tra điều kiện, luôn khởi động AP mode'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Mức độ logging'
    )

    args = parser.parse_args()

    # Setup logging
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format))

    # File handler (nếu có config)
    handlers = [console_handler]
    if config.LOG_FILE:
        try:
            file_handler = logging.FileHandler(config.LOG_FILE)
            file_handler.setFormatter(logging.Formatter(log_format))
            handlers.append(file_handler)
        except Exception as e:
            print(f"Không thể tạo log file: {e}")

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        handlers=handlers
    )

    # Start AP mode server
    server = APModeServer()
    exit_code = server.start(force=args.force)

    logger.info("AP Mode đã kết thúc")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
