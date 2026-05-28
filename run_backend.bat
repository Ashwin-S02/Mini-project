@echo off
cd /d "%~dp0"
echo Starting Backend Server...
call venv\Scripts\activate.bat
python -X utf8 main.py
