# AI Core - Analytics Module

> **Owner**: AI Engineer (TBD)

## Purpose
Behavior analysis and action recognition.

## Components

### Pose Estimation
- MediaPipe or custom model
- Skeleton keypoints detection

### Action Recognition
- Fall detection
- Violence detection
- Loitering detection

### Anomaly Detection
- Behavior baseline learning
- Deviation scoring

## Interface
```python
class BehaviorAnalyzer:
    def estimate_pose(frame) -> List[Skeleton]
    def detect_action(track, frames) -> str
    def detect_anomaly(track, history) -> float
```

## TODO
- [ ] Pose estimation model
- [ ] Action classification
- [ ] Anomaly scoring algorithm
- [ ] Temporal analysis
