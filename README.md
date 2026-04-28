# CareerPrep Job-Hunting Agent

A file-driven Python agent that helps a student manage the full job-search workflow:
job analysis, resume tailoring, interview prep, application tracking, and reminders.
Ships with both a **command-line interface** and a **Streamlit web UI**, plus
optional **LLM-tailored output** via OpenRouter.

Built for the **Basic Agentic AI Lab — File-Driven Job-Hunting Agent** activity,
following the GAME framework (Goal / Actions / Memory / Environment).

---

## Deploying your own copy on Streamlit Cloud

1. Fork this repo (or clone + push to your own).
2. Go to <https://share.streamlit.io>, sign in with GitHub, click **New app**.
3. Pick the repo, branch `main`, main file `ui.py`. Click **Deploy**.
4. After it builds, open **Settings → Secrets** and paste:
   ```toml
   OPENROUTER_API_KEY = "sk-or-v1-your-key-here"
   ```
   (See [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example).)
   Without a key the app still runs — it just falls back to template output.

> **Cost note:** every analysis a public visitor runs hits *your* OpenRouter
> account. Set spend limits on <https://openrouter.ai/account> if you're worried.

---

## LLM-tailored output (optional but recommended)

By default the agent uses templates for the cover letter, interview questions, and
resume bullet rewrites. With an **OpenRouter** API key, those three outputs become
personalized — the cover letter quotes a specific project from your resume, the
interview list shrinks from 50 generic questions to 8 tailored ones, and resume
suggestions rewrite *your actual bullets* in JD vocabulary.

```bash
# 1. Get a key at https://openrouter.ai/keys
# 2. Copy the example and paste your key in
cp .env.example .env
$EDITOR .env

# 3. Run as usual — the agent auto-detects the key
python app.py
```

The web UI shows a 🤖 **LLM ON** badge in the top-right when the key is detected,
or a 📝 **Templates** badge when running in fallback mode. No key? Everything still
works; you just get the template versions.

---

## Two ways to use it

### Option A — Web UI (recommended for non-technical users)

Double-click the launcher for your OS:

- **macOS:** double-click `start.command`
- **Windows:** double-click `start.bat`
- **Linux:** `streamlit run ui.py`

A browser tab opens at `http://localhost:8501`. Drag-drop your resume and a job poster
into the sidebar, click **Run analysis now**, and review every report inline.

### Option B — Command line (original CLI)

```bash
# 1. Install optional deps (PDF reading, PDF export, Streamlit UI)
pip install -r requirements.txt

# 2. Copy samples (or paste your own)
cp samples/sample_job_poster.txt input_jobs/
cp samples/sample_resume.txt     input_resumes/
cp samples/sample_kb.txt         input_kb/

# 3. Run analysis
python app.py

# 4. Or interactive menu
python app.py --menu
```

---

## How it works

The agent reads three input folders, runs analysis, and writes results to
`outputs/` and `tracker/`.

```
input_jobs/      ->  job posters / job descriptions (.txt or .pdf)
input_resumes/   ->  your resume (.txt or .pdf)
input_kb/        ->  course slide notes / interview prep notes (.txt or .pdf)

outputs/         ->  generated reports (text + PDF)
tracker/         ->  applications.csv, reminders.txt, calendar.ics, memory.json, status_history.log
samples/         ->  ready-to-copy example files
```

---

## What the agent generates

| File | Purpose |
| --- | --- |
| `outputs/job_analysis_report.txt` | Skills/keywords detected + extra capitalized JD terms |
| `outputs/skill_gap_report.txt` | Match score, matched + missing skills |
| `outputs/tailored_resume_suggestions.txt` | JD-aligned bullet rewrites + hygiene tips |
| `outputs/interview_questions.txt` | Technical (per-skill) + HR + KB-derived + reverse questions |
| `outputs/cover_letter.txt` | Draft cover letter using extracted role/company/name |
| `outputs/project_mapping.txt` | Per-project JD coverage % |
| `outputs/resume_quality_score.txt` | 6-component quality score out of 100 |
| `outputs/final_agent_report.txt` | All sections combined into one timestamped report |
| `outputs/final_agent_report.pdf` | Same content as PDF (via fpdf2) |
| `tracker/applications.csv` | Application tracker with status, dates, next actions |
| `tracker/applications.csv.bak` | Auto-backup before each tracker write |
| `tracker/reminders.txt` | Date-aware reminders (TODAY / TOMORROW / OVERDUE / in Nd) |
| `tracker/calendar.ics` | Import into Google / Apple Calendar / Outlook |
| `tracker/memory.json` | Machine-readable run snapshot |
| `tracker/status_history.log` | Append-only log of status transitions |

---

## Web UI features

- **Sidebar uploads** for jobs, resumes, KB files (txt or pdf, multi-file)
- **One-click analyze button** with progress spinner
- **Inline preview** of every report (collapsible, with per-file download)
- **Tracker tab**: form-based add/update, no CSV editing needed
- **Dashboard tab**: status funnel chart, upcoming-dates table, JSON memory inspector
- **Bonus downloads**: PDF report and `.ics` calendar straight from the browser

---

## Robustness features (Phase 8)

The agent is designed to handle messy real-world inputs:

- **Word-boundary keyword regex** — won't match `"oop"` inside `"loop"` or `"api"` inside `"capital"`.
- **Encoding fallback** — utf-8 first, then latin-1 if a `.txt` file uses a different encoding.
- **Scanned-PDF detection** — warns if a PDF returns < 50 chars/page (likely an image scan).
- **File-size cap** — refuses files larger than 5 MB so a huge PDF can't slow the regex pass.
- **Word-document warning** — `.docx`/`.doc` files are flagged with a clear "save as PDF" message.
- **Flexible date parsing** — accepts `2026-05-03`, `5/3/2026`, `3 May 2026`, `May 3, 2026`, and more, normalizing all to ISO before storing.
- **Auto-backup** — `applications.csv.bak` is written before every tracker overwrite.
- **Status validation** — only the 6 official statuses are accepted by the API.
- **Status history log** — every status change is timestamped to `tracker/status_history.log`.
- **Empty-extraction warning** — if no skills are detected, the agent says so explicitly instead of producing an empty report.
- **Bigram surfacing** — capitalized JD phrases not in the keyword list are surfaced as "other notable terms" so you don't miss role-specific jargon.
- **Project section aliases** — recognizes `Projects:`, `Project Experience`, `Personal Projects`, `Academic Projects`, etc.

---

## Project structure

```
job-hunting-agent/
├── README.md
├── reflection.md
├── requirements.txt
├── app.py                          # Core agent logic (~1300 lines)
├── ui.py                           # Streamlit web UI (~280 lines)
├── start.command                   # macOS one-click launcher
├── start.bat                       # Windows one-click launcher
├── input_jobs/                     # Place job posters here
├── input_resumes/                  # Place resume here
├── input_kb/                       # Place interview-prep KB here
├── outputs/                        # 9 generated reports (text + pdf)
├── tracker/                        # CSV, reminders, calendar, memory, history
└── samples/                        # Copy-pasteable starter files
```

---

## Requirements

- **Required:** Python 3.9+ (the core agent runs on stdlib only)
- **Optional:**
  - `pypdf` — read PDF inputs
  - `fpdf2` — export the final report as PDF
  - `streamlit` + `pandas` — the web UI

Install everything: `pip install -r requirements.txt`
