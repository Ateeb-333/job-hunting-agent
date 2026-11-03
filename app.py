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

try:
    from fpdf import FPDF
    _FPDF_AVAILABLE = True
except ImportError:
    _FPDF_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Phase 11: LLM integration via OpenRouter (OpenAI-compatible API)
# --------------------------------------------------------------------------- #

def _load_dotenv(path=".env"):
    """Lightweight .env loader (no external dep). Reads KEY=VALUE per line."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _get_secret(key, default=""):
    """
    Read a secret from (in order): Streamlit Cloud secrets -> env var (incl. .env) -> default.
    This lets the same code work locally (with .env) and on Streamlit Cloud
    (with the dashboard's Secrets pane).
    """
    # 1. Streamlit secrets (only available when running under streamlit)
    try:
        import streamlit as _st  # noqa: WPS433
        if hasattr(_st, "secrets") and key in _st.secrets:
            return str(_st.secrets[key]).strip()
    except Exception:
        pass
    # 2. Environment / .env
    return os.environ.get(key, default).strip()


OPENROUTER_API_KEY = _get_secret("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = _get_secret("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

try:
    from openai import OpenAI
    _OPENAI_SDK_AVAILABLE = True
except ImportError:
    _OPENAI_SDK_AVAILABLE = False

def _current_api_key():
    return _get_secret("OPENROUTER_API_KEY", "") or OPENROUTER_API_KEY


def _current_model():
    return _get_secret("OPENROUTER_MODEL", "") or OPENROUTER_MODEL


def llm_available():
    """True if we have both an API key (env / .env / st.secrets) and the openai SDK."""
    return bool(_current_api_key()) and _OPENAI_SDK_AVAILABLE


def _build_llm_client():
    if not llm_available():
        return None
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=_current_api_key(),
        default_headers={
            "HTTP-Referer": "https://github.com/Ateeb-333/job-hunting-agent",
            "X-Title": "CareerPrep Job-Hunting Agent",
        },
    )


def call_llm(prompt, system="You are a helpful assistant.",
             model=None, max_tokens=800, temperature=0.4):
    """
    Single-shot LLM call via OpenRouter. Returns text on success or None on
    any failure (missing key, network error, API error). The caller must
    fall back to the template path on None.
    """
    client = _build_llm_client()
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=model or _current_model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if resp.choices and resp.choices[0].message:
            return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"  ! LLM call failed ({type(e).__name__}): {e}")
    return None

JOB_DIR = "input_jobs"
RESUME_DIR = "input_resumes"
KB_DIR = "input_kb"
OUTPUT_DIR = "outputs"
TRACKER_DIR = "tracker"
SAMPLES_DIR = "samples"

ALL_FOLDERS = [JOB_DIR, RESUME_DIR, KB_DIR, OUTPUT_DIR, TRACKER_DIR, SAMPLES_DIR]

# Skills/keywords the agent recognizes. Multi-word entries are matched as phrases.
# Expanded for Phase 8 to cover modern AI/ML, data, web, devops, and soft-skill terms.
KEYWORDS = [
    # Languages
    "python", "java", "c++", "c#", "javascript", "typescript", "go", "rust",
    "kotlin", "swift", "ruby", "php", "scala", "r", "bash", "shell scripting",
    # Web
    "html", "css", "react", "next.js", "vue", "angular", "node", "express",
    "flask", "django", "fastapi", "streamlit", "tailwind",
    # Data / ML / AI
    "machine learning", "deep learning", "data preprocessing", "data analysis",
    "data visualization", "pandas", "numpy", "scikit-learn", "tensorflow",
    "pytorch", "jupyter", "matplotlib", "seaborn", "huggingface",
    "transformers", "nlp", "computer vision", "reinforcement learning",
    "feature engineering", "model deployment", "mlops",
    # LLM / GenAI
    "prompt engineering", "rag", "vector database", "embeddings", "fine-tuning",
    "llm", "openai", "anthropic", "langchain", "llama",
    # Databases / storage
    "sql", "nosql", "postgresql", "mysql", "mongodb", "redis", "sqlite",
    "elasticsearch", "snowflake", "bigquery", "database", "data warehouse",
    # APIs / backend
    "api", "rest", "graphql", "grpc", "microservices", "websocket", "etl",
    # DevOps / cloud
    "docker", "kubernetes", "aws", "gcp", "azure", "terraform", "ansible",
    "ci/cd", "github actions", "jenkins", "linux", "nginx",
    # Tools / version control
    "git", "github", "gitlab", "bitbucket", "version control", "jira",
    "agile", "scrum", "vs code", "intellij",
    # Testing / quality
    "unit testing", "pytest", "jest", "selenium", "tdd",
    # Foundations
    "oop", "data structures", "algorithms", "design patterns", "system design",
    "object-oriented programming",
    # Soft skills
    "communication", "problem solving", "teamwork", "leadership",
    "critical thinking", "time management", "collaboration", "presentation",
    # Misc
    "excel", "powerpoint", "tableau", "power bi", "figma",
]


# --------------------------------------------------------------------------- #
# File I/O helpers
# --------------------------------------------------------------------------- #

def ensure_folders():
    for folder in ALL_FOLDERS:
        os.makedirs(folder, exist_ok=True)


_MAX_FILE_BYTES = 5 * 1024 * 1024     # 5 MB hard cap per input file
_SCANNED_PDF_THRESHOLD = 50            # avg chars per page below which we suspect a scan


def _read_pdf(path):
    """Extract text from a PDF. Detects scanned/image-only PDFs and warns the user."""
    if not _PDF_AVAILABLE:
        print(f"  ! Skipping {os.path.basename(path)} (install pypdf to enable PDF reading)")
        return ""
    try:
        reader = PdfReader(path)
        pages = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(pages)
        if pages:
            avg = sum(len(p) for p in pages) / len(pages)
            if avg < _SCANNED_PDF_THRESHOLD:
                print(
                    f"  ! {os.path.basename(path)} produced only {int(avg)} chars/page — "
                    f"likely a scanned/image PDF. Try copy-pasting the text into a .txt file "
                    f"or running OCR first."
                )
        return text
    except Exception as e:
        print(f"  ! Failed to read {os.path.basename(path)}: {e}")
        return ""


def _read_text_with_fallback(path):
    """Read a .txt file with utf-8, falling back to latin-1 if needed."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        print(f"  ! {os.path.basename(path)} is not UTF-8, retrying as latin-1.")
        with open(path, "r", encoding="latin-1") as f:
            return f.read()


def read_text_files(folder):
    """
    Read every .txt and .pdf file in `folder`. Returns (combined_text, file_count, file_list).

    Robustness:
      - utf-8 -> latin-1 fallback for stubborn .txt files.
      - Warns on .docx / .doc (unsupported, suggests conversion).
      - Warns on files larger than _MAX_FILE_BYTES.
      - Detects scanned/empty PDFs and tells the user how to recover.
    """
    combined_text = ""
    files_read = []
    if not os.path.isdir(folder):
        return combined_text, 0, files_read

    for filename in sorted(os.listdir(folder)):
        lower = filename.lower()
        path = os.path.join(folder, filename)
        if filename.startswith("."):
            continue  # skip hidden files like .DS_Store

        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > _MAX_FILE_BYTES:
            mb = size / 1024 / 1024
            print(f"  ! {filename} is {mb:.1f} MB — skipping (max 5 MB). "
                  f"Trim the file and try again.")
            continue

        if lower.endswith(".txt"):
            content = _read_text_with_fallback(path)
        elif lower.endswith(".pdf"):
            content = _read_pdf(path)
            if not content.strip():
                continue
        elif lower.endswith((".docx", ".doc")):
            print(f"  ! {filename} is a Word file — not supported. "
                  f"Save it as PDF or copy the text into a .txt file.")
            continue
        elif os.path.isdir(path):
            continue
        else:
            print(f"  ! {filename} has an unsupported extension — skipping.")
            continue

        combined_text += f"\n\n--- FILE: {filename} ---\n{content}"
        files_read.append(filename)

    return combined_text, len(files_read), files_read


def surface_extra_jd_terms(job_text, known_skills, top_n=10):
    """
    Surface frequent capitalized phrases (1-3 words) in the JD that are NOT
    already in our keyword list. Lightweight bigram/trigram detection so a
    layman uploading a JD with role-specific terms (e.g. 'Vector Database',
    'Snowflake', 'RAG Pipeline') gets *some* signal even if they're not in
    our static list.
    """
    if not job_text:
        return []
    # Find capitalized 1-3 word sequences (proper nouns, technical terms).
    pattern = r"\b([A-Z][A-Za-z0-9+#.\-]{1,}(?:\s+[A-Z][A-Za-z0-9+#.\-]{1,}){0,2})\b"
    matches = re.findall(pattern, job_text)
    counts = {}
    known_lower = {s.lower() for s in known_skills}
    stop_phrases = {
        "Job", "Title", "Company", "Location", "Type", "About", "Role",
        "Responsibilities", "Required", "Skills", "Nice", "Have", "Education",
        "How", "Apply", "We", "You", "The", "This", "Our", "Your",
        "Send", "Strong", "Familiarity", "Working", "Knowledge", "Comfortable",
        "Good", "Internship", "Months", "Remote", "Lahore", "Karachi", "Islamabad",
        "BS", "Computer", "Science", "Software", "Engineering", "Field", "Year",
    }
    for raw in matches:
        phrase = raw.strip()
        if phrase.lower() in known_lower:
            continue
        # drop single-word stop phrases / very short tokens
        if phrase in stop_phrases:
            continue
        if len(phrase) < 3:
            continue
        counts[phrase] = counts.get(phrase, 0) + 1
    # keep terms that appear at least twice (more likely to be meaningful)
    ranked = sorted(
        ((p, c) for p, c in counts.items() if c >= 2),
        key=lambda x: (-x[1], x[0]),
    )
    return [p for p, _ in ranked[:top_n]]


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

def generate_job_analysis(job_files, job_skills, extra_terms=None):
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
        lines.append("  (none detected — the JD may use uncommon terminology)")

    if extra_terms:
        lines += [
            "",
            "Other notable terms in the JD (not in the agent's known skill list):",
        ]
        for term in extra_terms:
            lines.append(f"  ? {term}")
        lines.append("  (consider whether any of these are skills you should highlight)")

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


def _llm_resume_rewrites(resume_text, job_text, matched, missing):
    """Ask the LLM to rewrite the candidate's actual bullets in JD vocabulary."""
    if not llm_available():
        return None
    system = (
        "You are an expert resume writer. You rewrite a candidate's existing "
        "resume bullets so they mirror the language of a target job posting, "
        "without inventing experience. You preserve facts but tighten phrasing, "
        "add JD vocabulary where it fits, and quantify when the original bullet "
        "implies a number. You never fabricate metrics."
    )
    prompt = f"""Below is a candidate's resume and a target job posting.

Pull out 5-8 bullet points from the resume that could be rewritten to better
match the JD. For each one, output:

  ORIGINAL:  <the bullet exactly as it appears in the resume>
  REWRITE:   <improved version that mirrors JD vocabulary>
  WHY:       <one short sentence explaining the change>

Then list 3-5 BULLETS TO ADD: skills the JD wants but the resume doesn't show.
For each, propose a short bullet that the candidate could earn quickly through
a small project.

Rules:
- Never invent experience the candidate doesn't already have.
- Keep ORIGINAL exactly as written. Don't paraphrase.
- REWRITE must be the same factual content with stronger wording.
- Output plain text, no markdown headers.

JOB POSTING (excerpt):
\"\"\"
{job_text[:2500]}
\"\"\"

RESUME:
\"\"\"
{resume_text[:3000]}
\"\"\"

JD skills the candidate already has: {', '.join(matched) if matched else 'none'}
JD skills the candidate is missing: {', '.join(missing) if missing else 'none'}
"""
    return call_llm(prompt, system=system, max_tokens=1100, temperature=0.4)


def generate_resume_suggestions(matched, missing, resume_text="", job_text=""):
    """LLM-first resume rewrites with template fallback."""
    llm_text = _llm_resume_rewrites(resume_text, job_text, matched, missing) if resume_text else None

    if llm_text:
        return (
            "Tailored Resume Suggestions\n"
            "===========================\n"
            f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            "Mode: LLM-tailored (rewrites your actual bullets in JD vocabulary)\n\n"
            f"{llm_text}\n"
        )

    lines = [
        "Tailored Resume Suggestions",
        "===========================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "Mode: template (set OPENROUTER_API_KEY for personalized rewrites)",
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


def _llm_technical_questions(job_text, resume_text, job_skills, missing):
    """Use the LLM to pick 8 high-signal technical questions tailored to JD + resume."""
    if not llm_available():
        return None
    system = (
        "You are a senior engineering interviewer. You pick interview questions "
        "that test depth, not breadth. You pick questions that are likely to "
        "actually be asked for the given role and that probe both strengths and "
        "weak spots in the candidate's resume."
    )
    prompt = f"""Pick exactly 8 interview questions for this candidate applying to this role.

Rules:
- Mix: 5 should target skills the candidate has (test depth), 3 should target gaps (test honesty + learning ability).
- Each question must be specific (not "what is Python?") and reference real engineering practice.
- For 2 of the 8, include a one-line "What I'd watch for in the answer:" note for the candidate.
- Output as a numbered list. No preamble, no closing.

JOB POSTING (excerpt):
\"\"\"
{job_text[:2000]}
\"\"\"

RESUME (excerpt):
\"\"\"
{resume_text[:2000]}
\"\"\"

Skills the candidate already has from the JD: {', '.join(job_skills[:20])}
Skills the candidate is missing: {', '.join(missing[:10])}
"""
    return call_llm(prompt, system=system, max_tokens=900, temperature=0.4)


def _llm_hr_questions(job_text, resume_text):
    """LLM-picked behavioural questions tailored to the role."""
    if not llm_available():
        return None
    system = "You write tight, role-aware behavioural interview questions."
    prompt = f"""Pick 5 behavioural questions tailored to this specific role and candidate.

Rules:
- Avoid generic openers ("Tell me about yourself"). Pick questions a real interviewer for THIS role would ask.
- Output as a numbered list. No preamble, no closing.
- Each question should be answerable in 2-3 minutes using STAR.

JOB POSTING (excerpt):
\"\"\"
{job_text[:1500]}
\"\"\"

RESUME (excerpt):
\"\"\"
{resume_text[:1500]}
\"\"\"
"""
    return call_llm(prompt, system=system, max_tokens=400, temperature=0.5)


def generate_interview_questions(job_skills, kb_text, job_text="", resume_text="", missing=None):
    """
    Tailored interview questions. If LLM is available, picks 8 technical + 5 HR
    questions specific to the candidate. Falls back to the per-skill template
    list when the LLM is not configured.
    """
    missing = missing or []

    lines = [
        "Interview Questions",
        "===================",
        f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Mode: {'LLM-tailored' if llm_available() else 'template (set OPENROUTER_API_KEY for tailored questions)'}",
        "",
    ]

    llm_tech = _llm_technical_questions(job_text, resume_text, job_skills, missing) if job_text else None
    llm_hr = _llm_hr_questions(job_text, resume_text) if job_text else None

    if llm_tech:
        lines.append("A. Technical questions (LLM-picked, tailored to your resume + the JD):")
        lines.append("")
        lines.append(llm_tech)
        lines.append("")
    else:
        lines.append("A. Technical questions (derived from the job posters):")
        if job_skills:
            for s in sorted(job_skills)[:15]:    # cap template version too
                lines.append(f"  - Walk me through a project where you used {s}. What trade-offs did you make?")
                lines.append(f"  - What's a common pitfall with {s} that you've personally hit?")
        else:
            lines.append("  (no JD skills detected)")

    lines.append("")
    if llm_hr:
        lines.append("B. HR / behavioural questions (LLM-tailored to this role):")
        lines.append("")
        lines.append(llm_hr)
    else:
        lines += [
            "B. HR / behavioural questions (standard set):",
            "  - Tell me about yourself.",
            "  - Why this role / why this company?",
            "  - Walk me through your strongest project end-to-end.",
            "  - Tell me about a time you disagreed with a teammate. What did you do?",
            "  - Tell me about a time you failed and what you learned.",
            "  - What's a weakness you're actively working on?",
            "  - Where do you want to be in 2-3 years?",
            "  - Why should we pick you over other candidates?",
        ]
    # Section C is only added when the user has supplied KB material.
    topics = _extract_kb_topics(kb_text) if kb_text else []
    if topics:
        lines += [
            "",
            "C. Questions inspired by your KB / course material:",
        ]
        seen = set()
        for kind, content in topics:
            if content in seen:
                continue
            seen.add(content)
            if kind == "topic":
                lines.append(f"  [Topic] {content}")
                lines.append(f"    - How would you explain '{content}' to a non-technical interviewer?")
            else:
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
    """Write tracker rows. Auto-backs up the existing file before overwriting."""
    if os.path.exists(TRACKER_PATH):
        backup = TRACKER_PATH + ".bak"
        try:
            with open(TRACKER_PATH, "r", encoding="utf-8") as src, \
                 open(backup, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        except OSError:
            pass  # backup is best-effort; never block the write
    with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKER_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in TRACKER_FIELDS})


def parse_flexible_date(raw):
    """
    Accept multiple common date formats from a layman:
      2026-05-03   (ISO)
      05/03/2026   (US M/D/Y)
      03/05/2026   (DMY)  -> ambiguous, we trust ISO first then DMY
      03-05-2026
      May 3, 2026
      3 May 2026
    Returns 'YYYY-MM-DD' string on success, '' otherwise.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    formats = [
        "%Y-%m-%d", "%Y/%m/%d",
        "%d-%m-%Y", "%d/%m/%Y",
        "%m-%d-%Y", "%m/%d/%Y",
        "%d %b %Y", "%d %B %Y",
        "%b %d, %Y", "%B %d, %Y",
        "%b %d %Y", "%B %d %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


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
        "company": company.strip(),
        "role": role.strip(),
        "source": source.strip(),
        "status": status,
        "applied_date": parse_flexible_date(applied_date) or applied_date.strip(),
        "interview_date": parse_flexible_date(interview_date) or interview_date.strip(),
        "follow_up_date": parse_flexible_date(follow_up_date) or follow_up_date.strip(),
        "next_action": next_action.strip(),
        "notes": notes.strip(),
    })
    write_tracker(rows)
    return new_id


def update_application(application_id, **fields):
    """Update fields on an existing application. Returns True if found."""
    rows = read_tracker()
    found = False
    old_status = ""
    for row in rows:
        if row.get("application_id") == application_id:
            old_status = row.get("status", "")
            for k, v in fields.items():
                if k in TRACKER_FIELDS:
                    if k in ("applied_date", "interview_date", "follow_up_date"):
                        row[k] = parse_flexible_date(v) or v
                    else:
                        row[k] = v
            found = True
            new_status = row.get("status", "")
            break
    if found:
        write_tracker(rows)
        if "status" in fields and fields["status"] != old_status:
            log_status_change(application_id, old_status, fields["status"])
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


def _template_cover_letter(job_text, resume_text, matched, missing):
    """Template-based cover letter (no LLM). Used when LLM is unavailable."""
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
        if len(top_matches) > 1:
            skills_phrase = ", ".join(top_matches[:-1]) + f", and {top_matches[-1]}"
        else:
            skills_phrase = top_matches[0]
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


def generate_cover_letter(job_text, resume_text, matched, missing):
    """
    Personalized cover letter via LLM if available, otherwise template fallback.
    """
    role, company = _guess_role_and_company(job_text)
    name = _guess_candidate_name(resume_text)

    if not llm_available():
        return _template_cover_letter(job_text, resume_text, matched, missing)

    system = (
        "You are an expert career coach who writes concise, specific cover letters "
        "for entry-level / internship candidates. You always quote one concrete "
        "project from the candidate's resume. You never invent experience that is "
        "not in the resume. You never use cliches like 'I am writing to express my "
        "interest' or 'team player'. You write in the candidate's voice."
    )
    prompt = f"""Write a 3-paragraph cover letter for this candidate applying to this role.

Constraints:
- Open with a direct first line (NOT "I am writing to express..."). Mention the role and one specific reason this company is interesting based on the JD.
- Middle paragraph: quote ONE specific project from the resume by name and explain how it maps to the JD. Use the JD's vocabulary where natural.
- Close: brief, confident, no boilerplate. End with "Sincerely," then "{name}".
- Total length: 180-260 words. No bullet points.
- Address it to "Dear Hiring Team at {company},".
- Never invent skills or experience that aren't in the resume.

JOB POSTING:
\"\"\"
{job_text[:3000]}
\"\"\"

CANDIDATE'S RESUME:
\"\"\"
{resume_text[:3000]}
\"\"\"

JD skills the candidate already has: {', '.join(matched) if matched else 'none'}
JD skills the candidate is missing: {', '.join(missing) if missing else 'none'}
"""
    text = call_llm(prompt, system=system, max_tokens=600, temperature=0.5)
    if not text:
        return _template_cover_letter(job_text, resume_text, matched, missing)

    if not text.endswith("\n"):
        text += "\n"
    return text


# --- Project-to-JD mapping ------------------------------------------------- #

_PROJECT_HEADER_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")


_PROJECT_SECTION_HEADERS = (
    "projects", "project experience", "personal projects", "academic projects",
    "selected projects", "side projects", "key projects", "portfolio",
)


def _is_project_header(line_stripped):
    low = line_stripped.lower().rstrip(":").strip()
    return low in _PROJECT_SECTION_HEADERS


def _split_resume_projects(resume_text):
    """Heuristic split: find a Projects-style section and group bullets per project."""
    lines = resume_text.splitlines()
    projects = []
    in_projects = False
    current = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if _is_project_header(stripped):
            in_projects = True
            continue
        if not in_projects:
            continue
        # Stop when we hit another major section header (line ending with ':').
        if re.match(r"^[A-Z][A-Za-z ]+:\s*$", stripped) and not _is_project_header(stripped):
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
# Phase 10: calendar export, status history log, PDF export
# --------------------------------------------------------------------------- #

STATUS_HISTORY_PATH = os.path.join(TRACKER_DIR, "status_history.log")


def log_status_change(application_id, old_status, new_status, note=""):
    """Append-only log of status transitions. Lets the user see when things changed."""
    os.makedirs(TRACKER_DIR, exist_ok=True)
    line = (
        f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{application_id}\t"
        f"{old_status or '(new)'} -> {new_status}\t{note}\n"
    )
    with open(STATUS_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def _ics_escape(text):
    """Minimal RFC 5545 escaping for TEXT values."""
    if text is None:
        return ""
    return (text.replace("\\", "\\\\")
                .replace(",", "\\,")
                .replace(";", "\\;")
                .replace("\n", "\\n"))


def _ics_event(uid, summary, dt_start, dt_end, description=""):
    fmt = "%Y%m%dT%H%M%S"
    return "\r\n".join([
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.utcnow().strftime(fmt)}Z",
        f"DTSTART:{dt_start.strftime(fmt)}",
        f"DTEND:{dt_end.strftime(fmt)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        "END:VEVENT",
    ])


def export_calendar_ics(path=None):
    """
    Build a `.ics` file from interview_date / follow_up_date in the tracker.
    Returns the output path. Layman use: import this file into Google
    Calendar / Apple Calendar / Outlook to see all upcoming actions.
    """
    if path is None:
        path = os.path.join(TRACKER_DIR, "calendar.ics")
    rows = read_tracker()

    events = []
    for row in rows:
        app_id = row.get("application_id", "?")
        company = row.get("company", "")
        role = row.get("role", "")
        next_action = row.get("next_action", "")

        idate = _parse_date(row.get("interview_date"))
        if idate:
            start = datetime.combine(idate, datetime.min.time().replace(hour=10))
            end = start.replace(hour=11)
            events.append(_ics_event(
                uid=f"{app_id}-interview@careerprep",
                summary=f"Interview: {company} — {role}",
                dt_start=start, dt_end=end,
                description=f"Application {app_id}\nNext action: {next_action}",
            ))

        fdate = _parse_date(row.get("follow_up_date"))
        if fdate:
            start = datetime.combine(fdate, datetime.min.time().replace(hour=9))
            end = start.replace(hour=9, minute=15)
            events.append(_ics_event(
                uid=f"{app_id}-followup@careerprep",
                summary=f"Follow up: {company} — {role}",
                dt_start=start, dt_end=end,
                description=f"Application {app_id}: send a follow-up note.",
            ))

    body = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CareerPrep//Job-Hunting Agent//EN",
        "CALSCALE:GREGORIAN",
        *events,
        "END:VCALENDAR",
        "",
    ])
    save_text(path, body)
    return path


def export_final_report_pdf(text=None, path=None):
    """
    Render the final combined report as a PDF. Returns the output path,
    or None if `fpdf2` is not installed or rendering fails (graceful fallback —
    the PDF is a nice-to-have and must never block other outputs).
    """
    if not _FPDF_AVAILABLE:
        return None

    if text is None:
        src = os.path.join(OUTPUT_DIR, "final_agent_report.txt")
        if not os.path.exists(src):
            return None
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()

    if path is None:
        path = os.path.join(OUTPUT_DIR, "final_agent_report.pdf")

    try:
        import textwrap
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=10)

        # FPDF (the latin1-only classic) chokes on smart quotes / em dashes /
        # the box-drawing chars our reports use. Strip to ASCII first.
        safe_text = text.encode("ascii", "replace").decode("ascii")

        for raw in safe_text.splitlines():
            if not raw.strip():
                pdf.ln(4)
                continue
            # Hard-wrap to a width that always fits, avoiding multi_cell's
            # "not enough horizontal space" edge case with long unbroken tokens.
            for chunk in textwrap.wrap(raw, width=95, break_long_words=True) or [""]:
                pdf.cell(0, 5, chunk)
                pdf.ln(5)

        pdf.output(path)
        return path
    except Exception as e:
        print(f"  ! PDF export skipped: {e}")
        return None


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

    if job_count == 0 or resume_count == 0:
        print("\nMissing required inputs. Copy a sample to get started:")
        print(f"  cp {SAMPLES_DIR}/sample_job_poster.txt {JOB_DIR}/")
        print(f"  cp {SAMPLES_DIR}/sample_resume.txt     {RESUME_DIR}/")
        print(f"  (input_kb/ is optional — drop course slide notes there for KB-derived "
              f"interview questions)")
        return None
    if kb_count == 0 and verbose:
        print("  (input_kb/ is empty — skipping KB-derived interview questions)")

    job_skills = extract_keywords(job_text)
    resume_skills = extract_keywords(resume_text)
    matched, missing, score = compare_skills(job_skills, resume_skills)

    extra_terms = surface_extra_jd_terms(job_text, KEYWORDS)

    if not job_skills and verbose:
        print("\n  ! No known skills detected in the job poster.")
        print("    Either the JD uses unusual terminology, or the file isn't really a JD.")
        if extra_terms:
            print(f"    However, these capitalized phrases stood out: {', '.join(extra_terms[:5])}")
    if not resume_skills and verbose:
        print("\n  ! No known skills detected in the resume.")
        print("    If your resume is a PDF that looks like a scanned image, "
              "the text may not be extractable. Paste the resume into a .txt file instead.")

    job_report = generate_job_analysis(job_files, job_skills, extra_terms=extra_terms)
    gap_report = generate_skill_gap_report(job_skills, resume_skills, matched, missing, score)
    resume_suggestions = generate_resume_suggestions(
        matched, missing, resume_text=resume_text, job_text=job_text,
    )
    interview_questions = generate_interview_questions(
        job_skills, kb_text,
        job_text=job_text, resume_text=resume_text, missing=missing,
    )
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

    ics_path = export_calendar_ics()
    pdf_path = export_final_report_pdf(text=final_report)

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
        print(f"Calendar:    {ics_path}  (import into Google / Apple Calendar)")
        if pdf_path:
            print(f"PDF report:  {pdf_path}")
        else:
            print("PDF report:  skipped (install fpdf2 to enable)")

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
