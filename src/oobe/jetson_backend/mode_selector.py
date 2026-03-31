#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OOBE Setup - BLE WiFi Setup
============================
Entry point cho Jetson OOBE Setup service.
Khởi động BLE mode để thiết lập WiFi qua Bluetooth.

Command line usage:
    python3 mode_selector.py                # Chạy BLE mode
    python3 mode_selector.py --log-level DEBUG  # Chạy với debug logging

Tác giả: Jetson AI Kit Team
"""

import sys
import argparse
import logging

import config

logger = logging.getLogger(__name__)


def run_ble_mode():
    """
    Khởi động BLE mode.
    """
    logger.info("=" * 60)
    logger.info("Khởi động BLE Mode")
    logger.info("=" * 60)

    try:
        # Import BLE module
        import ble_wifi_setup

        # Chạy BLE main function
        ble_wifi_setup.main()

    except ImportError as e:
        logger.error(f"Không thể import ble_wifi_setup module: {e}")
        logger.error("Đảm bảo file ble_wifi_setup.py tồn tại trong cùng thư mục")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Lỗi khi chạy BLE mode: {e}", exc_info=True)
        sys.exit(1)


def main():
    """Main entry point."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Jetson OOBE Setup - BLE WiFi Configuration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Chạy BLE mode:
  python3 mode_selector.py

  # Chạy với debug logging:
  python3 mode_selector.py --log-level DEBUG
        """
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default=None,
        help='Mức độ logging (mặc định từ config.LOG_LEVEL)'
    )

    args = parser.parse_args()

    # Setup logging
    log_level = args.log_level or config.LOG_LEVEL
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
            print(f"Cảnh báo: Không thể tạo log file {config.LOG_FILE}: {e}")

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level),
        handlers=handlers,
        force=True  # Override any existing config
    )

    logger.info("=" * 60)
    logger.info("Jetson OOBE Setup - BLE WiFi Configuration")
    logger.info("=" * 60)

    # Launch BLE mode
    run_ble_mode()


if __name__ == "__main__":
    main()
