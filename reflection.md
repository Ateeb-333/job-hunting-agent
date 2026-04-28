# Reflection

## What I built

A file-driven Job-Hunting Agent in a single `app.py` (~600 lines, stdlib + optional `pypdf`).
The agent reads three input folders (`input_jobs/`, `input_resumes/`, `input_kb/`), runs
keyword extraction and skill-gap analysis, and produces eight output files plus an
application tracker and a JSON memory snapshot. There is also an interactive CLI menu
(`python app.py --menu`) for managing applications without hand-editing the CSV.

## How I built it (phased delivery)

I split the work into seven phases so each phase produced a runnable agent:

1. **Skeleton** — folders, file-reading helpers, sample inputs, README.
2. **Analysis core** — keyword extraction with **word-boundary regex** (so `"oop"` does not match `"loop"`), skill comparison, gap report.
3. **Resume tailoring + interview questions** — JD-aligned bullet templates, technical + HR + KB-derived questions, plus reverse questions to ask the interviewer.
4. **Tracker + reminders** — CSV with status validation, auto-incrementing IDs, and **date-aware urgency tags** (`[TODAY]`, `[TOMORROW]`, `[OVERDUE by Nd]`, `[in Nd]`).
5. **Orchestration + CLI** — `run_analysis()` writes everything; `--menu` flag opens an interactive shell.
6. **Uniqueness features** — PDF reading, cover-letter generator, project-to-JD mapping, resume quality score, JSON memory snapshot.
7. **Polish** — README, this reflection, final smoke test.

## What I tested

- Empty-input path: agent prints clear instructions and exits cleanly.
- Populated-input path: against the bundled samples, the agent reports a 61.5% match score with 16 matched / 10 missing skills.
- `add_application()` auto-increments IDs (`APP-002`, `APP-003`, ...) and rejects unknown statuses.
- Date-aware reminders tested with mixed dates: produced `[TOMORROW]` for an interview the next day, `[in 3d]` for a follow-up, plain status note for `Not Applied`.
- Resume quality score breakdown (74.9/100 on the sample) called out the actual weakness (only 3 of 18 bullets contained numbers).
- Project-to-JD mapping correctly identified that the Image Classifier project covered only 1/26 JD skills — useful, because that's exactly the project a real student should rewrite or drop.

## What I would improve next

- **LLM-backed keyword extraction**: the current regex list is curated and brittle. A small LLM call against a job poster would catch role-specific terms ("vector database", "RAG") that aren't in the static list.
- **Actual cover-letter quality**: the template is solid scaffolding, but a real student would replace the body with project-specific evidence. An LLM-driven version that quotes the strongest matching project would be a clear next step.
- **Streamlit dashboard**: the data is all there in `tracker/memory.json` and `applications.csv` — wiring it into a small Streamlit app would make the whole thing demo-able without a terminal.
- **PDF export of the final report**: easy with `reportlab`, would make the deliverable look more polished.

## What I learned

- **Sweat the regex**: the manual's starter code used naïve substring matching (`"api" in text`), which silently mis-fires on words like `"capital"`. Using `\b` boundaries and special-casing `c++` / `scikit-learn` was a 10-line fix that meaningfully improved precision.
- **Phase the build**: shipping a runnable agent at the end of every phase made it easy to verify that nothing regressed when I added the next layer. By Phase 4, I could already demo the agent end-to-end.
- **Trust the user on dates**: I almost overengineered timezone handling; defaulting to `date.today()` and accepting `YYYY-MM-DD` strings is enough for a tracker.
