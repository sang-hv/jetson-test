"""
Generate face embeddings from images and insert into SQLite database.

Usage:
    # From a folder structure (known_faces/PersonName/photo.jpg)
    python gen_embeddings.py --known_dir known_faces

    # From a single image
    python gen_embeddings.py --image photo.jpg --user_id "Alice"

    # Custom DB path
    python gen_embeddings.py --known_dir known_faces --db logic_service/logic_service.db
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# Stub out face3d to avoid Cython/numpy binary incompatibility on Jetson
import types

_face3d_stub = types.ModuleType("insightface.thirdparty.face3d")
_face3d_stub.mesh = types.ModuleType("insightface.thirdparty.face3d.mesh")
sys.modules.setdefault("insightface.thirdparty.face3d", _face3d_stub)
sys.modules.setdefault("insightface.thirdparty.face3d.mesh", _face3d_stub.mesh)

from insightface.app import FaceAnalysis

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def init_insightface(device: str = "cpu") -> FaceAnalysis:
    providers = ["CPUExecutionProvider"]
    if device == "cuda":
        providers = ["CUDAExecutionProvider"] + providers

    app = FaceAnalysis(
        name="buffalo_l",
        providers=providers,
        allowed_modules=["detection", "recognition"],
    )
    ctx_id = 0 if device == "cuda" else -1
    app.prepare(ctx_id=ctx_id, det_size=(640, 640))
    return app


def extract_embedding(app: FaceAnalysis, image_path: str) -> np.ndarray | None:
    pil_img = Image.open(image_path).convert("RGB")
    img = np.array(pil_img)
    img_bgr = img[:, :, ::-1].copy()
    h, w = img_bgr.shape[:2]
    print(f"    Image size: {w}x{h}")

    faces = app.get(img_bgr)

    # Retry with different det_size if no face found
    # Large close-up faces can be missed by det_size=640
    if not faces and hasattr(app, 'det_model'):
        original_size = app.det_model.input_size
        for size in [(320, 320), (160, 160)]:
            app.det_model.input_size = size
            faces = app.get(img_bgr)
            if faces:
                print(f"    Detected with det_size={size}")
                break
        app.det_model.input_size = original_size

    if not faces:
        return None

    # Pick largest face
    largest = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    emb = largest.normed_embedding
    if emb is None:
        emb = largest.embedding
        norm = np.linalg.norm(emb)
        if norm > 1e-10:
            emb = emb / norm
    return emb


def insert_embedding(conn: sqlite3.Connection, user_id: str, embedding: np.ndarray):
    vector_json = json.dumps(embedding.tolist())
    conn.execute(
        "INSERT INTO face_embeddings (user_id, vector) VALUES (?, ?)",
        (user_id, vector_json),
    )


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS face_embeddings (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            vector    TEXT NOT NULL
        )
    """)
    conn.commit()


def process_folder(app: FaceAnalysis, conn: sqlite3.Connection, known_dir: str):
    root = Path(known_dir)
    if not root.exists():
        print(f"Directory not found: {known_dir}")
        return

    person_dirs = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith("_"))
    if not person_dirs:
        print(f"No person folders found in {known_dir}")
        return

    total = 0
    for person_dir in person_dirs:
        user_id = person_dir.name
        images = sorted(f for f in person_dir.iterdir() if f.suffix.lower() in SUPPORTED_EXTENSIONS)

        for img_path in images:
            emb = extract_embedding(app, str(img_path))
            if emb is not None:
                insert_embedding(conn, user_id, emb)
                total += 1
                print(f"  [OK] {user_id}/{img_path.name}")
            else:
                print(f"  [SKIP] No face: {user_id}/{img_path.name}")

    conn.commit()
    print(f"\nInserted {total} embeddings into database.")


def process_single(app: FaceAnalysis, conn: sqlite3.Connection, image_path: str, user_id: str):
    emb = extract_embedding(app, image_path)
    if emb is not None:
        insert_embedding(conn, user_id, emb)
        conn.commit()
        print(f"[OK] Inserted embedding for {user_id} from {image_path}")
    else:
        print(f"[FAIL] No face detected in {image_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate face embeddings and insert into SQLite")
    parser.add_argument("--known_dir", type=str, help="Folder with person subfolders (known_faces/)")
    parser.add_argument("--image", type=str, help="Single image path")
    parser.add_argument("--user_id", type=str, help="User ID for single image mode")
    parser.add_argument("--db", type=str, default="logic_service/logic_service.db", help="SQLite DB path")
    parser.add_argument("--device", type=str, default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--clear", action="store_true", help="Clear existing embeddings before inserting")
    args = parser.parse_args()

    if not args.known_dir and not args.image:
        parser.error("Provide --known_dir or --image")
    if args.image and not args.user_id:
        parser.error("--user_id is required with --image")

    print(f"Initializing InsightFace ({args.device})...")
    app = init_insightface(args.device)

    conn = sqlite3.connect(args.db)
    ensure_table(conn)

    if args.clear:
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()
        print("Cleared existing embeddings.")

    if args.known_dir:
        process_folder(app, conn, args.known_dir)
    else:
        process_single(app, conn, args.image, args.user_id)

    # Show summary
    count = conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
    users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM face_embeddings").fetchone()[0]
    print(f"\nDatabase now has {count} embeddings for {users} persons.")
    conn.close()


if __name__ == "__main__":
    main()
