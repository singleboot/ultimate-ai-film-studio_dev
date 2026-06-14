@echo off
title Ultimate AI Film Studio - Stop
echo Stopping Ultimate AI Film Studio...

REM Kill any existing process on port 7860
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7860 ^| findstr LISTENING') do (
    echo Killing process %%a on port 7860...
    taskkill /PID %%a /F >nul 2>&1
)

echo.
echo App successfully stopped!
pause
