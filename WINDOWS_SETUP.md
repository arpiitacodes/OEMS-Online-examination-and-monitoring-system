# OEMS — Online Exam Management System
## Complete Windows Setup & Installation Guide

> A step-by-step guide to set up and run the **OEMS Exam System** on a fresh Windows 10/11 machine — for both end users (administrators/proctors) and developers.

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

**OEMS (Online Exam Management System)** is a secure, proctored online examination platform built for educational institutions. It allows administrators to create exams (MCQ/MSQ or theory), manage students, and monitor results, while students take exams through a locked-down secure browser with **biometric face verification** and **AI-based proctoring**.

### Key Features

- **Admin portal** — create/publish exams, add questions (MCQ, MSQ, theory), manage students (single + bulk CSV import), view results, and review violation logs.
- **Student portal** — password login followed by **mandatory face verification** on every login.
- **Face authentication** — real ArcFace 512-d identity embeddings via InsightFace (`buffalo_l` model pack) with active liveness checks (blink / head-turn).
- **AI proctoring** — camera-based face & gaze monitoring, multiple-face detection, and object detection (YOLOv8) during exams.
- **Secure exam browser** — an Electron-based kiosk browser (SEB-like) that blocks copy/paste, navigation away, dev tools, and force-quit.
- **AI answer evaluation** — descriptive answers scored with SBERT (Sentence-BERT) semantic similarity; built-in plagiarism detection.
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
| **Face Recognition** | InsightFace (ArcFace `buffalo_l`), ONNX Runtime, OpenCV |
| **AI Proctoring** | Ultralytics YOLOv8 (`yolov8n.pt`), OpenCV Haar cascades |
| **AI Evaluation** | Sentence-Transformers (SBERT `all-MiniLM-L6-v2`), scikit-learn, PyTorch |
| **PDF** | ReportLab |
| **Email** | Gmail SMTP (smtplib) |
| **Reverse Proxy (optional)** | Nginx |
| **Templating** | Jinja2 (Flask `templates/`) |

The system runs as **two separate processes**:
1. The **Flask backend** (serves the web UI + all APIs) on `http://127.0.0.1:5000`.
2. The **Electron secure browser** (optional, required only for secure-mode exams), which loads the backend over `http://localhost`.

---

## 3. System Requirements

### Minimum

| Resource | Requirement |
|----------|-------------|
| OS | Windows 10 (64-bit) or Windows 11 |
| CPU | Quad-core (the ML models are CPU-bound) |
| RAM | **8 GB** minimum |
| Disk | **6 GB** free (Python deps + ML models can total ~4–5 GB) |
| Camera | A working webcam (required for face verification & proctoring) |
| Network | Internet access on first run (to download Python packages and the ~280 MB InsightFace model) |

### Recommended

- **16 GB RAM** — PyTorch, OpenCV, InsightFace, and YOLO loaded together are memory-hungry.
- A modern multi-core CPU. (There is no CUDA/GPU requirement; inference runs on CPU via `CPUExecutionProvider`.)
- SSD storage for faster model loading.

---

## 4. Required Software & Dependencies

Install the following **before** setting up the project.

### 4.1 Python 3.14 (64-bit)

> The project's virtual environment was created with **Python 3.14.3**. Use Python **3.11+** at minimum; 3.14.x matches the pinned dependency versions exactly.

1. Download from <https://www.python.org/downloads/windows/>.
2. Run the installer and **CHECK** the box **"Add python.exe to PATH"** on the first screen.
3. Choose **"Customize installation"** → ensure **pip** and **"py launcher"** are selected.
4. Verify in a new terminal:
   ```powershell
   python --version
   pip --version
   ```

### 4.2 Git for Windows

- Download from <https://git-scm.com/download/win> and install with defaults.
- Verify:
  ```powershell
  git --version
  ```

### 4.3 MySQL Server 8.x

- Download the **MySQL Installer for Windows** from <https://dev.mysql.com/downloads/installer/>.
- See [Section 7](#7-mysql-installation--database-setup) for full setup.

### 4.4 Node.js 18+ LTS (only for the Secure Browser)

- Download from <https://nodejs.org/> (LTS) and install with defaults.
- Verify:
  ```powershell
  node --version
  npm --version
  ```

### 4.5 Microsoft C++ Build Tools (required)

InsightFace and some ML wheels may need to compile native code on Windows.

- Install **"Build Tools for Visual Studio"** from <https://visualstudio.microsoft.com/visual-cpp-build-tools/>.
- During install, select the **"Desktop development with C++"** workload.
- This avoids `Microsoft Visual C++ 14.0 or greater is required` errors.

### 4.6 Nginx (optional — production reverse proxy only)

- Download from <https://nginx.org/en/download.html>. See [Section 15](#15-build--production-deployment).

---

## 5. Getting the Code (Git Clone / Download)

### Option A — Git clone (recommended)

Open **PowerShell** (or Git Bash) and run:

```powershell
cd C:\Projects
git clone <YOUR_REPOSITORY_URL> exam-system
cd exam-system
```

> Replace `<YOUR_REPOSITORY_URL>` with your actual Git remote URL.

### Option B — ZIP download

1. On the repository page, click **Code → Download ZIP**.
2. Extract to `C:\Projects\exam-system`.
3. Open PowerShell in that folder.

### What you should see

```
exam-system\
├── backend\          # Flask app + ML engines + templates
├── secure-browser\   # Electron secure exam browser
├── logs\             # Violation logs (gitignored)
├── oems_nginx.conf   # Sample Nginx config
└── .gitignore
```

> **Note:** `backend\venv\`, `backend\.env`, `node_modules\`, model weight files (`*.pt`), and `logs\` are **gitignored**. You will recreate these locally during setup.

---

## 6. Python Virtual Environment & Backend Dependencies

All backend commands run from the `backend\` folder.

```powershell
cd C:\Projects\exam-system\backend
```

### 6.1 Create and activate a virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

> If PowerShell blocks activation with *"running scripts is disabled on this system"*, run once:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```
> Then re-run the activation command. (Or use `venv\Scripts\activate.bat` from `cmd.exe`.)

When active, your prompt shows `(venv)`.

### 6.2 Upgrade pip and install dependencies

```powershell
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

> ⏳ **This step is large (several GB) and slow on first run** (PyTorch + OpenCV + ONNX). Be patient and ensure a stable connection.

### 6.3 ML model files (auto-downloaded)

- **InsightFace `buffalo_l` (~280 MB)** — auto-downloads to `C:\Users\<you>\.insightface\models\` on the **first** face-verification request. The first request will be slow.
- **YOLOv8 weights (`yolov8n.pt`)** — already shipped in `backend\yolov8n.pt`. If missing, Ultralytics auto-downloads it on first use.
- **SBERT `all-MiniLM-L6-v2`** — auto-downloads via `sentence-transformers` on first AI evaluation.

---

## 7. MySQL Installation & Database Setup

### 7.1 Install MySQL Server

1. Run the **MySQL Installer**, choose **"Server only"** (or "Developer Default" to also get MySQL Workbench — recommended for beginners).
2. Configure:
   - **Authentication:** "Use Strong Password Encryption".
   - Set a **root password** and **remember it** — you will put it in `.env`.
   - Keep the default port **3306**.
   - Configure MySQL as a **Windows Service** that starts automatically.
3. Finish and verify the service is running (`services.msc` → "MySQL80" → Running).

### 7.2 Create the database

The application connects to a database named **`exam_system`** (configurable via `DB_NAME`). Open **MySQL Workbench** or the MySQL command-line client:

```powershell
mysql -u root -p
```

Then:

```sql
CREATE DATABASE exam_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE exam_system;
```

### 7.3 Create the schema

> ⚠️ **Important:** The application does **not** auto-create the core tables — it expects them to already exist. (It only *adds* face-recognition columns to `students` automatically on first login.) If you have an existing database dump (`.sql` file), import that instead (see [Section 7.5](#75-importing-an-existing-database-dump)). Otherwise, create the schema below.

Run the following SQL inside the `exam_system` database. This schema is reconstructed from the application's queries and matches all column names and types OEMS uses.

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

> The three face columns (`face_embedding_v2`, `face_registered`, `face_registered_at`) are added to `students` **automatically** by `ensure_student_face_schema()` the first time a student logs in. You do **not** need to add them manually — but it is harmless to do so.

### 7.4 Verify the schema

```sql
USE exam_system;
SHOW TABLES;
-- Expected: admins, answers, exam_violations, exams, questions, results, students
```

### 7.5 Importing an existing database dump

If you were given a `.sql` dump (e.g. `exam_system.sql`):

```powershell
mysql -u root -p exam_system < C:\path\to\exam_system.sql
```

This restores all tables and existing data in one step (skip the manual schema creation above).

---

## 8. Environment Variable Setup (.env)

The backend reads configuration from `backend\.env` using `python-dotenv`. A template is provided at [backend/.env.example](backend/.env.example).

### 8.1 Create your `.env`

From `backend\`:

```powershell
copy .env.example .env
```

Then open `backend\.env` in a text editor and fill in real values:

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

```powershell
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

These have safe defaults in code and only need to be set to override behaviour (uncomment in `.env`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `FACE_MATCH_THRESHOLD` | `0.42` | Cosine cutoff for "same person" (higher = stricter). |
| `FACE_DUPLICATE_THRESHOLD` | `0.55` | Blocks one face being registered on two accounts. |
| `FACE_LIVENESS_CHALLENGE` | `any` | `any` \| `blink` \| `turn`. |
| `FACE_VERIFY_FRAMES` | `4` | Clean frames averaged per login. |
| `FACE_REGISTER_FRAMES` | `6` | Clean frames averaged on first registration. |
| `FACE_MODEL_PACK` | `buffalo_l` | InsightFace model pack. |
| `FACE_AUTH_WINDOW_SECONDS` | `600` | How long the face step stays valid after the password step. |

> 🔒 **Never commit `.env`.** It is gitignored. It contains your DB password and email app password.

---

## 9. Creating the First Admin Account

There is **no self-service admin signup** and **no automatic admin seeding**. You must insert the first admin directly into the database with a properly **hashed** password (Werkzeug PBKDF2 — the app verifies with `check_password_hash`).

### 9.1 Generate a password hash

With the venv active, from `backend\`:

```powershell
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('YourAdminPassword123'))"
```

Copy the full hash string it prints.

### 9.2 Insert the admin row

In MySQL:

```sql
USE exam_system;
INSERT INTO admins (admin_id, name, password, branch)
VALUES ('admin01', 'Head Proctor', 'PASTE_THE_HASH_HERE', 'ALL');
```

- `admin_id` — the login ID (e.g. `admin01`).
- `branch` — use `'ALL'` for a super-admin who sees every branch, or a specific branch like `'SCSE'` / `'SOM'` to scope an admin to one branch.

You can now log in at **`/admin_login`** with `admin_id` = `admin01` and the plaintext password you hashed.

> Students are created **from the admin portal** ("Add Student", single or bulk CSV) — their passwords are auto-generated, hashed, and emailed. You do not insert students by hand.

---

## 10. Face Verification & Camera Permissions

Face verification is **mandatory on every student login** and AI proctoring is optional per-exam. Both require camera access.

### 10.1 How it works

1. Student logs in with **admission number + password**.
2. The server creates a short-lived pending-auth session and redirects to **`/student_face_verify`**.
3. The browser streams ~3–4 webcam frames/second to the server.
4. **First time** → the student **registers** their face (6 clean frames averaged into one ArcFace embedding, stored as a `MEDIUMBLOB` in `students.face_embedding_v2`).
5. **Subsequent logins** → the student is **verified** (4 frames) against the stored embedding, with an active-liveness challenge (blink or head-turn).
6. On success, the full student session is granted.

### 10.2 Camera permissions on Windows

- **Browser:** When prompted, click **Allow** for camera access. (In Chrome/Edge, check the camera icon in the address bar if you previously denied it.)
- **Windows privacy settings:** Go to **Settings → Privacy & security → Camera** and ensure:
  - **"Camera access"** is **On**.
  - **"Let apps access your camera"** is **On**.
  - **"Let desktop apps access your camera"** is **On** (required for the Electron secure browser).
- Close any other app (Zoom, Teams, OBS) that may be holding the camera.

### 10.3 First-run model download

The first face request downloads the **InsightFace `buffalo_l`** pack (~280 MB) to `C:\Users\<you>\.insightface\models\`. This request will be slow and **requires internet**. Subsequent requests are fast and offline.

### 10.4 Tips for reliable verification

- Good, even lighting on the face; avoid strong backlight.
- One person only in frame, centred in the circle, not too far.
- Hold still during capture (motion blur is rejected by a sharpness check).

---

## 11. Running the Backend (Flask)

From `backend\` with the venv active and `.env` configured:

```powershell
cd C:\Projects\exam-system\backend
.\venv\Scripts\Activate.ps1
python app.py
```

You should see startup logs like:

```
[OEMS] SBERT model: all-MiniLM-L6-v2
 * Running on http://127.0.0.1:5000
```

The app binds to **`127.0.0.1:5000`** with `debug=False` (see the `if __name__ == "__main__"` block in [backend/app.py](backend/app.py)).

> **First startup is slow** because PyTorch, OpenCV, ONNX Runtime, and the SBERT model initialise. Wait for the "Running on" line before opening a browser.

To stop the server: press **Ctrl + C** in the terminal.

---

## 12. Running the Secure Browser (Electron)

The Electron **secure exam browser** is only needed for exams whose `browser_mode` is `secure_any` or `secure_campus`. For `any`-mode exams, students can use a normal browser.

### 12.1 Install Node dependencies

In a **second** terminal:

```powershell
cd C:\Projects\exam-system\secure-browser
npm install
```

This installs `electron@^28` and `axios` (see [secure-browser/package.json](secure-browser/package.json)).

### 12.2 Start the secure browser

> ⚠️ Make sure the **Flask backend is already running** first — the browser loads `http://localhost`.

```powershell
npm start
```

This launches a **full-screen kiosk** Electron window that:

- Loads a splash screen, then your OEMS site at `http://localhost`.
- Sends a `X-OEMS-Secure-Browser: ElectronV1` header so the backend recognises it as the secure browser.
- Blocks copy/paste/cut, right-click, dev tools, navigation to non-local URLs, new windows, and force-quit.
- Shows an **"← Exit"** button on non-exam pages (hidden during an exam) that requires confirmation.

> The browser points at `http://localhost` (port 80). If you run Flask directly on port 5000 **without** Nginx, either:
> - put Nginx in front (see [Section 15](#15-build--production-deployment)) so `http://localhost` proxies to Flask, **or**
> - change `CONFIG.baseUrl` in [secure-browser/main.js](secure-browser/main.js) to `http://localhost:5000` for local testing.

### 12.3 Build a distributable (optional)

The build script targets macOS only (`electron-builder --mac`). To build a **Windows** installer, install `electron-builder` and target Windows:

```powershell
npm install --save-dev electron-builder
npx electron-builder --win
```

The output appears in `secure-browser\dist\`.

---

## 13. Default URLs & Access Instructions

With the backend running on `http://127.0.0.1:5000` (or `http://localhost` behind Nginx):

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

OEMS sends **welcome emails** (with student credentials) and **OTP verification** emails via **Gmail SMTP** (`smtp.gmail.com:587`, STARTTLS). See `EMAIL_CONFIG` in [backend/app.py](backend/app.py).

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

> Flask's built-in server (`app.run`) is for development. For production on Windows, run behind a WSGI server and a reverse proxy.

### 15.1 Use a production WSGI server (Waitress)

Flask's dev server is single-threaded and not hardened. On Windows, **Waitress** is the simplest production server:

```powershell
pip install waitress
waitress-serve --host=127.0.0.1 --port=5000 app:app
```

(For `app:app`, ensure you run this from `backend\` so Python imports `app.py`.)

### 15.2 Put Nginx in front (recommended)

A sample config is provided at [oems_nginx.conf](oems_nginx.conf). It:

- Listens on **port 80** and proxies all requests to `http://127.0.0.1:5000`.
- Serves `/static/` directly (faster).
- Sets `client_max_body_size 10M` (camera frame uploads).
- Forwards `X-Real-IP` / `X-Forwarded-For` so campus-IP gating works correctly.

On Windows:
1. Download Nginx for Windows and extract to `C:\nginx`.
2. Copy `oems_nginx.conf` into `C:\nginx\conf\` (or `include` it from `nginx.conf`).
3. **Edit the paths** in the config:
   - Change the `location /static/` `alias` to your real path, e.g. `C:/Projects/exam-system/backend/static/`.
   - Fix the `favicon.ico` `alias` placeholder (`/YOUR_PROJECT_PATH/...`).
4. Start Nginx:
   ```powershell
   cd C:\nginx
   .\nginx.exe
   ```
5. Now `http://localhost` proxies to Flask, and the Electron secure browser (which loads `http://localhost`) works without code changes.

> ⚠️ The committed `oems_nginx.conf` contains macOS/absolute paths and a hardcoded user path — **always review and adjust paths** before using it on Windows. (Note: this file is gitignored in fresh clones because it may contain server/IP details; the sample in the repo is for reference.)

### 15.3 Production hardening checklist

- Generate a **fresh** `SECRET_KEY`; never reuse the example.
- Use a **dedicated, least-privilege MySQL user** (not `root`) for `DB_USER`/`DB_PASS`.
- Run behind **HTTPS** (terminate TLS at Nginx with a real certificate).
- Keep `debug=False` (already the default).
- Restrict camera-frame upload size at the proxy (already `10M`).
- Run Flask/Waitress as a Windows service (e.g. via NSSM) so it restarts on reboot.

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
│   ├── package.json           # Electron + axios; start/build scripts
│   └── node_modules/          # (gitignored — run npm install)
├── logs/
│   └── oems_violations.log    # Secure-browser violation log (gitignored)
├── oems_nginx.conf            # Sample Nginx reverse-proxy config
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
| `Can't connect to MySQL server` | MySQL service stopped | Start "MySQL80" in `services.msc`. |

### Camera / face issues

| Symptom | Fix |
|---------|-----|
| "Face engine unavailable on server." | InsightFace failed to load — check the backend console for the load error; confirm the model downloaded and you have RAM free. |
| Camera not detected in browser | Allow camera permission; check Windows Camera privacy settings ([10.2](#102-camera-permissions-on-windows)); close other apps using the camera. |
| "Bring your face into the circle" / "Move closer" | Improve lighting, centre and approach the camera. |
| First face login extremely slow | Normal — `buffalo_l` (~280 MB) is downloading. Wait; requires internet. |

### Secure browser issues

| Symptom | Fix |
|---------|-----|
| Electron loads a blank/error page | Backend not running, or `http://localhost` not served. Start Flask + Nginx, or set `CONFIG.baseUrl` to `http://localhost:5000` in `main.js`. |
| "Secure browser required" page shown | The exam's `browser_mode` is `secure_any`/`secure_campus`; you must use the Electron browser, not Chrome. |
| Campus-only block | The exam is `secure_campus` and your IP prefix isn't in `CAMPUS_IP_RANGES`. Adjust `.env` or connect to the campus network. |

---

## 18. Common Errors & Fixes

| Error | Likely Cause | Fix |
|-------|--------------|-----|
| `Microsoft Visual C++ 14.0 or greater is required` | Missing C++ build tools | Install Visual C++ Build Tools ([4.5](#45-microsoft-c-build-tools-required)). |
| `running scripts is disabled on this system` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| `pip` install of `torch`/`insightface` fails | Network timeout / old pip | Upgrade pip; retry; ensure stable internet. |
| `ModuleNotFoundError: No module named 'flask'` | venv not activated | Activate venv before running `app.py`. |
| `Address already in use` / port 5000 busy | Another process on 5000 | Stop the other process or change the port in `app.py`. |
| `smtplib ... 535 Authentication failed` | Using Gmail password, not App Password | Generate a Gmail **App Password** ([14.1](#141-create-a-gmail-app-password)). |
| Welcome/OTP email never arrives | Wrong email creds / 2FA off | Verify App Password; check spam; confirm 2-Step Verification on the sender account. |
| `numpy`/`opencv` import DLL error | Corrupt partial install | `pip uninstall` then `pip install -r requirements.txt` again. |
| Bulk CSV student import fails | Wrong CSV headers | Match the headers expected by the Add Student page (name, admission_no, program, branch, semester, email). |
| "You have already attempted this exam" | A `results` row already exists for that student+exam | Expected — one attempt per exam. Delete the result row only if intentionally allowing a retake. |

---

## 19. FAQ

**Q: Do students need the Electron secure browser?**
A: Only for exams set to `secure_any` or `secure_campus`. Exams set to `any` work in a normal browser.

**Q: Where is the face data stored?**
A: As a raw float32 ArcFace embedding (2048 bytes) in `students.face_embedding_v2` (a MySQL BLOB). No face images are stored.

**Q: Is a GPU required?**
A: No. All ML runs on CPU (ONNX `CPUExecutionProvider`, CPU PyTorch). A GPU is not used on Windows by default.

**Q: How do I add the very first admin?**
A: Insert one row into the `admins` table with a Werkzeug-hashed password ([Section 9](#9-creating-the-first-admin-account)). There is no signup page.

**Q: How are student passwords created?**
A: Auto-generated when an admin adds a student, hashed with Werkzeug, and emailed via the welcome email. Admins can resend credentials.

**Q: How are theory answers graded?**
A: With SBERT (`all-MiniLM-L6-v2`) semantic-similarity scoring, plus plagiarism detection (threshold `PLAGIARISM_THRESHOLD`, default 70%). Admins can trigger AI evaluation and release results.

**Q: What does `branch = 'ALL'` mean for an admin?**
A: A super-admin who can see and manage all branches. A specific branch (e.g. `SCSE`) scopes the admin to that branch only.

**Q: Can I change the port?**
A: Yes — edit `app.run(host='127.0.0.1', port=5000, ...)` at the bottom of `app.py`, and update the Electron `baseUrl` / Nginx `proxy_pass` accordingly.

**Q: First request after start is slow — is something wrong?**
A: No. ML models initialise lazily on first use (and `buffalo_l` may download). It's fast afterward.

---

## 20. Security Recommendations

1. **Never commit `.env`, `*.sql`, `*.pem`, `*.key`, logs, or model/biometric files** — `.gitignore` already excludes them; keep it that way.
2. **Rotate the `SECRET_KEY`** if it was ever exposed; generate a unique one per deployment.
3. **Use a dedicated MySQL user** with only the needed privileges on `exam_system` — not `root` — in production.
4. **Use a Gmail App Password**, and store it only in `.env`; never in code or chat.
5. **Serve over HTTPS** in production (TLS at Nginx) so credentials and camera frames aren't sent in clear text.
6. **Protect the violation logs** (`logs/oems_violations.log`) — they may contain student/violation data; they are gitignored for a reason.
7. **Keep dependencies patched** — periodically review `requirements.txt` and Electron for security updates.
8. **Restrict the admin portal** at the network level (firewall / campus-only) where possible.
9. **Back up the database** regularly (it holds results, students, and face embeddings).
10. Run the secure browser in **kiosk mode on managed machines** for high-stakes exams; the browser alone is not a substitute for a proctor.

---

## 21. Update & Maintenance

### Pull the latest code

```powershell
cd C:\Projects\exam-system
git pull
```

### Update backend dependencies

```powershell
cd backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt --upgrade
```

> If you change pinned versions, re-test face verification, proctoring, and AI evaluation — these ML libraries are version-sensitive.

### Update the secure browser

```powershell
cd secure-browser
npm install
```

### Database migrations

- The app **auto-adds** the face columns to `students` on first login (no action needed).
- For any other schema change, take a backup first:
  ```powershell
  mysqldump -u root -p exam_system > exam_system_backup.sql
  ```

### Log rotation

- `logs/oems_violations.log` grows over time. Archive/rotate it periodically and keep backups secure.

### Refresh ML models

- To force a re-download of the InsightFace model, delete `C:\Users\<you>\.insightface\models\` and restart.

---

*Generated for the OEMS Exam System. Verify paths, ports, and credentials for your specific deployment before going live.*
