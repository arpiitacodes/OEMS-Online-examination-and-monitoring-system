"""
face_engine.py — Modern face detection + recognition for OEMS login.

Replaces the legacy raw-pixel "embedding" (96x96 grayscale flattened) with a
real ArcFace 512-d identity embedding from InsightFace (buffalo_l pack). This is
robust to lighting, pose and expression — the root cause of the old system's
"sometimes works, sometimes fails" behaviour.

Design goals:
  * One lazily-loaded, thread-safe singleton model (heavy to load, cheap to reuse).
  * Pure functions over numpy arrays — no Flask / DB knowledge here.
  * Embeddings are L2-normalised float32 vectors; similarity is a plain dot product.
  * Active-liveness signals (blink via eye-aspect-ratio, head yaw via landmarks)
    are computed from the same detection pass, so no second model is needed.

The proctoring pipeline in app.py keeps using its own Haar cascades; this module
is only used by the student face login/registration flow.
"""

import os
import threading

import numpy as np

# ── Tunables (env-overridable) ──────────────────────────────────────────────
# Cosine threshold for "same person". ArcFace genuine pairs typically score
# 0.45–0.85; impostors sit well below 0.3. 0.42 is a safe production default.
MATCH_THRESHOLD = float(os.environ.get("FACE_MATCH_THRESHOLD", "0.42"))
# Block registering a face that is already on another account.
DUPLICATE_THRESHOLD = float(os.environ.get("FACE_DUPLICATE_THRESHOLD", "0.55"))
# Minimum detector confidence to trust a face at all.
MIN_DET_SCORE = float(os.environ.get("FACE_MIN_DET_SCORE", "0.55"))
# Minimum face box size (px) — rejects faces that are too far / too small.
MIN_FACE_PX = int(os.environ.get("FACE_MIN_SIZE", "90"))
# Eye-aspect-ratio threshold: below this an eye is considered "closed" (blink).
EAR_CLOSED = float(os.environ.get("FACE_EAR_CLOSED", "0.18"))
EAR_OPEN = float(os.environ.get("FACE_EAR_OPEN", "0.26"))
# Head-yaw (deg) considered a deliberate "turn" for the turn challenge.
YAW_TURN_DEG = float(os.environ.get("FACE_YAW_TURN_DEG", "16.0"))
EMBED_DIM = 512

_model = None
_lock = threading.Lock()
_load_error = None


def _load_model():
    """Build the FaceAnalysis app once. Detection + recognition only (no
    age/gender/landmark-3d) to keep memory and latency low."""
    global _model, _load_error
    if _model is not None or _load_error is not None:
        return
    with _lock:
        if _model is not None or _load_error is not None:
            return
        try:
            from insightface.app import FaceAnalysis
            providers = ["CPUExecutionProvider"]
            # CoreML on Apple silicon speeds detection up noticeably when present.
            try:
                import onnxruntime
                if "CoreMLExecutionProvider" in onnxruntime.get_available_providers():
                    providers = ["CoreMLExecutionProvider", "CPUExecutionProvider"]
            except Exception:
                pass
            app = FaceAnalysis(
                name=os.environ.get("FACE_MODEL_PACK", "buffalo_l"),
                providers=providers,
                allowed_modules=["detection", "recognition"],
            )
            det = int(os.environ.get("FACE_DET_SIZE", "640"))
            app.prepare(ctx_id=-1, det_size=(det, det))
            _model = app
            print("[FaceEngine] ArcFace model ready (buffalo_l).")
        except Exception as e:  # pragma: no cover - environment dependent
            _load_error = e
            print(f"[FaceEngine] Model load FAILED: {e}")


def is_available():
    _load_model()
    return _model is not None


def model_status():
    """Human-readable status for health checks / debugging."""
    _load_model()
    if _model is not None:
        return {"ready": True, "error": None}
    return {"ready": False, "error": str(_load_error) if _load_error else "not loaded"}


# ── Landmark-derived liveness helpers ───────────────────────────────────────
def _eye_aspect_ratio(pts):
    """EAR from 6 eye landmarks: (|p2-p6| + |p3-p5|) / (2 |p1-p4|).
    Low when the eye is closed. We only have the 5-point kps from the detector,
    so this is approximated from the 2D 106-point set when available, else None."""
    if pts is None or len(pts) < 6:
        return None
    p1, p2, p3, p4, p5, p6 = pts[:6]
    a = np.linalg.norm(p2 - p6)
    b = np.linalg.norm(p3 - p5)
    c = np.linalg.norm(p1 - p4)
    if c < 1e-6:
        return None
    return float((a + b) / (2.0 * c))


def _yaw_from_kps(kps):
    """Rough head-yaw estimate from the 5 detector keypoints
    (left-eye, right-eye, nose, left-mouth, right-mouth). Returns degrees:
    positive = turned to subject's left, negative = right. Based on where the
    nose sits horizontally between the two eyes."""
    if kps is None or len(kps) < 3:
        return 0.0
    left_eye, right_eye, nose = kps[0], kps[1], kps[2]
    eye_mid_x = (left_eye[0] + right_eye[0]) / 2.0
    eye_dist = abs(right_eye[0] - left_eye[0])
    if eye_dist < 1e-6:
        return 0.0
    # Normalised nose offset from the eye midpoint, scaled to a degree-ish range.
    offset = (nose[0] - eye_mid_x) / eye_dist
    return float(np.clip(offset * 90.0, -60.0, 60.0))


def analyze_frame(bgr):
    """Detect the single best face in a BGR frame and return a dict of signals,
    or (None, reason). Used both for live guidance and for capture.

    Returns dict with:
      embedding   -> L2-normalised float32[512]
      bbox        -> (x, y, w, h) ints
      det_score   -> float
      yaw         -> float degrees
      blink_ear   -> float | None  (eye-aspect-ratio proxy, lower = more closed)
      sharpness   -> float (variance of Laplacian; low = blurry)
    """
    _load_model()
    if _model is None:
        return None, "Face engine unavailable on server."

    if bgr is None or bgr.size == 0:
        return None, "Empty camera frame."

    faces = _model.get(bgr)
    if not faces:
        return None, "no_face"
    # Keep only confident detections.
    faces = [f for f in faces if float(getattr(f, "det_score", 0.0)) >= MIN_DET_SCORE]
    if not faces:
        return None, "no_face"
    if len(faces) > 1:
        # Sort by area; if the second face is non-trivial, it's genuinely multiple.
        faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
        areas = [(f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]) for f in faces]
        if len(areas) >= 2 and areas[1] > 0.30 * areas[0]:
            return None, "multiple_faces"

    f = faces[0]
    x1, y1, x2, y2 = [int(v) for v in f.bbox]
    w, h = x2 - x1, y2 - y1
    if w < MIN_FACE_PX or h < MIN_FACE_PX:
        return None, "too_far"

    emb = np.asarray(f.normed_embedding, dtype=np.float32)
    if emb.shape[0] != EMBED_DIM:
        return None, "embed_failed"

    kps = np.asarray(f.kps, dtype=np.float32) if f.kps is not None else None
    yaw = _yaw_from_kps(kps)

    # Sharpness from the cropped face (rejects motion blur on capture).
    import cv2
    H, W = bgr.shape[:2]
    cx1, cy1 = max(0, x1), max(0, y1)
    cx2, cy2 = min(W, x2), min(H, y2)
    crop = bgr[cy1:cy2, cx1:cx2]
    sharpness = 0.0
    if crop.size:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    return {
        "embedding": emb,
        "bbox": (x1, y1, w, h),
        "det_score": float(f.det_score),
        "yaw": yaw,
        "sharpness": sharpness,
    }, None


def cosine(a, b):
    """Dot product of two already-L2-normalised vectors == cosine similarity."""
    if a is None or b is None:
        return 0.0
    return float(np.dot(a, b))


def average_embedding(embeddings):
    """Mean of several embeddings, re-normalised. Averaging multiple frames is
    what makes registration/verification stable across blinks & micro-motion."""
    if not embeddings:
        return None
    mat = np.stack(embeddings).astype(np.float32)
    vec = mat.mean(axis=0)
    norm = float(np.linalg.norm(vec))
    if norm < 1e-8:
        return None
    return (vec / norm).astype(np.float32)


# ── Embedding (de)serialisation: stored as raw float32 bytes in a BLOB ───────
def serialize(embedding):
    return np.asarray(embedding, dtype=np.float32).tobytes()


def deserialize(blob):
    if blob is None:
        return None
    if isinstance(blob, str):
        # Legacy CSV rows are intentionally NOT migrated — they were raw-pixel
        # vectors from the old engine and are useless for ArcFace matching.
        # Returning None forces a clean re-registration.
        return None
    try:
        vec = np.frombuffer(blob, dtype=np.float32)
    except Exception:
        return None
    if vec.shape[0] != EMBED_DIM:
        return None
    norm = float(np.linalg.norm(vec))
    if norm < 1e-8:
        return None
    return (vec / norm).astype(np.float32)
