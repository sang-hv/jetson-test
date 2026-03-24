# Mini PC - Jetson Nano AI Edge Computing System

AI-powered edge computing system for security monitoring, access control, and analytics.

## Hardware Requirements

- **NVIDIA Jetson Nano** (4GB recommended)
- USB Camera hoặc IP Camera (RTSP support)
- Microphone & Speaker (optional)
- WiFi/4G/5G Module
- Storage: 64GB+ SD Card hoặc NVMe SSD

## Project Structure

```
mini-pc/
├── src/
│   ├── ai_core/          # AI Models & Inference (TensorRT)
│   ├── features/         # Feature Modules
│   ├── camera/           # Camera & Streaming
│   ├── audio/            # Audio Processing
│   ├── backend_client/   # Cloud Connectivity
│   ├── storage/          # Local Storage Management
│   ├── scheduler/        # Task Scheduling
│   ├── security/         # Security & Encryption
│   └── system/           # System Monitoring & Management
├── config/               # Configuration Files
├── scripts/              # Utility Scripts
├── tests/                # Test Suites
├── docs/                 # Documentation
└── docker/               # Docker Configuration
```

## Quick Start

```bash
# Install dependencies
./scripts/setup.sh

# Run development server
python src/main.py

# Run with Docker
docker-compose up -d
```

## 4G LTE Module Setup (SIM7600G-H)

The system is pre-configured to automatically manage the 4G LTE connection and handle network failover (LAN > WiFi > 4G) via `master-setup.sh`.

**Important for New Hardware Installations:**
If you are deploying a brand new SIM7600 module and it fails to get an IP address via DHCP (e.g. `usb0` or `usb2` timeouts), it is likely operating in the default Serial mode rather than the required Linux RNDIS mode.

Run this one-time command on the Jetson Nano terminal to switch the module to RNDIS mode at the hardware level:
```bash
sudo systemctl stop ModemManager
sudo python3 -c 'import os, time; fd = os.open("/dev/ttyUSB2", os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK); os.write(fd, b"AT+CUSBPIDSWITCH=9011,1,1\r\n"); time.sleep(1); os.close(fd)'
sudo reboot
```
The auto-healing watchdog service will take care of everything after the reboot.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for development guidelines.

## License

Proprietary - DEHA Solutions
