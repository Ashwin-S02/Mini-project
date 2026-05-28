@echo off
cd /d "%~dp0"
echo ==========================================
echo Starting Maps Lead Scraper
echo ==========================================
echo.

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found!
    echo Please run: python -m venv venv
    echo Then: venv\Scripts\activate
    echo Then: pip install -r requirements.txt
    pause
    exit /b 1
)

REM Activate virtual environment
call venv\Scripts\activate.bat

echo Starting Backend Server (Port 8081)...
start "Backend - Maps Scraper" cmd /k "python -X utf8 main.py"

timeout /t 3 /nobreak >nul

echo Starting Frontend Server (Port 3000)...
start "Frontend - Maps Scraper" cmd /k "python -X utf8 start_frontend.py"

timeout /t 2 /nobreak >nul

echo.
echo ==========================================
echo Both servers are starting!
echo ==========================================
echo.
echo Backend:  http://localhost:8081
echo Frontend: http://localhost:3000
echo.
echo Opening browser...
timeout /t 3 /nobreak >nul
start http://localhost:3000
echo.
echo Press any key to stop all servers...
pause >nul

echo.
echo Stopping servers...
taskkill /FI "WindowTitle eq Backend - Maps Scraper*" /T /F >nul 2>&1
taskkill /FI "WindowTitle eq Frontend - Maps Scraper*" /T /F >nul 2>&1
echo Done!
