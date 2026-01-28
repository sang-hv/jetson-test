# Hướng dẫn Triển khai OOBE WiFi Setup

Tài liệu này hướng dẫn chi tiết cách cài đặt và triển khai giải pháp OOBE (Out of Box Experience) 
WiFi Setup qua Bluetooth Low Energy cho Jetson Nano AI Kit.

---

## Mục lục

1. [Yêu cầu hệ thống](#1-yêu-cầu-hệ-thống)
2. [Cài đặt trên Jetson Nano](#2-cài-đặt-trên-jetson-nano)
3. [Cấu hình Bluetooth](#3-cấu-hình-bluetooth)
4. [Cài đặt Service Systemd](#4-cài-đặt-service-systemd)
5. [Chạy Web App](#5-chạy-web-app)
6. [Kiểm tra và Debug](#6-kiểm-tra-và-debug)
7. [Câu hỏi thường gặp](#7-câu-hỏi-thường-gặp)

---

## 1. Yêu cầu hệ thống

### Jetson Nano

| Thành phần | Yêu cầu |
|------------|---------|
| OS | JetPack 4.5+ (Ubuntu 18.04) |
| Python | 3.6+ |
| Bluetooth | BlueZ 5.48+ |
| Network Manager | nmcli |

### Web App (Thiết bị client)

| Thành phần | Yêu cầu |
|------------|---------|
| Trình duyệt | Chrome 56+, Edge 79+, Opera 43+ |
| Bluetooth | BLE 4.0+ |
| HTTPS | Bắt buộc (hoặc localhost) |

> ⚠️ **Lưu ý**: Web Bluetooth API chỉ hoạt động trên HTTPS hoặc localhost.

---

## 2. Cài đặt trên Jetson Nano

### Bước 1: Cập nhật hệ thống

```bash
sudo apt-get update
sudo apt-get upgrade -y
```

### Bước 2: Cài đặt các thư viện hệ thống

```bash
# BlueZ - Bluetooth stack cho Linux
sudo apt-get install -y bluetooth bluez libbluetooth-dev

# D-Bus và GLib bindings cho Python
sudo apt-get install -y python3-dbus python3-gi python3-gi-cairo gir1.2-gtk-3.0

# Network Manager (thường đã có sẵn)
sudo apt-get install -y network-manager

# GPIO library cho Jetson
sudo apt-get install -y python3-pip
sudo pip3 install Jetson.GPIO
```

### Bước 3: Copy code lên Jetson

```bash
# Tạo thư mục cho ứng dụng
sudo mkdir -p /opt/oobe-setup

# Copy tất cả file trong jetson_backend vào /opt/oobe-setup
# (Thực hiện từ máy development)
scp -r jetson_backend/* jetson@<JETSON_IP>:/opt/oobe-setup/

# Hoặc nếu đang trên Jetson, copy trực tiếp
sudo cp -r /path/to/jetson_backend/* /opt/oobe-setup/
```

### Bước 4: Cài đặt Python dependencies

```bash
cd /opt/oobe-setup
sudo pip3 install -r requirements.txt
```

### Bước 5: Đặt quyền thực thi

```bash
sudo chmod +x /opt/oobe-setup/ble_wifi_setup.py
```

---

## 3. Cấu hình Bluetooth

### Bước 1: Bỏ chặn Bluetooth

Đôi khi Bluetooth bị chặn bởi rfkill. Kiểm tra và bỏ chặn:

```bash
# Kiểm tra trạng thái
rfkill list bluetooth

# Nếu bị blocked, chạy lệnh sau
sudo rfkill unblock bluetooth
```

### Bước 2: Khởi động Bluetooth service

```bash
# Bật service
sudo systemctl enable bluetooth
sudo systemctl start bluetooth

# Kiểm tra trạng thái
sudo systemctl status bluetooth
```

### Bước 3: Cấu hình BlueZ

Chỉnh sửa file cấu hình BlueZ:

```bash
sudo nano /etc/bluetooth/main.conf
```

Thêm hoặc sửa các dòng sau:

```ini
[General]
# Cho phép BLE advertising
Name = Jetson_AI_Kit
DiscoverableTimeout = 0
PairableTimeout = 0

[Policy]
AutoEnable=true
```

### Bước 4: Khởi động lại Bluetooth

```bash
sudo systemctl restart bluetooth
```

### Bước 5: Kiểm tra adapter

```bash
# Liệt kê các adapter
hciconfig

# Bật adapter nếu cần
sudo hciconfig hci0 up

# Kiểm tra bằng bluetoothctl
bluetoothctl
> power on
> show
> exit
```

---

## 4. Cài đặt Service Systemd

### Bước 1: Copy file service

```bash
sudo cp /opt/oobe-setup/systemd/oobe-setup.service /etc/systemd/system/
```

### Bước 2: Reload systemd

```bash
sudo systemctl daemon-reload
```

### Bước 3: Bật service tự động khởi động

```bash
sudo systemctl enable oobe-setup.service
```

### Bước 4: Khởi động service

```bash
sudo systemctl start oobe-setup.service
```

### Bước 5: Kiểm tra trạng thái

```bash
# Xem trạng thái
sudo systemctl status oobe-setup.service

# Xem log realtime
sudo journalctl -u oobe-setup.service -f
```

### Quản lý Service

```bash
# Dừng service
sudo systemctl stop oobe-setup.service

# Khởi động lại
sudo systemctl restart oobe-setup.service

# Tắt tự động khởi động
sudo systemctl disable oobe-setup.service
```

---

## 5. Chạy Web App

### Development Mode

```bash
# Di chuyển đến thư mục web_app
cd web_app

# Cài đặt dependencies
npm install

# Chạy development server
npm run dev
```

Mở trình duyệt Chrome/Edge và truy cập: `http://localhost:3000`

### Production Build

```bash
# Build production
npm run build

# Preview build
npm run preview
```

### Deploy lên Web Server

Sau khi build, copy thư mục `dist` lên web server (nginx, Apache, etc.):

```bash
# Ví dụ với nginx
sudo cp -r dist/* /var/www/html/oobe-setup/
```

> ⚠️ **Quan trọng**: Web Bluetooth yêu cầu HTTPS trong production. 
> Cấu hình SSL/TLS cho web server của bạn.

---

## 6. Kiểm tra và Debug

### Test Script thủ công

```bash
# Chạy script với quyền root
sudo python3 /opt/oobe-setup/ble_wifi_setup.py --force
```

### Xem Log

```bash
# Log của service
sudo journalctl -u oobe-setup.service -n 100

# Log file (nếu cấu hình)
sudo cat /var/log/oobe-setup.log

# Log realtime
sudo journalctl -u oobe-setup.service -f
```

### Kiểm tra BLE đang hoạt động

```bash
# Quét BLE devices
sudo hcitool lescan

# Hoặc dùng bluetoothctl
bluetoothctl
> scan on
```

### Kiểm tra WiFi

```bash
# Liệt kê connections
nmcli connection show

# Liệt kê mạng WiFi
nmcli device wifi list

# Kiểm tra kết nối hiện tại
nmcli device status
```

### Debug GPIO

```bash
# Kiểm tra GPIO có hoạt động không
python3 -c "
import Jetson.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.IN, pull_up_down=GPIO.PUD_UP)
print('GPIO 17 state:', GPIO.input(17))
GPIO.cleanup()
"
```

---

## 7. Câu hỏi thường gặp

### Q: Web Bluetooth không hoạt động?

**A**: Kiểm tra:
1. Đang sử dụng Chrome/Edge/Opera
2. Đang truy cập qua HTTPS hoặc localhost
3. Bluetooth trên thiết bị đã bật
4. Cho phép quyền Bluetooth trong trình duyệt

### Q: Không tìm thấy thiết bị Jetson_AI_Kit?

**A**: Kiểm tra:
1. Script BLE đang chạy trên Jetson: `sudo systemctl status oobe-setup`
2. Bluetooth đã bật: `hciconfig`
3. Không bị rfkill chặn: `rfkill list`

### Q: Kết nối WiFi thất bại?

**A**: Kiểm tra:
1. SSID và password chính xác
2. Mạng WiFi trong tầm phủ sóng
3. Jetson có hỗ trợ loại bảo mật của mạng (WPA2, WPA3)

### Q: Service không khởi động khi boot?

**A**: Kiểm tra:
1. Service đã enable: `sudo systemctl is-enabled oobe-setup`
2. Xem lỗi: `sudo journalctl -u oobe-setup -b`
3. Đường dẫn script đúng trong file service

### Q: Làm sao để test trên máy Mac/Windows?

**A**: Script Python sử dụng D-Bus chỉ hoạt động trên Linux với BlueZ.
Để test trên máy khác:
- Sử dụng chế độ mock (script tự động detect)
- Hoặc test trên máy ảo Linux với USB Bluetooth adapter

---

## Cấu trúc file hoàn chỉnh

```
/opt/oobe-setup/
├── ble_wifi_setup.py      # Script BLE chính
├── config.py              # Cấu hình
├── wifi_manager.py        # Quản lý WiFi
├── gpio_handler.py        # Xử lý GPIO
├── requirements.txt       # Python dependencies
└── systemd/
    └── oobe-setup.service # Systemd service file
```

---

## Liên hệ hỗ trợ

Nếu gặp vấn đề, vui lòng:
1. Kiểm tra log: `sudo journalctl -u oobe-setup.service`
2. Chạy script thủ công với `--force` để debug
3. Mở issue trên GitHub repository

---

**Phiên bản**: 1.0.0  
**Cập nhật**: Tháng 1, 2026
