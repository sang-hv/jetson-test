#!/bin/bash
# Mini PC - System Check Script
# Kiểm tra môi trường và dependencies

set -e

echo "========================================="
echo "  Mini PC System Check"
echo "========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
}

check_warn() {
    echo -e "${YELLOW}!${NC} $1"
}

# 1. OS Info
echo "=== OS Information ==="
if [ -f /etc/nv_tegra_release ]; then
    check_pass "Jetson detected"
    cat /etc/nv_tegra_release
else
    check_fail "Not running on Jetson"
fi
echo ""

# 2. CUDA
echo "=== CUDA ==="
if command -v nvcc &> /dev/null; then
    CUDA_VERSION=$(nvcc --version | grep "release" | awk '{print $6}')
    check_pass "CUDA installed: $CUDA_VERSION"
else
    check_fail "CUDA not found"
fi
echo ""

# 3. Python
echo "=== Python ==="
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version)
    check_pass "$PY_VERSION"
else
    check_fail "Python3 not found"
fi

if command -v pip3 &> /dev/null; then
    PIP_VERSION=$(pip3 --version | awk '{print $2}')
    check_pass "pip3: $PIP_VERSION"
else
    check_fail "pip3 not found"
fi
echo ""

# 4. OpenCV
echo "=== OpenCV ==="
CV2_INFO=$(python3 -c "import cv2; print(f'{cv2.__version__}')" 2>/dev/null) || CV2_INFO=""
if [ -n "$CV2_INFO" ]; then
    check_pass "OpenCV: $CV2_INFO"
    
    # Check CUDA support in OpenCV
    CUDA_ENABLED=$(python3 -c "import cv2; print('Yes' if cv2.cuda.getCudaEnabledDeviceCount() > 0 else 'No')" 2>/dev/null) || CUDA_ENABLED="Unknown"
    if [ "$CUDA_ENABLED" == "Yes" ]; then
        check_pass "OpenCV CUDA: Enabled"
    else
        check_warn "OpenCV CUDA: Disabled (may affect performance)"
    fi
else
    check_fail "OpenCV not installed"
fi
echo ""

# 5. Docker
echo "=== Docker ==="
if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version | awk '{print $3}')
    check_pass "Docker: $DOCKER_VERSION"
    
    # Check if user is in docker group
    if groups | grep -q docker; then
        check_pass "User in docker group"
    else
        check_warn "User not in docker group (run: sudo usermod -aG docker \$USER)"
    fi
else
    check_fail "Docker not installed"
fi
echo ""

# 6. Git
echo "=== Git ==="
if command -v git &> /dev/null; then
    GIT_VERSION=$(git --version | awk '{print $3}')
    check_pass "Git: $GIT_VERSION"
else
    check_fail "Git not installed"
fi
echo ""

# 7. Camera
echo "=== Camera ==="
VIDEO_DEVICES=$(ls /dev/video* 2>/dev/null | wc -l)
if [ "$VIDEO_DEVICES" -gt 0 ]; then
    check_pass "Video devices found: $VIDEO_DEVICES"
    ls /dev/video*
else
    check_warn "No video devices found"
fi
echo ""

# 8. Disk Space
echo "=== Disk Space ==="
ROOT_USAGE=$(df -h / | awk 'NR==2 {print $5}')
check_pass "Root (/): $ROOT_USAGE used"

if [ -d /data ]; then
    DATA_USAGE=$(df -h /data | awk 'NR==2 {print $5}')
    DATA_SIZE=$(df -h /data | awk 'NR==2 {print $2}')
    DATA_AVAIL=$(df -h /data | awk 'NR==2 {print $4}')
    check_pass "/data: $DATA_SIZE total, $DATA_AVAIL available ($DATA_USAGE used)"
else
    check_warn "/data not mounted (SSD not configured)"
fi
echo ""

# 9. Memory
echo "=== Memory ==="
TOTAL_MEM=$(free -h | awk 'NR==2 {print $2}')
AVAIL_MEM=$(free -h | awk 'NR==2 {print $7}')
check_pass "Total: $TOTAL_MEM, Available: $AVAIL_MEM"

SWAP=$(free -h | awk 'NR==3 {print $2}')
if [ "$SWAP" != "0B" ]; then
    check_pass "Swap: $SWAP"
else
    check_warn "Swap not configured (recommended for AI inference)"
fi
echo ""

# 10. GPU
echo "=== GPU ==="
if command -v nvidia-smi &> /dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null) || GPU_INFO=""
    if [ -n "$GPU_INFO" ]; then
        check_pass "GPU: $GPU_INFO"
    fi
else
    # For Jetson, try tegrastats
    if [ -f /etc/nv_tegra_release ]; then
        check_pass "Jetson GPU (integrated)"
    fi
fi
echo ""

# 11. Network
echo "=== Network ==="
# Check internet
if ping -c 1 google.com &> /dev/null; then
    check_pass "Internet: Connected"
else
    check_fail "Internet: Not connected"
fi

# Check IP
IP_ADDR=$(hostname -I | awk '{print $1}')
if [ -n "$IP_ADDR" ]; then
    check_pass "IP Address: $IP_ADDR"
fi
echo ""

# Summary
echo "========================================="
echo "  Check Complete"
echo "========================================="
