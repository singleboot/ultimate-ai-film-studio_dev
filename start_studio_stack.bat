@echo off
rem Double-click launcher for the Ultimate AI Film Studio stack.
rem Starts ComfyUI, the WanGP bridge, and the studio app (each only if not already up),
rem health-checks all three, and leaves this window open so you can read the report.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_studio_stack.ps1" %*
echo.
pause
