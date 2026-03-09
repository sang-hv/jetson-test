"""Test script for YOLO mask detection model."""
import cv2
from pathlib import Path

# Find the model file
model_paths = [
    "yolov8n-face-mask.pt",
    "best.pt",
    "face_mask_detect.pt",
    "runs/detect/train/weights/best.pt",
]

model_path = None
for p in model_paths:
    if Path(p).exists():
        model_path = p
        break

if model_path is None:
    print("ERROR: No mask detection model found!")
    print("Please download or train a YOLO mask detection model.")
    print("\nSearched paths:")
    for p in model_paths:
        print(f"  - {p}")
    print("\nYou can train a model using train_mask_model.py")
    exit(1)

print(f"Loading model: {model_path}")

from src.mask_detector import MaskDetector

# Initialize detector
detector = MaskDetector(
    model_path=model_path,
    device="cpu",
    conf_threshold=0.5,
)

if not detector.is_enabled:
    print("ERROR: Failed to load model!")
    exit(1)

print(f"\nModel enabled: {detector.is_enabled}")

# Test with webcam
print("\n" + "=" * 50)
print("Testing with webcam (press 'q' to quit)")
print("=" * 50)

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Cannot open webcam")
    exit(1)

while True:
    ret, frame = cap.read()
    if not ret:
        break

    # Detect mask
    is_masked, confidence = detector.detect(frame)

    # Draw result
    if is_masked is True:
        text = f"MASK: {confidence:.2f}"
        color = (0, 255, 0)  # Green
    elif is_masked is False:
        text = f"NO MASK: {confidence:.2f}"
        color = (0, 0, 255)  # Red
    else:
        text = "No face detected"
        color = (128, 128, 128)  # Gray

    # Draw info
    cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

    cv2.imshow("YOLO Mask Detection Test", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
print("\nDone!")
