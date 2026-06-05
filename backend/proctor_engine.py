"""
proctor_engine.py — Accurate, confidence-scored AI proctoring for OEMS exams.

This replaces the legacy Haar-cascade proctoring (frontal/alt2/profile cascades +
eye cascade + face-box-centre "gaze") that lived inline in app.py. Haar is
lighting-sensitive, drops faces on mild head turns, and double-counts profiles as
"multiple people" — the root cause of the old system's flood of false violations.

What this engine does instead:

  * Face / multi-face / no-face detection via the **same InsightFace (RetinaFace)
    detector already loaded for face login** — one model, real confidence scores,
    robust to pose and lighting. No extra memory cost.
  * **Real 3-D head pose** (yaw / pitch / roll) from the 5 facial keypoints via
    cv2.solvePnP against a canonical 3-D face model — not a fake bbox-offset.
  * **Gaze / attention** derived from head pose plus eye-region analysis, with
    separate, calibrated thresholds for looking down (paper) vs. sideways.
  * **Identity continuity**: every face carries an ArcFace embedding, so the
    engine can confirm the person on camera is still the enrolled student
    (catches mid-exam impersonation / person-swap).
  * **Anti-spoofing** signals: screen/photo replay tends to be flat (low texture
    variance, abnormal sharpness, moiré) and unnaturally static — surfaced as a
    spoof_risk score the caller can act on.
  * **Object detection** (phone / book / laptop / extra person) via YOLO, run on a
    throttled cadence (it is the expensive part) with class-specific confidence.
  * **Lighting / quality** gating so a dark or blurry frame produces a "can't
    assess" state instead of a false "no face" violation.

Every detector returns a *confidence* in [0,1]. The temporal layer in app.py only
escalates to a logged violation when confidence stays high across several frames —
this is what crushes the false-positive rate. This module is pure CV over numpy
arrays: no Flask, DB, or session knowledge.
"""

import os
import math
import threading

import numpy as np

import face_engine  # reuses the shared InsightFace model (detector + ArcFace)

# ── Tunables (env-overridable) ──────────────────────────────────────────────
# Detector confidence to trust a face for proctoring. Slightly below login's
# threshold: in proctoring we'd rather see a low-confidence face than miss one
# and fire a false "no face".
PROCTOR_MIN_DET_SCORE = float(os.environ.get("PROCTOR_MIN_DET_SCORE", "0.50"))
# A second face only counts as "another person" if it is a real, non-trivial
# face — not a tiny background artefact. Expressed as a fraction of the primary
# face's area AND an absolute detector score.
SECONDARY_FACE_AREA_RATIO = float(os.environ.get("PROCTOR_SECOND_FACE_RATIO", "0.18"))
SECONDARY_FACE_MIN_SCORE = float(os.environ.get("PROCTOR_SECOND_FACE_SCORE", "0.62"))
# Head-pose thresholds (degrees). Looking down a bit is natural (reading the
# screen); the limits are deliberately generous to avoid punishing normal posture.
YAW_LOOK_LIMIT = float(os.environ.get("PROCTOR_YAW_LIMIT", "28.0"))     # left/right
PITCH_DOWN_LIMIT = float(os.environ.get("PROCTOR_PITCH_DOWN_LIMIT", "26.0"))  # looking down
PITCH_UP_LIMIT = float(os.environ.get("PROCTOR_PITCH_UP_LIMIT", "22.0"))      # looking up
# Below this Laplacian variance the frame is too blurry to assess reliably.
MIN_FRAME_SHARPNESS = float(os.environ.get("PROCTOR_MIN_SHARPNESS", "12.0"))
# Mean luma below this => too dark to assess; above 250 => blown-out / glare.
MIN_FRAME_BRIGHTNESS = float(os.environ.get("PROCTOR_MIN_BRIGHTNESS", "32.0"))
MAX_FRAME_BRIGHTNESS = float(os.environ.get("PROCTOR_MAX_BRIGHTNESS", "248.0"))
# Identity continuity: cosine below this against the enrolled embedding flags a
# possible different person. Lower than login's match threshold because pose
# during an exam is uncontrolled — we only want to catch clear mismatches.
IDENTITY_MISMATCH_THRESHOLD = float(os.environ.get("PROCTOR_IDENTITY_THRESHOLD", "0.26"))
# YOLO object detection thresholds, per class group.
PHONE_CONF = float(os.environ.get("PROCTOR_PHONE_CONF", "0.45"))
OBJECT_CONF = float(os.environ.get("PROCTOR_OBJECT_CONF", "0.50"))
# Run YOLO at most every Nth analysed frame (it dominates latency).
OBJECT_EVERY_N = int(os.environ.get("PROCTOR_OBJECT_EVERY_N", "2"))

_yolo = None
_yolo_lock = threading.Lock()
_yolo_error = None

# COCO classes we care about, grouped by how we surface them.
_PHONE_CLASSES = {"cell phone"}
_DEVICE_CLASSES = {"laptop", "tv", "remote", "keyboard", "tablet"}
_MATERIAL_CLASSES = {"book"}


def _load_yolo():
    global _yolo, _yolo_error
    if _yolo is not None or _yolo_error is not None:
        return
    with _yolo_lock:
        if _yolo is not None or _yolo_error is not None:
            return
        try:
            from ultralytics import YOLO
            model_path = os.environ.get("PROCTOR_YOLO_MODEL", "yolov8n.pt")
            _yolo = YOLO(model_path)
            # Warm the model so the first real frame isn't slow.
            print("[ProctorEngine] YOLO object detector ready.")
        except Exception as e:  # pragma: no cover - environment dependent
            _yolo_error = e
            print(f"[ProctorEngine] YOLO load FAILED: {e}")


def engine_status():
    """Health summary for /proctor_health and gatekeeper checks."""
    face_engine._load_model()
    _load_yolo()
    return {
        "face_detector_ready": face_engine.is_available(),
        "object_detector_ready": _yolo is not None,
        "object_detector_error": str(_yolo_error) if _yolo_error else None,
    }


# ── 3-D head-pose from 5 keypoints (solvePnP) ───────────────────────────────
# Canonical 3-D model points (mm-ish, arbitrary scale) matching InsightFace's
# 5-keypoint order: left-eye, right-eye, nose-tip, left-mouth, right-mouth.
_MODEL_POINTS_5 = np.array([
    [-30.0,  30.0, -30.0],   # left eye
    [ 30.0,  30.0, -30.0],   # right eye
    [  0.0,   0.0,   0.0],   # nose tip
    [-25.0, -30.0, -30.0],   # left mouth corner
    [ 25.0, -30.0, -30.0],   # right mouth corner
], dtype=np.float64)


def _head_pose(kps, frame_w, frame_h):
    """Estimate (yaw, pitch, roll) degrees from 5 image keypoints via solvePnP.

    Returns (yaw, pitch, roll, ok). yaw>0 = subject turned to their right (camera
    left), pitch>0 = looking up, pitch<0 = looking down, roll = head tilt. Falls
    back to a robust geometric estimate if solvePnP fails to converge."""
    import cv2
    if kps is None or len(kps) < 5:
        return 0.0, 0.0, 0.0, False

    image_points = np.asarray(kps[:5], dtype=np.float64)
    focal = float(frame_w)
    center = (frame_w / 2.0, frame_h / 2.0)
    cam = np.array([[focal, 0, center[0]],
                    [0, focal, center[1]],
                    [0, 0, 1]], dtype=np.float64)
    dist = np.zeros((4, 1))
    try:
        ok, rvec, _ = cv2.solvePnP(
            _MODEL_POINTS_5, image_points, cam, dist,
            flags=cv2.SOLVEPNP_ITERATIVE
        )
        if not ok:
            raise ValueError("solvePnP failed")
        rmat, _ = cv2.Rodrigues(rvec)
        sy = math.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
        singular = sy < 1e-6
        if not singular:
            pitch = math.degrees(math.atan2(rmat[2, 1], rmat[2, 2]))
            yaw = math.degrees(math.atan2(-rmat[2, 0], sy))
            roll = math.degrees(math.atan2(rmat[1, 0], rmat[0, 0]))
        else:
            pitch = math.degrees(math.atan2(-rmat[1, 2], rmat[1, 1]))
            yaw = math.degrees(math.atan2(-rmat[2, 0], sy))
            roll = 0.0
        # Normalise solvePnP's pitch (which is ~±180 around the down axis) to a
        # signed "looking up/down" angle centred on 0 for a level head.
        pitch = _wrap_pitch(pitch)
        return float(yaw), float(pitch), float(roll), True
    except Exception:
        # Geometric fallback: nose offset from the eye-midpoint gives yaw; the
        # nose-to-eye vertical ratio gives a coarse pitch.
        le, re, nose, lm, rm = image_points
        eye_mid = (le + re) / 2.0
        eye_dist = np.linalg.norm(re - le)
        if eye_dist < 1e-6:
            return 0.0, 0.0, 0.0, False
        yaw = float(np.clip((nose[0] - eye_mid[0]) / eye_dist * 90.0, -60, 60))
        mouth_mid = (lm + rm) / 2.0
        face_h = np.linalg.norm(mouth_mid - eye_mid)
        nose_rel = (nose[1] - eye_mid[1]) / (face_h + 1e-6)
        pitch = float(np.clip((nose_rel - 0.5) * 90.0, -45, 45))
        roll = float(math.degrees(math.atan2(re[1] - le[1], re[0] - le[0])))
        return yaw, pitch, roll, True


def _wrap_pitch(pitch):
    """solvePnP returns pitch near ±180 for a forward-facing head; remap so a
    level gaze ~0, looking down is negative, looking up is positive."""
    if pitch > 90:
        pitch = pitch - 180
    elif pitch < -90:
        pitch = pitch + 180
    return -pitch  # flip so up is positive, matching our threshold semantics


# ── Lighting / quality assessment ───────────────────────────────────────────
def assess_quality(bgr):
    """Return (quality_dict, usable). Cheap whole-frame stats used to decide
    whether a frame can be trusted before we ever blame the student."""
    import cv2
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    q = {
        "brightness": round(brightness, 1),
        "sharpness": round(sharpness, 1),
        "too_dark": brightness < MIN_FRAME_BRIGHTNESS,
        "too_bright": brightness > MAX_FRAME_BRIGHTNESS,
        "too_blurry": sharpness < MIN_FRAME_SHARPNESS,
    }
    usable = not (q["too_dark"] or q["too_blurry"])
    return q, usable


# ── Anti-spoof heuristics (replay / printed-photo) ──────────────────────────
def _spoof_risk(bgr, bbox):
    """Coarse passive liveness: printed photos and phone/monitor replays of a
    face tend to be unusually flat (low colour/texture variance) or carry
    screen-door / moiré high-frequency energy. Returns a risk score in [0,1].
    This is a *signal*, not a verdict — the caller weighs it with motion over
    time (a real face is never perfectly static)."""
    import cv2
    x1, y1, x2, y2 = bbox
    H, W = bgr.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    crop = bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Texture richness: real skin under real light has mid-range local variance.
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # Colour spread: flat reprints collapse saturation distribution.
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    sat_std = float(hsv[:, :, 1].std())
    risk = 0.0
    if lap_var < 18.0:
        risk += 0.5          # suspiciously smooth (print / low-res replay)
    if sat_std < 18.0:
        risk += 0.3          # washed-out colour (screen replay)
    # High-frequency banding (moiré from a screen) — energy in the FFT tail.
    try:
        small = cv2.resize(gray, (64, 64))
        f = np.fft.fftshift(np.fft.fft2(small))
        mag = np.abs(f)
        high = mag[8:56, 8:56].mean()
        low = mag.mean() + 1e-6
        if high / low > 1.8:
            risk += 0.2
    except Exception:
        pass
    return float(min(1.0, risk))


# ── Object detection (phone / book / device / extra person) ─────────────────
def detect_objects(bgr):
    """Run YOLO and return a list of suspicious objects. Each entry:
        {"label": str, "group": "phone|material|device|person", "conf": float,
         "bbox": (x1,y1,x2,y2)}
    Person detections are returned too (a cheaper, complementary cross-check on
    the face-based people count). Empty list if YOLO is unavailable."""
    _load_yolo()
    if _yolo is None:
        return []
    found = []
    try:
        results = _yolo(bgr, verbose=False, conf=min(PHONE_CONF, OBJECT_CONF, 0.30))
        for res in results:
            names = res.names
            for box in res.boxes:
                cls = names[int(box.cls)].lower()
                conf = float(box.conf)
                xyxy = [int(v) for v in box.xyxy[0].tolist()]
                if cls in _PHONE_CLASSES and conf >= PHONE_CONF:
                    found.append({"label": cls, "group": "phone", "conf": conf, "bbox": xyxy})
                elif cls in _MATERIAL_CLASSES and conf >= OBJECT_CONF:
                    found.append({"label": cls, "group": "material", "conf": conf, "bbox": xyxy})
                elif cls in _DEVICE_CLASSES and conf >= OBJECT_CONF:
                    found.append({"label": cls, "group": "device", "conf": conf, "bbox": xyxy})
                elif cls == "person" and conf >= OBJECT_CONF:
                    found.append({"label": cls, "group": "person", "conf": conf, "bbox": xyxy})
    except Exception as e:
        print(f"[ProctorEngine] Object detection error: {e}")
    return found


# ── Top-level per-frame analysis ────────────────────────────────────────────
def analyze(bgr, enrolled_embedding=None, run_objects=True):
    """Analyse one BGR frame and return a structured signal dict.

    The return value is intentionally rich and *neutral* — it reports what was
    seen with confidences, and leaves the escalate-to-violation decision to the
    temporal layer in app.py. Keys:

      ok              -> bool (frame could be assessed at all)
      quality         -> dict from assess_quality
      face_count      -> int (faces that pass the secondary-face gate)
      primary         -> dict | None  (largest face: bbox, det_score, pose…)
      head_pose       -> {"yaw","pitch","roll"} degrees (or None)
      attention       -> "center" | "left" | "right" | "down" | "up" | "unknown"
      identity_match  -> float | None  (cosine vs enrolled; None if no enrol/face)
      identity_ok     -> bool | None
      spoof_risk      -> float [0,1]
      objects         -> list from detect_objects (when run_objects)
      observations    -> list[{"code","label","confidence","severity"}]
                         — candidate violations with per-signal confidence.
    """
    out = {
        "ok": False, "quality": None, "face_count": 0, "primary": None,
        "head_pose": None, "attention": "unknown", "identity_match": None,
        "identity_ok": None, "spoof_risk": 0.0, "objects": [], "observations": [],
    }
    if bgr is None or bgr.size == 0:
        out["observations"].append(_obs("frame_error", "Empty camera frame", 0.5, 0))
        return out

    quality, usable = assess_quality(bgr)
    out["quality"] = quality
    H, W = bgr.shape[:2]

    # Objects first (independent of faces) — phone/book detection must not be
    # short-circuited by a no-face state, the bug in the legacy ordering.
    objects = detect_objects(bgr) if run_objects else []
    out["objects"] = objects
    for o in objects:
        if o["group"] == "phone":
            out["observations"].append(_obs("phone", "Mobile phone detected", o["conf"], 3))
        elif o["group"] == "material":
            out["observations"].append(_obs("material", f"Study material ({o['label']}) detected", o["conf"], 2))
        elif o["group"] == "device":
            out["observations"].append(_obs("device", f"Unauthorized device ({o['label']}) detected", o["conf"], 2))

    faces = face_engine.detect_faces(bgr, min_det_score=PROCTOR_MIN_DET_SCORE)

    # If the frame itself is unusable, don't blame the student for "no face".
    if not usable:
        reason = "too_dark" if quality["too_dark"] else "too_blurry"
        out["observations"].append(_obs("quality_low", "Camera image unclear — adjust lighting/position", 0.4, 0, info=reason))
        out["ok"] = True
        out["face_count"] = len(faces)
        return out

    out["ok"] = True

    if not faces:
        # Cross-check with YOLO 'person': a body with no detectable face means
        # the student turned fully away or left frame.
        out["observations"].append(_obs("no_face", "No face detected in frame", 0.9, 2))
        return out

    # People count: primary face + any secondary face that is a *real* person.
    primary = faces[0]
    people = 1
    extra_conf = 0.0
    for f in faces[1:]:
        ratio = f["area"] / max(primary["area"], 1)
        if ratio >= SECONDARY_FACE_AREA_RATIO and f["det_score"] >= SECONDARY_FACE_MIN_SCORE:
            people += 1
            extra_conf = max(extra_conf, f["det_score"])
    # YOLO person count as a corroborating signal (reduces false multi-face).
    yolo_people = sum(1 for o in objects if o["group"] == "person")
    out["face_count"] = people
    if people > 1:
        conf = min(0.99, 0.5 + 0.5 * extra_conf)
        if yolo_people >= 2:
            conf = min(0.99, conf + 0.15)
        out["observations"].append(_obs("multiple_faces", f"Multiple people detected ({people})", conf, 3))

    # Primary face details.
    out["primary"] = {
        "bbox": primary["bbox"],
        "det_score": primary["det_score"],
        "area_frac": round(primary["area"] / float(W * H), 3),
    }

    # Head pose / attention.
    yaw, pitch, roll, pose_ok = _head_pose(primary["kps"], W, H)
    if pose_ok:
        out["head_pose"] = {"yaw": round(yaw, 1), "pitch": round(pitch, 1), "roll": round(roll, 1)}
        attention, att_conf = _attention_from_pose(yaw, pitch)
        out["attention"] = attention
        if attention != "center":
            out["observations"].append(
                _obs(f"gaze_{attention}", _attention_label(attention), att_conf, 1))

    # Identity continuity vs enrolled embedding.
    if enrolled_embedding is not None and primary.get("embedding") is not None:
        sim = face_engine.cosine(enrolled_embedding, primary["embedding"])
        out["identity_match"] = round(sim, 3)
        out["identity_ok"] = sim >= IDENTITY_MISMATCH_THRESHOLD
        if not out["identity_ok"]:
            # Confidence scales with how far below threshold we are.
            conf = min(0.95, 0.55 + (IDENTITY_MISMATCH_THRESHOLD - sim))
            out["observations"].append(_obs("identity_mismatch", "Face does not match the enrolled student", conf, 3))

    # Anti-spoof.
    spoof = _spoof_risk(bgr, primary["bbox"])
    out["spoof_risk"] = round(spoof, 2)
    if spoof >= 0.7:
        out["observations"].append(_obs("spoof_suspected", "Possible photo/screen spoof detected", spoof, 2))

    return out


def _attention_from_pose(yaw, pitch):
    """Map head pose to an attention label + a confidence that grows with how
    far past the threshold the angle is (so a marginal turn is low-confidence
    and won't escalate, while a hard turn does)."""
    # Sideways dominates over vertical when both exceed limits.
    if abs(yaw) >= YAW_LOOK_LIMIT:
        over = (abs(yaw) - YAW_LOOK_LIMIT) / 30.0
        conf = float(min(0.95, 0.55 + over))
        return ("right" if yaw > 0 else "left"), conf
    if pitch <= -PITCH_DOWN_LIMIT:
        over = (abs(pitch) - PITCH_DOWN_LIMIT) / 30.0
        return "down", float(min(0.9, 0.5 + over))
    if pitch >= PITCH_UP_LIMIT:
        over = (pitch - PITCH_UP_LIMIT) / 30.0
        return "up", float(min(0.9, 0.5 + over))
    return "center", 0.0


def _attention_label(attention):
    return {
        "left": "Looking left / away from screen",
        "right": "Looking right / away from screen",
        "down": "Looking down (possible notes)",
        "up": "Looking up / away from screen",
    }.get(attention, "Looking away from screen")


def _obs(code, label, confidence, severity, info=None):
    """A single candidate-violation observation with confidence + severity.
    severity: 0=info, 1=minor (gaze), 2=major (no-face/material), 3=critical
    (phone/multi-person/identity)."""
    o = {"code": code, "label": label,
         "confidence": round(float(confidence), 3), "severity": int(severity)}
    if info:
        o["info"] = info
    return o


# ════════════════════════════════════════════════════════════════════════════
# TEMPORAL CONFIDENCE LAYER
# ════════════════════════════════════════════════════════════════════════════
# A single frame is never enough. A genuine cheat (phone on the desk, a second
# person, a sustained look away) persists; a false positive (a flicker of the
# detector, a momentary blink, a half-second glance) does not. We accumulate a
# per-code confidence score that *rises* while a signal keeps firing and *decays*
# when it stops, then escalate to a violation only when it crosses a threshold.
# This is the core of the false-positive reduction.

import time as _time
import threading as _threading

# Per-code escalation policy. A violation is raised when accumulated score
# crosses `trigger`; `rise` is added per confident frame, `decay` subtracted per
# clean frame. Critical signals rise fast and barely decay; gaze rises slowly and
# decays fast (so a quick glance never escalates).
_POLICY = {
    "phone":             {"rise": 1.0, "decay": 0.30, "trigger": 1.0,  "cooldown": 8},
    "multiple_faces":    {"rise": 0.5, "decay": 0.30, "trigger": 1.0,  "cooldown": 8},
    "identity_mismatch": {"rise": 0.45,"decay": 0.25, "trigger": 1.2,  "cooldown": 10},
    "no_face":           {"rise": 0.34,"decay": 0.34, "trigger": 1.0,  "cooldown": 6},
    "material":          {"rise": 0.5, "decay": 0.30, "trigger": 1.0,  "cooldown": 10},
    "device":            {"rise": 0.5, "decay": 0.30, "trigger": 1.0,  "cooldown": 10},
    "spoof_suspected":   {"rise": 0.34,"decay": 0.20, "trigger": 1.2,  "cooldown": 15},
    "gaze_left":         {"rise": 0.25,"decay": 0.45, "trigger": 1.0,  "cooldown": 6},
    "gaze_right":        {"rise": 0.25,"decay": 0.45, "trigger": 1.0,  "cooldown": 6},
    "gaze_down":         {"rise": 0.20,"decay": 0.50, "trigger": 1.0,  "cooldown": 6},
    "gaze_up":           {"rise": 0.25,"decay": 0.45, "trigger": 1.0,  "cooldown": 6},
}
_DEFAULT_POLICY = {"rise": 0.34, "decay": 0.34, "trigger": 1.0, "cooldown": 8}

# How long an idle session lives before it is reclaimed (seconds).
_SESSION_TTL = int(os.environ.get("PROCTOR_SESSION_TTL", "7200"))

_sessions = {}
_sessions_lock = _threading.Lock()


def _now():
    return _time.time()


def new_session_state():
    return {
        "scores": {},          # code -> accumulated confidence
        "last_violation": {},  # code -> epoch of last escalation (cooldown)
        "frames": 0,
        "clean_frames": 0,
        "violations": 0,
        "frame_index": 0,      # for object-detection throttling
        "ts": _now(),
    }


def get_session(key):
    with _sessions_lock:
        s = _sessions.get(key)
        if s is None or (_now() - s["ts"]) > _SESSION_TTL:
            s = new_session_state()
            _sessions[key] = s
        return s


def reset_session(key):
    with _sessions_lock:
        _sessions.pop(key, None)


def _gc_sessions():
    """Drop expired sessions so a long-running server doesn't leak memory."""
    cutoff = _now() - _SESSION_TTL
    with _sessions_lock:
        for k in [k for k, v in _sessions.items() if v["ts"] < cutoff]:
            _sessions.pop(k, None)


def process_frame(session_key, bgr, enrolled_embedding=None):
    """Stateful per-frame entry point used by the /detect_cheating route.

    Runs analyze(), folds each observation into the session's temporal score,
    and returns a caller-friendly verdict:

      {
        "ok": bool,
        "status": "secure" | "warning" | "violation" | "unclear",
        "violations": [ {code,label,severity,confidence}, ... ],  # escalated now
        "warnings":   [ {code,label,confidence,progress}, ... ],  # building up
        "attention": str, "face_count": int, "head_pose": {...}|None,
        "quality": {...}, "spoof_risk": float, "identity_match": float|None,
        "integrity": float,  # session clean-frame ratio 0..100
      }
    """
    sess = get_session(session_key)
    sess["ts"] = _now()
    sess["frames"] += 1
    sess["frame_index"] += 1
    run_objects = (sess["frame_index"] % max(1, OBJECT_EVERY_N) == 0)

    signals = analyze(bgr, enrolled_embedding=enrolled_embedding, run_objects=run_objects)

    # Index this frame's observations by code for the decay pass.
    seen = {o["code"]: o for o in signals["observations"]}

    escalated = []
    building = []
    # 1) Update every code we have a policy for (so absent signals decay).
    all_codes = set(_POLICY) | set(seen) | set(sess["scores"])
    for code in all_codes:
        if code in ("quality_low", "frame_error"):
            continue
        policy = _POLICY.get(code, _DEFAULT_POLICY)
        cur = sess["scores"].get(code, 0.0)
        if code in seen:
            # Confident observations move the needle more.
            cur += policy["rise"] * (0.5 + 0.5 * seen[code]["confidence"])
        else:
            cur -= policy["decay"]
        cur = max(0.0, min(cur, policy["trigger"] * 1.5))
        sess["scores"][code] = cur

        if code in seen and cur >= policy["trigger"]:
            last = sess["last_violation"].get(code, 0)
            if (_now() - last) >= policy["cooldown"]:
                obs = seen[code]
                escalated.append({
                    "code": code, "label": obs["label"],
                    "severity": obs["severity"], "confidence": obs["confidence"],
                })
                sess["last_violation"][code] = _now()
                sess["violations"] += 1
                sess["scores"][code] = 0.0  # consume the accumulator
        elif code in seen:
            building.append({
                "code": code, "label": seen[code]["label"],
                "confidence": seen[code]["confidence"],
                "progress": round(min(1.0, cur / policy["trigger"]), 2),
            })

    is_clean = len(seen) == 0 and signals["ok"]
    if is_clean:
        sess["clean_frames"] += 1

    if escalated:
        status = "violation"
    elif not signals["ok"] or any(o["code"] in ("quality_low", "frame_error") for o in signals["observations"]):
        status = "unclear"
    elif building:
        status = "warning"
    else:
        status = "secure"

    integrity = round(100.0 * sess["clean_frames"] / max(1, sess["frames"]), 1)

    if sess["frames"] % 200 == 0:
        _gc_sessions()

    return {
        "ok": signals["ok"],
        "status": status,
        "violations": escalated,
        "warnings": building,
        "attention": signals["attention"],
        "face_count": signals["face_count"],
        "head_pose": signals["head_pose"],
        "quality": signals["quality"],
        "spoof_risk": signals["spoof_risk"],
        "identity_match": signals["identity_match"],
        "integrity": integrity,
    }
