#!/bin/bash
# Mini PC - Quick Setup Script
# Run this script after cloning the project

set -e

echo "========================================="
echo "  Mini PC Quick Setup"
echo "========================================="
echo ""

# Detect project directory (where this script is located)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Project directory: $PROJECT_DIR"

# Check if SSD is mounted at /data
if [ -d /data ]; then
    DATA_DIR="/data/mini-pc"
    VENV_DIR="/data/venv/mini-pc"
    echo "SSD detected at /data - using SSD for data storage"
else
    DATA_DIR="$PROJECT_DIR/data"
    VENV_DIR="$PROJECT_DIR/.venv"
    echo "No SSD at /data - using local directory for data storage"
    echo "Warning: Consider mounting SSD for better performance and storage"
fi

echo "Data directory: $DATA_DIR"
echo "Venv directory: $VENV_DIR"
echo ""

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

# 4. Create data directories
echo "=== Creating data directories ==="
if [ "$DATA_DIR" = "/data/mini-pc" ]; then
    # SSD mounted as root, need sudo
    sudo mkdir -p "$DATA_DIR"/{db,media,faces,logs,models}
    sudo chown -R $USER:$USER "$DATA_DIR"
else
    # Local directory, no sudo needed
    mkdir -p "$DATA_DIR"/{db,media,faces,logs,models}
fi


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

# 8. Create virtual environment
echo "=== Creating Python virtual environment ==="
if [ "$VENV_DIR" = "/data/venv/mini-pc" ]; then
    # SSD mounted as root, need sudo to create parent dir
    sudo mkdir -p /data/venv
    sudo chown -R $USER:$USER /data/venv
fi
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip

# 9. Install Python dependencies
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    echo "=== Installing Python dependencies ==="
    pip install -r "$PROJECT_DIR/requirements.txt"
fi

# 10. Create .env file if not exists
if [ ! -f "$PROJECT_DIR/config/.env" ]; then
    echo "=== Creating .env file ==="
    cp "$PROJECT_DIR/config/.env.example" "$PROJECT_DIR/config/.env" 2>/dev/null || true
fi

echo ""
echo "========================================="
echo "  Setup Complete!"
echo "========================================="
echo ""
echo "Configuration:"
echo "  Project: $PROJECT_DIR"
echo "  Data:    $DATA_DIR"
echo "  Venv:    $VENV_DIR"
echo ""
echo "Next steps:"
echo "1. Log out and log back in (for group changes)"
echo "2. Activate venv: source $VENV_DIR/bin/activate"
echo "3. Edit config: nano $PROJECT_DIR/config/.env"
echo "4. Run check: ./scripts/check_system.sh"
echo ""
