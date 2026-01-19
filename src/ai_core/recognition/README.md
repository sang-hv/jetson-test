# AI Core - Recognition Module

> **Owner**: AI Engineer (TBD)

## Purpose
Face recognition and demographic analysis.

## Components

### Face Detection
- SCRFD 2.5g or RetinaFace
- Output: face bounding boxes + landmarks

### Face Encoding
- ArcFace MobileFaceNet
- Output: 512-dim embedding vector

### Age/Gender Estimation
- Lightweight CNN models
- Output: age range, gender probability

## Interface
```python
class FaceRecognizer:
    def detect_faces(frame) -> List[Face]
    def encode_face(face_image) -> np.ndarray  # 512-dim
    def estimate_age_gender(face_image) -> (int, str)
```

## TODO
- [ ] Face detection model setup
- [ ] ArcFace TensorRT conversion
- [ ] Age/Gender model integration
- [ ] Face alignment preprocessing
