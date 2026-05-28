@echo off
cd /d "%~dp0"
echo Starting Frontend Server...
call venv\Scripts\activate.bat
python -X utf8 start_frontend.py
