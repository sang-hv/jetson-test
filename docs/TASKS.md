# Mini PC (Jetson Nano) - Task Breakdown

Comprehensive task list for the Mini PC project based on the provided schedule.

---

## Phase 1: Environment Setup

> 📚 **Documentation**: [SETUP_GUIDE.md](./SETUP_GUIDE.md) | [PYTORCH_INSTALL.md](./PYTORCH_INSTALL.md)
> 🔧 **Scripts**: `scripts/setup.sh` | `scripts/check_system.sh`

### 1.1 JetPack SDK Installation
- [/] Flash JetPack SDK (latest) to Jetson Nano *(User completed)*
- [ ] Configure GPIO/Camera interface
- [ ] Setup network connection (WiFi/4G/5G)
- [ ] Prepare storage (SD Card/NVMe) - Mount SSD to /data
- [ ] Test basic functionality

### 1.2 Development Environment Setup
- [ ] Setup Python 3.10+ environment
- [ ] Install Docker & Docker Compose
- [ ] Install OpenCV with CUDA support
- [ ] Setup Git & SSH keys
- [ ] Configure CI/CD tools
- [ ] Create standard project structure

---

## Phase 2: Camera & Streaming

### 2.1 Camera Connection Module
- [ ] RTSP client implementation
- [ ] Multi-camera support
- [ ] Frame capture pipeline
- [ ] Health check mechanism
- [ ] Auto-reconnect logic
- [ ] Local recording (fallback)

### 2.2 Live Streaming
- [ ] WebRTC server setup
- [ ] HLS streaming support
- [ ] Adaptive bitrate
- [ ] Multi-viewer support
- [ ] Stream authentication

---

## Phase 3: AI Core (Separate Developer)

### 3.1 Detection Models
- [ ] YOLO v11 integration
- [ ] TensorRT optimization
- [ ] FP16 inference
- [ ] Batch processing

### 3.2 Face Recognition
- [ ] Face detection (SCRFD/RetinaFace)
- [ ] Face encoding (ArcFace)
- [ ] Face matching algorithm

### 3.3 Tracking
- [ ] Person tracking (DeepSORT)
- [ ] Multi-object tracking
- [ ] Re-identification

---

## Phase 4: Feature Modules

### 4.1 Family Member Arrival Detection (home_monitoring)
- [ ] Face recognition & matching
- [ ] Person tracking in/out
- [ ] Entry/exit detection with ROI
- [ ] Daily counter with reset schedule
- [ ] Arrival notification via LINE
- [ ] History by day/week/month

### 4.2 People Flow Counting (counting/people_flow)
- [ ] YOLO person detection
- [ ] De-duplication algorithm (tracking ID)
- [ ] Time-series analytics
- [ ] Daily stats & reset
- [ ] Hourly visualization chart

### 4.3 Stranger Alert (alerts/stranger)
- [ ] Face detection & extraction
- [ ] Matching against whitelist database
- [ ] Unknown face alert with snapshot
- [ ] Visitor log (time/duration)
- [ ] Confidence threshold tuning
- [ ] LINE emergency notification

### 4.4 Animal Detection (alerts/animal)
- [ ] YOLO animal classes (dog, cat, bird, bear, snake, rat)
- [ ] Animal tracking & counting
- [ ] Pet vs wild classification
- [ ] Unwanted animal alert
- [ ] Activity timeline
- [ ] Integration with emergency rules

### 4.5 Store Customer Counting (counting/store)
- [ ] Entry zone ROI detection
- [ ] Person counting with de-duplication
- [ ] Entry/exit direction detection
- [ ] Time-based analytics (peak hours)
- [ ] Daily/weekly/monthly reports
- [ ] Heatmap visualization

### 4.6 Demographics Analysis (demographics)
- [ ] Age estimation model
- [ ] Gender classification CNN
- [ ] Face detection preprocessing
- [ ] Demographics aggregation & statistics
- [ ] Dashboard charts (age groups, gender ratio, trends)
- [ ] Privacy compliance (no image storage)

### 4.7 Abnormal Behavior Detection (behavior_analysis)
- [ ] Loitering detection (dwell time tracking)
- [ ] Shoplifting pattern (pose estimation, hand-to-pocket)
- [ ] Running detection
- [ ] Anomaly detection (ML-based behavior analysis)
- [ ] Alert escalation by severity
- [ ] Video clip evidence capture

### 4.8 Blacklist Alert (alerts/blacklist)
- [ ] Face matching against blacklist database
- [ ] High-priority alert with image
- [ ] Log entry attempts & location
- [ ] Integration with security staff notification
- [ ] Real-time tracking in store

### 4.9 Real-time People Counting (counting/realtime)
- [ ] YOLO person detection
- [ ] Real-time counting in ROI
- [ ] Occupancy display overlay on video
- [ ] Max capacity warning
- [ ] Evacuation support
- [ ] Heatmap density visualization

### 4.10 Emergency Situation Detection (alerts/emergency)
- [ ] Fall detection (pose estimation + motion analysis)
- [ ] Fight/violence detection (action recognition, skeleton tracking)
- [ ] Fire/smoke detection (YOLO custom class + color analysis)
- [ ] Emergency broadcast alert
- [ ] Multi-camera coordination
- [ ] Automatic emergency contact

---

## Phase 5: PPE Detection

### 5.1 Helmet Detection (ppe_detection/helmet)
- [ ] YOLO helmet class training
- [ ] Person without helmet detection
- [ ] Head region tracking
- [ ] Safety zone enforcement (restricted areas)
- [ ] Violation logging & reporting
- [ ] Compliance rate statistics
- [ ] Supervisor notification

### 5.2 Mask Detection (ppe_detection/mask)
- [ ] YOLO mask detection model
- [ ] Face without mask detection
- [ ] Mask wearing verification
- [ ] Compliance rate statistics
- [ ] Time-series compliance tracking
- [ ] Non-compliance alert
- [ ] COVID-19 protocol enforcement

### 5.3 Gloves Detection (ppe_detection/gloves)
- [ ] Hand detection (MediaPipe/YOLO)
- [ ] Glove classification (binary: yes/no)
- [ ] Hand region tracking
- [ ] Safety zone enforcement (hazardous areas)
- [ ] Violation tracking & logging
- [ ] Integration with access control
- [ ] Real-time warning

---

## Phase 6: Audio Processing

### 6.1 Audio Hardware
- [ ] Connect camera mic & speaker
- [ ] Audio input/output configuration

### 6.2 Audio Streaming
- [ ] Stream audio from camera to cloud
- [ ] Receive audio from mobile/web to play
- [ ] Record audio on events
- [ ] Audio processing & enhancement
- [ ] Sync audio settings from cloud
- [ ] Optimize for real-time audio

---

## Phase 7: Backend Integration

### 7.1 Event Management
- [ ] Local queue (offline support)
- [ ] Deduplication
- [ ] Snapshot capture
- [ ] Video clip recording
- [ ] Priority queue
- [ ] Retry mechanism

### 7.2 Backend Connection
- [ ] WebSocket client
- [ ] REST API client
- [ ] Event/media upload
- [ ] Config sync
- [ ] Remote control
- [ ] Heartbeat service
- [ ] Offline mode handling

### 7.3 Face Database Management
- [ ] Face enrollment (embeddings)
- [ ] Cloud sync
- [ ] Local cache
- [ ] Whitelist/blacklist management
- [ ] CRUD operations

---

## Phase 8: Storage & Scheduling

### 8.1 Storage Management
- [ ] Local storage management
- [ ] Auto cleanup
- [ ] Circular buffer
- [ ] Media indexing (SQLite)
- [ ] Export function
- [ ] Disk space alert

### 8.2 Scheduling Engine
- [ ] Schedule engine (cron-like)
- [ ] 3 modes (Home/Away/Sleep)
- [ ] Auto/manual switching
- [ ] Per-rule scheduling
- [ ] Holiday calendar

---

## Phase 9: Setup & Security

### 9.1 First-time Setup
- [ ] QR code generation
- [ ] WiFi config via BLE/AP
- [ ] Device pairing with user account
- [ ] Setup wizard
- [ ] Network config UI

### 9.2 Security
- [ ] Device authentication (certificate)
- [ ] Data encryption
- [ ] Secure credentials storage
- [ ] SSL/TLS implementation
- [ ] Privacy mode
- [ ] Tamper detection

---

## Phase 10: System Management

### 10.1 System Monitoring
- [ ] CPU/GPU/RAM monitoring
- [ ] Temperature monitoring
- [ ] Network bandwidth
- [ ] Disk usage
- [ ] Service watchdog
- [ ] Health metrics reporting

### 10.2 Remote Updates (OTA)
- [ ] OTA client
- [ ] Version management
- [ ] Rollback mechanism
- [ ] Update scheduling
- [ ] Progress reporting
- [ ] A/B partition support

### 10.3 Debug Tools
- [ ] Local web UI (port 8080)
- [ ] Log management
- [ ] Remote log upload
- [ ] Performance dashboard
- [ ] Mock mode
- [ ] Test data generator

---

## Phase 11: Deployment

### 11.1 Packaging
- [ ] Docker containerization
- [ ] systemd service
- [ ] Auto-start on boot
- [ ] Backup/restore
- [ ] Factory reset
- [ ] Documentation

### 11.2 Quality Assurance
- [ ] Unit tests
- [ ] Integration tests
- [ ] Stress testing (24/7)
- [ ] Network failure simulation
- [ ] Load testing
- [ ] E2E testing

---

## Priority Order (Recommended)

1. **Phase 1** - Environment setup (Critical path)
2. **Phase 2** - Camera module (Foundation)
3. **Phase 3** - AI Core (Parallel development)
4. **Phase 7.2** - Backend connection (Integration)
5. **Phase 4.1-4.3** - Core features (Home monitoring, Counting, Alerts)
6. **Phase 6** - Audio (Enhancement)
7. **Phase 4.4-4.10** - Advanced features
8. **Phase 5** - PPE Detection
9. **Phase 8-10** - System management
10. **Phase 11** - Deployment & QA
