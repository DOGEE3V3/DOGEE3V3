@echo off
setlocal
cd /d %~dp0

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
  echo Run setup first: python -m venv .venv ^&^& .venv\Scripts\python -m pip install -r requirements.txt
  exit /b 1
)

.venv\Scripts\python.exe bot.py
