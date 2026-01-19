# Development Guide

Hướng dẫn phát triển cho Mini PC Edge AI System.

---

## Project Structure

```
mini-pc/
├── src/
│   ├── ai_core/          # AI inference (TensorRT)
│   ├── features/         # Feature modules
│   ├── camera/           # Camera & streaming
│   ├── audio/            # Audio processing
│   ├── backend_client/   # Cloud connection
│   ├── storage/          # Local storage
│   ├── scheduler/        # Task scheduling
│   ├── security/         # Security & encryption
│   └── system/           # System management
├── config/               # Configuration
├── scripts/              # Utility scripts
├── tests/                # Test suites
├── docs/                 # Documentation
└── docker/               # Docker config
```

---

## Development Setup

### 1. Clone và setup
```bash
cd /data/projects
git clone <repo-url> mini-pc
cd mini-pc

# Virtual environment
python3 -m venv /data/venv/mini-pc
source /data/venv/mini-pc/bin/activate

# Dependencies
pip install -r requirements.txt
```

### 2. Configuration
```bash
# Copy config
cp config/.env.example config/.env

# Edit với credentials của bạn
nano config/.env
```

### 3. Run development server
```bash
python src/main.py
```

---

## Coding Standards

### Python Style
- PEP 8 compliance
- Type hints required
- Docstrings for all public functions
- Max line length: 100

### Tools
```bash
# Format
black src/

# Lint
ruff check src/

# Type check
mypy src/
```

---

## Git Workflow

### Branches
- `main` - Production
- `develop` - Development
- `feature/*` - New features
- `bugfix/*` - Bug fixes
- `release/*` - Release prep

### Commit Messages
```
feat: add person counting module
fix: camera reconnection timeout
docs: update setup guide
refactor: optimize detection pipeline
test: add unit tests for tracker
```

---

## Testing

```bash
# Unit tests
pytest tests/unit/

# Integration tests
pytest tests/integration/

# All tests with coverage
pytest --cov=src tests/
```

---

## Deployment

### Docker
```bash
# Build
docker build -t mini-pc:latest .

# Run
docker run -d --runtime nvidia \
  -v /data:/data \
  -p 8080:8080 \
  mini-pc:latest
```

### Systemd
```bash
# Install service
sudo cp docker/mini-pc.service /etc/systemd/system/
sudo systemctl enable mini-pc
sudo systemctl start mini-pc
```

---

## Debugging

### Local Web UI
```
http://localhost:8080
```

### Logs
```bash
# View logs
tail -f /data/mini-pc/logs/app.log

# System logs
journalctl -u mini-pc -f
```

### Performance
```bash
# GPU monitoring
jtop

# System stats
htop
```
