#!/bin/bash
# Mini PC - Quick Setup Script
# Run this script after mounting SSD

set -e

echo "========================================="
echo "  Mini PC Quick Setup"
echo "========================================="
echo ""

# Kiểm tra /data
if [ ! -d /data ]; then
    echo "Error: /data not found. Please mount SSD first."
    echo "See docs/SETUP_GUIDE.md for instructions."
    exit 1
fi

# 1. Update system
echo "=== Updating system packages ==="
sudo apt update
sudo apt upgrade -y

# 2. Install essential packages
echo "=== Installing essential packages ==="
sudo apt install -y \
    build-essential \
    cmake \
    pkg-config \
    python3-pip \
    python3-venv \
    python3-dev \
    git \
    curl \
    wget \
    htop \
    nano \
    vim \
    net-tools \
    v4l-utils \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    libasound2-dev \
    portaudio19-dev

# 3. Install jetson-stats
echo "=== Installing jetson-stats ==="
sudo pip3 install jetson-stats || true

# 4. Create directories
echo "=== Creating project directories ==="
mkdir -p /data/projects
mkdir -p /data/venv
mkdir -p /data/mini-pc/{db,media,faces,logs,models}

# 5. Add user to groups
echo "=== Configuring user groups ==="
sudo usermod -aG video,docker,i2c $USER 2>/dev/null || true

# 6. Configure swap (if not exists)
if [ ! -f /swapfile ]; then
    echo "=== Creating swap file (4GB) ==="
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab
fi

# 7. Set performance mode
echo "=== Setting performance mode ==="
sudo nvpmodel -m 0 2>/dev/null || true  # Max performance
sudo jetson_clocks 2>/dev/null || true

echo ""
echo "========================================="
echo "  Setup Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Log out and log back in (for group changes)"
echo "2. Clone your project to /data/projects"
echo "3. Create virtual environment: python3 -m venv /data/venv/mini-pc"
echo "4. Run check_system.sh to verify"
echo ""
