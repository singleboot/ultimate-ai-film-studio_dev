# start_studio_stack.ps1 - launch the Ultimate AI Film Studio stack with health checks.
#
# Services managed (in this order):
#   1. ComfyUI      :8188  (GPU engine - workflow-based image/video generation)
#   2. WanGP bridge :8189  (alternate engine sidecar; owns the WanGP session)
#   3. Studio app   :7860  (FastAPI + UI; proxies to both engines)
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File start_studio_stack.ps1              # start what's missing
#   powershell -NoProfile -ExecutionPolicy Bypass -File start_studio_stack.ps1 -CheckOnly   # report health, start nothing
#   powershell -NoProfile -ExecutionPolicy Bypass -File start_studio_stack.ps1 -Stop        # stop studio + bridge (ComfyUI untouched)
#
# Note: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less scripts
# in the system ANSI codepage, and UTF-8 punctuation (em dashes etc.) corrupts
# string parsing under that decoding.
#
# Logs: scratch\comfyui.log / comfyui.err.log, scratch\wangp_bridge.out.log / .err.log,
#       scratch\studio_app.log / studio_app.err.log

param(
    [switch]$CheckOnly,
    [switch]$Stop,
    [int]$TimeoutSec = 90
)

$ErrorActionPreference = "Continue"

$StudioRoot = "F:\MY APP\ULTIMATE AI STUDIO\ultimate-ai-film-studio-V11"
$StudioPy   = Join-Path $StudioRoot "env\Scripts\python.exe"
$StudioApp  = Join-Path $StudioRoot "app"
$StudioLog  = Join-Path $StudioRoot "scratch\studio_app.log"
$StudioErr  = Join-Path $StudioRoot "scratch\studio_app.err.log"

$WanGPPy     = "D:\01_PINOKIO\api\wan_sep2026.git\app\venv\Scripts\python.exe"
$WanGPBridge = Join-Path $StudioRoot "app\tools\wangp_bridge.py"
$WanGPLogOut = Join-Path $StudioRoot "scratch\wangp_bridge.out.log"
$WanGPLogErr = Join-Path $StudioRoot "scratch\wangp_bridge.err.log"

$ComfyRoot = "F:\001 Comfyui Easy installer\ComfyUI-Easy-Install\ComfyUI-Easy-Install\ComfyUI"
$ComfyPy   = "F:\001 Comfyui Easy installer\ComfyUI-Easy-Install\ComfyUI-Easy-Install\python_embeded\python.exe"
$ComfyLog  = Join-Path $StudioRoot "scratch\comfyui.log"
$ComfyErr  = Join-Path $StudioRoot "scratch\comfyui.err.log"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

function Test-Http([string]$Url, [int]$Seconds = 3) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec $Seconds -UseBasicParsing
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch { return $false }
}

function Get-ListenerPid([int]$Port) {
    $line = netstat -ano | Select-String "LISTENING" | Select-String ":$Port " | Select-Object -First 1
    if ($line) {
        $parts = ($line.ToString()) -split '\s+'
        return [int]$parts[-1]
    }
    return $null
}

function Wait-Healthy([string]$Name, [string]$Url, [int]$Timeout) {
    $deadline = (Get-Date).AddSeconds($Timeout)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $Url 3) {
            Write-Ok "$Name healthy at $Url"
            return $true
        }
        Start-Sleep -Seconds 2
    }
    Write-Warn2 "$Name did not become healthy within ${Timeout}s ($Url)"
    return $false
}

function Show-StackHealth {
    $services = @(
        @{ Name = "Studio app  (7860)"; Url = "http://127.0.0.1:7860/" },
        @{ Name = "ComfyUI      (8188)"; Url = "http://127.0.0.1:8188/system_stats" },
        @{ Name = "WanGP bridge (8189)"; Url = "http://127.0.0.1:8189/health" }
    )
    $allUp = $true
    foreach ($svc in $services) {
        if (Test-Http $svc.Url 3) {
            $detail = ""
            if ($svc.Url -like "*8189*") {
                try {
                    $h = Invoke-RestMethod -Uri $svc.Url -TimeoutSec 3
                    if ($h.version) {
                        $detail = " - WanGP v$($h.version), jobs: $($h.jobs)"
                    } else {
                        $detail = " - WanGP session pending (lazy init on first render)"
                    }
                } catch {}
            }
            Write-Ok "$($svc.Name) UP$detail"
        } else {
            Write-Warn2 "$($svc.Name) DOWN"
            $allUp = $false
        }
    }
    return $allUp
}

# ---------- stop mode ----------
if ($Stop) {
    Write-Step "Stopping studio stack (ComfyUI left running)"
    foreach ($port in 7860, 8189) {
        $procId = Get-ListenerPid $port
        if ($procId) {
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Ok "Stopped PID $procId (port $port)"
            } catch { Write-Warn2 "Could not stop PID $procId on port $port : $_" }
        } else {
            Write-Ok "Port $port already free"
        }
    }
    exit 0
}

# ---------- check-only mode ----------
if ($CheckOnly) {
    Write-Step "Stack health check (read-only)"
    $ok = Show-StackHealth
    if ($ok) { exit 0 } else { exit 1 }
}

# ---------- launch mode ----------
Write-Step "Starting Ultimate AI Film Studio stack"

# 1) ComfyUI first (slowest to boot, most likely to already be running)
if (Test-Http "http://127.0.0.1:8188/system_stats" 3) {
    Write-Ok "ComfyUI already running on 8188"
} else {
    if (-not (Test-Path $ComfyPy) -or -not (Test-Path (Join-Path $ComfyRoot "main.py"))) {
        Write-Warn2 "ComfyUI not found at $ComfyRoot - fix ComfyRoot in this script."
    } else {
        Write-Step "Launching ComfyUI"
        Start-Process -FilePath $ComfyPy `
            -ArgumentList "-s main.py --windows-standalone-build --use-ck-attention --disable-auto-launch" `
            -WorkingDirectory $ComfyRoot -WindowStyle Hidden `
            -RedirectStandardOutput $ComfyLog -RedirectStandardError $ComfyErr
        Wait-Healthy "ComfyUI" "http://127.0.0.1:8188/system_stats" $TimeoutSec | Out-Null
    }
}

# 2) WanGP bridge (WanGP session initializes lazily on first render)
if (Test-Http "http://127.0.0.1:8189/health" 3) {
    Write-Ok "WanGP bridge already running on 8189"
} else {
    if (-not (Test-Path $WanGPPy)) {
        Write-Warn2 "WanGP venv python not found at $WanGPPy - WanGP engine will be unavailable."
    } else {
        Write-Step "Launching WanGP bridge"
        # Single argument string: PS 5.1 mangles array elements containing spaces
        # (the bridge path has spaces), so the quoted-array form fails to launch.
        Start-Process -FilePath $WanGPPy `
            -ArgumentList ('"' + $WanGPBridge + '"' + ' --port 8189') `
            -WorkingDirectory $StudioRoot -WindowStyle Hidden `
            -RedirectStandardOutput $WanGPLogOut -RedirectStandardError $WanGPLogErr
        Wait-Healthy "WanGP bridge" "http://127.0.0.1:8189/health" 30 | Out-Null
    }
}

# 3) Studio app last (the UI is the user's entry point)
if (Test-Http "http://127.0.0.1:7860/" 3) {
    Write-Ok "Studio app already running on 7860"
} else {
    Write-Step "Launching Studio app"
    Start-Process -FilePath $StudioPy `
        -ArgumentList "main.py" `
        -WorkingDirectory $StudioApp -WindowStyle Hidden `
        -RedirectStandardOutput $StudioLog -RedirectStandardError $StudioErr
    Wait-Healthy "Studio app" "http://127.0.0.1:7860/" $TimeoutSec | Out-Null
}

# 4) Final report
Write-Step "Final stack status"
$allUp = Show-StackHealth
Write-Host ""
if ($allUp) {
    Write-Host "Studio ready:  http://127.0.0.1:7860/" -ForegroundColor Green
    Write-Host "ComfyUI UI:    http://127.0.0.1:8188/" -ForegroundColor Green
    Write-Host "WanGP MCP:     app\tools\wangp_mcp_server.py (needs bridge on 8189)" -ForegroundColor Green
    exit 0
} else {
    Write-Warn2 "One or more services are not healthy. Check scratch\*.log"
    exit 1
}
