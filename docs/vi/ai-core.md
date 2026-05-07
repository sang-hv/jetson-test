# Cài đặt AI Core

Hướng dẫn chạy thử pipeline AI Core và chuyển đổi (export) model YOLO từ định dạng `.pt` sang `.engine` (TensorRT) để tăng tốc inference trên Jetson Orin Nano.

| Mục | Mô tả |
|-----|-------|
| [Chạy test AI Core](#chạy-test-ai-core) | Chạy pipeline nhận diện khuôn mặt từ webcam |
| [Export model `.pt` → `.engine`](#export-model-pt--engine) | Convert YOLO `.pt` sang TensorRT `.engine` (FP16/INT8) |

---

## Chạy test AI Core

Pipeline nhận diện người (YOLO) + tracking (ByteTrack) + nhận diện khuôn mặt (InsightFace).

### Chuẩn bị

```bash
cd src/ai_core

# Tạo virtual environment
python3 -m venv venv
source venv/bin/activate

# Cài đặt dependencies
pip install -r requirements.txt
```

Tạo thư mục `known_faces/` chứa ảnh tham chiếu, mỗi người 1 thư mục con:

```
known_faces/
├── Alice/
│   ├── photo1.jpg
│   └── photo2.jpg
└── Bob/
    └── selfie.jpg
```

### Lệnh chạy test

```bash
# Chạy cơ bản với webcam (camera index 0)
python main.py --source 0 --known_dir known_faces
```

Một số ví dụ khác:

```bash
# Chạy với CUDA (Jetson)
python main.py --source 0 --known_dir known_faces --device cuda

# Chạy với video file
python main.py --source video.mp4 --known_dir known_faces --device cuda

# Tinh chỉnh threshold matching khuôn mặt (0.0 - 1.0, mặc định 0.45)
python main.py --source 0 --known_dir known_faces --threshold 0.5

# Giảm độ phân giải để chạy nhanh hơn trên CPU
python main.py --source 0 --known_dir known_faces --cam_width 640 --cam_height 480
```

Phím điều khiển:
- `q` — Thoát
- `r` — Reload database known faces (sau khi thêm ảnh mới)

Xem chi tiết các option còn lại tại [src/ai_core/README.md](../../src/ai_core/README.md).

---

## Export model `.pt` → `.engine`

TensorRT engine chỉ tương thích với **đúng kiến trúc GPU đã build**, vì vậy **bắt buộc phải chạy lệnh export ngay trên Jetson Orin Nano** (thiết bị đích). Engine build trên máy khác (PC, máy chủ, Jetson đời khác) sẽ **không chạy được**.

Script: [src/ai_core/export_tensorrt.py](../../src/ai_core/export_tensorrt.py)

### Các lệnh export

```bash
cd src/ai_core

# Export tất cả model mặc định trong pipeline (FP16)
python export_tensorrt.py

# Export 1 model cụ thể
python export_tensorrt.py --models yolo11l.pt

# Export với custom image size
python export_tensorrt.py --models yolo11l.pt --imgsz 640

# Export với INT8 quantization (cần dataset để calibrate)
python export_tensorrt.py --models yolo11l.pt --int8 --data coco128.yaml
```

### Các tham số

| Tham số | Mặc định | Mô tả |
|---------|----------|-------|
| `--models` | `yolo11l.pt`, `yolo11l-pose.pt`, `yolov8n-face-mask.pt`, `yolov8m-protective-equipment-detection.pt` | Danh sách file `.pt` cần export |
| `--imgsz` | `640` | Kích thước ảnh inference (vuông) |
| `--int8` | `False` | Bật INT8 quantization (cần `--data` để calibrate) |
| `--data` | `None` | File yaml dataset dùng cho calibrate INT8 (vd: `coco128.yaml`) |
| `--workspace` | `4` | Workspace TensorRT (GB) |

### Sau khi export xong

File `.engine` được tạo cùng thư mục với file `.pt` tương ứng. Cập nhật lại config / `.env` để pipeline dùng engine thay cho `.pt`:

```bash
YOLO_MODEL=yolo11l.engine
MASK_MODEL_PATH=yolov8n-face-mask.engine
PPE_MODEL_PATH=yolov8m-protective-equipment-detection.engine
```

> Lưu ý: nếu file `.engine` đã tồn tại, script sẽ skip để tránh build lại. Xoá file cũ nếu muốn build lại từ đầu.
