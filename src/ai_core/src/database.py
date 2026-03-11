"""
Known faces database with folder-based organization and NPZ caching,
plus SQLite-based loading for pre-computed embeddings.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from .recognizer import FaceRecognizer


@dataclass
class KnownFacesData:
    """Container for known faces database."""

    embeddings: np.ndarray  # Shape: (N, 512)
    labels: List[str]  # Person names corresponding to each embedding
    image_paths: List[str]  # Source image paths for debugging

    @property
    def count(self) -> int:
        """Total number of embeddings."""
        return len(self.labels)

    @property
    def unique_persons(self) -> int:
        """Number of unique persons."""
        return len(set(self.labels))

    def get_embeddings_for_person(self, name: str) -> np.ndarray:
        """Get all embeddings for a specific person."""
        indices = [i for i, label in enumerate(self.labels) if label == name]
        return self.embeddings[indices]


class FaceDatabase:
    """
    Manages known faces with folder-based organization and NPZ caching.

    Expected folder structure:
        known_dir/
            Alice/
                photo1.jpg
                photo2.png
            Bob/
                selfie.jpg
            Carol/
                portrait.jpg
                id_photo.png

    Each subfolder name becomes the person's label. All supported image
    files within are processed for face embeddings.

    Cache files (stored in known_dir):
        _embeddings_cache.npz: Cached embeddings, labels, and paths
        _cache_manifest.json: Folder fingerprint for invalidation

    The cache is automatically invalidated when:
        - Files are added, removed, or renamed
        - File modification times change
        - File sizes change

    Example:
        recognizer = FaceRecognizer(device="cpu")
        db = FaceDatabase("./known_faces", recognizer)
        data = db.load()  # Uses cache if valid
        recognizer.set_known_faces(data.embeddings, data.labels)
    """

    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    CACHE_FILENAME = "_embeddings_cache.npz"
    MANIFEST_FILENAME = "_cache_manifest.json"

    def __init__(self, known_dir: str, recognizer: FaceRecognizer):
        """
        Initialize face database.

        Args:
            known_dir: Path to directory containing person subfolders
            recognizer: FaceRecognizer instance for embedding extraction
        """
        self.known_dir = Path(known_dir)
        self.recognizer = recognizer
        self.cache_path = self.known_dir / self.CACHE_FILENAME
        self.manifest_path = self.known_dir / self.MANIFEST_FILENAME

        # Create directory if it doesn't exist
        if not self.known_dir.exists():
            print(f"[Database] Creating directory: {known_dir}")
            self.known_dir.mkdir(parents=True, exist_ok=True)

    def _get_folder_manifest(self) -> Dict[str, str]:
        """
        Generate manifest of folder contents for cache invalidation.

        Creates a fingerprint of all image files including their
        modification times and sizes.

        Returns:
            Dict mapping relative paths to "mtime_size" strings
        """
        manifest = {}

        for person_dir in self.known_dir.iterdir():
            # Skip non-directories and cache files
            if not person_dir.is_dir() or person_dir.name.startswith("_"):
                continue

            for image_path in person_dir.iterdir():
                if image_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    try:
                        stat = image_path.stat()
                        key = str(image_path.relative_to(self.known_dir))
                        # Use mtime and size for quick change detection
                        manifest[key] = f"{stat.st_mtime:.6f}_{stat.st_size}"
                    except OSError:
                        continue

        return manifest

    def _compute_manifest_hash(self, manifest: Dict[str, str]) -> str:
        """Compute MD5 hash of manifest for quick comparison."""
        content = json.dumps(manifest, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()

    def _is_cache_valid(self) -> bool:
        """
        Check if cached embeddings are still valid.

        Compares stored manifest hash with current folder state.
        """
        if not self.cache_path.exists() or not self.manifest_path.exists():
            return False

        try:
            with open(self.manifest_path, "r") as f:
                cached_manifest = json.load(f)

            current_manifest = self._get_folder_manifest()

            cached_hash = self._compute_manifest_hash(cached_manifest)
            current_hash = self._compute_manifest_hash(current_manifest)

            return cached_hash == current_hash

        except Exception as e:
            print(f"[Database] Cache validation error: {e}")
            return False

    def _load_from_cache(self) -> Optional[KnownFacesData]:
        """Load embeddings from NPZ cache file."""
        try:
            data = np.load(self.cache_path, allow_pickle=True)
            return KnownFacesData(
                embeddings=data["embeddings"],
                labels=data["labels"].tolist(),
                image_paths=data["image_paths"].tolist(),
            )
        except Exception as e:
            print(f"[Database] Failed to load cache: {e}")
            return None

    def _save_to_cache(
        self,
        data: KnownFacesData,
        manifest: Dict[str, str],
    ) -> None:
        """Save embeddings and manifest to cache files."""
        try:
            np.savez(
                self.cache_path,
                embeddings=data.embeddings,
                labels=np.array(data.labels, dtype=object),
                image_paths=np.array(data.image_paths, dtype=object),
            )

            with open(self.manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

        except Exception as e:
            print(f"[Database] Failed to save cache: {e}")

    def _extract_embeddings(self) -> KnownFacesData:
        """
        Extract embeddings from all images in known_dir.

        Processes each person's folder, extracts face embeddings,
        and compiles them into a KnownFacesData object.
        """
        embeddings: List[np.ndarray] = []
        labels: List[str] = []
        image_paths: List[str] = []

        print(f"[Database] Extracting embeddings from {self.known_dir}")

        person_dirs = sorted(
            [d for d in self.known_dir.iterdir() if d.is_dir() and not d.name.startswith("_")]
        )

        if not person_dirs:
            print(f"[Database] No person folders found in {self.known_dir}")
            print("[Database] Running in detection-only mode")
            return KnownFacesData(
                embeddings=np.zeros((0, 512), dtype=np.float32),
                labels=[],
                image_paths=[],
            )

        for person_dir in person_dirs:
            person_name = person_dir.name
            person_embeddings: List[np.ndarray] = []
            person_paths: List[str] = []

            image_files = sorted(
                [
                    f
                    for f in person_dir.iterdir()
                    if f.suffix.lower() in self.SUPPORTED_EXTENSIONS
                ]
            )

            for image_path in image_files:
                try:
                    # Load image using PIL and convert to BGR for InsightFace
                    img = np.array(Image.open(image_path).convert("RGB"))
                    img_bgr = img[:, :, ::-1].copy()  # RGB to BGR

                    # Extract face embedding
                    embedding = self.recognizer.get_embedding_for_image(img_bgr)

                    if embedding is not None:
                        person_embeddings.append(embedding)
                        person_paths.append(str(image_path))
                        print(f"  [OK] {person_name}/{image_path.name}")
                    else:
                        print(f"  [SKIP] No face: {person_name}/{image_path.name}")

                except Exception as e:
                    print(f"  [ERROR] {person_name}/{image_path.name}: {e}")

            if person_embeddings:
                # Store all embeddings for this person
                for emb, path in zip(person_embeddings, person_paths):
                    embeddings.append(emb)
                    labels.append(person_name)
                    image_paths.append(path)

                print(f"[Database] {person_name}: {len(person_embeddings)} embeddings")
            else:
                print(f"[Database] WARNING: No valid faces for {person_name}")

        if not embeddings:
            print(f"[Database] No valid face embeddings found in {self.known_dir}")
            print("[Database] Running in detection-only mode")
            return KnownFacesData(
                embeddings=np.zeros((0, 512), dtype=np.float32),
                labels=[],
                image_paths=[],
            )

        # Stack embeddings and L2-normalize
        embeddings_array = np.vstack(embeddings)
        norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)  # Avoid division by zero
        embeddings_array = embeddings_array / norms

        return KnownFacesData(
            embeddings=embeddings_array,
            labels=labels,
            image_paths=image_paths,
        )

    def load(self, force_refresh: bool = False) -> KnownFacesData:
        """
        Load known faces database, using cache if valid.

        Args:
            force_refresh: If True, ignore cache and re-extract embeddings

        Returns:
            KnownFacesData with embeddings and labels

        Raises:
            ValueError: If no valid face embeddings can be extracted
            FileNotFoundError: If known_dir doesn't exist
        """
        # Try to use cache
        if not force_refresh and self._is_cache_valid():
            print("[Database] Loading from cache...")
            cached = self._load_from_cache()
            if cached is not None:
                print(
                    f"[Database] Loaded {cached.count} embeddings "
                    f"({cached.unique_persons} persons) from cache"
                )
                return cached
            print("[Database] Cache load failed, extracting fresh...")

        # Extract fresh embeddings
        print("[Database] Extracting embeddings (this may take a moment)...")
        data = self._extract_embeddings()

        # Only save to cache if we have embeddings
        if data.count > 0:
            manifest = self._get_folder_manifest()
            self._save_to_cache(data, manifest)
            print(
                f"[Database] Saved {data.count} embeddings "
                f"({data.unique_persons} persons) to cache"
            )
        else:
            print("[Database] No embeddings to cache")

        return data

    def get_person_names(self) -> List[str]:
        """Get list of all person folder names in known_dir."""
        return sorted(
            [
                d.name
                for d in self.known_dir.iterdir()
                if d.is_dir() and not d.name.startswith("_")
            ]
        )

    def clear_cache(self) -> None:
        """Delete cache files to force re-extraction on next load."""
        if self.cache_path.exists():
            self.cache_path.unlink()
        if self.manifest_path.exists():
            self.manifest_path.unlink()
        print("[Database] Cache cleared")


class FaceDatabaseSQLite:
    """
    Loads pre-computed face embeddings from a SQLite database.

    Reads from the `face_embeddings` table:
        id        INTEGER PRIMARY KEY AUTOINCREMENT
        user_id   TEXT NOT NULL
        vector    TEXT NOT NULL  -- JSON array "[0.1, 0.2, ...]"

    One user_id can have multiple rows (multiple embeddings per person).

    Example:
        db = FaceDatabaseSQLite("logic_service/logic_service.db")
        data = db.load()
        recognizer.set_known_faces(data.embeddings, data.labels)
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def load(self, force_refresh: bool = False) -> KnownFacesData:
        """
        Load face embeddings from SQLite database.

        Args:
            force_refresh: Ignored (kept for interface compatibility with FaceDatabase)

        Returns:
            KnownFacesData with embeddings and labels
        """
        if not Path(self.db_path).exists():
            print(f"[Database] SQLite DB not found: {self.db_path}")
            print("[Database] Running in detection-only mode")
            return self._empty()

        print(f"[Database] Loading embeddings from SQLite: {self.db_path}")

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("SELECT user_id, vector FROM face_embeddings")
            rows = cursor.fetchall()
            conn.close()
        except sqlite3.Error as e:
            print(f"[Database] SQLite error: {e}")
            return self._empty()

        if not rows:
            print("[Database] No embeddings found in face_embeddings table")
            print("[Database] Running in detection-only mode")
            return self._empty()

        embeddings: List[np.ndarray] = []
        labels: List[str] = []

        for user_id, vector_text in rows:
            try:
                vector = json.loads(vector_text)
                embeddings.append(np.array(vector, dtype=np.float32))
                labels.append(user_id)
            except (json.JSONDecodeError, ValueError) as e:
                print(f"  [SKIP] Bad vector for {user_id}: {e}")

        if not embeddings:
            print("[Database] No valid embeddings parsed")
            return self._empty()

        # Stack and L2-normalize
        embeddings_array = np.vstack(embeddings)
        norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        embeddings_array = embeddings_array / norms

        data = KnownFacesData(
            embeddings=embeddings_array,
            labels=labels,
            image_paths=[""] * len(labels),
        )

        print(
            f"[Database] Loaded {data.count} embeddings "
            f"({data.unique_persons} persons) from SQLite"
        )
        return data

    @staticmethod
    def _empty() -> KnownFacesData:
        return KnownFacesData(
            embeddings=np.zeros((0, 512), dtype=np.float32),
            labels=[],
            image_paths=[],
        )
