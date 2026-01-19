# Demographics Analysis Feature

## Purpose
Phân tích nhân khẩu học khách hàng.

## Features
- Age estimation model
- Gender classification
- Face detection preprocessing
- Demographics aggregation
- Dashboard charts (age groups, gender ratio, trends)
- Privacy compliance (không lưu ảnh)

## Dependencies
- `ai_core.recognition` - Face detection, age/gender estimation
- `storage` - Statistics storage

## Privacy
- Chỉ lưu thống kê aggregate
- Không lưu ảnh gốc
- Xử lý real-time, không buffer

## TODO
- [ ] Age estimation integration
- [ ] Gender classification
- [ ] Statistics aggregation
- [ ] Dashboard API
