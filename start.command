#!/usr/bin/env bash
# macOS one-click launcher for the CareerPrep web UI.
# Double-click this file in Finder to start the app in your browser.

set -e
cd "$(dirname "$0")"

# Pick the right python: prefer python3, fall back to python.
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
    osascript -e 'display alert "Python not found" message "Install Python 3.9+ from python.org and re-run."'
    exit 1
fi

# Make sure dependencies are installed (idempotent — pip skips if already there).
"$PY" -m pip install --user --quiet -r requirements.txt || true

# Streamlit may install to ~/Library/Python/3.x/bin which isn't on PATH.
STREAMLIT="$(command -v streamlit || true)"
if [ -z "$STREAMLIT" ]; then
    USER_BIN="$("$PY" -c 'import site, os; print(os.path.join(site.USER_BASE, "bin"))')"
    if [ -x "$USER_BIN/streamlit" ]; then
        STREAMLIT="$USER_BIN/streamlit"
    fi
fi
if [ -z "$STREAMLIT" ]; then
    "$PY" -m pip install --user streamlit
    USER_BIN="$("$PY" -c 'import site, os; print(os.path.join(site.USER_BASE, "bin"))')"
    STREAMLIT="$USER_BIN/streamlit"
fi

echo "Launching CareerPrep UI on http://localhost:8501 ..."
"$STREAMLIT" run ui.py
