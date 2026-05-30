@echo off
title Ultimate AI Film Studio

REM Kill any existing process on port 7860
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7860 ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)

cd app
..\env\Scripts\python.exe main.py
pause