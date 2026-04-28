@echo off
REM Windows one-click launcher for the CareerPrep web UI.
REM Double-click this file to start the app in your default browser.

cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Python not found. Install Python 3.9+ from https://www.python.org/downloads/ and re-run.
    pause
    exit /b 1
)

python -m pip install --user --quiet -r requirements.txt

where streamlit >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    python -m pip install --user streamlit
)

echo Launching CareerPrep UI on http://localhost:8501 ...
python -m streamlit run ui.py
pause
