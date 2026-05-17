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
if not exist "env" (
    echo Creating virtual environment...
    python -m venv env
)

REM Activate and install
echo Installing dependencies...
call env\Scripts\activate.bat
pip install -r requirements.txt

cd app

echo.
echo ========================================
echo Installation complete!
echo ========================================
echo.
echo Starting app...
python main.py

pause