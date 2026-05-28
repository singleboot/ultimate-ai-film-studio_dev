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

REM Create models directory
if not exist "models" (
    echo Creating models directory...
    mkdir models
)

REM Create bin directory for bundled binaries
if not exist "bin" (
    echo Creating bin directory...
    mkdir bin
)

REM Check if ComfyUI is already installed
if not exist "comfyui\main.py" (
    echo.
    echo ========================================
    echo ComfyUI not found. Options:
    echo 1. Quick Install via ComfyUI-Easy-Install (recommended)
    echo 2. Manual: git clone https://github.com/comfyanonymous/ComfyUI.git comfyui
    echo 3. Skip (you can install later in Settings)
    echo ========================================
    echo.
    set /p comfy_choice="Choose [1/2/3] (default: 1): "
    if "!comfy_choice!"=="" set comfy_choice=1
    if "!comfy_choice!"=="1" (
        echo Downloading ComfyUI-Easy-Install...
        powershell -Command "& {Invoke-WebRequest -Uri 'https://github.com/Tavris1/ComfyUI-Easy-Install/archive/refs/heads/Windows.zip' -OutFile '%TEMP%\comfyui-easy-install.zip'}"
        echo Extracting ComfyUI-Easy-Install...
        powershell -Command "& {Expand-Archive -Path '%TEMP%\comfyui-easy-install.zip' -DestinationPath '%TEMP%\comfyui-extracted' -Force}"
        if exist "%TEMP%\comfyui-extracted\ComfyUI-Easy-Install-Windows" (
            xcopy /E /I /Y "%TEMP%\comfyui-extracted\ComfyUI-Easy-Install-Windows\*" "comfyui\"
        ) else (
            echo Easy-Install download failed. Falling back to git clone...
            git clone https://github.com/comfyanonymous/ComfyUI.git comfyui
        )
        if exist "comfyui\ComfyUI-Easy-Install.bat" (
            echo Running ComfyUI-Easy-Install setup...
            cd comfyui
            call ComfyUI-Easy-Install.bat
            cd ..
        )
        echo ComfyUI setup initiated.
    ) else if "!comfy_choice!"=="2" (
        echo Cloning ComfyUI from GitHub...
        git clone https://github.com/comfyanonymous/ComfyUI.git comfyui
        if exist "comfyui\requirements.txt" (
            echo Installing ComfyUI dependencies...
            pip install -r comfyui\requirements.txt
        )
    ) else (
        echo Skipping ComfyUI installation. You can install it later from Settings.
    )
) else (
    echo ComfyUI already installed.
    if exist "comfyui\requirements.txt" (
        echo Ensuring ComfyUI dependencies are installed...
        pip install -r comfyui\requirements.txt
    )
)

REM Download llama-server for App LLM
echo.
echo Checking for llama-server binary...
if not exist "bin\llama-server.exe" (
    echo Downloading llama-server for App LLM...
    echo Fetching latest llama.cpp release...
    powershell -Command "& {$progressPreference='silentlyContinue'; try { $r = Invoke-WebRequest -Uri 'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest' -UseBasicParsing -TimeoutSec 10; $d = $r.Content | ConvertFrom-Json; Write-Host $d.tag_name } catch { Write-Host 'b9370' }" > "%TEMP%\llama-latest-tag.txt"
    set /p LLAMA_VERSION=<"%TEMP%\llama-latest-tag.txt"
    echo Latest version: %LLAMA_VERSION%
    powershell -Command "& {$progressPreference='silentlyContinue'; try { $k = Get-CimInstance 'CIM_VideoController' | Select-Object -ExpandProperty Name; if ($k -match 'NVIDIA') { $v = (nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>$null); if ($v) { $m = [Version]$v; if ($m.Major -ge 13) { 'cuda-13.3' } else { 'cuda-12.4' } } else { 'cuda-12.4' } } else { 'cpu' } } catch { 'cpu' }" > "%TEMP%\llama-variant.txt"
    set /p LLAMA_VARIANT=<"%TEMP%\llama-variant.txt"
    if "%LLAMA_VARIANT%"=="cuda-13.3" (
        set "LLAMA_URL=https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_VERSION%/llama-%LLAMA_VERSION%-bin-win-cuda-13.3-x64.zip"
    ) else if "%LLAMA_VARIANT%"=="cuda-12.4" (
        set "LLAMA_URL=https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_VERSION%/llama-%LLAMA_VERSION%-bin-win-cuda-12.4-x64.zip"
    ) else (
        set "LLAMA_URL=https://github.com/ggml-org/llama.cpp/releases/download/%LLAMA_VERSION%/llama-%LLAMA_VERSION%-bin-win-cpu-x64.zip"
    )
    echo Downloading from: %LLAMA_URL%
    powershell -Command "& {$progressPreference='silentlyContinue'; Invoke-WebRequest -Uri '%LLAMA_URL%' -OutFile '%TEMP%\llama.zip'}"
    if exist "%TEMP%\llama.zip" (
        echo Extracting...
        powershell -Command "& {Expand-Archive -Path '%TEMP%\llama.zip' -DestinationPath '%TEMP%\llama-extracted' -Force}"
        echo Installing llama-server and dependencies to bin\...
        xcopy /E /I /Y "%TEMP%\llama-extracted\*" "bin\" >nul
        if exist "bin\llama-server.exe" (
            echo llama-server and all dependencies installed to bin\.
        ) else (
            dir "%TEMP%\llama-extracted" /s /b
            echo Could not find llama-server.exe in the archive. You may need to download it manually.
            echo Download from: https://github.com/ggml-org/llama.cpp/releases
        )
    ) else (
        echo Could not download llama-server. You can download it manually from:
        echo https://github.com/ggml-org/llama.cpp/releases
    )
) else (
    echo llama-server already downloaded.
)

REM Download Windows-specific requirements
pip install huggingface-hub

echo.
echo ========================================
echo Installation Complete!
echo ========================================
echo.
echo Starting Ultimate AI Film Studio...
echo You can also start it later with: start.bat
echo.

cd /d "%~dp0"
call env\Scripts\activate.bat
cd app
python main.py

pause
