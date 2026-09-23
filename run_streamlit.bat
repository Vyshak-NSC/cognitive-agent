@REM  @echo off
@REM  setlocal
@REM  cd /d "%~dp0"
@REM  if not exist ".venv\Scripts\python.exe" (
@REM    echo Creating virtual environment...
@REM    py -3 -m venv .venv || exit /b 1
@REM  )
@REM  call ".venv\Scripts\activate.bat"
@REM  python -m pip install -U pip
@REM  python -m pip install -e .
@REM  python -m streamlit run ragapp\interfaces\streamlit_app\main.py

@echo off
uv run py -m streamlit run ragapp\interfaces\streamlit_app\main.py