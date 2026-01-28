#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Xử lý GPIO cho nút Reset
=========================
Module này cung cấp các hàm để:
- Khởi tạo GPIO cho nút nhấn
- Phát hiện nhấn giữ nút để kích hoạt chế độ BLE Setup
- Callback khi có sự kiện nút nhấn

Tác giả: Jetson AI Kit Team

Hỗ trợ:
- Jetson Nano
- Jetson Orin Nano (yêu cầu Jetson.GPIO >= 2.1.0)
- Jetson Xavier series
- Raspberry Pi (fallback to RPi.GPIO)

Yêu cầu:
- Jetson.GPIO hoặc RPi.GPIO
- Quyền root HOẶC user trong group 'gpio'
- Trên Jetson Orin Nano: JetPack 5.1.2+ khuyến nghị

Cài đặt quyền GPIO:
    sudo groupadd -f -r gpio
    sudo usermod -a -G gpio $USER
    # Sau đó logout và login lại

Lưu ý:
- Nếu gặp lỗi "Could not determine Jetson model", hãy chạy với sudo
- Module tự động fallback sang mock mode nếu GPIO không khả dụng
"""

import time
import logging
import threading
from typing import Callable, Optional

# Thử import Jetson.GPIO, nếu không có thì dùng mock cho development
GPIO_AVAILABLE = False
GPIO = None
GPIO_ERROR_MSG = None

try:
    import Jetson.GPIO as GPIO
    GPIO_AVAILABLE = True
    print("Jetson.GPIO imported successfully")
except ImportError as e:
    GPIO_ERROR_MSG = f"ImportError: {e}"
    # Thử RPi.GPIO nếu Jetson.GPIO không có
    try:
        import RPi.GPIO as GPIO
        GPIO_AVAILABLE = True
        print("RPi.GPIO imported successfully")
    except ImportError:
        GPIO_ERROR_MSG = "Neither Jetson.GPIO nor RPi.GPIO available"
except Exception as e:
    # Bắt các lỗi khác khi import (ví dụ: không detect được Jetson model)
    GPIO_ERROR_MSG = f"GPIO initialization error: {type(e).__name__}: {e}"
    GPIO_AVAILABLE = False
    GPIO = None
    print(f"Warning: {GPIO_ERROR_MSG}")
    print("Fallback to Mock GPIO mode")

from config import GPIO_RESET_BUTTON, BUTTON_HOLD_TIME

# Thiết lập logger
logger = logging.getLogger(__name__)


class GPIOHandler:
    """
    Class xử lý GPIO cho nút Reset.
    
    Chức năng:
    - Theo dõi trạng thái nút nhấn
    - Phát hiện nhấn giữ (hold) để kích hoạt chế độ BLE
    - Hỗ trợ callback khi phát hiện sự kiện
    
    Attributes:
        pin (int): Số GPIO pin được sử dụng
        hold_time (float): Thời gian nhấn giữ để kích hoạt (giây)
        on_hold_callback: Hàm callback khi phát hiện nhấn giữ
        
    Example:
        >>> def on_reset_pressed():
        ...     print("Đã nhấn giữ nút Reset!")
        ...     # Khởi động chế độ BLE Setup
        ...
        >>> handler = GPIOHandler(on_hold_callback=on_reset_pressed)
        >>> handler.start_monitoring()
    """
    
    def __init__(
        self,
        pin: int = GPIO_RESET_BUTTON,
        hold_time: float = BUTTON_HOLD_TIME,
        on_hold_callback: Optional[Callable] = None
    ):
        """
        Khởi tạo GPIO Handler.
        
        Args:
            pin: Số GPIO pin (BCM numbering)
            hold_time: Thời gian nhấn giữ để kích hoạt (giây)
            on_hold_callback: Hàm được gọi khi phát hiện nhấn giữ
        """
        self.pin = pin
        self.hold_time = hold_time
        self.on_hold_callback = on_hold_callback
        
        # Biến theo dõi trạng thái
        self._button_pressed_time: Optional[float] = None
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        # Khởi tạo GPIO nếu có
        self._initialized = False
        if GPIO_AVAILABLE:
            self._setup_gpio()
        else:
            if GPIO_ERROR_MSG:
                logger.warning(f"GPIO không khả dụng: {GPIO_ERROR_MSG}")
            else:
                logger.warning(
                    "GPIO không khả dụng (chạy trên máy không phải Jetson/RPi). "
                )
            logger.info("Chế độ mock được kích hoạt - GPIO monitoring disabled.")
    
    def _setup_gpio(self) -> None:
        """
        Thiết lập GPIO pin cho nút nhấn.

        Cấu hình:
        - Mode: BCM (đánh số theo Broadcom)
        - Input với Pull-up resistor (nút nhấn nối xuống GND)
        """
        try:
            # Sử dụng BCM numbering
            GPIO.setmode(GPIO.BCM)

            # Cấu hình pin là input với pull-up resistor
            # Khi nhấn nút: LOW, khi thả: HIGH
            GPIO.setup(
                self.pin,
                GPIO.IN,
                pull_up_down=GPIO.PUD_UP
            )

            # Thêm event detect cho falling edge (khi nhấn nút)
            GPIO.add_event_detect(
                self.pin,
                GPIO.BOTH,  # Cả khi nhấn và thả
                callback=self._button_event_callback,
                bouncetime=50  # Debounce 50ms
            )

            self._initialized = True
            logger.info(f"GPIO pin {self.pin} đã được khởi tạo thành công")

        except Exception as e:
            logger.error(f"Lỗi khi khởi tạo GPIO pin {self.pin}: {e}")
            logger.warning("GPIO monitoring sẽ không hoạt động. BLE setup vẫn có thể chạy với --force flag.")
            self._initialized = False
            import traceback
            logger.debug(traceback.format_exc())
    
    def _button_event_callback(self, channel: int) -> None:
        """
        Callback được gọi khi có sự kiện trên GPIO pin.
        
        Logic:
        - Khi nhấn (LOW): Ghi nhận thời điểm bắt đầu nhấn
        - Khi thả (HIGH): Tính thời gian nhấn giữ
        - Nếu thời gian >= hold_time: Gọi on_hold_callback
        
        Args:
            channel: GPIO channel có sự kiện (không sử dụng)
        """
        # Đọc trạng thái hiện tại của nút
        button_state = GPIO.input(self.pin)
        
        if button_state == GPIO.LOW:
            # Nút được nhấn xuống
            self._button_pressed_time = time.time()
            logger.debug("Nút được nhấn")
            
        elif button_state == GPIO.HIGH and self._button_pressed_time is not None:
            # Nút được thả ra
            hold_duration = time.time() - self._button_pressed_time
            self._button_pressed_time = None
            
            logger.debug(f"Nút được thả sau {hold_duration:.2f}s")
            
            # Kiểm tra xem có giữ đủ lâu không
            if hold_duration >= self.hold_time:
                logger.info(
                    f"Phát hiện nhấn giữ {hold_duration:.2f}s >= {self.hold_time}s"
                )
                
                # Gọi callback nếu có
                if self.on_hold_callback:
                    try:
                        self.on_hold_callback()
                    except Exception as e:
                        logger.error(f"Lỗi trong callback: {e}")
    
    def start_monitoring(self) -> None:
        """
        Bắt đầu theo dõi nút nhấn.
        
        Lưu ý: Trên Jetson/RPi, event detect đã được thiết lập
        trong _setup_gpio(), method này chủ yếu để compatibility.
        """
        self._monitoring = True
        logger.info("Bắt đầu theo dõi nút nhấn GPIO")
    
    def stop_monitoring(self) -> None:
        """
        Dừng theo dõi nút nhấn và giải phóng GPIO.
        """
        self._monitoring = False

        if GPIO_AVAILABLE and self._initialized:
            try:
                GPIO.remove_event_detect(self.pin)
                GPIO.cleanup(self.pin)
                logger.info("Đã dừng theo dõi và giải phóng GPIO")
            except Exception as e:
                logger.warning(f"Lỗi khi cleanup GPIO (có thể bỏ qua): {e}")
    
    def is_button_pressed(self) -> bool:
        """
        Kiểm tra xem nút có đang được nhấn không.
        
        Returns:
            bool: True nếu nút đang được nhấn
        """
        if not GPIO_AVAILABLE or not self._initialized:
            return False
            
        return GPIO.input(self.pin) == GPIO.LOW
    
    def wait_for_release(self, timeout: float = 10.0) -> bool:
        """
        Đợi cho đến khi nút được thả ra.
        
        Args:
            timeout: Thời gian tối đa để đợi (giây)
            
        Returns:
            bool: True nếu nút được thả trong thời gian timeout
        """
        start_time = time.time()
        
        while self.is_button_pressed():
            if time.time() - start_time > timeout:
                return False
            time.sleep(0.1)
        
        return True
    
    def simulate_hold(self) -> None:
        """
        Mô phỏng sự kiện nhấn giữ nút (dùng cho testing).
        
        Gọi callback như thể nút đã được nhấn giữ.
        """
        logger.info("Mô phỏng nhấn giữ nút Reset")
        if self.on_hold_callback:
            self.on_hold_callback()
    
    def __enter__(self):
        """Context manager entry."""
        self.start_monitoring()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_monitoring()
        return False


class MockGPIOHandler(GPIOHandler):
    """
    Mock GPIO Handler cho môi trường không có GPIO thực.
    
    Hữu ích cho:
    - Development trên máy desktop
    - Unit testing
    - CI/CD pipelines
    """
    
    def __init__(self, **kwargs):
        # Không gọi super().__init__() để tránh khởi tạo GPIO thực
        self.pin = kwargs.get('pin', GPIO_RESET_BUTTON)
        self.hold_time = kwargs.get('hold_time', BUTTON_HOLD_TIME)
        self.on_hold_callback = kwargs.get('on_hold_callback')
        
        self._button_pressed_time = None
        self._monitoring = False
        self._initialized = True
        self._mock_pressed = False
        
        logger.info("MockGPIOHandler được khởi tạo (chế độ giả lập)")
    
    def _setup_gpio(self) -> None:
        pass  # Không làm gì
    
    def is_button_pressed(self) -> bool:
        return self._mock_pressed
    
    def set_mock_pressed(self, pressed: bool) -> None:
        """Thiết lập trạng thái nút cho testing."""
        self._mock_pressed = pressed


def get_gpio_handler(**kwargs) -> GPIOHandler:
    """
    Factory function để tạo GPIO handler phù hợp.
    
    Tự động chọn:
    - GPIOHandler thực nếu chạy trên Jetson/RPi
    - MockGPIOHandler nếu chạy trên máy khác
    
    Args:
        **kwargs: Tham số truyền cho constructor
        
    Returns:
        GPIOHandler hoặc MockGPIOHandler
    """
    if GPIO_AVAILABLE:
        return GPIOHandler(**kwargs)
    else:
        return MockGPIOHandler(**kwargs)


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    # Thiết lập logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    print("=" * 50)
    print("GPIO Handler - Test Mode")
    print("=" * 50)
    
    def on_reset():
        print("\n" + "!" * 50)
        print("NÚT RESET ĐÃ ĐƯỢC NHẤN GIỮ!")
        print("Sẽ khởi động chế độ BLE Setup...")
        print("!" * 50 + "\n")
    
    # Tạo handler với callback
    handler = get_gpio_handler(on_hold_callback=on_reset)
    
    print(f"\nĐang theo dõi GPIO {handler.pin}")
    print(f"Nhấn giữ nút {handler.hold_time}s để kích hoạt Reset")
    print("Nhấn Ctrl+C để thoát\n")
    
    try:
        handler.start_monitoring()
        
        # Nếu là mock, mô phỏng nhấn giữ sau 3 giây
        if not GPIO_AVAILABLE:
            print("(Chế độ mock - Sẽ mô phỏng nhấn giữ sau 3 giây)")
            time.sleep(3)
            handler.simulate_hold()
        else:
            # Chờ vô hạn
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\nĐang thoát...")
    finally:
        handler.stop_monitoring()
        print("Đã dừng theo dõi GPIO")
