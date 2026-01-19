# PPE Detection Features

## Modules

### helmet/
Kiểm tra mũ bảo hiểm.
- YOLO helmet detection
- Safety zone enforcement
- Violation logging
- Compliance statistics

### mask/
Kiểm tra khẩu trang.
- YOLO mask detection
- Compliance rate tracking
- Non-compliance alert
- COVID-19 protocol

### gloves/
Kiểm tra găng tay.
- Hand detection (MediaPipe/YOLO)
- Glove classification
- Hazardous area enforcement
- Access control integration

## Dependencies
- `ai_core.detection` - PPE detection models
- `ai_core.tracking` - Person tracking
- `backend_client` - Supervisor notification
