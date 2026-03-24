"""
Utility functions for drawing, FPS counting, and common helpers.
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Tuple

import cv2
import numpy as np

if TYPE_CHECKING:
    from .detector import TrackedAnimal, TrackedPerson


class FPSCounter:
    """Smoothed FPS counter using moving average."""

    def __init__(self, window_size: int = 30):
        """
        Initialize FPS counter.

        Args:
            window_size: Number of frames for moving average
        """
        self.window_size = window_size
        self.timestamps: deque = deque(maxlen=window_size)

    def update(self) -> float:
        """
        Update with current timestamp and return smoothed FPS.

        Returns:
            Current FPS estimate
        """
        current_time = time.time()
        self.timestamps.append(current_time)

        if len(self.timestamps) < 2:
            return 0.0

        time_diff = self.timestamps[-1] - self.timestamps[0]
        if time_diff <= 0:
            return 0.0

        return (len(self.timestamps) - 1) / time_diff

    def reset(self) -> None:
        """Reset the FPS counter."""
        self.timestamps.clear()


def is_known_label(label: str) -> bool:
    """
    Check if a label represents a known person.

    Args:
        label: The label to check

    Returns:
        True if the label is for a known person
    """
    return label != "Unknown" and not label.endswith("?")


def get_bbox_color(label: str) -> Tuple[int, int, int]:
    """
    Get bounding box color based on label (BGR format).

    - Known person: Green (0, 255, 0)
    - Unknown: Red (0, 0, 255)
    - Uncertain (ends with ?): Yellow (0, 255, 255)

    Args:
        label: Person label/name

    Returns:
        BGR color tuple
    """
    if label == "Unknown":
        return (0, 0, 255)  # Red for unknown
    elif label.endswith("?"):
        return (0, 255, 255)  # Yellow for uncertain
    else:
        return (0, 255, 0)  # Green for known


def draw_tracked_person(
    frame: np.ndarray,
    person: TrackedPerson,
    label: str,
    mask_status: bool = None,
    age: int = None,
    gender: str = None,
    helmet_status: bool = None,
    glove_status: bool = None,
    thickness: int = 2,
    font_scale: float = 0.7,
) -> np.ndarray:
    """
    Draw bounding box and label for tracked person.

    - Known person: Green bbox with name
    - Unknown: Red bbox with "Unknown"
    - Uncertain: Yellow bbox with name + "?"

    Args:
        frame: Image to draw on (modified in place)
        person: TrackedPerson object with bbox and track_id
        label: Label to display
        mask_status: True if wearing mask, False if not, None if unknown
        age: Detected age or None
        gender: Gender string ("M" or "F") or None
        helmet_status: True if wearing helmet, False if not, None if unknown
        glove_status: True if wearing glove, False if not, None if unknown
        thickness: Line thickness
        font_scale: Font scale for text

    Returns:
        Annotated frame
    """
    x1, y1, x2, y2 = person.bbox.astype(int)
    color = get_bbox_color(label)

    # Draw bounding box
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

    # Prepare label text with track ID
    main_text = f"{label} #{person.track_id}"

    # Add age/gender if available
    age_gender_text = ""
    if age is not None or gender is not None:
        age_str = str(age) if age is not None else "?"
        gender_str = gender if gender is not None else "?"
        age_gender_text = f" [{age_str},{gender_str}]"

    # Determine mask text and color
    mask_text = ""
    mask_color = (255, 255, 255)  # Default white
    if mask_status is True:
        mask_text = " [MASK]"
        mask_color = (255, 200, 0)  # Blue (BGR)
    elif mask_status is False:
        mask_text = " [NO MASK]"
        mask_color = (255, 255, 255)  # White

    # Determine helmet text and color (Orange/White style)
    helmet_text = ""
    helmet_color = (255, 255, 255)  # Default white
    if helmet_status is True:
        helmet_text = " [HELMET]"
        helmet_color = (0, 165, 255)  # Orange (BGR)
    elif helmet_status is False:
        helmet_text = " [NO HELMET]"
        helmet_color = (255, 255, 255)  # White

    # Determine glove text and color (Orange/White style)
    glove_text = ""
    glove_color = (255, 255, 255)  # Default white
    if glove_status is True:
        glove_text = " [GLOVE]"
        glove_color = (0, 165, 255)  # Orange (BGR)
    elif glove_status is False:
        glove_text = " [NO GLOVE]"
        glove_color = (255, 255, 255)  # White

    # Calculate total text width for background
    full_text = main_text + age_gender_text + mask_text + helmet_text + glove_text
    (text_w, text_h), baseline = cv2.getTextSize(
        full_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
    )

    # Draw label background (filled rectangle)
    label_y1 = max(0, y1 - text_h - baseline - 8)
    label_y2 = y1
    cv2.rectangle(
        frame,
        (x1, label_y1),
        (x1 + text_w + 8, label_y2),
        color,
        -1,  # Filled
    )

    # Draw main text (white)
    text_x = x1 + 4
    text_y = y1 - baseline - 4
    cv2.putText(
        frame,
        main_text,
        (text_x, text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),  # White
        thickness,
    )

    # Track current x position for additional text segments
    current_x = text_x
    (main_w, _), _ = cv2.getTextSize(
        main_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
    )
    current_x += main_w

    # Draw age/gender text (cyan color)
    if age_gender_text:
        cv2.putText(
            frame,
            age_gender_text,
            (current_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 0),  # Cyan (BGR)
            thickness,
        )
        (ag_w, _), _ = cv2.getTextSize(
            age_gender_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        current_x += ag_w

    # Draw mask text with specific color
    if mask_text:
        cv2.putText(
            frame,
            mask_text,
            (current_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            mask_color,
            thickness,
        )
        (mask_w, _), _ = cv2.getTextSize(
            mask_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        current_x += mask_w

    # Draw helmet text with specific color
    if helmet_text:
        cv2.putText(
            frame,
            helmet_text,
            (current_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            helmet_color,
            thickness,
        )
        (helmet_w, _), _ = cv2.getTextSize(
            helmet_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        current_x += helmet_w

    # Draw glove text with specific color
    if glove_text:
        cv2.putText(
            frame,
            glove_text,
            (current_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            glove_color,
            thickness,
        )

    return frame


def draw_tracked_animal(
    frame: np.ndarray,
    animal: TrackedAnimal,
    thickness: int = 2,
    font_scale: float = 0.6,
) -> np.ndarray:
    """
    Draw bounding box and label for a tracked animal.

    Args:
        frame: Image to draw on (modified in place)
        animal: TrackedAnimal object with bbox, class_name, track_id
        thickness: Line thickness
        font_scale: Font scale for text

    Returns:
        Annotated frame
    """
    x1, y1, x2, y2 = animal.bbox.astype(int)
    color = (255, 0, 255)  # Magenta for animals

    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

    label = f"{animal.class_name} #{animal.track_id} ({animal.confidence:.2f})"
    (text_w, text_h), baseline = cv2.getTextSize(
        label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
    )

    label_y1 = max(0, y1 - text_h - baseline - 8)
    cv2.rectangle(frame, (x1, label_y1), (x1 + text_w + 8, y1), color, -1)
    cv2.putText(
        frame,
        label,
        (x1 + 4, y1 - baseline - 4),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        thickness,
    )

    return frame


def draw_info_overlay(
    frame: np.ndarray,
    fps: float,
    person_count: int,
    extra_info: str = "",
) -> np.ndarray:
    """
    Draw FPS and person count info overlay on frame.

    Args:
        frame: Image to draw on (modified in place)
        fps: Current FPS value
        person_count: Number of tracked persons
        extra_info: Optional additional info to display

    Returns:
        Annotated frame
    """
    # Build info text
    info_text = f"FPS: {fps:.1f} | Persons: {person_count}"
    if extra_info:
        info_text += f" | {extra_info}"

    # Get text dimensions
    (text_w, text_h), baseline = cv2.getTextSize(
        info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
    )

    # Create semi-transparent background overlay
    padding = 10
    overlay = frame.copy()
    cv2.rectangle(
        overlay,
        (5, 5),
        (text_w + padding * 2 + 5, text_h + padding * 2 + 5),
        (0, 0, 0),
        -1,
    )

    # Blend with original (semi-transparent)
    alpha = 0.6
    frame = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)

    # Draw text
    cv2.putText(
        frame,
        info_text,
        (padding + 5, text_h + padding + 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )

    return frame


def compute_crop_score(
    bbox: np.ndarray,
    frame_shape: tuple,
    edge_margin: float = 0.02,
) -> float:
    """Score a person bbox for crop quality (higher = better).

    Considers bbox area and penalizes bboxes near frame edges.
    """
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = bbox[:4].astype(float)

    box_area = max(0.0, (x2 - x1) * (y2 - y1))

    margin_x = w * edge_margin
    margin_y = h * edge_margin

    left_dist = min(max(x1, 0.0), margin_x)
    top_dist = min(max(y1, 0.0), margin_y)
    right_dist = min(max(w - x2, 0.0), margin_x)
    bottom_dist = min(max(h - y2, 0.0), margin_y)

    visibility = (
        (left_dist / margin_x)
        * (top_dist / margin_y)
        * (right_dist / margin_x)
        * (bottom_dist / margin_y)
    )

    return box_area * visibility


def crop_with_padding(
    image: np.ndarray,
    bbox: np.ndarray,
    padding: float = 0.1,
) -> np.ndarray:
    """
    Crop image region with padding around bounding box.

    Args:
        image: Source image
        bbox: Bounding box [x1, y1, x2, y2]
        padding: Padding ratio (0.1 = 10% padding on each side)

    Returns:
        Cropped image region
    """
    h, w = image.shape[:2]
    x1, y1, x2, y2 = bbox.astype(int)

    # Calculate padding in pixels
    pad_w = int((x2 - x1) * padding)
    pad_h = int((y2 - y1) * padding)

    # Apply padding with bounds checking
    x1 = max(0, x1 - pad_w)
    y1 = max(0, y1 - pad_h)
    x2 = min(w, x2 + pad_w)
    y2 = min(h, y2 + pad_h)

    return image[y1:y2, x1:x2].copy()


def format_time_ms(ms: float) -> str:
    """Format milliseconds to human-readable string."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    else:
        return f"{ms / 1000:.1f}s"


def _cross_sign_px(
    line_start: Tuple[int, int],
    line_end: Tuple[int, int],
    point: Tuple[int, int],
) -> int:
    """Return +1 / -1 / 0 for which side of the line the point is on."""
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    px = point[0] - line_start[0]
    py = point[1] - line_start[1]
    cross = dx * py - dy * px
    if cross > 0:
        return 1
    elif cross < 0:
        return -1
    return 0


def draw_in_zone_overlay(
    frame: np.ndarray,
    pt1: Tuple[int, int],
    pt2: Tuple[int, int],
    in_point: Tuple[int, int],
    color: Tuple[int, int, int] = (0, 255, 0),
    alpha: float = 0.15,
) -> np.ndarray:
    """Draw a semi-transparent overlay on the IN zone side of the counting line."""
    h, w = frame.shape[:2]
    in_sign = _cross_sign_px(pt1, pt2, in_point)
    if in_sign == 0:
        return frame

    # Collect frame corners on the IN side
    corners = [(0, 0), (w, 0), (w, h), (0, h)]
    in_corners = [c for c in corners if _cross_sign_px(pt1, pt2, c) == in_sign]

    # Build polygon: pt1 -> in-side corners (sorted by angle) -> pt2
    # Sort corners clockwise around centroid of the polygon
    all_pts = [pt1] + in_corners + [pt2]
    cx = sum(p[0] for p in all_pts) / len(all_pts)
    cy = sum(p[1] for p in all_pts) / len(all_pts)
    import math
    all_pts.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))

    polygon = np.array(all_pts, dtype=np.int32)
    overlay = frame.copy()
    cv2.fillPoly(overlay, [polygon], color)
    return cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)


def draw_detection_zone(
    frame: np.ndarray,
    zone: Tuple[float, float, float, float],
    color: Tuple[int, int, int] = (255, 200, 0),
    thickness: int = 2,
    alpha: float = 0.08,
) -> np.ndarray:
    """Draw the detection zone rectangle with a subtle fill overlay."""
    h, w = frame.shape[:2]
    x1 = int(zone[0] * w)
    y1 = int(zone[1] * h)
    x2 = int(zone[2] * w)
    y2 = int(zone[3] * h)

    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
    frame = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)

    cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

    label = "DETECTION ZONE"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.putText(frame, label, (x1 + 4, y1 + th + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    return frame


def draw_counting_line(
    frame: np.ndarray,
    pt1: Tuple[int, int],
    pt2: Tuple[int, int],
) -> np.ndarray:
    """
    Draw the counting line on the frame.

    Args:
        frame: Image to draw on (modified in place).
        pt1: First point of the line in pixel coordinates.
        pt2: Second point of the line in pixel coordinates.

    Returns:
        Annotated frame.
    """
    cv2.line(frame, pt1, pt2, (255, 255, 0), 2, cv2.LINE_AA)
    return frame


def draw_counting_info(
    frame: np.ndarray,
    in_count: int,
    out_count: int,
) -> np.ndarray:
    """
    Draw IN/OUT counting overlay on the top-right corner of the frame.

    Args:
        frame: Image to draw on (modified in place).
        in_count: Number of people counted as IN.
        out_count: Number of people counted as OUT.

    Returns:
        Annotated frame.
    """
    info_text = f"IN: {in_count} | OUT: {out_count}"

    (text_w, text_h), baseline = cv2.getTextSize(
        info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2
    )

    padding = 10
    frame_w = frame.shape[1]
    x1 = frame_w - text_w - padding * 2 - 5
    y1 = 5
    x2 = frame_w - 5
    y2 = text_h + padding * 2 + 5

    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 0), -1)
    alpha = 0.6
    frame = cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0)

    cv2.putText(
        frame,
        info_text,
        (x1 + padding, text_h + padding + 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255),
        2,
    )

    return frame
