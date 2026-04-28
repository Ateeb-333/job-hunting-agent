"""
CareerPrep Job-Hunting Agent — Streamlit UI

Run with:
    streamlit run ui.py

This is a thin presentation layer over `app.py`. Drag-drop file uploads
replace manual folder copying; tracker edits happen via forms; results
render inline. No terminal needed.
"""

from __future__ import annotations

import io
import os
import shutil
from datetime import date, datetime

import pandas as pd
import streamlit as st

import app as agent

# --------------------------------------------------------------------------- #
# Page setup
# --------------------------------------------------------------------------- #

st.set_page_config(
    page_title="CareerPrep Job-Hunting Agent",
    page_icon="🎯",
    layout="wide",
)

title_col, badge_col = st.columns([4, 1])
title_col.title("CareerPrep Job-Hunting Agent")
title_col.caption(
    "Upload a job poster, your resume, and your interview-prep notes. "
    "Get a skill-gap report, tailored bullets, interview questions, "
    "a draft cover letter, and a tracker — all in your browser."
)
with badge_col:
    if agent.llm_available():
        st.success(f"🤖 LLM ON\n\n`{agent._current_model()}`")
    else:
        st.warning("📝 Templates\n\nSet `OPENROUTER_API_KEY` for tailored output")

# Privacy + multi-user notice — important when this is publicly deployed.
st.info(
    "**Public demo notice:** the tracker and uploaded files are *shared across "
    "all visitors* on this hosted instance. Don't paste real personal info or "
    "private resumes here. Run the agent locally (clone the repo) for private use.",
    icon="🛡️",
)

# Make sure folders exist before any upload.
agent.ensure_folders()
agent.create_tracker_if_missing()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _save_upload(uploaded_file, folder):
    """Persist an UploadedFile into one of the input folders."""
    target = os.path.join(folder, uploaded_file.name)
    with open(target, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return target


def _wipe_folder(folder):
    """Remove every file in `folder` (keep the folder itself)."""
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            os.remove(path)


def _list_folder_files(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(
        f for f in os.listdir(folder)
        if not f.startswith(".") and os.path.isfile(os.path.join(folder, f))
    )


def _read_output(name):
    path = os.path.join(agent.OUTPUT_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Report parsers — pull structured data out of the .txt reports so we can
# render with Streamlit components instead of dumping monospace text.
# --------------------------------------------------------------------------- #

import re as _re


def _chip(text, color):
    """Inline coloured 'pill'. Streamlit renders raw HTML inside markdown."""
    palette = {
        "green":  ("#dcfce7", "#166534"),
        "red":    ("#fee2e2", "#991b1b"),
        "blue":   ("#dbeafe", "#1e40af"),
        "amber":  ("#fef3c7", "#92400e"),
        "gray":   ("#e5e7eb", "#374151"),
        "purple": ("#ede9fe", "#5b21b6"),
    }
    bg, fg = palette.get(color, palette["gray"])
    return (
        f"<span style='display:inline-block;padding:2px 10px;margin:2px 4px 2px 0;"
        f"border-radius:999px;background:{bg};color:{fg};font-size:0.85rem;"
        f"font-weight:500'>{text}</span>"
    )


def _render_chips(skills, color):
    if not skills:
        return "_none_"
    return " ".join(_chip(s, color) for s in skills)


def _parse_resume_quality(text):
    """Return (overall_score, [(name, points, max, comment), ...])."""
    overall = None
    rows = []
    if not text:
        return overall, rows
    m = _re.search(r"Overall:\s*([\d.]+)\s*/\s*100", text)
    if m:
        overall = float(m.group(1))
    # Lines look like:  "JD alignment            24.6 / 40    16/26 JD skills present"
    pat = _re.compile(r"^\s{2}([A-Za-z+ /]+?)\s{2,}([\d.]+)\s*/\s*(\d+)\s+(.*)$")
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            rows.append((m.group(1).strip(), float(m.group(2)), int(m.group(3)), m.group(4).strip()))
    return overall, rows


def _parse_project_mapping(text):
    """Return [{title, coverage, mapped: [str, ...]}, ...]."""
    projects = []
    if not text:
        return projects
    current = None
    for raw in text.splitlines():
        if raw.startswith("- "):
            if current:
                projects.append(current)
            current = {"title": raw[2:].strip(), "coverage": 0.0, "mapped": [], "raw_jd_count": ""}
            continue
        if current is None:
            continue
        m = _re.search(r"JD coverage:\s*([\d.]+)%\s*\(([^)]+)\)", raw)
        if m:
            current["coverage"] = float(m.group(1))
            current["raw_jd_count"] = m.group(2).strip()
            continue
        m = _re.match(r"\s*Mapped skills:\s*(.+)$", raw)
        if m:
            mapped = m.group(1).strip()
            if mapped and "(none" not in mapped:
                current["mapped"] = [s.strip() for s in mapped.split(",")]
            continue
    if current:
        projects.append(current)
    return projects


def _parse_interview_questions(text):
    """
    Split interview-questions report into sections by their heading
    (lines starting with 'A.', 'B.', 'C.', 'D.'). Returns dict label->body.
    """
    sections = {}
    if not text:
        return sections
    current_label = None
    buffer = []
    for line in text.splitlines():
        m = _re.match(r"^([A-D])\.\s+(.+)$", line)
        if m:
            if current_label:
                sections[current_label] = "\n".join(buffer).strip()
            current_label = f"{m.group(1)}. {m.group(2)}"
            buffer = []
        else:
            if current_label:
                buffer.append(line)
    if current_label:
        sections[current_label] = "\n".join(buffer).strip()
    return sections


def _parse_resume_rewrites(text):
    """
    For LLM-mode tailored resume output, extract ORIGINAL/REWRITE/WHY blocks.
    Returns (rewrites: [{original, rewrite, why}], extras_text).
    """
    rewrites = []
    if not text or "ORIGINAL:" not in text:
        return rewrites, text
    blocks = _re.split(r"\n(?=ORIGINAL:\s)", text)
    for blk in blocks:
        if not blk.strip().startswith("ORIGINAL:"):
            continue
        original = _re.search(r"ORIGINAL:\s*(.+?)(?=\n\s*REWRITE:)", blk, _re.S)
        rewrite = _re.search(r"REWRITE:\s*(.+?)(?=\n\s*WHY:)", blk, _re.S)
        why = _re.search(r"WHY:\s*(.+?)(?=\n\s*(ORIGINAL:|BULLETS TO ADD|$))", blk, _re.S)
        if original and rewrite:
            rewrites.append({
                "original": original.group(1).strip(),
                "rewrite": rewrite.group(1).strip(),
                "why": (why.group(1).strip() if why else ""),
            })
    # Look for the "BULLETS TO ADD" tail
    extras = ""
    m = _re.search(r"BULLETS TO ADD.*", text, _re.S)
    if m:
        extras = m.group(0)
    return rewrites, extras


# --------------------------------------------------------------------------- #
# Section renderers — each one takes the raw report text and renders rich UI.
# --------------------------------------------------------------------------- #

def _render_skill_gap(last, raw_text):
    score = last.get("score", 0)
    matched = last.get("matched") or []
    missing = last.get("missing") or []

    st.subheader("Skill match")
    bar_col, stat_col = st.columns([3, 1])
    with bar_col:
        st.progress(min(int(score), 100), text=f"Match score · {score}% of JD skills present in your resume")
    with stat_col:
        st.metric("Matched", f"{len(matched)} skills")
        st.metric("Missing", f"{len(missing)} skills")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**✅ You already have these JD skills**")
        st.markdown(_render_chips(matched, "green"), unsafe_allow_html=True)
    with c2:
        st.markdown("**🔍 Skills the JD wants but your resume doesn't show**")
        st.markdown(_render_chips(missing, "red"), unsafe_allow_html=True)

    extras = sorted(set(last.get("resume_skills") or []) - set(last.get("job_skills") or []))
    if extras:
        with st.expander(f"Skills only in your resume ({len(extras)})  —  keep these for other roles"):
            st.markdown(_render_chips(extras, "gray"), unsafe_allow_html=True)


def _render_resume_quality(raw_text):
    overall, rows = _parse_resume_quality(raw_text)
    if overall is None:
        st.code(raw_text or "(report not generated)", language="text")
        return

    st.subheader("Resume quality")
    headline_col, breakdown_col = st.columns([1, 3])
    with headline_col:
        st.metric("Overall", f"{overall:.1f} / 100")
        if overall >= 85:
            st.success("Strong")
        elif overall >= 65:
            st.info("Decent — clear wins available")
        else:
            st.warning("Weak — significant gaps")

    with breakdown_col:
        for name, points, maximum, comment in rows:
            ratio = points / maximum if maximum else 0
            label_col, bar_col, value_col = st.columns([2, 4, 1])
            label_col.markdown(f"**{name}**")
            label_col.caption(comment)
            bar_col.progress(min(int(ratio * 100), 100))
            value_col.markdown(f"`{points} / {maximum}`")


def _render_project_mapping(raw_text):
    projects = _parse_project_mapping(raw_text)
    st.subheader("Your projects vs. this JD")
    if not projects:
        st.code(raw_text or "(no projects detected)", language="text")
        return

    df = pd.DataFrame([{"Project": p["title"], "JD coverage %": p["coverage"]} for p in projects])
    st.bar_chart(df, x="Project", y="JD coverage %", use_container_width=True)

    for p in projects:
        with st.container(border=True):
            top_left, top_right = st.columns([4, 1])
            top_left.markdown(f"### {p['title']}")
            top_right.metric("Coverage", f"{p['coverage']:.0f}%")
            if p["raw_jd_count"]:
                st.caption(p["raw_jd_count"])
            if p["mapped"]:
                st.markdown(_render_chips(p["mapped"], "blue"), unsafe_allow_html=True)
            else:
                st.warning(
                    "This project doesn't map to any JD skill yet — "
                    "rewrite the bullets to surface relevant keywords or de-emphasize it."
                )


def _render_cover_letter(raw_text):
    st.subheader("Cover letter draft")
    if not raw_text or not raw_text.strip():
        st.info("No cover letter generated yet.")
        return
    if agent.llm_available():
        st.caption("✨ LLM-tailored — quotes a specific project from your resume.")
    else:
        st.caption("📝 Template mode — turn on LLM via `.env` for a personalized version.")
    with st.container(border=True):
        st.markdown(raw_text.replace("\n\n", "\n\n").replace("\n", "  \n"))
    st.download_button(
        "📋 Copy / Download cover letter",
        data=raw_text,
        file_name="cover_letter.txt",
        mime="text/plain",
        key="dl_cover_visual",
    )


def _render_interview_questions(raw_text):
    st.subheader("Interview prep")
    if not raw_text:
        st.info("No interview questions generated yet.")
        return

    sections = _parse_interview_questions(raw_text)
    if not sections:
        st.code(raw_text, language="text")
        return

    if agent.llm_available():
        st.caption("✨ LLM-picked questions tailored to your resume vs. this JD.")
    else:
        st.caption("📝 Template questions — set `OPENROUTER_API_KEY` in `.env` for tailored ones.")

    icon_for = {"A": "💻", "B": "💬", "C": "📚", "D": "❓"}

    for label, body in sections.items():
        first_letter = label[0]
        with st.expander(f"{icon_for.get(first_letter, '•')}  {label}", expanded=(first_letter == "A")):
            for raw_line in body.splitlines():
                line = raw_line.strip()
                if not line:
                    st.write("")
                    continue
                if line.startswith(("- ", "  - ")):
                    st.markdown(f"- {line.lstrip('- ').strip()}")
                elif _re.match(r"^\d+[.)]\s", line):
                    st.markdown(f"- {line}")
                else:
                    st.markdown(line)


def _render_tailored_resume(raw_text):
    st.subheader("Resume rewrites")
    if not raw_text:
        st.info("No suggestions generated yet.")
        return

    rewrites, extras = _parse_resume_rewrites(raw_text)
    if not rewrites:
        # Template mode — render the bulleted list as nicely as we can
        if agent.llm_available():
            st.caption("✨ LLM-tailored rewrites of your actual resume bullets.")
        else:
            st.caption("📝 Template suggestions — set `OPENROUTER_API_KEY` in `.env` for personalized rewrites.")
        # Just markdown-render: bullets render as a list
        cleaned = _re.sub(r"^Tailored Resume Suggestions\n=+\n.*?\n\n", "", raw_text, flags=_re.S)
        st.markdown(cleaned)
        return

    st.caption(
        f"✨ LLM rewrote {len(rewrites)} of your bullets in JD vocabulary, "
        "without inventing experience."
    )
    for i, item in enumerate(rewrites, 1):
        with st.container(border=True):
            st.markdown(f"**Bullet #{i}**")
            cols = st.columns(2)
            with cols[0]:
                st.markdown("**Before**")
                st.markdown(
                    f"<div style='background:#fef2f2;padding:8px 12px;border-radius:6px;"
                    f"font-size:0.92rem;color:#7f1d1d'>{item['original']}</div>",
                    unsafe_allow_html=True,
                )
            with cols[1]:
                st.markdown("**After**")
                st.markdown(
                    f"<div style='background:#ecfdf5;padding:8px 12px;border-radius:6px;"
                    f"font-size:0.92rem;color:#065f46'>{item['rewrite']}</div>",
                    unsafe_allow_html=True,
                )
            if item["why"]:
                st.caption(f"💡 {item['why']}")

    if extras.strip():
        st.divider()
        st.markdown("### Bullets to earn next")
        st.markdown(extras.replace("BULLETS TO ADD:", "").strip())


def _render_job_analysis(last, raw_text):
    job_skills = last.get("job_skills") or []
    st.subheader("What this JD asks for")
    if job_skills:
        st.markdown(_render_chips(job_skills, "purple"), unsafe_allow_html=True)
    else:
        st.warning("No known skills detected in the job poster.")

    # Surface 'Other notable terms' from the report.
    if raw_text and "Other notable terms" in raw_text:
        tail = raw_text.split("Other notable terms", 1)[1]
        terms = []
        for line in tail.splitlines():
            m = _re.match(r"\s*\?\s*(.+)$", line)
            if m:
                terms.append(m.group(1).strip())
        if terms:
            st.markdown("**Notable phrases not in our skill list — worth checking manually:**")
            st.markdown(_render_chips(terms, "amber"), unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Sidebar — inputs
# --------------------------------------------------------------------------- #

st.sidebar.header("Inputs")

with st.sidebar:
    st.markdown("**Job posters** (`.txt` or `.pdf`)")
    job_uploads = st.file_uploader(
        "Drop job descriptions here", type=["txt", "pdf"],
        accept_multiple_files=True, key="job_upload",
    )
    st.markdown("**Resume** (`.txt` or `.pdf`)")
    resume_uploads = st.file_uploader(
        "Drop your resume here", type=["txt", "pdf"],
        accept_multiple_files=True, key="resume_upload",
    )
    st.markdown("**Interview-prep KB** (`.txt` or `.pdf`) — _optional_")
    kb_uploads = st.file_uploader(
        "Drop slide notes / prep notes here", type=["txt", "pdf"],
        accept_multiple_files=True, key="kb_upload",
    )
    st.caption(
        "Optional. If supplied, the agent generates extra interview questions "
        "from your course/KB material. Skip it if you don't have notes handy."
    )

    if st.button("📥 Save uploads to input folders", use_container_width=True):
        saved = 0
        for f in job_uploads or []:
            _save_upload(f, agent.JOB_DIR); saved += 1
        for f in resume_uploads or []:
            _save_upload(f, agent.RESUME_DIR); saved += 1
        for f in kb_uploads or []:
            _save_upload(f, agent.KB_DIR); saved += 1
        if saved:
            st.success(f"Saved {saved} file(s).")
        else:
            st.warning("No files were uploaded.")

    st.divider()
    st.caption("Currently in input folders:")
    cols = st.columns(3)
    cols[0].metric("Jobs", len(_list_folder_files(agent.JOB_DIR)))
    cols[1].metric("Resumes", len(_list_folder_files(agent.RESUME_DIR)))
    cols[2].metric("KB", len(_list_folder_files(agent.KB_DIR)))

    with st.expander("Folder contents", expanded=False):
        st.write("**input_jobs/**", _list_folder_files(agent.JOB_DIR))
        st.write("**input_resumes/**", _list_folder_files(agent.RESUME_DIR))
        st.write("**input_kb/**", _list_folder_files(agent.KB_DIR))

    st.divider()
    danger = st.checkbox("Show clear buttons", value=False)
    if danger:
        if st.button("🗑 Clear input_jobs/", use_container_width=True):
            _wipe_folder(agent.JOB_DIR)
            st.rerun()
        if st.button("🗑 Clear input_resumes/", use_container_width=True):
            _wipe_folder(agent.RESUME_DIR)
            st.rerun()
        if st.button("🗑 Clear input_kb/", use_container_width=True):
            _wipe_folder(agent.KB_DIR)
            st.rerun()


# --------------------------------------------------------------------------- #
# Main tabs
# --------------------------------------------------------------------------- #

tab_analyze, tab_tracker, tab_dashboard = st.tabs([
    "🔍 Analyze", "📋 Tracker", "📊 Dashboard",
])


# --- Tab 1: Analyze --------------------------------------------------------- #

with tab_analyze:
    col_run, col_score = st.columns([3, 2])
    with col_run:
        st.subheader("Run analysis")
        st.write(
            "Click below to read all files in the three input folders, "
            "extract skills, compute the JD-vs-resume gap, and generate every report."
        )
        run_clicked = st.button("⚡ Run analysis now", type="primary", use_container_width=True)

    if run_clicked:
        if not _list_folder_files(agent.JOB_DIR):
            st.error("`input_jobs/` is empty. Upload at least one job poster.")
        elif not _list_folder_files(agent.RESUME_DIR):
            st.error("`input_resumes/` is empty. Upload your resume.")
        else:
            if not _list_folder_files(agent.KB_DIR):
                st.info("`input_kb/` is empty — KB-derived interview questions will be skipped.")
            try:
                with st.spinner("Running agent... (this can take 10–30s if the LLM is on)"):
                    result = agent.run_analysis(verbose=False)
            except Exception as e:
                st.error(
                    f"Analysis hit an unexpected error: `{type(e).__name__}: {e}`. "
                    "Try removing the most recently uploaded file and re-running."
                )
                result = None
            if result is None:
                st.error("Analysis didn't complete. Check that your inputs are readable text or PDFs.")
            else:
                st.session_state["last_result"] = result
                st.success(
                    f"Done. Match score {result['score']}% — "
                    f"{len(result['matched'])} matched, {len(result['missing'])} missing."
                )

    last = st.session_state.get("last_result")
    if last:
        with col_score:
            m1, m2, m3 = st.columns(3)
            m1.metric("Match", f"{last['score']:.0f}%")
            m2.metric("Quality", f"{last.get('resume_quality_score', 0):.0f}/100")
            m3.metric("JD skills", str(len(last.get("job_skills") or [])))

        st.divider()

        # New dashboard view — sub-tabs replace the long expander list.
        sub_overview, sub_resume, sub_letter, sub_interview, sub_raw = st.tabs([
            "📈 Overview", "📝 Resume", "💌 Cover letter", "🎤 Interview", "📄 Raw reports",
        ])

        with sub_overview:
            _render_skill_gap(last, _read_output("skill_gap_report.txt") or "")
            st.divider()
            _render_job_analysis(last, _read_output("job_analysis_report.txt") or "")

        with sub_resume:
            _render_resume_quality(_read_output("resume_quality_score.txt") or "")
            st.divider()
            _render_tailored_resume(_read_output("tailored_resume_suggestions.txt") or "")
            st.divider()
            _render_project_mapping(_read_output("project_mapping.txt") or "")

        with sub_letter:
            _render_cover_letter(_read_output("cover_letter.txt") or "")

        with sub_interview:
            _render_interview_questions(_read_output("interview_questions.txt") or "")

        with sub_raw:
            st.caption("Plain-text reports — useful for pasting into a doc or downloading.")
            report_files = [
                ("Job analysis", "job_analysis_report.txt"),
                ("Skill gap", "skill_gap_report.txt"),
                ("Resume quality", "resume_quality_score.txt"),
                ("Tailored resume", "tailored_resume_suggestions.txt"),
                ("Project mapping", "project_mapping.txt"),
                ("Interview questions", "interview_questions.txt"),
                ("Cover letter", "cover_letter.txt"),
                ("Final report (combined)", "final_agent_report.txt"),
            ]
            for label, fname in report_files:
                content = _read_output(fname)
                if content is None:
                    continue
                with st.expander(f"📄 {label}  —  outputs/{fname}"):
                    st.code(content, language="text")
                    st.download_button(
                        f"Download {fname}",
                        data=content,
                        file_name=fname,
                        mime="text/plain",
                        key=f"dl_{fname}",
                    )

        st.divider()
        st.subheader("Bonus exports")
        bonus_cols = st.columns(2)
        pdf_path = os.path.join(agent.OUTPUT_DIR, "final_agent_report.pdf")
        ics_path = os.path.join(agent.TRACKER_DIR, "calendar.ics")
        with bonus_cols[0]:
            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "📕 Download final report (PDF)",
                        data=f.read(),
                        file_name="final_agent_report.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
            else:
                st.caption("PDF export not available (install fpdf2 or check logs).")
        with bonus_cols[1]:
            if os.path.exists(ics_path):
                with open(ics_path, "rb") as f:
                    st.download_button(
                        "📅 Download calendar (.ics)",
                        data=f.read(),
                        file_name="calendar.ics",
                        mime="text/calendar",
                        use_container_width=True,
                    )
                st.caption("Import into Google / Apple Calendar to see all interviews & follow-ups.")
            else:
                st.caption("No calendar file yet — add an application with dates.")
    elif not run_clicked:
        st.info("No analysis run yet. Upload files in the sidebar, then click **Run analysis now**.")


# --- Tab 2: Tracker --------------------------------------------------------- #

with tab_tracker:
    st.subheader("Application tracker")

    rows = agent.read_tracker()
    if rows:
        df = pd.DataFrame(rows)[agent.TRACKER_FIELDS]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No applications yet. Add one below.")

    st.divider()
    st.markdown("### Add a new application")

    with st.form("add_app"):
        c1, c2 = st.columns(2)
        company = c1.text_input("Company *")
        role = c2.text_input("Role *")
        c3, c4 = st.columns(2)
        source = c3.text_input("Source", placeholder="LinkedIn / Rozee / Job Fair")
        status = c4.selectbox("Status", sorted(agent.VALID_STATUSES), index=sorted(agent.VALID_STATUSES).index("Not Applied"))

        c5, c6, c7 = st.columns(3)
        applied = c5.text_input("Applied date", placeholder="2026-05-03 or 5/3/2026")
        interview = c6.text_input("Interview date", placeholder="optional")
        follow_up = c7.text_input("Follow-up date", placeholder="optional")

        next_action = st.text_area("Next action", placeholder="What do you need to do next?")
        notes = st.text_area("Notes")

        submitted = st.form_submit_button("➕ Add to tracker", type="primary")

    if submitted:
        if not company.strip() or not role.strip():
            st.error("Company and Role are required.")
        else:
            new_id = agent.add_application(
                company=company, role=role, source=source, status=status,
                applied_date=applied, interview_date=interview,
                follow_up_date=follow_up, next_action=next_action, notes=notes,
            )
            st.success(f"Added {new_id}. Refresh / scroll up to see it in the table.")
            st.rerun()

    st.divider()
    st.markdown("### Update an application's status")

    if rows:
        with st.form("update_app"):
            ids = [r["application_id"] for r in rows]
            target = st.selectbox("Application", ids)
            new_status = st.selectbox(
                "New status", sorted(agent.VALID_STATUSES),
                index=sorted(agent.VALID_STATUSES).index("Applied"),
            )
            c1, c2, c3 = st.columns(3)
            new_applied = c1.text_input("Applied date (if Applied)")
            new_interview = c2.text_input("Interview date (if Interview Scheduled)")
            new_follow = c3.text_input("Follow-up date")
            new_action = st.text_input("Next action (optional)")
            update_clicked = st.form_submit_button("💾 Update")

        if update_clicked:
            updates = {"status": new_status}
            if new_applied:
                updates["applied_date"] = agent.parse_flexible_date(new_applied) or new_applied
            if new_interview:
                updates["interview_date"] = agent.parse_flexible_date(new_interview) or new_interview
            if new_follow:
                updates["follow_up_date"] = agent.parse_flexible_date(new_follow) or new_follow
            if new_action:
                updates["next_action"] = new_action
            ok = agent.update_application(target, **updates)
            if ok:
                st.success(f"Updated {target}.")
                st.rerun()
            else:
                st.error(f"No application with id {target}.")

    st.divider()
    st.markdown("### Reminders")
    if st.button("🔔 Refresh reminders"):
        reminders = agent.generate_reminders()
        agent.save_text(agent.REMINDERS_PATH, reminders)
        st.text(reminders)
    elif os.path.exists(agent.REMINDERS_PATH):
        with open(agent.REMINDERS_PATH, "r", encoding="utf-8") as f:
            st.text(f.read())


# --- Tab 3: Dashboard ------------------------------------------------------- #

with tab_dashboard:
    st.subheader("Application funnel")
    rows = agent.read_tracker()
    if not rows:
        st.info("No applications tracked yet.")
    else:
        df = pd.DataFrame(rows)
        # status counts
        status_counts = df["status"].value_counts().reset_index()
        status_counts.columns = ["status", "count"]
        st.bar_chart(status_counts, x="status", y="count", use_container_width=True)

        st.divider()
        st.subheader("Upcoming dates")
        today = date.today()
        upcoming = []
        for r in rows:
            for label, key in (("Interview", "interview_date"),
                               ("Follow-up", "follow_up_date")):
                d = agent._parse_date(r.get(key))
                if d and d >= today:
                    upcoming.append({
                        "when": d.isoformat(),
                        "in days": (d - today).days,
                        "what": label,
                        "company": r.get("company", ""),
                        "role": r.get("role", ""),
                        "id": r.get("application_id", ""),
                    })
        if upcoming:
            upcoming.sort(key=lambda x: x["when"])
            st.dataframe(pd.DataFrame(upcoming), hide_index=True, use_container_width=True)
        else:
            st.caption("Nothing coming up.")

        st.divider()
        st.subheader("Last analysis snapshot (memory.json)")
        mem_path = os.path.join(agent.TRACKER_DIR, "memory.json")
        if os.path.exists(mem_path):
            with open(mem_path) as f:
                st.json(f.read())
        else:
            st.caption("Run an analysis to populate this.")
