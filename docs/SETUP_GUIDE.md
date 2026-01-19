# Jetson Nano Setup Guide

Installation and configuration guide for Jetson Nano Mini PC Edge AI System.

> **Requirements**: JetPack SDK installed on SD Card, SSD for extended storage.

---

## Table of Contents
1. [System Check](#1-system-check)
2. [Mount SSD](#2-mount-ssd)
3. [Network Configuration](#3-network-configuration)
4. [Camera Interface Configuration](#4-camera-interface-configuration)
5. [Development Environment Setup](#5-development-environment-setup)
6. [Clone Project](#6-clone-project)
7. [Verify Installation](#7-verify-installation)

---

## 1. System Check

### Check JetPack Version
```bash
# Check L4T version
cat /etc/nv_tegra_release

# Check CUDA version
nvcc --version

# Check JetPack components
sudo apt-cache show nvidia-jetpack | grep Version
```

### Check GPU
```bash
# View GPU info
tegrastats

# Or more detailed
sudo jetson_clocks --show
```

### Check Current Disk
```bash
df -h
lsblk
```

---

## 2. Mount SSD

### 2.1 Identify SSD
```bash
lsblk
# SSD usually shows as /dev/nvme0n1 (NVMe) or /dev/sda (SATA/USB)
```

### 2.2 Create Partition (if new SSD)
```bash
# Replace /dev/nvme0n1 with your device
sudo fdisk /dev/nvme0n1

# In fdisk:
# n -> new partition
# p -> primary
# 1 -> partition number
# Enter -> default first sector
# Enter -> default last sector (use all space)
# w -> write and exit
```

### 2.3 Format Partition
```bash
sudo mkfs.ext4 /dev/nvme0n1p1
```

### 2.4 Create Mount Point and Mount
```bash
# Create mount directory
sudo mkdir -p /data

# Mount SSD
sudo mount /dev/nvme0n1p1 /data

# Verify
df -h /data
```

### 2.5 Auto-mount on Boot
```bash
# Get partition UUID
sudo blkid /dev/nvme0n1p1

# Add to fstab
echo "UUID=<your-uuid> /data ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab

# Verify fstab
sudo mount -a
```

### 2.6 Set Permissions
```bash
# Allow current user access
sudo chown -R $USER:$USER /data
sudo chmod 755 /data
```

---

## 3. Network Configuration

### 3.1 WiFi Setup
```bash
# Scan WiFi networks
nmcli device wifi list

# Connect to WiFi
nmcli device wifi connect "SSID_NAME" password "YOUR_PASSWORD"

# Verify connection
nmcli connection show
ip addr show wlan0
```

### 3.2 Static IP (optional)
```bash
# Set static IP for WiFi
sudo nmcli connection modify "SSID_NAME" \
  ipv4.addresses "192.168.1.100/24" \
  ipv4.gateway "192.168.1.1" \
  ipv4.dns "8.8.8.8,8.8.4.4" \
  ipv4.method "manual"

# Apply
sudo nmcli connection up "SSID_NAME"
```

### 3.3 4G/5G USB Dongle (if available)
```bash
# Install ModemManager
sudo apt update
sudo apt install -y modemmanager

# Check modem
mmcli -L

# Connect 4G
sudo nmcli connection add type gsm con-name "4G" ifname "*" \
  gsm.apn "your_apn" \
  gsm.username "" \
  gsm.password ""

# Enable connection
sudo nmcli connection up "4G"
```

### 3.4 Verify Internet
```bash
ping -c 3 google.com
curl -I https://api.github.com
```

---

## 4. Camera Interface Configuration

### 4.1 CSI Camera (Raspberry Pi Camera Module)
```bash
# Check CSI camera
ls /dev/video*

# Test camera with nvgstcapture
nvgstcapture-1.0

# Or with v4l2
v4l2-ctl --list-devices
```

### 4.2 USB Camera
```bash
# Plug in USB camera and check
lsusb
ls /dev/video*

# Test with v4l2
v4l2-ctl -d /dev/video0 --list-formats-ext
```

### 4.3 Configure Camera Permissions
```bash
# Add user to video group
sudo usermod -aG video $USER

# Apply immediately
newgrp video
```

---

## 5. Development Environment Setup

### 5.1 Update System
```bash
sudo apt update
sudo apt upgrade -y
```

### 5.2 Install Python 3.10+ (if needed)
```bash
# JetPack 5.x usually has Python 3.8
# If you need Python 3.10:
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3.10-dev

# Create alias
echo "alias python=python3.10" >> ~/.bashrc
source ~/.bashrc
```

### 5.3 Install pip and venv
```bash
sudo apt install -y python3-pip python3-venv

# Upgrade pip
pip3 install --upgrade pip
```

### 5.4 Install OpenCV with CUDA
```bash
# JetPack already has OpenCV with CUDA, verify:
python3 -c "import cv2; print(cv2.getBuildInformation())" | grep CUDA

# If not available, install from wheel:
pip3 install opencv-python
```

### 5.5 Install Docker
```bash
# Docker is usually included in JetPack, verify:
docker --version

# If not installed:
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Install docker-compose
sudo apt install -y docker-compose
```

### 5.6 Install Git
```bash
sudo apt install -y git

# Configure Git
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Create SSH key (optional)
ssh-keygen -t ed25519 -C "your.email@example.com"
cat ~/.ssh/id_ed25519.pub
# Copy public key to GitHub/GitLab
```

### 5.7 Install Other Dependencies
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

### 5.8 Install jtop (Jetson monitoring)
```bash
sudo pip3 install jetson-stats
sudo systemctl restart jtop.service

# Run jtop
jtop
```

---

## 6. Clone Project

### 6.1 Create Project Directory on SSD
```bash
mkdir -p /data/projects
cd /data/projects
```

### 6.2 Clone Repository
```bash
# Clone project
git clone <your-repo-url> mini-pc
cd mini-pc
```

### 6.3 Create Python Virtual Environment
```bash
# Create venv on SSD to save SD card space
python3 -m venv /data/venv/mini-pc

# Activate
source /data/venv/mini-pc/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### 6.4 Create Data Directories
```bash
mkdir -p /data/mini-pc/{db,media,faces,logs,models}
```

---

## 7. Verify Installation

### 7.1 Run Check Script
```bash
cd /data/projects/mini-pc
./scripts/check_system.sh
```

### 7.2 Manual Verification
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

### SSD Not Detected
```bash
# Check NVMe driver
lsmod | grep nvme

# Load driver if needed
sudo modprobe nvme
```

### Camera Not Working
```bash
# Reset camera module
sudo systemctl restart nvargus-daemon
```

### CUDA Out of Memory
```bash
# Increase swap space
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
```

### Permission Denied
```bash
# User permissions
sudo usermod -aG video,docker,i2c $USER
```

---

## Next Steps

After completing setup, proceed to:
1. [Phase 2: Camera Module](./CAMERA_SETUP.md)
2. [Phase 3: AI Core Setup](./AI_CORE_SETUP.md)

---

## Checklist

- [ ] JetPack installed and working
- [ ] SSD mounted to /data
- [ ] WiFi/4G connected to Internet
- [ ] Camera interface working
- [ ] Python 3.8+ with pip
- [ ] OpenCV with CUDA
- [ ] Docker and docker-compose
- [ ] Git configured
- [ ] Project cloned to /data/projects
- [ ] Virtual environment created
- [ ] Data directories created
