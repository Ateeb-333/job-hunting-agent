"""
CareerPrep Job-Hunting Agent
File-driven agent that reads job posters, resumes, and KB material from folders,
then generates analysis reports, tailored resume suggestions, interview questions,
and maintains an application tracker with reminders.
"""

import csv
import json
import os
import re
from datetime import date, datetime

try:
    from pypdf import PdfReader
    _PDF_AVAILABLE = True
except ImportError:
    _PDF_AVAILABLE = False

JOB_DIR = "input_jobs"
RESUME_DIR = "input_resumes"
KB_DIR = "input_kb"
OUTPUT_DIR = "outputs"
TRACKER_DIR = "tracker"
SAMPLES_DIR = "samples"

ALL_FOLDERS = [JOB_DIR, RESUME_DIR, KB_DIR, OUTPUT_DIR, TRACKER_DIR, SAMPLES_DIR]

# Skills/keywords the agent recognizes. Multi-word entries are matched as phrases.
KEYWORDS = [
    "python", "machine learning", "data preprocessing", "github", "git",
    "api", "prompt engineering", "sql", "communication", "problem solving",
    "oop", "database", "jupyter", "pandas", "numpy", "deep learning",
    "html", "css", "flask", "streamlit", "tensorflow", "pytorch",
    "scikit-learn", "rest", "etl", "linux", "docker", "aws",
    "javascript", "react", "node", "java", "c++", "version control",
]


# --------------------------------------------------------------------------- #
# File I/O helpers
# --------------------------------------------------------------------------- #

def ensure_folders():
    for folder in ALL_FOLDERS:
        os.makedirs(folder, exist_ok=True)


def _read_pdf(path):
    """Extract text from a PDF. Returns "" and warns if pypdf is missing."""
    if not _PDF_AVAILABLE:
        print(f"  ! Skipping {os.path.basename(path)} (install pypdf to enable PDF reading)")
        return ""
    try:
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as e:
        print(f"  ! Failed to read {os.path.basename(path)}: {e}")
        return ""


def read_text_files(folder):
    """Read every .txt and .pdf file in `folder`, return (combined_text, file_count, file_list)."""
    combined_text = ""
    files_read = []
    if not os.path.isdir(folder):
        return combined_text, 0, files_read

    for filename in sorted(os.listdir(folder)):
        lower = filename.lower()
        path = os.path.join(folder, filename)
        if lower.endswith(".txt"):
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        elif lower.endswith(".pdf"):
            content = _read_pdf(path)
            if not content:
                continue
        else:
            continue
        combined_text += f"\n\n--- FILE: {filename} ---\n{content}"
        files_read.append(filename)

    return combined_text, len(files_read), files_read


def save_text(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# --------------------------------------------------------------------------- #
# Phase 2: keyword extraction + skill matching
# --------------------------------------------------------------------------- #

def extract_keywords(text, keywords=KEYWORDS):
    """
    Return the subset of `keywords` that appear in `text` as whole words/phrases.
    Uses regex word boundaries so 'oop' won't match 'loop' and 'api' won't match
    'capital'. Special-cases tokens that contain regex metacharacters (e.g. c++).
    """
    text_lower = text.lower()
    found = []
    for kw in keywords:
        kw_lower = kw.lower()
        # Tokens with non-word characters (c++, scikit-learn) need escape + custom boundary.
        if re.search(r"\W", kw_lower):
            pattern = r"(?<!\w)" + re.escape(kw_lower) + r"(?!\w)"
        else:
            pattern = r"\b" + re.escape(kw_lower) + r"\b"
        if re.search(pattern, text_lower):
            found.append(kw)
    return found


def compare_skills(job_skills, resume_skills):
    """Return (matched, missing, score%) where score = matched / job_skills."""
    job_set = set(job_skills)
    resume_set = set(resume_skills)
    matched = sorted(job_set & resume_set)
    missing = sorted(job_set - resume_set)
    score = 0.0 if not job_set else round(len(matched) / len(job_set) * 100, 2)
    return matched, missing, score


# --------------------------------------------------------------------------- #
# Phase 2: report generation
# --------------------------------------------------------------------------- #

def generate_job_analysis(job_files, job_skills):
    lines = [
        "Job Analysis Report",
        "===================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"Job posters analyzed: {len(job_files)}",
    ]
    for f in job_files:
        lines.append(f"  - {f}")
    lines += [
        "",
        f"Skills / keywords detected ({len(job_skills)}):",
    ]
    if job_skills:
        for skill in sorted(job_skills):
            lines.append(f"  - {skill}")
    else:
        lines.append("  (none detected — consider expanding the KEYWORDS list)")
    return "\n".join(lines) + "\n"


def generate_skill_gap_report(job_skills, resume_skills, matched, missing, score):
    lines = [
        "Skill Gap Report",
        "================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"Match score: {score}%  ({len(matched)} of {len(job_skills)} job skills found in resume)",
        "",
        f"Matched skills ({len(matched)}):",
    ]
    if matched:
        for s in matched:
            lines.append(f"  + {s}")
    else:
        lines.append("  (no overlap yet)")

    lines += ["", f"Missing skills ({len(missing)}):"]
    if missing:
        for s in missing:
            lines.append(f"  - {s}")
    else:
        lines.append("  (none — your resume covers every skill in the JD)")

    lines += [
        "",
        f"Resume-only skills ({len(set(resume_skills) - set(job_skills))}):",
        "  (skills you have that this JD didn't ask for — keep them, "
        "they may matter for other roles)",
    ]
    extras = sorted(set(resume_skills) - set(job_skills))
    for s in extras:
        lines.append(f"  . {s}")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Phase 3: resume tailoring + interview questions
# --------------------------------------------------------------------------- #

# Curated bullet-point templates per skill. Used to write JD-aligned resume bullets.
SKILL_BULLET_TEMPLATES = {
    "python": "Wrote production-quality Python (OOP, error handling, virtual envs) for {context}.",
    "machine learning": "Trained and evaluated ML models, reporting accuracy / precision / recall on a held-out test set.",
    "data preprocessing": "Built data preprocessing pipelines (missing-value imputation, encoding, scaling) avoiding train/test leakage.",
    "github": "Used GitHub for version control, pull requests, and code review on team projects.",
    "git": "Used Git feature-branch workflow with clear commit messages and rebases.",
    "api": "Designed and consumed REST APIs; handled auth, pagination, and error responses.",
    "prompt engineering": "Drafted, tested, and iterated on LLM prompts; measured output quality across versions.",
    "sql": "Wrote SQL (JOINs, GROUP BY, window functions) against a relational database.",
    "communication": "Presented technical work to non-technical stakeholders; wrote clear README/docs.",
    "problem solving": "Broke down ambiguous problems, scoped a solution, and shipped iteratively.",
    "oop": "Applied OOP design (encapsulation, inheritance, polymorphism) to keep code modular.",
    "database": "Modeled schemas and queried relational databases for application features.",
    "jupyter": "Documented experiments and findings in Jupyter notebooks with reproducible cells.",
    "pandas": "Used pandas for data cleaning, joining, and aggregation on real-world datasets.",
    "numpy": "Used numpy for vectorized numerical computation and array manipulation.",
    "deep learning": "Trained neural networks (feed-forward / CNN) and tuned hyperparameters.",
    "html": "Built HTML5 layouts with semantic markup and accessible structure.",
    "css": "Styled responsive layouts using modern CSS (flexbox, grid).",
    "flask": "Built and deployed Flask web apps with templating, sessions, and route handlers.",
    "streamlit": "Shipped interactive Streamlit apps to demo data/ML projects.",
    "tensorflow": "Trained models in TensorFlow / Keras and exported them for inference.",
    "pytorch": "Built and trained PyTorch models with custom datasets and training loops.",
    "scikit-learn": "Used scikit-learn (pipelines, model selection, metrics) for classical ML tasks.",
    "rest": "Designed and consumed REST endpoints; followed status-code and resource conventions.",
    "etl": "Built ETL jobs that extracted from source systems, transformed records, and loaded warehouses.",
    "linux": "Comfortable on Linux command line: shell scripting, file permissions, processes.",
    "docker": "Containerized applications with Docker and used compose for multi-service local dev.",
    "aws": "Deployed services to AWS (EC2 / S3 / Lambda) and managed basic IAM.",
    "javascript": "Wrote JavaScript for browser interactivity and async API calls.",
    "react": "Built React components with hooks and managed component-level state.",
    "node": "Wrote Node.js server-side code and used npm for dependency management.",
    "java": "Wrote Java with strong typing and standard library for course / project work.",
    "c++": "Used C++ for performance-sensitive course projects (data structures, algorithms).",
    "version control": "Used Git/GitHub version control with branches, PRs, and code review.",
}


def generate_resume_suggestions(matched, missing):
    lines = [
        "Tailored Resume Suggestions",
        "===========================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "1) Strengthen these bullets — you already have evidence, make sure it's visible:",
    ]
    if matched:
        for s in matched:
            tmpl = SKILL_BULLET_TEMPLATES.get(s)
            if tmpl:
                lines.append(f"  + [{s}] {tmpl.format(context='your project')}")
            else:
                lines.append(f"  + [{s}] Add a specific bullet showing where you used {s}.")
    else:
        lines.append("  (no overlapping skills yet — focus on section 2 first)")

    lines += [
        "",
        "2) Close these gaps before applying — the JD asks for them, your resume doesn't show them:",
    ]
    if missing:
        for s in missing:
            tmpl = SKILL_BULLET_TEMPLATES.get(s)
            if tmpl:
                lines.append(f"  - [{s}] Suggested bullet: {tmpl.format(context='a small practice project')}")
            else:
                lines.append(f"  - [{s}] Build or document one example of {s}.")
    else:
        lines.append("  (none — your resume already covers every JD skill)")

    lines += [
        "",
        "3) General resume hygiene:",
        "  . Lead each bullet with a strong verb (Built, Shipped, Analyzed, Reduced).",
        "  . Quantify outcomes where possible (rows processed, accuracy %, time saved).",
        "  . Match terminology to the JD: if the JD says 'REST API', don't write 'web service'.",
        "  . Keep it to one page; cut anything older than 2 years that isn't load-bearing.",
        "  . Link to GitHub for every project bullet that has source code.",
    ]
    return "\n".join(lines) + "\n"


def _extract_kb_topics(kb_text):
    """Pull lines that look like topic headers or bullet points from the KB."""
    topics = []
    for raw in kb_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("---"):
            continue
        # Topic headers like "Topic: Tell Me About Yourself"
        m = re.match(r"^Topic:\s*(.+)$", line, re.IGNORECASE)
        if m:
            topics.append(("topic", m.group(1).strip()))
            continue
        # Bullet-point notes
        if line.startswith("-") or line.startswith("*"):
            cleaned = line.lstrip("-*").strip()
            if cleaned:
                topics.append(("bullet", cleaned))
    return topics


def generate_interview_questions(job_skills, kb_text):
    lines = [
        "Interview Questions",
        "===================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "A. Technical questions (derived from the job posters):",
    ]
    if job_skills:
        for s in sorted(job_skills):
            lines.append(f"  - Walk me through a project where you used {s}. What trade-offs did you make?")
            lines.append(f"  - What's a common pitfall with {s} that you've personally hit?")
    else:
        lines.append("  (no JD skills detected)")

    lines += [
        "",
        "B. HR / behavioral questions (standard set):",
        "  - Tell me about yourself.",
        "  - Why this role / why this company?",
        "  - Walk me through your strongest project end-to-end.",
        "  - Tell me about a time you disagreed with a teammate. What did you do?",
        "  - Tell me about a time you failed and what you learned.",
        "  - What's a weakness you're actively working on?",
        "  - Where do you want to be in 2-3 years?",
        "  - Why should we pick you over other candidates?",
        "",
        "C. Questions inspired by your KB / course material:",
    ]

    topics = _extract_kb_topics(kb_text)
    if not topics:
        lines.append("  (no topics extracted from input_kb/ — add some notes there)")
    else:
        seen = set()
        for kind, content in topics:
            if content in seen:
                continue
            seen.add(content)
            if kind == "topic":
                lines.append(f"  [Topic] {content}")
                lines.append(f"    - How would you explain '{content}' to a non-technical interviewer?")
            else:
                # Trim long bullets so the question stays readable.
                short = content if len(content) <= 110 else content[:107] + "..."
                lines.append(f"    - In an interview, how would you respond to: \"{short}\"?")
            if len(seen) >= 25:
                break

    lines += [
        "",
        "D. Questions YOU should ask the interviewer:",
        "  - What does success look like for this role in the first 90 days?",
        "  - What's the team's tech stack and how does code get reviewed?",
        "  - What's the biggest technical challenge the team is facing right now?",
        "  - How does the team support an intern's learning and growth?",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Phase 4: application tracker + date-aware reminders
# --------------------------------------------------------------------------- #

TRACKER_PATH = os.path.join(TRACKER_DIR, "applications.csv")
REMINDERS_PATH = os.path.join(TRACKER_DIR, "reminders.txt")

TRACKER_FIELDS = [
    "application_id", "company", "role", "source", "status",
    "applied_date", "interview_date", "follow_up_date", "next_action", "notes",
]

VALID_STATUSES = {
    "Not Applied", "Applied", "Shortlisted",
    "Interview Scheduled", "Rejected", "Offered",
}


def create_tracker_if_missing():
    """Create applications.csv with header + one example row, only if missing."""
    if os.path.exists(TRACKER_PATH):
        return
    os.makedirs(TRACKER_DIR, exist_ok=True)
    with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKER_FIELDS)
        writer.writeheader()
        writer.writerow({
            "application_id": "APP-001",
            "company": "ABC Tech",
            "role": "Junior AI Engineer Intern",
            "source": "LinkedIn",
            "status": "Interview Scheduled",
            "applied_date": "2026-04-28",
            "interview_date": "2026-05-03",
            "follow_up_date": "2026-05-06",
            "next_action": "Revise Python and ML basics; prepare project walkthrough",
            "notes": "Resume tailored for Python and ML role",
        })


def read_tracker():
    if not os.path.exists(TRACKER_PATH):
        return []
    with open(TRACKER_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_tracker(rows):
    with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKER_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in TRACKER_FIELDS})


def _next_application_id(rows):
    max_n = 0
    for row in rows:
        m = re.match(r"APP-(\d+)", row.get("application_id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"APP-{max_n + 1:03d}"


def add_application(company, role, source="", status="Not Applied",
                    applied_date="", interview_date="", follow_up_date="",
                    next_action="", notes=""):
    """Append a new application to the tracker. Returns the new application_id."""
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}, got {status!r}")
    create_tracker_if_missing()
    rows = read_tracker()
    new_id = _next_application_id(rows)
    rows.append({
        "application_id": new_id,
        "company": company,
        "role": role,
        "source": source,
        "status": status,
        "applied_date": applied_date,
        "interview_date": interview_date,
        "follow_up_date": follow_up_date,
        "next_action": next_action,
        "notes": notes,
    })
    write_tracker(rows)
    return new_id


def update_application(application_id, **fields):
    """Update fields on an existing application. Returns True if found."""
    rows = read_tracker()
    found = False
    for row in rows:
        if row.get("application_id") == application_id:
            for k, v in fields.items():
                if k in TRACKER_FIELDS:
                    row[k] = v
            found = True
            break
    if found:
        write_tracker(rows)
    return found


def _parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _urgency_tag(target_date, today):
    """Return a short urgency tag based on days from today to `target_date`."""
    if target_date is None:
        return ""
    delta = (target_date - today).days
    if delta < 0:
        return f"[OVERDUE by {abs(delta)}d]"
    if delta == 0:
        return "[TODAY]"
    if delta == 1:
        return "[TOMORROW]"
    if delta <= 7:
        return f"[in {delta}d]"
    return f"[in {delta}d]"


def generate_reminders(today=None):
    """Build reminders.txt content from the current tracker state."""
    today = today or date.today()
    rows = read_tracker()

    lines = [
        "Application Reminders",
        "=====================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}  (today = {today:%Y-%m-%d})",
        "",
    ]

    if not rows:
        lines.append("No applications tracked yet. Add one to get started.")
        return "\n".join(lines) + "\n"

    counts = {}
    for row in rows:
        counts[row.get("status", "Unknown")] = counts.get(row.get("status", "Unknown"), 0) + 1

    lines.append("Summary by status:")
    for status, n in sorted(counts.items()):
        lines.append(f"  {status:<22} {n}")
    lines.append("")
    lines.append("Per-application reminders:")

    for row in rows:
        app_id = row.get("application_id", "")
        company = row.get("company", "")
        role = row.get("role", "")
        status = (row.get("status") or "").strip()
        interview_date = _parse_date(row.get("interview_date"))
        follow_up_date = _parse_date(row.get("follow_up_date"))
        applied_date = _parse_date(row.get("applied_date"))
        next_action = row.get("next_action", "")

        header = f"- {app_id} | {company} - {role} | {status}"
        lines.append(header)

        if status.lower() == "not applied":
            lines.append("    -> Tailor your resume for this role and submit. Don't sit on it.")
        elif status.lower() == "applied":
            if follow_up_date:
                tag = _urgency_tag(follow_up_date, today)
                lines.append(f"    -> Follow up on {follow_up_date} {tag} if no response.")
            elif applied_date:
                lines.append(f"    -> Applied on {applied_date}. Set a follow-up date.")
            else:
                lines.append("    -> Set an applied_date and a follow-up date.")
        elif status.lower() == "shortlisted":
            lines.append("    -> Confirm next steps with recruiter; start prep now.")
        elif status.lower() == "interview scheduled":
            if interview_date:
                tag = _urgency_tag(interview_date, today)
                lines.append(f"    -> Interview on {interview_date} {tag}.")
            else:
                lines.append("    -> Interview scheduled but no date recorded — fix this.")
            if next_action:
                lines.append(f"    -> Next action: {next_action}")
        elif status.lower() == "rejected":
            lines.append("    -> Closed. Note the lesson learned and move on.")
        elif status.lower() == "offered":
            lines.append("    -> Offer received. Review terms; respond by the deadline.")
        else:
            lines.append(f"    -> Unknown status {status!r}. Review this row.")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Phase 6: uniqueness features
#   - cover letter generator
#   - project-to-JD mapping
#   - resume quality score
#   - JSON memory snapshot
# --------------------------------------------------------------------------- #

def _guess_role_and_company(job_text):
    """Best-effort scrape of role + company from a job poster's first lines."""
    role, company = "", ""
    for raw in job_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("---"):
            continue
        m = re.match(r"^Job Title\s*:\s*(.+)$", line, re.IGNORECASE)
        if m and not role:
            role = m.group(1).strip()
            continue
        m = re.match(r"^Company\s*:\s*(.+)$", line, re.IGNORECASE)
        if m and not company:
            company = m.group(1).strip()
            continue
        if role and company:
            break
    return role or "the advertised role", company or "your team"


def _guess_candidate_name(resume_text):
    for raw in resume_text.splitlines():
        line = raw.strip()
        m = re.match(r"^Name\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return "[Your Name]"


def generate_cover_letter(job_text, resume_text, matched, missing):
    role, company = _guess_role_and_company(job_text)
    name = _guess_candidate_name(resume_text)
    top_matches = matched[:5] if matched else []
    top_growth = missing[:3] if missing else []

    paragraphs = [
        f"Dear Hiring Team at {company},",
        "",
        f"I am writing to apply for the {role} position. I am a final-year computer "
        f"science student with hands-on experience in the areas you describe, and I "
        f"would welcome the chance to contribute to {company}.",
        "",
    ]

    if top_matches:
        skills_phrase = ", ".join(top_matches[:-1]) + (f", and {top_matches[-1]}" if len(top_matches) > 1 else top_matches[0])
        paragraphs.append(
            f"My most directly relevant strengths include {skills_phrase}. "
            "I have applied these in academic and self-directed projects — "
            "happy to walk through the code and the trade-offs in an interview."
        )
        paragraphs.append("")

    if top_growth:
        growth_phrase = ", ".join(top_growth)
        paragraphs.append(
            f"I am also actively building experience in {growth_phrase}, "
            "and I see this role as a chance to apply that work in a real team."
        )
        paragraphs.append("")

    paragraphs += [
        f"Thank you for considering my application. I would be glad to discuss how "
        f"I can contribute to {company} and to share specific examples from my "
        "projects that match your requirements.",
        "",
        "Sincerely,",
        name,
    ]
    return "\n".join(paragraphs) + "\n"


# --- Project-to-JD mapping ------------------------------------------------- #

_PROJECT_HEADER_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")


def _split_resume_projects(resume_text):
    """Heuristic split: find a 'Projects:' section and group bullets per project."""
    lines = resume_text.splitlines()
    projects = []
    in_projects = False
    current = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if re.match(r"^Projects\s*:?\s*$", stripped, re.IGNORECASE):
            in_projects = True
            continue
        if not in_projects:
            continue
        # Stop when we hit another major section header.
        if re.match(r"^[A-Z][A-Za-z ]+:\s*$", stripped) and not stripped.lower().startswith("project"):
            break

        m = _PROJECT_HEADER_RE.match(line)
        if m:
            if current:
                projects.append(current)
            current = {"title": m.group(1).strip(), "body": ""}
        elif current is not None and stripped:
            current["body"] += " " + stripped

    if current:
        projects.append(current)
    return projects


def generate_project_mapping(resume_text, job_skills):
    projects = _split_resume_projects(resume_text)
    lines = [
        "Project-to-JD Mapping",
        "=====================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
    ]
    if not projects:
        lines.append("No 'Projects:' section detected in resume. Add one to enable this report.")
        return "\n".join(lines) + "\n"
    if not job_skills:
        lines.append("No JD skills detected. Add a job poster to input_jobs/.")
        return "\n".join(lines) + "\n"

    job_set = set(job_skills)
    lines.append(f"Found {len(projects)} project(s). Showing JD coverage per project:")
    lines.append("")

    for p in projects:
        full_text = p["title"] + " " + p["body"]
        hits = sorted(set(extract_keywords(full_text)) & job_set)
        coverage = round(len(hits) / len(job_set) * 100, 1) if job_set else 0.0
        lines.append(f"- {p['title']}")
        lines.append(f"    JD coverage: {coverage}%  ({len(hits)} of {len(job_set)} JD skills)")
        lines.append(f"    Mapped skills: {', '.join(hits) if hits else '(none — consider rewriting bullets)'}")
        lines.append("")

    return "\n".join(lines) + "\n"


# --- Resume quality score -------------------------------------------------- #

def _score_resume_quality(resume_text, job_skills, resume_skills, matched):
    """Return (score_0_to_100, list_of_(category, points, max, comment))."""
    components = []

    # 1. JD alignment (40 pts)
    align_pct = (len(matched) / len(job_skills)) if job_skills else 0
    align_points = round(align_pct * 40, 1)
    components.append(("JD alignment", align_points, 40,
                       f"{len(matched)}/{len(job_skills)} JD skills present"))

    # 2. Skill breadth (15 pts) — distinct skills detected overall
    breadth_points = min(15, len(resume_skills))
    components.append(("Skill breadth", breadth_points, 15,
                       f"{len(resume_skills)} distinct skills detected"))

    # 3. Has Projects section (15 pts)
    projects = _split_resume_projects(resume_text)
    proj_points = 15 if projects else 0
    components.append(("Projects section", proj_points, 15,
                       f"{len(projects)} project(s) found" if projects else "no Projects: section"))

    # 4. Quantification — looks for digits in bullets (10 pts)
    bullet_lines = [l for l in resume_text.splitlines() if l.strip().startswith("-")]
    quantified = sum(1 for l in bullet_lines if re.search(r"\d", l))
    quant_ratio = (quantified / len(bullet_lines)) if bullet_lines else 0
    quant_points = round(quant_ratio * 10, 1)
    components.append(("Quantified bullets", quant_points, 10,
                       f"{quantified}/{len(bullet_lines)} bullets contain numbers"))

    # 5. Contact / GitHub link (10 pts)
    contact_points = 0
    if re.search(r"\bgithub\.com/", resume_text, re.IGNORECASE):
        contact_points += 5
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", resume_text):
        contact_points += 5
    components.append(("Contact + GitHub", contact_points, 10,
                       f"{contact_points}/10 (email + github.com link expected)"))

    # 6. Length sanity (10 pts) — penalize very short or very long
    word_count = len(resume_text.split())
    if 200 <= word_count <= 800:
        length_points = 10
        length_note = f"{word_count} words (good range)"
    elif word_count < 200:
        length_points = round(word_count / 200 * 10, 1)
        length_note = f"{word_count} words (too short — flesh out projects)"
    else:
        length_points = max(0, 10 - (word_count - 800) // 100)
        length_note = f"{word_count} words (too long — tighten to one page)"
    components.append(("Length sanity", length_points, 10, length_note))

    total = round(sum(p for _, p, _, _ in components), 1)
    return total, components


def generate_resume_quality_report(resume_text, job_skills, resume_skills, matched):
    total, components = _score_resume_quality(resume_text, job_skills, resume_skills, matched)
    lines = [
        "Resume Quality Score",
        "====================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        f"Overall: {total} / 100",
        "",
        "Breakdown:",
    ]
    for category, points, maximum, comment in components:
        lines.append(f"  {category:<22} {points:>5} / {maximum:<3}   {comment}")
    return "\n".join(lines) + "\n"


# --- JSON memory snapshot -------------------------------------------------- #

def write_memory_snapshot(payload):
    path = os.path.join(TRACKER_DIR, "memory.json")
    os.makedirs(TRACKER_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True, default=str)
    return path


# --------------------------------------------------------------------------- #
# Phase 5: orchestration + final combined report + CLI menu
# --------------------------------------------------------------------------- #

def build_final_report(sections):
    """Concatenate report sections into a single timestamped artifact."""
    header = [
        "CareerPrep Job-Hunting Agent — Final Report",
        "===========================================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
    ]
    body_parts = []
    for title, content in sections:
        body_parts.append(f"\n\n##### {title} #####\n")
        body_parts.append(content)
    return "\n".join(header) + "".join(body_parts)


def run_analysis(verbose=True):
    """Run the full analysis pipeline and write all output files."""
    ensure_folders()

    job_text, job_count, job_files = read_text_files(JOB_DIR)
    resume_text, resume_count, resume_files = read_text_files(RESUME_DIR)
    kb_text, kb_count, kb_files = read_text_files(KB_DIR)

    if verbose:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Running analysis...")
        print(f"  input_jobs/    : {job_count} file(s) {job_files}")
        print(f"  input_resumes/ : {resume_count} file(s) {resume_files}")
        print(f"  input_kb/      : {kb_count} file(s) {kb_files}")

    if job_count == 0 or resume_count == 0 or kb_count == 0:
        print("\nMissing inputs. Copy a sample to get started:")
        print(f"  cp {SAMPLES_DIR}/sample_job_poster.txt {JOB_DIR}/")
        print(f"  cp {SAMPLES_DIR}/sample_resume.txt     {RESUME_DIR}/")
        print(f"  cp {SAMPLES_DIR}/sample_kb.txt         {KB_DIR}/")
        return None

    job_skills = extract_keywords(job_text)
    resume_skills = extract_keywords(resume_text)
    matched, missing, score = compare_skills(job_skills, resume_skills)

    job_report = generate_job_analysis(job_files, job_skills)
    gap_report = generate_skill_gap_report(job_skills, resume_skills, matched, missing, score)
    resume_suggestions = generate_resume_suggestions(matched, missing)
    interview_questions = generate_interview_questions(job_skills, kb_text)
    cover_letter = generate_cover_letter(job_text, resume_text, matched, missing)
    project_mapping = generate_project_mapping(resume_text, job_skills)
    quality_report = generate_resume_quality_report(resume_text, job_skills, resume_skills, matched)
    quality_score, _ = _score_resume_quality(resume_text, job_skills, resume_skills, matched)

    create_tracker_if_missing()
    reminders = generate_reminders()

    final_report = build_final_report([
        ("JOB ANALYSIS", job_report),
        ("SKILL GAP", gap_report),
        ("RESUME QUALITY SCORE", quality_report),
        ("TAILORED RESUME SUGGESTIONS", resume_suggestions),
        ("PROJECT-TO-JD MAPPING", project_mapping),
        ("INTERVIEW QUESTIONS", interview_questions),
        ("COVER LETTER DRAFT", cover_letter),
        ("APPLICATION REMINDERS", reminders),
    ])

    save_text(os.path.join(OUTPUT_DIR, "job_analysis_report.txt"), job_report)
    save_text(os.path.join(OUTPUT_DIR, "skill_gap_report.txt"), gap_report)
    save_text(os.path.join(OUTPUT_DIR, "tailored_resume_suggestions.txt"), resume_suggestions)
    save_text(os.path.join(OUTPUT_DIR, "interview_questions.txt"), interview_questions)
    save_text(os.path.join(OUTPUT_DIR, "cover_letter.txt"), cover_letter)
    save_text(os.path.join(OUTPUT_DIR, "project_mapping.txt"), project_mapping)
    save_text(os.path.join(OUTPUT_DIR, "resume_quality_score.txt"), quality_report)
    save_text(os.path.join(OUTPUT_DIR, "final_agent_report.txt"), final_report)
    save_text(REMINDERS_PATH, reminders)

    memory_path = write_memory_snapshot({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "job_files": job_files,
            "resume_files": resume_files,
            "kb_files": kb_files,
        },
        "job_skills": job_skills,
        "resume_skills": resume_skills,
        "matched": matched,
        "missing": missing,
        "match_score": score,
        "resume_quality_score": quality_score,
        "tracked_applications": len(read_tracker()),
    })

    if verbose:
        print(f"\nMatch score: {score}%  ({len(matched)} matched, {len(missing)} missing)")
        print(f"Resume quality score: {quality_score} / 100")
        print(f"Tracked applications: {len(read_tracker())}")
        print(f"\nWrote 8 files in outputs/, plus tracker/reminders.txt and {memory_path}.")

    return {
        "score": score,
        "matched": matched,
        "missing": missing,
        "job_skills": job_skills,
        "resume_skills": resume_skills,
        "resume_quality_score": quality_score,
    }


# --------------------------------------------------------------------------- #
# CLI menu
# --------------------------------------------------------------------------- #

def _prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def _menu_run_analysis():
    run_analysis(verbose=True)


def _menu_add_application():
    print("\nAdd a new application (Ctrl-C to cancel)")
    company = _prompt("Company")
    if not company:
        print("Company is required. Cancelled.")
        return
    role = _prompt("Role")
    if not role:
        print("Role is required. Cancelled.")
        return
    source = _prompt("Source (LinkedIn / Rozee / Job Fair / ...)", "")
    print(f"Status options: {', '.join(sorted(VALID_STATUSES))}")
    status = _prompt("Status", "Not Applied")
    if status not in VALID_STATUSES:
        print(f"Invalid status. Cancelled.")
        return
    applied_date = _prompt("Applied date (YYYY-MM-DD, blank if none)")
    interview_date = _prompt("Interview date (YYYY-MM-DD, blank if none)")
    follow_up_date = _prompt("Follow-up date (YYYY-MM-DD, blank if none)")
    next_action = _prompt("Next action")
    notes = _prompt("Notes")

    new_id = add_application(
        company=company, role=role, source=source, status=status,
        applied_date=applied_date, interview_date=interview_date,
        follow_up_date=follow_up_date, next_action=next_action, notes=notes,
    )
    print(f"Added {new_id}.")


def _menu_show_applications():
    rows = read_tracker()
    if not rows:
        print("\nNo applications tracked yet.")
        return
    print(f"\n{len(rows)} application(s):")
    for r in rows:
        print(f"  {r['application_id']:<8} {r['company']:<20} {r['role']:<30} "
              f"{r['status']:<22} interview={r.get('interview_date','-') or '-'}")


def _menu_show_reminders():
    create_tracker_if_missing()
    reminders = generate_reminders()
    save_text(REMINDERS_PATH, reminders)
    print()
    print(reminders)


def _menu_update_status():
    rows = read_tracker()
    if not rows:
        print("\nNo applications to update.")
        return
    _menu_show_applications()
    app_id = _prompt("\nApplication ID to update")
    if not app_id:
        print("Cancelled.")
        return
    print(f"Status options: {', '.join(sorted(VALID_STATUSES))}")
    new_status = _prompt("New status")
    if new_status not in VALID_STATUSES:
        print("Invalid status. Cancelled.")
        return
    extra = {"status": new_status}
    if new_status == "Interview Scheduled":
        d = _prompt("Interview date (YYYY-MM-DD)")
        if d:
            extra["interview_date"] = d
    if new_status == "Applied":
        d = _prompt("Applied date (YYYY-MM-DD)", datetime.now().strftime("%Y-%m-%d"))
        f = _prompt("Follow-up date (YYYY-MM-DD)")
        extra["applied_date"] = d
        if f:
            extra["follow_up_date"] = f
    ok = update_application(app_id, **extra)
    print(f"Updated {app_id}." if ok else f"No application with id {app_id}.")


MENU = """
=================================================
CareerPrep Job-Hunting Agent
=================================================
  1) Run full analysis (read inputs, write outputs)
  2) Add a new application
  3) Show all applications
  4) Update application status
  5) Show reminders
  q) Quit
"""


def run_menu():
    ensure_folders()
    create_tracker_if_missing()
    actions = {
        "1": _menu_run_analysis,
        "2": _menu_add_application,
        "3": _menu_show_applications,
        "4": _menu_update_status,
        "5": _menu_show_reminders,
    }
    while True:
        print(MENU)
        try:
            choice = input("Choose: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        if choice in ("q", "quit", "exit"):
            print("Bye.")
            return
        action = actions.get(choice)
        if action is None:
            print(f"Unknown option: {choice!r}")
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\nCancelled.")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("--menu", "-m"):
        run_menu()
    else:
        run_analysis(verbose=True)


if __name__ == "__main__":
    main()
