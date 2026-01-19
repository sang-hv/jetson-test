# Home Monitoring Feature

## Purpose
Đếm thành viên gia đình về nhà, thông báo qua LINE.

## Features
- Face recognition & matching
- Person tracking vào/ra
- Entry/exit detection với ROI
- Daily counter với reset schedule
- Arrival notification qua LINE
- Lịch sử theo ngày/tuần/tháng

## Dependencies
- `ai_core.recognition` - Face detection & encoding
- `ai_core.tracking` - Person tracking
- `backend_client` - LINE notification

## TODO
- [ ] Face matching against family database
- [ ] Entry/exit ROI configuration
- [ ] Daily schedule reset logic
- [ ] LINE notification integration
- [ ] History storage & query
