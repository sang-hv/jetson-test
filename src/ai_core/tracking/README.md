# AI Core - Tracking Module

> **Owner**: AI Engineer (TBD)

## Purpose
Multi-object tracking and re-identification.

## Algorithms
- DeepSORT (recommended for accuracy)
- ByteTrack (lighter weight)
- Custom tracker options

## Features
- Track ID assignment
- Track lifecycle management
- Re-identification across camera gaps
- Direction detection (in/out)

## Interface
```python
class Tracker:
    def update(detections) -> List[Track]
    def get_active_tracks() -> List[Track]
    def get_direction(track_id) -> str  # 'IN', 'OUT', 'UNKNOWN'
```

## TODO
- [ ] DeepSORT implementation
- [ ] Track state management
- [ ] Line crossing detection
- [ ] Direction classification
