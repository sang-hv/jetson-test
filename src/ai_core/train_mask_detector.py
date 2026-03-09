"""
Train YOLOv11 Face Mask Detection Model

Steps:
1. Download dataset from Roboflow
2. Train YOLOv11 on the dataset
3. Export model for use with MaskDetector

Usage:
    python train_mask_detector.py

Requirements:
    pip install roboflow ultralytics
"""

import os
from pathlib import Path


def download_dataset():
    """Download Face Mask Detection dataset from Roboflow."""
    try:
        from roboflow import Roboflow
    except ImportError:
        print("Installing roboflow...")
        os.system("pip install roboflow")
        from roboflow import Roboflow

    print("=" * 60)
    print("Downloading Face Mask Detection Dataset from Roboflow")
    print("=" * 60)

    # Use public dataset - no API key needed for public datasets
    # Dataset: Face Mask Detection
    # Source: https://universe.roboflow.com/yolov8-cthlv/face-mask-detection-x1fre
    rf = Roboflow()
    project = rf.universe("yolov8-cthlv").project("face-mask-detection-x1fre")
    version = project.version(1)
    dataset = version.download("yolov11")

    print(f"Dataset downloaded to: {dataset.location}")
    return dataset.location


def train_model(dataset_path: str, epochs: int = 50):
    """Train YOLOv11 on the dataset."""
    from ultralytics import YOLO

    print("=" * 60)
    print("Training YOLOv11 Face Mask Detection Model")
    print("=" * 60)

    # Load YOLOv11 nano model (smallest, fastest)
    model = YOLO("yolo11n.pt")

    # Train
    data_yaml = Path(dataset_path) / "data.yaml"
    if not data_yaml.exists():
        # Try alternative path
        data_yaml = Path(dataset_path).parent / "data.yaml"

    print(f"Training with: {data_yaml}")

    results = model.train(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=640,
        batch=16,
        name="face_mask_yolo11n",
        project="runs/mask_detection",
        exist_ok=True,
        patience=10,  # Early stopping
        device="cpu",  # Use "0" for GPU
    )

    return results


def export_model():
    """Export trained model to project directory."""
    from pathlib import Path
    import shutil

    # Find best model
    best_model = Path("runs/mask_detection/face_mask_yolo11n/weights/best.pt")

    if best_model.exists():
        # Copy to project root
        output_path = Path("yolov11n-face-mask.pt")
        shutil.copy(best_model, output_path)
        print(f"\nModel exported to: {output_path.absolute()}")
        print("\nYou can now run the face recognition system with mask detection!")
        return str(output_path)
    else:
        print(f"Model not found at: {best_model}")
        return None


def main():
    print("\n" + "=" * 60)
    print("YOLOv11 Face Mask Detection Training")
    print("=" * 60 + "\n")

    # Step 1: Download dataset
    print("\n[Step 1/3] Downloading dataset...")
    try:
        dataset_path = download_dataset()
    except Exception as e:
        print(f"Error downloading dataset: {e}")
        print("\nAlternative: Manual download")
        print("1. Go to: https://universe.roboflow.com/yolov8-cthlv/face-mask-detection-x1fre")
        print("2. Click 'Download Dataset' > Select 'YOLOv11' format")
        print("3. Extract to ./datasets/face-mask-detection/")
        return

    # Step 2: Train model
    print("\n[Step 2/3] Training model...")
    print("This may take 10-30 minutes on CPU...")
    train_model(dataset_path, epochs=30)

    # Step 3: Export model
    print("\n[Step 3/3] Exporting model...")
    export_model()

    print("\n" + "=" * 60)
    print("Training Complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Run: python main.py --source 0 --known_dir known_faces")
    print("2. Mask detection should now work!")


if __name__ == "__main__":
    main()
