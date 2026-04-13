"""
Export YOLO .pt models to TensorRT FP16 .engine format.

Run this script ON THE JETSON ORIN NANO (the target device) because
TensorRT engines are hardware-specific — an engine built on one GPU
architecture will NOT work on another.

Usage:
    # Export all models used in the pipeline
    python export_tensorrt.py

    # Export a specific model
    python export_tensorrt.py --models yolo11l.pt

    # Export with custom image size
    python export_tensorrt.py --models yolo11l.pt --imgsz 640

    # Export with INT8 quantization (needs calibration images)
    python export_tensorrt.py --models yolo11l.pt --int8 --data coco128.yaml
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ultralytics import YOLO

DEFAULT_MODELS = [
    "yolo11l.pt",
    "yolov8n-face-mask.pt",
    "yolov8m-protective-equipment-detection.pt",
]


def export_model(
    model_path: str,
    imgsz: int = 640,
    half: bool = True,
    int8: bool = False,
    workspace: int = 4,
    data: str | None = None,
) -> str | None:
    """Export a single YOLO model to TensorRT engine.

    Args:
        model_path: Path to .pt model file.
        imgsz: Inference image size (square).
        half: Use FP16 precision.
        int8: Use INT8 quantization (requires ``data`` for calibration).
        workspace: TensorRT workspace size in GB.
        data: Dataset yaml for INT8 calibration (e.g. "coco128.yaml").

    Returns:
        Path to exported .engine file, or None on failure.
    """
    pt = Path(model_path)
    if not pt.exists():
        print(f"[SKIP] Model not found: {pt}")
        return None

    engine_path = pt.with_suffix(".engine")
    if engine_path.exists():
        print(f"[SKIP] Engine already exists: {engine_path}")
        return str(engine_path)

    print("=" * 60)
    print(f"Exporting: {pt}")
    print(f"  imgsz    : {imgsz}")
    print(f"  precision: {'INT8' if int8 else 'FP16' if half else 'FP32'}")
    print(f"  workspace: {workspace} GB")
    print("=" * 60)

    model = YOLO(str(pt))

    t0 = time.time()
    export_kwargs: dict = dict(
        format="engine",
        imgsz=imgsz,
        half=half,
        int8=int8,
        workspace=workspace,
        simplify=True,
        device=0,
    )
    if int8 and data:
        export_kwargs["data"] = data

    result = model.export(**export_kwargs)
    elapsed = time.time() - t0

    print(f"Done in {elapsed:.0f}s  ->  {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export YOLO .pt models to TensorRT FP16 engines"
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help=f"Model .pt files to export (default: {DEFAULT_MODELS})",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Inference image size (default: 640)",
    )
    parser.add_argument(
        "--int8",
        action="store_true",
        help="Use INT8 quantization instead of FP16 (needs --data)",
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Dataset yaml for INT8 calibration (e.g. coco128.yaml)",
    )
    parser.add_argument(
        "--workspace",
        type=int,
        default=4,
        help="TensorRT workspace size in GB (default: 4)",
    )
    args = parser.parse_args()

    if args.int8 and not args.data:
        print("WARNING: --int8 requires --data for calibration. Falling back to FP16.")
        args.int8 = False

    print(f"Models to export: {args.models}")
    print()

    results = {}
    for model_path in args.models:
        engine = export_model(
            model_path=model_path,
            imgsz=args.imgsz,
            half=not args.int8,
            int8=args.int8,
            workspace=args.workspace,
            data=args.data,
        )
        results[model_path] = engine

    print()
    print("=" * 60)
    print("Export Summary")
    print("=" * 60)
    for pt, engine in results.items():
        status = engine if engine else "FAILED / NOT FOUND"
        print(f"  {pt:<50s} -> {status}")
    print()
    print("Next steps:")
    print("  1. Update .env or pipeline config to use .engine files")
    print("  2. Example: YOLO_MODEL=yolo11l.engine")
    print("  3. Mask:    MASK_MODEL_PATH=yolov8n-face-mask.engine")
    print("  4. PPE:     PPE_MODEL_PATH=yolov8m-protective-equipment-detection.engine")


if __name__ == "__main__":
    main()
