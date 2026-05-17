@echo off
title Ultimate AI Film Studio - Installer

echo ========================================
echo Ultimate AI Film Studio - Setup
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

REM Create virtual environment
echo Creating virtual environment...
python -m venv env

REM Activate and install
echo Installing dependencies...
call env\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo ========================================
echo Installation complete!
echo ========================================
echo.
echo Press any key to start the app...
pause >nul

REM Start the app
start /b cmd /c "env\Scripts\activate.bat && python app\main.py"
echo Starting app...
timeout /t 3 /nobreak >nul

echo App should be running at: http://127.0.0.1:7860
echo Press any key to exit...
pause >nul