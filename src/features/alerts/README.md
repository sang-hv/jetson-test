# Alerts Features

## Modules

### stranger/
Cảnh báo người lạ.
- Face matching vs whitelist
- Unknown face alert + snapshot
- Visitor log
- LINE notification

### animal/
Phát hiện động vật.
- YOLO animal classes
- Pet vs wild classification
- Unwanted animal alert

### blacklist/
Cảnh báo blacklist.
- Face matching vs blacklist
- High-priority alert
- Real-time tracking

### emergency/
Phát hiện tình huống khẩn.
- Fall detection
- Fight/violence detection
- Fire/smoke detection
- Emergency broadcast

## Dependencies
- `ai_core.detection` - Object detection
- `ai_core.recognition` - Face matching
- `ai_core.analytics` - Behavior analysis
- `backend_client` - Notifications
