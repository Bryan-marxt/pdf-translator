@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Preparando o ambiente pela primeira vez...
  python -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" http://localhost:8000
.venv\Scripts\python run.py
pause
