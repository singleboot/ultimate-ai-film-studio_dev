@echo off
title Ultimate AI Film Studio
setlocal EnableDelayedExpansion

cd /d "%~dp0"

REM ============================================================
REM  Self-healing launcher:
REM  Detects a broken/missing venv (e.g. Python was moved or
REM  upgraded) and rebuilds it automatically with
REM  pip install -r requirements.txt before starting the app.
REM ============================================================

REM Kill any existing process on port 7860
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :7860 ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)

set "PY=%~dp0env\Scripts\python.exe"

REM ---- Health check: venv python runs AND all runtime deps import ----
set "VENV_OK=1"
if not exist "%PY%" set "VENV_OK=0"
if "!VENV_OK!"=="1" (
    "%PY%" -c "import fastapi, uvicorn, jinja2, requests, PIL, pydantic, huggingface_hub, multipart" >nul 2>&1
    if not "!errorlevel!"=="0" set "VENV_OK=0"
)
if "!VENV_OK!"=="1" goto :launch

echo.
echo ============================================================
echo  Virtual environment is broken or missing - repairing...
echo  NOTE: keep the import list above in sync with requirements.txt
echo ============================================================

REM ---- Step 1: read the Python minor version the venv was built with ----
set "CFG_VER="
set "CFG_MIN="
if exist "env\pyvenv.cfg" (
    for /f "usebackq tokens=1,2,* delims= " %%a in ("env\pyvenv.cfg") do (
        if /i "%%a"=="version" set "CFG_VER=%%c"
    )
)
if defined CFG_VER for /f "tokens=2 delims=." %%v in ("!CFG_VER!") do set "CFG_MIN=%%v"

REM ---- Step 2: find a host Python, preferring the same minor version ----
set "HOST_PY="
set "HOST_VER="
set "HOST_MIN="
set "PYV="
if defined CFG_VER for /f "tokens=1,2 delims=." %%a in ("!CFG_VER!") do set "PYV=%%a.%%b"

if defined PYV (
    py -!PYV! -c "import sys" >nul 2>&1
    if "!errorlevel!"=="0" set "HOST_PY=py -!PYV!"
)

if not defined HOST_PY (
    python --version > "%TEMP%\uafs_pyver.txt" 2>nul
    for /f "usebackq tokens=2" %%v in ("%TEMP%\uafs_pyver.txt") do set "HOST_VER=%%v"
    if defined HOST_VER for /f "tokens=2 delims=." %%v in ("!HOST_VER!") do set "HOST_MIN=%%v"
    if defined CFG_MIN if defined HOST_MIN if "!HOST_MIN!"=="!CFG_MIN!" set "HOST_PY=python"
)

REM Last resort: accept any Python 3.12+ on PATH
if not defined HOST_PY if defined HOST_MIN if !HOST_MIN! GEQ 12 set "HOST_PY=python"

if not defined HOST_PY (
    echo ERROR: No suitable Python found to rebuild the venv.
    echo        Install Python 3.12+ and make sure "python" is on PATH.
    pause
    exit /b 1
)

!HOST_PY! --version > "%TEMP%\uafs_pyver.txt" 2>nul
for /f "usebackq tokens=2" %%v in ("%TEMP%\uafs_pyver.txt") do set "HOST_VER=%%v"
echo Host Python: !HOST_PY!  version !HOST_VER!

REM ---- Step 3: rebuild the venv in place (keeps installed packages) ----
if not exist "env\Scripts" mkdir "env\Scripts"
!HOST_PY! -m venv --without-pip env >nul 2>&1
if not "!errorlevel!"=="0" (
    echo In-place repair failed - recreating the venv from scratch...
    rmdir /s /q env >nul 2>&1
    if exist "env\Scripts\python.exe" (
        echo ERROR: Cannot remove the old venv - close running instances and retry.
        pause
        exit /b 1
    )
    !HOST_PY! -m venv --without-pip env
    if not "!errorlevel!"=="0" (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

REM ---- Step 4: bootstrap pip and (re)install dependencies ----
"%PY%" -m ensurepip --upgrade >nul 2>&1
echo Installing dependencies (this can take a few minutes)...
"%PY%" -m pip install -r requirements.txt
if not "!errorlevel!"=="0" (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo.
echo Repair complete!

:launch
echo.
echo Starting Ultimate AI Film Studio on http://127.0.0.1:7860 ...
cd app
"%PY%" main.py
pause
