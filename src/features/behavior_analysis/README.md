# Behavior Analysis Feature

## Purpose
Detect abnormal behavior patterns.

## Features
- Loitering detection (dwell time tracking)
- Shoplifting pattern (pose + hand-to-pocket)
- Running detection
- Anomaly detection (ML-based)
- Alert escalation by severity
- Video clip evidence capture

## Dependencies
- `ai_core.detection` - Person detection
- `ai_core.tracking` - Tracking & dwell time
- `ai_core.analytics` - Pose estimation, action recognition
- `storage` - Video clip recording

## TODO
- [ ] Dwell time logic
- [ ] Pose-based behavior detection
- [ ] Anomaly scoring algorithm
- [ ] Evidence capture mechanism
- [ ] Severity-based alerting
