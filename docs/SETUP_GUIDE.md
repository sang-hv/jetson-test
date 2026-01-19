# Jetson Nano Setup Guide

Hướng dẫn cài đặt và cấu hình Jetson Nano cho Mini PC Edge AI System.

> **Yêu cầu**: JetPack SDK đã được cài đặt trên SD Card, có SSD để mở rộng storage.

---

## Mục lục
1. [Kiểm tra hệ thống](#1-kiểm-tra-hệ-thống)
2. [Mount SSD](#2-mount-ssd)
3. [Cấu hình Network](#3-cấu-hình-network)
4. [Cấu hình Camera Interface](#4-cấu-hình-camera-interface)
5. [Thiết lập Development Environment](#5-thiết-lập-development-environment)
6. [Clone Project](#6-clone-project)
7. [Xác minh cài đặt](#7-xác-minh-cài-đặt)

---

## 1. Kiểm tra hệ thống

### Kiểm tra JetPack version
```bash
# Kiểm tra L4T version
cat /etc/nv_tegra_release

# Kiểm tra CUDA version
nvcc --version

# Kiểm tra JetPack components
sudo apt-cache show nvidia-jetpack | grep Version
```

### Kiểm tra GPU
```bash
# Xem thông tin GPU
tegrastats

# Hoặc chi tiết hơn
sudo jetson_clocks --show
```

### Kiểm tra disk hiện tại
```bash
df -h
lsblk
```

---

## 2. Mount SSD

### 2.1 Xác định SSD
```bash
lsblk
# SSD thường hiển thị là /dev/nvme0n1 (NVMe) hoặc /dev/sda (SATA/USB)
```

### 2.2 Tạo partition (nếu SSD mới)
```bash
# Thay /dev/nvme0n1 bằng device của bạn
sudo fdisk /dev/nvme0n1

# Trong fdisk:
# n -> new partition
# p -> primary
# 1 -> partition number
# Enter -> default first sector
# Enter -> default last sector (use all space)
# w -> write and exit
```

### 2.3 Format partition
```bash
sudo mkfs.ext4 /dev/nvme0n1p1
```

### 2.4 Tạo mount point và mount
```bash
# Tạo thư mục mount
sudo mkdir -p /data

# Mount SSD
sudo mount /dev/nvme0n1p1 /data

# Kiểm tra
df -h /data
```

### 2.5 Auto-mount khi khởi động
```bash
# Lấy UUID của partition
sudo blkid /dev/nvme0n1p1

# Thêm vào fstab
echo "UUID=<your-uuid> /data ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab

# Kiểm tra fstab
sudo mount -a
```

### 2.6 Thiết lập quyền
```bash
# Cho phép user hiện tại truy cập
sudo chown -R $USER:$USER /data
sudo chmod 755 /data
```

---

## 3. Cấu hình Network

### 3.1 WiFi Setup
```bash
# Scan WiFi networks
nmcli device wifi list

# Kết nối WiFi
nmcli device wifi connect "SSID_NAME" password "YOUR_PASSWORD"

# Kiểm tra kết nối
nmcli connection show
ip addr show wlan0
```

### 3.2 Static IP (tùy chọn)
```bash
# Tạo static IP cho WiFi
sudo nmcli connection modify "SSID_NAME" \
  ipv4.addresses "192.168.1.100/24" \
  ipv4.gateway "192.168.1.1" \
  ipv4.dns "8.8.8.8,8.8.4.4" \
  ipv4.method "manual"

# Áp dụng
sudo nmcli connection up "SSID_NAME"
```

### 3.3 4G/5G USB Dongle (nếu có)
```bash
# Cài đặt ModemManager
sudo apt update
sudo apt install -y modemmanager

# Kiểm tra modem
mmcli -L

# Kết nối 4G
sudo nmcli connection add type gsm con-name "4G" ifname "*" \
  gsm.apn "your_apn" \
  gsm.username "" \
  gsm.password ""

# Bật kết nối
sudo nmcli connection up "4G"
```

### 3.4 Kiểm tra Internet
```bash
ping -c 3 google.com
curl -I https://api.github.com
```

---

## 4. Cấu hình Camera Interface

### 4.1 CSI Camera (Raspberry Pi Camera Module)
```bash
# Kiểm tra CSI camera
ls /dev/video*

# Test camera với nvgstcapture
nvgstcapture-1.0

# Hoặc với v4l2
v4l2-ctl --list-devices
```

### 4.2 USB Camera
```bash
# Cắm USB camera và kiểm tra
lsusb
ls /dev/video*

# Test với v4l2
v4l2-ctl -d /dev/video0 --list-formats-ext
```

### 4.3 Cấu hình Camera permissions
```bash
# Thêm user vào group video
sudo usermod -aG video $USER

# Áp dụng ngay
newgrp video
```

---

## 5. Thiết lập Development Environment

### 5.1 Update hệ thống
```bash
sudo apt update
sudo apt upgrade -y
```

### 5.2 Cài đặt Python 3.10+ (nếu cần)
```bash
# JetPack 5.x thường có Python 3.8
# Nếu cần Python 3.10:
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3.10-dev

# Tạo alias
echo "alias python=python3.10" >> ~/.bashrc
source ~/.bashrc
```

### 5.3 Cài đặt pip và venv
```bash
sudo apt install -y python3-pip python3-venv

# Upgrade pip
pip3 install --upgrade pip
```

### 5.4 Cài đặt OpenCV với CUDA
```bash
# JetPack đã có OpenCV với CUDA, kiểm tra:
python3 -c "import cv2; print(cv2.getBuildInformation())" | grep CUDA

# Nếu chưa có, cài từ wheel:
pip3 install opencv-python
```

### 5.5 Cài đặt Docker
```bash
# Docker thường đã có trong JetPack, kiểm tra:
docker --version

# Nếu chưa có:
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Thêm user vào docker group
sudo usermod -aG docker $USER
newgrp docker

# Cài docker-compose
sudo apt install -y docker-compose
```

### 5.6 Cài đặt Git
```bash
sudo apt install -y git

# Cấu hình Git
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Tạo SSH key (tùy chọn)
ssh-keygen -t ed25519 -C "your.email@example.com"
cat ~/.ssh/id_ed25519.pub
# Copy public key lên GitHub/GitLab
```

### 5.7 Cài đặt các dependencies khác
```bash
# Build tools
sudo apt install -y build-essential cmake pkg-config

# Image/Video libraries
sudo apt install -y libjpeg-dev libpng-dev libtiff-dev
sudo apt install -y libavcodec-dev libavformat-dev libswscale-dev
sudo apt install -y libv4l-dev v4l-utils

# Audio libraries
sudo apt install -y libasound2-dev portaudio19-dev

# Networking
sudo apt install -y curl wget net-tools

# Utilities
sudo apt install -y htop nvtop jtop nano vim
```

### 5.8 Cài đặt jtop (Jetson monitoring)
```bash
sudo pip3 install jetson-stats
sudo systemctl restart jtop.service

# Chạy jtop
jtop
```

---

## 6. Clone Project

### 6.1 Tạo thư mục project trên SSD
```bash
mkdir -p /data/projects
cd /data/projects
```

### 6.2 Clone repository
```bash
# Clone project
git clone <your-repo-url> mini-pc
cd mini-pc
```

### 6.3 Tạo Python virtual environment
```bash
# Tạo venv trên SSD để tiết kiệm SD card
python3 -m venv /data/venv/mini-pc

# Activate
source /data/venv/mini-pc/bin/activate

# Cài dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 6.4 Tạo data directories
```bash
mkdir -p /data/mini-pc/{db,media,faces,logs,models}
```

---

## 7. Xác minh cài đặt

### 7.1 Chạy script kiểm tra
```bash
cd /data/projects/mini-pc
python scripts/check_system.py
```

### 7.2 Kiểm tra thủ công
```bash
# Python & GPU
python3 -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# OpenCV
python3 -c "import cv2; print(f'OpenCV: {cv2.__version__}')"

# Camera
python3 -c "import cv2; cap = cv2.VideoCapture(0); print(f'Camera: {cap.isOpened()}')"

# Disk space
df -h /data

# Memory
free -h

# GPU memory
tegrastats
```

---

## Troubleshooting

### SSD không được nhận
```bash
# Kiểm tra driver NVMe
lsmod | grep nvme

# Load driver nếu cần
sudo modprobe nvme
```

### Camera không hoạt động
```bash
# Reset camera module
sudo systemctl restart nvargus-daemon
```

### CUDA out of memory
```bash
# Tăng swap space
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

### Permission denied
```bash
# User permissions
sudo usermod -aG video,docker,i2c $USER
```

---

## Tiếp theo

Sau khi hoàn thành setup, tiến hành:
1. [Phase 2: Camera Module](./CAMERA_SETUP.md)
2. [Phase 3: AI Core Setup](./AI_CORE_SETUP.md)

---

## Checklist

- [ ] JetPack đã cài đặt và hoạt động
- [ ] SSD đã mount vào /data
- [ ] WiFi/4G đã kết nối Internet
- [ ] Camera interface hoạt động
- [ ] Python 3.8+ với pip
- [ ] OpenCV với CUDA
- [ ] Docker và docker-compose
- [ ] Git đã cấu hình
- [ ] Project đã clone về /data/projects
- [ ] Virtual environment đã tạo
- [ ] Data directories đã tạo
