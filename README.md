# Mini PC - Jetson Nano AI Edge Computing System

AI-powered edge computing system for security monitoring, access control, and analytics, built on NVIDIA Jetson Orin Nano.

The system processes video streams locally with CUDA-accelerated AI models (YOLO + InsightFace), supports three facility modes (home/shop/enterprise), and communicates with the backend via Cloudflare tunnel for remote management and OTA updates.

## Hardware Requirements

- **NVIDIA Jetson Orin Nano** (8GB RAM)
- Waveshare CSI Camera IMX219
- Waveshare USB Sound Card
- Waveshare SIM7600 LTE Module
- NVMe SSD 256GB

## Documentation

| | English | 日本語 | Tiếng Việt |
|---|---------|--------|------------|
| Architecture | [docs/en/architecture.md](docs/en/architecture.md) | [docs/ja/architecture.md](docs/ja/architecture.md) | - |
| Setup | [docs/en/setup.md](docs/en/setup.md) | [docs/ja/setup.md](docs/ja/setup.md) | [docs/vi/setup.md](docs/vi/setup.md) |

### Architecture

System architecture, core components (video pipeline, AI detection, logic service, streaming, networking, OTA, BLE OOBE), service reference, data flow diagrams, file system layout, and authentication.

### Setup

Step-by-step setup guide covering 3 phases: create camera on AIVIS Admin, create Cloudflare Tunnel, and install/configure the Jetson device. Includes screenshots for each step.

## Quick Start

See the [Setup Guide](docs/en/setup.md) for full instructions. Summary:

1. **Phase 1** — Create camera record on [AIVIS Admin](docs/en/setup.md#phase-1-create-camera-on-aivis-admin) and get Device Info
2. **Phase 2** — Create [Cloudflare Tunnel](docs/en/setup.md#phase-2-create-cloudflare-tunnel) with HTTP + SSH hostnames
3. **Phase 3** — Write image to SSD, assemble hardware, run setup:

```bash
sudo bash mini-pc/setup-firstboot/master-setup.sh --prompt-device-env --restart-all
```
