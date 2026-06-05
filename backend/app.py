from flask import Flask, render_template, request, redirect, session, jsonify, current_app
import mysql.connector
import mysql.connector.pooling
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import smtplib
import random
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
import threading
import re
import os
import logging
from collections import Counter
from html import escape
from werkzeug.exceptions import TooManyRequests
import base64
from dotenv import load_dotenv
import io
import time
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table,
    TableStyle, HRFlowable, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ================================================================
# APP INIT
# ================================================================
load_dotenv()
app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable set nahi hai!")

db_pool = mysql.connector.pooling.MySQLConnectionPool(
    pool_name="oems_pool",
    pool_size=10,
    host=os.environ.get("DB_HOST", "localhost"),
    user=os.environ.get("DB_USER", "root"),
    password=os.environ.get("DB_PASS"),
    database=os.environ.get("DB_NAME", "exam_system")
)

# ── DB Connection ──
def get_db_connection():
    return db_pool.get_connection()

# ── Face Auth Config ──
# Recognition tunables (thresholds, min size, model pack) live in face_engine.py.
# These are flow/session-level knobs for the login challenge.
import face_engine
import proctor_engine

FACE_AUTH_WINDOW_SECONDS = int(os.environ.get("FACE_AUTH_WINDOW_SECONDS", "600"))
# How many clean ArcFace embeddings to average before deciding register/verify.
FACE_REGISTER_FRAMES = int(os.environ.get("FACE_REGISTER_FRAMES", "6"))
FACE_VERIFY_FRAMES = int(os.environ.get("FACE_VERIFY_FRAMES", "4"))
# Minimum face-crop sharpness (variance of Laplacian) to accept a capture frame.
FACE_MIN_SHARPNESS = float(os.environ.get("FACE_MIN_SHARPNESS", "35.0"))
# Active-liveness: which challenge to require. "blink", "turn", or "any".
FACE_LIVENESS_CHALLENGE = os.environ.get("FACE_LIVENESS_CHALLENGE", "any").strip().lower()
# Per-session live capture state (server-authoritative; cannot be spoofed by JS).
# Keyed by student_id -> {"frames": [...], "challenge": {...}, "ts": epoch}.
_face_sessions = {}
_face_sessions_lock = threading.Lock()
_face_schema_checked = False
_face_schema_lock = threading.Lock()


def ensure_student_face_schema():
    """
    Migration-safe guard: adds face embedding column if missing.
    Called lazily from login flow so older DBs keep working.
    """
    global _face_schema_checked
    if _face_schema_checked:
        return
    with _face_schema_lock:
        if _face_schema_checked:
            return
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # ArcFace 512-d vectors are stored as raw float32 bytes (2048 bytes)
            # in a dedicated BLOB column. The legacy `face_embedding` LONGTEXT (if
            # present) held raw-pixel CSV vectors from the old engine — those are
            # incompatible with ArcFace, so they are NOT migrated; affected users
            # simply re-register once on next login.
            cursor.execute("SHOW COLUMNS FROM students LIKE 'face_embedding_v2'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE students ADD COLUMN face_embedding_v2 MEDIUMBLOB NULL")
                print("[FaceAuth] Added students.face_embedding_v2 column (ArcFace)")

            cursor.execute("SHOW COLUMNS FROM students LIKE 'face_registered'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE students ADD COLUMN face_registered TINYINT(1) NOT NULL DEFAULT 0")
                print("[FaceAuth] Added students.face_registered column")

            cursor.execute("SHOW COLUMNS FROM students LIKE 'face_registered_at'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE students ADD COLUMN face_registered_at DATETIME NULL")
                print("[FaceAuth] Added students.face_registered_at column")

            # Anyone whose face was registered only under the old engine must
            # re-enrol with ArcFace: clear their stale "registered" flag if they
            # have no v2 embedding yet.
            cursor.execute("""
                UPDATE students
                SET face_registered=0
                WHERE face_registered=1 AND face_embedding_v2 IS NULL
            """)
            _face_schema_checked = True
            conn.commit()
        except Exception as e:
            print(f"[FaceAuth] Schema check failed: {e}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

# ── SBERT Config ──
SBERT_MODEL_NAME = os.environ.get("SBERT_MODEL", "all-MiniLM-L6-v2")
print(f"[OEMS] SBERT model: {SBERT_MODEL_NAME}")

# ── Campus + Browser Helpers ──
_raw_ip = os.environ.get("CAMPUS_IP_RANGES", "10.104.242")
CAMPUS_IP_RANGES = [ip.strip() for ip in _raw_ip.split(",") if ip.strip()]

def is_secure_browser():
    return request.headers.get('X-OEMS-Secure-Browser') == 'ElectronV1'

def is_campus_ip():
    client_ip = request.remote_addr
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        client_ip = forwarded.split(',')[0].strip()
    return any(client_ip.startswith(p) for p in CAMPUS_IP_RANGES)

# ── First Name Helper ──
def get_first_name(full_name):
    if not full_name:
        return "there"
    return full_name.strip().split()[0]


def _decode_base64_frame(image_data_url):
    """Decode a data-URL camera frame into a BGR numpy array."""
    _ensure_cv_loaded()
    if not image_data_url or "," not in image_data_url:
        return None, "Invalid camera frame."
    try:
        raw = base64.b64decode(image_data_url.split(",", 1)[1])
        arr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None, "Camera frame decode failed."
        return frame, None
    except Exception:
        return None, "Camera frame decode failed."


# Friendly guidance text for each detector state, surfaced to the UI overlay.
_FACE_STATE_HINTS = {
    "no_face": "Bring your face into the circle.",
    "too_far": "Move a little closer to the camera.",
    "multiple_faces": "Only one person should be visible.",
    "embed_failed": "Hold still — couldn't read your face clearly.",
}


def _new_face_session():
    """Fresh server-side capture state for one login attempt."""
    return {
        "embeddings": [],     # clean ArcFace vectors collected this attempt
        "yaws": [],           # head-yaw history for the turn challenge
        "blink_seen": False,  # eyes-open -> eyes-closed -> eyes-open observed
        "turn_seen": False,   # head turned past threshold then re-centred
        "eye_open_prev": True,
        "ts": int(time.time()),
    }


def _get_face_session(student_id, reset=False):
    """Get-or-create the per-student capture session. Expires with the auth window."""
    with _face_sessions_lock:
        sess = _face_sessions.get(student_id)
        stale = sess and (int(time.time()) - sess["ts"]) > FACE_AUTH_WINDOW_SECONDS
        if reset or sess is None or stale:
            sess = _new_face_session()
            _face_sessions[student_id] = sess
        return sess


def _clear_face_session(student_id):
    with _face_sessions_lock:
        _face_sessions.pop(student_id, None)


def _update_liveness(sess, signals):
    """Track active-liveness challenges from a single analysed frame.
    Blink = eye-aspect-ratio dips closed then reopens. Turn = yaw passes the
    threshold in either direction. We only have 5-point kps from the detector,
    so blink uses a coarse eyes-detected fallback; turn is the primary, most
    reliable challenge."""
    sess["yaws"].append(signals["yaw"])

    # Turn challenge: any frame past the yaw threshold counts (then re-centre).
    if abs(signals["yaw"]) >= face_engine.YAW_TURN_DEG:
        sess["turn_seen"] = True

    # Blink challenge (best-effort): use eyes-open heuristic via Haar on the crop
    # is handled in _analyze_login_frame; here we trust the precomputed flag.
    eye_open = signals.get("eye_open", True)
    if sess["eye_open_prev"] and not eye_open:
        sess["_closing"] = True
    if sess.get("_closing") and eye_open:
        sess["blink_seen"] = True
        sess["_closing"] = False
    sess["eye_open_prev"] = eye_open


def _liveness_satisfied(sess):
    if FACE_LIVENESS_CHALLENGE == "blink":
        return sess["blink_seen"]
    if FACE_LIVENESS_CHALLENGE == "turn":
        return sess["turn_seen"]
    return sess["blink_seen"] or sess["turn_seen"]


def _liveness_prompt(sess):
    """What to ask the user to do next for the active-liveness challenge."""
    if _liveness_satisfied(sess):
        return "Liveness confirmed."
    if FACE_LIVENESS_CHALLENGE == "blink":
        return "Please blink once."
    if FACE_LIVENESS_CHALLENGE == "turn":
        return "Slowly turn your head left, then back."
    return "Blink or gently turn your head to confirm you're live."


def _analyze_login_frame(frame_bgr):
    """Run ArcFace analysis plus a coarse eyes-open check for blink detection.
    Returns (signals_dict, None) or (None, state_code)."""
    signals, err = face_engine.analyze_frame(frame_bgr)
    if err:
        return None, err
    # Coarse eyes-open detection on the face crop (for the blink challenge).
    # Reuses the proctoring Haar eye cascade — cheap and already loaded.
    eye_open = True
    try:
        x, y, w, h = signals["bbox"]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        roi = gray[max(0, y):y + h, max(0, x):x + w]
        if roi.size:
            eyes = eye_cascade.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=4, minSize=(18, 18))
            eye_open = len(eyes) > 0
    except Exception:
        eye_open = True
    signals["eye_open"] = eye_open
    return signals, None


def _find_duplicate_face(cursor, student_id, probe_embedding):
    """Scan other students' ArcFace embeddings for a near-duplicate (anti-sharing)."""
    cursor.execute(
        "SELECT id, name, admission_no, face_embedding_v2 FROM students "
        "WHERE id<>%s AND face_embedding_v2 IS NOT NULL",
        (student_id,)
    )
    best_student = None
    best_score = 0.0
    for row in cursor.fetchall():
        other_vec = face_engine.deserialize(row.get("face_embedding_v2"))
        if other_vec is None:
            continue
        score = face_engine.cosine(other_vec, probe_embedding)
        if score > best_score:
            best_score = score
            best_student = row
    if best_student and best_score >= face_engine.DUPLICATE_THRESHOLD:
        return best_student, best_score
    return None, best_score


def _finalize_student_session(student):
    session["student_id"] = student["id"]
    session["role"] = "student"
    session["student_name"] = student["name"]
    session["admission_no"] = student["admission_no"]
    session["program"] = student["program"]
    session["branch"] = student["branch"]
    session["semester"] = student["semester"]
    session.pop("pending_student_auth", None)


def _is_pending_face_auth_valid(pending_auth):
    if not pending_auth or not pending_auth.get("student_id"):
        return False
    created_at = int(pending_auth.get("created_at", 0))
    if created_at <= 0:
        return False
    return (int(time.time()) - created_at) <= FACE_AUTH_WINDOW_SECONDS


# ================================================================
# PDF GENERATOR
# ================================================================
def generate_result_pdf(student, exam, answers, total_score, percentage):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style   = ParagraphStyle('CustomTitle',    parent=styles['Title'],   fontSize=22, textColor=colors.HexColor('#1e293b'), spaceAfter=6,  fontName='Helvetica-Bold')
    heading_style = ParagraphStyle('SectionHeading', parent=styles['Heading2'],fontSize=13, textColor=colors.HexColor('#4f46e5'), spaceBefore=14,spaceAfter=6, fontName='Helvetica-Bold')
    normal_style  = ParagraphStyle('CustomNormal',   parent=styles['Normal'],  fontSize=10, textColor=colors.HexColor('#374151'), spaceAfter=4,  leading=16)
    muted_style   = ParagraphStyle('Muted',          parent=styles['Normal'],  fontSize=9,  textColor=colors.HexColor('#6b7280'), spaceAfter=3)
    story = []
    story.append(Paragraph("OEMS — Examination Result Report", title_style))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#4f46e5'), spaceAfter=12))
    story.append(Paragraph("Student Details", heading_style))
    detail_data = [
        ['Field', 'Value'],
        ['Student Name',    str(student.get('name', 'N/A'))],
        ['Admission No',    str(student.get('admission_no', 'N/A'))],
        ['Email',           str(student.get('email', 'N/A') or 'Not provided')],
        ['Program (Branch) - Sem', f"{student.get('program','N/A')} ({student.get('branch','N/A')}) — Semester ({student.get('semester','N/A')})"],
        ['Course Name',     str(exam.get('title', 'N/A'))],
        ['Exam Date & Time',str(exam.get('start_time', 'N/A'))],
        ['Report Generated',datetime.now().strftime('%d %b %Y, %I:%M %p')],
    ]
    detail_table = Table(detail_data, colWidths=[5*cm, 12*cm])
    detail_table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#4f46e5')),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,0),10),
        ('BACKGROUND',(0,1),(0,-1),colors.HexColor('#f1f5f9')),('FONTNAME',(0,1),(0,-1),'Helvetica-Bold'),
        ('FONTSIZE',(0,1),(-1,-1),10),('TEXTCOLOR',(0,1),(-1,-1),colors.HexColor('#374151')),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8fafc')]),
        ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#e2e8f0')),('PADDING',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Question-wise Analysis", heading_style))
    for idx, ans in enumerate(answers, 1):
        q_text = str(ans.get('question_text','N/A'))
        student_ans = str(ans.get('student_answer','') or '').strip() or 'Not Answered'
        q_marks = ans.get('marks', 0)
        score   = ans.get('score', 0) or 0
        feedback= str(ans.get('feedback','Pending evaluation') or 'Pending evaluation')
        q_data = [
            [Paragraph(f'<b>Q{idx}.</b> {q_text}', normal_style), ''],
            ['Student Answer:', Paragraph(student_ans, normal_style)],
            ['Marks Obtained:', Paragraph(f'<b>{score} / {q_marks}</b>', ParagraphStyle('Score', parent=normal_style, textColor=colors.HexColor('#16a34a') if score > 0 else colors.HexColor('#dc2626')))],
            ['Feedback:', Paragraph(feedback, muted_style)],
        ]
        q_table = Table(q_data, colWidths=[4*cm, 13*cm])
        q_table.setStyle(TableStyle([
            ('SPAN',(0,0),(-1,0)),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eef2ff')),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('BACKGROUND',(0,1),(0,-1),colors.HexColor('#f8fafc')),
            ('FONTNAME',(0,1),(0,-1),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),10),
            ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#e2e8f0')),('PADDING',(0,0),(-1,-1),8),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#fafafa')]),
        ]))
        story.append(q_table)
        story.append(Spacer(1, 8))
    story.append(PageBreak())
    story.append(Paragraph("Performance Summary", heading_style))
    total_q  = len(answers)
    attempted= sum(1 for a in answers if str(a.get('student_answer','') or '').strip())
    max_marks= sum(a.get('marks',0) for a in answers)
    pct      = round(percentage, 1)
    grade    = 'O (Outstanding)' if pct>=90 else 'A (Excellent)' if pct>=75 else 'B (Good)' if pct>=60 else 'C (Average)' if pct>=45 else 'D (Pass)' if pct>=35 else 'F (Fail)'
    summary_data = [['Metric','Value'],['Total Questions',str(total_q)],['Attempted',str(attempted)],['Not Attempted',str(total_q-attempted)],['Total Score',f'{total_score} / {max_marks}'],['Percentage',f'{pct}%'],['Grade',grade]]
    summary_table = Table(summary_data, colWidths=[8*cm, 9*cm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#4f46e5')),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,0),10),
        ('BACKGROUND',(0,1),(0,-1),colors.HexColor('#f1f5f9')),('FONTNAME',(0,1),(0,-1),'Helvetica-Bold'),
        ('FONTSIZE',(0,1),(-1,-1),11),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8fafc')]),
        ('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#e2e8f0')),('PADDING',(0,0),(-1,-1),10),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 16))
    story.append(Paragraph("Performance Feedback", heading_style))
    if pct>=75: fb = f"Excellent performance! The student scored {pct}% demonstrating strong understanding. Keep it up."
    elif pct>=50: fb = f"Good attempt. The student scored {pct}%. Review deducted questions to improve further."
    elif pct>=35: fb = f"The student scored {pct}%. Basic concepts understood but more practice needed."
    else:         fb = f"The student scored {pct}%. Significant improvement needed. Please review all course material."
    fb_table = Table([[Paragraph(fb, normal_style)]], colWidths=[17*cm])
    fb_table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#eef2ff')),('BORDER',(0,0),(-1,-1),1,colors.HexColor('#c7d2fe')),('PADDING',(0,0),(-1,-1),12)]))
    story.append(fb_table)
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0'), spaceBefore=10))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Generated by OEMS — Online Examination & Monitoring System | {datetime.now().strftime('%d %b %Y, %I:%M %p')}", ParagraphStyle('Footer', parent=muted_style, alignment=TA_CENTER, fontSize=8)))
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


# ================================================================
# AI EVALUATION ENGINE — SBERT only
# ================================================================
class EvaluationUnavailable(RuntimeError):
    """Raised when SBERT could not run, so marks must stay pending."""

_sbert_model = None
_sbert_lock = threading.Lock() # Added lock to prevent thread collisions during bulk evaluation

# EXPANDED STOP WORDS: Includes academic connectors to protect good students
_EVAL_STOP_WORDS = {
    "the","a","an","is","are","am","was","were","of","in","on","at","to","and","or",
    "it","this","that","these","those","with","for","by","be","been","being","has",
    "have","had","can","will","would","could","should","as","from","into","than",
    "then","there","their","its","if","but","so","such","also","very",
    "wherein", "where", "when", "why", "how", "multiple", "held", "via", "between",
    "through", "during", "occur", "occurs", "cause", "causes", "due", "about",
    "which", "who", "whom", "all", "any", "both", "each", "other", "some", "no",
    "not", "only", "same", "too", "while", "until", "upon", "within", "without"
}

def _get_sbert():
    global _sbert_model
    if _sbert_model is not None:
        return _sbert_model
    try:
        from sentence_transformers import SentenceTransformer
        _sbert_model = SentenceTransformer(SBERT_MODEL_NAME)
        print(f"[OEMS] SBERT model loaded ({SBERT_MODEL_NAME})")
        return _sbert_model
    except ImportError as e:
        raise EvaluationUnavailable(
            "sentence-transformers is not installed. Install it before running AI evaluation."
        ) from e
    except Exception as e:
        raise EvaluationUnavailable(f"SBERT model load failed: {str(e)[:100]}") from e

def _tokenize_answer(text):
    return re.findall(r"[a-zA-Z0-9]+(?:'[a-z]+)?", (text or "").lower())

def _answer_quality_guard(student_answer):
    """
    Final Guardrail: Safely passes normal to dense academic text while blocking
    pure keyword lists and repetition loops. Designed for real-world student inputs.
    """
    answer = (student_answer or "").strip()
    tokens = _tokenize_answer(answer)

    if len(answer) < 10 or len(tokens) < 3:
        return 0.0, "Answer too short or not provided."

    meaningful = [w for w in tokens if w not in _EVAL_STOP_WORDS and len(w) > 2]

    # 1. Advanced Repetition Check
    if len(meaningful) > 4:
        wc = Counter(meaningful)
        top_word, top_count = wc.most_common(1)[0]

        # Fails if a single word dominates 40%+ of the answer
        if top_count >= 3 and (top_count / max(len(tokens), 1)) >= 0.4:
            return 0.0, f"Keyword stuffing: '{top_word}' repeated excessively."

        # Fails if the top 3 words loop endlessly, making up 70%+ of the text
        top_3_count = sum(count for _, count in wc.most_common(3))
        if len(tokens) > 10 and (top_3_count / len(tokens)) >= 0.7:
            return 0.0, "Excessive repetition of a few keywords detected."

    # 2. Pure Keyword Density Check
    # Normal students: 50-65%. Dense academic: ~85-88%. Pure keyword spam: 95-100%.
    meaningful_ratio = len(meaningful) / max(len(tokens), 1)
    if meaningful_ratio >= 0.92 and len(tokens) > 5:
         return 0.0, "Keyword list detected without proper grammatical explanation."

    # Passed! Let SBERT assign the semantic score.
    return None

def evaluate_answer(question, student_answer, max_marks, model_answer=""):
    """
    SBERT-only semantic evaluation.
    Thread-safe version for background processing.
    """
    student_answer = (student_answer or "").strip()
    model_answer   = (model_answer   or "").strip()

    guard_result = _answer_quality_guard(student_answer)
    if guard_result is not None:
        score, feedback = guard_result
        print(f"[SBERT Guard] -> {score}/{max_marks} | {feedback}")
        return score, feedback

    model = _get_sbert()
    reference = model_answer or (question or "").strip()
    if not reference:
        raise EvaluationUnavailable("No reference answer or question text available for SBERT evaluation.")

    try:
        from sklearn.metrics.pairwise import cosine_similarity as _cos_sim
        # Lock SBERT encoding to prevent concurrency crashes when multiple students are evaluated at once
        with _sbert_lock:
            embeddings = model.encode([reference, student_answer], convert_to_numpy=True)

        sim = float(_cos_sim([embeddings[0]], [embeddings[1]])[0][0])
        sim = max(0.0, min(1.0, sim))
    except Exception as e:
        raise EvaluationUnavailable(f"SBERT evaluation failed: {str(e)[:100]}") from e

    ref_tokens = [w for w in _tokenize_answer(reference) if w not in _EVAL_STOP_WORDS and len(w) > 2]
    ans_tokens = [w for w in _tokenize_answer(student_answer) if w not in _EVAL_STOP_WORDS and len(w) > 2]
    length_factor = min(len(ans_tokens) / max(len(ref_tokens) * 0.35, 6), 1.0)
    final_sim = sim * length_factor

    if sim < 0.30 or final_sim < 0.25:
        score = 0.0
    else:
        score = max(0.0, min(float(max_marks), round(final_sim * max_marks * 2) / 2))

    pct = round(sim * 100)
    if score == 0:
        feedback = f"Answer lacks correct explanation ({pct}% semantic match)."
    elif pct >= 80:
        feedback = f"Excellent answer with strong semantic match ({pct}%)."
    elif pct >= 65:
        feedback = f"Good answer with minor missing points ({pct}% match)."
    elif pct >= 45:
        feedback = f"Partial answer; key explanation is incomplete ({pct}% match)."
    else:
        feedback = f"Weak answer; limited semantic match ({pct}%)."

    print(f"[SBERT Eval] -> {score}/{max_marks} | sim={pct}% | length_factor={round(length_factor, 2)}")
    return score, feedback


# ── Background Evaluation Thread ──
def run_background_evaluation(student_id, exam_id, app_context):
    with app_context:
        try:
            conn   = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            # FIX: correct_answer (model answer) bhi fetch karo
            cursor.execute("""SELECT a.id, a.answer, q.question_text, q.marks,
                                     q.question_type, q.correct_answer AS model_answer
                FROM answers a JOIN questions q ON a.question_id = q.id
                WHERE a.student_id=%s AND a.exam_id=%s AND q.question_type='theory' AND a.score IS NULL
            """, (student_id, exam_id))
            theory_answers = cursor.fetchall()
            evaluation_error = None
            for ans in theory_answers:
                student_answer = (ans['answer'] or '').strip()
                model_answer   = (ans['model_answer'] or '').strip()
                if not student_answer or len(student_answer) < 10:
                    score, feedback = 0, "No answer submitted or too short."
                else:
                    # Pass model_answer to strict evaluator
                    try:
                        score, feedback = evaluate_answer(
                            ans['question_text'], student_answer, ans['marks'], model_answer
                        )
                    except EvaluationUnavailable as e:
                        evaluation_error = str(e)
                        print(f"[BG Eval] Student {student_id}, exam {exam_id}: {evaluation_error}")
                        break
                cursor.execute("UPDATE answers SET score=%s, feedback=%s WHERE id=%s", (round(score, 1), feedback, ans['id']))

            if evaluation_error:
                cursor.execute("""
                    UPDATE answers a
                    JOIN questions q ON a.question_id=q.id
                    SET a.feedback=%s
                    WHERE a.student_id=%s AND a.exam_id=%s
                      AND q.question_type='theory'
                      AND a.score IS NULL
                """, ("Evaluation pending. Please retry after evaluator is available.", student_id, exam_id))
                cursor.execute("""
                    UPDATE results SET submission_status='Pending'
                    WHERE student_id=%s AND exam_id=%s
                      AND submission_status NOT IN ('Hold','Disqualified')
                """, (student_id, exam_id))
                conn.commit()
                cursor.close()
                conn.close()
                return

            conn.commit()
            cursor.execute("""
                SELECT COUNT(*) AS pending
                FROM answers a
                JOIN questions q ON a.question_id=q.id
                WHERE a.student_id=%s AND a.exam_id=%s
                  AND q.question_type='theory'
                  AND a.score IS NULL
            """, (student_id, exam_id))
            pending_theory = cursor.fetchone()['pending']
            if pending_theory > 0:
                cursor.execute("""
                    UPDATE results SET submission_status='Pending'
                    WHERE student_id=%s AND exam_id=%s
                      AND submission_status NOT IN ('Hold','Disqualified')
                """, (student_id, exam_id))
                conn.commit()
                cursor.close()
                conn.close()
                print(f"[BG Eval] Student {student_id}, exam {exam_id}: {pending_theory} answer(s) still pending; no email sent")
                return

            cursor.execute("SELECT COALESCE(SUM(a.score),0) AS total FROM answers a WHERE a.student_id=%s AND a.exam_id=%s", (student_id, exam_id))
            total_score = float(cursor.fetchone()['total'] or 0)
            # Set Evaluated only if not Hold/Disqualified
            cursor.execute("""
                UPDATE results SET total_score=%s, submission_status='Evaluated'
                WHERE student_id=%s AND exam_id=%s
                  AND submission_status NOT IN ('Hold','Disqualified')
            """, (round(total_score, 2), student_id, exam_id))
            updated_results = cursor.rowcount
            conn.commit()
            if updated_results == 0:
                cursor.close()
                conn.close()
                print(f"[BG Eval] Student {student_id}, exam {exam_id}: result not marked Evaluated; no email sent")
                return

            cursor.execute("SELECT * FROM students WHERE id=%s", (student_id,))
            student = cursor.fetchone()
            cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
            exam = cursor.fetchone()
            cursor.execute("""SELECT q.question_text, q.marks, q.question_type, a.answer AS student_answer, a.score, a.feedback
                FROM answers a JOIN questions q ON a.question_id=q.id WHERE a.student_id=%s AND a.exam_id=%s ORDER BY q.id""", (student_id, exam_id))
            all_answers = cursor.fetchall()
            cursor.close()
            conn.close()
            for ans in all_answers:
                if ans['score']         is None: ans['score']         = 0
                if not ans['feedback']:          ans['feedback']      = 'Evaluated'
                if not ans['student_answer']:    ans['student_answer'] = ''
            max_marks  = sum(a['marks'] for a in all_answers)
            percentage = (total_score / max_marks * 100) if max_marks > 0 else 0
            pdf_bytes  = generate_result_pdf(student, exam, all_answers, total_score, percentage)
            zero_reasons = []
            if total_score <= 0:
                for idx, ans in enumerate(all_answers, 1):
                    if float(ans.get('score') or 0) == 0:
                        reason = ans.get('feedback') or "No marks awarded."
                        zero_reasons.append(f"Q{idx}: {reason}")
            student_email = student.get('email')
            if student_email and is_valid_email(student_email):
                send_result_email(student, exam, total_score, percentage, pdf_bytes, zero_reasons=zero_reasons)
        except Exception as e:
            print(f"[BG Eval] ERROR: {e}")
            import traceback; traceback.print_exc()


# ================================================================
# EMAIL FUNCTIONS
# ================================================================
def send_result_email(student, exam, total_score, percentage, pdf_bytes, zero_reasons=None):
    from email.mime.base import MIMEBase
    from email import encoders
    try:
        first_name   = get_first_name(student.get('name','Student'))
        student_email= student.get('email')
        exam_title   = exam.get('title','Exam')
        pct          = round(percentage, 1)
        result_word  = "PASS" if pct >= 35 else "FAIL"
        result_color = "#2e7d32" if pct >= 35 else "#c62828"
        zero_reasons = zero_reasons or []
        zero_reason_html = ""
        if total_score <= 0:
            reason_items = "".join(
                f"<li style=\"margin-bottom:6px;\">{escape(str(reason))}</li>"
                for reason in zero_reasons[:8]
            ) or "<li>No marks were awarded based on the submitted answers.</li>"
            zero_reason_html = f"""
      <div style="background:#fff1f2;border:1px solid #fecdd3;border-radius:6px;padding:14px 16px;margin:0 0 18px;">
        <p style="margin:0 0 8px;color:#991b1b;font-size:14px;font-weight:700;">Why your score is 0.00</p>
        <ul style="margin:0;padding-left:18px;color:#7f1d1d;font-size:13px;line-height:1.5;">{reason_items}</ul>
      </div>
            """
        ref_id = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = MIMEMultipart('mixed')
        msg['From']    = formataddr(('OEMS Examination Team', EMAIL_CONFIG['SENDER_EMAIL']))
        msg['To']      = student_email
        msg['Subject'] = f"Exam Result: {exam_title}" if total_score > 0 else f"Exam Result: {exam_title} - Score 0.00"
        html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px;margin:0;">
<table align="center" width="100%" style="max-width:500px;background:#fff;border-radius:8px;border:1px solid #e0e0e0;margin:0 auto;border-collapse:collapse;">
  <tr><td style="background:#e8f5e9;padding:16px 20px;text-align:center;border-radius:8px 8px 0 0;border-bottom:1px solid #c8e6c9;">
    <p style="margin:0;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:1px;">OEMS Examination Team</p>
    <h2 style="margin:4px 0 0;color:#1e293b;font-size:20px;font-weight:700;">Exam Result Published</h2>
  </td></tr>
  <tr><td style="padding:28px 24px;">
    <p style="margin:0 0 16px;color:#333;font-size:15px;">Hi <strong>{first_name}</strong>,</p>
      <p style="margin:0 0 16px;color:#555;font-size:14px;line-height:1.6;">Your result for <strong>{exam_title}</strong> has been evaluated.</p>
      <table width="100%" style="background:#f8fafc;border-radius:6px;border:1px solid #e0e0e0;margin-bottom:20px;border-collapse:collapse;">
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;width:50%;">Total Score</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;border-bottom:1px solid #eee;text-align:right;"><strong>{total_score}</strong></td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;">Percentage</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;border-bottom:1px solid #eee;text-align:right;"><strong>{pct}%</strong></td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;">Result</td>
            <td style="padding:10px 14px;font-size:14px;text-align:right;"><strong style="color:{result_color};">{result_word}</strong></td></tr>
      </table>
      {zero_reason_html}
      <p style="margin:0 0 20px;font-size:13px;color:#888;line-height:1.5;">Detailed question-wise analysis is attached as a PDF report.</p>
    <p style="margin:0 0 4px;color:#333;font-size:14px;"><strong>Regards,</strong></p>
    <p style="margin:0;color:#555;font-size:14px;">OEMS Examination Team</p>
  </td></tr>
  <tr><td style="padding:12px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:11px;color:#aaa;">Ref ID: {ref_id}</p>
  </td></tr>
</table></body></html>"""
        msg.attach(MIMEText(html, 'html'))
        pdf_part = MIMEBase('application', 'octet-stream')
        pdf_part.set_payload(pdf_bytes)
        encoders.encode_base64(pdf_part)
        safe_name = exam_title.replace(' ', '_')[:30]
        pdf_part.add_header('Content-Disposition', f'attachment; filename="OEMS_Result_{safe_name}.pdf"')
        msg.attach(pdf_part)
        with smtplib.SMTP(EMAIL_CONFIG['SMTP_SERVER'], EMAIL_CONFIG['SMTP_PORT']) as server:
            server.starttls()
            server.login(EMAIL_CONFIG['SENDER_EMAIL'], EMAIL_CONFIG['APP_PASSWORD'])
            server.sendmail(EMAIL_CONFIG['SENDER_EMAIL'], student_email, msg.as_string())
        print(f"[Email] Result sent to {student_email}")
    except Exception as e:
        print(f"[Email] Failed: {e}")


def send_hold_email(student, exam, hold_reasons):
    try:
        student_email = student.get('email')
        if not student_email or not is_valid_email(student_email):
            return

        first_name = get_first_name(student.get('name', 'Student'))
        exam_title = exam.get('title', 'Exam') if exam else 'Exam'
        reason_items = "".join(
            f"<li style=\"margin-bottom:6px;\">{escape(str(reason))}</li>"
            for reason in hold_reasons[:8]
        ) or f"<li>Similarity was equal to or above the plagiarism threshold of {PLAGIARISM_THRESHOLD}%.</li>"
        ref_id = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        msg = MIMEMultipart('alternative')
        msg['From']    = formataddr(('OEMS Examination Team', EMAIL_CONFIG['SENDER_EMAIL']))
        msg['To']      = student_email
        msg['Subject'] = f"Result On Hold: {exam_title}"

        html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px;margin:0;">
<table align="center" width="100%" style="max-width:520px;background:#fff;border-radius:8px;border:1px solid #e0e0e0;margin:0 auto;border-collapse:collapse;">
  <tr><td style="background:#fff8e1;padding:16px 20px;text-align:center;border-radius:8px 8px 0 0;border-bottom:1px solid #fde68a;">
    <p style="margin:0;font-size:12px;color:#92400e;text-transform:uppercase;letter-spacing:1px;">OEMS Examination Team</p>
    <h2 style="margin:4px 0 0;color:#78350f;font-size:20px;font-weight:700;">Result Placed On Hold</h2>
  </td></tr>
  <tr><td style="padding:28px 24px;">
    <p style="margin:0 0 16px;color:#333;font-size:15px;">Hi <strong>{escape(first_name)}</strong>,</p>
    <p style="margin:0 0 16px;color:#555;font-size:14px;line-height:1.6;">Your result for <strong>{escape(str(exam_title))}</strong> is currently on hold for review.</p>
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:14px 16px;margin:0 0 18px;">
      <p style="margin:0 0 8px;color:#92400e;font-size:14px;font-weight:700;">Reason</p>
      <ul style="margin:0;padding-left:18px;color:#78350f;font-size:13px;line-height:1.5;">{reason_items}</ul>
    </div>
    <p style="margin:0 0 20px;font-size:13px;color:#666;line-height:1.5;">An administrator will review the submission. If it is released, your answers will be evaluated again and the final result will be emailed to you.</p>
    <p style="margin:0 0 4px;color:#333;font-size:14px;"><strong>Regards,</strong></p>
    <p style="margin:0;color:#555;font-size:14px;">OEMS Examination Team</p>
  </td></tr>
  <tr><td style="padding:12px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:11px;color:#aaa;">Ref ID: {ref_id}</p>
  </td></tr>
</table></body></html>"""

        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(EMAIL_CONFIG['SMTP_SERVER'], EMAIL_CONFIG['SMTP_PORT']) as server:
            server.starttls()
            server.login(EMAIL_CONFIG['SENDER_EMAIL'], EMAIL_CONFIG['APP_PASSWORD'])
            server.sendmail(EMAIL_CONFIG['SENDER_EMAIL'], student_email, msg.as_string())
        print(f"[Email] Hold notice sent to {student_email}")
    except Exception as e:
        print(f"[Email] Hold notice failed: {e}")


# ================================================================
# ── HOME
# ================================================================
@app.route("/")
def home():
    return render_template("home.html")

# ── STUDENT LOGIN (Password step → face verification required every login)
@app.route("/student_login", methods=["POST"])
def student_login():
    admission_no = (request.form.get("admission_no") or "").strip().upper()
    password = request.form.get("password")

    ensure_student_face_schema()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students WHERE admission_no=%s", (admission_no,))
    student = cursor.fetchone()
    cursor.close()
    conn.close()

    if student and check_password_hash(student["password"], password):
        session.clear()
        session["pending_student_auth"] = {
            "student_id": student["id"],
            "created_at": int(time.time())
        }
        return redirect("/student_face_verify")
    return "Invalid Admission Number or Password ❌"


@app.route("/student_login", methods=["GET"])
def student_login_get():
    return redirect("/")


@app.route("/student_face_verify", methods=["GET"])
def student_face_verify():
    pending = session.get("pending_student_auth")
    if not _is_pending_face_auth_valid(pending):
        session.pop("pending_student_auth", None)
        return redirect("/")

    ensure_student_face_schema()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, name, admission_no, face_embedding_v2, face_registered FROM students WHERE id=%s",
        (pending["student_id"],)
    )
    student = cursor.fetchone()
    cursor.close()
    conn.close()

    if not student:
        session.pop("pending_student_auth", None)
        return redirect("/")

    # Fresh capture state for this visit so a reload starts clean.
    _get_face_session(student["id"], reset=True)

    is_registered = bool(student.get("face_registered")) and student.get("face_embedding_v2") is not None
    face_mode = "verify" if is_registered else "register"
    frame_target = FACE_VERIFY_FRAMES if face_mode == "verify" else FACE_REGISTER_FRAMES

    return render_template(
        "student_face_verify.html",
        student_name=student["name"],
        admission_no=student["admission_no"],
        face_mode=face_mode,
        frame_target=frame_target,
        engine_ready=face_engine.is_available(),
    )


@app.route("/student_face_frame", methods=["POST"])
def student_face_frame():
    """Single streaming endpoint. The browser POSTs ~3-4 frames/sec; the server
    is authoritative for detection guidance, the active-liveness challenge, clean
    frame collection, and the final register/verify decision. No manual button,
    no second request — when enough good frames pass liveness, this returns the
    redirect (verify) or relogin (register) directly."""
    pending = session.get("pending_student_auth")
    if not _is_pending_face_auth_valid(pending):
        session.pop("pending_student_auth", None)
        return jsonify({"ok": False, "state": "expired",
                        "message": "Session expired. Please login again."}), 401

    if not face_engine.is_available():
        return jsonify({"ok": False, "state": "engine_error",
                        "message": "Face engine is unavailable. Contact support."}), 503

    student_id = pending["student_id"]
    frame, decode_error = _decode_base64_frame((request.get_json() or {}).get("image"))
    if decode_error:
        return jsonify({"ok": False, "state": "decode_error", "message": decode_error}), 400

    signals, state = _analyze_login_frame(frame)
    if state is not None:
        # Not a usable frame yet — return live guidance, keep waiting.
        return jsonify({
            "ok": True, "state": state, "done": False,
            "message": _FACE_STATE_HINTS.get(state, "Hold steady and look at the camera."),
        })

    # Reject blurry captures so we never store a smeared embedding.
    if signals["sharpness"] < FACE_MIN_SHARPNESS:
        return jsonify({"ok": True, "state": "blurry", "done": False,
                        "message": "Hold still — image is a little blurry."})

    sess = _get_face_session(student_id)
    _update_liveness(sess, signals)
    sess["embeddings"].append(signals["embedding"])
    sess["ts"] = int(time.time())

    # Determine mode from current DB state (registered? -> verify, else register).
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students WHERE id=%s", (student_id,))
    student = cursor.fetchone()
    if not student:
        cursor.close(); conn.close()
        session.pop("pending_student_auth", None)
        _clear_face_session(student_id)
        return jsonify({"ok": False, "state": "no_student",
                        "message": "Student record not found. Login again."}), 404

    stored = face_engine.deserialize(student.get("face_embedding_v2"))
    mode = "verify" if (student.get("face_registered") and stored is not None) else "register"
    target = FACE_VERIFY_FRAMES if mode == "verify" else FACE_REGISTER_FRAMES

    captured = len(sess["embeddings"])
    live_ok = _liveness_satisfied(sess)

    # Still collecting frames or awaiting the liveness challenge — report progress.
    if captured < target or not live_ok:
        cursor.close(); conn.close()
        return jsonify({
            "ok": True, "state": "scanning", "done": False,
            "captured": captured, "target": target,
            "liveness_ok": live_ok,
            "message": _liveness_prompt(sess) if not live_ok
                       else f"Scanning… {min(captured, target)}/{target}",
        })

    # Enough clean frames + liveness passed → finalise.
    probe = face_engine.average_embedding(sess["embeddings"][-max(target, 1):])
    if probe is None:
        cursor.close(); conn.close()
        _get_face_session(student_id, reset=True)
        return jsonify({"ok": True, "state": "retry", "done": False,
                        "message": "Couldn't build a clean face profile. Let's retry."})

    if mode == "register":
        dup_student, dup_score = _find_duplicate_face(cursor, student_id, probe)
        if dup_student is not None:
            cursor.close(); conn.close()
            _clear_face_session(student_id)
            return jsonify({
                "ok": False, "state": "duplicate", "done": True,
                "message": "This face is already registered to another account. Contact admin support.",
            }), 409

        cursor.execute(
            "UPDATE students SET face_embedding_v2=%s, face_registered=1, "
            "face_registered_at=NOW() WHERE id=%s",
            (face_engine.serialize(probe), student_id)
        )
        conn.commit()
        cursor.close(); conn.close()
        session.pop("pending_student_auth", None)
        _clear_face_session(student_id)
        # New enrolment → drop any cached embedding so proctoring identity
        # checks pick up the fresh face immediately.
        _enrolled_emb_cache.pop(student_id, None)
        return jsonify({
            "ok": True, "state": "registered", "done": True, "relogin": True,
            "message": "Face registered successfully. Please log in again to continue.",
        })

    # mode == "verify"
    if stored is None:
        cursor.close(); conn.close()
        session.pop("pending_student_auth", None)
        _clear_face_session(student_id)
        return jsonify({"ok": False, "state": "not_registered", "done": True,
                        "message": "Face profile missing. Please log in again."}), 400

    similarity = face_engine.cosine(stored, probe)
    cursor.close(); conn.close()

    if similarity >= face_engine.MATCH_THRESHOLD:
        _finalize_student_session(student)
        _clear_face_session(student_id)
        return jsonify({
            "ok": True, "state": "matched", "done": True,
            "redirect": "/student", "similarity": round(similarity, 3),
            "message": "Identity confirmed. Redirecting…",
        })

    # Mismatch — reset capture so the student can retry cleanly.
    _get_face_session(student_id, reset=True)
    return jsonify({
        "ok": False, "state": "mismatch", "done": False,
        "similarity": round(similarity, 3),
        "message": "Face didn't match our records. Re-centre and we'll try again.",
    })

# ── ADMIN LOGIN
@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        admin_id = request.form.get("admin_id")
        password = request.form.get("password")
        conn   = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM admins WHERE admin_id=%s", (admin_id,))
        admin = cursor.fetchone()
        cursor.close(); conn.close()
        if admin and check_password_hash(admin["password"], password):
            session["admin_id"]     = admin["admin_id"]
            session["role"]         = "admin"
            session["admin_name"]   = admin["name"]
            session["admin_branch"] = admin["branch"]
            return redirect("/admin")
        return "Invalid Admin ID or Password ❌"
    return render_template("admin_login.html")

# ── LOGIN REQUIRED DECORATOR
def login_required(role):
    def wrapper(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if role == "admin"   and "admin_id"   not in session: return redirect("/")
            if role == "student" and "student_id" not in session: return redirect("/")
            return f(*args, **kwargs)
        return decorated_function
    return wrapper

# ── ADMIN DASHBOARD
@app.route("/admin")
@login_required("admin")
def admin_dashboard():
    admin_branch = session.get("admin_branch")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if admin_branch == "ALL":
        cursor.execute("SELECT * FROM exams"); exams = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS total FROM students");            total_students    = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM exams WHERE status='publish'"); active_exams = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM answers WHERE score IS NULL");  pending_ai_checks = cursor.fetchone()["total"]
    else:
        cursor.execute("SELECT * FROM exams WHERE branch=%s", (admin_branch,)); exams = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) AS total FROM students WHERE branch=%s", (admin_branch,));            total_students    = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM exams WHERE status='publish' AND branch=%s", (admin_branch,)); active_exams = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(a.id) AS total FROM answers a JOIN exams e ON a.exam_id=e.id WHERE a.score IS NULL AND e.branch=%s", (admin_branch,)); pending_ai_checks = cursor.fetchone()["total"]
    cursor.close(); conn.close()
    return render_template("admin_dashboard.html", exams=exams, total_students=total_students, active_exams=active_exams, pending_ai_checks=pending_ai_checks, admin_branch=admin_branch)

# ── STUDENT MANAGER
@app.route("/student_manager")
@login_required("admin")
def students():
    admin_branch = session.get("admin_branch")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if admin_branch == "ALL":
        cursor.execute("SELECT * FROM students ORDER BY program, semester, admission_no, name")
    else:
        cursor.execute("SELECT * FROM students WHERE branch=%s ORDER BY program, semester, admission_no, name", (admin_branch,))
    students = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template("student_manager.html", students=students, admin_branch=admin_branch)

# ── ADD STUDENT (Single + Bulk CSV + Welcome Email)
@app.route("/add_student", methods=["GET", "POST"])
@login_required("admin")
def add_student():
    admin_branch = session.get("admin_branch")
    if request.method == "POST":
        mode = request.form.get("mode", "single")  # single | bulk

        if mode == "bulk":
            # ── BULK CSV ADD ──
            import csv, io as _io
            file      = request.files.get("csv_file")
            program   = request.form.get("program", "")
            semester  = request.form.get("semester", "")
            branch    = request.form.get("branch") if admin_branch == "ALL" else admin_branch
            send_mail = request.form.get("send_welcome_email") == "1"

            if not file or not file.filename.endswith(".csv"):
                return render_template("add_student.html", admin_branch=admin_branch,
                    bulk_error="Please upload a valid .csv file.")

            stream  = _io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
            reader  = csv.DictReader(stream)
            headers = set(h.strip().lower() for h in (reader.fieldnames or []))

            if not {"name", "admission_no"}.issubset(headers):
                return render_template("add_student.html", admin_branch=admin_branch,
                    bulk_error="CSV missing required columns: name, admission_no")

            conn   = get_db_connection()
            cursor = conn.cursor()
            added = 0; skipped = 0; skip_list = []; mail_queue = []
            default_pass = "OEMS@12345"

            for row in reader:
                row     = {k.strip().lower(): v.strip() for k, v in row.items()}
                s_name  = row.get("name","").strip()
                s_adm   = row.get("admission_no","").strip()
                s_email = row.get("email","").strip()
                s_prog  = row.get("program", program).strip() or program
                s_sem   = row.get("semester", semester).strip() or semester
                s_br    = row.get("branch",  branch).strip()  or branch

                if not s_name or not s_adm:
                    skipped += 1; skip_list.append(f"{s_adm or '?'} — missing name/admission_no"); continue

                cursor.execute("SELECT id FROM students WHERE admission_no=%s", (s_adm,))
                if cursor.fetchone():
                    skipped += 1; skip_list.append(f"{s_adm} — already exists"); continue

                hashed = generate_password_hash(default_pass)
                cursor.execute("INSERT INTO students (name, admission_no, program, branch, semester, email, password) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (s_name, s_adm, s_prog, s_br, s_sem, s_email or None, hashed))
                added += 1
                if send_mail and s_email and is_valid_email(s_email):
                    mail_queue.append((s_email, s_name, s_adm, default_pass, s_prog, s_br, s_sem))

            conn.commit(); cursor.close(); conn.close()

            if mail_queue:
                def _send_all(q):
                    for em, nm, adm, pw, prog, br, sem in q:
                        try: email_service.send_welcome_email(em, nm, adm, pw, prog, br, sem)
                        except Exception as ex: print(f"[BulkMail] {em}: {ex}")
                threading.Thread(target=_send_all, args=(mail_queue,), daemon=True).start()

            return render_template("add_student.html", admin_branch=admin_branch,
                bulk_result={"added": added, "skipped": skipped, "skip_list": skip_list, "mailed": len(mail_queue) if send_mail else 0})

        else:
            # ── SINGLE ADD ──
            name         = request.form["name"]
            admission_no = request.form["admission_no"]
            program      = request.form["program"]
            semester     = request.form["semester"]
            email        = request.form.get("email", "").strip()
            branch       = request.form.get("branch") if admin_branch == "ALL" else admin_branch
            default_pass = "OEMS@12345"
            hashed_password = generate_password_hash(default_pass)

            conn   = get_db_connection()
            cursor = conn.cursor()
            # Duplicate check
            cursor.execute("SELECT id FROM students WHERE admission_no=%s", (admission_no,))
            if cursor.fetchone():
                cursor.close(); conn.close()
                return render_template("add_student.html", admin_branch=admin_branch,
                    single_error=f"Admission number {admission_no} already exists.")

            cursor.execute("INSERT INTO students (name, admission_no, program, branch, semester, email, password) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (name, admission_no, program, branch, semester, email or None, hashed_password))
            conn.commit(); cursor.close(); conn.close()

            if email and is_valid_email(email):
                send_email_async(email_service.send_welcome_email, email, name, admission_no, default_pass, program, branch, semester)

            return redirect("/student_manager")

    return render_template("add_student.html", admin_branch=admin_branch)

# ── RESEND CREDENTIALS (Admin — existing student ko email bhejo)
@app.route("/resend_credentials/<int:student_id>", methods=["POST"])
@login_required("admin")
def resend_credentials(student_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students WHERE id=%s", (student_id,))
    student = cursor.fetchone()
    cursor.close(); conn.close()

    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404

    email = student.get("email")
    if not email or not is_valid_email(email):
        return jsonify({"success": False, "message": "Student has no valid email on record"}), 400

    send_email_async(
        email_service.send_welcome_email,
        email, student["name"], student["admission_no"],
        "OEMS@12345", student["program"], student["branch"], student["semester"]
    )
    return jsonify({"success": True, "message": f"Credentials resent to {email}"})

# ── EDIT PROFILE (Student)
@app.route("/edit_profile", methods=["GET", "POST"])
@login_required("student")
def edit_profile():
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    student_id = session["student_id"]
    if request.method == "POST":
        email    = request.form["email"]
        password = request.form["password"]
        if password:
            cursor.execute("UPDATE students SET email=%s, password=%s WHERE id=%s", (email, generate_password_hash(password), student_id))
        else:
            cursor.execute("UPDATE students SET email=%s WHERE id=%s", (email, student_id))
        conn.commit()
        cursor.execute("SELECT email FROM students WHERE id=%s", (student_id,))
        session["email"] = cursor.fetchone()["email"]
        cursor.close(); conn.close()
        return redirect("/student")
    cursor.execute("SELECT * FROM students WHERE id=%s", (student_id,))
    user = cursor.fetchone()
    session["program"]  = user["program"]
    session["semester"] = user["semester"]
    cursor.close(); conn.close()
    return render_template("edit_profile.html", user=user)

# ── CREATE EXAM
@app.route("/create_exam", methods=["GET", "POST"])
@login_required("admin")
def create_exam():
    admin_branch = session.get("admin_branch")
    if request.method == "POST":
        title         = request.form["title"]
        exam_type     = request.form["exam_type"]
        total_marks   = request.form["total_marks"]
        program       = request.form["program"]
        semester      = request.form["semester"]
        start_time    = request.form["start_time"]
        duration      = request.form["duration"]
        browser_mode  = request.form.get("browser_mode", "any")
        ai_proctoring = 1 if request.form.get("ai_proctoring") == "1" else 0
        branch        = request.form.get("branch") if admin_branch == "ALL" else admin_branch
        conn   = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO exams (title, exam_type, total_marks, program, branch, semester, start_time, duration, status, browser_mode, ai_proctoring) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s)",
            (title, exam_type, total_marks, program, branch, semester, start_time, duration, browser_mode, ai_proctoring))
        conn.commit(); cursor.close(); conn.close()
        return redirect("/admin")
    return render_template("create_exam.html", admin_branch=admin_branch)


# ── ADD QUESTION (FIXED — MCQ/MSQ bug resolved)
@app.route("/add_question/<int:exam_id>", methods=["GET", "POST"])
@login_required("admin")
def add_question(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
    exam = cursor.fetchone()
    cursor.execute("SELECT COUNT(*) as total FROM questions WHERE exam_id=%s", (exam_id,))
    question_count = cursor.fetchone()["total"]
    max_limit = 20 if exam["exam_type"] == "theory" else 50

    if request.method == "POST":
        if question_count >= max_limit:
            cursor.close(); conn.close()
            return "Maximum question limit reached ❌"

        question_text = request.form.get("question_text", "").strip()
        try:    marks = int(request.form.get("marks", 0))
        except: marks = 0
        question_type = request.form.get("question_type", "theory").lower()

        optionA = request.form.get("optionA", "").strip() or None
        optionB = request.form.get("optionB", "").strip() or None
        optionC = request.form.get("optionC", "").strip() or None
        optionD = request.form.get("optionD", "").strip() or None

        if question_type == "theory":
            correct_answer = request.form.get("correct_answer", "").strip()
            if not correct_answer:
                cursor.close(); conn.close()
                return "Provide answer outline for theory ❌"

        elif question_type == "mcq":
            # MCQ: radio button — single value, name="correct_answer" value="optionA/B/C/D"
            selected = request.form.get("correct_answer", "").strip()
            if not selected:
                cursor.close(); conn.close()
                return "Select the correct answer for MCQ ❌"
            correct_answer = selected  # e.g. "optionA"

        else:
            # MSQ: checkboxes — multiple values
            selected_list = request.form.getlist("correct_answer")
            if not selected_list:
                cursor.close(); conn.close()
                return "Select at least one correct answer for MSQ ❌"
            correct_answer = ",".join(sorted(selected_list))  # e.g. "optionA,optionC"

        cursor.execute(
            "INSERT INTO questions (exam_id, question_text, question_type, optionA, optionB, optionC, optionD, correct_answer, marks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (exam_id, question_text, question_type, optionA, optionB, optionC, optionD, correct_answer, marks)
        )
        conn.commit(); cursor.close(); conn.close()
        return redirect(f"/add_question/{exam_id}")

    cursor.close(); conn.close()
    return render_template("add_question.html", exam=exam, question_count=question_count, max_limit=max_limit)

# ── EDIT QUESTION (FIXED)
@app.route("/edit_question/<int:question_id>", methods=["GET", "POST"])
@login_required("admin")
def edit_question(question_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM questions WHERE id=%s", (question_id,))
    question = cursor.fetchone()
    if not question:
        cursor.close(); conn.close()
        return "Question not found", 404

    if request.method == "POST":
        question_text = request.form.get("question_text","").strip()
        optionA = request.form.get("optionA","").strip() or None
        optionB = request.form.get("optionB","").strip() or None
        optionC = request.form.get("optionC","").strip() or None
        optionD = request.form.get("optionD","").strip() or None
        try:    marks = int(request.form.get("marks",0))
        except: marks = 0

        q_type = question.get("question_type","theory").lower()

        if q_type == "theory":
            correct_answer = request.form.get("correct_answer","").strip()
        elif q_type == "mcq":
            # Radio button: get single value
            correct_answer = request.form.get("correct_answer","").strip()
        else:
            # MSQ: checkboxes
            selected_list  = request.form.getlist("correct_answer")
            correct_answer = ",".join(sorted(selected_list)) if selected_list else ""

        cursor.execute(
            "UPDATE questions SET question_text=%s, optionA=%s, optionB=%s, optionC=%s, optionD=%s, correct_answer=%s, marks=%s WHERE id=%s",
            (question_text, optionA, optionB, optionC, optionD, correct_answer, marks, question_id)
        )
        conn.commit()
        exam_id = question["exam_id"]
        cursor.close(); conn.close()
        return redirect(f"/questions/{exam_id}")

    cursor.close(); conn.close()
    return render_template("edit_question.html", question=question)

# ── DELETE QUESTION
@app.route("/delete_question/<int:question_id>", methods=["POST"])
@login_required("admin")
def delete_question(question_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT exam_id FROM questions WHERE id=%s", (question_id,))
    question = cursor.fetchone()
    exam_id  = question["exam_id"] if question else None
    cursor.execute("DELETE FROM questions WHERE id=%s", (question_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect(f"/questions/{exam_id}") if exam_id else redirect("/admin")

# ── DELETE EXAM
@app.route("/delete_exam/<int:exam_id>", methods=["POST"])
@login_required("admin")
def delete_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE answers FROM answers JOIN questions ON answers.question_id=questions.id WHERE questions.exam_id=%s", (exam_id,))
    cursor.execute("DELETE FROM results WHERE exam_id=%s", (exam_id,))
    cursor.execute("DELETE FROM questions WHERE exam_id=%s", (exam_id,))
    cursor.execute("DELETE FROM exams WHERE id=%s", (exam_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect("/admin")

# ── PUBLISH EXAM
@app.route("/publish_exam/<int:exam_id>")
@login_required("admin")
def publish_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE exams SET status='publish' WHERE id=%s", (exam_id,))
        cursor.execute("SELECT title, start_time, duration, program, branch, semester FROM exams WHERE id=%s", (exam_id,))
        exam_data = cursor.fetchone()
        if exam_data:
            exam_name, exam_date, duration, target_program, target_branch, target_semester = [str(x) for x in exam_data]
            cursor.execute("SELECT name, email FROM students WHERE program=%s AND branch=%s AND semester=%s", (target_program, target_branch, target_semester))
            student_list = [{"name": row[0], "email": row[1]} for row in cursor.fetchall()]
            if student_list:
                def _send():
                    try: email_service.send_bulk_exam_alerts(student_list, exam_name, exam_date, duration)
                    except Exception as e: print(f"[BulkEmail] {e}")
                threading.Thread(target=_send).start()
        conn.commit()
    except Exception as e:
        print(f"[Publish] Error: {e}"); conn.rollback()
    finally:
        cursor.close(); conn.close()
    return redirect("/admin")

# ── UNPUBLISH EXAM
@app.route("/unpublish_exam/<int:exam_id>")
@login_required("admin")
def unpublish_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE exams SET status='draft' WHERE id=%s", (exam_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect("/admin")

# ── RESULTS SUMMARY (Admin)
@app.route("/results")
@login_required("admin")
def results_summary():
    admin_branch = session.get("admin_branch")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if admin_branch == "ALL":
        cursor.execute("SELECT students.id AS student_id, students.name AS student_name, students.admission_no, students.program, students.branch, students.semester, exams.id AS exam_id, exams.title AS exam_title, results.total_score, results.submission_status FROM results JOIN students ON results.student_id=students.id JOIN exams ON results.exam_id=exams.id ORDER BY results.id DESC")
    else:
        cursor.execute("SELECT students.id AS student_id, students.name AS student_name, students.admission_no, students.program, students.branch, students.semester, exams.id AS exam_id, exams.title AS exam_title, results.total_score, results.submission_status FROM results JOIN students ON results.student_id=students.id JOIN exams ON results.exam_id=exams.id WHERE students.branch=%s ORDER BY results.id DESC", (admin_branch,))
    results_data = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template("results_summary.html", results=results_data, admin_branch=admin_branch)

# ── RESULT DETAILS (Admin)
@app.route("/result_details/<int:student_id>/<int:exam_id>")
@login_required("admin")
def result_details(student_id, exam_id):
    admin_branch = session.get("admin_branch")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT branch FROM students WHERE id=%s", (student_id,))
    student = cursor.fetchone()
    if not student:
        cursor.close(); conn.close()
        return "Student not found ❌", 404
    if admin_branch != "ALL" and student["branch"] != admin_branch:
        cursor.close(); conn.close()
        return "Access Denied 🚫", 403
    cursor.execute("SELECT answers.id, questions.question_text, questions.marks, answers.answer AS student_answer, answers.score, answers.feedback FROM answers JOIN questions ON answers.question_id=questions.id WHERE answers.student_id=%s AND answers.exam_id=%s", (student_id, exam_id))
    answers = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template("result_details.html", answers=answers)

# ── VIEW QUESTIONS
@app.route("/questions/<int:exam_id>")
@login_required("admin")
def view_questions(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM questions WHERE exam_id=%s", (exam_id,))
    questions = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template("questions.html", questions=questions, exam_id=exam_id)

# ── STUDENT DASHBOARD
@app.route("/student")
@login_required("student")
def student_dashboard():
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students WHERE id=%s", (session.get("student_id"),))
    student_info = cursor.fetchone()
    if not student_info:
        cursor.close(); conn.close()
        return "Student data not found. Please login again.", 404
    cursor.execute("SELECT * FROM exams WHERE program=%s AND branch=%s AND semester=%s AND status='publish'", (student_info["program"], student_info["branch"], student_info["semester"]))
    exams = cursor.fetchall()
    cursor.execute("SELECT exam_id FROM results WHERE student_id=%s", (session.get("student_id"),))
    attempted_exam_ids = {row["exam_id"] for row in cursor.fetchall()}
    cursor.close(); conn.close()
    return render_template("student_dashboard.html", student=student_info, exams=exams, attempted_exam_ids=attempted_exam_ids, now_time=datetime.now(), timedelta=timedelta)

# ── START EXAM
@app.route("/start_exam/<int:exam_id>")
@login_required("student")
def start_exam(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
    exam = cursor.fetchone()
    if not exam:
        cursor.close(); conn.close()
        return "<script>alert('Exam not found!'); window.location.href='/student';</script>"
    exam_start = exam["start_time"]
    exam_end   = exam_start + timedelta(minutes=exam["duration"])
    now        = datetime.now()
    if now < exam_start:
        cursor.close(); conn.close()
        start_str = exam_start.strftime("%I:%M %p")
        return f"<script>alert('Exam has not started yet. Starts at {start_str}'); window.location.href='/student';</script>"
    if now > exam_end:
        cursor.close(); conn.close()
        return "<script>alert('Exam time is over.'); window.location.href='/student';</script>"
    cursor.execute("SELECT id FROM results WHERE student_id=%s AND exam_id=%s", (session["student_id"], exam_id))
    if cursor.fetchone():
        cursor.close(); conn.close()
        return "<script>alert('Security Alert: You have already attempted this exam.'); window.location.href='/student';</script>"
    browser_mode  = exam.get("browser_mode", "any")
    ai_proctoring = bool(exam.get("ai_proctoring", 0))
    if browser_mode in ("secure_any", "secure_campus"):
        if not is_secure_browser():
            cursor.close(); conn.close()
            return render_template("secure_browser_required.html", exam=exam, mode=browser_mode)
    if browser_mode == "secure_campus":
        if not is_campus_ip():
            cursor.close(); conn.close()
            return render_template("campus_only.html", exam=exam, client_ip=request.remote_addr)
    cursor.execute("SELECT * FROM questions WHERE exam_id=%s", (exam_id,))
    questions = cursor.fetchall()
    cursor.close(); conn.close()
    random.shuffle(questions)

    # Schedule exam end processor (idempotent — safe to call multiple times)
    schedule_exam_end_processor(exam_id, exam_end)

    return render_template("start_exam.html", questions=questions, exam_id=exam_id, exam=exam,
        exam_start=exam_start.strftime("%Y-%m-%dT%H:%M:%S"), exam_end=exam_end.strftime("%Y-%m-%dT%H:%M:%S"),
        duration=exam["duration"], ai_proctoring=ai_proctoring)

# ── SUBMIT EXAM (FIXED — MSQ match logic)
@app.route("/submit_exam/<int:exam_id>", methods=["POST"])
@login_required("student")
def submit_exam(exam_id):
    answers_data = request.form
    student_id   = session.get("student_id")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM results WHERE student_id=%s AND exam_id=%s", (student_id, exam_id))
    if cursor.fetchone():
        cursor.close(); conn.close()
        return render_template('already_submitted.html'), 400
    cursor.execute("SELECT * FROM questions WHERE exam_id=%s", (exam_id,))
    questions   = cursor.fetchall()
    total_score = 0
    has_theory  = False

    for q in questions:
        q_type = q["question_type"].lower()

        if q_type == "mcq":
            # Single choice — radio button value
            student_answer = answers_data.get(f"q{q['id']}", "").strip()
            score    = q["marks"] if student_answer == q["correct_answer"] else 0
            feedback = "Correct answer" if score > 0 else "Incorrect or no answer"
            total_score += score
            cursor.execute("INSERT INTO answers (student_id,exam_id,question_id,answer,score,feedback) VALUES (%s,%s,%s,%s,%s,%s)",
                (student_id, exam_id, q["id"], student_answer, score, feedback))

        elif q_type == "msq":
            # Multiple choice — checkbox values, sorted and joined
            selected = sorted(answers_data.getlist(f"q{q['id']}"))
            student_answer = ",".join(selected)
            # DB stores sorted: "optionA,optionC" — match exactly
            db_correct = ",".join(sorted([a.strip() for a in (q["correct_answer"] or "").split(",") if a.strip()]))
            score    = q["marks"] if (student_answer == db_correct and student_answer) else 0
            feedback = "Correct answers" if score > 0 else "Incorrect or partially correct"
            total_score += score
            cursor.execute("INSERT INTO answers (student_id,exam_id,question_id,answer,score,feedback) VALUES (%s,%s,%s,%s,%s,%s)",
                (student_id, exam_id, q["id"], student_answer, score, feedback))

        else:
            # Theory — pending evaluation
            has_theory     = True
            student_answer = answers_data.get(f"q{q['id']}", "").strip()
            cursor.execute("INSERT INTO answers (student_id,exam_id,question_id,answer,score,feedback) VALUES (%s,%s,%s,%s,%s,%s)",
                (student_id, exam_id, q["id"], student_answer, None, "Pending AI evaluation"))

    # ── STRICT RULE: Evaluation ONLY after exam end + plagiarism check ──
    # No evaluation on immediate submit — exam_end_processor handles it.
    # MCQ/MSQ: score already calculated above (objective).
    # Theory: score = NULL until exam ends and plagiarism clears.
    if has_theory:
        initial_status = "AwaitingExamEnd"
    else:
        # MCQ-only exam: no plagiarism risk for objective, but still
        # wait for exam end so all students are checked together.
        initial_status = "AwaitingExamEnd"

    cursor.execute("INSERT INTO results (student_id,exam_id,total_score,submission_status) VALUES (%s,%s,%s,%s)",
        (student_id, exam_id, round(total_score, 2), initial_status))
    conn.commit(); cursor.close(); conn.close()

    # NO immediate evaluation thread — exam_end_processor will handle it
    return render_template('exam_submitted.html', has_theory=has_theory, objective_score=round(total_score, 2))

# ── DELETE STUDENT
@app.route("/delete_student/<int:id>", methods=["POST"])
@login_required("admin")
def delete_student(id):
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM answers WHERE student_id=%s", (id,))
    cursor.execute("DELETE FROM results WHERE student_id=%s", (id,))
    cursor.execute("DELETE FROM students WHERE id=%s", (id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect("/student_manager")

# ── RUN AI EVALUATION (Manual)
@app.route("/run_ai_check")
@login_required("admin")
def run_ai_check():
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # FIX: correct_answer (model answer) bhi select karo
    cursor.execute("""SELECT answers.id, answers.answer, answers.student_id, answers.exam_id,
                             questions.question_text, questions.marks, questions.correct_answer AS model_answer
                      FROM answers JOIN questions ON answers.question_id=questions.id
                      WHERE answers.score IS NULL AND questions.question_type='theory'""")
    records = cursor.fetchall()
    evaluated_answers = 0
    failed_results = set()
    for r in records:
        student_answer = (r["answer"] or "").strip()
        model_answer   = (r["model_answer"] or "").strip()
        if not student_answer or len(student_answer) < 10:
            cursor.execute("UPDATE answers SET score=%s, feedback=%s WHERE id=%s", (0, "No answer submitted.", r["id"]))
            evaluated_answers += 1
            continue
        try:
            score, feedback = evaluate_answer(r["question_text"], student_answer, r["marks"], model_answer)
        except EvaluationUnavailable as e:
            failed_results.add((r["student_id"], r["exam_id"]))
            print(f"[AI Eval] Student {r['student_id']}, exam {r['exam_id']}: {e}")
            cursor.execute(
                "UPDATE answers SET feedback=%s WHERE id=%s AND score IS NULL",
                ("Evaluation pending. Please retry after evaluator is available.", r["id"])
            )
            continue
        cursor.execute("UPDATE answers SET score=%s, feedback=%s WHERE id=%s", (round(score, 1), feedback, r["id"]))
        evaluated_answers += 1
    conn.commit()
    cursor.execute("SELECT DISTINCT answers.student_id, answers.exam_id FROM answers JOIN questions ON answers.question_id=questions.id WHERE questions.question_type='theory'")
    for se in cursor.fetchall():
        sid, eid = se["student_id"], se["exam_id"]
        cursor.execute("SELECT COALESCE(SUM(score),0) AS total FROM answers WHERE student_id=%s AND exam_id=%s", (sid,eid))
        total = float(cursor.fetchone()["total"] or 0)
        cursor.execute("SELECT COUNT(*) AS pending FROM answers WHERE student_id=%s AND exam_id=%s AND score IS NULL", (sid,eid))
        new_status = "Pending" if cursor.fetchone()["pending"] > 0 else "Evaluated"
        # Don't touch Hold or Disqualified students
        cursor.execute("""
            UPDATE results SET total_score=%s, submission_status=%s
            WHERE student_id=%s AND exam_id=%s
              AND submission_status NOT IN ('Hold','Disqualified')
        """, (round(total,2), new_status, sid, eid))
    conn.commit(); cursor.close(); conn.close()
    return f"AI Evaluation Complete — {evaluated_answers} answers evaluated. {len(failed_results)} result(s) left pending."

# ── RESET AI EVALUATION
@app.route("/reset_ai_evaluation")
@login_required("admin")
def reset_ai_evaluation():
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE answers JOIN questions ON answers.question_id=questions.id SET answers.score=NULL, answers.feedback=NULL WHERE questions.question_type='theory'")
    conn.commit(); cursor.close(); conn.close()
    return "AI Evaluation Reset Successfully"

# ── PLAGIARISM CHECK
from sklearn.feature_extraction.text import TfidfVectorizer as _TfidfVec
from sklearn.metrics.pairwise import cosine_similarity as _cosine_sim
@app.route("/plagiarism/<int:exam_id>")
@login_required("admin")
def plagiarism_check(exam_id):
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT students.name, answers.answer FROM answers JOIN students ON answers.student_id=students.id JOIN questions ON answers.question_id=questions.id WHERE questions.exam_id=%s AND questions.question_type='theory'", (exam_id,))
    data = cursor.fetchall(); cursor.close(); conn.close()
    student_answers = {}
    for row in data:
        if not row["answer"] or not row["answer"].strip(): continue
        student_answers[row["name"]] = student_answers.get(row["name"], "") + " " + row["answer"]
    names = list(student_answers.keys()); texts = list(student_answers.values())
    similarity_results = []; cheaters = set()
    if len(texts) > 1:
        try:
            vectors    = _TfidfVec().fit_transform(texts)
            sim_matrix = _cosine_sim(vectors)
            for i in range(len(names)):
                for j in range(i+1, len(names)):
                    sim = round(sim_matrix[i][j]*100, 2)
                    similarity_results.append({"student1": names[i], "student2": names[j], "similarity": sim})
                    if sim > 80: cheaters.add(names[i]); cheaters.add(names[j])
        except ValueError: pass
    return render_template("plagiarism.html", similarity_results=similarity_results, cheaters=cheaters)


# ================================================================
# EXAM END AUTO-PROCESSOR
# ================================================================
# Flow (triggered when exam end time passes):
# 1. Force-submit answers of students who didn't submit
# 2. Run plagiarism check on all theory answers
# 3. Similarity >= PLAGIARISM_THRESHOLD → status = "Hold"
# 4. Normal students → AI evaluate → email PDF
# ================================================================

PLAGIARISM_THRESHOLD = 70  # % similarity → result hold

_exam_end_scheduled = set()  # track which exam_ids already scheduled


def _force_submit_missing_students(exam_id, exam, app_ctx):
    """Students jo exam dete rahe but submit nahi kiya — unka force submit"""
    with app_ctx:
        try:
            conn   = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # Students who were eligible for this exam
            cursor.execute("""
                SELECT s.id AS student_id FROM students s
                WHERE s.program=%s AND s.branch=%s AND s.semester=%s
            """, (exam['program'], exam['branch'], exam['semester']))
            all_students = cursor.fetchall()

            # Students who already submitted
            cursor.execute("SELECT student_id FROM results WHERE exam_id=%s", (exam_id,))
            submitted_ids = {r['student_id'] for r in cursor.fetchall()}

            # Questions for this exam
            cursor.execute("SELECT * FROM questions WHERE exam_id=%s", (exam_id,))
            questions = cursor.fetchall()

            force_submitted = 0
            for s in all_students:
                sid = s['student_id']
                if sid in submitted_ids:
                    continue  # already submitted

                # Check if student started the exam (has any answers)
                cursor.execute(
                    "SELECT COUNT(*) AS cnt FROM answers WHERE student_id=%s AND exam_id=%s",
                    (sid, exam_id)
                )
                ans_count = cursor.fetchone()['cnt']

                # ── RULE: Jo student ne exam attempt hi nahi kiya → IGNORE ──
                # Unhe force-submit nahi karna — no answers = absent student
                if ans_count == 0:
                    continue

                # Student ne exam shuru kiya tha but submit nahi kiya
                # (answers hain, result row nahi) → force-submit their existing answers
                has_theory = any(q['question_type'].lower() == 'theory' for q in questions)
                cursor.execute(
                    "INSERT INTO results (student_id,exam_id,total_score,submission_status) VALUES (%s,%s,%s,%s)",
                    (sid, exam_id, 0, "AwaitingExamEnd")
                )
                force_submitted += 1

            conn.commit()
            cursor.close(); conn.close()
            print(f"[ExamEnd] Exam {exam_id}: {force_submitted} students force-submitted")
        except Exception as e:
            print(f"[ExamEnd] force_submit error: {e}")


def _run_plagiarism_and_evaluate(exam_id, app_ctx):
    """
    Post-exam processor — STRICT flow:

    1. Collect all students queued for evaluation
    2. Theory exam → TF-IDF plagiarism check
       - similarity >= PLAGIARISM_THRESHOLD → status='Hold' (NO eval, hold email)
       - similarity <  PLAGIARISM_THRESHOLD → evaluate + email PDF
    3. MCQ-only students → directly evaluate + email (no plagiarism check needed)

    THIS is the ONLY place evaluation happens.
    submit_exam just saves answers and sets AwaitingExamEnd.
    """
    with app_ctx:
        try:
            conn   = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            print(f"[ExamEnd] ══ Post-exam processor started for exam {exam_id} ══")

            # ── Check if exam has theory questions ──
            cursor.execute("""
                SELECT COUNT(*) AS theory_count
                FROM questions
                WHERE exam_id=%s AND question_type='theory'
            """, (exam_id,))
            has_theory_questions = cursor.fetchone()['theory_count'] > 0

            # ── Step 1: All students queued for processing ──
            cursor.execute("""
                SELECT r.student_id, r.submission_status
                FROM results r
                WHERE r.exam_id=%s AND r.submission_status IN ('AwaitingExamEnd','Pending')
            """, (exam_id,))
            pending_students = cursor.fetchall()
            print(f"[ExamEnd] {len(pending_students)} students to process")

            if not pending_students:
                cursor.close(); conn.close()
                print(f"[ExamEnd] No queued students — skipping")
                return

            cheater_ids = set()
            hold_reasons = {}

            # ── Step 2: Plagiarism check (only if theory questions exist) ──
            if has_theory_questions:
                print(f"[ExamEnd] Running plagiarism check...")

                cursor.execute("""
                    SELECT s.id AS student_id, s.name, s.email,
                           a.answer
                    FROM answers a
                    JOIN students  s ON a.student_id  = s.id
                    JOIN questions q ON a.question_id = q.id
                    WHERE q.exam_id=%s
                      AND q.question_type='theory'
                      AND a.answer IS NOT NULL
                      AND a.answer != ''
                      AND s.id IN (
                          SELECT student_id FROM results
                          WHERE exam_id=%s AND submission_status IN ('AwaitingExamEnd','Pending')
                      )
                """, (exam_id, exam_id))
                rows = cursor.fetchall()

                # Concatenate all answers per student
                student_texts = {}
                student_info  = {}
                for row in rows:
                    sid = row['student_id']
                    student_texts[sid] = student_texts.get(sid, "") + " " + (row['answer'] or "")
                    student_info[sid]  = {'name': row['name'], 'email': row['email']}

                # Run TF-IDF similarity
                if len(student_texts) >= 2:
                    sids  = list(student_texts.keys())
                    texts = [student_texts[s] for s in sids]
                    try:
                        vectors    = _TfidfVec().fit_transform(texts)
                        sim_matrix = _cosine_sim(vectors)
                        for i in range(len(sids)):
                            for j in range(i + 1, len(sids)):
                                sim = round(sim_matrix[i][j] * 100, 2)
                                print(f"[Plagiarism] {student_info.get(sids[i],{}).get('name','?')} "
                                      f"↔ {student_info.get(sids[j],{}).get('name','?')}: {sim}%")
                                if sim >= PLAGIARISM_THRESHOLD:
                                    sid_a, sid_b = sids[i], sids[j]
                                    name_a = student_info.get(sid_a, {}).get('name', f"Student {sid_a}")
                                    name_b = student_info.get(sid_b, {}).get('name', f"Student {sid_b}")
                                    cheater_ids.add(sid_a)
                                    cheater_ids.add(sid_b)
                                    hold_reasons.setdefault(sid_a, []).append(
                                        f"Similarity {sim}% with {name_b}; threshold is {PLAGIARISM_THRESHOLD}%."
                                    )
                                    hold_reasons.setdefault(sid_b, []).append(
                                        f"Similarity {sim}% with {name_a}; threshold is {PLAGIARISM_THRESHOLD}%."
                                    )
                    except Exception as pe:
                        print(f"[Plagiarism] TF-IDF error: {pe}")

                cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
                hold_exam = cursor.fetchone() or {'title': f'Exam #{exam_id}'}
                held_ids = []

                # Mark plagiarists as Hold — NO evaluation, but notify by email
                for sid in cheater_ids:
                    cursor.execute(
                        "UPDATE results SET submission_status='Hold' WHERE student_id=%s AND exam_id=%s",
                        (sid, exam_id)
                    )
                    if cursor.rowcount > 0:
                        held_ids.append(sid)
                    name = student_info.get(sid, {}).get('name', str(sid))
                    print(f"[ExamEnd] {name} → Hold (similarity >= {PLAGIARISM_THRESHOLD}%)")

                conn.commit()
                for sid in held_ids:
                    send_hold_email(
                        {
                            'id': sid,
                            'name': student_info.get(sid, {}).get('name', f"Student {sid}"),
                            'email': student_info.get(sid, {}).get('email')
                        },
                        hold_exam,
                        hold_reasons.get(sid, [f"Similarity >= {PLAGIARISM_THRESHOLD}%"])
                    )
                print(f"[ExamEnd] {len(cheater_ids)} student(s) flagged as Hold")
            else:
                print(f"[ExamEnd] MCQ-only exam — skipping plagiarism check")

            # ── Step 3: Evaluate clean students ──
            # Only students still queued (not Hold) get evaluated
            cursor.execute("""
                SELECT r.student_id
                FROM results r
                WHERE r.exam_id=%s AND r.submission_status IN ('AwaitingExamEnd','Pending')
            """, (exam_id,))
            clean_students = [r['student_id'] for r in cursor.fetchall()]
            cursor.close(); conn.close()

            print(f"[ExamEnd] {len(clean_students)} student(s) cleared for evaluation")

            for sid in clean_students:
                print(f"[ExamEnd] Starting evaluation for student {sid}...")
                _ctx = app.app_context()
                threading.Thread(
                    target=run_background_evaluation,
                    args=(sid, exam_id, _ctx),
                    daemon=True
                ).start()
                time.sleep(0.5)  # Small stagger — avoid DB connection burst

        except Exception as e:
            print(f"[ExamEnd] _run_plagiarism_and_evaluate error: {e}")
            import traceback; traceback.print_exc()


def schedule_exam_end_processor(exam_id, exam_end_dt, force=False):
    """
    Schedule the exam end processor to run when exam ends.
    Uses a daemon thread with sleep — no external scheduler needed.
    force=True: bypass idempotent check (for re-trigger after DB time change).
    """
    if not force and exam_id in _exam_end_scheduled:
        return
    _exam_end_scheduled.add(exam_id)

    def _runner():
        now   = datetime.now()
        delay = (exam_end_dt - now).total_seconds()
        # If exam already ended → run immediately (0s delay)
        # If still running → wait until end + 30s grace
        delay = max(0, delay)
        if delay > 0:
            delay += 30  # 30s grace for last-minute submissions
        print(f"[ExamEnd] Exam {exam_id} processor starts in {delay:.0f}s")
        time.sleep(delay)

        print(f"[ExamEnd] Exam {exam_id}: starting post-exam processing")
        ctx = app.app_context()

        # Step A: Force-submit students who never submitted
        with ctx:
            conn   = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
            exam   = cursor.fetchone()
            cursor.close(); conn.close()

        if exam:
            force_ctx = app.app_context()
            _force_submit_missing_students(exam_id, exam, force_ctx)

        time.sleep(2)

        # Step B: Plagiarism + evaluate + email
        eval_ctx = app.app_context()
        _run_plagiarism_and_evaluate(exam_id, eval_ctx)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()


# ── Admin: Manually trigger exam end processor (re-evaluate) ──
@app.route("/trigger_exam_evaluation/<int:exam_id>", methods=["POST"])
@login_required("admin")
def trigger_exam_evaluation(exam_id):
    """
    Admin manually triggers post-exam processing.
    Use when:
    - Exam time was changed in DB
    - Students are stuck on AwaitingExamEnd/Pending
    - Need to re-run plagiarism + evaluation
    Queues AwaitingExamEnd/Pending students (not Hold/Disqualified/Evaluated).
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
    exam = cursor.fetchone()
    if not exam:
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "Exam not found"}), 404

    # Only count queued students — don't touch Hold/Disqualified/Evaluated
    cursor.execute("""
        SELECT COUNT(*) AS cnt FROM results
        WHERE exam_id=%s AND submission_status IN ('AwaitingExamEnd','Pending')
    """, (exam_id,))
    awaiting_count = cursor.fetchone()['cnt']
    cursor.close(); conn.close()

    # Force-trigger processor immediately (delay=0)
    exam_end_dt = datetime.now() - timedelta(seconds=1)  # already ended
    _exam_end_scheduled.discard(exam_id)  # clear so force works
    schedule_exam_end_processor(exam_id, exam_end_dt, force=True)

    return jsonify({
        "success": True,
        "message": f"Evaluation triggered for exam '{exam['title']}'. {awaiting_count} student(s) in queue."
    })


# ── Admin: Release hold result manually ──
@app.route("/release_result/<int:student_id>/<int:exam_id>", methods=["POST"])
@login_required("admin")
def release_result(student_id, exam_id):
    """Admin manually releases a held result → resets scores, re-evaluates, then emails result"""
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT submission_status
        FROM results
        WHERE student_id=%s AND exam_id=%s
    """, (student_id, exam_id))
    result = cursor.fetchone()
    if not result:
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "Result not found"}), 404

    if result["submission_status"] != "Hold":
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "Only held results can be released"}), 400

    cursor.execute("""
        UPDATE answers a
        JOIN questions q ON a.question_id=q.id
        SET a.score=NULL, a.feedback='Pending AI evaluation'
        WHERE a.student_id=%s AND a.exam_id=%s
          AND q.question_type='theory'
    """, (student_id, exam_id))
    reset_count = cursor.rowcount

    cursor.execute("""
        UPDATE results
        SET total_score=0, submission_status='Pending'
        WHERE student_id=%s AND exam_id=%s
          AND submission_status='Hold'
    """, (student_id, exam_id))
    conn.commit()
    cursor.close(); conn.close()

    # Trigger fresh evaluation + final result email in background
    ctx = app.app_context()
    threading.Thread(target=run_background_evaluation, args=(student_id, exam_id, ctx), daemon=True).start()
    return jsonify({"success": True, "message": f"Result released — re-evaluation started for {reset_count} theory answer(s)"})


# ── Admin: Re-evaluate a clean result manually ──
@app.route("/reevaluate_result/<int:student_id>/<int:exam_id>", methods=["POST"])
@login_required("admin")
def reevaluate_result(student_id, exam_id):
    """
    Reset one non-held student's theory scores and run SBERT evaluation again.
    Use this for old false-zero results created before the evaluator fix.
    """
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT submission_status
        FROM results
        WHERE student_id=%s AND exam_id=%s
    """, (student_id, exam_id))
    result = cursor.fetchone()
    if not result:
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "Result not found"}), 404

    if result["submission_status"] in ("Hold", "Disqualified"):
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "Held or disqualified results cannot be re-evaluated here"}), 400

    cursor.execute("""
        UPDATE answers a
        JOIN questions q ON a.question_id=q.id
        SET a.score=NULL, a.feedback='Pending AI evaluation'
        WHERE a.student_id=%s AND a.exam_id=%s
          AND q.question_type='theory'
    """, (student_id, exam_id))
    reset_count = cursor.rowcount
    if reset_count == 0:
        cursor.close(); conn.close()
        return jsonify({"success": False, "message": "No theory answers found to re-evaluate"}), 400

    cursor.execute("""
        UPDATE results
        SET total_score=0, submission_status='Pending'
        WHERE student_id=%s AND exam_id=%s
          AND submission_status NOT IN ('Hold','Disqualified')
    """, (student_id, exam_id))
    conn.commit()
    cursor.close(); conn.close()

    ctx = app.app_context()
    threading.Thread(target=run_background_evaluation, args=(student_id, exam_id, ctx), daemon=True).start()
    return jsonify({"success": True, "message": f"Re-evaluation started for {reset_count} theory answer(s)"})


# ── Admin: Disqualify held result ──
@app.route("/disqualify_result/<int:student_id>/<int:exam_id>", methods=["POST"])
@login_required("admin")
def disqualify_result(student_id, exam_id):
    """Admin disqualifies a plagiarism-held result"""
    conn   = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE results SET submission_status='Disqualified', total_score=0 WHERE student_id=%s AND exam_id=%s",
        (student_id, exam_id)
    )
    conn.commit(); cursor.close(); conn.close()
    return jsonify({"success": True, "message": "Student disqualified"})


cv2 = None
np = None
eye_cascade = None
_cv_initialized = False

def _ensure_cv_loaded():
    """Lazily import OpenCV/numpy and the one Haar cascade the *login* blink
    challenge still uses. Proctoring face/object detection lives in
    proctor_engine.py (InsightFace + YOLO) and does not depend on this."""
    global cv2, np, eye_cascade, _cv_initialized
    if _cv_initialized:
        return
    import cv2 as _cv2
    import numpy as _np
    cv2 = _cv2
    np = _np
    eye_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + 'haarcascade_eye.xml')
    _cv_initialized = True

# ── PROCTORING SCHEMA MIGRATION ──────────────────────────────────────────────
# Adds confidence/severity/source/evidence columns to exam_violations so the new
# engine can store rich, auditable records. Migration-safe: old DBs upgrade lazily.
_violation_schema_checked = False
_violation_schema_lock = threading.Lock()

# Where client-/server-captured evidence snapshots are written. Served back to
# admins on the violation log page.
EVIDENCE_DIR = os.environ.get(
    "OEMS_EVIDENCE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "evidence")
)


def ensure_violation_schema():
    global _violation_schema_checked
    if _violation_schema_checked:
        return
    with _violation_schema_lock:
        if _violation_schema_checked:
            return
        conn = cursor = None
        try:
            os.makedirs(EVIDENCE_DIR, exist_ok=True)
            conn = get_db_connection()
            cursor = conn.cursor()
            for col, ddl in [
                ("severity",    "ALTER TABLE exam_violations ADD COLUMN severity TINYINT NOT NULL DEFAULT 1"),
                ("confidence",  "ALTER TABLE exam_violations ADD COLUMN confidence FLOAT NULL"),
                ("source",      "ALTER TABLE exam_violations ADD COLUMN source VARCHAR(20) NOT NULL DEFAULT 'client'"),
                ("evidence",    "ALTER TABLE exam_violations ADD COLUMN evidence VARCHAR(255) NULL"),
            ]:
                cursor.execute(f"SHOW COLUMNS FROM exam_violations LIKE '{col}'")
                if not cursor.fetchone():
                    cursor.execute(ddl)
                    print(f"[Proctor] Added exam_violations.{col} column")
            # Index helps the dashboard's per-student / per-exam aggregation.
            cursor.execute("SHOW INDEX FROM exam_violations WHERE Key_name='idx_violation_lookup'")
            if not cursor.fetchone():
                cursor.execute("CREATE INDEX idx_violation_lookup ON exam_violations (exam_id, student_id, violation_type)")
            conn.commit()
            _violation_schema_checked = True
        except Exception as e:
            print(f"[Proctor] Violation schema check failed: {e}")
        finally:
            if cursor: cursor.close()
            if conn: conn.close()


def _save_evidence(image_data_url, student_id, exam_id, code):
    """Persist a JPEG snapshot of the offending frame; return the relative path
    (or None). Best-effort — a failed write must never block violation logging."""
    if not image_data_url or "," not in image_data_url:
        return None
    try:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        raw = base64.b64decode(image_data_url.split(",", 1)[1])
        if len(raw) > 600_000:  # guard against oversized payloads
            return None
        fname = f"v_{exam_id}_{student_id}_{int(time.time()*1000)}_{re.sub(r'[^a-z0-9]+','',str(code).lower())[:16]}.jpg"
        with open(os.path.join(EVIDENCE_DIR, fname), "wb") as fh:
            fh.write(raw)
        return f"evidence/{fname}"
    except Exception as e:
        print(f"[Proctor] Evidence save failed: {e}")
        return None


def _store_violation(student_id, exam_id, v_type, details, severity=1,
                     confidence=None, source="client", evidence=None):
    """Single insert path for every violation, AI or client-side."""
    ensure_violation_schema()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO exam_violations "
            "(student_id, exam_id, violation_type, details, severity, confidence, source, evidence) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (student_id, exam_id, str(v_type)[:100], str(details)[:500],
             int(severity), confidence, str(source)[:20], evidence)
        )
        conn.commit()
    finally:
        cursor.close(); conn.close()


# ── LOG VIOLATION (called from JS/Electron during exam)
@app.route("/log_violation", methods=["POST"])
@login_required("student")
def log_violation():
    """Store a client-detected violation (tab switch, devtools, print, focus
    loss, fullscreen exit). AI camera violations come through /detect_cheating."""
    try:
        data       = request.get_json() or {}
        student_id = session.get("student_id")
        exam_id    = data.get("exam_id")
        v_type     = str(data.get("type", "unknown"))[:100]
        details    = str(data.get("details", ""))[:500]
        severity   = int(data.get("severity", 1) or 1)
        if not student_id or not exam_id:
            return jsonify({"ok": False}), 400
        evidence = _save_evidence(data.get("evidence"), student_id, exam_id, v_type) \
            if data.get("evidence") else None
        _store_violation(student_id, exam_id, v_type, details,
                         severity=severity, source="client", evidence=evidence)
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[Violation] DB error: {e}")
        return jsonify({"ok": False}), 500


def _get_enrolled_embedding(student_id):
    """Fetch the student's enrolled ArcFace embedding for identity continuity.
    Cached per process to avoid a DB hit on every frame."""
    cached = _enrolled_emb_cache.get(student_id)
    if cached is not None:
        return cached if cached is not False else None
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT face_embedding_v2 FROM students WHERE id=%s", (student_id,))
        row = cursor.fetchone()
    except Exception:
        row = None
    finally:
        cursor.close(); conn.close()
    emb = face_engine.deserialize(row.get("face_embedding_v2")) if row else None
    _enrolled_emb_cache[student_id] = emb if emb is not None else False
    return emb


_enrolled_emb_cache = {}


# ── AI PROCTORING — DETECT CHEATING (engine-backed, temporal, confidence-scored)
@app.route("/detect_cheating", methods=["POST"])
@login_required("student")
def detect_cheating():
    """Analyse one proctoring frame. The heavy lifting (InsightFace detection,
    real head-pose, identity, anti-spoof, YOLO objects) and the temporal
    confidence accumulation live in proctor_engine; this route owns auth, frame
    decode, persistence of escalated violations, and the JSON contract with the
    client. Any escalated violation is stored server-side here so the count can
    never be tampered with from the browser."""
    _ensure_cv_loaded()
    student_id = session.get("student_id")
    if not student_id:
        return jsonify({"ok": False, "status": "secure"}), 401
    try:
        data = request.get_json(silent=True) or {}
        exam_id = data.get("exam_id")
        image = data.get("image")
        if not image:
            return jsonify({"ok": False, "status": "secure"})

        frame, decode_err = _decode_base64_frame(image)
        if decode_err or frame is None:
            return jsonify({"ok": True, "status": "unclear",
                            "message": "Camera frame unreadable."})

        session_key = f"{student_id}:{exam_id}"
        enrolled = _get_enrolled_embedding(student_id) if exam_id else None
        verdict = proctor_engine.process_frame(session_key, frame, enrolled_embedding=enrolled)

        # Persist any escalated violation server-side (authoritative count).
        if verdict["violations"] and exam_id:
            evidence = _save_evidence(image, student_id, exam_id,
                                      verdict["violations"][0]["code"])
            for v in verdict["violations"]:
                try:
                    _store_violation(
                        student_id, exam_id, v["code"], v["label"],
                        severity=v["severity"], confidence=v["confidence"],
                        source="ai", evidence=evidence,
                    )
                except Exception as se:
                    print(f"[Proctor] violation store failed: {se}")

        return jsonify({"ok": True, **verdict})
    except Exception as e:
        print("[Proctor] detect_cheating error:", e)
        # Fail open: a server hiccup must never falsely terminate an exam.
        return jsonify({"ok": False, "status": "secure"})


# ── PROCTORING HEALTH (gatekeeper readiness check)
@app.route("/proctor_health")
def proctor_health():
    try:
        return jsonify({"ok": True, **proctor_engine.engine_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ================================================================
# EMAIL SERVICE
# ================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EMAIL_CONFIG = {
    'SENDER_EMAIL':       os.environ.get('OEMS_EMAIL'),
    'APP_PASSWORD':       os.environ.get('OEMS_EMAIL_PASSWORD'),
    'SMTP_SERVER':        'smtp.gmail.com',
    'SMTP_PORT':          587,
    'OTP_EXPIRY_MINUTES': 10,
    'MAX_OTP_ATTEMPTS':   3,
    'RATE_LIMIT_MINUTES': 5
}
if not EMAIL_CONFIG['SENDER_EMAIL'] or not EMAIL_CONFIG['APP_PASSWORD']:
    raise RuntimeError("OEMS_EMAIL aur OEMS_EMAIL_PASSWORD .env mein set nahi hain!")

def is_valid_email(email):
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', str(email)))

def sanitize_input(data):
    return data.strip() if isinstance(data, str) else data

def rate_limit(max_attempts=3, window_minutes=5):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            key  = f"rate_limit_{request.remote_addr}_{f.__name__}"
            now  = datetime.now()
            if key not in session:
                session[key] = {'count': 0, 'first_attempt': now.isoformat()}
            ad = session[key]
            fa = datetime.fromisoformat(ad['first_attempt'])
            if now - fa > timedelta(minutes=window_minutes):
                session[key] = {'count': 0, 'first_attempt': now.isoformat()}; ad = session[key]
            if ad['count'] >= max_attempts:
                raise TooManyRequests(f"Too many attempts. Try again in {window_minutes - (now-fa).seconds//60} minutes.")
            ad['count'] += 1; session[key] = ad
            return f(*args, **kwargs)
        return decorated_function
    return decorator

class OTPManager:
    @staticmethod
    def generate_otp():
        return ''.join([str(secrets.randbelow(10)) for _ in range(6)])
    @staticmethod
    def store_otp(email, otp, mode='email', user_name=None):
        session['otp_data'] = {'otp': otp, 'email': email, 'mode': mode, 'user_name': user_name,
            'expires_at': (datetime.now()+timedelta(minutes=EMAIL_CONFIG['OTP_EXPIRY_MINUTES'])).isoformat(),
            'attempts': 0, 'created_at': datetime.now().isoformat()}
    @staticmethod
    def verify_otp(user_otp):
        if 'otp_data' not in session: return False, "No OTP found."
        od = session['otp_data']
        if datetime.now() > datetime.fromisoformat(od['expires_at']):
            session.pop('otp_data', None); return False, "OTP expired."
        if od['attempts'] >= EMAIL_CONFIG['MAX_OTP_ATTEMPTS']:
            session.pop('otp_data', None); return False, "Too many attempts."
        od['attempts'] += 1; session['otp_data'] = od
        if str(user_otp) != od['otp']:
            return False, f"Invalid OTP. {EMAIL_CONFIG['MAX_OTP_ATTEMPTS']-od['attempts']} attempts remaining."
        session.pop('otp_data', None); return True, od


class EmailService:
    def __init__(self):
        self.sender_email = EMAIL_CONFIG['SENDER_EMAIL']
        self.app_password = EMAIL_CONFIG['APP_PASSWORD']

    def _create_connection(self):
        server = smtplib.SMTP(EMAIL_CONFIG['SMTP_SERVER'], EMAIL_CONFIG['SMTP_PORT'])
        server.starttls()
        server.login(self.sender_email, self.app_password)
        return server

    def send_welcome_email(self, receiver_email, student_name, admission_no, password, program="", branch="", semester=""):
        """Welcome email — new/existing student credentials"""
        try:
            first_name = get_first_name(student_name)
            ref_id     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = MIMEMultipart('alternative')
            msg['From']    = formataddr(('OEMS Support Team', self.sender_email))
            msg['To']      = receiver_email
            msg['Subject'] = "Welcome to OEMS — Your Login Credentials"
            text = (f"Hi {first_name},\n\nYour OEMS account is ready.\n\n"
                    f"Admission No: {admission_no}\nPassword: {password}\n"
                    f"Program: {program} | Branch: {branch} | Semester: {semester}\n\n"
                    f"Please login and change your password immediately.\n\nRegards,\nOEMS Support Team\nRef ID: {ref_id}")
            html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px;margin:0;">
<table align="center" width="100%" style="max-width:500px;background:#fff;border-radius:8px;border:1px solid #e0e0e0;margin:0 auto;border-collapse:collapse;">
  <tr><td style="background:#e8f5e9;padding:16px 20px;text-align:center;border-radius:8px 8px 0 0;border-bottom:1px solid #c8e6c9;">
    <p style="margin:0;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:1px;">OEMS Support Team</p>
    <h2 style="margin:4px 0 0;color:#1e293b;font-size:20px;font-weight:700;">Welcome to OEMS</h2>
  </td></tr>
  <tr><td style="padding:28px 24px;">
    <p style="margin:0 0 16px;color:#333;font-size:15px;">Hi <strong>{first_name}</strong>,</p>
    <p style="margin:0 0 16px;color:#555;font-size:14px;line-height:1.6;">Your OEMS account is ready. Use the credentials below to login:</p>
    <table width="100%" style="background:#f8fafc;border-radius:6px;border:1px solid #e0e0e0;margin-bottom:16px;border-collapse:collapse;">
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;width:45%;">Admission No</td>
          <td style="padding:10px 14px;font-size:15px;border-bottom:1px solid #eee;"><span style="font-family:monospace;font-weight:700;color:#1e293b;">{admission_no}</span></td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;">Password</td>
          <td style="padding:10px 14px;font-size:15px;border-bottom:1px solid #eee;"><span style="font-family:monospace;font-weight:700;color:#4f46e5;">{password}</span></td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;">Program</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;border-bottom:1px solid #eee;">{program}</td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;">Branch</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;border-bottom:1px solid #eee;">{branch}</td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;">Semester</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;">{semester}</td></tr>
    </table>
    <table width="100%" style="margin-bottom:20px;">
      <tr><td style="background:#fff3e0;border-left:4px solid #ff9800;border-radius:0 4px 4px 0;padding:12px 14px;">
        <p style="margin:0;color:#e65100;font-size:13px;line-height:1.5;">⚠️ Please login and change your password immediately for security.</p>
      </td></tr>
    </table>
    <p style="margin:0 0 4px;color:#333;font-size:14px;"><strong>Regards,</strong></p>
    <p style="margin:0;color:#555;font-size:14px;">OEMS Support Team</p>
  </td></tr>
  <tr><td style="padding:12px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:11px;color:#aaa;">Ref ID: {ref_id}</p>
  </td></tr>
</table></body></html>"""
            msg.attach(MIMEText(text, 'plain'))
            msg.attach(MIMEText(html, 'html'))
            with self._create_connection() as server:
                server.sendmail(self.sender_email, receiver_email, msg.as_string())
            logger.info(f"Welcome email sent to {receiver_email}")
            return True
        except Exception as e:
            logger.error(f"Welcome email failed: {e}"); return False

    def send_otp_email(self, receiver_email, otp, mode="email", user_name="Student"):
        try:
            first_name = get_first_name(user_name)
            ref_id     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = MIMEMultipart('alternative')
            msg['From'] = formataddr(('OEMS Support', self.sender_email))
            msg['To']   = receiver_email
            if mode == "password":
                msg['Subject'] = 'OEMS Account - Password Reset Request'
                action_text  = "reset the password for your OEMS account"
                warning_text = "If you did not request a password reset, please ignore this email."
                heading      = "Password Reset Request"
            else:
                msg['Subject'] = 'OEMS Account - Verify Your New Email'
                action_text  = "update the email address associated with your OEMS account"
                warning_text = "If you did not request an email change, please ignore this email."
                heading      = "Email Verification"
            text = (f"Hi {first_name},\n\nWe received a request to {action_text}.\n\n"
                    f"Verification Code\nPlease use the following 6-digit code. Valid for {EMAIL_CONFIG['OTP_EXPIRY_MINUTES']} minutes.\n\n"
                    f"{otp}\n\nSecurity Notice\n{warning_text}\n\nRegards,\nOEMS Support Team\nRef ID: {ref_id}")
            html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px;margin:0;">
<table align="center" width="100%" style="max-width:500px;background:#fff;border-radius:8px;border:1px solid #e0e0e0;margin:0 auto;border-collapse:collapse;">
  <tr><td style="background:#e8f5e9;padding:16px 20px;text-align:center;border-radius:8px 8px 0 0;border-bottom:1px solid #c8e6c9;">
    <p style="margin:0;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:1px;">OEMS Security Update</p>
    <h2 style="margin:4px 0 0;color:#1e293b;font-size:20px;font-weight:700;">{heading}</h2>
  </td></tr>
  <tr><td style="padding:28px 24px;">
    <p style="margin:0 0 16px;color:#333;font-size:15px;">Hi <strong>{first_name}</strong>,</p>
    <p style="margin:0 0 20px;color:#555;font-size:14px;line-height:1.6;">We received a request to {action_text}.</p>
    <p style="margin:0 0 8px;color:#333;font-size:14px;font-weight:700;">Verification Code</p>
    <p style="margin:0 0 12px;color:#555;font-size:14px;line-height:1.6;">Please use the following 6-digit code to complete your process. It is valid for {EMAIL_CONFIG['OTP_EXPIRY_MINUTES']} minutes.</p>
    <table width="100%" style="margin-bottom:20px;"><tr>
      <td align="center" style="background:#f8fafc;padding:16px;border-radius:6px;border:1.5px dashed #b0bec5;">
        <span style="font-family:monospace;font-size:32px;font-weight:800;letter-spacing:10px;color:#1e293b;">{otp}</span>
      </td></tr></table>
    <p style="margin:0 0 8px;color:#333;font-size:14px;font-weight:700;">Security Notice</p>
    <table width="100%" style="margin-bottom:24px;"><tr>
      <td style="background:#fff3e0;border-left:4px solid #ff9800;border-radius:0 4px 4px 0;padding:12px 14px;">
        <p style="margin:0;color:#e65100;font-size:13px;line-height:1.5;">⚠️ {warning_text}</p>
      </td></tr></table>
    <p style="margin:0 0 4px;color:#333;font-size:14px;"><strong>Regards,</strong></p>
    <p style="margin:0;color:#555;font-size:14px;">OEMS Support Team</p>
  </td></tr>
  <tr><td style="padding:12px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:11px;color:#aaa;">Ref ID: {ref_id}</p>
  </td></tr>
</table></body></html>"""
            msg.attach(MIMEText(text, 'plain'))
            msg.attach(MIMEText(html, 'html'))
            with self._create_connection() as server:
                server.sendmail(self.sender_email, receiver_email, msg.as_string())
            return True
        except Exception as e:
            logger.error(f"OTP email failed: {e}"); return False

    def send_success_email(self, receiver_email, mode, user_name="Student"):
        try:
            first_name = get_first_name(user_name)
            ref_id     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = MIMEMultipart('alternative')
            msg['From'] = formataddr(('OEMS Support', self.sender_email))
            msg['To']   = receiver_email
            if mode == 'password':
                msg['Subject'] = "OEMS Account - Password Updated Successfully"
                heading = "Password Updated"; message = "Your password has been successfully changed."
            else:
                msg['Subject'] = "OEMS Account - Email Updated Successfully"
                heading = "Email Updated"; message = "Your email address has been successfully updated."
            text = f"Hi {first_name},\n\n{message}\n\nRegards,\nOEMS Support Team\nRef ID: {ref_id}"
            html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px;margin:0;">
<table align="center" width="100%" style="max-width:500px;background:#fff;border-radius:8px;border:1px solid #e0e0e0;margin:0 auto;border-collapse:collapse;">
  <tr><td style="background:#e8f5e9;padding:16px 20px;text-align:center;border-radius:8px 8px 0 0;border-bottom:1px solid #c8e6c9;">
    <p style="margin:0;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:1px;">OEMS Security Update</p>
    <h2 style="margin:4px 0 0;color:#1e293b;font-size:20px;font-weight:700;">{heading}</h2>
  </td></tr>
  <tr><td style="padding:28px 24px;">
    <p style="margin:0 0 16px;color:#333;font-size:15px;">Hi <strong>{first_name}</strong>,</p>
    <p style="margin:0 0 20px;color:#555;font-size:14px;line-height:1.6;">{message}</p>
    <p style="margin:0 0 4px;color:#333;font-size:14px;"><strong>Regards,</strong></p>
    <p style="margin:0;color:#555;font-size:14px;">OEMS Support Team</p>
  </td></tr>
  <tr><td style="padding:12px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:11px;color:#aaa;">Ref ID: {ref_id}</p>
  </td></tr>
</table></body></html>"""
            msg.attach(MIMEText(text, 'plain'))
            msg.attach(MIMEText(html, 'html'))
            with self._create_connection() as server:
                server.sendmail(self.sender_email, receiver_email, msg.as_string())
            return True
        except Exception as e:
            logger.error(f"Success email failed: {e}"); return False

    def create_exam_alert_msg(self, receiver_email, student_name, exam_name, exam_date, duration):
        first_name = get_first_name(student_name)
        ref_id     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = MIMEMultipart('alternative')
        msg['From']    = formataddr(('OEMS Examination Team', self.sender_email))
        msg['To']      = receiver_email
        msg['Subject'] = f"New Exam Scheduled: {exam_name}"
        text = f"Hi {first_name},\n\nNew exam scheduled.\n\nCourse: {exam_name}\nDate: {exam_date}\nDuration: {duration} minutes\n\nBest of luck!\n\nRegards,\nOEMS Examination Team\nRef ID: {ref_id}"
        html = f"""
<html><body style="font-family:Arial,sans-serif;background:#f4f5f7;padding:20px;margin:0;">
<table align="center" width="100%" style="max-width:500px;background:#fff;border-radius:8px;border:1px solid #e0e0e0;margin:0 auto;border-collapse:collapse;">
  <tr><td style="background:#e8f5e9;padding:16px 20px;text-align:center;border-radius:8px 8px 0 0;border-bottom:1px solid #c8e6c9;">
    <p style="margin:0;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:1px;">OEMS Examination Team</p>
    <h2 style="margin:4px 0 0;color:#1e293b;font-size:20px;font-weight:700;">New Exam Scheduled</h2>
  </td></tr>
  <tr><td style="padding:28px 24px;">
    <p style="margin:0 0 16px;color:#333;font-size:15px;">Hi <strong>{first_name}</strong>,</p>
    <p style="margin:0 0 16px;color:#555;font-size:14px;line-height:1.6;">A new exam has been published for your batch:</p>
    <table width="100%" style="background:#f8fafc;border-radius:6px;border:1px solid #e0e0e0;margin-bottom:20px;border-collapse:collapse;">
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;width:35%;">Course</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;border-bottom:1px solid #eee;">{exam_name}</td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;border-bottom:1px solid #eee;">Date</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;border-bottom:1px solid #eee;">{exam_date}</td></tr>
      <tr><td style="padding:10px 14px;color:#333;font-size:14px;font-weight:600;">Duration</td>
          <td style="padding:10px 14px;color:#555;font-size:14px;">{duration} minutes</td></tr>
    </table>
    <p style="margin:0 0 20px;color:#555;font-size:14px;">Best of luck!</p>
    <p style="margin:0 0 4px;color:#333;font-size:14px;"><strong>Regards,</strong></p>
    <p style="margin:0;color:#555;font-size:14px;">OEMS Examination Team</p>
  </td></tr>
  <tr><td style="padding:12px 24px;border-top:1px solid #f0f0f0;text-align:center;">
    <p style="margin:0;font-size:11px;color:#aaa;">Ref ID: {ref_id}</p>
  </td></tr>
</table></body></html>"""
        msg.attach(MIMEText(text, 'plain'))
        msg.attach(MIMEText(html, 'html'))
        return msg

    def send_bulk_exam_alerts(self, student_list, exam_name, exam_date, duration):
        if not student_list: return {'success': False, 'message': 'No students'}
        success_count = 0; failed_emails = []
        try:
            with self._create_connection() as server:
                for student in student_list:
                    receiver_email = student.get('email')
                    student_name   = student.get('name') or 'Student'
                    if not receiver_email or not is_valid_email(receiver_email):
                        failed_emails.append({'name': student_name, 'reason': 'Invalid email'}); continue
                    try:
                        msg = self.create_exam_alert_msg(receiver_email, student_name, exam_name, exam_date, duration)
                        server.sendmail(self.sender_email, receiver_email, msg.as_string())
                        success_count += 1
                    except Exception as e:
                        failed_emails.append({'name': student_name, 'email': receiver_email, 'reason': str(e)})
            return {'success': True, 'sent': success_count, 'failed': len(failed_emails)}
        except Exception as e:
            return {'success': False, 'message': str(e)}


email_service = EmailService()

def send_email_async(func, *args, **kwargs):
    def task():
        try: func(*args, **kwargs)
        except Exception as e: logger.error(f"Async email failed: {e}")
    t = threading.Thread(target=task); t.daemon = True; t.start()
    return t


# ── SEND OTP
@app.route('/send_otp', methods=['POST'])
@rate_limit(max_attempts=5, window_minutes=5)
def send_otp():
    try:
        data      = request.get_json()
        if not data: return jsonify({"success": False, "message": "No data"}), 400
        new_email = sanitize_input(data.get('email'))
        mode      = data.get('mode', 'email')
        user_name = data.get('name') or data.get('student_name')
        if not new_email or not is_valid_email(new_email):
            return jsonify({"success": False, "message": "Valid email required"}), 400
        if mode == 'email':
            conn = get_db_connection(); cursor = conn.cursor()
            try:
                cursor.execute("SELECT id FROM students WHERE email=%s", (new_email,))
                if cursor.fetchone(): return jsonify({"success": False, "message": "Email already registered"}), 400
            finally: cursor.close(); conn.close()
        if mode not in ['email', 'password']: return jsonify({"success": False, "message": "Invalid mode"}), 400
        otp = OTPManager.generate_otp()
        OTPManager.store_otp(new_email, otp, mode, user_name)
        send_email_async(email_service.send_otp_email, new_email, otp, mode, user_name)
        return jsonify({"success": True, "message": "OTP sent"}), 200
    except TooManyRequests as e: return jsonify({"success": False, "message": str(e)}), 429
    except Exception as e: logger.error(f"send_otp: {e}"); return jsonify({"success": False, "message": "Internal server error"}), 500

# ── VERIFY OTP
@app.route('/verify_otp', methods=['POST'])
@rate_limit(max_attempts=5, window_minutes=5)
def verify_otp():
    try:
        data         = request.get_json()
        if not data: return jsonify({"success": False, "message": "No data"}), 400
        user_otp     = sanitize_input(data.get('otp'))
        mode         = data.get('mode')
        target_email = sanitize_input(data.get('email'))
        if not user_otp or not target_email or not is_valid_email(target_email):
            return jsonify({"success": False, "message": "OTP and valid email required"}), 400
        is_valid, result = OTPManager.verify_otp(user_otp)
        if not is_valid: return jsonify({"success": False, "message": result}), 400
        otp_data = result
        if otp_data['email'] != target_email: return jsonify({"success": False, "message": "Email mismatch"}), 400
        user_name = otp_data.get('user_name', 'Student')
        send_email_async(email_service.send_success_email, target_email, mode or otp_data['mode'], user_name)
        return jsonify({"success": True, "message": "Verification successful", "verified_email": target_email}), 200
    except TooManyRequests as e: return jsonify({"success": False, "message": str(e)}), 429
    except Exception as e: logger.error(f"verify_otp: {e}"); return jsonify({"success": False, "message": "Internal server error"}), 500

# ── RESEND OTP
@app.route('/resend_otp', methods=['POST'])
@rate_limit(max_attempts=3, window_minutes=10)
def resend_otp():
    try:
        data  = request.get_json()
        email = sanitize_input(data.get('email'))
        if not email or not is_valid_email(email): return jsonify({"success": False, "message": "Valid email required"}), 400
        if 'otp_data' in session:
            ca = datetime.fromisoformat(session['otp_data']['created_at'])
            if datetime.now() - ca < timedelta(seconds=30):
                return jsonify({"success": False, "message": f"Wait {30-(datetime.now()-ca).seconds}s before new OTP"}), 429
        od = session.get('otp_data', {})
        otp = OTPManager.generate_otp()
        OTPManager.store_otp(email, otp, od.get('mode','email'), od.get('user_name','Student'))
        send_email_async(email_service.send_otp_email, email, otp, od.get('mode','email'), od.get('user_name','Student'))
        return jsonify({"success": True, "message": "New OTP sent"}), 200
    except TooManyRequests as e: return jsonify({"success": False, "message": str(e)}), 429
    except Exception as e: logger.error(f"resend_otp: {e}"); return jsonify({"success": False, "message": "Internal server error"}), 500

# ── BULK EXAM ALERTS
@app.route('/send_bulk_exam_alerts', methods=['POST'])
def bulk_exam_alerts():
    try:
        data = request.get_json()
        for f in ['students','exam_name','exam_date','duration']:
            if f not in data: return jsonify({"success": False, "message": f"Missing: {f}"}), 400
        student_list = data['students']
        if not isinstance(student_list, list) or not student_list:
            return jsonify({"success": False, "message": "Student list empty"}), 400
        send_email_async(email_service.send_bulk_exam_alerts, student_list, data['exam_name'], data['exam_date'], data['duration'])
        return jsonify({"success": True, "message": "Bulk alert started", "total_students": len(student_list)}), 202
    except Exception as e: logger.error(f"bulk_alerts: {e}"); return jsonify({"success": False, "message": "Internal server error"}), 500

# ── STUDENT RESULT PAGE
@app.route("/my_result/<int:exam_id>")
@login_required("student")
def student_result(exam_id):
    student_id = session.get("student_id")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM results WHERE student_id=%s AND exam_id=%s", (student_id, exam_id))
    result = cursor.fetchone()
    if not result:
        cursor.close(); conn.close()
        return "<script>alert('Result not found.'); window.location.href='/student';</script>"
    cursor.execute("SELECT * FROM students WHERE id=%s", (student_id,))
    student = cursor.fetchone()
    cursor.execute("SELECT * FROM exams WHERE id=%s", (exam_id,))
    exam = cursor.fetchone()
    cursor.execute("SELECT q.question_text, q.marks, q.question_type, a.answer AS student_answer, a.score, a.feedback FROM answers a JOIN questions q ON a.question_id=q.id WHERE a.student_id=%s AND a.exam_id=%s ORDER BY q.id", (student_id, exam_id))
    answers = cursor.fetchall()
    cursor.close(); conn.close()
    max_marks   = sum(a['marks'] for a in answers)
    total_score = result['total_score'] or 0
    percentage  = round((total_score/max_marks*100), 1) if max_marks > 0 else 0
    total_q     = len(answers)
    attempted   = sum(1 for a in answers if str(a.get('student_answer') or '').strip())
    grade = 'O' if percentage>=90 else 'A' if percentage>=75 else 'B' if percentage>=60 else 'C' if percentage>=45 else 'D' if percentage>=35 else 'F'
    return render_template('student_result.html', student=student, exam=exam, answers=answers, total_score=total_score,
        max_marks=max_marks, percentage=percentage, grade=grade, total_questions=total_q, attempted=attempted,
        not_attempted=total_q-attempted, submission_status=result['submission_status'])

# ── VIOLATION LOGS — Single page (DB-based)
@app.route("/violation_logs")
@login_required("admin")
def violation_logs():
    ensure_violation_schema()  # guarantees the enriched columns exist
    admin_branch = session.get("admin_branch")
    conn   = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    base_select = """
        SELECT v.id, v.student_id, v.exam_id, v.violation_type,
               v.details, v.created_at,
               COALESCE(v.severity, 1)      AS severity,
               v.confidence                 AS confidence,
               COALESCE(v.source, 'client') AS source,
               v.evidence                   AS evidence,
               s.name AS student_name, s.admission_no,
               s.program, s.branch, s.semester,
               e.title AS exam_title
        FROM exam_violations v
        JOIN students s ON v.student_id = s.id
        JOIN exams    e ON v.exam_id    = e.id
    """
    if admin_branch == "ALL":
        cursor.execute(base_select + " ORDER BY v.created_at DESC")
    else:
        cursor.execute(base_select + " WHERE e.branch = %s ORDER BY v.created_at DESC", (admin_branch,))
    logs = cursor.fetchall()
    cursor.close(); conn.close()

    total_violations = len(logs)
    terminations     = sum(1 for l in logs if l['violation_type'] == 'force_terminate')
    ai_flags         = sum(1 for l in logs if l.get('source') == 'ai')
    unique_students  = len(set(l['student_id'] for l in logs))
    unique_exams     = len(set(l['exam_id']    for l in logs))

    # Per-student risk ranking for the analytics panel: students with the most
    # (severity-weighted) violations bubble to the top for review.
    risk = {}
    for l in logs:
        key = (l['student_id'], l['student_name'], l['admission_no'])
        r = risk.setdefault(key, {"count": 0, "weight": 0, "terminated": False})
        r["count"] += 1
        r["weight"] += int(l.get('severity') or 1)
        if l['violation_type'] == 'force_terminate':
            r["terminated"] = True
    top_risk = sorted(
        ({"name": k[1], "admission_no": k[2], **v} for k, v in risk.items()),
        key=lambda x: (x["terminated"], x["weight"]), reverse=True
    )[:8]

    return render_template("violation_logs.html",
        logs=logs,
        total_violations=total_violations,
        terminations=terminations,
        ai_flags=ai_flags,
        unique_students=unique_students,
        unique_exams=unique_exams,
        top_risk=top_risk,
    )

# ── LOGOUT
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ================================================================
# RUN
# ================================================================
if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=False)
