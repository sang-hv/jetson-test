#!/usr/bin/env python3
"""
Face Recognition System - Main Entry Point

Recognizes familiar vs. unfamiliar people from a USB webcam using
YOLO (person detection), ByteTrack (tracking), and InsightFace (ArcFace).

Usage:
    python main.py --source 0 --known_dir ./known_faces

Example with all options:
    python main.py \\
        --source 0 \\
        --known_dir ./known_faces \\
        --threshold 0.45 \\
        --device cpu \\
        --min_confirm_frames 3 \\
        --recognize_interval_ms 500 \\
        --person_conf 0.4

Keyboard controls:
    q - Quit
    r - Refresh known faces database
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Face Recognition System - Recognize familiar vs. unfamiliar people",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with webcam
  python main.py --source 0 --known_dir ./known_faces

  # With custom threshold (higher = stricter matching)
  python main.py --source 0 --known_dir ./known_faces --threshold 0.5

  # Video file input with CUDA
  python main.py --source video.mp4 --known_dir ./known_faces --device cuda

  # Lower camera resolution for faster processing
  python main.py --source 0 --known_dir ./known_faces --cam_width 640 --cam_height 480

Threshold Guide:
  The --threshold parameter controls face matching sensitivity.

  Default: 0.45 (balanced for most conditions)

  - Higher (0.50-0.60): Stricter matching, fewer false positives
    Use when: False matches are unacceptable

  - Lower (0.35-0.40): More lenient, may have some false positives
    Use when: Poor lighting, low camera quality, or different angles

  - Tune based on your camera/lighting conditions:
    1. Start with 0.45
    2. If known faces are labeled "Unknown", lower threshold
    3. If strangers are mislabeled as known, raise threshold
""",
    )

    # Required arguments
    parser.add_argument(
        "--known_dir",
        type=str,
        default="known_faces",
        help="Directory containing known face images organized by person. "
        "Structure: known_dir/PersonName/*.jpg (default: known_faces)",
    )

    # Video source
    parser.add_argument(
        "--source",
        type=str,
        default="0",
        help="Video source: camera index (0, 1), video file path, or RTSP URL "
        "(default: 0 for first camera)",
    )

    # Recognition parameters
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.45,
        help="Cosine similarity threshold for face matching (0.0-1.0). "
        "Higher = stricter. See --help for tuning guide. (default: 0.45)",
    )
    parser.add_argument(
        "--min_confirm_frames",
        type=int,
        default=3,
        help="Number of consistent recognition results required to confirm identity. "
        "Higher = more stable but slower to confirm. (default: 3)",
    )
    parser.add_argument(
        "--recognize_interval_ms",
        type=int,
        default=500,
        help="Minimum milliseconds between face recognition attempts per person. "
        "Higher = less CPU usage but slower response. (default: 500)",
    )

    # Detection parameters
    parser.add_argument(
        "--person_conf",
        type=float,
        default=0.4,
        help="Minimum confidence threshold for person detection (0.0-1.0). "
        "(default: 0.4)",
    )

    # Device selection
    parser.add_argument(
        "--device",
        type=str,
        choices=["cpu", "cuda"],
        default="cpu",
        help="Inference device: 'cpu' or 'cuda' for GPU. "
        "Falls back to CPU if CUDA unavailable. (default: cpu)",
    )

    # Camera settings (optional)
    parser.add_argument(
        "--cam_width",
        type=int,
        default=1280,
        help="Requested camera width in pixels. Actual may differ. (default: 1280)",
    )
    parser.add_argument(
        "--cam_height",
        type=int,
        default=720,
        help="Requested camera height in pixels. Actual may differ. (default: 720)",
    )
    parser.add_argument(
        "--cam_fps",
        type=int,
        default=30,
        help="Requested camera FPS. Actual may differ. (default: 30)",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Check known_dir but don't block - just warn
    path = Path(args.known_dir)
    if not path.exists():
        print(f"[INFO] Known faces directory not found: {args.known_dir}")
        print("[INFO] Running in detection-only mode (all persons will be 'Unknown')")
        print(f"[INFO] To add known faces, create: {args.known_dir}/PersonName/*.jpg\n")
    elif not any(d.is_dir() and not d.name.startswith("_") for d in path.iterdir()):
        print(f"[INFO] No person folders in {args.known_dir}")
        print("[INFO] Running in detection-only mode (all persons will be 'Unknown')\n")

    # Import here to avoid slow import if validation fails
    try:
        from src.pipeline import Config, Pipeline
    except ImportError as e:
        print(f"ERROR: Failed to import modules: {e}")
        print("\nEnsure dependencies are installed:")
        print("  pip install -r requirements.txt")
        return 1

    try:
        # Create configuration
        config = Config.from_args(args)

        # Create and run pipeline
        pipeline = Pipeline(config)
        pipeline.run()

        return 0

    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 1
    except ValueError as e:
        print(f"ERROR: Configuration error: {e}")
        return 1
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 0
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
