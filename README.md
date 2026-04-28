# CareerPrep Job-Hunting Agent

A file-driven Python agent that helps a student manage the full job-search workflow:
job analysis, resume tailoring, interview prep, application tracking, and reminders.

Built for the **Basic Agentic AI Lab — File-Driven Job-Hunting Agent** activity,
following the GAME framework (Goal / Actions / Memory / Environment).

---

## How it works

The agent reads three input folders, runs analysis, and writes results to
`outputs/` and `tracker/`.

```
input_jobs/      ->  job posters / job descriptions (.txt or .pdf)
input_resumes/   ->  your resume (.txt or .pdf)
input_kb/        ->  course slide notes / interview prep notes (.txt or .pdf)

outputs/         ->  generated reports
tracker/         ->  applications.csv, reminders.txt, memory.json
samples/         ->  ready-to-copy example files
```

---

## Quick start

```bash
# 1. Copy the sample inputs (or paste your own)
cp samples/sample_job_poster.txt input_jobs/
cp samples/sample_resume.txt     input_resumes/
cp samples/sample_kb.txt         input_kb/

# 2. (Optional) install pypdf if you want to feed in PDF files
pip install -r requirements.txt

# 3. Run analysis
python app.py

# 4. Or use the interactive menu (add applications, update statuses, etc.)
python app.py --menu
```

---

## What the agent generates

| File | Purpose |
| --- | --- |
| `outputs/job_analysis_report.txt` | Skills/keywords detected in job posters |
| `outputs/skill_gap_report.txt` | Match score, matched + missing skills |
| `outputs/tailored_resume_suggestions.txt` | JD-aligned bullet rewrites + hygiene tips |
| `outputs/interview_questions.txt` | Technical (per-skill) + HR + KB-derived + reverse questions |
| `outputs/cover_letter.txt` | Draft cover letter using extracted role/company/name |
| `outputs/project_mapping.txt` | Per-project JD coverage % |
| `outputs/resume_quality_score.txt` | 6-component quality score out of 100 |
| `outputs/final_agent_report.txt` | All sections combined into one timestamped report |
| `tracker/applications.csv` | Application tracker with status, dates, next actions |
| `tracker/reminders.txt` | Date-aware reminders (TODAY / TOMORROW / OVERDUE / in Nd) |
| `tracker/memory.json` | Machine-readable run snapshot |

---

## Interactive menu

Run `python app.py --menu` to:

1. Run full analysis
2. Add a new application (auto-generates `APP-NNN` ID)
3. Show all applications
4. Update an application's status (with date prompts)
5. Show reminders
6. Quit

---

## Features beyond the minimum spec

Implemented for the "uniqueness" rubric points:

- **PDF reading** for any input folder (via `pypdf`, graceful fallback if not installed)
- **Cover letter generator** that scrapes role, company, and candidate name from inputs
- **Project-to-JD mapping** — shows which of your resume projects cover which JD skills, with coverage %
- **Resume quality score** with 6 weighted components (JD alignment, breadth, projects section, quantification, contact info, length sanity)
- **Date-aware urgency tags** in reminders (`[TODAY]`, `[TOMORROW]`, `[OVERDUE by 3d]`, `[in 5d]`)
- **JSON memory snapshot** at `tracker/memory.json` for downstream tooling
- **CLI menu** for tracker operations (no manual CSV editing required)
- **Word-boundary keyword regex** — avoids false matches like `"oop"` inside `"loop"` or `"api"` inside `"capital"`
- **Auto-incrementing application IDs** with status validation

---

## Project structure

```
job-hunting-agent/
├── README.md
├── reflection.md
├── requirements.txt
├── app.py                          # All logic; ~600 lines, single file
├── input_jobs/                     # Place job posters here
├── input_resumes/                  # Place resume here
├── input_kb/                       # Place interview-prep KB here
├── outputs/                        # 8 generated reports
├── tracker/                        # applications.csv, reminders.txt, memory.json
└── samples/                        # Copy-pasteable starter files
```

---

## Requirements

- Python 3.9+
- Optional: `pypdf` for PDF input support (see `requirements.txt`)
