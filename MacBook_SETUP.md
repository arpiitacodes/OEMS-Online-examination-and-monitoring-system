# OEMS — Online Exam Management System
## Complete macOS (MacBook) Setup & Installation Guide

> A step-by-step guide to set up and run the **OEMS Exam System** on a fresh macOS machine (Intel or Apple Silicon) — for both end users (administrators/proctors) and developers.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture & Technology Stack](#2-architecture--technology-stack)
3. [System Requirements](#3-system-requirements)
4. [Required Software & Dependencies](#4-required-software--dependencies)
5. [Getting the Code (Git Clone / Download)](#5-getting-the-code-git-clone--download)
6. [Python Virtual Environment & Backend Dependencies](#6-python-virtual-environment--backend-dependencies)
7. [MySQL Installation & Database Setup](#7-mysql-installation--database-setup)
8. [Environment Variable Setup (.env)](#8-environment-variable-setup-env)
9. [Creating the First Admin Account](#9-creating-the-first-admin-account)
10. [Face Verification & Camera Permissions](#10-face-verification--camera-permissions)
11. [Running the Backend (Flask)](#11-running-the-backend-flask)
12. [Running the Secure Browser (Electron)](#12-running-the-secure-browser-electron)
13. [Default URLs & Access Instructions](#13-default-urls--access-instructions)
14. [Email / SMTP Configuration](#14-email--smtp-configuration)
15. [Build & Production Deployment](#15-build--production-deployment)
16. [Project Structure Overview](#16-project-structure-overview)
17. [Troubleshooting](#17-troubleshooting)
18. [Common Errors & Fixes](#18-common-errors--fixes)
19. [FAQ](#19-faq)
20. [Security Recommendations](#20-security-recommendations)
21. [Update & Maintenance](#21-update--maintenance)

---

## 1. Project Overview

**OEMS (Online Exam Management System)** is a secure, proctored online examination platform built for educational institutions. Administrators create exams (MCQ/MSQ or theory), manage students, and review results; students take exams through a locked-down secure browser with **biometric face verification** and **AI-based proctoring**.

> 💡 This project was originally developed and tested on **macOS**, so the Mac path is the most native one — including **CoreML acceleration** for face detection on Apple Silicon and a macOS Electron build target.

### Key Features

- **Admin portal** — create/publish exams, add questions (MCQ, MSQ, theory), manage students (single + bulk CSV import), view results, and review violation logs.
- **Student portal** — password login followed by **mandatory face verification** on every login.
- **Face authentication** — real ArcFace 512-d identity embeddings via InsightFace (`buffalo_l` model pack) with active liveness checks (blink / head-turn).
- **AI proctoring** — camera-based face & gaze monitoring, multiple-face detection, and object detection (YOLOv8) during exams.
- **Secure exam browser** — an Electron-based kiosk browser (SEB-like) that blocks copy/paste, navigation away, dev tools, and force-quit.
- **AI answer evaluation** — descriptive answers scored with SBERT semantic similarity; built-in plagiarism detection.
- **Email automation** — welcome emails with credentials and OTP verification via Gmail SMTP.
- **PDF result generation** — server-side result sheets via ReportLab.
- **Browser/network gating** — exams can require the secure browser and/or a campus IP range.

---

## 2. Architecture & Technology Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.14, Flask 3.1, Werkzeug 3.1 |
| **Database** | MySQL (via `mysql-connector-python` with connection pooling) |
| **Secure Browser** | Electron 28 (Node.js), Axios |
| **Face Recognition** | InsightFace (ArcFace `buffalo_l`), ONNX Runtime (CoreML on Apple Silicon), OpenCV |
| **AI Proctoring** | Ultralytics YOLOv8 (`yolov8n.pt`), OpenCV Haar cascades |
| **AI Evaluation** | Sentence-Transformers (SBERT `all-MiniLM-L6-v2`), scikit-learn, PyTorch |
| **PDF** | ReportLab |
| **Email** | Gmail SMTP (smtplib) |
| **Reverse Proxy (optional)** | Nginx |
| **Templating** | Jinja2 (Flask `templates/`) |

The system runs as **two separate processes**:
1. The **Flask backend** (web UI + all APIs) on `http://127.0.0.1:5000`.
2. The **Electron secure browser** (optional; required only for secure-mode exams), which loads the backend over `http://localhost`.

> On **Apple Silicon (M1/M2/M3)**, the face engine automatically uses the **CoreML execution provider** when available (see `face_engine.py`), which speeds up detection. It falls back to CPU otherwise.

---

## 3. System Requirements

### Minimum

| Resource | Requirement |
|----------|-------------|
| OS | macOS 12 (Monterey) or newer |
| Chip | Intel or Apple Silicon (M1/M2/M3) — both supported |
| RAM | **8 GB** minimum |
| Disk | **6 GB** free (Python deps + ML models can total ~4–5 GB) |
| Camera | Built-in FaceTime camera or external webcam |
| Network | Internet on first run (downloads Python packages + ~280 MB InsightFace model) |

### Recommended

- **16 GB RAM** — PyTorch, OpenCV, InsightFace, and YOLO loaded together are memory-hungry.
- Apple Silicon for CoreML-accelerated face detection.
- SSD storage (standard on modern Macs) for fast model loading.

---

## 4. Required Software & Dependencies

### 4.1 Xcode Command Line Tools

Required for compilers/headers used when building Python wheels.

```bash
xcode-select --install
```

### 4.2 Homebrew

The package manager for macOS. Install from <https://brew.sh>:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

> On Apple Silicon, Homebrew installs to `/opt/homebrew`; on Intel, `/usr/local`. Follow the post-install instructions Homebrew prints to add it to your `PATH`.

### 4.3 Python 3.14

> The project's virtual environment was created with **Python 3.14.3** (Homebrew: `python@3.14`). Use Python **3.11+** at minimum; 3.14.x matches the pinned dependency versions exactly.

```bash
brew install python@3.14
python3 --version    # confirm 3.14.x (or 3.11+)
```

### 4.4 Git

Usually present via Xcode CLT. If needed:

```bash
brew install git
git --version
```

### 4.5 MySQL 8.x

```bash
brew install mysql
```

See [Section 7](#7-mysql-installation--database-setup) for setup.

### 4.6 Node.js 18+ LTS (only for the Secure Browser)

```bash
brew install node
node --version
npm --version
```

### 4.7 Nginx (optional — production reverse proxy only)

```bash
brew install nginx
```

See [Section 15](#15-build--production-deployment).

---

## 5. Getting the Code (Git Clone / Download)

### Option A — Git clone (recommended)

```bash
cd ~/Projects        # or any folder you like; create it with: mkdir -p ~/Projects
git clone <YOUR_REPOSITORY_URL> exam-system
cd exam-system
```

> Replace `<YOUR_REPOSITORY_URL>` with your actual Git remote URL.

### Option B — ZIP download

1. On the repository page, click **Code → Download ZIP**.
2. Extract it (e.g. to `~/Projects/exam-system`).
3. Open Terminal in that folder.

### What you should see

```
exam-system/
├── backend/          # Flask app + ML engines + templates
├── secure-browser/   # Electron secure exam browser
├── logs/             # Violation logs (gitignored)
├── oems_nginx.conf   # Sample Nginx config (macOS paths)
└── .gitignore
```

> **Note:** `backend/venv/`, `backend/.env`, `node_modules/`, model weight files (`*.pt`), and `logs/` are **gitignored**. You recreate these locally during setup.

---

## 6. Python Virtual Environment & Backend Dependencies

All backend commands run from the `backend/` folder.

```bash
cd ~/Projects/exam-system/backend
```

### 6.1 Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

When active, your prompt shows `(venv)`. To deactivate later: `deactivate`.

### 6.2 Upgrade pip and install dependencies

```bash
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

This installs (pinned versions — see [requirements.txt](backend/requirements.txt)):

- **Web:** `flask==3.1.3`, `werkzeug==3.1.7`, `flask-cors==6.0.2`
- **DB:** `mysql-connector-python==9.6.0`
- **Config:** `python-dotenv==1.2.2`
- **PDF:** `reportlab==4.4.10`
- **Email:** `email-validator==2.3.0`
- **Vision/Proctoring:** `opencv-contrib-python==4.13.0.92`, `numpy==2.4.3`, `ultralytics==8.4.26`
- **Face recognition:** `insightface==1.0.1`, `onnxruntime==1.26.0`
- **AI evaluation:** `sentence-transformers==5.3.0`, `scikit-learn==1.8.0`, `torch==2.11.0`, `torchvision==0.26.0`

> ⏳ **This step is large (several GB) and slow on first run** (PyTorch + OpenCV + ONNX). Be patient and keep a stable connection.

> 🍎 **Apple Silicon note:** PyTorch and ONNX Runtime install native `arm64` wheels automatically. The face engine prefers `CoreMLExecutionProvider` when present (`onnxruntime`'s CoreML backend), falling back to CPU.

### 6.3 ML model files (auto-downloaded)

- **InsightFace `buffalo_l` (~280 MB)** — auto-downloads to `~/.insightface/models/` on the **first** face-verification request. That first request is slow.
- **YOLOv8 weights (`yolov8n.pt`)** — already shipped in `backend/yolov8n.pt`. If missing, Ultralytics auto-downloads on first use.
- **SBERT `all-MiniLM-L6-v2`** — auto-downloads via `sentence-transformers` on first AI evaluation.

---

## 7. MySQL Installation & Database Setup

### 7.1 Start MySQL

```bash
brew services start mysql      # starts MySQL now and on login
# (one-off, no auto-start: mysql.server start)
```

Secure the install and set a root password (recommended):

```bash
mysql_secure_installation
```

> Note the password you set — it goes in `.env` as `DB_PASS`.

### 7.2 Create the database

The app connects to a database named **`exam_system`** (configurable via `DB_NAME`):

```bash
mysql -u root -p
```

```sql
CREATE DATABASE exam_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE exam_system;
```

### 7.3 Create the schema

> ⚠️ **Important:** The application does **not** auto-create the core tables — it expects them to already exist. (It only *adds* face-recognition columns to `students` automatically on first login.) If you have an existing database dump (`.sql` file), import that instead (see [Section 7.5](#75-importing-an-existing-database-dump)). Otherwise, create the schema below.

Run this SQL inside the `exam_system` database. It's reconstructed from the app's queries and matches all column names/types OEMS uses.

```sql
-- ============================================================
-- OEMS — Core Schema
-- ============================================================

-- Admins (institution staff / proctors)
CREATE TABLE admins (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    admin_id  VARCHAR(50)  NOT NULL UNIQUE,
    name      VARCHAR(150) NOT NULL,
    password  VARCHAR(255) NOT NULL,          -- Werkzeug PBKDF2 hash
    branch    VARCHAR(50)  NOT NULL           -- e.g. 'SCSE', 'SOM', or 'ALL'
);

-- Students
CREATE TABLE students (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(150) NOT NULL,
    admission_no  VARCHAR(50)  NOT NULL UNIQUE,
    program       VARCHAR(50),                -- BCA, MCA, BTECH, MTECH, BBA, MBA
    branch        VARCHAR(50),                -- SCSE, SOM
    semester      VARCHAR(10),                -- I, II, ... VIII
    email         VARCHAR(150),
    password      VARCHAR(255) NOT NULL        -- Werkzeug PBKDF2 hash
    -- Face columns below are added AUTOMATICALLY by the app on first login:
    -- face_embedding_v2    MEDIUMBLOB NULL
    -- face_registered      TINYINT(1) NOT NULL DEFAULT 0
    -- face_registered_at   DATETIME NULL
);

-- Exams
CREATE TABLE exams (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(255) NOT NULL,
    exam_type     VARCHAR(20)  NOT NULL,       -- 'mcq' or 'theory'
    total_marks   INT          NOT NULL,
    program       VARCHAR(50),
    branch        VARCHAR(50),
    semester      VARCHAR(10),
    start_time    DATETIME     NOT NULL,
    duration      INT          NOT NULL,       -- minutes
    status        VARCHAR(20)  DEFAULT 'draft',-- 'draft' or 'publish'
    browser_mode  VARCHAR(30)  DEFAULT 'any',  -- 'any' | 'secure_any' | 'secure_campus'
    ai_proctoring TINYINT(1)   DEFAULT 0       -- 0 = off, 1 = on
);

-- Questions
CREATE TABLE questions (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    exam_id        INT NOT NULL,
    question_text  TEXT NOT NULL,
    question_type  VARCHAR(20) NOT NULL,       -- 'mcq', 'msq', or 'theory'
    optionA        VARCHAR(500),
    optionB        VARCHAR(500),
    optionC        VARCHAR(500),
    optionD        VARCHAR(500),
    correct_answer VARCHAR(255),               -- e.g. 'A', or 'A,C' for MSQ
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
    score       FLOAT NULL,                    -- NULL = pending AI evaluation
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
    violation_type VARCHAR(100) NOT NULL,      -- e.g. 'force_terminate', 'multiple_faces'
    details        TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (exam_id)    REFERENCES exams(id)    ON DELETE CASCADE
);
```

> The three face columns (`face_embedding_v2`, `face_registered`, `face_registered_at`) are added to `students` **automatically** by `ensure_student_face_schema()` the first time a student logs in. You do **not** need to add them manually.

### 7.4 Verify the schema

```sql
USE exam_system;
SHOW TABLES;
-- Expected: admins, answers, exam_violations, exams, questions, results, students
```

### 7.5 Importing an existing database dump

If you were given a `.sql` dump (e.g. `exam_system.sql`):

```bash
mysql -u root -p exam_system < ~/path/to/exam_system.sql
```

This restores all tables and data in one step (skip the manual schema creation).

---

## 8. Environment Variable Setup (.env)

The backend reads configuration from `backend/.env` via `python-dotenv`. A template is at [backend/.env.example](backend/.env.example).

### 8.1 Create your `.env`

From `backend/`:

```bash
cp .env.example .env
```

Then edit `backend/.env`:

```ini
# Flask session signing key (REQUIRED — app refuses to start without it)
SECRET_KEY=replace-with-a-random-64-char-hex-string

# MySQL database
DB_HOST=localhost
DB_USER=root
DB_PASS=your-mysql-root-password
DB_NAME=exam_system

# Gmail SMTP for welcome / OTP emails (REQUIRED — app refuses to start without these)
OEMS_EMAIL=your-email@gmail.com
OEMS_EMAIL_PASSWORD=your-16-char-google-app-password

# Misc
GRPC_DNS_RESOLVER=native
CAMPUS_IP_RANGES=10.0.0           # comma-separated IP prefixes allowed for campus-only exams
SBERT_MODEL=all-MiniLM-L6-v2
PLAGIARISM_THRESHOLD=70
```

### 8.2 Generate a secure `SECRET_KEY`

With the venv active:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output into `SECRET_KEY`.

### 8.3 Required vs optional variables

| Variable | Required? | Notes |
|----------|-----------|-------|
| `SECRET_KEY` | ✅ **Yes** | App raises `RuntimeError` at startup if missing. |
| `DB_PASS` | ✅ **Yes** | Your MySQL password (no default). |
| `DB_HOST` / `DB_USER` / `DB_NAME` | Optional | Default to `localhost` / `root` / `exam_system`. |
| `OEMS_EMAIL` | ✅ **Yes** | App raises `RuntimeError` if missing. |
| `OEMS_EMAIL_PASSWORD` | ✅ **Yes** | Gmail **App Password** (16 chars). |
| `CAMPUS_IP_RANGES` | Optional | Needed only for `secure_campus` exams. |
| `SBERT_MODEL` | Optional | Default `all-MiniLM-L6-v2`. |
| `PLAGIARISM_THRESHOLD` | Optional | Default `70` (%). |

### 8.4 Optional face-recognition tuning

Safe defaults exist in code; override only if needed (uncomment in `.env`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `FACE_MATCH_THRESHOLD` | `0.42` | Cosine cutoff for "same person" (higher = stricter). |
| `FACE_DUPLICATE_THRESHOLD` | `0.55` | Blocks one face being registered on two accounts. |
| `FACE_LIVENESS_CHALLENGE` | `any` | `any` \| `blink` \| `turn`. |
| `FACE_VERIFY_FRAMES` | `4` | Clean frames averaged per login. |
| `FACE_REGISTER_FRAMES` | `6` | Clean frames averaged on first registration. |
| `FACE_MODEL_PACK` | `buffalo_l` | InsightFace model pack. |
| `FACE_AUTH_WINDOW_SECONDS` | `600` | How long the face step stays valid after the password step. |

> 🔒 **Never commit `.env`.** It is gitignored and holds your DB password and email app password.

---

## 9. Creating the First Admin Account

There is **no admin signup** and **no automatic admin seeding**. Insert the first admin directly into the database with a properly **hashed** password (Werkzeug PBKDF2 — the app verifies with `check_password_hash`).

### 9.1 Generate a password hash

With the venv active, from `backend/`:

```bash
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YourAdminPassword123'))"
```

Copy the full hash it prints.

### 9.2 Insert the admin row

```bash
mysql -u root -p
```

```sql
USE exam_system;
INSERT INTO admins (admin_id, name, password, branch)
VALUES ('admin01', 'Head Proctor', 'PASTE_THE_HASH_HERE', 'ALL');
```

- `admin_id` — the login ID (e.g. `admin01`).
- `branch` — use `'ALL'` for a super-admin who sees every branch, or a specific branch (`'SCSE'` / `'SOM'`) to scope the admin.

Log in at **`/admin_login`** with `admin_id` = `admin01` and the plaintext password you hashed.

> Students are created **from the admin portal** ("Add Student", single or bulk CSV) — their passwords are auto-generated, hashed, and emailed. You do not insert students by hand.

---

## 10. Face Verification & Camera Permissions

Face verification is **mandatory on every student login**; AI proctoring is optional per-exam. Both require camera access.

### 10.1 How it works

1. Student logs in with **admission number + password**.
2. The server creates a short-lived pending-auth session and redirects to **`/student_face_verify`**.
3. The browser streams ~3–4 webcam frames/second to the server.
4. **First time** → the student **registers** their face (6 clean frames averaged into one ArcFace embedding, stored as a `MEDIUMBLOB` in `students.face_embedding_v2`).
5. **Subsequent logins** → the student is **verified** (4 frames) against the stored embedding, with an active-liveness challenge (blink or head-turn).
6. On success, the full student session is granted.

### 10.2 Camera permissions on macOS

macOS gates camera access at the OS level — **per application**.

- **Browser (Chrome/Safari/Edge):** When prompted, click **Allow**. To change later: **System Settings → Privacy & Security → Camera** → enable your browser.
- **Electron secure browser:** The first time it accesses the camera, macOS prompts to allow **camera access for the app**. Approve it. If you missed the prompt: **System Settings → Privacy & Security → Camera** → enable the app (it may appear as "Electron" during development).
- Close any other app holding the camera (Zoom, Teams, Photo Booth, OBS).

> If the camera light is on but frames are black, another app is using it, or the app was denied in Privacy settings — toggle it off/on there and relaunch.

### 10.3 First-run model download

The first face request downloads the **InsightFace `buffalo_l`** pack (~280 MB) to `~/.insightface/models/`. This is slow and **requires internet**; later requests are fast and offline.

> 🍎 On Apple Silicon, `onnxruntime` uses **CoreML** for detection when available (auto-detected in `face_engine.py`), giving noticeably faster face detection.

### 10.4 Tips for reliable verification

- Good, even lighting on the face; avoid strong backlight.
- One person only in frame, centred in the circle, not too far.
- Hold still during capture (a sharpness check rejects motion blur).

---

## 11. Running the Backend (Flask)

From `backend/` with the venv active and `.env` configured:

```bash
cd ~/Projects/exam-system/backend
source venv/bin/activate
python app.py
```

You should see:

```
[OEMS] SBERT model: all-MiniLM-L6-v2
 * Running on http://127.0.0.1:5000
```

The app binds to **`127.0.0.1:5000`** with `debug=False` (see the `if __name__ == "__main__"` block in [backend/app.py](backend/app.py)).

> **First startup is slow** while PyTorch, OpenCV, ONNX Runtime, and SBERT initialise. Wait for the "Running on" line before opening a browser.

Stop the server with **Ctrl + C**.

---

## 12. Running the Secure Browser (Electron)

The Electron **secure exam browser** is needed only for exams whose `browser_mode` is `secure_any` or `secure_campus`. For `any`-mode exams, a normal browser works.

### 12.1 Install Node dependencies

In a **second** Terminal tab:

```bash
cd ~/Projects/exam-system/secure-browser
npm install
```

Installs `electron@^28` and `axios` (see [secure-browser/package.json](secure-browser/package.json)).

### 12.2 Start the secure browser

> ⚠️ Start the **Flask backend first** — the browser loads `http://localhost`.

```bash
npm start
```

This launches a **full-screen kiosk** Electron window that:

- Loads a splash screen, then your OEMS site at `http://localhost`.
- Sends an `X-OEMS-Secure-Browser: ElectronV1` header so the backend recognises it.
- Blocks copy/paste/cut, right-click, dev tools, navigation to non-local URLs, new windows, and force-quit.
- Shows an **"← Exit"** button on non-exam pages (hidden during an exam) requiring confirmation.

> The browser points at `http://localhost` (port 80). If you run Flask on port 5000 **without** Nginx, either:
> - put Nginx in front (see [Section 15](#15-build--production-deployment)) so `http://localhost` proxies to Flask, **or**
> - change `CONFIG.baseUrl` in [secure-browser/main.js](secure-browser/main.js) to `http://localhost:5000` for local testing.

### 12.3 Build a macOS app (`.app` / `.dmg`)

The `build` script is already configured for macOS:

```bash
npm run build      # runs: electron-builder --mac
```

You'll need `electron-builder` installed:

```bash
npm install --save-dev electron-builder
npm run build
```

Output appears in `secure-browser/dist/` (a `.app` bundle and/or `.dmg`).

> 🍎 **Code signing & notarization:** An unsigned `.app` will be blocked by Gatekeeper on other Macs. For distribution, sign and notarize with an Apple Developer ID (configure `electron-builder` accordingly). For your own machine, right-click the app → **Open** to bypass Gatekeeper once.

---

## 13. Default URLs & Access Instructions

With the backend on `http://127.0.0.1:5000` (or `http://localhost` behind Nginx):

| URL | Who | Purpose |
|-----|-----|---------|
| `/` | Everyone | Landing / home page. |
| `/student_login` (POST) | Student | Submit admission no + password → redirects to face verification. |
| `/student_face_verify` | Student | Face registration / verification step. |
| `/student` | Student | Student dashboard (available exams). |
| `/start_exam/<exam_id>` | Student | Take an exam. |
| `/admin_login` | Admin | Admin login (admin_id + password). |
| `/admin` | Admin | Admin dashboard (exam & stats overview). |
| `/student_manager` | Admin | Manage students. |
| `/add_student` | Admin | Add students (single or bulk CSV). |
| `/create_exam` | Admin | Create a new exam. |
| `/add_question/<exam_id>` | Admin | Add questions to an exam. |
| `/results` | Admin | Results summary. |
| `/violation_logs` | Admin | Proctoring violation logs. |
| `/plagiarism/<exam_id>` | Admin | Plagiarism report. |
| `/logout` | Both | Clears the session. |

**First-time access flow:**
1. Start backend → open `http://127.0.0.1:5000/admin_login`.
2. Log in with the admin you created in [Section 9](#9-creating-the-first-admin-account).
3. Create an exam, add questions, add students (welcome emails go out automatically).
4. Students log in at `/` → password → face verify → dashboard → exam.

---

## 14. Email / SMTP Configuration

OEMS sends **welcome emails** (with student credentials) and **OTP verification** via **Gmail SMTP** (`smtp.gmail.com:587`, STARTTLS). See `EMAIL_CONFIG` in [backend/app.py](backend/app.py).

### 14.1 Create a Gmail App Password

1. Use a Gmail account with **2-Step Verification enabled**.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Generate a new **App Password** (16 characters, no spaces).
4. Put the Gmail address in `OEMS_EMAIL` and the app password in `OEMS_EMAIL_PASSWORD` in `.env`.

> ⚠️ Use an **App Password**, *not* your normal Gmail password. The app **will not start** if `OEMS_EMAIL` / `OEMS_EMAIL_PASSWORD` are missing.

### 14.2 Email settings (in code)

| Setting | Value |
|---------|-------|
| SMTP server | `smtp.gmail.com` |
| SMTP port | `587` (STARTTLS) |
| OTP expiry | 10 minutes |
| Max OTP attempts | 3 |
| Rate limit window | 5 minutes |

---

## 15. Build & Production Deployment

> Flask's built-in server (`app.run`) is for development. For production, run behind a WSGI server and a reverse proxy.

### 15.1 Use a production WSGI server (Gunicorn)

On macOS/Unix, **Gunicorn** is a solid choice:

```bash
pip install gunicorn
gunicorn --workers 3 --bind 127.0.0.1:5000 app:app
```

(Run from `backend/` so Python imports `app.py`.)

> ⚠️ The ML models (PyTorch/InsightFace/YOLO) load **per worker** and use a lot of RAM. Start with **1–3 workers** and watch memory. Consider `--timeout 120` for long evaluation requests.

### 15.2 Put Nginx in front (recommended)

A sample config is provided at [oems_nginx.conf](oems_nginx.conf) — it was written **for macOS**:

- Listens on **port 80** and proxies all requests to `http://127.0.0.1:5000`.
- Serves `/static/` directly (faster).
- Sets `client_max_body_size 10M` (camera frame uploads).
- Forwards `X-Real-IP` / `X-Forwarded-For` so campus-IP gating works.

Setup:

1. Find your Nginx servers directory:
   - **Apple Silicon:** `/opt/homebrew/etc/nginx/servers/`
   - **Intel:** `/usr/local/etc/nginx/servers/`
2. Copy the config there as `oems.conf`:
   ```bash
   # Apple Silicon example
   cp oems_nginx.conf /opt/homebrew/etc/nginx/servers/oems.conf
   ```
3. **Edit the paths** in that file:
   - Update the `location /static/` `alias` to your real path, e.g. `/Users/<you>/Projects/exam-system/backend/static/`.
   - Fix the `favicon.ico` `alias` placeholder (`/YOUR_PROJECT_PATH/...`).
4. Start/reload Nginx:
   ```bash
   brew services start nginx     # or: nginx -s reload
   ```
5. Now `http://localhost` proxies to Flask, and the Electron secure browser (which loads `http://localhost`) works without code changes.

> ⚠️ The committed `oems_nginx.conf` contains an example absolute path under `/Users/...`. **Always review and adjust paths** for your machine. (In fresh clones this file is gitignored because it may contain server/IP details; the repo copy is a reference.)

### 15.3 Production hardening checklist

- Generate a **fresh** `SECRET_KEY`; never reuse the example.
- Use a **dedicated, least-privilege MySQL user** (not `root`) for `DB_USER`/`DB_PASS`.
- Serve over **HTTPS** (terminate TLS at Nginx with a real certificate).
- Keep `debug=False` (already the default).
- Restrict camera-frame upload size at the proxy (already `10M`).
- Use `brew services` (launchd) so Gunicorn/Nginx/MySQL restart on reboot.

---

## 16. Project Structure Overview

```
exam-system/
├── backend/
│   ├── app.py                 # Main Flask app: routes, DB, proctoring, AI eval, email, PDF
│   ├── face_engine.py         # ArcFace (InsightFace) face detection + recognition engine
│   ├── requirements.txt       # Pinned Python dependencies
│   ├── .env.example           # Environment template (copy to .env)
│   ├── .env                   # YOUR secrets (gitignored — create locally)
│   ├── yolov8n.pt             # YOLOv8 nano weights for object detection (proctoring)
│   ├── venv/                  # Python virtual environment (gitignored — create locally)
│   └── templates/             # Jinja2 HTML templates
│       ├── home.html              # Landing page
│       ├── admin_login.html       # Admin login
│       ├── admin_dashboard.html   # Admin dashboard
│       ├── student_manager.html   # Manage students
│       ├── add_student.html       # Add/import students
│       ├── create_exam.html       # Create exam
│       ├── add_question.html      # Add questions
│       ├── edit_question.html
│       ├── student_dashboard.html # Student dashboard
│       ├── student_face_verify.html  # Face registration / verification UI
│       ├── start_exam.html        # Exam-taking page (proctoring runs here)
│       ├── results_summary.html   # Results overview
│       ├── result_details.html
│       ├── student_result.html
│       ├── plagiarism.html        # Plagiarism report
│       ├── violation_logs.html    # Proctoring violations
│       ├── secure_browser.html
│       ├── campus_only.html
│       └── ...
├── secure-browser/            # Electron secure exam browser (kiosk mode)
│   ├── main.js                # Electron main process (kiosk, shortcut blocking, IPC)
│   ├── preload.js             # Secure bridge (contextIsolation) to the page
│   ├── splash.html            # Splash screen
│   ├── package.json           # Electron + axios; start/build (--mac) scripts
│   └── node_modules/          # (gitignored — run npm install)
├── logs/
│   └── oems_violations.log    # Secure-browser violation log (gitignored)
├── oems_nginx.conf            # Sample Nginx reverse-proxy config (macOS paths)
└── .gitignore
```

---

## 17. Troubleshooting

### Backend won't start

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RuntimeError: SECRET_KEY environment variable set nahi hai!` | `SECRET_KEY` missing in `.env` | Add it (see [8.2](#82-generate-a-secure-secret_key)). |
| `RuntimeError: OEMS_EMAIL aur OEMS_EMAIL_PASSWORD .env mein set nahi hain!` | Email vars missing | Set `OEMS_EMAIL` / `OEMS_EMAIL_PASSWORD`. |
| `mysql.connector ... Access denied for user 'root'` | Wrong `DB_PASS` | Fix the MySQL password in `.env`. |
| `Unknown database 'exam_system'` | DB not created | Run the `CREATE DATABASE` step ([7.2](#72-create-the-database)). |
| `Table 'exam_system.admins' doesn't exist` | Schema not created | Run the schema SQL ([7.3](#73-create-the-schema)). |
| `Can't connect to MySQL server` | MySQL not running | `brew services start mysql`. |

### Camera / face issues

| Symptom | Fix |
|---------|-----|
| "Face engine unavailable on server." | InsightFace failed to load — check the backend console for the load error; confirm the model downloaded and RAM is available. |
| Camera not detected / black frames | Allow camera in **System Settings → Privacy & Security → Camera** for your browser/Electron; close other apps using the camera. |
| "Bring your face into the circle" / "Move closer" | Improve lighting, centre and approach the camera. |
| First face login extremely slow | Normal — `buffalo_l` (~280 MB) is downloading; requires internet. |

### Secure browser issues

| Symptom | Fix |
|---------|-----|
| Electron loads a blank/error page | Backend not running or `http://localhost` not served. Start Flask + Nginx, or set `CONFIG.baseUrl` to `http://localhost:5000` in `main.js`. |
| macOS won't open the built `.app` | Gatekeeper — right-click → **Open** once, or sign/notarize for distribution. |
| "Secure browser required" page | The exam's `browser_mode` is `secure_any`/`secure_campus`; use the Electron browser, not Safari/Chrome. |
| Campus-only block | The exam is `secure_campus` and your IP prefix isn't in `CAMPUS_IP_RANGES`. Adjust `.env` or connect to the campus network. |

---

## 18. Common Errors & Fixes

| Error | Likely Cause | Fix |
|-------|--------------|-----|
| `xcrun: error: invalid active developer path` | Xcode CLT missing/broken | `xcode-select --install`. |
| `command not found: brew` | Homebrew not on PATH | Follow Homebrew's post-install `PATH` instructions. |
| `pip` install of `torch`/`insightface` fails | Network timeout / old pip | Upgrade pip; retry on stable internet. |
| `ModuleNotFoundError: No module named 'flask'` | venv not activated | `source venv/bin/activate` before `python app.py`. |
| `Address already in use` / port 5000 busy | Another process on 5000 (e.g. macOS AirPlay Receiver also uses 5000!) | Stop the other process, or disable **System Settings → General → AirDrop & Handoff → AirPlay Receiver**, or change the port in `app.py`. |
| `smtplib ... 535 Authentication failed` | Using Gmail password, not App Password | Generate a Gmail **App Password** ([14.1](#141-create-a-gmail-app-password)). |
| Welcome/OTP email never arrives | Wrong creds / 2FA off | Verify App Password; check spam; confirm 2-Step Verification on the sender account. |
| `Library not loaded` / dyld errors on import | Mixed arch (Rosetta vs native) | Use a native `arm64` Python on Apple Silicon; recreate the venv. |
| "You have already attempted this exam" | A `results` row exists for that student+exam | Expected — one attempt per exam. Remove the result row only to intentionally allow a retake. |

> 🍎 **macOS port-5000 gotcha:** macOS's **AirPlay Receiver** also listens on port 5000. If Flask reports the port busy, disable AirPlay Receiver in System Settings, or change the Flask port (and update Nginx/Electron `baseUrl` to match).

---

## 19. FAQ

**Q: Do students need the Electron secure browser?**
A: Only for exams set to `secure_any` or `secure_campus`. Exams set to `any` work in a normal browser.

**Q: Where is the face data stored?**
A: As a raw float32 ArcFace embedding (2048 bytes) in `students.face_embedding_v2` (a MySQL BLOB). No face images are stored.

**Q: Does it use the Apple Neural Engine / GPU?**
A: Face detection uses **CoreML** (`CoreMLExecutionProvider`) on Apple Silicon when available, falling back to CPU. PyTorch/SBERT run on CPU by default.

**Q: How do I add the very first admin?**
A: Insert one row into the `admins` table with a Werkzeug-hashed password ([Section 9](#9-creating-the-first-admin-account)). There is no signup page.

**Q: How are student passwords created?**
A: Auto-generated when an admin adds a student, hashed with Werkzeug, and emailed via the welcome email. Admins can resend credentials.

**Q: How are theory answers graded?**
A: With SBERT (`all-MiniLM-L6-v2`) semantic-similarity scoring, plus plagiarism detection (threshold `PLAGIARISM_THRESHOLD`, default 70%). Admins trigger AI evaluation and release results.

**Q: What does `branch = 'ALL'` mean for an admin?**
A: A super-admin who can see and manage all branches. A specific branch (e.g. `SCSE`) scopes the admin to that branch only.

**Q: Can I change the port?**
A: Yes — edit `app.run(host='127.0.0.1', port=5000, ...)` at the bottom of `app.py`, and update the Electron `baseUrl` / Nginx `proxy_pass` accordingly. (Useful to avoid the AirPlay port-5000 clash.)

**Q: First request after start is slow — is something wrong?**
A: No. ML models initialise lazily on first use (and `buffalo_l` may download). It's fast afterward.

---

## 20. Security Recommendations

1. **Never commit `.env`, `*.sql`, `*.pem`, `*.key`, logs, or model/biometric files** — `.gitignore` already excludes them; keep it that way.
2. **Rotate the `SECRET_KEY`** if it was ever exposed; use a unique one per deployment.
3. **Use a dedicated MySQL user** with only the needed privileges on `exam_system` — not `root` — in production.
4. **Use a Gmail App Password**, stored only in `.env`; never in code.
5. **Serve over HTTPS** in production (TLS at Nginx) so credentials and camera frames aren't sent in clear text.
6. **Protect the violation logs** (`logs/oems_violations.log`) — they may contain student/violation data and are gitignored for a reason.
7. **Keep dependencies patched** — periodically review `requirements.txt` and Electron for security updates.
8. **Sign & notarize** the distributed `.app` so students can trust and run it without Gatekeeper workarounds.
9. **Back up the database** regularly (it holds results, students, and face embeddings).
10. Use the secure browser **on managed machines** for high-stakes exams; it complements, but does not replace, a live proctor.

---

## 21. Update & Maintenance

### Pull the latest code

```bash
cd ~/Projects/exam-system
git pull
```

### Update backend dependencies

```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt --upgrade
```

> If you change pinned versions, re-test face verification, proctoring, and AI evaluation — these ML libraries are version-sensitive.

### Update the secure browser

```bash
cd secure-browser
npm install
```

### Database migrations

- The app **auto-adds** face columns to `students` on first login (no action needed).
- For any other schema change, back up first:
  ```bash
  mysqldump -u root -p exam_system > exam_system_backup.sql
  ```

### Manage services (Homebrew)

```bash
brew services list                 # see MySQL/Nginx status
brew services restart mysql
brew services restart nginx
```

### Log rotation

- `logs/oems_violations.log` grows over time. Archive/rotate periodically and keep backups secure.

### Refresh ML models

- To force a re-download of the InsightFace model, delete `~/.insightface/models/` and restart.

---

*Generated for the OEMS Exam System. Verify paths, ports, and credentials for your specific deployment before going live.*
