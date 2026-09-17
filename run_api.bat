@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv || exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m pip install -U pip
python -m pip install -e .
python -m uvicorn api:app --reload
