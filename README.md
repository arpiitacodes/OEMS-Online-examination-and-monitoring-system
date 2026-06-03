# OEMS — Online Examination & Monitoring System

> A secure, AI-proctored online examination platform with ArcFace biometric login, real-time monitoring, automated theory grading, plagiarism detection, and a kiosk-mode secure browser.

**OEMS (Online Examination & Monitoring System)** is a full-stack exam delivery and proctoring platform built for institutions that need to run trustworthy online assessments. It combines password + **face-recognition** authentication, real-time **AI monitoring/proctoring** (face / phone / gaze detection), **SBERT-based** semantic grading of theory answers, **TF-IDF plagiarism** screening, and an **Electron kiosk browser** that locks the student into the exam environment.

---

## Table of Contents

- [Project Overview](#project-overview)
- [User Roles](#user-roles)
- [Features](#features)
- [Technology Stack](#technology-stack)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Installation & Setup](#installation--setup)
- [Environment Variable Configuration](#environment-variable-configuration)
- [Database Setup](#database-setup)
- [Face Verification Workflow](#face-verification-workflow)
- [Authentication Flow](#authentication-flow)
- [Usage Instructions](#usage-instructions)
- [Deployment](#deployment)
- [Security Considerations](#security-considerations)
- [Troubleshooting](#troubleshooting)
- [Screenshots](#screenshots)
- [Contributing](#contributing)
- [License](#license)

---

## Project Overview

OEMS lets administrators create and publish exams (MCQ, MSQ, and theory), enroll students individually or in bulk via CSV, and deliver assessments with layered integrity controls. Students authenticate with a password **and** a live face check on every login. During the exam, optional AI proctoring watches the webcam for missing/multiple faces, mobile phones, and off-screen gaze, logging every violation.

Grading is fully automated: objective questions (MCQ/MSQ) are scored on submission, while theory answers are evaluated **after the exam window closes** using a Sentence-BERT semantic model — but only after a class-wide plagiarism check clears the submission. Results are compiled into a styled PDF report and emailed to each student automatically.

The platform ships with an **Electron-based secure browser** (kiosk mode) that disables copy/paste, blocks navigation away from the exam, and intercepts dangerous keyboard shortcuts — and a `secure_campus` mode that additionally restricts exams to on-campus IP ranges.

---

## User Roles

OEMS has **two roles**, each with its own login and access scope (enforced by a `@login_required(role)` decorator on every protected route):

| Role | Logs in via | Capabilities |
|------|-------------|--------------|
| **Admin** (staff / proctor) | `/admin_login` — Admin ID + password | Create & publish exams, author questions, enroll students (single / bulk CSV), monitor proctoring violation logs, run plagiarism reports, and manage held results (**Release** / **Re-evaluate** / **Disqualify**). Access is **branch-scoped** — an admin sees only their own branch unless their branch is `ALL`. |
| **Student** | `/` — Admission No + password, then **live face verification** | Take published exams during their window (in the secure browser when required), submit answers, and view/download their own result reports. Students only ever see their own data. |

> The first admin account is seeded directly in the database (Werkzeug-hashed password); see the setup guides. Students are created by admins, who can auto-send welcome emails with credentials.

---

## Features

### Exam Management
- Create MCQ, MSQ (multi-select), and theory exams with per-question marks.
- Draft / publish workflow with scheduled start time and duration.
- Per-exam **browser policy**: `any`, `secure_any` (kiosk required), or `secure_campus` (kiosk + campus IP required).
- Per-exam **AI proctoring** toggle.
- Branch-scoped admin access — admins see only their branch (or `ALL`).

### Student Management
- Single add or **bulk CSV import** of students.
- Automated welcome emails with login credentials.
- OTP-verified email and password updates.
- Werkzeug PBKDF2-hashed passwords.

### Authentication & Biometrics
- Two-factor login: **password → live face verification** on every sign-in.
- **ArcFace 512-d** identity embeddings via InsightFace (`buffalo_l`).
- **Active liveness** challenge (blink / head-turn) to defeat photo spoofing.
- **Duplicate-face detection** prevents one face being registered to two accounts.
- Server-authoritative capture (the browser cannot fake frames or decisions).

### AI Proctoring
- Multi-cascade face detection (frontal + alt2 + profile) with CLAHE preprocessing and IoU deduplication.
- **Mobile phone detection** via YOLOv8 (`yolov8n`).
- Gaze/head-pose monitoring (looking left/right/up/down).
- "Face not visible", "Eyes not visible", and "Multiple people detected" violations.
- All violations logged to the database and the secure-browser log file.

### Grading & Results
- Instant objective scoring for MCQ/MSQ on submission.
- **SBERT semantic grading** for theory answers (`all-MiniLM-L6-v2`) with anti-gaming guards (keyword-stuffing & repetition detection).
- Strict post-exam pipeline: force-submit stragglers → plagiarism check → grade → email.
- **TF-IDF cosine plagiarism** detection; submissions over the threshold are placed on **Hold** for admin review.
- Admin actions: **Release**, **Re-evaluate**, **Disqualify**.
- Auto-generated, professionally styled **PDF result reports** (ReportLab) emailed to students.

### Secure Browser (Electron)
- Fullscreen **kiosk mode**, always-on-top, no window frame.
- Blocks copy / cut / paste / right-click and text selection.
- Blocks ~20 keyboard shortcuts (devtools, new tab/window, alt-tab, force-quit, etc.).
- Prevents navigation to non-local URLs and blocks new windows.
- Sends a signed `X-OEMS-Secure-Browser` header so the backend can enforce kiosk-only exams.
- Tamper-resistant exit (confirmation dialog + force-quit blocker) with session logging.

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.14, Flask 3.1, Werkzeug |
| **Database** | MySQL 8.x (connection pooling via `mysql-connector-python`) |
| **Face Recognition** | InsightFace (ArcFace `buffalo_l`), ONNX Runtime |
| **AI Proctoring** | OpenCV (Haar cascades + CLAHE), Ultralytics YOLOv8 |
| **Theory Grading** | Sentence-Transformers (SBERT `all-MiniLM-L6-v2`), scikit-learn, PyTorch |
| **Plagiarism** | scikit-learn TF-IDF + cosine similarity |
| **PDF Reports** | ReportLab |
| **Email / OTP** | Gmail SMTP (App Password), `smtplib` |
| **Secure Browser** | Electron 28, Node.js 18+, Axios |
| **Frontend** | Server-rendered Jinja2 templates, vanilla JS |
| **Reverse Proxy** | Nginx (optional, production) |

> Heavy ML models (ArcFace `buffalo_l` ~280 MB, YOLOv8n, SBERT) **auto-download on first use** to `~/.insightface/models` and the respective cache directories — they are **not** committed to the repo.

---

## Architecture Overview

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│   Secure Browser (Electron) │         │      Standard Web Browser     │
│   kiosk · header signature  │         │     (admin & open exams)      │
└──────────────┬──────────────┘         └───────────────┬──────────────┘
               │  HTTP(S) (X-OEMS-Secure-Browser)        │
               └────────────────────┬────────────────────┘
                                    ▼
                        ┌──────────────────────┐
                        │   Flask App (app.py)  │
                        │  ─ Auth & sessions    │
                        │  ─ Exam delivery      │
                        │  ─ Proctoring API     │
                        │  ─ Grading pipeline   │
                        │  ─ Email / OTP / PDF  │
                        └──────────┬───────────┘
              ┌────────────────────┼────────────────────────┐
              ▼                    ▼                         ▼
   ┌──────────────────┐  ┌──────────────────┐   ┌──────────────────────┐
   │ face_engine.py   │  │  ML Inference     │   │   MySQL (pooled)      │
   │ ArcFace + live-  │  │ YOLOv8 · OpenCV   │   │ students · exams ·    │
   │ ness (InsightF.) │  │ SBERT · TF-IDF    │   │ questions · answers · │
   └──────────────────┘  └──────────────────┘   │ results · violations  │
                                                 └──────────────────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │  Gmail SMTP       │
                          │ welcome · OTP ·   │
                          │ result PDF email  │
                          └──────────────────┘
```

**Key modules**

- **`backend/app.py`** — the Flask application: routes, authentication, exam flow, proctoring endpoints, the post-exam grading pipeline, email/OTP, and PDF generation.
- **`backend/face_engine.py`** — a self-contained, thread-safe ArcFace engine (detection, recognition, liveness signals, embedding (de)serialization). Pure functions over NumPy arrays; no Flask/DB coupling.
- **`secure-browser/`** — the Electron kiosk browser (`main.js`, `preload.js`, `splash.html`).

---

## Project Structure

```
exam-system/
├── backend/
│   ├── app.py                  # Main Flask application (~2,800 lines)
│   ├── face_engine.py          # ArcFace recognition + liveness engine
│   ├── requirements.txt        # Pinned Python dependencies
│   ├── .env.example            # Environment template (copy to .env)
│   └── templates/              # Jinja2 templates
│       ├── home.html               # Landing / login
│       ├── admin_dashboard.html    # Admin home
│       ├── student_dashboard.html  # Student home
│       ├── student_face_verify.html# Live face capture UI
│       ├── add_student.html        # Single + bulk CSV enroll
│       ├── create_exam.html        # Exam creation
│       ├── add_question.html        # Question authoring
│       ├── edit_question.html
│       ├── questions.html
│       ├── start_exam.html         # Exam runner + proctoring JS
│       ├── exam_submitted.html
│       ├── results_summary.html
│       ├── result_details.html
│       ├── student_result.html
│       ├── plagiarism.html
│       ├── violation_logs.html
│       ├── secure_browser.html
│       ├── campus_only.html
│       ├── edit_profile.html
│       └── student_manager.html
├── secure-browser/
│   ├── main.js                 # Electron main process (kiosk logic)
│   ├── preload.js              # Secure IPC bridge
│   ├── splash.html             # Launch screen
│   └── package.json
├── oems_nginx.conf             # Nginx reverse-proxy config (gitignored)
├── WINDOWS_SETUP.md            # Full Windows setup guide
├── MacBook_SETUP.md            # Full macOS setup guide
├── .gitignore
└── README.md
```

> `yolov8n.pt`, `.env`, `logs/`, `oems_nginx.conf`, and all biometric/database artifacts are **gitignored** and never committed (see [Security Considerations](#security-considerations)).

---

## Installation & Setup

> 📘 For exhaustive, OS-specific, step-by-step instructions (software prerequisites, MySQL install, building the secure browser, Nginx, first admin account), see **[WINDOWS_SETUP.md](WINDOWS_SETUP.md)** and **[MacBook_SETUP.md](MacBook_SETUP.md)**. The summary below covers the common path.

### Prerequisites

- **Python 3.14** (64-bit)
- **MySQL Server 8.x**
- **Node.js 18+ LTS** (only required for the secure browser)
- **Git**
- On Windows: **Microsoft C++ Build Tools** (for compiling ML deps)
- On macOS: **Xcode Command Line Tools** + **Homebrew**

### 1. Clone the repository

```bash
git clone https://github.com/anshumanks2004/OEMS-ExamSystem.git
cd OEMS-ExamSystem
```

### 2. Create a virtual environment & install backend dependencies

```bash
cd backend
python -m venv venv

# macOS / Linux
source venv/bin/activate
# Windows
venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

> First run downloads the ArcFace, YOLOv8, and SBERT models automatically (a few hundred MB total). This is a one-time download.

### 3. Configure environment variables

```bash
cp .env.example .env
# then edit .env with your real values (see next section)
```

### 4. Set up the database

Create the MySQL database and schema (see [Database Setup](#database-setup)).

### 5. Run the backend

```bash
python app.py
```

The server starts on **http://127.0.0.1:5000**.

### 6. (Optional) Build & run the secure browser

```bash
cd ../secure-browser
npm install
npm start        # launches the kiosk browser
# npm run build  # produces a packaged app (electron-builder)
```

---

## Environment Variable Configuration

Copy `backend/.env.example` to `backend/.env` and fill in real values. **Never commit `.env`** — it is gitignored.

### Required

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask session signing key. The app **refuses to start** without it. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. |
| `DB_HOST` | MySQL host (default `localhost`). |
| `DB_USER` | MySQL user (default `root`). |
| `DB_PASS` | MySQL password. |
| `DB_NAME` | Database name (default `exam_system`). |
| `OEMS_EMAIL` | Gmail address used to send welcome/OTP/result emails. **Required.** |
| `OEMS_EMAIL_PASSWORD` | Gmail **App Password** (16 chars, not your account password). **Required.** |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMPUS_IP_RANGES` | `10.104.242` | Comma-separated IP prefixes allowed for `secure_campus` exams. |
| `SBERT_MODEL` | `all-MiniLM-L6-v2` | Sentence-BERT model for theory grading. |
| `PLAGIARISM_THRESHOLD` | `70` | % similarity at/above which a result is placed on Hold. |
| `GRPC_DNS_RESOLVER` | `native` | gRPC resolver hint (avoids DNS warnings). |

### Face-recognition tuning (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `FACE_MATCH_THRESHOLD` | `0.42` | Cosine cutoff for "same person" (higher = stricter). |
| `FACE_DUPLICATE_THRESHOLD` | `0.55` | Blocks registering one face on two accounts. |
| `FACE_LIVENESS_CHALLENGE` | `any` | `any` \| `blink` \| `turn`. |
| `FACE_VERIFY_FRAMES` | `4` | Clean frames averaged per login. |
| `FACE_REGISTER_FRAMES` | `6` | Clean frames averaged on first enrollment. |
| `FACE_MODEL_PACK` | `buffalo_l` | InsightFace model pack. |
| `FACE_MIN_SHARPNESS` | `35.0` | Minimum face-crop sharpness to accept a frame. |
| `FACE_AUTH_WINDOW_SECONDS` | `600` | How long the face step stays valid after the password step. |

---

## Database Setup

OEMS uses **MySQL 8.x**. The application does **not** auto-create core tables — it expects them to exist (it only *adds* the face-recognition columns to `students` automatically on first login).

### 1. Create the database

```sql
CREATE DATABASE exam_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. Create the schema

```sql
USE exam_system;

-- Admins (institution staff / proctors)
CREATE TABLE admins (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    admin_id  VARCHAR(50)  NOT NULL UNIQUE,
    name      VARCHAR(150) NOT NULL,
    password  VARCHAR(255) NOT NULL,          -- Werkzeug PBKDF2 hash
    branch    VARCHAR(50)  NOT NULL           -- e.g. 'SCSE', 'SOM', or 'ALL'
);

-- Students (face_* columns auto-added by the app on first login)
CREATE TABLE students (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(150) NOT NULL,
    admission_no  VARCHAR(50)  NOT NULL UNIQUE,
    program       VARCHAR(50),
    branch        VARCHAR(50),
    semester      VARCHAR(10),
    email         VARCHAR(150),
    password      VARCHAR(255) NOT NULL        -- Werkzeug PBKDF2 hash
);

-- Exams
CREATE TABLE exams (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    exam_type     VARCHAR(20)  NOT NULL,
    total_marks   INT          NOT NULL,
    program       VARCHAR(50),
    branch        VARCHAR(50),
    semester      VARCHAR(10),
    start_time    DATETIME     NOT NULL,
    duration      INT          NOT NULL,       -- minutes
    status        VARCHAR(20)  DEFAULT 'draft',-- 'draft' | 'publish'
    browser_mode  VARCHAR(30)  DEFAULT 'any',  -- 'any' | 'secure_any' | 'secure_campus'
    ai_proctoring TINYINT(1)   DEFAULT 0
);

-- Questions
CREATE TABLE questions (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    exam_id        INT NOT NULL,
    question_text  TEXT NOT NULL,
    question_type  VARCHAR(20) NOT NULL,       -- 'mcq' | 'msq' | 'theory'
    optionA        VARCHAR(500),
    optionB        VARCHAR(500),
    optionC        VARCHAR(500),
    optionD        VARCHAR(500),
    correct_answer VARCHAR(255),               -- 'A' or 'A,C' for MSQ
    marks          INT NOT NULL DEFAULT 1,
    FOREIGN KEY (exam_id) REFERENCES exams(id) ON DELETE CASCADE
);

-- Answers (one row per student per question)
CREATE TABLE answers (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    student_id  INT NOT NULL,
    exam_id     INT NOT NULL,
    question_id INT NOT NULL,
    answer      TEXT,
    score       FLOAT NULL,                    -- NULL = pending evaluation
    feedback    TEXT,
    FOREIGN KEY (student_id)  REFERENCES students(id)  ON DELETE CASCADE,
    FOREIGN KEY (exam_id)     REFERENCES exams(id)     ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

-- Results (one row per student per exam)
CREATE TABLE results (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    student_id        INT NOT NULL,
    exam_id           INT NOT NULL,
    total_score       FLOAT,
    submission_status VARCHAR(30),             -- Pending | Evaluated | Hold |
                                               -- AwaitingExamEnd | Disqualified | Released
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (exam_id)    REFERENCES exams(id)    ON DELETE CASCADE
);

-- Proctoring violations
CREATE TABLE exam_violations (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    student_id     INT NOT NULL,
    exam_id        INT NOT NULL,
    violation_type VARCHAR(100) NOT NULL,
    details        TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (exam_id)    REFERENCES exams(id)    ON DELETE CASCADE
);
```

The three face columns — `face_embedding_v2` (`MEDIUMBLOB`), `face_registered` (`TINYINT`), and `face_registered_at` (`DATETIME`) — are added to `students` **automatically** by `ensure_student_face_schema()` on the first student login. You do not need to add them manually.

> The first admin account must be inserted manually (with a Werkzeug-hashed password). See the setup guides' "Creating the First Admin Account" section for the exact snippet.

---

## Face Verification Workflow

OEMS uses **ArcFace 512-d embeddings** (InsightFace `buffalo_l`) for biometric identity. The browser streams ~3–4 webcam frames per second to the server; **the server is authoritative** for all detection, liveness, and match decisions — the client cannot spoof the outcome.

### Enrollment (first login)

1. After a successful password check, the student is redirected to the live face-capture page.
2. The server analyzes each incoming frame: it requires exactly **one** confident, sufficiently large, **sharp** face.
3. An **active-liveness challenge** must be satisfied — a **blink** or a **head-turn** — proving a live person (not a photo).
4. Once `FACE_REGISTER_FRAMES` clean frames pass, their embeddings are averaged and re-normalized into a stable identity vector.
5. A **duplicate-face check** scans all other accounts; if the new face matches an existing student above `FACE_DUPLICATE_THRESHOLD`, registration is **rejected** (anti account-sharing).
6. The averaged embedding is stored as raw float32 bytes in `students.face_embedding_v2`, and the student is asked to log in again.

### Verification (every subsequent login)

1. Password check → redirect to live face capture.
2. The server collects `FACE_VERIFY_FRAMES` clean frames + a passed liveness challenge.
3. The averaged probe embedding is compared to the stored embedding via **cosine similarity**.
4. If similarity ≥ `FACE_MATCH_THRESHOLD` (default `0.42`), the session is finalized and the student enters their dashboard. Otherwise the capture resets and the student retries.

> Legacy raw-pixel "embeddings" from older versions are intentionally **not** migrated — affected students simply re-enroll once with ArcFace on next login.

---

## Authentication Flow

### Student

```
/  ──(POST /student_login: admission_no + password)──►  password verified?
                                                        │
                                              ┌─────────┴─────────┐
                                              ▼                   ▼
                                        pending_auth set      "Invalid ❌"
                                              │
                                              ▼
                                  /student_face_verify  (live capture)
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                    not registered → enroll          registered → verify
                              │                               │
                              ▼                               ▼
                     "log in again"                  match ≥ threshold?
                                                              │
                                                        ┌─────┴─────┐
                                                        ▼           ▼
                                                  /student      retry
```

- Password is checked with `check_password_hash` (Werkzeug PBKDF2).
- Between the password and face steps, a short-lived `pending_student_auth` session token (valid for `FACE_AUTH_WINDOW_SECONDS`) gates access — it cannot be replayed after expiry.
- The full student session is only established **after** the face check passes.

### Admin

- `POST /admin_login` with `admin_id` + password (Werkzeug-hashed).
- Session carries the admin's `branch`; all admin views are **branch-scoped** unless the branch is `ALL`.

### OTP (email / password updates)

- `/send_otp`, `/verify_otp`, `/resend_otp` issue and verify a 6-digit OTP (`secrets`-based), with rate limiting, expiry (10 min), and a max-attempt cap.

Access control is enforced by a `@login_required(role)` decorator on every protected route.

---

## Usage Instructions

### Admin

1. Log in at `/admin_login`.
2. **Add students** — single entry or bulk CSV upload; optionally send welcome emails with credentials.
3. **Create an exam** — set program/branch/semester, start time, duration, browser policy (`any` / `secure_any` / `secure_campus`), and AI-proctoring toggle.
4. **Add questions** — MCQ, MSQ (multi-select), or theory, each with marks.
5. **Publish** the exam to make it visible to eligible students.
6. After the exam window closes, OEMS automatically force-submits stragglers, runs the plagiarism check, grades theory answers, and emails PDF results.
7. **Review** — view results, plagiarism reports, and violation logs. For held results: **Release**, **Re-evaluate**, or **Disqualify**.

### Student

1. Log in with admission number + password.
2. Complete the **live face verification** (enroll on first login, verify thereafter).
3. From the dashboard, start a published exam during its window.
4. Exams marked `secure_*` must be opened in the **secure browser**; `secure_campus` exams additionally require a campus IP.
5. Answer and submit. Objective scores are computed immediately; theory results arrive by email after the exam ends and plagiarism/grading complete.
6. View results and download the PDF report.

### Secure Browser

```bash
cd secure-browser
npm install
npm start
```

The kiosk browser launches fullscreen, signs requests with `X-OEMS-Secure-Browser`, and locks the student into the exam environment until they finish or exit (with confirmation).

---

## Deployment

The Flask app listens on **`127.0.0.1:5000`** by default (`app.run(...)` in [`backend/app.py`](backend/app.py)). For anything beyond local testing, run it behind a production WSGI server and an Nginx reverse proxy.

### Backend (production)

1. **Use a WSGI server** instead of the Flask dev server:
   ```bash
   # macOS / Linux
   pip install gunicorn
   gunicorn --workers 3 --bind 127.0.0.1:5000 --timeout 120 app:app

   # Windows
   pip install waitress
   waitress-serve --listen=127.0.0.1:5000 app:app
   ```
   > Note: ML models (ArcFace / YOLO / SBERT) load lazily per process and consume significant memory — size the worker count to your RAM.

2. **Put Nginx in front** as a reverse proxy. The repo ships a reference config, `oems_nginx.conf` (gitignored — adjust the `static`/project paths for your host). Key points it already handles:
   - Proxies all requests to `http://127.0.0.1:5000`.
   - Forwards `X-Real-IP` and `X-Forwarded-For` — **required** for the `secure_campus` IP check, which reads `X-Forwarded-For` to determine the real client IP.
   - Serves static files directly and raises `client_max_body_size` (for CSV/bulk uploads).
   - Generous proxy timeouts so exam submissions don't time out.

   ```bash
   # macOS (Homebrew) — drop the config into Nginx's servers dir:
   cp oems_nginx.conf /opt/homebrew/etc/nginx/servers/oems.conf   # Apple Silicon
   # or /usr/local/etc/nginx/servers/oems.conf                    # Intel
   nginx -t && nginx -s reload
   ```

3. **Enable HTTPS.** Terminate TLS at Nginx (e.g. via Let's Encrypt / Certbot). HTTPS is also required for the browser's `getUserMedia` webcam access in face verification and proctoring on any non-`localhost` host.

4. **Harden Flask sessions** for production: set `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, and a strong `SECRET_KEY`. Run with `debug=False` (already the default).

5. **Configure `CAMPUS_IP_RANGES`** in `.env` to your institution's real on-campus IP prefixes if you use `secure_campus` exams.

### Secure browser distribution

Package the Electron kiosk browser for distribution to exam machines:

```bash
cd secure-browser
npm install
npm run build        # electron-builder --mac (see package.json)
```

Set the target server URL in [`secure-browser/main.js`](secure-browser/main.js) (`CONFIG.baseUrl`) to point at your deployed backend before building. The packaged app is what students launch to sit `secure_any` / `secure_campus` exams.

### Operational notes

- **Database:** point `.env` at your production MySQL instance and ensure the schema exists (the app does not create core tables, only the face columns). Restrict DB network access and use a least-privilege user.
- **Logs:** `logs/` (proctoring/violation logs) may contain student data — keep it off version control (already gitignored) and secure it on disk.
- **Email:** the Gmail App Password must be valid in the deployed environment, or welcome/OTP/result emails will fail.

---

## Security Considerations

- **No secrets in the repo.** `SECRET_KEY`, DB credentials, and the Gmail App Password live only in `.env`, which is gitignored. The app refuses to start if `SECRET_KEY` or email credentials are missing.
- **Password hashing.** All admin and student passwords use Werkzeug PBKDF2 hashes — never stored in plaintext.
- **Server-authoritative biometrics.** Face detection, liveness, and match decisions happen server-side; the client only streams frames. Embeddings are stored as raw bytes, never exposed to the client.
- **Active liveness.** Blink / head-turn challenges defeat static-photo spoofing; duplicate-face detection blocks account sharing.
- **Two-factor login.** Password **and** live face match are both required on every student login, with a time-boxed pending-auth window.
- **Kiosk lockdown.** The secure browser blocks copy/paste, devtools, navigation, and force-quit; the backend can require it (and a campus IP) per exam.
- **Rate limiting.** OTP and sensitive endpoints are rate-limited with attempt caps and expiry.
- **Input handling.** Violation details and user inputs are length-capped and HTML-escaped where rendered.
- **Sensitive artifacts are gitignored.** Biometric data (`*.npy`, `embeddings/`, `faces/`), uploads, databases/dumps, ML weight files (`*.pt`, `*.onnx`, `*.h5`), logs (which may contain student/violation data), keys/certs, and the Nginx config are all excluded from version control.

> ⚠️ **Production hardening:** run behind HTTPS (terminate TLS at Nginx), set `SESSION_COOKIE_SECURE`/`HttpOnly`, run with a production WSGI server (e.g. gunicorn/waitress) rather than the Flask dev server, and restrict database/network access appropriately.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `SECRET_KEY environment variable set nahi hai!` on startup | `.env` is missing or `SECRET_KEY` is unset. Copy `.env.example` → `.env` and generate a key. |
| `OEMS_EMAIL aur OEMS_EMAIL_PASSWORD .env mein set nahi hain!` | Set `OEMS_EMAIL` and a Gmail **App Password** in `.env`. |
| Face engine unavailable / "Contact support" | InsightFace/ONNX Runtime failed to load or the model is still downloading. Check console logs; ensure `insightface` + `onnxruntime` installed and the first-run model download finished. |
| Camera not detected in browser | Grant webcam permission; HTTPS or `localhost` is required for `getUserMedia`. |
| MySQL connection errors | Verify `DB_HOST/USER/PASS/NAME`, that MySQL is running, and the `exam_system` schema exists. |
| Theory answers stuck "Pending" | SBERT/scikit-learn not installed or model load failed. Confirm `sentence-transformers` is installed; results stay pending (never wrongly zeroed) until the evaluator is available. |
| Phone detection not firing | YOLO weights (`yolov8n.pt`) failed to load — check the `[Phone Detection]` / `YOLO Load Error` console output. |
| Secure browser won't build | Ensure Node.js 18+ and run `npm install` inside `secure-browser/`. |
| First model run is slow | The ArcFace/YOLO/SBERT models download once (~hundreds of MB). Subsequent runs are fast. |

---

## Screenshots

> _Screenshots can be added here once captured (e.g. an `assets/` or `docs/screenshots/` folder)._

| View | Description |
|------|-------------|
| Admin Dashboard | Exam, student, and proctoring overview |
| Face Verification | Live capture with liveness challenge |
| Exam Runner | In-progress exam with proctoring active |
| Result Report | PDF / on-screen result with feedback |
| Violation Logs | Proctoring violations per student/exam |

---

## Contributing

Contributions are welcome!

1. **Fork** the repository and create a feature branch:
   ```bash
   git checkout -b feature/your-feature
   ```
2. Follow the existing code style and structure. Keep `face_engine.py` free of Flask/DB coupling.
3. **Never commit** secrets, `.env` files, ML weights, biometric data, databases, or logs (the `.gitignore` enforces this — keep it intact).
4. Test your changes locally against a development database.
5. Commit with a clear message and open a **Pull Request** describing the change and its rationale.

For larger changes, please open an issue first to discuss the approach.

---

## License

This project's **secure browser** component is published under the **MIT License** (see `secure-browser/package.json`). Unless a separate top-level `LICENSE` file specifies otherwise, the project is intended for educational and institutional use.

---

<div align="center">

**OEMS — Online Examination & Monitoring System**
Built by Anshuman Kumar Singh

</div>
