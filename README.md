# Mini PC - Jetson Nano AI Edge Computing System

AI-powered edge computing system for security monitoring, access control, and analytics, built on NVIDIA Jetson Orin Nano.

The system processes video streams locally with CUDA-accelerated AI models (YOLO + InsightFace), supports three facility modes (home/shop/enterprise), and communicates with the backend via Cloudflare tunnel for remote management and OTA updates.

## Hardware Requirements

- **NVIDIA Jetson Orin Nano** (8GB RAM)
- CSI Camera IMX219
- USB Microphone & Speaker (Jabra)
- SIM7600 LTE Module
- NVMe SSD 256GB

## Documentation

| | English | 日本語 |
|---|---------|--------|
| Architecture | [docs/en/architecture.md](docs/en/architecture.md) | [docs/ja/architecture.md](docs/ja/architecture.md) |
| Setup | [docs/en/setup.md](docs/en/setup.md) | [docs/ja/setup.md](docs/ja/setup.md) |

### Architecture

System architecture, core components (video pipeline, AI detection, logic service, streaming, networking, OTA, BLE OOBE), service reference, data flow diagrams, file system layout, and authentication.

### Setup

Installation guide, device provisioning, service management, network configuration, OTA updates, system cloning, backend sync, diagnostics, and troubleshooting.

## Quick Start

```bash
scp -r . user@jetson-ip:/home/user/mini-pc/
ssh user@jetson-ip
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh
sudo reboot
```

See [Setup documentation](docs/en/setup.md) for detailed instructions.
