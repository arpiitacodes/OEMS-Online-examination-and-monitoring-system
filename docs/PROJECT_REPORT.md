# Online Examination & Monitoring System (OEMS)
## Project Report

**A Final-Year / Major Project Report**

| | |
|---|---|
| **Project Title** | OEMS — Online Examination & Monitoring System |
| **Author** | Anshuman Kumar Singh |
| **Repository** | https://github.com/anshumanks2004/OEMS-ExamSystem |
| **Document Type** | Technical Project Report |
| **Backend** | Python 3.14 · Flask 3.1 · MySQL 8 |
| **AI/ML** | InsightFace (ArcFace), YOLOv8, SBERT, scikit-learn TF-IDF |
| **Secure Client** | Electron 28 kiosk browser |

---

## Abstract

The **Online Examination & Monitoring System (OEMS)** is a full-stack, AI-assisted platform for conducting secure online examinations and remotely monitoring candidate integrity. It combines a Flask web application, a MySQL database, several deep-learning inference engines, and a dedicated Electron-based **secure exam browser** to deliver an end-to-end assessment workflow: student enrolment, biometric login, exam authoring, proctored exam delivery, automated grading, plagiarism detection, and result publishing.

The platform's distinguishing feature is its **multi-layer integrity stack**. Identity is verified at login using a real **ArcFace 512-dimensional face embedding** (InsightFace `buffalo_l`) with active-liveness challenges, defeating photo and account-sharing attacks. During the exam, a **temporal, confidence-scored AI proctoring engine** uses RetinaFace detection, true 3-D head-pose estimation via `cv2.solvePnP`, YOLOv8 object detection (phone/book/device/extra-person), anti-spoofing heuristics, and ArcFace identity-continuity checks to flag suspicious behaviour — escalating to a logged violation only when a signal persists across multiple frames, which sharply reduces false positives. Theory answers are graded automatically with a **Sentence-BERT (SBERT)** semantic model, and cross-student copying is caught with a **TF-IDF cosine-similarity** plagiarism check that can place results on hold. The whole exam can be locked into a **kiosk-mode Electron browser** that blocks navigation, shortcuts, and window switching, and can additionally restrict an exam to the campus network.

This report documents the system's architecture, technology choices and their justifications, database design, authentication and face-verification workflows, module-level descriptions, the HTTP/JSON API surface, security features, and the complete examination lifecycle, followed by the engineering challenges encountered and their solutions, the planned future scope, and conclusions.

---

## 1. Introduction

Remote and computer-based examinations have become a permanent part of higher education and professional certification. Their convenience, however, introduces a hard problem: **how do you trust that the right person took the exam, alone, without external help, and without copying?** Commercial proctoring suites exist but are expensive, opaque, privacy-invasive, and difficult to integrate into an institution's own workflow.

OEMS was built to answer that problem with a **self-hosted, transparent, and modular** system that an institution can run on its own infrastructure. It treats integrity as a layered concern rather than a single check:

1. **Who is taking the exam?** — biometric face verification on every login.
2. **Are they who they claimed throughout?** — continuous identity checks during the exam.
3. **Are they alone and focused?** — multi-face, gaze, and object detection.
4. **Is the environment locked down?** — a secure kiosk browser and optional campus-only enforcement.
5. **Did they copy from peers?** — automated plagiarism detection across submissions.

The result is a single codebase that an administrator can deploy to create exams, enrol students (individually or in bulk via CSV), publish exams to specific program/branch/semester cohorts, and then receive auto-graded, integrity-checked results delivered to students by email as a formatted PDF.

### 1.1 Document Organization

The remainder of this report is organized as follows: the **Problem Statement** (§2) and **Objectives** (§3) frame the work; **Scope** (§4) bounds it; **System Architecture** (§5) and **Technology Stack** (§6) describe the design and tooling; **Database Design** (§7), **Authentication Flow** (§8), and **Face Verification Workflow** (§9) cover the data and identity layers; **Module Descriptions** (§10), **API Overview** (§11), and **Security Features** (§12) detail the implementation; **Project Workflow** (§13) walks the examination lifecycle; and the report closes with **Challenges & Solutions** (§15), **Future Scope** (§16), and **Conclusion** (§17).

---

## 2. Problem Statement

Conducting trustworthy examinations over the internet faces several concrete, simultaneous challenges:

- **Impersonation.** A student may have someone else log in and take the exam on their behalf. Plain username/password authentication cannot detect this.
- **Account / face sharing.** One person may try to register their face on multiple student accounts, or sit two exams for two people.
- **Mid-exam person swap.** Even if the correct person logs in, a different person could take over the keyboard after the exam starts.
- **Use of unauthorized aids.** Phones, printed notes, textbooks, second monitors, and additional people in the room undermine assessment validity.
- **Window / focus evasion.** Candidates may switch tabs, open developer tools, screenshot questions, or print the paper.
- **False accusations from naïve detectors.** Older proctoring methods (e.g., raw Haar cascades and bounding-box "gaze") are lighting-sensitive and fire a flood of false violations on normal head movement, eroding trust and unfairly penalizing honest students.
- **Manual grading bottleneck.** Theory/descriptive answers traditionally require human evaluation, which is slow and inconsistent at scale.
- **Collusion.** Students sitting the same paper may copy each other's descriptive answers.

OEMS addresses each of these in a **single integrated system**, with a deliberate emphasis on **minimizing false positives** so that the integrity layer is fair as well as strict.

---

## 3. Objectives

The project set out to achieve the following measurable objectives:

1. **Biometric identity assurance** — verify each student's face against an enrolled reference on *every* login, using a modern, lighting-robust recognition model with active-liveness anti-spoofing.
2. **Continuous, fair proctoring** — monitor the webcam during proctored exams for multiple people, looking away, phones, books, devices, and identity drift, while keeping false positives low through temporal confidence accumulation.
3. **Secure exam environment** — provide a kiosk-mode browser that blocks navigation, dangerous keyboard shortcuts, copy/paste, new windows, and forced quitting, and optionally restricts exams to the campus network.
4. **Automated objective grading** — score MCQ (single-choice) and MSQ (multi-select) questions instantly and deterministically.
5. **Automated subjective grading** — evaluate theory answers semantically with SBERT, including guardrails against keyword-stuffing and repetition.
6. **Plagiarism detection** — compare descriptive answers across all candidates and hold suspiciously similar submissions for review.
7. **End-to-end exam lifecycle automation** — auto-trigger post-exam processing (force-submit, plagiarism, grading, emailing) when the exam window closes, with no manual intervention required.
8. **Role-based administration** — branch-scoped admin control over students, exams, questions, results, and violation analytics.
9. **Auditable evidence** — store rich, confidence-scored violation records with image snapshots for human review.

---

## 4. Scope

### 4.1 In Scope

- Two user roles: **Admin** (institution staff / proctor) and **Student**.
- Student management: single add, **bulk CSV import**, credential email, resend credentials, edit, delete.
- Exam authoring: create exams targeted at a *program / branch / semester* cohort; MCQ, MSQ, and theory questions; draft/publish lifecycle.
- Three browser-enforcement modes per exam: `any`, `secure_any` (secure browser required), and `secure_campus` (secure browser **and** campus IP required).
- Per-exam AI-proctoring toggle.
- ArcFace face enrolment + verification with blink/turn liveness and duplicate-face prevention.
- Real-time AI proctoring with temporal escalation and evidence capture.
- Automated grading (objective + SBERT theory) and TF-IDF plagiarism with admin hold/release/disqualify controls.
- Email notifications: welcome credentials, exam alerts, OTP for email/password changes, result PDF, and hold notices.
- PDF result reports (ReportLab) with question-wise analysis, grade, and feedback.
- An Electron kiosk browser and an optional Nginx reverse-proxy front.

### 4.2 Out of Scope (Current Version)

- Live human invigilator video streaming / two-way audio.
- Mobile native apps (the secure browser targets desktop).
- Question banks with randomized per-student question selection (questions are shuffled in order, not sampled from a larger pool).
- Built-in horizontal scaling / load balancing beyond a single Flask process (the post-exam scheduler uses in-process daemon threads).

---

## 5. System Architecture

OEMS follows a classic **server-authoritative, layered web architecture**. The browser (standard or secure) is treated as untrusted: every integrity decision — face match, liveness, violation escalation, grading — is made on the server, so a tampered client cannot fake a pass.

### 5.1 High-Level Architecture

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│   Secure Browser (Electron)  │         │      Standard Web Browser     │
│   kiosk · header signature   │         │     (admin & open exams)      │
└──────────────┬──────────────┘         └───────────────┬──────────────┘
               │  HTTP(S) (X-OEMS-Secure-Browser header) │
               └────────────────────┬────────────────────┘
                                    ▼
                      ┌──────────────────────────┐
                      │   Nginx (optional)        │
                      │  static · X-Forwarded-For │
                      └───────────┬──────────────┘
                                  ▼
                      ┌──────────────────────────┐
                      │   Flask App (app.py)      │
                      │  ─ Auth & sessions        │
                      │  ─ Exam delivery          │
                      │  ─ Proctoring API         │
                      │  ─ Grading pipeline       │
                      │  ─ Email / OTP / PDF      │
                      └──────────┬───────────────┘
        ┌──────────────┬─────────┼──────────────┬───────────────┐
        ▼              ▼         ▼              ▼               ▼
 ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────┐
 │face_engine │ │proctor_    │ │ SBERT +  │ │  YOLOv8  │ │ MySQL pool  │
 │ ArcFace +  │ │engine.py   │ │ TF-IDF   │ │ objects  │ │ 7 tables    │
 │ liveness   │ │ pose/gaze/ │ │ grading/ │ │          │ │             │
 │(InsightF.) │ │ identity   │ │ plagiar. │ │          │ │             │
 └────────────┘ └────────────┘ └──────────┘ └──────────┘ └─────────────┘
                                  │
                                  ▼
                        ┌──────────────────┐
                        │   Gmail SMTP      │
                        │ welcome · OTP ·   │
                        │ result PDF email  │
                        └──────────────────┘
```

### 5.2 Architectural Layers

| Layer | Responsibility | Implementation |
|---|---|---|
| **Presentation** | UI rendering, client-side exam runner, camera capture, client-event violation detection | Jinja2 server-rendered templates + vanilla JavaScript |
| **Secure client** | Kiosk lockdown, navigation/shortcut blocking, browser-signature header | Electron `main.js` + `preload.js` |
| **Application** | Routing, sessions, auth, exam flow, orchestration of ML engines, email/PDF | `app.py` (Flask) |
| **Domain / AI** | Face recognition, proctoring CV, semantic grading, plagiarism | `face_engine.py`, `proctor_engine.py`, SBERT, scikit-learn |
| **Persistence** | Relational storage with pooled connections | MySQL via `mysql-connector-python` |
| **Integration** | Outbound email (SMTP), optional reverse proxy | `smtplib`, Nginx |

### 5.3 Server-Authoritative Principle

A central design rule is that **the client cannot be trusted to report its own integrity**. Concretely:

- Webcam frames are streamed to `/student_face_frame` (login) and `/detect_cheating` (exam); detection, liveness, and matching all happen server-side.
- The face-capture **session state** (collected embeddings, liveness flags) lives in a server-side dictionary keyed by student id, protected by a lock — JavaScript cannot forge it.
- When the AI engine escalates a violation, it is **persisted server-side** in the same request, so the on-screen violation counter can never be lowered by tampering with the browser.

---

## 6. Technology Stack

Each technology below was chosen for a specific reason, documented here for evaluation and viva defence.

| Layer | Technology | Why this choice |
|---|---|---|
| **Web framework** | Flask 3.1.3 + Werkzeug 3.1.7 | Lightweight, explicit, and ideal for a single-author project: routing, sessions, and templating without the ceremony of a larger framework. Werkzeug provides the secure password hashing (`generate_password_hash` / `check_password_hash`, PBKDF2). |
| **CORS** | flask-cors 6.0.2 | Allows controlled cross-origin requests (e.g., from the Electron client / tooling). |
| **Database** | MySQL 8 via `mysql-connector-python` 9.6.0 with a **connection pool** (`pool_size=10`) | A mature relational DB fits the highly relational data (students→answers→results→violations). The pool avoids the cost of opening a new TCP connection per request and prevents connection exhaustion under the burst of post-exam grading threads. |
| **Config / secrets** | `python-dotenv` 1.2.2 | Keeps secrets (`SECRET_KEY`, DB password, SMTP app password) out of source control in a gitignored `.env`. The app refuses to start without `SECRET_KEY`. |
| **Face detection + recognition** | InsightFace 1.0.1 (`buffalo_l`, ArcFace) on ONNX Runtime 1.26.0 | ArcFace produces a 512-d identity embedding that is **robust to lighting, pose, and expression** — the root-cause fix for the old raw-pixel engine's "sometimes works, sometimes fails" behaviour. The same RetinaFace detector + ArcFace embeddings are **reused** by the proctoring engine, so there is no extra model memory cost. ONNX Runtime gives portable CPU/CoreML inference. |
| **Object detection** | Ultralytics YOLOv8n (`yolov8n.pt`) | The `n` (nano) model is fast enough to run on CPU at a throttled cadence while reliably detecting COCO classes that matter for proctoring: cell phone, book, laptop/tv/remote/keyboard (devices), and person. |
| **Computer vision** | OpenCV (`opencv-contrib-python` 4.13) + NumPy 2.4 | Frame decode, 3-D head-pose via `solvePnP`, anti-spoof texture/FFT statistics, sharpness/brightness quality gating, and the one Haar eye-cascade used for the login blink challenge. |
| **Theory grading** | Sentence-Transformers (SBERT) 5.3.0 `all-MiniLM-L6-v2`, scikit-learn 1.8.0, PyTorch 2.11 | `all-MiniLM-L6-v2` is a small, fast, high-quality sentence-embedding model. Cosine similarity between the model answer and the student answer gives a **semantic** score that rewards meaning, not exact keyword matching — far better than string overlap for descriptive answers. |
| **Plagiarism** | scikit-learn `TfidfVectorizer` + cosine similarity | TF-IDF + cosine is the standard, explainable baseline for textual similarity across documents and needs no training — appropriate for comparing all students' descriptive answers pairwise. |
| **PDF reports** | ReportLab 4.4.10 | Generates a styled, multi-section A4 result PDF (student details, question-wise analysis, performance summary, grade, feedback) entirely in memory for emailing. |
| **Email / OTP** | Gmail SMTP over `smtplib` + `email` MIME, `email-validator` 2.3.0 | Reliable transactional email using a Google **App Password** (never the account password). Used for welcome credentials, exam alerts, OTP, result PDFs, and hold notices. |
| **Secure browser** | Electron 28 + Node 18+, Axios | Electron gives a Chromium webview that can be locked into **kiosk + fullscreen + alwaysOnTop**, with `globalShortcut` blocking, navigation allow-listing, and a unique `X-OEMS-Secure-Browser` request header the server can require. |
| **Frontend** | Jinja2 + vanilla JavaScript | Server-side rendering keeps the trust boundary on the server; no heavy SPA framework is needed. The exam runner and client-event proctoring are hand-written vanilla JS. |
| **Reverse proxy** | Nginx (optional) | Serves static files, forwards `X-Forwarded-For` (so the server can read the real client IP for campus enforcement), and sets generous timeouts for exam submission. |

---

## 7. Database Design

OEMS uses a normalized relational schema of **seven tables** in the `exam_system` MySQL database (`utf8mb4`). Foreign keys cascade on delete so that removing an exam or student cleans up all dependent rows.

### 7.1 Entity–Relationship Overview

```
admins (standalone — institution staff, branch-scoped)

students ──1:N──> answers <──N:1── questions ──N:1──> exams
   │                  │                                   ▲
   │                  └──────────── results ─────────────┘
   │                                  ▲
   └────────────── exam_violations ───┘
```

- A **student** has many **answers**, many **results** (one per exam attempted), and many **exam_violations**.
- An **exam** has many **questions**, many **answers**, many **results**, and many **exam_violations**.
- A **question** has many **answers** (one per student who attempted it).

### 7.2 Table Definitions

**`admins`** — institution staff / proctors.

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK AUTO_INCREMENT | |
| `admin_id` | VARCHAR(50) UNIQUE | Login identifier |
| `name` | VARCHAR(150) | |
| `password` | VARCHAR(255) | Werkzeug PBKDF2 hash |
| `branch` | VARCHAR(50) | e.g. `SCSE`, `SOM`, or `ALL` (super-admin) |

**`students`** — candidates. The three `face_*` columns are **added automatically** by `ensure_student_face_schema()` on first login (migration-safe).

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `name` | VARCHAR(150) | |
| `admission_no` | VARCHAR(50) UNIQUE | Login id (upper-cased) |
| `program`, `branch`, `semester` | VARCHAR | Cohort targeting |
| `email` | VARCHAR(150) | Nullable |
| `password` | VARCHAR(255) | PBKDF2 hash; default `OEMS@12345` |
| `face_embedding_v2` | MEDIUMBLOB | ArcFace 512-d float32 (2048 bytes) |
| `face_registered` | TINYINT(1) | 0/1 enrolment flag |
| `face_registered_at` | DATETIME | |

**`exams`**

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `title` | VARCHAR(255) | |
| `exam_type` | VARCHAR(20) | `theory` / objective |
| `total_marks` | INT | |
| `program`, `branch`, `semester` | VARCHAR | Target cohort |
| `start_time` | DATETIME | |
| `duration` | INT | Minutes |
| `status` | VARCHAR(20) | `draft` / `publish` |
| `browser_mode` | VARCHAR(30) | `any` / `secure_any` / `secure_campus` |
| `ai_proctoring` | TINYINT(1) | Per-exam camera-proctoring toggle |

**`questions`**

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `exam_id` | INT FK → exams | ON DELETE CASCADE |
| `question_text` | TEXT | |
| `question_type` | VARCHAR(20) | `mcq` / `msq` / `theory` |
| `optionA..optionD` | VARCHAR(500) | Nullable |
| `correct_answer` | VARCHAR(255) | `optionA` (MCQ), `optionA,optionC` sorted (MSQ), or answer outline (theory) |
| `marks` | INT | |

**`answers`** — one row per student per question.

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `student_id`, `exam_id`, `question_id` | INT FK | All cascade |
| `answer` | TEXT | |
| `score` | FLOAT NULL | **NULL = pending evaluation** (theory not yet graded) |
| `feedback` | TEXT | Auto-generated grading feedback |

**`results`** — one row per student per exam.

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `student_id`, `exam_id` | INT FK | Cascade |
| `total_score` | FLOAT | |
| `submission_status` | VARCHAR(30) | State machine (see below) |

**`exam_violations`** — proctoring audit trail. The `severity`, `confidence`, `source`, and `evidence` columns are **added automatically** by `ensure_violation_schema()`.

| Column | Type | Notes |
|---|---|---|
| `id` | INT PK | |
| `student_id`, `exam_id` | INT FK | Cascade |
| `violation_type` | VARCHAR(100) | e.g. `phone`, `multiple_faces`, `tab_switch`, `force_terminate` |
| `details` | TEXT | Human-readable label |
| `created_at` | DATETIME | Default `CURRENT_TIMESTAMP` |
| `severity` | TINYINT | 0=info, 1=minor, 2=major, 3=critical |
| `confidence` | FLOAT NULL | Detector confidence (AI rows) |
| `source` | VARCHAR(20) | `client` or `ai` |
| `evidence` | VARCHAR(255) | Relative path to a JPEG snapshot |

An index `idx_violation_lookup (exam_id, student_id, violation_type)` speeds the admin dashboard's per-student / per-exam aggregation.

### 7.3 Result Status State Machine

The `submission_status` field drives the entire post-exam pipeline:

```
                  ┌──────────────────┐
   submit_exam →  │ AwaitingExamEnd   │  (no grading happens yet)
                  └────────┬─────────┘
                           │  exam window closes → processor runs
            ┌──────────────┼───────────────────────┐
            ▼              ▼                         ▼
      ┌──────────┐   ┌──────────┐             ┌────────────┐
      │   Hold    │   │ Pending  │  evaluator  │ Evaluated  │
      │(plagiar.) │   │ (waiting │  finishes → │  + emailed │
      └────┬─────┘   │  retry)  │             └────────────┘
           │         └──────────┘
   admin release → Pending → (re-evaluate) → Evaluated
   admin disqualify → Disqualified (score 0, no email)
```

This explicit status machine is why grading is deterministic and auditable: a result only becomes `Evaluated` (and emails out) after all theory answers are scored and the row is not held or disqualified.

### 7.4 Design Decisions

- **Float-bytes BLOB for embeddings.** ArcFace vectors are stored as raw `float32` bytes (`serialize`/`deserialize` in `face_engine.py`) rather than CSV text — compact (2 KB) and lossless.
- **Lazy, migration-safe schema upgrades.** New columns (`face_*`, violation `severity/confidence/source/evidence`) are added at runtime if missing, so older databases upgrade transparently without a manual migration step.
- **Pending-as-NULL.** A NULL `answers.score` is the single source of truth for "not yet graded", letting aggregate queries cleanly detect when a result is ready.

---

## 8. Authentication Flow

OEMS has three authentication surfaces: **student** (two-factor: password + face), **admin** (password), and **OTP** (for self-service email/password updates). Sessions are signed with Flask's `SECRET_KEY`.

### 8.1 Student Login (Two-Factor: Password → Face)

```
1. POST /student_login  (admission_no + password)
        │
        ▼  check_password_hash() succeeds
2. session cleared; session["pending_student_auth"] = {student_id, created_at}
        │  (this is a *partial* auth — not logged in yet)
        ▼
3. redirect → /student_face_verify
        │  pending auth must be < FACE_AUTH_WINDOW_SECONDS (600s) old
        ▼
4. Live capture: browser streams frames → POST /student_face_frame
        │  server collects clean frames + active-liveness (blink/turn)
        ▼
5a. First time (not registered) → REGISTER: average frames, duplicate-face
    check, store embedding → ask to log in again.
5b. Returning → VERIFY: cosine(stored, probe) ≥ FACE_MATCH_THRESHOLD (0.42)
        │
        ▼  _finalize_student_session() sets full session, clears pending
6. redirect → /student dashboard
```

Key properties:

- The **password step alone never logs the student in** — it only creates a short-lived `pending_student_auth` marker. Only a successful face match calls `_finalize_student_session()`.
- The pending window (`FACE_AUTH_WINDOW_SECONDS`, default **600 s**) bounds how long the face step stays valid after the password.
- Every route that needs a logged-in user is wrapped with the `@login_required("student")` or `@login_required("admin")` decorator, which redirects to `/` if the session lacks the right key.

### 8.2 Admin Login

`GET/POST /admin_login` checks `admins.admin_id` + password (`check_password_hash`). On success the session stores `admin_id`, `role="admin"`, `admin_name`, and `admin_branch`. The `admin_branch` value scopes every admin query: an admin with branch `ALL` sees everything; otherwise they see only their branch's students, exams, results, and violations.

### 8.3 OTP Flow (Email / Password Self-Service)

For changing email or resetting a password, OEMS uses a 6-digit OTP:

- `POST /send_otp` → generates a cryptographically-random OTP (`secrets.randbelow`), stores it in the session with a 10-minute expiry and an attempt counter, and emails it.
- `POST /verify_otp` → validates the code (max 3 attempts, expiry-checked) and emails a success confirmation.
- `POST /resend_otp` → rate-limited resend (30-second cooldown).

All three are protected by a session-based `@rate_limit` decorator (e.g. 5 attempts / 5 minutes) that raises HTTP 429 when exceeded.

---

## 9. Face Verification Workflow

Biometric identity is implemented in `face_engine.py` (pure CV/NumPy) and orchestrated by the login routes in `app.py`. The browser only streams frames; **the server is authoritative** for every decision.

### 9.1 The Engine (`face_engine.py`)

- **Model:** InsightFace `FaceAnalysis` with `buffalo_l`, loading only the `detection` + `recognition` modules (no age/gender) to keep memory and latency low. It is a **lazily-loaded, thread-safe singleton** — heavy to load once, cheap to reuse. On Apple Silicon it prefers the CoreML execution provider, falling back to CPU.
- **Embeddings:** 512-d, L2-normalized `float32`. Because vectors are normalized, **cosine similarity is just a dot product** (`cosine()`), which is fast.
- **Liveness signals** are derived from the same detection pass: head **yaw** is estimated from the 5 facial keypoints, and a coarse blink is detected via an OpenCV Haar eye-cascade on the face crop.
- **Quality gating:** a per-frame **sharpness** (variance of Laplacian) check rejects blurry captures so a smeared embedding is never stored.

### 9.2 Enrolment (First Login)

```
For each streamed frame:
  ├─ detect exactly ONE confident, large-enough, sharp face
  ├─ reject if: no_face / too_far / multiple_faces / blurry
  ├─ accumulate the ArcFace embedding in the server-side session
  └─ track liveness (blink OR head-turn)

When FACE_REGISTER_FRAMES (6) clean frames + liveness pass:
  ├─ average + re-normalize the embeddings → stable identity vector
  ├─ DUPLICATE-FACE SCAN against every other account
  │     └─ if cosine ≥ FACE_DUPLICATE_THRESHOLD (0.55) → REJECT (anti-sharing)
  ├─ store vector in students.face_embedding_v2, set face_registered=1
  └─ ask the student to log in again
```

### 9.3 Verification (Every Subsequent Login)

```
Collect FACE_VERIFY_FRAMES (4) clean frames + a passed liveness challenge
  ├─ average → probe embedding
  ├─ similarity = cosine(stored, probe)
  ├─ if similarity ≥ FACE_MATCH_THRESHOLD (0.42) → MATCH → log in
  └─ else → reset capture, ask to re-centre and retry
```

### 9.4 Active-Liveness Challenges

To defeat a printed photo or a static image on a phone, the server requires a **live action**:

- **Blink:** eyes-open → closed → open transition observed.
- **Turn:** head-yaw exceeds `YAW_TURN_DEG` (16°) then re-centres.
- Configurable via `FACE_LIVENESS_CHALLENGE` = `blink` / `turn` / `any` (default `any`).

Because the liveness state machine lives in the server-side session, the client cannot simply POST "liveness OK".

### 9.5 Tunable Thresholds

| Parameter | Default | Meaning |
|---|---|---|
| `FACE_MATCH_THRESHOLD` | 0.42 | Cosine cutoff for "same person" (higher = stricter) |
| `FACE_DUPLICATE_THRESHOLD` | 0.55 | Blocks one face on two accounts |
| `FACE_MIN_DET_SCORE` | 0.55 | Minimum detector confidence |
| `FACE_MIN_SIZE` | 90 px | Reject faces too small / far |
| `FACE_REGISTER_FRAMES` / `FACE_VERIFY_FRAMES` | 6 / 4 | Frames averaged |
| `FACE_AUTH_WINDOW_SECONDS` | 600 | Face step validity after password |

---

## 10. Module Descriptions

### 10.1 `backend/app.py` — Flask Application (~2,800 lines)

The orchestration core. Responsibilities:

- **Auth & sessions:** student two-factor login, admin login, OTP, `login_required` and `rate_limit` decorators.
- **Student management:** single add, bulk CSV import (`utf-8-sig`, duplicate-skip, async welcome emails), resend credentials, edit profile, delete.
- **Exam authoring:** create/delete/publish/unpublish exams; add/edit/delete MCQ/MSQ/theory questions with type-specific validation and per-type limits (50 objective / 20 theory).
- **Exam delivery:** `/start_exam` enforces timing, single-attempt, browser-mode, and campus-IP gates; shuffles questions; schedules the end-processor.
- **Submission & grading orchestration:** `/submit_exam` scores objective questions instantly and stores theory answers as pending; the **exam-end processor** runs plagiarism + SBERT grading and emails results.
- **Proctoring API:** `/detect_cheating` (AI camera) and `/log_violation` (client events), plus evidence persistence and `/proctor_health`.
- **Email service:** welcome, OTP, exam-alert, result-PDF, and hold-notice emails (HTML + plain-text MIME) over Gmail SMTP, mostly fired asynchronously on daemon threads.
- **PDF generation:** `generate_result_pdf()` builds a styled A4 report in memory with ReportLab.

### 10.2 `backend/face_engine.py` — ArcFace Recognition + Liveness

Self-contained, thread-safe identity engine: detection, recognition, yaw/blink liveness signals, embedding averaging, and BLOB (de)serialization. No Flask/DB coupling. Also exposes `detect_faces()` — the primitive the proctoring engine builds on (every face + keypoints + embeddings).

### 10.3 `backend/proctor_engine.py` — AI Proctoring (~600 lines)

The exam-time monitoring engine. Two halves:

1. **Per-frame analysis (`analyze`)** — reuses the shared InsightFace detector for face counting, computes **real 3-D head-pose** via `cv2.solvePnP` against a canonical 5-point face model (with a geometric fallback), maps pose to an **attention** label, runs **YOLOv8** object detection on a throttled cadence, computes **anti-spoof** risk (texture variance + saturation spread + FFT high-frequency moiré energy), checks **identity continuity** vs the enrolled embedding, and gates on **lighting/quality** so a dark/blurry frame yields "can't assess" rather than a false "no face". Every observation carries a **confidence in [0,1]** and a **severity** (0–3).
2. **Temporal confidence layer (`process_frame`)** — per-code accumulator that **rises** while a signal keeps firing and **decays** when it stops; a violation is escalated only when the score crosses a per-code `trigger`, then a `cooldown` prevents re-spamming. Critical signals (phone, multi-person, identity) rise fast and barely decay; gaze rises slowly and decays fast so a quick glance never escalates. This is the **core false-positive reducer**. It also tracks a session **integrity** percentage (clean-frame ratio).

### 10.4 `secure-browser/` — Electron Kiosk Browser

`main.js` (main process) creates a fullscreen, kiosk, always-on-top, frameless window; injects the `X-OEMS-Secure-Browser: ElectronV1` header on every request; allow-lists navigation to `localhost`/`127.0.0.1`/`file://`; denies new windows; blocks ~20 dangerous global shortcuts (DevTools, new tab/window, Alt+F4, copy/paste, etc.); blocks force-quit until the exam is safely submitted; and writes an audit log. `preload.js` exposes a minimal, validated `secureBrowser` IPC bridge (`reportViolation`, `submitExam`, `examStarted`, `requestExit`) via `contextBridge` with `contextIsolation` on.

### 10.5 Templates (Jinja2)

20 server-rendered templates cover the full UI: landing/login, admin and student dashboards, student manager, add-student (single + bulk), exam/question authoring, the proctored **exam runner** (`start_exam.html` — ~1,200 lines of vanilla-JS proctoring), results summary/details, student result, plagiarism, **violation logs** (with per-student risk ranking), and the secure-browser/campus gates.

---

## 11. API Overview

OEMS exposes a server-rendered + JSON API. Selected routes:

### 11.1 Public / Auth

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Landing / login page |
| POST | `/student_login` | Password step → pending auth |
| GET | `/student_face_verify` | Live face-capture page |
| POST | `/student_face_frame` | **Stream one frame**; server returns guidance / register / verify / redirect |
| GET/POST | `/admin_login` | Admin password login |
| GET | `/logout` | Clear session |
| POST | `/send_otp`, `/verify_otp`, `/resend_otp` | OTP for email/password updates (rate-limited) |

### 11.2 Admin (all `@login_required("admin")`, branch-scoped)

| Method | Route | Purpose |
|---|---|---|
| GET | `/admin` | Dashboard (counts: students, active exams, pending AI checks) |
| GET | `/student_manager` | List students |
| GET/POST | `/add_student` | Single add + **bulk CSV** |
| POST | `/resend_credentials/<id>` | Re-email credentials |
| POST | `/delete_student/<id>` | Delete student + cascade |
| GET/POST | `/create_exam` | Create exam (draft) |
| GET/POST | `/add_question/<exam_id>` | Add MCQ/MSQ/theory question |
| GET/POST | `/edit_question/<id>`, POST `/delete_question/<id>` | Edit/delete |
| GET | `/questions/<exam_id>` | View questions |
| GET | `/publish_exam/<id>` / `/unpublish_exam/<id>` | Publish (emails cohort) / revert |
| POST | `/delete_exam/<id>` | Delete exam + cascade |
| GET | `/results` | Results summary |
| GET | `/result_details/<sid>/<eid>` | Per-student answers |
| GET | `/run_ai_check`, `/reset_ai_evaluation` | Manual grade / reset |
| GET | `/plagiarism/<exam_id>` | On-demand TF-IDF report |
| POST | `/trigger_exam_evaluation/<eid>` | Manually run end-processor |
| POST | `/release_result/<sid>/<eid>` | Release a held result |
| POST | `/reevaluate_result/<sid>/<eid>` | Re-grade a clean result |
| POST | `/disqualify_result/<sid>/<eid>` | Disqualify (score 0) |
| GET | `/violation_logs` | Proctoring audit + risk ranking |

### 11.3 Student (all `@login_required("student")`)

| Method | Route | Purpose |
|---|---|---|
| GET | `/student` | Dashboard (published exams for cohort) |
| GET/POST | `/edit_profile` | Update email/password |
| GET | `/start_exam/<exam_id>` | Launch exam (gated) |
| POST | `/submit_exam/<exam_id>` | Submit answers |
| GET | `/my_result/<exam_id>` | View own result |
| POST | `/detect_cheating` | **AI proctoring frame** → verdict, persists escalated violations |
| POST | `/log_violation` | Client-event violation (tab switch, devtools, print, terminate) |

### 11.4 Health

| Method | Route | Purpose |
|---|---|---|
| GET | `/proctor_health` | Face + object detector readiness |

### 11.5 Representative JSON Contract — `/detect_cheating`

Request: `{ image: <dataURL>, exam_id, session }`. Response:

```json
{
  "ok": true,
  "status": "secure | warning | violation | unclear",
  "violations": [{ "code": "phone", "label": "...", "severity": 3, "confidence": 0.9 }],
  "warnings":   [{ "code": "gaze_down", "label": "...", "confidence": 0.6, "progress": 0.7 }],
  "attention": "center|left|right|down|up",
  "face_count": 1,
  "head_pose": { "yaw": 3.1, "pitch": -5.0, "roll": 1.2 },
  "quality": { "brightness": 120.0, "sharpness": 88.0, ... },
  "spoof_risk": 0.0,
  "identity_match": 0.71,
  "integrity": 96.5
}
```

---

## 12. Security Features

OEMS layers many independent controls so that defeating one does not defeat the system.

### 12.1 Identity & Authentication

- **Two-factor student login** — password (Werkzeug PBKDF2) **plus** ArcFace face match on *every* login.
- **Active-liveness** (blink/turn) defeats printed photos and static screens.
- **Duplicate-face prevention** blocks one face from enrolling on two accounts.
- **Continuous identity** during the exam catches mid-exam person-swaps.
- **Server-authoritative everything** — the client cannot forge a pass.

### 12.2 Exam Environment Lockdown

- **Secure browser (Electron kiosk):** fullscreen, always-on-top, frameless; navigation allow-listing; ~20 blocked shortcuts; new-window denial; force-quit blocked until safe submit; unique request-header signature the server can require (`secure_any` / `secure_campus` modes).
- **Campus-only enforcement:** `secure_campus` exams require the client IP (via `X-Forwarded-For`) to match a configured `CAMPUS_IP_RANGES` prefix.
- **Client-event proctoring (in-page JS):** detects tab switch / minimize (`visibilitychange` + debounced `blur`), print attempts, possible DevTools (window-size delta, debounced), blocks cheat keyboard shortcuts, and re-enforces fullscreen.
- **Violation budget:** **5** violations (`maxWarnings`) trigger an automatic `force_terminate` + auto-submit. The count is reflected from the **server-recorded** total so it cannot be lowered by tampering.

### 12.3 AI Proctoring Integrity

- Multi-signal detection (multi-face, gaze, phone, book, device, identity, spoof) with **per-signal confidence** and **temporal escalation** (rise/decay/trigger/cooldown) to minimize false positives.
- **Evidence capture:** JPEG snapshots of offending frames are stored and shown to admins; payloads are size-capped (≤600 KB) and best-effort (never block logging).
- **Fail-open safety:** a server hiccup in `/detect_cheating` returns `secure` rather than wrongly terminating an exam.

### 12.4 Data & Application Security

- **Password hashing** with Werkzeug PBKDF2 (`generate_password_hash`).
- **Parameterized SQL everywhere** — every query uses bound parameters (`%s`), preventing SQL injection.
- **Secret hygiene:** `SECRET_KEY`, DB password, and SMTP app-password come from a gitignored `.env`; the app **refuses to start** without `SECRET_KEY`. The `.gitignore` excludes `.env`, logs, evidence images, model weights, DB dumps, and biometric artifacts.
- **HTML escaping** of user-influenced content in emails (`html.escape`) to avoid injection in HTML mail.
- **Rate limiting** on OTP endpoints (HTTP 429).
- **Single-attempt enforcement:** a student cannot start or submit the same exam twice (checked against `results`).
- **Branch-scoped authorization:** admins only see and act on their own branch (or `ALL`), with explicit 403 on cross-branch result access.

---

## 13. Project Workflow

### 13.1 Administrator Lifecycle

```
Admin login → add students (single / bulk CSV, welcome email)
           → create exam (cohort, type, timing, browser mode, AI toggle)
           → add MCQ/MSQ/theory questions
           → publish exam  → cohort gets "new exam" email
           → monitor violation_logs during exam
           → review results / release-hold / disqualify / re-evaluate
```

### 13.2 Student Exam Lifecycle

```
Student login (password) → face verify (register first time, else match)
        → dashboard (published exams for their cohort)
        → start exam (timing + single-attempt + browser-mode + campus gates)
        → gatekeeper: camera + engine readiness checks
        → answer questions (shuffled); proctoring runs every 1.2–2.5 s
        → submit (or auto-submit on time-up / 5 violations)
        → "submitted" page (objective score shown; theory pending)
```

### 13.3 Automated Post-Exam Pipeline

When the exam window closes, an in-process daemon thread (`schedule_exam_end_processor`, with a 30-second grace period) runs the **only** place grading happens:

```
1. Force-submit students who started but never submitted (have answers, no result).
   (Students who never attempted are ignored as absent.)
2. If the exam has theory questions → TF-IDF plagiarism across all submissions:
      similarity ≥ PLAGIARISM_THRESHOLD (70%) → status = Hold + hold email (NO grading)
3. Remaining (clean) students → SBERT theory grading (objective already scored):
      all theory scored → total computed → status = Evaluated → result PDF emailed.
      any answer un-gradable → status = Pending (retry later).
```

Admins can re-trigger this manually (`/trigger_exam_evaluation`), release a held result (re-grades and emails), re-evaluate a clean result, or disqualify a cheater.

### 13.4 Grading Logic

- **MCQ:** exact match of the chosen option → full marks, else 0.
- **MSQ:** the sorted set of chosen options must equal the sorted stored set exactly → full marks (no partial credit).
- **Theory (SBERT):** a **quality guard** first rejects too-short answers, keyword-stuffing, and repetition (score 0 with feedback). Otherwise the answer and the model answer are embedded with `all-MiniLM-L6-v2`; cosine similarity, scaled by a length factor, maps to marks (rounded to the nearest half-mark), with similarity-tiered feedback. SBERT encoding is mutex-locked for thread safety during bulk grading.

---

## 14. Screenshots & Evidence

The repository ships sample **proctoring evidence snapshots** under `backend/static/evidence/` (gitignored in production because they contain student images). Their filenames encode the audit context — `v_<exam>_<student>_<timestamp>_<code>.jpg`:

| File | Decoded meaning |
|---|---|
| `v_3_10_..._phone.jpg` | Exam 3, student 10 — **mobile phone** detected |
| `v_3_10_..._gazeup.jpg` | Exam 3, student 10 — **looking up / away** |
| `v_3_10_..._tabswitch.jpg` | Exam 3, student 10 — **tab switch** client event |

The admin **violation logs** page renders these snapshots beside each record, along with a severity-weighted **per-student risk ranking** so reviewers can triage the highest-risk candidates first. (UI screenshots of the dashboards, exam runner, and result PDF can be inserted here from a live run; the live exam runner displays a real-time integrity bar, a violation counter `n/5`, and an AI status indicator.)

---

## 15. Challenges & Solutions

| # | Challenge | Solution Implemented |
|---|---|---|
| 1 | **Flood of false proctoring violations** from the legacy Haar-cascade approach (lighting-sensitive, drops faces on head turns, double-counts profiles as "multiple people"). | Replaced with InsightFace (RetinaFace) detection + a **temporal confidence layer**: per-code rise/decay/trigger/cooldown so only persistent signals escalate. Critical signals rise fast; gaze decays fast. |
| 2 | **Unreliable face login** with the old raw-pixel (96×96 grayscale) "embedding" — sometimes worked, sometimes failed under lighting/pose changes. | Switched to **ArcFace 512-d** embeddings (lighting/pose robust) and averaged multiple clean frames for a stable identity vector. |
| 3 | **Photo / replay spoofing** of the camera. | **Active-liveness** challenges (blink + head-turn) tracked in server-side session, plus passive anti-spoof heuristics (texture variance, saturation spread, FFT moiré energy). |
| 4 | **Account / face sharing.** | **Duplicate-face scan** at enrolment rejects a face already on another account (cosine ≥ 0.55). |
| 5 | **Mid-exam person swap.** | **Identity continuity:** every proctoring frame carries an ArcFace embedding compared to the enrolled vector. |
| 6 | **Fake "gaze" from bounding-box centre** in the old engine. | **Real 3-D head-pose** via `cv2.solvePnP` against a canonical 5-point model, with a robust geometric fallback. |
| 7 | **Blaming students for dark/blurry frames.** | **Quality gating** (brightness + sharpness) returns "can't assess" instead of a false "no face". |
| 8 | **Thread-safety during bulk grading** (multiple students evaluated concurrently). | A **mutex lock** around SBERT encoding; a **connection pool** prevents DB connection bursts; evaluation threads are staggered (0.5 s). |
| 9 | **Keyword-stuffing / repetition gaming** of the semantic grader. | A pre-SBERT **answer quality guard** rejects too-short answers, dominant-word repetition, and pure keyword lists. |
| 10 | **Grading before all submissions are in** (unfair early plagiarism checks). | All grading is deferred to a **single exam-end processor** so every candidate is checked together. |
| 11 | **Schema drift across deployments.** | **Lazy, migration-safe** column/index creation (`ensure_student_face_schema`, `ensure_violation_schema`) at runtime. |
| 12 | **Reading the real client IP** behind a proxy for campus enforcement. | Honour `X-Forwarded-For` (set by Nginx) when matching `CAMPUS_IP_RANGES`. |
| 13 | **Server hiccup terminating an exam unfairly.** | **Fail-open** proctoring: any error returns `secure`. |

---

## 16. Future Scope

- **Live human invigilation** — optional real-time video/audio streaming to a human proctor alongside the AI layer.
- **Question banks with per-student sampling** — randomly draw N questions from a larger pool per candidate (currently only order is shuffled).
- **Horizontal scalability** — replace in-process daemon-thread scheduling with a distributed task queue (e.g., Celery + Redis) and run multiple Flask workers behind a load balancer; move proctoring sessions to a shared store.
- **WebSocket / streaming proctoring** — push verdicts over a persistent socket instead of per-frame HTTP POSTs.
- **Mobile-native secure client** — Android/iOS lockdown apps.
- **Configurable per-exam thresholds** — let admins tune proctoring strictness and the violation budget per exam from the UI.
- **Analytics dashboard** — trends across exams (integrity distributions, common violation types, cohort comparisons).
- **Audio-based detection** — flag conversation/voices via on-device audio analysis.
- **GDPR/consent tooling** — explicit consent capture, retention windows, and one-click biometric deletion.

---

## 17. Conclusion

OEMS demonstrates that a single, self-hosted, and transparent codebase can deliver **trustworthy online examinations** by treating integrity as a layered, server-authoritative concern. It unifies five normally-separate concerns — biometric authentication, environment lockdown, live AI proctoring, automated grading, and plagiarism detection — into one coherent workflow, and it does so with a deliberate engineering focus on **fairness**: real ArcFace identity rather than brittle raw pixels, true 3-D head-pose rather than a bounding-box hack, and temporal confidence accumulation rather than per-frame accusations, so honest candidates are not punished for normal behaviour.

The architecture is intentionally modular (a thin Flask orchestrator over self-contained CV/ML engines and a normalized MySQL schema), which makes each layer independently testable and replaceable. The system is production-aware — connection pooling, lazy schema migrations, asynchronous email, fail-open proctoring, secret hygiene, an optional Nginx front, and a kiosk client — while remaining understandable as a final-year project. Its design choices, documented throughout this report, are defensible from first principles, and the clearly-scoped future work (distributed scheduling, live invigilation, question banks) charts a credible path from a strong prototype to a deployable institutional service.
