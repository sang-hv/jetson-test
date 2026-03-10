"""
InsightFace-based face detection and recognition.

Uses ArcFace embeddings for face recognition with cosine similarity matching.
Supports both CPU and CUDA inference with graceful fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

import sys
import types

# Stub out face3d to avoid Cython/numpy binary incompatibility on Jetson
_face3d_stub = types.ModuleType("insightface.thirdparty.face3d")
_face3d_stub.mesh = types.ModuleType("insightface.thirdparty.face3d.mesh")  # type: ignore
sys.modules.setdefault("insightface.thirdparty.face3d", _face3d_stub)
sys.modules.setdefault("insightface.thirdparty.face3d.mesh", _face3d_stub.mesh)

from insightface.app import FaceAnalysis


@dataclass
class FaceMatch:
    """Result of face matching against known faces."""

    label: str  # Person name or "Unknown"
    similarity: float  # Cosine similarity score (0.0-1.0, higher is better)
    face_bbox: np.ndarray  # Face bounding box [x1, y1, x2, y2] within the crop
    embedding: np.ndarray  # 512-dim face embedding
    age: Optional[int] = None  # Estimated age (0-100)
    gender: Optional[int] = None  # Gender (0=Female, 1=Male)


class FaceRecognizer:
    """
    InsightFace-based face recognition using ArcFace embeddings.

    Performs face detection within person crops and matches detected faces
    against a database of known face embeddings using cosine similarity.

    Similarity Threshold Guide:
    - threshold=0.45 (default): Balanced for most conditions
    - threshold=0.50-0.55: Higher precision, may miss some known faces
    - threshold=0.35-0.40: Higher recall, may have some false matches

    Tuning Tips:
    - Poor lighting: Lower threshold (0.35-0.40)
    - High-quality cameras: Can use higher threshold (0.50-0.55)
    - Multiple images per person: Improves matching, can use higher threshold

    Example:
        recognizer = FaceRecognizer(device="cpu")
        recognizer.set_known_faces(embeddings, labels)
        match = recognizer.recognize_in_crop(person_crop, threshold=0.45)
        if match:
            print(f"Recognized: {match.label} ({match.similarity:.2f})")
    """

    def __init__(
        self,
        model_name: str = "buffalo_l",
        device: str = "cpu",
        det_size: Tuple[int, int] = (640, 640),
        age_gender_enabled: bool = False,
    ):
        """
        Initialize face recognizer with InsightFace.

        Args:
            model_name: InsightFace model pack name. Options:
                - "buffalo_l" (default): Large model, best accuracy (~326MB)
                - "buffalo_sc": Small model, faster but less accurate (~16MB)
                - "buffalo_s": Small model alternative
            device: 'cpu' or 'cuda' for inference
            det_size: Detection input size (width, height). Smaller sizes
                     are faster but may miss small faces. Use (320, 320)
                     for faster CPU inference.
            age_gender_enabled: Enable age/gender detection module
        """
        self.device = device
        self.det_size = det_size
        self.model_name = model_name
        self.age_gender_enabled = age_gender_enabled

        # Configure ONNX Runtime providers
        providers = self._get_providers(device)
        print(f"[Recognizer] Using providers: {providers}")

        # Initialize FaceAnalysis
        # allowed_modules: skip landmark models for speed
        # Add genderage module if age/gender detection is enabled
        allowed_modules = ["detection", "recognition"]
        if age_gender_enabled:
            allowed_modules.append("genderage")
            print("[Recognizer] Age/Gender detection enabled")

        print(f"[Recognizer] Loading InsightFace model: {model_name}")
        self.app = FaceAnalysis(
            name=model_name,
            providers=providers,
            allowed_modules=allowed_modules,
        )

        # ctx_id: 0 for first GPU, -1 for CPU
        ctx_id = 0 if device == "cuda" and self._cuda_available() else -1
        self.app.prepare(ctx_id=ctx_id, det_size=det_size)
        print(f"[Recognizer] Model ready (det_size={det_size})")

        # Known faces database (set via set_known_faces)
        self.known_embeddings: Optional[np.ndarray] = None  # Shape: (N, 512)
        self.known_labels: List[str] = []

    def _cuda_available(self) -> bool:
        """Check if CUDA is actually available (including TensorRT on Jetson)."""
        if ort is None:
            return False
        available = ort.get_available_providers()
        return "CUDAExecutionProvider" in available or "TensorrtExecutionProvider" in available

    def _get_providers(self, device: str) -> List[str]:
        """
        Get ONNX Runtime providers with graceful fallback.

        Always includes CPU as fallback even when CUDA is requested.
        """
        if ort is None:
            print("[Recognizer] WARNING: onnxruntime not found")
            return ["CPUExecutionProvider"]

        available = ort.get_available_providers()

        if device == "cuda":
            providers = []
            if "TensorrtExecutionProvider" in available:
                providers.append("TensorrtExecutionProvider")
            if "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
            if providers:
                providers.append("CPUExecutionProvider")
                return providers
            else:
                print("[Recognizer] WARNING: CUDA requested but not available, using CPU")
                return ["CPUExecutionProvider"]
        else:
            return ["CPUExecutionProvider"]

    def set_known_faces(
        self,
        embeddings: np.ndarray,
        labels: List[str],
    ) -> None:
        """
        Set the known faces database for matching.

        Args:
            embeddings: Face embeddings array, shape (N, 512).
                       Should be L2-normalized for best results.
            labels: Corresponding person names, length N
        """
        if len(embeddings) != len(labels):
            raise ValueError(
                f"Mismatch: {len(embeddings)} embeddings vs {len(labels)} labels"
            )

        # Ensure embeddings are L2-normalized
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        # Avoid division by zero
        norms = np.maximum(norms, 1e-10)
        self.known_embeddings = embeddings / norms
        self.known_labels = list(labels)

        print(f"[Recognizer] Loaded {len(labels)} known face embeddings")

    def detect_and_embed(
        self,
        image: np.ndarray,
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Detect faces and extract embeddings from image.

        Args:
            image: BGR image (can be full frame or person crop)

        Returns:
            List of (face_bbox, embedding) tuples where:
            - face_bbox: [x1, y1, x2, y2] coordinates
            - embedding: 512-dim L2-normalized vector
        """
        if image is None or image.size == 0:
            return []

        # Ensure image is valid
        if len(image.shape) != 3 or image.shape[2] != 3:
            return []

        try:
            faces = self.app.get(image)
        except Exception as e:
            print(f"[Recognizer] Face detection error: {e}")
            return []

        results = []
        for face in faces:
            bbox = face.bbox.astype(int)  # [x1, y1, x2, y2]
            # InsightFace provides normed_embedding which is already L2-normalized
            embedding = face.normed_embedding
            results.append((bbox, embedding))

        return results

    def match_embedding(
        self,
        embedding: np.ndarray,
        threshold: float = 0.45,
    ) -> Tuple[str, float]:
        """
        Match embedding against known faces using cosine similarity.

        For L2-normalized vectors, cosine similarity equals dot product:
            sim(a, b) = dot(a, b) / (||a|| * ||b||)
            When ||a|| = ||b|| = 1: sim(a, b) = dot(a, b)

        The similarity score ranges from -1 to 1:
        - 1.0: Identical faces
        - 0.5+: Likely same person
        - 0.3-0.5: Possibly same person (use threshold to decide)
        - <0.3: Likely different people

        Args:
            embedding: L2-normalized face embedding (512,)
            threshold: Minimum similarity for a positive match.
                      See class docstring for tuning guide.

        Returns:
            (label, similarity) tuple where:
            - label: Person name if matched, "Unknown" otherwise
            - similarity: Best similarity score found (even if below threshold)
        """
        if self.known_embeddings is None or len(self.known_labels) == 0:
            return ("Unknown", 0.0)

        # Ensure input is L2-normalized
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        # Cosine similarity via dot product (all vectors are L2-normalized)
        similarities = np.dot(self.known_embeddings, embedding)

        best_idx = int(np.argmax(similarities))
        best_similarity = float(similarities[best_idx])

        if best_similarity >= threshold:
            return (self.known_labels[best_idx], best_similarity)
        else:
            return ("Unknown", best_similarity)

    def recognize_in_crop(
        self,
        person_crop: np.ndarray,
        threshold: float = 0.45,
    ) -> Optional[FaceMatch]:
        """
        Detect face in person crop and match against known faces.

        This is the main recognition method. It:
        1. Detects faces within the person bounding box crop
        2. Extracts embedding from the largest face
        3. Matches against known face database
        4. Extracts age/gender if enabled

        Args:
            person_crop: BGR image of cropped person region
            threshold: Similarity threshold for matching

        Returns:
            FaceMatch if a face was found (may be "Unknown" if below threshold),
            None if no face was detected in the crop
        """
        if person_crop is None or person_crop.size == 0:
            return None

        # Minimum crop size for reliable face detection
        if person_crop.shape[0] < 50 or person_crop.shape[1] < 30:
            return None

        # Get faces directly from app.get() to access all attributes
        try:
            faces = self.app.get(person_crop)
        except Exception as e:
            print(f"[Recognizer] Face detection error: {e}")
            return None

        if not faces:
            return None

        # Use largest face if multiple detected (by area)
        def face_area(face):
            bbox = face.bbox
            return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

        best_face = max(faces, key=face_area)
        face_bbox = best_face.bbox.astype(int)
        embedding = best_face.normed_embedding

        label, similarity = self.match_embedding(embedding, threshold)

        # Extract age/gender if enabled
        age = None
        gender = None
        if self.age_gender_enabled:
            age = getattr(best_face, 'age', None)
            gender = getattr(best_face, 'gender', None)
            # Convert age to int if available
            if age is not None:
                age = int(age)
            # Gender: 0=Female, 1=Male
            if gender is not None:
                gender = int(gender)

        return FaceMatch(
            label=label,
            similarity=similarity,
            face_bbox=face_bbox,
            embedding=embedding,
            age=age,
            gender=gender,
        )

    def get_embedding_for_image(
        self,
        image: np.ndarray,
    ) -> Optional[np.ndarray]:
        """
        Extract face embedding from an image (for database building).

        Args:
            image: BGR image containing a face

        Returns:
            512-dim embedding or None if no face found
        """
        detections = self.detect_and_embed(image)
        if detections:
            # Return embedding of largest face
            def face_area(det):
                bbox = det[0]
                return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])

            _, embedding = max(detections, key=face_area)
            return embedding
        return None

    @property
    def known_face_count(self) -> int:
        """Number of known face embeddings loaded."""
        return len(self.known_labels)

    @property
    def unique_person_count(self) -> int:
        """Number of unique persons in database."""
        return len(set(self.known_labels))
