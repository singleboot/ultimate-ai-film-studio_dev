@echo off
title Ultimate AI Film Studio

REM Check if env exists
if not exist "env\Scripts\python.exe" (
    echo Virtual environment not found. Run setup.bat first!
    pause
    exit /b 1
)

echo Starting Ultimate AI Film Studio...
call env\Scripts\activate.bat
python app\main.py
pause