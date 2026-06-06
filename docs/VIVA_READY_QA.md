# OEMS — Viva & Interview Question Bank
## Online Examination & Monitoring System

**Prepared for:** University viva, final-year project defence, internship evaluation, and placement interviews.
**Project:** OEMS — Online Examination & Monitoring System
**Author:** Anshuman Kumar Singh
**Repository:** https://github.com/anshumanks2004/OEMS-ExamSystem

> Every answer below is grounded in the **actual OEMS codebase** (`oems.py`, `face_engine.py`, `proctor_engine.py`, the Electron secure browser, and the MySQL schema). Use the cross-questions to anticipate follow-ups an examiner is likely to ask.

---

## Section 1 — General & Conceptual Questions

**Q1. In one sentence, what is OEMS?**
OEMS is a self-hosted, AI-assisted platform for conducting secure online examinations with biometric login, real-time webcam proctoring, automated grading of objective and theory answers, and plagiarism detection — all decided server-side so the client cannot cheat the system.

**Q2. What problem does OEMS solve?**
It answers the core trust question of remote exams: *is the right person taking this exam, alone, without unauthorized aids, and without copying?* It does this with layered integrity — face verification, continuous identity checks, multi-signal proctoring, a kiosk browser, and plagiarism detection — while deliberately minimizing false accusations against honest students.

**Q3. Who are the users and what can each do?**
Two roles: **Admin** (institution staff/proctor) manages students, exams, questions, results, and reviews violations — scoped to their branch (or `ALL`). **Student** logs in biometrically, takes published exams for their cohort, and views results.

**Q4. What makes OEMS different from a basic exam portal?**
The integrity stack: ArcFace biometric login with active-liveness, a temporal confidence-scored AI proctoring engine (not naïve per-frame detection), a true secure kiosk browser, SBERT semantic grading, and TF-IDF plagiarism — integrated into one automated lifecycle.

**Cross-question: Why not just use username/password?**
Password alone can't stop impersonation — someone else could log in for the student. That's why login is **two-factor**: password *and* a live ArcFace face match on every login.

---

## Section 2 — Architecture Questions

**Q5. Describe the overall architecture.**
A layered, server-authoritative web app: a thin **Flask** orchestrator (`oems.py`) sits over self-contained CV/ML engines (`face_engine.py`, `proctor_engine.py`, SBERT, scikit-learn) and a normalized **MySQL** database accessed through a connection pool. The frontend is server-rendered Jinja2 + vanilla JS. A dedicated **Electron** kiosk browser is the secure client, and an optional **Nginx** reverse proxy serves static files and forwards the real client IP.

**Q6. What does "server-authoritative" mean here and why is it important?**
The browser is untrusted; every integrity decision (face match, liveness, violation escalation, grading) is made on the server. For example, face-capture session state lives in a server-side dictionary keyed by student id, and escalated violations are persisted server-side in the same request — so a tampered client can't fake a pass or lower its violation count.

**Q7. Why a modular separation between `oems.py`, `face_engine.py`, and `proctor_engine.py`?**
Separation of concerns and testability. `face_engine.py` and `proctor_engine.py` are **pure functions over NumPy arrays** with no Flask/DB knowledge, so they can be reused and tested in isolation. `oems.py` owns HTTP, auth, sessions, and DB. Notably, the proctoring engine **reuses the same InsightFace model** loaded for login, so there's no extra model memory cost.

**Q8. How do the two engines share a model?**
`proctor_engine.py` imports `face_engine` and calls `face_engine.detect_faces()`, which uses the single lazily-loaded InsightFace singleton. One detector serves both login (best single face) and proctoring (all faces + keypoints + embeddings).

**Cross-question: Isn't a single Flask process a scalability bottleneck?**
Yes — the current post-exam scheduler uses in-process daemon threads, which is fine for a single-institution deployment but doesn't scale horizontally. The documented future work replaces it with a distributed task queue (Celery + Redis) and multiple workers behind a load balancer.

**Q9. Why server-side rendering (Jinja2) instead of a React/SPA frontend?**
SSR keeps the trust boundary on the server, reduces complexity for a single-author project, and avoids shipping integrity logic to the client. The only heavy client logic is the exam runner's proctoring loop, written in vanilla JS.

---

## Section 3 — Database Questions

**Q10. Describe the database schema.**
Seven tables in MySQL (`utf8mb4`): `admins`, `students`, `exams`, `questions`, `answers`, `results`, and `exam_violations`. Relationships: a student has many answers, results, and violations; an exam has many questions, answers, results, and violations; a question has many answers. Foreign keys cascade on delete.

**Q11. Why MySQL and why a connection pool?**
The data is highly relational (students→answers→results→violations), so a relational DB with foreign keys and cascades fits naturally. A **connection pool** (`pool_size=10`) avoids opening a new TCP connection per request and, importantly, prevents connection exhaustion during the burst of concurrent grading threads after an exam ends.

**Q12. How are face embeddings stored?**
As raw `float32` bytes in a `MEDIUMBLOB` column `students.face_embedding_v2` (512 floats = 2048 bytes). `serialize()` does `np.asarray(...).tobytes()` and `deserialize()` reconstructs and re-normalizes. This is compact and lossless versus storing CSV text.

**Q13. What is the `submission_status` field and what are its states?**
It's the result state machine that drives the whole post-exam pipeline: `AwaitingExamEnd` (submitted, not yet graded) → `Hold` (plagiarism) / `Pending` (waiting to grade) / `Evaluated` (graded + emailed) / `Disqualified` (admin action, score 0). A result only emails out once it becomes `Evaluated`.

**Q14. How does the app handle schema changes without manual migrations?**
**Lazy, migration-safe** runtime checks. `ensure_student_face_schema()` adds the `face_*` columns on first student login if missing; `ensure_violation_schema()` adds `severity/confidence/source/evidence` columns and the lookup index. Old databases upgrade transparently.

**Q15. How do you prevent SQL injection?**
Every query uses **parameterized statements** with bound `%s` placeholders (e.g., `cursor.execute("SELECT * FROM students WHERE admission_no=%s", (admission_no,))`). User input is never string-concatenated into SQL.

**Cross-question: What does `NULL` in `answers.score` mean?**
It's the single source of truth for "not yet graded." Objective answers get a numeric score immediately; theory answers are stored with `score=NULL` until the exam-end processor grades them. Aggregate queries count NULLs to decide whether a result is ready.

**Cross-question: Why store the admin's branch on the session?**
For **branch-scoped authorization**: every admin query filters by `admin_branch` (or shows all if it's `ALL`), and cross-branch result access returns HTTP 403.

---

## Section 4 — Authentication & Security Questions

**Q16. Walk me through the student login flow.**
(1) `POST /student_login` with admission number + password; (2) `check_password_hash` succeeds → the session is **cleared** and only a short-lived `pending_student_auth = {student_id, created_at}` marker is set — the student is *not* logged in yet; (3) redirect to `/student_face_verify`; (4) the browser streams webcam frames to `/student_face_frame`; (5) the server registers (first time) or verifies the face; (6) only a successful match calls `_finalize_student_session()` and grants access.

**Q17. Why is the password step alone not enough to log in?**
Because it only creates the `pending_student_auth` marker. Full session keys are set exclusively by `_finalize_student_session()` after the face match. The pending window is bounded by `FACE_AUTH_WINDOW_SECONDS` (default 600 s).

**Q18. How are passwords stored?**
Hashed with Werkzeug's `generate_password_hash` (PBKDF2) and checked with `check_password_hash`. Plaintext passwords are never stored. The default issued password is `OEMS@12345`, and students are urged to change it.

**Q19. How does admin login differ?**
Admin login is single-factor (password) and sets `role="admin"`, `admin_name`, and `admin_branch` in the session. There's no face step for admins.

**Q20. How is OTP implemented for email/password changes?**
A 6-digit OTP generated with `secrets.randbelow` (cryptographically secure), stored in the session with a 10-minute expiry and an attempt counter (max 3). `/send_otp`, `/verify_otp`, and `/resend_otp` are protected by a session-based `@rate_limit` decorator that raises HTTP 429 on abuse, plus a 30-second resend cooldown.

**Q21. What security measures protect the application overall?**
PBKDF2 password hashing; parameterized SQL; secrets in a gitignored `.env` (app refuses to start without `SECRET_KEY`); HTML-escaping of user content in emails; OTP rate limiting; single-attempt exam enforcement; branch-scoped authorization with 403s; and a `.gitignore` that excludes `.env`, logs, evidence images, model weights, DB dumps, and biometric data.

**Cross-question: How are sessions secured?**
Flask signs the session cookie with `SECRET_KEY`. The app raises a `RuntimeError` at startup if `SECRET_KEY` is unset, so it can never run with an empty/predictable signing key.

**Cross-question: Is the SMTP password safe?**
It's a Google **App Password** (not the account password) loaded from `.env`, never committed. If it leaks, only mail sending is affected and it can be revoked independently.

---

## Section 5 — Face Recognition Questions

**Q22. What face-recognition technology does OEMS use and why?**
**InsightFace** with the `buffalo_l` model pack, producing **ArcFace 512-dimensional** embeddings, on **ONNX Runtime**. ArcFace is robust to lighting, pose, and expression — it replaced an old raw-pixel (96×96 grayscale) "embedding" that worked inconsistently. The same model is reused for proctoring.

**Q23. How is face matching done mathematically?**
Embeddings are **L2-normalized**, so cosine similarity is just a dot product (`np.dot(a, b)`). Two faces match if cosine ≥ `FACE_MATCH_THRESHOLD` (default 0.42). Genuine ArcFace pairs typically score 0.45–0.85; impostors sit well below 0.3.

**Q24. How does enrolment work?**
The server collects `FACE_REGISTER_FRAMES` (6) clean frames — each must be exactly one confident, large-enough, sharp face — averages and re-normalizes their embeddings into a stable identity vector, runs a **duplicate-face scan** against all other accounts, and stores the vector if no duplicate is found.

**Q25. Why average multiple frames instead of using one?**
Averaging several embeddings cancels out blinks, micro-motion, and noise, producing a far more stable and reliable identity vector for both registration and verification.

**Q26. How do you prevent someone using a photo to log in?**
**Active-liveness challenges**: the student must blink (eyes open→closed→open) or turn their head past `YAW_TURN_DEG` (16°) and re-centre. This state is tracked in the **server-side session**, so the client can't just claim "liveness OK." There are also passive anti-spoof heuristics in the proctoring engine.

**Q27. How does OEMS stop two students sharing one face/account?**
At enrolment, `_find_duplicate_face()` scans every other account's embedding; if any matches above `FACE_DUPLICATE_THRESHOLD` (0.55), registration is rejected with a "face already registered" error.

**Q28. How does it catch a mid-exam person swap?**
**Identity continuity**: every proctoring frame carries an ArcFace embedding compared via cosine to the student's enrolled vector. If it drops below `IDENTITY_MISMATCH_THRESHOLD` (0.26) persistently, an `identity_mismatch` violation escalates. The enrolled embedding is cached per process to avoid a DB hit per frame.

**Q29. How is the engine made efficient?**
It's a **lazily-loaded, thread-safe singleton** (loaded once, reused). Only `detection` + `recognition` modules load (no age/gender). On Apple Silicon it uses CoreML, else CPU. A sharpness gate rejects blurry frames before computing embeddings.

**Cross-question: What if the face engine fails to load?**
`is_available()` returns False; the login face route returns HTTP 503 ("Face engine unavailable") and `/proctor_health` reports it — the system fails safely rather than silently letting people in.

**Cross-question: Why 0.42 for matching but 0.26 for in-exam identity?**
Login is a controlled pose, so a stricter 0.42 is appropriate. During an exam, pose/lighting are uncontrolled, so the identity-continuity threshold is deliberately looser (0.26) to only catch *clear* mismatches and avoid false person-swap alarms.

---

## Section 6 — AI Proctoring Questions

**Q30. What does the AI proctoring engine detect?**
Per frame: face count (no-face / single / multiple people), real 3-D head-pose → attention (center/left/right/up/down), phones/books/devices/extra-person via YOLO, identity continuity vs the enrolled face, anti-spoof risk, and lighting/quality. Each observation carries a confidence (0–1) and a severity (0–3).

**Q31. How does OEMS avoid false-positive violations? (Key question.)**
A **temporal confidence layer**. For each signal code, a score **rises** while it keeps firing and **decays** when it stops; a violation escalates only when the score crosses a per-code `trigger`, after which a `cooldown` prevents re-spamming. Critical signals (phone, multi-person, identity) rise fast and barely decay; gaze signals rise slowly and decay fast, so a momentary glance never escalates. This is the core false-positive reducer.

**Q32. How is head-pose computed — is it just the face box?**
No — it's **real 3-D pose** via `cv2.solvePnP` against a canonical 5-point face model (eyes, nose, mouth corners), recovering yaw/pitch/roll from the rotation matrix. There's a geometric fallback (nose offset from eye-midpoint) if solvePnP doesn't converge. This replaced the old fake "bounding-box centre" gaze.

**Q33. How does object detection work and why throttled?**
**YOLOv8n** detects COCO classes: cell phone (`phone`), book (`material`), laptop/tv/remote/keyboard (`device`), and person. YOLO is the most expensive step, so it runs only every Nth frame (`OBJECT_EVERY_N`, default 2). Person counts also cross-check the face-based people count to reduce false multi-face alarms.

**Q34. How does anti-spoofing work?**
A passive heuristic on the face crop: printed photos / screen replays tend to be unusually flat (low Laplacian texture variance), have washed-out colour (low saturation std), and carry moiré high-frequency energy (FFT tail). These combine into a `spoof_risk` in [0,1]; ≥0.7 raises a signal. It's treated as a *signal*, not a verdict, weighed over time.

**Q35. What happens when a violation is confirmed during an exam?**
The escalated violation is **persisted server-side** in the `/detect_cheating` request (with severity, confidence, source=`ai`, and a JPEG evidence snapshot). The client reflects it in the visible counter. At **5** total violations (`maxWarnings`), the exam is auto-terminated and submitted.

**Q36. What client-side (non-camera) violations are detected?**
In `start_exam.html`: tab switch / minimize (`visibilitychange` + debounced `blur`), print attempts (`beforeprint`), possible DevTools (window outer/inner size delta > 200px, debounced over 2 checks), blocked cheat shortcuts (Ctrl+Shift+I/J/C, Ctrl+U/P/S, F12, F5, etc.), and fullscreen exit (auto re-enter). These POST to `/log_violation` with `source=client`.

**Q37. What is the "integrity" score on the exam screen?**
A session clean-frame ratio: `100 × clean_frames / total_frames`, computed server-side and shown as a live bar. Green > 80%, amber > 50%, red below.

**Cross-question: Why does the proctoring loop use self-scheduling `setTimeout` instead of `setInterval`?**
To adapt cadence (faster — 1.2 s — while a signal is building/violating; 2.5 s when secure) and to **never stack overlapping requests**, since each frame analysis is async.

**Cross-question: What if the proctoring server call errors out mid-exam?**
It **fails open** — `/detect_cheating` returns `status: secure` on any exception, so a transient server error never wrongly terminates an honest student's exam.

**Cross-question: Why not block the student instantly on one phone detection?**
A single frame is never trusted — a detector can flicker. The temporal layer requires the signal to persist (the phone policy still rises fast, `rise=1.0`, `trigger=1.0`, so a genuinely present phone escalates within ~1–2 confident frames, but a one-frame artifact decays away).

---

## Section 7 — Grading & Plagiarism Questions

**Q38. How are objective questions graded?**
**MCQ**: the chosen option must exactly equal the stored `correct_answer` (e.g., `optionA`) → full marks else 0. **MSQ**: the sorted set of chosen options must equal the sorted stored set exactly (e.g., `optionA,optionC`) → full marks, no partial credit. Both are scored instantly at submit time.

**Q39. How are theory/descriptive answers graded?**
With **SBERT** (`all-MiniLM-L6-v2`). The model embeds the model answer and the student answer; their **cosine similarity**, scaled by a length factor, maps to marks (rounded to the nearest half-mark), with similarity-tiered feedback. A pre-SBERT **quality guard** first rejects too-short answers, dominant-word repetition, and pure keyword lists (score 0).

**Q40. Why SBERT and not keyword matching?**
SBERT captures **semantic meaning**, so a correct answer phrased differently from the model answer still scores well — keyword/string matching would unfairly penalize paraphrasing. It's a small, fast, high-quality sentence-embedding model.

**Q41. How is plagiarism detected?**
With **TF-IDF + cosine similarity** (scikit-learn). All students' descriptive answers are concatenated per student, vectorized, and compared pairwise. If similarity ≥ `PLAGIARISM_THRESHOLD` (70%), both students' results are placed on **Hold** with a hold email — no grading until an admin reviews.

**Q42. When does grading actually happen?**
Only in the **exam-end processor**, never at submit time. This ensures every candidate is checked together for plagiarism before anyone is graded. `submit_exam` just stores answers and sets `AwaitingExamEnd`.

**Q43. How is the post-exam pipeline triggered?**
`schedule_exam_end_processor` spawns a daemon thread that sleeps until the exam end time (+30 s grace), then runs: (1) force-submit students who started but never submitted; (2) TF-IDF plagiarism → Hold the cheaters; (3) SBERT-grade the clean students → mark `Evaluated` → email a result PDF.

**Q44. How is concurrency handled during bulk grading?**
SBERT encoding is wrapped in a **mutex lock** (`_sbert_lock`) to prevent thread collisions; the DB **connection pool** absorbs the burst; and evaluation threads are staggered by 0.5 s to avoid a connection burst.

**Cross-question: What if SBERT can't run (e.g., model load fails)?**
`evaluate_answer` raises `EvaluationUnavailable`; the answer stays ungraded with a "retry later" feedback and the result is set to `Pending` — marks are never guessed.

**Cross-question: Can an admin override grading?**
Yes — admins can `release_result` (un-hold and re-grade), `reevaluate_result` (re-grade a clean result), `disqualify_result` (force score 0), or run `/run_ai_check` / `/reset_ai_evaluation` manually.

---

## Section 8 — Secure Browser & Environment Questions

**Q45. What is the secure browser and how is it built?**
An **Electron 28** kiosk app. `main.js` creates a fullscreen, kiosk, always-on-top, frameless window; `preload.js` exposes a minimal IPC bridge via `contextBridge` with `contextIsolation: true` and `nodeIntegration: false`.

**Q46. How does the server know a request came from the secure browser?**
The Electron app injects a custom header `X-OEMS-Secure-Browser: ElectronV1` on every request (via `onBeforeSendHeaders`). The server's `is_secure_browser()` checks it. Exams with `browser_mode` of `secure_any` or `secure_campus` reject ordinary browsers.

**Q47. What does the secure browser lock down?**
Navigation is allow-listed to `localhost`/`127.0.0.1`/`file://`; new windows are denied; ~20 dangerous global shortcuts are blocked (DevTools, new tab/window, Alt+F4, copy/paste, etc.); force-quit is blocked until the exam is safely submitted (`before-quit` guarded by `isSafeToQuit`); and an audit log records every blocked action, navigation, and violation.

**Q48. What are the three browser modes?**
`any` (any browser), `secure_any` (secure browser required), and `secure_campus` (secure browser **and** campus IP required).

**Q49. How does campus-only enforcement work?**
`is_campus_ip()` reads the client IP — honouring `X-Forwarded-For` (set by Nginx) — and matches it against the configured `CAMPUS_IP_RANGES` prefix. `secure_campus` exams render a "campus only" page otherwise.

**Cross-question: Can't a student just edit the Electron app to fake the header?**
In principle a determined attacker could repackage the client, which is why the header is **one layer**, not the only one. The exam still requires face verification, runs server-side AI proctoring, and (for `secure_campus`) a campus IP. Defeating one control doesn't defeat the system.

**Cross-question: Why allow refresh (Cmd+R/F5) in the browser but the in-page JS blocks F5?**
The Electron layer deliberately allows refresh (a usability requirement so a stuck page can recover), while the exam page's own JS discourages it during an active exam — layered, slightly redundant controls by design.

---

## Section 9 — Technical / Implementation Questions

**Q50. What is the role of the `@login_required` decorator?**
It wraps protected routes and redirects to `/` if the session lacks the right key (`admin_id` for admin routes, `student_id` for student routes), centralizing access control.

**Q51. How are emails sent without blocking the request?**
Via `send_email_async`, which runs the send on a **daemon thread**, so the HTTP response returns immediately. Bulk emails (welcome, exam alerts) reuse a single SMTP connection across recipients.

**Q52. How is the result PDF generated?**
`generate_result_pdf()` uses **ReportLab** to build a styled A4 document **in memory** (BytesIO): student details, question-wise analysis, performance summary, grade, and feedback — then it's attached to the result email.

**Q53. How does the app read a webcam frame from the browser?**
The browser sends a base64 **data URL**; `_decode_base64_frame()` strips the prefix, base64-decodes, and uses `cv2.imdecode` to get a BGR NumPy array for the CV pipeline.

**Q54. How is evidence stored and served?**
`_save_evidence()` writes a size-capped (≤600 KB) JPEG to `static/evidence/` with a filename encoding exam/student/timestamp/code, returns the relative path stored in `exam_violations.evidence`, and the violation-logs page renders it. It's best-effort — a failed write never blocks logging.

**Q55. How does the app prevent a student attempting an exam twice?**
`start_exam` and `submit_exam` both check the `results` table for an existing row for that `(student_id, exam_id)` and block with a security alert / `already_submitted` page.

**Q56. What configuration is environment-driven?**
`SECRET_KEY`, DB host/user/pass/name, SMTP email + app password, `CAMPUS_IP_RANGES`, `SBERT_MODEL`, `PLAGIARISM_THRESHOLD`, and many face/proctor thresholds — all via `.env` / `os.environ` with sensible defaults in code.

**Cross-question: What's the question limit per exam and why?**
50 questions for objective exams, 20 for theory (`max_limit` in `add_question`) — a practical cap to keep exams and grading manageable.

**Cross-question: How are MSQ answers normalized so comparison is reliable?**
Both at authoring and grading, the selected options are **sorted and comma-joined** (e.g., `optionA,optionC`), so order of selection never affects correctness.

---

## Section 10 — Deployment Questions

**Q57. How is OEMS deployed?**
The Flask app runs on `127.0.0.1:5000`. In production it sits behind **Nginx**, which serves `/static/`, forwards everything else to Flask, passes `X-Forwarded-For`/`X-Real-IP`, and sets generous timeouts (120 s) so exam submission never times out. The secure browser is packaged with `electron-builder`.

**Q58. Why put Nginx in front of Flask?**
To offload static-file serving, terminate connections efficiently, pass the **real client IP** (needed for campus enforcement), and provide robust timeouts and buffering for large frame/submit payloads.

**Q59. How are the large ML models handled in deployment?**
They are **not committed** — ArcFace `buffalo_l` (~280 MB), YOLOv8n, and SBERT **auto-download on first use** to their cache directories (e.g., `~/.insightface/models`). `*.pt/*.pth/*.onnx/*.h5` are gitignored.

**Q60. What are the prerequisites to run it?**
Python 3.14, MySQL 8, a created `exam_system` database with the schema, a populated `.env`, and (for proctored/secure exams) Node 18+ for the Electron browser. The first admin must be inserted manually with a Werkzeug-hashed password.

**Q61. Is there OS-specific setup?**
Yes — the repo ships `WINDOWS_SETUP.md` and `MacBook_SETUP.md` with full step-by-step guides, plus a production `README.md`.

**Cross-question: How does the post-exam processor survive without a separate scheduler?**
It uses **in-process daemon threads** with `time.sleep` until the exam end, made **idempotent** via an `_exam_end_scheduled` set. The trade-off: if the process restarts, in-flight schedules are lost — admins can re-trigger via `/trigger_exam_evaluation`. The documented fix is a durable task queue.

---

## Section 11 — GitHub & Version Control Questions

**Q62. How is the project version-controlled?**
With **Git**, hosted on **GitHub** (`anshumanks2004/OEMS-ExamSystem`). The default branch is `main`.

**Q63. What does your `.gitignore` exclude and why?**
Secrets (`.env`, keys, certs), Python artifacts (`venv/`, `__pycache__`), Node/Electron (`node_modules/`, `dist/`), large ML weights (`*.pt/*.pth/*.onnx/*.h5`), databases/dumps, biometric data (`*.npy/*.npz/*.pkl`, `embeddings/`, `faces/`), **proctoring evidence images** (contain student faces), logs (contain student/violation data), and the Nginx config (server/IP details). The goal is to never commit secrets or personal/biometric data.

**Q64. How do you keep secrets out of the repo?**
A committed `.env.example` template documents every variable; the real `.env` is gitignored. The app refuses to start without `SECRET_KEY`, forcing proper configuration rather than a hardcoded fallback.

**Q65. Describe your commit practice.**
Conventional, descriptive commits (e.g., `docs: add production README and OS-specific setup guides`, `OEMS Exam System — initial clean commit`). The initial commit was a deliberately *clean* commit with secrets and large artifacts already excluded.

**Cross-question: How would you collaborate on this with a team?**
Feature branches off `main`, pull requests with review, the same `.gitignore` discipline, and `requirements.txt` (pinned versions) + `package.json` to reproduce environments. CI could run linting/tests before merge.

**Cross-question: Why pin exact versions in `requirements.txt`?**
Reproducibility — ML stacks (torch/onnxruntime/insightface) are version-sensitive, so pinning the verified-working versions avoids "works on my machine" breakage.

---

## Section 12 — Project-Specific Deep-Dive Questions

**Q66. Walk me through exactly what happens when a student clicks "Start Exam."**
`/start_exam/<id>` checks: exam exists; current time is within `[start_time, start_time+duration]`; no existing result (single attempt); browser mode (`secure_*` → require the Electron header; `secure_campus` → also require campus IP). It then shuffles the questions, schedules the exam-end processor, and renders `start_exam.html`, which runs a gatekeeper (camera + engine readiness), then the proctoring loop and security listeners.

**Q67. What exactly is stored when a student submits?**
For each question: MCQ/MSQ get an `answers` row with an immediate score; theory gets a row with `score=NULL` and feedback "Pending AI evaluation." A `results` row is inserted with the objective `total_score` and status `AwaitingExamEnd`. **No grading email is sent at submit time.**

**Q68. How is the violation count kept tamper-proof?**
AI violations are escalated and **stored server-side** in `/detect_cheating`; the client only *reflects* the count. Even client-event violations POST to the server. So the authoritative tally lives in `exam_violations`, not in JavaScript state.

**Q69. What's the difference between a "warning" and a "violation" in the verdict?**
A **warning** is a signal whose temporal score is *building* toward its trigger (shown with a progress %); a **violation** is one that *crossed* its trigger and was escalated + persisted. The UI polls faster while warnings/violations are active.

**Q70. How does the admin review integrity after an exam?**
Via `/violation_logs`: a branch-scoped table of every violation (type, details, severity, confidence, source, evidence snapshot, student, exam) plus summary stats (totals, terminations, AI flags, unique students/exams) and a **severity-weighted per-student risk ranking** (top 8) so reviewers triage the riskiest candidates first.

**Q71. What happens to students who never submit?**
The exam-end processor **force-submits** students who *started* (have answers) but never submitted. Students who **never attempted** (no answers) are treated as absent and ignored.

**Q72. Why store `face_embedding_v2` and not migrate the old column?**
The legacy `face_embedding` held raw-pixel CSV vectors incompatible with ArcFace. Migrating them would be meaningless, so the code intentionally **does not** migrate; affected users simply re-enrol once with ArcFace on next login (their stale `face_registered` flag is cleared).

**Cross-question: If I disabled `ai_proctoring` on an exam, what changes?**
The gatekeeper skips camera/engine checks, the proctoring loop and `/detect_cheating` calls don't run, and only the in-page client-event listeners (tab switch, print, devtools, fullscreen) remain. Face *login* is unaffected — it's separate from per-exam AI proctoring.

**Cross-question: Where is the single most important line of defence?**
Arguably the **server-authoritative** principle — because identity, liveness, violation escalation, and grading are all decided on the server, no amount of client tampering can fake a pass. Every other control builds on that foundation.

---

## Section 13 — Likely "Gotcha" / Reflective Questions

**Q73. What was the hardest part of this project?**
Reducing **false-positive violations**. Naïve per-frame detection accuses honest students constantly. The fix — a temporal confidence layer with per-signal rise/decay/trigger/cooldown — required carefully tuned policies so genuine cheating still escalates quickly while normal head movement and detector flicker decay away.

**Q74. What would you do differently or improve next?**
Move scheduling to a durable task queue (Celery/Redis) for restart-safety and horizontal scale, push proctoring verdicts over WebSockets instead of per-frame HTTP, add question-bank per-student sampling, and add explicit consent/retention tooling for the biometric data.

**Q75. What are the system's current limitations?**
Single Flask process with in-process scheduling (not restart-safe / horizontally scalable); desktop-only secure client; question order is shuffled but not sampled from a pool; no live human invigilator stream.

**Q76. How did you validate that proctoring works?**
By generating real evidence captures during test exams (the repo ships sample snapshots: phone, gaze-up, tab-switch) and confirming each escalated through the temporal layer, was persisted to `exam_violations`, and surfaced in the admin logs with the correct severity and snapshot.

**Q77. Is the biometric approach privacy-respecting?**
Embeddings (not raw face images) are stored, evidence images are gitignored and never pushed, and secrets/PII are kept out of source control. The roadmap adds explicit consent capture, retention windows, and one-click biometric deletion to strengthen this further.

**Q78. If the examiner asks "show me the cleverest piece of engineering" — what do you show?**
The temporal confidence layer in `proctor_engine.process_frame`: a compact per-code accumulator (`rise`/`decay`/`trigger`/`cooldown`) that turns noisy per-frame detections into fair, persistent-only violations — the difference between an unusable false-positive generator and a trustworthy proctor.

---

*End of question bank. Tip for the viva: lead every answer with the decision and its reason ("we used X because Y"), then offer the implementation detail only if asked — examiners reward understanding over recall.*
