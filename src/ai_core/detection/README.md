# AI Core - Detection Module

> **Owner**: AI Engineer (TBD)

## Purpose
Object detection using YOLO v11 with TensorRT acceleration.

## Models
- `yolov11n.engine` - Nano (fastest)
- `yolov11s.engine` - Small (balanced)
- `yolov11m.engine` - Medium (accurate)

## Classes
- Person (0)
- Animals: dog (16), cat (15), bird (14), bear (21)
- Fire/Smoke (custom trained)

## Interface
```python
class Detector:
    def detect(frame) -> List[Detection]
    def detect_batch(frames) -> List[List[Detection]]
```

## TODO
- [ ] Model conversion (ONNX -> TensorRT)
- [ ] Benchmark on Jetson Nano
- [ ] FP16 optimization
