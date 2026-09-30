"""WanGP bridge auto-starter.

Launches the WanGP bridge sidecar (app/tools/wangp_bridge.py) with WanGP's own
venv python so the studio can route image/video/TTS jobs to WanGP without a
manual launch step. Mirrors the ComfyUI/OmniRoute auto-start pattern:

- If the bridge already answers /health (any python, any launcher), reuse it.
- Otherwise spawn it detached (CREATE_NO_WINDOW / DETACHED_PROCESS), logging to
  scratch/wangp_bridge.{out,err}.log.
- Health-check the spawned process; report (ok, unreachable) + tail of the error
  log on failure so Settings can show why.

Stdlib-only (urllib, not requests) so main.py can import it unconditionally,
even when HAS_LLM deps are missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

BRIDGE_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "wangp_bridge.py"
WANGP_PYTHON = Path(r"D:\01_PINOKIO\api\wan_sep2026.git\app\venv\Scripts\python.exe")
# Repo-root scratch/ (gitignored): app/ is the CWD when running the studio, so
# Path(__file__)/../scratch would resolve to app/scratch.
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "scratch"

_lock = threading.Lock()
_last_result: dict = {}


def bridge_host(settings: dict) -> str:
    """Bridge URL from settings (wangp.bridge_host), default 127.0.0.1:8189."""
    w = (settings or {}).get("wangp") or {}
    return w.get("bridge_host") or "http://127.0.0.1:8189"


def _health_once(host: str, timeout: float = 3.0) -> dict | None:
    """GET {host}/health with urllib. Returns parsed JSON or None."""
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def health(host: str, timeout: float = 3.0) -> dict:
    """Normalized health probe: {running, status, version, init_error, error}."""
    h = _health_once(host, timeout)
    if h is None:
        return {"running": False, "status": "unreachable",
                "error": f"WanGP bridge unreachable at {host}"}
    return {"running": True, "status": h.get("status", "ok"),
            "version": h.get("version"), "init_error": h.get("init_error"),
            "error": None}


def _error_log_tail() -> str:
    try:
        p = LOG_DIR / "wangp_bridge.err.log"
        if p.exists():
            lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            return "\n".join(lines[-5:])[:600]
        return ""
    except Exception:
        return ""


def ensure_running(settings: dict, wait_seconds: int = 25) -> dict:
    """Idempotent auto-start: reuse a live bridge, else launch one and wait.

    Returns {"success": bool, "already_running": bool, "started": bool,
             "host": str, "status": str, "version": str|None,
             "error": str|None, "log_tail": str}
    Never raises — startup code and the Settings button can call it safely.
    """
    global _last_result
    with _lock:
        host = bridge_host(settings)

        existing = health(host)
        if existing.get("running"):
            _last_result = {"success": True, "already_running": True, "started": False,
                            "host": host, "status": existing.get("status", "ok"),
                            "version": existing.get("version"), "error": None, "log_tail": ""}
            return _last_result

        if not BRIDGE_SCRIPT.exists():
            _last_result = {"success": False, "already_running": False, "started": False,
                            "host": host, "status": "unreachable",
                            "error": f"Bridge script not found: {BRIDGE_SCRIPT}",
                            "log_tail": ""}
            return _last_result
        if not WANGP_PYTHON.exists():
            _last_result = {"success": False, "already_running": False, "started": False,
                            "host": host, "status": "unreachable",
                            "error": f"WanGP venv python not found: {WANGP_PYTHON}",
                            "log_tail": ""}
            return _last_result

        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            out_log = open(LOG_DIR / "wangp_bridge.out.log", "ab")
            err_log = open(LOG_DIR / "wangp_bridge.err.log", "ab")
            out_log.write(f"\n===== bridge launch {time.strftime('%Y-%m-%d %H:%M:%S')} "
                          f"=====\n".encode())
            out_log.flush()
            # Detach: bridge must outlive the studio process (or not be killed by
            # the console) just like the ComfyUI/OmniRoute sidecars.
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            if sys.platform == "win32":
                flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(
                [str(WANGP_PYTHON), str(BRIDGE_SCRIPT), "--port", _port(host)],
                cwd=str(BRIDGE_SCRIPT.parent.parent),
                stdin=subprocess.DEVNULL, stdout=out_log, stderr=err_log,
                creationflags=flags, close_fds=True,
            )
        except Exception as e:
            _last_result = {"success": False, "already_running": False, "started": True,
                            "host": host, "status": "unreachable",
                            "error": f"Failed to spawn WanGP bridge: {e}", "log_tail": ""}
            return _last_result

        deadline = time.time() + wait_seconds
        last = health(host)
        while time.time() < deadline:
            last = health(host)
            if last.get("running"):
                _last_result = {"success": True, "already_running": False, "started": True,
                                "host": host, "status": last.get("status", "ok"),
                                "version": last.get("version"), "error": None, "log_tail": ""}
                return _last_result
            time.sleep(1.5)
        _last_result = {"success": False, "already_running": False, "started": True,
                        "host": host, "status": last.get("status", "unreachable"),
                        "error": (last.get("error")
                                  or "WanGP bridge did not answer /health in time"),
                        "log_tail": _error_log_tail()}
        return _last_result


def _port(host: str) -> str:
    """Extract the port from a bridge host URL (default 8189)."""
    from urllib.parse import urlparse
    try:
        p = urlparse(host if "://" in host else f"http://{host}")
        return str(p.port or 8189)
    except Exception:
        return "8189"
