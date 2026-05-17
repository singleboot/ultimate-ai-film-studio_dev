@echo off
title Ultimate AI Film Studio - Quick Start

echo Starting Ultimate AI Film Studio...

REM Try using global Python with pip install if needed
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies first...
    pip install fastapi uvicorn jinja2 requests pillow pydantic
)

REM Start the app
python app\main.py

pause