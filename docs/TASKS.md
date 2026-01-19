# Mini PC (Jetson Nano) - Task Breakdown

Tổng hợp các task cần thực hiện cho dự án Mini PC dựa trên schedule đã cung cấp.

---

## Phase 1: Thiết lập môi trường

> 📚 **Documentation**: [SETUP_GUIDE.md](./SETUP_GUIDE.md) | [PYTORCH_INSTALL.md](./PYTORCH_INSTALL.md)
> 🔧 **Scripts**: `scripts/setup.sh` | `scripts/check_system.sh`

### 1.1 Cài đặt JetPack SDK
- [/] Flash JetPack SDK (latest) lên Jetson Nano *(User đã cài)*
- [ ] Cấu hình GPIO/Camera interface
- [ ] Setup kết nối mạng (WiFi/4G/5G)
- [ ] Chuẩn bị storage (SD Card/NVMe) - Mount SSD vào /data
- [ ] Test basic functionality

### 1.2 Thiết lập môi trường development
- [ ] Setup Python 3.10+ environment
- [ ] Install Docker & Docker Compose
- [ ] Install OpenCV với CUDA support
- [ ] Setup Git & SSH keys
- [ ] Configure CI/CD tools
- [ ] Tạo project structure chuẩn


---

## Phase 2: Camera & Streaming

### 2.1 Module kết nối camera
- [ ] RTSP client implementation
- [ ] Multi-camera support
- [ ] Frame capture pipeline
- [ ] Health check mechanism
- [ ] Auto-reconnect logic
- [ ] Local recording (fallback)

### 2.2 Live streaming
- [ ] WebRTC server setup
- [ ] HLS streaming support
- [ ] Adaptive bitrate
- [ ] Multi-viewer support
- [ ] Stream authentication

---

## Phase 3: AI Core (Người khác phụ trách)

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

### 4.1 Đếm thành viên về nhà (home_monitoring)
- [ ] Face recognition & matching
- [ ] Person tracking vào/ra
- [ ] Entry/exit detection với ROI
- [ ] Daily counter với reset schedule
- [ ] Arrival notification qua LINE
- [ ] Lịch sử theo ngày/tuần/tháng

### 4.2 Đếm tổng lượng người qua (counting/people_flow)
- [ ] YOLO person detection
- [ ] De-duplication algorithm (tracking ID)
- [ ] Time-series analytics
- [ ] Daily stats & reset
- [ ] Visualization chart theo giờ

### 4.3 Cảnh báo người lạ (alerts/stranger)
- [ ] Face detection & extraction
- [ ] Matching against whitelist database
- [ ] Unknown face alert với snapshot
- [ ] Visitor log (time/duration)
- [ ] Confidence threshold tuning
- [ ] LINE emergency notification

### 4.4 Phát hiện động vật (alerts/animal)
- [ ] YOLO animal classes (dog, cat, bird, bear, snake, rat)
- [ ] Animal tracking & counting
- [ ] Pet vs wild classification
- [ ] Unwanted animal alert
- [ ] Activity timeline
- [ ] Integration với emergency rules

### 4.5 Đếm khách vào cửa hàng (counting/store)
- [ ] Entry zone ROI detection
- [ ] Person counting với de-duplication
- [ ] Entry/exit direction detection
- [ ] Time-based analytics (peak hours)
- [ ] Daily/weekly/monthly reports
- [ ] Heatmap visualization

### 4.6 Phân tích nhân khẩu học (demographics)
- [ ] Age estimation model
- [ ] Gender classification CNN
- [ ] Face detection preprocessing
- [ ] Demographics aggregation & statistics
- [ ] Dashboard charts (age groups, gender ratio, trends)
- [ ] Privacy compliance (không lưu ảnh)

### 4.7 Phát hiện hành vi bất thường (behavior_analysis)
- [ ] Loitering detection (dwell time tracking)
- [ ] Shoplifting pattern (pose estimation, hand-to-pocket)
- [ ] Running detection
- [ ] Anomaly detection (ML-based behavior analysis)
- [ ] Alert escalation theo severity
- [ ] Video clip evidence capture

### 4.8 Cảnh báo blacklist (alerts/blacklist)
- [ ] Face matching against blacklist database
- [ ] High-priority alert với ảnh
- [ ] Log entry attempts & location
- [ ] Integration với security staff notification
- [ ] Real-time tracking trong cửa hàng

### 4.9 Đếm người real-time (counting/realtime)
- [ ] YOLO person detection
- [ ] Real-time counting trong ROI
- [ ] Occupancy display overlay trên video
- [ ] Max capacity warning
- [ ] Evacuation support
- [ ] Heatmap density visualization

### 4.10 Phát hiện tình huống khẩn (alerts/emergency)
- [ ] Fall detection (pose estimation + motion analysis)
- [ ] Fight/violence detection (action recognition, skeleton tracking)
- [ ] Fire/smoke detection (YOLO custom class + color analysis)
- [ ] Emergency broadcast alert
- [ ] Multi-camera coordination
- [ ] Automatic emergency contact

---

## Phase 5: PPE Detection

### 5.1 Kiểm tra mũ bảo hiểm (ppe_detection/helmet)
- [ ] YOLO helmet class training
- [ ] Person without helmet detection
- [ ] Head region tracking
- [ ] Safety zone enforcement (restricted areas)
- [ ] Violation logging & reporting
- [ ] Compliance rate statistics
- [ ] Supervisor notification

### 5.2 Kiểm tra khẩu trang (ppe_detection/mask)
- [ ] YOLO mask detection model
- [ ] Face without mask detection
- [ ] Mask wearing verification
- [ ] Compliance rate statistics
- [ ] Time-series compliance tracking
- [ ] Non-compliance alert
- [ ] COVID-19 protocol enforcement

### 5.3 Kiểm tra găng tay (ppe_detection/gloves)
- [ ] Hand detection (MediaPipe/YOLO)
- [ ] Glove classification (binary: yes/no)
- [ ] Hand region tracking
- [ ] Safety zone enforcement (hazardous areas)
- [ ] Violation tracking & logging
- [ ] Integration với access control
- [ ] Real-time warning

---

## Phase 6: Audio Processing

### 6.1 Audio Hardware
- [ ] Kết nối mic & loa camera
- [ ] Audio input/output configuration

### 6.2 Audio Streaming
- [ ] Stream audio từ camera lên cloud
- [ ] Nhận audio từ mobile/web để phát
- [ ] Ghi âm khi có sự kiện
- [ ] Xử lý & cải thiện audio
- [ ] Đồng bộ cài đặt audio từ cloud
- [ ] Tối ưu cho audio thời gian thực

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
- [ ] Device pairing với user account
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
