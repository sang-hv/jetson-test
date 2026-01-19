# Counting Features

## Modules

### people_flow/
Đếm tổng lượng người qua camera.
- YOLO person detection
- De-duplication (tracking ID)
- Time-series analytics
- Hourly charts

### store/
Đếm khách vào cửa hàng.
- Entry zone ROI
- Direction detection
- Peak hours analysis
- Heatmap visualization

### realtime/
Đếm người real-time.
- Live occupancy display
- Max capacity warning
- Evacuation support
- Density heatmap

## Dependencies
- `ai_core.detection` - Person detection
- `ai_core.tracking` - De-duplication
- `storage` - Data persistence
