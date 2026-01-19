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

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for development guidelines.

## License

Proprietary - DEHA Solutions
