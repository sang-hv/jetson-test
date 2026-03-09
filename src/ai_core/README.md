# Face Recognition System

Real-time face recognition system that identifies familiar vs. unfamiliar people from a USB webcam.

**Features:**
- Person detection using YOLO v11 (ultralytics)
- Face recognition using InsightFace (ArcFace embeddings)
- Real-time tracking with ByteTrack
- Temporal smoothing to prevent label flickering
- Embedding cache for fast startup
- CPU and CUDA support

## Quick Start

### 1. Setup (macOS / Linux)

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Prepare Known Faces

Create a `known_faces` folder with subfolders for each person:

```
known_faces/
├── Alice/
│   ├── photo1.jpg
│   ├── photo2.jpg
│   └── photo3.png
├── Bob/
│   └── selfie.jpg
└── Carol/
    ├── portrait.jpg
    └── id_photo.png
```

**Tips for best results:**
- Use 2-5 clear face photos per person
- Include different angles and lighting
- Face should be clearly visible and unobscured
- Minimum resolution: 100x100 pixels for the face

### 3. Run

```bash
python main.py --source 0 --known_dir known_faces
```

**Keyboard controls:**
- `q` - Quit
- `r` - Refresh known faces database (after adding new photos)

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--source` | `0` | Video source: camera index, file path, or RTSP URL |
| `--known_dir` | `known_faces` | Directory with known face images |
| `--threshold` | `0.45` | Face matching threshold (0.0-1.0) |
| `--device` | `cpu` | Inference device: `cpu` or `cuda` |
| `--min_confirm_frames` | `3` | Frames needed to confirm identity |
| `--recognize_interval_ms` | `500` | Min ms between recognitions per track |
| `--person_conf` | `0.4` | Person detection confidence threshold |
| `--cam_width` | `1280` | Camera width (pixels) |
| `--cam_height` | `720` | Camera height (pixels) |
| `--cam_fps` | `30` | Camera FPS |

## Usage Examples

```bash
# Basic webcam usage
python main.py --source 0 --known_dir known_faces

# Stricter face matching (fewer false positives)
python main.py --source 0 --known_dir known_faces --threshold 0.5

# More lenient matching (poor lighting conditions)
python main.py --source 0 --known_dir known_faces --threshold 0.38

# Video file with CUDA acceleration
python main.py --source video.mp4 --known_dir known_faces --device cuda

# Lower resolution for faster processing on slow CPUs
python main.py --source 0 --known_dir known_faces --cam_width 640 --cam_height 480

# Longer recognition interval (less CPU usage)
python main.py --source 0 --known_dir known_faces --recognize_interval_ms 1000
```

## Threshold Tuning Guide

The `--threshold` parameter controls how strict face matching is:

| Threshold | Behavior | Use When |
|-----------|----------|----------|
| **0.50-0.60** | Very strict, fewer false positives | Security-critical applications |
| **0.45** | Balanced (default) | Most conditions |
| **0.35-0.40** | More lenient, may have some false positives | Poor lighting, low quality camera |

**How cosine similarity works:**

The system computes the similarity between face embeddings:
- `1.0` = Identical (never happens in practice)
- `0.5+` = Very likely same person
- `0.3-0.5` = Possibly same person
- `<0.3` = Different people

**Tuning process:**
1. Start with default `--threshold 0.45`
2. If known people are labeled "Unknown": lower threshold
3. If strangers are mislabeled as known: raise threshold
4. Re-run and repeat until satisfied

## Visualization

- **Green box**: Known person (recognized from database)
- **Red box**: Unknown person (not in database)
- **Yellow box**: Uncertain (not yet confirmed)
- Each box shows: `Name #track_id`
- Top-left overlay: FPS and person count

## How It Works

1. **Person Detection**: YOLO v11 detects all people in frame
2. **Tracking**: ByteTrack assigns persistent IDs to each person
3. **Face Recognition** (rate-limited per track):
   - Crop person bounding box
   - Run InsightFace face detection
   - Extract 512-dim ArcFace embedding
   - Compare against known face embeddings using cosine similarity
4. **Temporal Smoothing**: Require `min_confirm_frames` consistent results before confirming identity
5. **Visualization**: Draw colored boxes and labels

## Embedding Cache

On first run, the system extracts face embeddings from all images in `known_dir`. These are cached to:
- `known_dir/_embeddings_cache.npz` - Cached embeddings
- `known_dir/_cache_manifest.json` - Folder fingerprint

The cache is automatically invalidated when files are added, removed, or modified.

To force cache refresh:
- Press `r` while running, or
- Delete the cache files manually

## Troubleshooting

### "No face found in image"
- Ensure faces are clearly visible in reference photos
- Face should be at least 100x100 pixels
- Try photos with frontal face view

### "Known person labeled as Unknown"
- Lower the `--threshold` value (try 0.38-0.42)
- Add more reference photos with different angles/lighting
- Ensure reference photos are good quality

### "Unknown person labeled as known"
- Raise the `--threshold` value (try 0.48-0.55)
- Remove ambiguous reference photos

### Low FPS on CPU
- Reduce camera resolution: `--cam_width 640 --cam_height 480`
- Increase recognition interval: `--recognize_interval_ms 1000`
- The system is optimized for real-time but may be slower on older CPUs

### CUDA not detected
- Install `onnxruntime-gpu` instead of `onnxruntime`:
  ```bash
  pip uninstall onnxruntime
  pip install onnxruntime-gpu
  ```
- Ensure CUDA toolkit is installed

### Camera not opening
- Check camera index (try `--source 1` for second camera)
- On macOS, grant camera permissions to Terminal
- Verify camera works in other apps

## Project Structure

```
timima01/
├── main.py                 # CLI entry point
├── requirements.txt        # Dependencies
├── README.md              # This file
├── src/
│   ├── __init__.py
│   ├── detector.py        # YOLO person detection + ByteTrack
│   ├── tracker.py         # Track state + temporal smoothing
│   ├── recognizer.py      # InsightFace face recognition
│   ├── database.py        # Known faces + embedding cache
│   ├── pipeline.py        # Main orchestrator
│   └── utils.py           # Drawing utilities
└── known_faces/           # Your reference images
    ├── Person1/
    │   └── *.jpg
    └── Person2/
        └── *.png
```

## License

MIT License
