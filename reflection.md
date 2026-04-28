# Reflection

## What I built

A file-driven Job-Hunting Agent that grew from a 600-line single-file CLI into a
production-ready web app:

- **`app.py`** (~1500 lines) — agent core: file I/O, keyword extraction, skill matching,
  resume tailoring, interview questions, tracker, reminders, calendar export, PDF export,
  status history log, OpenRouter LLM integration with template fallback.
- **`ui.py`** (~470 lines) — Streamlit web UI: drag-drop uploads, click-to-analyze,
  rich rendering (chips, progress bars, side-by-side rewrite cards, charts), tracker CRUD
  via forms, dashboard tab.
- **Launchers + config** — `start.command` / `start.bat` for one-click local runs;
  `.streamlit/config.toml` + `runtime.txt` for Streamlit Cloud deployment.

## How I built it (phased delivery)

I split the work into 12 phases. Each phase produced a runnable agent that the previous
phase's tests still passed against.

1. **Skeleton** — folders, file-reading helpers, sample inputs, README.
2. **Analysis core** — keyword extraction with **word-boundary regex** (so `"oop"` does not match `"loop"`), skill comparison, gap report.
3. **Resume tailoring + interview questions** — JD-aligned bullet templates, technical + HR + KB-derived questions, plus reverse questions to ask the interviewer.
4. **Tracker + reminders** — CSV with status validation, auto-incrementing IDs, and **date-aware urgency tags** (`[TODAY]`, `[TOMORROW]`, `[OVERDUE by Nd]`, `[in Nd]`).
5. **Orchestration + CLI** — `run_analysis()` writes everything; `--menu` flag opens an interactive shell.
6. **Uniqueness features** — PDF reading, cover-letter generator, project-to-JD mapping, resume quality score, JSON memory snapshot.
7. **Polish** — README, reflection, smoke test, GitHub push.
8. **Robustness** — encoding fallback (utf-8 → latin-1), scanned-PDF detection, 5MB file cap, `.docx` warnings, flexible date parsing (5 formats), tracker auto-backup, project-section aliases, expanded `KEYWORDS` list (~120 entries), bigram surfacing for JD terms not in the keyword list.
9. **Streamlit UI** — sidebar uploads, one-click analyze, inline collapsible reports, tracker tab with form-based CRUD, dashboard tab with status funnel chart and upcoming-dates table.
10. **Launchers + extras** — `start.command` (macOS), `start.bat` (Windows), `.ics` calendar export, PDF export via `fpdf2` with graceful fallback, status history log.
11. **LLM integration via OpenRouter** — OpenAI-compatible client; cover letter, interview questions (8 technical + 5 HR), and resume bullet rewrites all become personalized when an API key is set; clean template fallback when not.
12. **Production hardening + Streamlit Cloud deploy** — `st.secrets` support alongside `.env`, privacy banner, `try/except` around the run button, dashboard rewrite (chips, progress bars, side-by-side Before/After rewrite cards, charts), KB folder made optional, scrubbed personal data, deployed on Streamlit Cloud.

## What I tested

- Empty-input and populated-input paths both behave cleanly.
- Word-boundary regex verified against false-positive cases (`"oop"` in `"loop"`, `"api"` in `"capital"`).
- Flexible date parser tested with 7 formats including `5/3/2026`, `May 3 2026`, `3 May 2026`.
- LLM path tested against the bundled Ahmed Khan sample resume — produced 7 ORIGINAL/REWRITE/WHY blocks with believable improvements and no fabricated experience.
- Template fallback tested by unsetting the key — all reports still generate, marked `Mode: template`.
- Streamlit UI smoke-tested with `curl http://localhost:8765` returning HTTP 200 and parsers unit-tested directly against generated reports.
- KB-optional path verified: with `input_kb/` empty, interview questions report cleanly omits Section C instead of leaving a stub.

## Production-readiness checklist (Phase 12)

Before pushing the deployable version I:

- Removed the personal resume PDF and JD PDF that had drifted into `input_*` folders during local testing.
- Regenerated `outputs/` and `tracker/` from the bundled sample data so the public repo only contains demo content.
- Added `.streamlit/config.toml` (theme, server hardening, no telemetry, `showErrorDetails=false` for public visitors) and `.streamlit/secrets.toml.example`.
- Switched the LLM client to read secrets dynamically — `st.secrets` first, then env / `.env` — so the same code works locally and on Streamlit Cloud without changes.
- Added a public-demo privacy banner clarifying that the tracker is shared across visitors.
- Added a `try/except` boundary around `run_analysis()` in the UI so an unhandled error never shows a stack trace to a stranger.
- Pinned `python-3.11` via `runtime.txt`.

## What I would improve next

- **Per-session tracker state** — currently the tracker is a single shared CSV. For a public deploy this means visitors see each other's entries. The right fix is to scope the tracker into `st.session_state` per visitor, with disk persistence behind a "save to my account" affordance.
- **BYOK (bring-your-own-key) UI option** — let visitors paste their own OpenRouter key into the sidebar so the LLM cost lands on them, not the deploy owner.
- **Real authentication** — for any private use, the deploy needs Streamlit's auth or a reverse proxy. Right now the URL is wide open.
- **Deeper resume parsing** — split sections more reliably (Education / Experience / Projects / Skills) instead of just "Projects:". Would sharpen the project-to-JD mapping.
- **OCR fallback for scanned PDFs** — currently the agent warns when extraction looks empty; integrating Tesseract would let it actually read those files.

## What I learned

- **Sweat the regex.** The manual's starter code used naïve substring matching (`"api" in text`), which silently misfires on words like `"capital"`. Using `\b` boundaries and special-casing `c++` / `scikit-learn` was a ten-line fix that meaningfully improved precision.
- **Phase the build.** Shipping a runnable agent at the end of every phase made it easy to verify that nothing regressed when I added the next layer. By Phase 4 I could already demo the agent end-to-end; by Phase 9 it had a web UI; by Phase 12 it was deployable.
- **LLM-first / template-fallback is the right pattern.** Every LLM call has a try/except that returns `None`, and every caller has a deterministic template path. The app never crashes when the LLM is offline, and the same code runs the lab demo (template) and the production deploy (LLM-tailored).
- **Personal data drift is real.** I caught my own resume PDF in `input_resumes/` during the pre-deploy audit because I'd been testing with my actual files. The lesson: any folder that accepts user input should be scrubbed and re-seeded with sample data before any public push.
- **Trust the user on dates.** I almost overengineered timezone handling; defaulting to `date.today()` and accepting a handful of common date formats is enough for a tracker.
