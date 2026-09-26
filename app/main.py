import os
import json
import re as _re_mod
import shutil
import platform
import logging
import subprocess
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Body
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("film-studio")

try:
    from core.template_manager import TemplateManager
    from core.llm_engine import LLMEngine
    from core.llm_agents import LLMAgentRegistry
    from core.project_manager import ProjectManager
    from core.approval_workflow import ApprovalWorkflow
    from core.image_engine import ImageEngine
    from core.orchestrator import CinematicOrchestrator, load_master_system_prompt
    from core.agent_project_creator import AgentProjectCreator
    from core.film_agent import FilmAgent
    from core.studio_bridge import StudioBridge
    from core.film_orchestrator import ExecutiveProducer
    from core.telegram_bot import TelegramBotService
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

@asynccontextmanager
async def lifespan(app):
    try:
        settings = load_settings()
        changed = False
        if "llm" not in settings:
            settings["llm"] = {
                "provider": "omniroute",
                "model": "auto",
                "host": "http://127.0.0.1:20128",
                "apiKey": "",
                "auto_start": True,
                "n_gpu_layers": -1
            }
            changed = True
        else:
            llm_settings = settings["llm"]
            if not llm_settings.get("provider"):
                llm_settings["provider"] = "omniroute"
                llm_settings["host"] = "http://127.0.0.1:20128"
                changed = True
            if "auto_start" not in llm_settings:
                llm_settings["auto_start"] = True
                changed = True
            if "n_gpu_layers" not in llm_settings:
                llm_settings["n_gpu_layers"] = -1
                changed = True

        if "comfyui" not in settings:
            settings["comfyui"] = {
                "auto_start": True,
                "host": "http://localhost:8188",
                "port": 8188,
                "use_sage_attention": True,
                "path": "",
                "models_path": ""
            }
            changed = True
        else:
            cui = settings["comfyui"]
            if "auto_start" not in cui:
                cui["auto_start"] = True
                changed = True
            if "use_sage_attention" not in cui:
                cui["use_sage_attention"] = True
                changed = True
            if "port" not in cui:
                cui["port"] = 8188
                changed = True

        if "omniroute" not in settings:
            settings["omniroute"] = {
                "auto_start": True,
                "port": 20128
            }
            changed = True
        else:
            or_settings = settings["omniroute"]
            if "auto_start" not in or_settings:
                or_settings["auto_start"] = True
                changed = True
            if "port" not in or_settings:
                or_settings["port"] = 20128
                changed = True

        if changed:
            save_settings(settings)

        # Local LLM auto-start is disabled to prevent VRAM usage. It loads on-demand during generation and unloads immediately after.
        pass

        comfyui_settings = settings.get("comfyui", {})
        if comfyui_settings.get("auto_start", True) and comfyui_client:
            try:
                status = comfyui_client.get_comfyui_status()
                if not status.get("running"):
                    logger.info("Auto-starting ComfyUI...")
                    use_sa = comfyui_settings.get("use_sage_attention", True)
                    res = comfyui_client.launch_comfyui(
                        path=comfyui_settings.get("path") or None,
                        host=comfyui_settings.get("host", "0.0.0.0"),
                        port=comfyui_settings.get("port", 8188),
                        use_sage_attention=use_sa
                    )
                    logger.info(f"ComfyUI auto-launch status: {res}")
                else:
                    logger.info("ComfyUI already running — skipping auto-start")
            except Exception as e:
                logger.error(f"Error during ComfyUI auto-start: {e}", exc_info=True)

        # Auto-start OmniRoute gateway if enabled
        or_settings = settings.get("omniroute", {})
        if or_settings.get("auto_start", True):
            try:
                import shutil as _shutil
                if _shutil.which("omniroute"):
                    try:
                        _resp = requests.get("http://127.0.0.1:20128/api/v1/models", timeout=5)
                        if _resp.status_code == 200:
                            logger.info("OmniRoute already running -- skipping auto-start")
                        else:
                            raise Exception("not running")
                    except Exception:
                        logger.info("Auto-starting OmniRoute gateway...")
                        or_bin = _shutil.which("omniroute")
                        subprocess.Popen(
                            [or_bin, "serve", "--daemon", "--no-open", "--no-tray", "--port", str(or_settings.get("port", 20128))],
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                        )
            except Exception as e:
                logger.error(f"Error during OmniRoute auto-start: {e}")
    except Exception as e:
        logger.error(f"Error during startup: {e}", exc_info=True)
    if telegram_bot:
        telegram_bot.start()
    yield
    if telegram_bot:
        telegram_bot.stop()
    if llm_engine:
        llm_engine.cleanup_subprocesses()

app = FastAPI(title="Ultimate AI Film Studio", lifespan=lifespan)

# Global exception handler - always return JSON
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "message": str(exc) if str(exc) else "Internal server error"}
    )

base_dir = Path(__file__).parent
static_dir = base_dir / "ui" / "static"
templates_dir = base_dir / "ui" / "templates"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

templates = Jinja2Templates(directory=str(templates_dir)) if templates_dir.exists() else None

# Initialize core modules
template_manager = TemplateManager() if HAS_LLM else None
# Settings storage — persist outside app directory so updates never overwrite data
_APP_DATA_NAME = "UltimateAIFilmStudio"
if platform.system() == "Windows":
    _base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
else:
    _base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
DATA_DIR = _base / _APP_DATA_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = DATA_DIR / "settings.json"
GENRES_DIR = DATA_DIR / "genres"
VISUAL_STYLES_DIR = DATA_DIR / "visual_styles"
FILM_AESTHETICS_DIR = DATA_DIR / "film_aesthetics"

# Migrate old data from app/ directory on first run
_old_app_dir = Path(__file__).parent
for _name, _dir_var in [("settings.json", SETTINGS_FILE), ("genres", GENRES_DIR),
                         ("visual_styles", VISUAL_STYLES_DIR), ("film_aesthetics", FILM_AESTHETICS_DIR)]:
    _old = _old_app_dir / _name
    if _old.exists() and not _dir_var.exists():
        try:
            _dir_var.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(_old), str(_dir_var))
            logger.info("Migrated %s to %s", _name, _dir_var)
        except Exception as e:
            logger.warning("Could not migrate %s: %s", _name, e)
            # Copy instead of move if cross-drive move failed
            if _old.is_file() and not _dir_var.exists():
                shutil.copy2(str(_old), str(_dir_var))
                logger.info("Copied %s to %s", _name, _dir_var)
            elif _old.is_dir() and not _dir_var.exists():
                shutil.copytree(str(_old), str(_dir_var))
                logger.info("Copied directory %s to %s", _name, _dir_var)

GENRES_DIR.mkdir(exist_ok=True)
VISUAL_STYLES_DIR.mkdir(exist_ok=True)
FILM_AESTHETICS_DIR.mkdir(exist_ok=True)

llm_engine = LLMEngine(settings_path=str(SETTINGS_FILE)) if HAS_LLM else None
llm_agents = LLMAgentRegistry(llm_engine) if HAS_LLM else None
project_manager = ProjectManager() if HAS_LLM else None
approval_workflow = ApprovalWorkflow() if HAS_LLM else None
agent_creator = AgentProjectCreator(project_manager, llm_engine) if HAS_LLM else None
telegram_bot = TelegramBotService(str(SETTINGS_FILE), agent_creator) if HAS_LLM else None

# ComfyUI client - ALWAYS use placeholder to avoid auto-connect issues
# User can manually connect ComfyUI when they want to use it
from core.comfyui_client import ComfyUIClient
import core.comfyui_client as _comfyui_client_module  # for the Style DNA project stamp
comfyui_client = ComfyUIClient()
image_engine = ImageEngine(comfyui_client=comfyui_client, settings_path=str(SETTINGS_FILE)) if HAS_LLM else None
orchestrator = CinematicOrchestrator(llm_engine=llm_engine) if HAS_LLM else None
studio_bridge = StudioBridge(comfyui_client=comfyui_client, image_engine=image_engine, project_manager=project_manager) if HAS_LLM else None
film_agent = FilmAgent(llm_engine=llm_engine, project_manager=project_manager, orchestrator=orchestrator, studio_bridge=studio_bridge) if HAS_LLM else None
executive_producer = ExecutiveProducer(llm_engine=llm_engine, project_manager=project_manager, orchestrator=orchestrator) if HAS_LLM else None

class GenerateRequest(BaseModel):
    provider: str
    model: str
    prompt: str
    host: Optional[str] = None
    system_prompt: Optional[str] = None
    template_name: Optional[str] = None
    stage_name: Optional[str] = None
    variables: Optional[Dict] = None
    images: Optional[List[str]] = None
    api_key: Optional[str] = None

class QueueCancelRequest(BaseModel):
    prompt_id: str

class ProjectCreateRequest(BaseModel):
    name: str
    template_name: Optional[str] = None
    description: Optional[str] = ""
    location: Optional[str] = None

class ApprovalAction(BaseModel):
    action: str
    output_path: Optional[str] = None
    reason: Optional[str] = ""

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main UI."""
    try:
        html_file = base_dir / "ui" / "templates" / "index.html"
        if html_file.exists():
            with open(html_file, 'r', encoding='utf-8') as f:
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
                return HTMLResponse(content=f.read(), headers=headers)
    except Exception as e:
        logger.error("Error serving HTML: %s", e)
    return HTMLResponse(content=get_default_html())

@app.get("/timeline", response_class=HTMLResponse)
async def serve_timeline():
    """Legacy /timeline NLE page — removed; redirect to the app with its inline storyboard timeline."""
    return RedirectResponse(url="/", status_code=302)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

@app.get("/api/templates")
async def get_templates():
    """Get all available templates."""
    return {"success": True, "templates": template_manager.get_templates()}

@app.get("/api/templates/{name}")
async def get_template(name: str):
    """Get a specific template."""
    template = template_manager.get_template(name)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True, "template": template}

@app.get("/api/templates/{name}/stages")
async def get_template_stages(name: str):
    """Get stages for a template."""
    stages = template_manager.get_template_stages(name)
    return {"success": True, "stages": stages}

@app.get("/api/providers")
async def get_providers():
    """Get all LLM providers."""
    return {"success": True, "providers": llm_engine.get_providers()}

@app.get("/api/providers/{provider_id}/models")
async def get_models(provider_id: str):
    """Get models for a provider."""
    models = llm_engine.get_models(provider_id)
    return {"success": True, "models": models}

@app.post("/api/providers/{provider_id}/test")
async def test_provider(provider_id: str):
    """Test connection to a provider."""
    result = llm_engine.test_connection(provider_id)
    return result

@app.post("/api/generate")
def generate(request: GenerateRequest):
    """Generate content using LLM."""
    logger.info("POST /api/generate — provider=%s, model=%s, stage=%s, template=%s",
                 request.provider, request.model, request.stage_name, request.template_name)
    system_prompt = None
    if request.template_name and request.stage_name and request.variables:
        try:
            prompt = template_manager.render_prompt(
                request.template_name,
                request.stage_name,
                request.variables,
                format="full"
            )
        except Exception as e:
            return {"success": False, "error": f"Prompt rendering failed: {str(e)}"}

    if not llm_engine:
        return {"success": False, "error": "LLM engine not available"}

    provider_id = request.provider or request.app_provider or "omniroute"
    default_model = "auto" if provider_id == "omniroute" else "gemma-4-E2B-it-Q4_K_M.gguf" if provider_id == "app_llm" else "llama3.1"
    model = request.model or default_model

    # Cold-start verify/auto-start local LLM subprocess
    if provider_id in ("app_llm", "llama_cpp"):
        try:
            status = llm_engine.get_local_status(provider_id)
            if not status.get("running"):
                logger.info(f"Cold-starting local LLM provider '{provider_id}' for /api/generate...")
                llm_engine.launch_local_llm(provider_id, model)
                # Wait up to 30 seconds for health check
                prov_cfg = llm_engine.config.get("providers", {}).get(provider_id, {})
                prov_host = prov_cfg.get("host", "").replace("localhost", "127.0.0.1")
                health_ep = prov_cfg.get("health_endpoint", "")
                if prov_host and health_ep:
                    for _ in range(30):
                        try:
                            if requests.get(f"{prov_host}{health_ep}", timeout=2).status_code == 200:
                                break
                        except Exception:
                            pass
                        time.sleep(1)
        except Exception as start_err:
            logger.warning(f"Error handling cold start of local LLM: {start_err}")

    try:
        result = llm_engine.generate(
            provider_id=provider_id,
            model=model,
            prompt=request.prompt or "",
            system_prompt=system_prompt or request.system_prompt or "",
        )
    finally:
        if provider_id in ("app_llm", "llama_cpp"):
            try:
                logger.info(f"Offloading/stopping local LLM provider '{provider_id}' after generation...")
                llm_engine.stop_local_llm(provider_id)
            except Exception as offload_err:
                logger.warning(f"Error offloading local LLM provider: {offload_err}")

    return result

# === LLM Subprocess Management ===

@app.get("/api/llm/detect")
async def detect_local_llm(provider: str):
    """Detect if a local LLM binary is installed."""
    return llm_engine.detect_local_llm(provider) if llm_engine else {"installed": False, "error": "LLM engine not available"}

@app.get("/api/llm/status")
def get_llm_status(provider: str):
    """Check if a local LLM is running.

    Sync on purpose: this is polled every few seconds and does blocking network
    I/O; running it as async would freeze the event loop.
    """
    return llm_engine.get_local_status(provider) if llm_engine else {"running": False}

@app.get("/api/pick-folder")
def pick_folder():
    """Open the native OS folder picker dialog and return the selected path.

    Uses tkinter.filedialog which renders the real Windows Explorer
    folder picker on Windows, GTK dialog on Linux, etc.
    Runs in a thread so it doesn't block the event loop.
    """
    import threading
    result = {"path": None, "cancelled": True}
    lock = threading.Event()

    def _pick():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            folder = filedialog.askdirectory(
                title="Select Project Folder",
                mustexist=True
            )
            root.destroy()
            if folder:
                result["path"] = folder
                result["cancelled"] = False
        except Exception as e:
            result["error"] = str(e)
        finally:
            lock.set()

    t = threading.Thread(target=_pick, daemon=True)
    t.start()
    lock.wait(timeout=60)
    return result

@app.get("/api/browse-directory")
def browse_directory(path: str = ""):
    """List subdirectories at the given path for the folder browser UI.

    On Windows with no path, returns drives with labels and free space
    like Windows Explorer.
    """
    import os
    import shutil

    def _get_drive_info(drive_path: str) -> dict:
        """Get Windows drive label and space info."""
        info = {
            "name": drive_path,
            "path": drive_path,
            "is_drive": True,
            "label": "",
            "total_bytes": 0,
            "free_bytes": 0,
            "used_bytes": 0,
            "usage_percent": 0,
        }
        try:
            total, used, free = shutil.disk_usage(drive_path)
            info["total_bytes"] = total
            info["free_bytes"] = free
            info["used_bytes"] = used
            info["usage_percent"] = round((used / total) * 100) if total > 0 else 0
        except Exception:
            pass
        # Try to get drive label via ctypes (Windows only)
        if os.name == 'nt':
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                buf = ctypes.create_unicode_buffer(256)
                kernel32.GetVolumeInformationW(drive_path, buf, 256, None, None, None, None, 0)
                if buf.value:
                    info["label"] = buf.value
            except Exception:
                pass
        return info

    def _format_bytes(size: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    if not path:
        if os.name == 'nt':
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\\\"
                if os.path.exists(drive):
                    info = _get_drive_info(drive)
                    # Build display name like Explorer: "Local Disk (C:)" or "Data (D:)"
                    label = info.get("label", "")
                    free = _format_bytes(info.get("free_bytes", 0))
                    total = _format_bytes(info.get("total_bytes", 0))
                    display = f"{label} ({letter}:)" if label else f"Local Disk ({letter}:)"
                    info["display"] = display
                    info["free_display"] = f"{free} free of {total}"
                    drives.append(info)
            return {"success": True, "current": "", "drives": drives, "entries": []}
        else:
            path = os.path.expanduser("~")

    try:
        path = os.path.normpath(path)
        if not os.path.isdir(path):
            return {"success": False, "error": f"Not a directory: {path}"}

        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isdir(full) and not name.startswith('.'):
                # Count children for folder size hint
                child_count = 0
                try:
                    child_count = len([x for x in os.listdir(full) if os.path.isdir(os.path.join(full, x))])
                except Exception:
                    pass
                entries.append({
                    "name": name,
                    "path": full,
                    "child_count": child_count
                })

        # Add parent directory link
        parent = os.path.dirname(path)
        if parent != path:
            entries.insert(0, {"name": "..", "path": parent, "is_parent": True})

        return {"success": True, "current": path, "entries": entries, "drives": []}
    except PermissionError:
        return {"success": False, "error": "Permission denied"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/omniroute/status")
def get_omniroute_status():
    """Check if OmniRoute gateway is running.

    Uses the root endpoint (/) for a fast heartbeat instead of /api/v1/models
    which loads all 745 models and can be slow under load.
    """
    # Quick TCP check first — cheapest way to see if OmniRoute is listening
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    try:
        result = sock.connect_ex(("127.0.0.1", 20128))
        if result == 0:
            sock.close()
            return {"running": True}
    except Exception:
        pass
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return {"running": False}

@app.get("/api/gpu/status")
async def get_gpu_status():
    """Check if GPU (CUDA) is available for llama.cpp."""
    if llm_engine:
        return {"gpu_available": llm_engine._gpu_available()}
    return {"gpu_available": False}

@app.get("/api/system/stats")
def get_system_stats():
    """Get system resource usage statistics (CPU, RAM, GPU, GPU Temp).

    Sync on purpose: this runs blocking subprocess calls (wmic, nvidia-smi) and is
    polled every few seconds; as async it would freeze the event loop for seconds
    at a time, stalling every other request.
    """
    import subprocess
    import os
    cpu_usage = 0
    ram_usage = 0
    gpu_usage = 0
    gpu_temp = 0

    # 1. CPU Usage
    try:
        if platform.system() == "Windows":
            proc = subprocess.run(["wmic", "cpu", "get", "loadpercentage"], capture_output=True, text=True, timeout=2)
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if len(lines) > 1 and lines[1].isdigit():
                cpu_usage = int(lines[1])
        else:
            with open("/proc/loadavg", "r") as f:
                cpu_usage = float(f.read().split()[0]) * 100 / (os.cpu_count() or 1)
    except Exception:
        pass

    # 2. RAM Usage
    try:
        if platform.system() == "Windows":
            proc = subprocess.run(["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize"], capture_output=True, text=True, timeout=2)
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    free_mem = int(parts[0])
                    total_mem = int(parts[1])
                    if total_mem > 0:
                        ram_usage = round(((total_mem - free_mem) / total_mem) * 100, 1)
        else:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].replace(":", "")] = int(parts[1])
                total = meminfo.get("MemTotal", 1)
                free = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
                ram_usage = round(((total - free) / total) * 100, 1)
    except Exception:
        pass

    # 3. GPU Usage & Temp
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if proc.returncode == 0:
            parts = proc.stdout.strip().split(",")
            if len(parts) >= 2:
                gpu_usage = int(parts[0].strip())
                gpu_temp = int(parts[1].strip())
    except Exception:
        pass

    return {
        "cpu": int(cpu_usage),
        "ram": float(ram_usage),
        "gpu": int(gpu_usage),
        "temp": int(gpu_temp)
    }

@app.post("/api/llm/start")
async def start_local_llm(data: dict):
    """Start a local LLM as a subprocess."""
    if not llm_engine:
        return {"success": False, "error": "LLM engine not available"}
    provider = data.get("provider", "")
    model_path = data.get("model_path", None)
    return llm_engine.launch_local_llm(provider, model_path)

@app.post("/api/llm/stop")
async def stop_local_llm(data: dict):
    """Stop a local LLM subprocess."""
    if not llm_engine:
        return {"success": False, "error": "LLM engine not available"}
    return llm_engine.stop_local_llm(data.get("provider", ""))

# === App LLM Model Management ===

@app.get("/api/llm/installed-models")
async def get_installed_models():
    """List models in the models/ folder."""
    if not llm_engine:
        return {"models": []}
    return {"models": llm_engine.get_installed_models()}

@app.get("/api/llm/search-models")
async def search_llm_models(q: str = "", size: str = "", quant: str = "", limit: int = 30):
    """Search HuggingFace for GGUF models."""
    if not llm_engine:
        return {"results": [], "error": "LLM engine not available"}
    results = llm_engine.search_huggingface_models(query=q, size_filter=size, quant_filter=quant, limit=limit)
    return {"results": results}

@app.post("/api/llm/download-model-hf")
async def download_llm_model(data: dict):
    """Download a GGUF model from HuggingFace."""
    if not llm_engine:
        return {"success": False, "error": "LLM engine not available"}
    repo = data.get("repo", "")
    filename = data.get("filename", "")
    if not repo or not filename:
        return {"success": False, "error": "repo and filename required"}
    return llm_engine.download_model_from_hf(repo, filename)

@app.get("/api/llm/download-progress")
async def get_llm_download_progress(filename: str):
    """Get status of an active model download."""
    if not llm_engine:
        return {"status": "failed", "error": "LLM engine not available"}
    return llm_engine.get_download_status(filename)

@app.post("/api/llm/copy-model")
async def copy_llm_model(data: dict):
    """Copy a model file from user path into models/ folder."""
    if not llm_engine:
        return {"success": False, "error": "LLM engine not available"}
    source = data.get("source", "")
    if not source:
        return {"success": False, "error": "source path required"}
    return llm_engine.copy_model_to_folder(source)

@app.delete("/api/llm/models/{filename:path}")
async def delete_llm_model(filename: str):
    """Delete a model file from models/ folder."""
    if not llm_engine:
        return {"success": False, "error": "LLM engine not available"}
    return llm_engine.delete_model(filename)

# === LLM Agents ===

@app.get("/api/llm/agents")
async def list_llm_agents():
    """List available LLM prompt-engineering agents."""
    if not llm_agents:
        return {"success": False, "error": "LLM agents not available"}
    return {"success": True, "agents": llm_agents.list_agents()}

@app.post("/api/llm/agents/{agent_id}/run")
async def run_llm_agent(agent_id: str, data: dict):
    """Run an LLM agent to refine a prompt."""
    if not llm_agents:
        return {"success": False, "error": "LLM agents not available"}
    prompt = data.get("prompt", "")
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    return llm_agents.run(
        agent_id,
        prompt,
        provider=data.get("provider"),
        model=data.get("model"),
        api_key=data.get("api_key"),
        host=data.get("host"),
    )

# === ComfyUI Model Management ===

@app.get("/api/comfyui/search-models")
async def search_comfyui_models(q: str = "", model_type: str = "checkpoints", limit: int = 30):
    """Search HuggingFace for ComfyUI-compatible models."""
    if not comfyui_client:
        return {"results": []}
    results = comfyui_client.search_huggingface_models(query=q, model_type=model_type, limit=limit)
    return {"results": results}

@app.post("/api/comfyui/download-model")
async def download_comfyui_model(data: dict):
    """Download a model to ComfyUI models folder."""
    if not comfyui_client:
        return {"success": False, "error": "ComfyUI client not available"}
    repo = data.get("repo", "")
    filename = data.get("filename", "")
    model_type = data.get("model_type", "checkpoints")
    if not repo or not filename:
        return {"success": False, "error": "repo and filename required"}
    logger.info("POST /api/comfyui/download-model — repo=%s, file=%s, type=%s", repo, filename, model_type)
    return comfyui_client.download_model(repo, filename, model_type)

@app.get("/api/comfyui/installed-models")
async def get_installed_comfyui_models(model_type: str = "checkpoints"):
    """List installed models in ComfyUI models folder."""
    if not comfyui_client:
        return {"models": []}
    return {"models": comfyui_client.get_installed_comfyui_models(model_type)}

@app.get("/api/comfyui/model-types")
async def get_comfyui_model_types():
    """Get available ComfyUI model type categories."""
    if not comfyui_client:
        return {"types": {}}
    types = {}
    for key, info in comfyui_client.COMFYUI_MODEL_TYPES.items():
        types[key] = info["folder"]
    return {"types": types}

@app.post("/api/comfyui/update")
async def update_comfyui():
    """Update ComfyUI via git pull."""
    if not comfyui_client:
        return {"success": False, "error": "ComfyUI client not available"}
    return comfyui_client.update_comfyui()

# === Cinematic Orchestrator Routes ===

@app.get("/api/orchestrator/skill")
async def get_orchestrator_skill():
    """Get the master cinematic system prompt."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return {"success": True, "skill": orchestrator.get_master_system_prompt()}

@app.get("/api/orchestrator/progress")
async def get_orchestrator_progress():
    """Get current generation progress."""
    if not orchestrator:
        return {"pct": 0, "title": "Not available", "sub": "", "finished": True, "error": ""}
    return orchestrator.get_progress()

@app.post("/api/orchestrator/ideas")
def generate_ideas(data: dict):
    """Stage 1: Generate 5 cinematic story ideas."""
    logger.info("POST /api/orchestrator/ideas — genres=%s", data.get("genres"))
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_ideas(data)
    return result

@app.post("/api/orchestrator/screenplay")
def generate_screenplay(data: dict):
    """Stage 2: Generate master screenplay from selected idea."""
    logger.info("POST /api/orchestrator/screenplay — idea_index=%s", data.get("idea_index"))
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_screenplay(data)
    return result

@app.post("/api/orchestrator/regenerate-idea")
def regenerate_idea(data: dict):
    """Regenerate a single idea."""
    logger.info("POST /api/orchestrator/regenerate-idea")
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.regenerate_single_idea(data)
    return result

@app.post("/api/orchestrator/idea-variants")
def generate_idea_variants(data: dict):
    """Generate 3 variants of an idea based on user change request."""
    logger.info("POST /api/orchestrator/idea-variants — change=%s", data.get("user_change", "")[:60])
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_idea_variants(data)
    return result

@app.post("/api/orchestrator/locations")
def generate_locations(data: dict = None):
    """Stage 3: Generate reusable location assets."""
    logger.info("POST /api/orchestrator/locations")
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_locations(data or {})
    return result

@app.post("/api/orchestrator/characters")
def generate_characters(data: dict = None):
    """Stage 4: Generate reusable character assets."""
    logger.info("POST /api/orchestrator/characters")
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_characters(data or {})
    return result

@app.post("/api/orchestrator/storyboard")
def generate_storyboard(data: dict = None):
    """Stage 5: Generate storyboard with shots."""
    logger.info("POST /api/orchestrator/storyboard")
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_storyboard(data or {})
    
    if result.get("success"):
        try:
            name = project_manager.current_project
            if name:
                state = project_manager.load_project_state(name) or {}
                # Update screenplayData scenes/shots from orchestrator memory
                pg = orchestrator.memory.project_graph
                scene_graph = pg.get("scene_graph", [])
                if scene_graph:
                    scr = state.get("screenplayData", {}) or {}
                    scr["scenes"] = scene_graph
                    state["screenplayData"] = scr
                
                # Update orchestrator memory in state
                state["_orchestrator_memory"] = orchestrator.to_dict()
                project_manager.save_project_state(name, state)
                logger.info("Successfully auto-saved project state after storyboard generation")
        except Exception as e:
            logger.error("Failed to auto-save project state after storyboard generation: %s", e)
            
    return result

@app.post("/api/orchestrator/video-prompts")
def generate_video_prompts(data: dict = None):
    """Stage 6: Generate LTX 2.3 video prompts."""
    logger.info("POST /api/orchestrator/video-prompts")
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_video_prompts(data or {})
    
    if result.get("success"):
        try:
            name = project_manager.current_project
            if name:
                state = project_manager.load_project_state(name) or {}
                # Update screenplayData scenes/shots from orchestrator memory
                pg = orchestrator.memory.project_graph
                scene_graph = pg.get("scene_graph", [])
                if scene_graph:
                    scr = state.get("screenplayData", {}) or {}
                    scr["scenes"] = scene_graph
                    state["screenplayData"] = scr
                
                # Update orchestrator memory in state
                state["_orchestrator_memory"] = orchestrator.to_dict()
                project_manager.save_project_state(name, state)
                logger.info("Successfully auto-saved project state after video prompts generation")
        except Exception as e:
            logger.error("Failed to auto-save project state after video prompts generation: %s", e)
            
    return result

@app.get("/api/orchestrator/memory")
async def get_orchestrator_memory():
    """Get full orchestrator memory state."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return {"success": True, "memory": orchestrator.to_dict()}

@app.post("/api/orchestrator/memory")
async def set_orchestrator_memory(data: dict):
    """Set full orchestrator memory state."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    memory_data = data.get("memory", {})
    orchestrator.from_dict(memory_data)
    return {"success": True}

@app.get("/api/orchestrator/continuity-context")
async def get_continuity_context():
    """Get the current continuity context string."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return {"success": True, "context": orchestrator.get_continuity_context()}

@app.post("/api/orchestrator/reset")
async def reset_orchestrator():
    """Reset orchestrator memory."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    orchestrator.reset()
    return {"success": True}

# === V2.0: Shot Variant, Lock, Approval, Turnaround, Asset Studio Routes ===

@app.post("/api/orchestrator/shot-variant")
def create_shot_variant(data: dict):
    """Generate 3 non-destructive variants of a shot node."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.create_shot_variant(data)
    return result

@app.post("/api/orchestrator/asset-lock")
async def set_asset_lock(data: dict):
    """Lock/unlock a character, location, or shot field."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.set_lock(data)
    return result

@app.get("/api/orchestrator/locks")
async def get_locks():
    """Get all continuity lock states."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.get_locks()

@app.post("/api/orchestrator/approve-characters")
async def approve_characters(request: Request):
    """Approve characters, enabling storyboard generation. Auto-locks image_prompt."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    result = orchestrator.approve_characters(data)
    if result.get("success"):
        _save_orchestrator_state()
    return result

@app.post("/api/orchestrator/approve-locations")
async def approve_locations(request: Request):
    """Approve locations, enabling storyboard generation. Auto-locks image_prompt."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    result = orchestrator.approve_locations(data)
    if result.get("success"):
        _save_orchestrator_state()
    return result

@app.get("/api/orchestrator/approvals")
async def get_approvals():
    """Get current character/location approval state."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.get_approvals()

@app.post("/api/orchestrator/generate-turnaround")
def generate_turnaround(data: dict):
    """Generate a turnaround sheet for an approved character."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_turnaround(data)
    if result.get("success"):
        _save_orchestrator_state()
    return result

@app.post("/api/orchestrator/describe-character-image")
def describe_character_image(data: dict):
    """Use LLM vision to describe a character from their approved image."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    character_id = data.get("character_id", "")
    image_path = data.get("image_path", "")
    if not character_id:
        return {"success": False, "error": "character_id required"}
    # If no image_path provided, look up from character_assets
    if not image_path:
        pg = orchestrator.memory.project_graph
        assets = pg.get("character_assets", {}).get(character_id, {})
        image_path = assets.get("approved_image", "")
    if not image_path:
        return {"success": False, "error": "No approved image found for this character"}
    project_path = data.get("project_path", "")
    # Resolve relative path against project directory
    if project_path and not image_path.startswith('/') and not image_path.startswith(''):
        from pathlib import Path as _P
        full_path = _P(project_path) / image_path
        if full_path.exists():
            return orchestrator.describe_character_image(str(full_path))
    return orchestrator.describe_character_image(image_path)


@app.post("/api/orchestrator/describe-location-image")
def describe_location_image(data: dict):
    """Use LLM vision to describe a location from their approved image."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    location_id = data.get("location_id", "")
    image_path = data.get("image_path", "")
    if not location_id:
        return {"success": False, "error": "location_id required"}
    if not image_path:
        pg = orchestrator.memory.project_graph
        assets = pg.get("location_assets", {}).get(location_id, {})
        image_path = assets.get("approved_image", "")
    if not image_path:
        return {"success": False, "error": "No approved image found for this location"}
    project_path = data.get("project_path", "")
    if project_path and not image_path.startswith('/') and not image_path.startswith(''):
        from pathlib import Path as _P
        full_path = _P(project_path) / image_path
        if full_path.exists():
            return orchestrator.describe_location_image(str(full_path))
    return orchestrator.describe_location_image(image_path)

@app.get("/api/orchestrator/asset-studio")
async def get_asset_studio(path: Optional[str] = None):
    """Get full asset studio state (bibles, assets, approvals, locks)."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.get_asset_studio_state(project_path=path)

@app.post("/api/orchestrator/save-character-sheet")
def save_character_sheet(data: dict):
    """Save/update character sheet state in project_graph."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    character_id = data.get("character_id", "")
    sheet_image = data.get("sheet_image", "")
    approved = data.get("approved", False)
    if not character_id:
        return {"success": False, "error": "character_id required"}
    pg = orchestrator.memory.project_graph
    sheets = pg.setdefault("character_sheets", {})
    entry = sheets.get(character_id, {"character_id": character_id, "generation_history": []})
    if sheet_image:
        entry["sheet_image"] = sheet_image
        history = entry.setdefault("generation_history", [])
        if sheet_image not in history:
            history.append(sheet_image)
    if approved:
        entry["approved"] = True
        current_img = entry.get("sheet_image", "")
        if current_img:
            p_path = project_manager.get_current_project_path()
            if p_path:
                project_path = str(p_path)
                src_rel = current_img.split("?")[0]
                if not src_rel.startswith("character_sheets/"):
                    src_rel = f"character_sheets/{src_rel}"
                src_file = Path(project_path) / src_rel
                dest_rel = f"character_sheets/approved/{character_id}.png"
                dest_file = Path(project_path) / dest_rel
                if src_file.exists() and src_file != dest_file:
                    try:
                        import shutil
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest_file)
                        logger.info("[Approve Sheet] Copied %s to %s", src_file, dest_file)
                        entry["sheet_image"] = dest_rel
                        if dest_rel not in entry["generation_history"]:
                            entry["generation_history"].append(dest_rel)
                    except Exception as e:
                        logger.error("[Approve Sheet] Copy failed: %s", e)
    sheets[character_id] = entry
    _save_orchestrator_state()
    return {"success": True, "character_sheets": sheets}

@app.post("/api/orchestrator/save-location-sheet")
def save_location_sheet(data: dict):
    """Save/update location sheet state in project_graph."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    location_id = data.get("location_id", "")
    sheet_image = data.get("sheet_image", "")
    approved = data.get("approved", False)
    if not location_id:
        return {"success": False, "error": "location_id required"}
    pg = orchestrator.memory.project_graph
    sheets = pg.setdefault("location_sheets", {})
    entry = sheets.get(location_id, {"location_id": location_id, "generation_history": []})
    if sheet_image:
        entry["sheet_image"] = sheet_image
        history = entry.setdefault("generation_history", [])
        if sheet_image not in history:
            history.append(sheet_image)
    if approved:
        entry["approved"] = True
        current_img = entry.get("sheet_image", "")
        if current_img:
            p_path = project_manager.get_current_project_path()
            if p_path:
                project_path = str(p_path)
                src_rel = current_img.split("?")[0]
                if not src_rel.startswith("location_sheets/"):
                    src_rel = f"location_sheets/{src_rel}"
                src_file = Path(project_path) / src_rel
                dest_rel = f"location_sheets/approved/{location_id}.png"
                dest_file = Path(project_path) / dest_rel
                if src_file.exists() and src_file != dest_file:
                    try:
                        import shutil
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dest_file)
                        logger.info("[Approve Sheet] Copied %s to %s", src_file, dest_file)
                        entry["sheet_image"] = dest_rel
                        if dest_rel not in entry["generation_history"]:
                            entry["generation_history"].append(dest_rel)
                    except Exception as e:
                        logger.error("[Approve Sheet] Copy failed: %s", e)
    sheets[location_id] = entry
    _save_orchestrator_state()
    return {"success": True, "location_sheets": sheets}

@app.post("/api/orchestrator/sync-bibles")
def sync_bibles(data: dict):
    """Sync frontend character/location/prop bible data into orchestrator project_graph."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.sync_bibles_from_frontend(
        character_bible=data.get("character_bible"),
        location_bible=data.get("location_bible"),
        prop_bible=data.get("prop_bible"),
    )

def _save_orchestrator_state():
    """Persist orchestrator memory to disk immediately (no debounce)."""
    if not orchestrator or not project_manager.current_project:
        return
    try:
        name = project_manager.current_project
        proj_path = project_manager.get_current_project_path()
        if not proj_path or not proj_path.exists():
            return
        state = project_manager.load_project_state(name, str(proj_path))
        if state is None:
            state = {}
        state["_orchestrator_memory"] = orchestrator.to_dict()
        project_manager.save_project_state(name, state, str(proj_path))
    except Exception:
        pass

@app.post("/api/orchestrator/generate-asset-prompt")
def generate_asset_prompt(data: dict):
    """Generate image_prompt for a character or location using LLM."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_asset_prompt(
        asset_type=data.get("type", ""),
        asset=data.get("asset", {}),
    )
    if result.get("success"):
        _save_orchestrator_state()
    return result

@app.post("/api/orchestrator/generate-variant")
def generate_asset_variant(data: dict):
    """Generate 3 variant prompts for an asset."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_asset_variant(
        asset_type=data.get("type", ""),
        asset=data.get("asset", {}),
        current_image=data.get("current_image", ""),
        variant_instruction=data.get("instruction", ""),
    )
    if result.get("success"):
        _save_orchestrator_state()
    return result

# === V2.0: Shot-level endpoints ===

@app.get("/api/orchestrator/shot/{shot_id}")
async def get_shot(shot_id: str):
    """Get enriched shot data including variants, locks, enrichment."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.get_shot(shot_id)

@app.post("/api/orchestrator/shot-regenerate")
def regenerate_shot(data: dict):
    """Regenerate a single shot preserving continuity and locks."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.regenerate_shot(data)

@app.post("/api/orchestrator/pace-shots")
async def pace_shots_endpoint(data: dict):
    """Ask the LLM to time every shot by its content so the timeline shows dynamic pacing."""
    shots = data.get("shots", [])
    if not shots:
        return {"success": False, "error": "shots list is required"}
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.pace_shots(shots)


@app.post("/api/orchestrator/verify-scene-consistency")
def verify_scene_consistency(data: dict):
    """Verify visual consistency of all shots in a scene using Vision LLM."""
    logger.info(f"POST /api/orchestrator/verify-scene-consistency: {data.get('scene_id')}")
    return orchestrator.verify_scene_consistency(data)


@app.post("/api/orchestrator/derive-style-dna")
async def derive_style_dna_endpoint(data: dict):
    """Derive a one-sentence Style DNA from a golden reference frame via the vision LLM."""
    image_path = data.get("image_path", "")
    if not image_path:
        return {"success": False, "error": "image_path is required"}
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}

    img_bytes = None
    try:
        if image_path.startswith("ComfyUI/output/"):
            import requests as _rq
            host = comfyui_client.host if comfyui_client else "http://127.0.0.1:8188"
            r = _rq.get(f"{host}/view", params={"filename": image_path.split("/")[-1]}, timeout=15)
            if r.status_code == 200:
                img_bytes = r.content
        if img_bytes is None:
            p = Path(image_path)
            if not p.is_absolute():
                pp = data.get("project_path") or (project_manager.get_current_project_path() if project_manager else None)
                if pp:
                    p = Path(str(pp)) / image_path
            if p.exists():
                img_bytes = p.read_bytes()
    except Exception as e:
        logger.warning("derive-style-dna: failed to load image %s: %s", image_path, e)
    if not img_bytes:
        return {"success": False, "error": f"Could not load image: {image_path}"}

    import base64 as _b64
    img_b64 = _b64.b64encode(img_bytes).decode("utf-8")
    prompt = (
        "You are a cinematographer looking at a single frame of a film. "
        "Describe ONLY its visual style — never its content, characters or story. "
        "Cover: color palette, lighting quality and direction, contrast, film grain and texture, "
        "lens and depth of field, color grade, and era or film-stock feel. "
        "Write ONE imperative style sentence (max 30 words) that can prefix every prompt of a film "
        "so all its shots share this exact look. "
        'Reply with ONLY a JSON object: {"style_dna": "<the sentence>"}'
    )
    result = orchestrator._call_llm(prompt, system_suffix="Output ONLY valid JSON.", json_output=True, images=[img_b64])
    if result.get("success"):
        d = result.get("data")
        sd = d.get("style_dna") if isinstance(d, dict) else ""
        sd = (sd or "").strip()
        if sd:
            return {"success": True, "style_dna": sd}
        return {"success": False, "error": "VLM returned no style_dna field", "raw": (result.get("raw") or "")[:300]}
    return {"success": False, "error": result.get("error", "VLM call failed")}

@app.post("/api/orchestrator/regenerate-shot-with-reference")
def regenerate_shot_with_reference(data: dict):
    """Regenerate a shot with a golden reference image and feedback."""
    logger.info(f"POST /api/orchestrator/regenerate-shot-with-reference: {data.get('shot_id')}")
    return orchestrator.regenerate_shot_with_reference(data)

@app.post("/api/orchestrator/shot-approve")
def approve_shot(data: dict):
    """Mark a shot as approved for timeline."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.approve_shot(data)

@app.post("/api/orchestrator/sync-shots")
def sync_shots(data: dict):
    """Sync screenplay shots into orchestrator project_graph for approve/lock."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.sync_shots_from_screenplay(data.get("screenplay", {}))

@app.post("/api/orchestrator/generate-shot-image")
def generate_shot_image(data: dict):
    """Generate storyboard image prompt for a shot."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.generate_shot_image(data)

@app.post("/api/orchestrator/generate-shot-from-scene")
def generate_shot_from_scene(data: dict):
    """Generate 3 shot variants from scene context (no existing shot needed)."""
    return orchestrator.generate_shot_from_scene(data)

def _save_cropped_background(host: str, project_path: str, filename: str, subfolder: str,
                             scene_id: str, pan_angle: int, previous_file: str = None) -> Optional[str]:
    """Download a cropped 360 background from ComfyUI and store it under
    <project>/backgrounds/ so the storyboard i2i pipeline can resolve the path.

    Returns the project-relative path (e.g. "backgrounds/scene_1_45_bg.png") or
    None if the download/save failed. Removes the previous extracted background
    file when it lives in the same folder (re-extract at a new pan angle).
    """
    import re
    import requests
    import time
    url = f"{host}/view?filename={filename}"
    if subfolder:
        url += f"&subfolder={subfolder}"
    url += "&type=output"
    resp = None
    last_error = ""
    for attempt in range(5):
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                break
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = str(e)
        time.sleep(1.0)
    if not resp or resp.status_code != 200:
        logger.error("[crop-360] failed to download output %s: %s", filename, last_error)
        return None

    ext = Path(filename).suffix or ".png"
    scene = re.sub(r"[^A-Za-z0-9_]+", "_", str(scene_id or "scene")) or "scene"
    safe_name = f"{scene}_{int(pan_angle or 0)}_bg{ext}"
    bg_dir = Path(project_path) / "backgrounds"
    try:
        bg_dir.mkdir(parents=True, exist_ok=True)
        dest = bg_dir / safe_name
        dest.write_bytes(resp.content)
    except Exception as e:
        logger.error("[crop-360] failed to save background: %s", e)
        return None

    if previous_file:
        try:
            prev = Path(project_path) / previous_file
            if prev.exists() and prev.resolve().parent == bg_dir.resolve() and prev != dest:
                prev.unlink()
                logger.info("[crop-360] removed previous background %s", previous_file)
        except Exception as e:
            logger.warning("[crop-360] could not remove previous background: %s", e)

    return f"backgrounds/{safe_name}"


@app.post("/api/orchestrator/crop-360")
async def crop_360_background(data: dict):
    """Crop a 360 panorama using the crop workflow."""
    import asyncio
    location_id = data.get("location_id")
    pan_angle = data.get("pan_angle", 0)  # 0 to 360
    project_path = data.get("project_path")
    previous_file = data.get("previous_file")
    scene_id = data.get("scene_id")
    
    if not location_id or not project_path:
        return {"success": False, "error": "Missing location_id or project_path"}
        
    # Get the location image from project manager state
    state = project_manager.load_project_state(project_manager.current_project, project_path) or {}
    locs = state.get("location_sheets", {})
    if location_id not in locs:
        return {"success": False, "error": f"No location sheet found for {location_id}"}
        
    img_path = locs[location_id].get("approved_image", "")
    if not img_path:
        return {"success": False, "error": "No approved image found for location"}
        
    # Calculate crop_left based on pan_angle (assuming 2048x1024 base image)
    # The workflow concatenates the image with itself, creating a 4096x1024 image
    # A 360 pan covers the first 2048 pixels.
    crop_x = int((pan_angle / 360.0) * 2048)
    
    from app.core.comfyui_client import ComfyUIClient
    client = ComfyUIClient()
    
    # Load the crop workflow, update x, and save to a temporary workflow file
    try:
        import json
        with open("app/workflows/crop_360_background.json", "r") as f:
            workflow = json.load(f)
            
        # Update inputs
        for nid, nd in workflow.items():
            if not isinstance(nd, dict): continue
            if nd.get("class_type") == "ImageCrop":
                nd["inputs"]["x"] = crop_x
                
        # Save dynamically generated workflow
        temp_workflow_name = "crop_360_temp"
        with open(f"app/workflows/{temp_workflow_name}.json", "w") as f:
            json.dump(workflow, f)
    except Exception as e:
        return {"success": False, "error": f"Failed to load crop workflow: {str(e)}"}
        
    # Send to ComfyUI
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        None,
        client.generate_with_workflow,
        "crop_360", # dummy prompt
        temp_workflow_name,
        None, # negative prompt
        None, # seed
        [img_path] # input image
    )
    
    # Cleanup temp workflow
    try:
        import os
        os.remove(f"app/workflows/{temp_workflow_name}.json")
    except:
        pass
    
    if not res.get("success"):
        return res

    # Save the crop into the project so the storyboard i2i pipeline can resolve
    # it and feed it into the background slot of the char+bg workflow.
    saved_rel = await loop.run_in_executor(
        None,
        _save_cropped_background,
        client.host, project_path,
        res.get("filename", ""), res.get("subfolder", ""),
        scene_id, pan_angle, previous_file
    )
    if saved_rel:
        logger.info("[crop-360] saved background to project: %s", saved_rel)
        return {
            "success": True,
            "filename": saved_rel,
            "path": str(Path(project_path) / saved_rel),
            "subfolder": "",
        }

    # Crop succeeded but the file could not be stored in the project; fall back
    # to the raw ComfyUI output filename (old behavior).
    logger.warning("[crop-360] crop OK but could not save into project; returning raw ComfyUI filename")
    return res

@app.post("/api/orchestrator/generate-scene-shots")
def generate_scene_shots(data: dict):
    """Generate shot nodes for a single scene."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_scene_shots(data)
    return result

# === Image / Video Generation Provider Management ===

@app.get("/api/image/providers")
async def get_image_providers():
    """List available image/video generation providers."""
    if not image_engine:
        return {"success": True, "providers": []}
    return {"success": True, "providers": image_engine.get_providers()}

@app.post("/api/image/test")
async def test_image_provider(data: dict):
    """Test connection to an image/video provider."""
    if not image_engine:
        return {"success": False, "error": "Image engine not available"}
    return image_engine.test_connection(
        provider_id=data.get("provider", ""),
        host=data.get("host"),
        api_key=data.get("api_key")
    )

@app.post("/api/image/generate")
def generate_image_endpoint(data: dict):
    """Generate an image using the selected provider."""
    if not image_engine:
        return {"success": False, "error": "Image engine not available"}
    
    input_images = data.get("input_images", [])
    if input_images:
        project_path = data.get("project_path", "")
        if not project_path:
            p_path = project_manager.get_current_project_path()
            if p_path:
                project_path = str(p_path)
        if project_path:
            resolved_images = []
            for rel in input_images:
                p = Path(rel)
                if not p.is_absolute():
                    p = Path(project_path) / rel
                if p.exists():
                    resolved_images.append(str(p.resolve()))
                else:
                    logger.warning("[generate_image_endpoint] Image NOT FOUND: %s", p)
                    resolved_images.append(rel)
            data["input_images"] = resolved_images

    return image_engine.generate_image(
        provider_id=data.get("provider", "comfyui"),
        model=data.get("model", ""),
        prompt=data.get("prompt", ""),
        host=data.get("host"),
        api_key=data.get("api_key"),
        width=data.get("width", 1024),
        height=data.get("height", 1024),
        workflow_name=data.get("workflow_name"),
        input_images=data.get("input_images"),
        aspect_ratio=data.get("aspect_ratio"),
        resolution=data.get("resolution"),
        seed=data.get("seed"),
        steps=data.get("steps"),
        cfg=data.get("cfg")
    )

@app.post("/api/image/upscale-temp")
def upscale_temp_image(data: dict = Body(...)):
    """4K-upscale a temp ComfyUI output image (e.g. an unapproved T2I preview).

    Downloads the temp image from ComfyUI's /view, writes it to a temp file,
    and runs the configured upscale workflow through the image engine (which
    uploads it into LoadImage automatically). Returns the new temp filename so
    the caller can preview/approve it like any other generation.

    Sync on purpose: blocking network I/O against ComfyUI.
    """
    if not image_engine:
        return {"success": False, "error": "Image engine not available"}
    filename = data.get("filename", "")
    subfolder = data.get("subfolder", "")
    if not filename:
        return {"success": False, "error": "No filename provided"}
    settings = load_settings()
    wf = (settings.get("workflows") or {}).get("upscale")
    if not wf:
        return {"success": False, "error": "No upscale workflow selected in Settings > Workflows"}
    img_settings = settings.get("image_gen") or {}
    import requests as _rq
    import tempfile
    url = f"{comfyui_client.host}/view?filename={filename}"
    if subfolder:
        url += f"&subfolder={subfolder}"
    try:
        resp = _rq.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"Failed to fetch temp image from ComfyUI: {e}"}
    tmp = tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".png", delete=False)
    try:
        tmp.write(resp.content)
        tmp.close()
        return image_engine.generate_image(
            provider_id=img_settings.get("provider", "comfyui"),
            model=img_settings.get("model", ""),
            prompt="upscale",
            host=img_settings.get("host", ""),
            api_key=img_settings.get("apiKey", ""),
            width=1024, height=1024,
            workflow_name=wf,
            input_images=[tmp.name],
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

@app.post("/api/image/shot-fix")
def shot_fix_image(data: dict = Body(...)):
    """Fix a shot image with Qwen-Image-Edit-2511.

    Takes a temp ComfyUI output (the unapproved storyboard preview), an
    instruction like "fix the warped hand", downloads the image, and runs
    the shot-fix workflow. The model re-renders the scene honoring the
    instruction while keeping composition (VAEEncode keeps dimensions).
    Returns the new temp filename so the preview/approve flow continues
    unchanged. Sync on purpose: blocking network I/O against ComfyUI.
    """
    if not image_engine:
        return {"success": False, "error": "Image engine not available"}
    filename = data.get("filename", "")
    subfolder = data.get("subfolder", "")
    instruction = (data.get("instruction") or "").strip()
    if not filename:
        return {"success": False, "error": "No filename provided"}
    if not instruction:
        return {"success": False, "error": "No fix instruction provided"}
    settings = load_settings()
    wf = (settings.get("workflows") or {}).get("shotfix", "image_qwen_edit_shotfix.json")
    import requests as _rq
    import tempfile
    url = f"{comfyui_client.host}/view?filename={filename}"
    if subfolder:
        url += f"&subfolder={subfolder}"
    try:
        resp = _rq.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        return {"success": False, "error": f"Failed to fetch temp image from ComfyUI: {e}"}
    tmp = tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".png", delete=False)
    try:
        tmp.write(resp.content)
        tmp.close()
        return image_engine.generate_image(
            provider_id="comfyui",
            model="",
            prompt=instruction,
            host=settings.get("comfyui", {}).get("host", ""),
            api_key="",
            width=1024, height=1024,
            workflow_name=wf,
            input_images=[tmp.name],
        )
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

@app.get("/api/image/progress")
async def get_image_progress():
    """Get current image generation progress."""
    if not image_engine:
        return {"pct": 0, "status": "idle", "label": ""}
    return image_engine.get_gen_progress()

@app.post("/api/image/interrupt")
def interrupt_image_generation():
    """Interrupt the current ComfyUI generation/queue.

    Sync on purpose: it does blocking network I/O against ComfyUI.
    """
    logger.info("POST /api/image/interrupt")
    try:
        success = comfyui_client.interrupt()
        comfyui_client.clear_queue()
        if image_engine:
            image_engine._update_gen_progress(0, "error", "Cancelled by user")
        return {"success": success}
    except Exception as e:
        logger.error("Failed to interrupt ComfyUI: %s", e)
        return {"success": False, "error": str(e)}

@app.post("/api/video/generate")
def generate_video_endpoint(data: dict):
    """Generate a video using the selected provider."""
    if not image_engine:
        return {"success": False, "error": "Image engine not available"}
    
    input_image = data.get("input_image")
    if input_image:
        project_path = data.get("project_path", "")
        if not project_path:
            p_path = project_manager.get_current_project_path()
            if p_path:
                project_path = str(p_path)
        if project_path:
            p = Path(input_image)
            if not p.is_absolute():
                p = Path(project_path) / input_image
            if p.exists():
                data["input_image"] = str(p.resolve())
            else:
                logger.warning("[generate_video_endpoint] Image NOT FOUND: %s", p)

    provider = data.get("provider", "comfyui")
    workflow_name = data.get("workflow_name")
    if provider.startswith("comfyui") and not workflow_name:
        # Default to the configured i2v workflow (LTX-2.5 with native audio)
        # so the endpoint works without the caller naming a workflow — same
        # convention as the t2v endpoint.
        settings = load_settings()
        workflow_name = settings.get("workflows", {}).get("i2v", "") or DEFAULT_I2V_WORKFLOW
    return image_engine.generate_video(
        provider_id=provider,
        model=data.get("model", ""),
        prompt=data.get("prompt", ""),
        host=data.get("host"),
        api_key=data.get("api_key"),
        input_image=data.get("input_image"),
        workflow_name=workflow_name
    )

# === ComfyUI Subprocess Management ===

@app.get("/api/comfyui/status")
def get_comfyui_status():
    """Check if ComfyUI is running.

    Sync on purpose: polled every few seconds and does blocking network I/O;
    running it as async would freeze the event loop.
    """
    return comfyui_client.get_comfyui_status()

@app.get("/api/comfyui/categories")
async def get_comfyui_categories():
    """Get ComfyUI workflow categories."""
    return {"success": True, "categories": comfyui_client.get_categories()}

@app.post("/api/comfyui/test")
async def test_comfyui():
    """Test ComfyUI connection."""
    return comfyui_client.test_connection()

@app.post("/api/comfyui/interrupt")
def interrupt_comfyui():
    """Interrupt the current ComfyUI generation and clear the queue.

    Sync on purpose: it does blocking network I/O against ComfyUI.
    """
    comfyui_client.clear_queue()
    success = comfyui_client.interrupt()
    return {"success": success}


@app.get("/api/comfyui/queue")
def get_comfyui_queue():
    """Live view of ComfyUI's queue for the dashboard.

    Merges ComfyUI's /queue with the app's job-label registry so each job
    shows what it is ("Image · image_z_image_turbo"), not a raw prompt_id.
    Sync on purpose: blocking network I/O against ComfyUI.
    """
    try:
        q = comfyui_client.get_queue()
        labels = comfyui_client.list_job_labels()

        def _entry(item):
            # Each queue entry is [number, prompt_id, prompt, extra_data, outputs]
            pid = str(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else "?"
            return {
                "prompt_id": pid,
                "label": labels.get(pid, "ComfyUI job"),
            }

        running = [_entry(it) for it in (q.get("queue_running") or [])]
        pending = [_entry(it) for it in (q.get("queue_pending") or [])]
        return {"success": True, "running": running, "pending": pending, "connected": True}
    except Exception as e:
        return {"success": False, "error": str(e), "running": [], "pending": [], "connected": False}


@app.post("/api/comfyui/queue/cancel")
def cancel_comfyui_job(req: QueueCancelRequest):
    """Cancel a single ComfyUI job from the queue dashboard.

    Pending jobs are dequeued by prompt_id; the running job gets /interrupt.
    Sync on purpose: blocking network I/O against ComfyUI.
    """
    try:
        q = comfyui_client.get_queue()
        running_ids = {str(it[1]) for it in (q.get("queue_running") or [])
                       if isinstance(it, (list, tuple)) and len(it) > 1}
        pending_ids = {str(it[1]) for it in (q.get("queue_pending") or [])
                       if isinstance(it, (list, tuple)) and len(it) > 1}
        pid = str(req.prompt_id)
        if pid in pending_ids:
            ok = comfyui_client.dequeue_prompt(pid)
            return {"success": ok, "action": "dequeued" if ok else "failed"}
        if pid in running_ids:
            ok = comfyui_client.interrupt()
            return {"success": ok, "action": "interrupted" if ok else "failed"}
        return {"success": False, "action": "not_found",
                "error": "Job already finished or removed"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/comfyui/model-sync")
async def model_sync_scan():
    """Scan all workflows for missing model references.

    Reuses the smoke gate's validator, so the wizard and the CI-style check
    always agree. Returns per-workflow issues plus same-family suggestions
    (e.g. z_image_turbo_bf16 -> Z-Image-Turbo-w4a8).
    """
    try:
        import smoke_check
        errs, sugg = smoke_check.check_models()
        return {"success": True, "issues": errs, "suggestions": sugg,
                "models_root": str(smoke_check.find_models_root())}
    except Exception as e:
        return {"success": False, "error": str(e), "issues": [], "suggestions": []}


class ModelSyncFixRequest(BaseModel):
    file: str            # path relative to repo root, e.g. app/workflows/x.json
    node_class: str
    input: str
    missing: str
    suggested: str


@app.post("/api/comfyui/model-sync")
async def model_sync_apply(fixes: List[ModelSyncFixRequest]):
    """Apply model remaps to workflow files.

    For each fix, every node in the workflow whose class_type and current
    value match gets its input rewritten to the suggested installed model.
    Writes are atomic (tmp + os.replace).
    """
    results = []
    for fix in fixes:
        # Path traversal guard: only allow fixes inside app/workflows/
        p = (Path(__file__).parent / fix.file.replace("\\", "/")).resolve()
        if not str(p).startswith(str((Path(__file__).parent / "workflows").resolve())) or p.suffix != ".json":
            results.append({"file": fix.file, "success": False, "error": "path outside app/workflows"})
            continue
        try:
            wf = json.loads(p.read_text(encoding="utf-8"))
            changed = 0
            for nd in wf.values():
                if (isinstance(nd, dict) and nd.get("class_type") == fix.node_class
                        and isinstance(nd.get("inputs"), dict)
                        and nd["inputs"].get(fix.input) == fix.missing):
                    nd["inputs"][fix.input] = fix.suggested
                    changed += 1
            if changed == 0:
                results.append({"file": fix.file, "success": False, "error": "no matching nodes found (already fixed?)"})
                continue
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
            results.append({"file": fix.file, "success": True, "changed": changed})
        except Exception as e:
            results.append({"file": fix.file, "success": False, "error": str(e)})
    return {"success": all(r.get("success") for r in results), "results": results}


@app.get("/api/comfyui/checkpoints")
async def get_comfyui_checkpoints():
    """Get available checkpoints from ComfyUI."""
    checkpoints = comfyui_client.get_available_checkpoints()
    return {"success": True, "checkpoints": checkpoints}

@app.post("/api/comfyui/generate/image")
def generate_image(
    prompt: str,
    model: str,
    width: int = 1024,
    height: int = 1024,
    seed: int = -1
):
    """Generate an image."""
    logger.info("POST /api/comfyui/generate/image — model=%s, %dx%d, seed=%d", model, width, height, seed)
    # Try to test connection first, but don't block if it fails
    conn_test = comfyui_client.test_connection()
    if not conn_test.get("success"):
        return {
            "success": False, 
            "error": f"ComfyUI not connected: {conn_test.get('error', 'Unknown error')}. Make sure ComfyUI is running on http://localhost:8188. You can still generate prompts without ComfyUI.",
            "connected": False
        }
    
    result = comfyui_client.generate_image(prompt, model, width, height, seed)
    return result

# Default ComfyUI workflow presets used when Settings has no workflow assigned yet.
DEFAULT_T2I_WORKFLOW = "image_qwen_image_2_1_t2i.json"
# LTX-2.5-Distilled i2v with native audio generation — the installed model
# outclasses the old MiniMax/LTX2.3 paths (which needed missing GGUFs).
DEFAULT_I2V_WORKFLOW = "video_ltx2_5_i2v.json"
DEFAULT_T2V_WORKFLOW = "video_minimax_h3_t2v_ltxupsampler.json"


def _refine_prompt_via_agent(agent_id: str, prompt: str, refine: bool) -> str:
    """Optionally refine a prompt through an LLM agent before ComfyUI dispatch.
    Falls back to the raw prompt if refinement is disabled, fails, or the LLM is off."""
    if not refine or not llm_agents:
        return prompt
    try:
        result = llm_agents.run(agent_id, prompt)
    except Exception as e:
        logger.warning("LLM agent %s raised: %s — using raw prompt", agent_id, e)
        return prompt
    if result.get("success") and result.get("response"):
        refined = result["response"].strip()
        logger.info("LLM agent %s refined prompt: %d -> %d chars", agent_id, len(prompt), len(refined))
        return refined
    logger.warning("LLM agent %s failed: %s — using raw prompt", agent_id, result.get("error"))
    return prompt


@app.post("/api/llm/polish-video-prompt")
async def polish_video_prompt(data: dict):
    """Rewrite a shot's video_prompt with the LTX i2v motion-polisher agent.

    Optional LLM step before rendering: never fails the flow — on any problem it
    returns success=False and the caller keeps the original prompt.
    """
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    if not llm_agents:
        return {"success": False, "error": "LLM agents not available"}

    context = data.get("context") or {}
    ctx_lines = []
    if context.get("shot_type"):
        ctx_lines.append(f"Shot type: {context['shot_type']}")
    if context.get("emotion"):
        ctx_lines.append(f"Emotion: {context['emotion']}")
    if context.get("action"):
        ctx_lines.append(f"Action: {context['action']}")
    if context.get("scene_synopsis"):
        ctx_lines.append(f"Scene: {context['scene_synopsis']}")
    user_text = prompt if not ctx_lines else "\n".join(ctx_lines) + "\n\nMotion prompt to polish:\n" + prompt

    import asyncio
    loop = asyncio.get_event_loop()

    def _run():
        return llm_agents.run(
            "prompt_engineer_ltx_i2v",
            user_text,
            provider=data.get("provider"),
            model=data.get("model"),
            api_key=data.get("api_key"),
            host=data.get("host"),
        )

    try:
        result = await loop.run_in_executor(None, _run)
    except Exception as e:
        return {"success": False, "error": str(e)}
    if not result.get("success") or not (result.get("response") or "").strip():
        return {"success": False, "error": result.get("error") or "LLM returned empty response"}
    polished = result["response"].strip()
    # strip common wrapper artifacts the LLM might add
    polished = polished.strip('`"')
    if polished.lower().startswith("polished prompt:"):
        polished = polished[len("polished prompt:"):].strip()
    return {"success": True, "prompt": polished, "original": prompt}


@app.post("/api/comfyui/generate/t2i")
async def generate_t2i_endpoint(request: Request):
    """Generate image using T2I workflow from settings (defaults to the Krea 2 Turbo preset)."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    settings = load_settings()
    workflow_name = data.get("workflow_name") or settings.get("workflows", {}).get("t2i", "") or DEFAULT_T2I_WORKFLOW
    resolution = data.get("resolution")
    steps = data.get("steps")
    logger.info("POST /api/comfyui/generate/t2i — workflow=%s, seed=%s, steps=%s", workflow_name, seed, steps)

    # Run blocking refinement + generation in a worker thread so the event loop
    # stays free (progress polling and other requests keep working).
    import asyncio
    loop = asyncio.get_event_loop()

    def _run_generation():
        final_prompt = _refine_prompt_via_agent("prompt_engineer_t2i", prompt, data.get("refine", False))
        names, strengths = _comfy_lora_stack("image")
        return comfyui_client.generate_with_workflow(
            final_prompt, workflow_name, seed=seed, steps=steps, resolution=resolution,
            loras=names, lora_strengths=strengths)

    result = await loop.run_in_executor(None, _run_generation)
    if isinstance(result, dict) and result.get("success"):
        result.setdefault("workflow", workflow_name)
    return result


@app.post("/api/comfyui/generate/t2v")
async def generate_t2v_endpoint(request: Request):
    """Generate a video from text using the T2V workflow (defaults to the MiniMax H3 T2V preset)."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    steps = data.get("steps")
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    settings = load_settings()
    workflow_name = data.get("workflow_name") or settings.get("workflows", {}).get("t2v", "") or DEFAULT_T2V_WORKFLOW
    logger.info("POST /api/comfyui/generate/t2v — workflow=%s, seed=%s, steps=%s", workflow_name, seed, steps)

    # Run blocking refinement + generation in a worker thread so the event loop
    # stays free (progress polling and other requests keep working).
    import asyncio
    loop = asyncio.get_event_loop()

    def _run_generation():
        final_prompt = _refine_prompt_via_agent("prompt_engineer_t2v", prompt, data.get("refine", False))
        return comfyui_client.generate_with_workflow(final_prompt, workflow_name, seed=seed, steps=steps)

    return await loop.run_in_executor(None, _run_generation)

import base64

def _is_krea2_char_bg_workflow(workflow_name: str) -> bool:
    """True if the workflow is a Krea 2 Edit 'character + background' workflow.

    Detected structurally: it patches the model with source latents
    (Krea2EditModelPatch) and has at least two image-input nodes, so the
    storyboard can feed it a dedicated character reference and background.
    """
    try:
        base = Path(__file__).parent / "workflows"
        path = base / workflow_name
        if not path.exists():
            path = base / (workflow_name + ".json")
        if not path.exists():
            return False
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    has_patch = False
    img_count = 0
    for n in workflow.values():
        if not isinstance(n, dict):
            continue
        ct = n.get("class_type", "")
        if ct == "Krea2EditModelPatch":
            has_patch = True
        elif ct in ("PixaromaLoadImageMini", "LoadImage"):
            img_count += 1
    return has_patch and img_count >= 2


def _order_char_bg_inputs(workflow_name: str, abs_paths: list, character_image: str = None,
                          background_image: str = None, project_path: str = None) -> list:
    """Reorder resolved input images for a Krea 2 Edit 'character + background'
    workflow so slot 1 is the character reference and slot 2 the background.

    The storyboard sends a flat list (character portraits, location portraits,
    sheets), so pick by project folder conventions. Explicit character_image /
    background_image hints (e.g. the previous shot's approved frame used as the
    character reference) win over folder-based selection. Returns the list
    unchanged for any other workflow or when fewer than two images resolve.
    """
    if not _is_krea2_char_bg_workflow(workflow_name) or len(abs_paths) <= 1:
        return abs_paths

    def _resolve(p):
        if not p:
            return None
        cp = Path(p)
        if not cp.is_absolute():
            cp = Path(project_path or "") / cp
        if cp.exists():
            return str(cp)
        return None

    def _norm(p):
        return p.replace("\\", "/").lower()

    ordered = []
    for hint in (character_image, background_image):
        r = _resolve(hint)
        if r and r not in ordered:
            ordered.append(r)

    if len(ordered) < 2:
        available = [p for p in abs_paths if p not in ordered]
        char_imgs = [p for p in available if "character" in _norm(p)]
        # Background candidates: cropped 360 extracts live in <project>/backgrounds/
        # and should win over the location portrait/sheet, which is why they are
        # matched too (abs_paths order keeps the extracted bg first).
        bg_imgs = [p for p in available if ("location" in _norm(p) or "background" in _norm(p))]
        if len(ordered) < 1 and char_imgs:
            ordered.append(char_imgs[0])
        if len(ordered) < 2 and bg_imgs:
            ordered.append(bg_imgs[0])

    for p in abs_paths:
        if p not in ordered and len(ordered) < 2:
            ordered.append(p)

    if ordered:
        logger.info("[i2i] char+bg workflow inputs: %s", ordered)
        return ordered
    return abs_paths


@app.post("/api/orchestrator/generate-consistent-shot")
async def generate_consistent_shot(request: Request):
    """Generate image using I2I workflow, checking consistency with the anchor shot."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    steps = data.get("steps")
    cfg = data.get("cfg")
    aspect_ratio = data.get("aspect_ratio")
    resolution = data.get("resolution")
    input_images = data.get("input_images", [])
    
    project_path = data.get("project_path", "")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)

    settings = load_settings()
    workflow_name = data.get("workflow_name") or settings.get("workflows", {}).get("i2i", "")
    if not workflow_name:
        return {"success": False, "error": "No I2I workflow assigned in Settings"}
    _comfyui_client_module._ACTIVE_PROJECT_PATH = str(project_path) if project_path else None  # Style DNA source for short-prompt padding

    abs_paths = []
    for rel in input_images:
        p = Path(project_path) / rel
        if p.exists():
            abs_paths.append(str(p))

    # Krea 2 Edit 'character + background' workflows take exactly two images:
    # slot 1 = the character reference (optionally the previous shot's approved
    # frame for continuity), slot 2 = the background.
    abs_paths = _order_char_bg_inputs(
        workflow_name, abs_paths,
        character_image=data.get("character_image"),
        background_image=data.get("background_image"),
        project_path=project_path
    )

    # The anchor is the first input image if it exists.
    anchor_path = abs_paths[0] if abs_paths else None

    # Approved portraits (character/location images, not busy sheets) are the
    # identity references the VLM compares the generated shot against.
    def _is_portrait_ref(p):
        parts = p.replace("\\", "/").split("/")
        return "approved" in parts and "sheets" not in parts
    portrait_paths = [p for p in abs_paths if _is_portrait_ref(p)]

    eval_info = None  # populated after a successful VLM check; attached to the result

    # Helper to download image from ComfyUI
    def _download_from_comfy(filename):
        import requests, time
        url = f"{comfyui_client.host}/view?filename={filename}&type=output"
        for _ in range(5):
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    return resp.content
            except Exception:
                pass
            time.sleep(1)
        return None

    import asyncio
    loop = asyncio.get_event_loop()
    
    max_retries = 2
    current_prompt = prompt
    best_result = None
    # ComfyUI LoRA stack override for the storyboard image engine (task: image/i2i)
    _img_loras, _img_lora_strengths = _comfy_lora_stack("image")
    
    for attempt in range(max_retries + 1):
        if attempt > 0:
            logger.info(f"Attempting consistency retry {attempt}/{max_retries}...")
            
        _stop_poll = False
        def _poll():
            while not _stop_poll:
                try:
                    if _stop_poll:
                        break
                    prog = comfyui_client.get_progress()
                    if prog.get("running") and prog.get("max", 0) > 0:
                        pct = round(prog["current"] / prog["max"] * 100)
                        if image_engine:
                            image_engine._update_gen_progress(pct, "running", f"Attempt {attempt+1}: Gen Step {prog.get('current')}/{prog.get('max')}")
                except Exception:
                    pass
                time.sleep(1)

        poll_thread = threading.Thread(target=_poll, daemon=True)
        if image_engine:
            image_engine._update_gen_progress(0, "running", f"Attempt {attempt+1}: Generating...")
        poll_thread.start()

        try:
            result = await loop.run_in_executor(
                None,
                lambda: comfyui_client.generate_with_workflow(
                    current_prompt, workflow_name, seed=seed, steps=steps, cfg=cfg,
                    input_images=abs_paths if abs_paths else None,
                    aspect_ratio=aspect_ratio, resolution=resolution,
                    loras=_img_loras, lora_strengths=_img_lora_strengths
                )
            )
            best_result = result
        finally:
            _stop_poll = True
            if image_engine:
                image_engine._update_gen_progress(0, "idle", "")

        if not result.get("success") or not anchor_path:
            break
            
        # 2. Evaluate
        if image_engine:
            image_engine._update_gen_progress(100, "running", f"Attempt {attempt+1}: VLM evaluating consistency...")
            
        filename = result.get("filename")
        new_img_bytes = await loop.run_in_executor(None, _download_from_comfy, filename)
        if not new_img_bytes:
            logger.warning("Failed to download generated image for VLM eval.")
            break

        # Identity references: approved portraits if present, else the first input image
        ref_paths = portrait_paths or ([anchor_path] if anchor_path else [])
        anchor_b64s = []
        for rp in ref_paths:
            try:
                with open(rp, "rb") as f:
                    anchor_b64s.append(base64.b64encode(f.read()).decode('utf-8'))
            except Exception as e:
                logger.warning("Failed to read reference image %s: %s", rp, e)
        if not anchor_b64s:
            break
        new_img_b64 = base64.b64encode(new_img_bytes).decode('utf-8')

        # Get rubric criteria from project state
        _rubric_criteria = []
        _scene_context = data.get("scene_context", {})
        try:
            _pg = orchestrator.memory.project_graph if orchestrator else {}
            _rubric = _pg.get("qc_rubric", {})
            _rubric_criteria = _rubric.get("criteria", []) if isinstance(_rubric, dict) else []
        except Exception:
            pass
        eval_res = await loop.run_in_executor(None, lambda: orchestrator.evaluate_vision_consistency(anchor_b64s, new_img_b64, shot_prompt=current_prompt, rubric_criteria=_rubric_criteria, scene_context=_scene_context))
        
        if eval_res.get("success"):
            eval_info = {
                "score": eval_res.get("score", 10 if eval_res.get("consistent") else 4),
                "passed": eval_res.get("passed", False),
                "feedback": eval_res.get("feedback", ""),
                "consistent": eval_res.get("consistent", False),
                "sub_scores": eval_res.get("sub_scores", {}),
                "attempts": attempt + 1,
                "retried": attempt > 0,
            }
            if eval_res.get("consistent"):
                logger.info(f"VLM: Shot is consistent on attempt {attempt+1}.")
                break
            else:
                inc = eval_res.get("inconsistencies", [])
                logger.info(f"VLM: Shot inconsistent. {inc}")
                corr = eval_res.get("correction_prompt", "")
                if attempt < max_retries:
                    current_prompt = f"{prompt}. CRITICAL FIX: {corr}"
                    import random
                    seed = random.randint(1, 99999999)
                else:
                    logger.warning("Max consistency retries reached.")
        else:
            logger.warning(f"VLM evaluation failed: {eval_res.get('error')}")
            break

    if image_engine:
        image_engine._update_gen_progress(0, "idle", "")
    if best_result and eval_info:
        best_result["consistency_eval"] = eval_info
    return best_result

@app.post("/api/comfyui/generate/i2i")
async def generate_i2i_endpoint(request: Request):
    """Generate image using I2I workflow from settings with reference images."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    steps = data.get("steps")
    project_path = data.get("project_path", "")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    input_images = data.get("input_images", [])
    aspect_ratio = data.get("aspect_ratio")
    resolution = data.get("resolution")
    cfg = data.get("cfg")
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    settings = load_settings()
    workflow_name = data.get("workflow_name") or settings.get("workflows", {}).get("i2i", "")
    if not workflow_name:
        return {"success": False, "error": "No I2I workflow assigned in Settings"}
    abs_paths = []
    for rel in input_images:
        p = Path(project_path) / rel
        if p.exists():
            abs_paths.append(str(p))
        else:
            logger.warning("[I2I] Image NOT FOUND: %s", p)
    abs_paths = _order_char_bg_inputs(
        workflow_name, abs_paths,
        character_image=data.get("character_image"),
        background_image=data.get("background_image"),
        project_path=project_path
    )
    logger.info("POST /api/comfyui/generate/i2i — workflow=%s, %d/%d images resolved, seed=%s, steps=%s",
                 workflow_name, len(abs_paths), len(input_images), seed, steps)

    # Start background progress poller so /api/image/progress shows live step info
    _stop_i2i_poll = False
    total_steps = steps or 20
    def _i2i_poll_progress():
        node_map = {
            "KSampler": "Sampling", "VAEDecode": "VAE Decode",
            "VAEEncode": "VAE Encode", "CLIPTextEncode": "Encoding prompt",
            "EmptyLatentImage": "Preparing", "LoadImage": "Loading image",
            "SaveImage": "Saving", "CheckpointLoaderSimple": "Loading model",
            "CLIPSetLastLayer": "Setting CLIP layer", "VAELoader": "Loading VAE",
            "ControlNetLoader": "Loading ControlNet", "LoraLoader": "Loading LoRA",
        }
        gen_start = time.time()
        saw_real = False   # have we seen real ComfyUI progress this run?
        last_pct = 0
        while not _stop_i2i_poll:
            try:
                prog = comfyui_client.get_progress()
                if prog.get("running") and prog.get("max", 0) > 0:
                    pct = round(prog["current"] / prog["max"] * 100)
                    step = prog.get("current", 0)
                    total = prog.get("max", 0)
                    step_label = f"Step {step}/{total}"
                    node_type = prog.get("node_type", "") or prog.get("node", "") or ""
                    readable = node_map.get(node_type, node_type.replace("_", " ").title() if node_type else "")
                    node_label = f" — {readable}" if readable else ""
                    if image_engine:
                        image_engine._update_gen_progress(pct, "running", step_label)
                        with image_engine._gen_lock:
                            image_engine._gen_progress["step_label"] = step_label
                            image_engine._gen_progress["node_label"] = node_label
                    saw_real = True
                    last_pct = pct
                    time.sleep(1)
                    continue
                if saw_real:
                    # Real steps were streaming and the node finished — the job
                    # is in its tail (VAE decode / save). Hold progress honestly.
                    if image_engine:
                        hold_pct = max(last_pct, 90)
                        image_engine._update_gen_progress(hold_pct, "running", "Finishing — VAE decode / saving")
                        with image_engine._gen_lock:
                            image_engine._gen_progress["step_label"] = ""
                            image_engine._gen_progress["node_label"] = ""
                    time.sleep(1)
                    continue
                # Fallback: estimate from elapsed time when /progress is unavailable
                elapsed = time.time() - gen_start
                est_per_step = 2.0  # seconds per step (rough estimate for flux workflows)
                est_step = min(int(elapsed / est_per_step) + 1, total_steps)
                pct = round((est_step / total_steps) * 100)
                if image_engine:
                    step_label = f"Step ~{est_step}/{total_steps}"
                    image_engine._update_gen_progress(pct, "running", step_label)
                    with image_engine._gen_lock:
                        image_engine._gen_progress["step_label"] = step_label
                        image_engine._gen_progress["node_label"] = ""
            except Exception:
                pass
            time.sleep(1)

    poll_thread = threading.Thread(target=_i2i_poll_progress, daemon=True)
    if image_engine:
        image_engine._update_gen_progress(0, "running", "Starting generation...")
    poll_thread.start()

    # Run blocking generation in thread pool so event loop stays free
    # (allows /api/image/progress polling to work during generation)
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: comfyui_client.generate_with_workflow(
                prompt, workflow_name, seed=seed, steps=steps, cfg=cfg,
                input_images=abs_paths if abs_paths else None,
                aspect_ratio=aspect_ratio, resolution=resolution
            )
        )
        return result
    finally:
        _stop_i2i_poll = True
        if image_engine:
            image_engine._update_gen_progress(0, "idle", "")

@app.post("/api/comfyui/generate/i2v")
async def generate_i2v_endpoint(request: Request):
    """Generate video using I2V workflow from settings with scene image input."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    steps = data.get("steps")
    project_path = data.get("project_path", "")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    input_image = data.get("input_image")
    scene_index = data.get("scene_index")
    logger.info("POST /api/comfyui/generate/i2v — seed=%s, steps=%s, scene_index=%s, input=%s",
                 seed, steps, scene_index, input_image)
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    settings = load_settings()
    workflow_name = settings.get("workflows", {}).get("i2v", "") or DEFAULT_I2V_WORKFLOW
    abs_image = None
    if input_image:
        p = Path(input_image)
        if p.exists():
            abs_image = str(p)
            logger.info("[I2V] Resolved input image: %s", abs_image)
        else:
            stem = p.stem
            parent = p.parent
            logger.warning("[I2V] Input image NOT at %s, trying alt extensions...", p)
            for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                alt = parent / f"{stem}{ext}"
                if alt.exists():
                    abs_image = str(alt)
                    logger.info("[I2V] Found image with alt extension: %s", abs_image)
                    break
            if not abs_image:
                logger.warning("[I2V] No alt extension found for %s", p)
    # Auto-discover scene image from project if not resolved yet
    if not abs_image and project_path:
        scene_index = data.get("scene_index", 0)
        scenes_dir = Path(project_path) / "scenes"
        if scenes_dir.exists():
            candidates = sorted([
                f for f in scenes_dir.iterdir()
                if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp')
            ])
            # Try scene_{index+1} first, then fall back to any scene image
            target = f"scene_{scene_index + 1}"
            for c in candidates:
                if c.stem == target:
                    abs_image = str(c)
                    logger.info("[I2V] Auto-discovered scene image: %s", abs_image)
                    break
            if not abs_image:
                # Try any scene image
                scene_files = [c for c in candidates if c.stem.startswith("scene_")]
                if scene_files:
                    abs_image = str(scene_files[0])
                    logger.info("[I2V] Auto-discovered first scene image: %s", abs_image)
    if not abs_image:
        return {"success": False, "error": "Scene image not found. Make sure you approved the storyboard image first."}

    # Run blocking refinement + generation in a worker thread so the event loop
    # stays free (progress polling and other requests keep working).
    import asyncio
    loop = asyncio.get_event_loop()

    def _run_generation():
        final_prompt = _refine_prompt_via_agent("prompt_engineer_i2v", prompt, data.get("refine", False))
        return comfyui_client.generate_with_workflow(
            final_prompt, workflow_name, seed=seed, steps=steps,
            input_images=[abs_image] if abs_image else None
        )

    result = await loop.run_in_executor(None, _run_generation)
    if isinstance(result, dict) and result.get("success"):
        result.setdefault("workflow", workflow_name)
    return result

@app.post("/api/projects/save-image")
def save_project_image(data: dict = Body(...)):
    """Save a generated image to project folder.

    Sync on purpose: the ComfyUI download retry loop (with sleeps) must run in
    the threadpool, not on the event loop.
    """
    logger.info("POST /api/projects/save-image — stage=%s, card_name=%s", data.get("stage"), data.get("card_name"))
    stage = data.get("stage", "")
    filename = data.get("filename", "")
    project_path = data.get("project_path", "")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    card_name = data.get("card_name", "")
    project_name = data.get("project_name", "")
    previous_file = data.get("previous_file")
    subfolder = data.get("subfolder", "")
    image_type = data.get("type", "")
    import requests
    import time
    resp = None
    last_error = ""
    for attempt in range(5):
        try:
            url = f"{comfyui_client.host}/view?filename={filename}"
            if subfolder:
                url += f"&subfolder={subfolder}"
            if image_type:
                url += f"&type={image_type}"
            elif filename.startswith("ComfyUI_temp_") or filename.startswith("clipspace"):
                url += "&type=temp"
            elif subfolder:
                url += "&type=output"
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                logger.info("Successfully downloaded image from ComfyUI on attempt %d", attempt + 1)
                break
            else:
                last_error = f"HTTP {resp.status_code}"
                logger.warning("ComfyUI view returned HTTP %d on attempt %d, retrying in 1s...", resp.status_code, attempt + 1)
        except Exception as e:
            last_error = str(e)
            logger.warning("Error fetching view from ComfyUI on attempt %d: %s, retrying in 1s...", attempt + 1, e)
        time.sleep(1.0)

    if not resp or resp.status_code != 200:
        logger.error("Failed to download image from ComfyUI after retries. Last error: %s", last_error)
        return {"success": False, "error": f"Failed to download from ComfyUI: {last_error}"}
    ext = Path(filename).suffix or ".png"
    safe = card_name.replace(" ", "_").replace("/", "_") if card_name else filename
    save_name = f"{safe}{ext}"
    # Delete previous file if re-saving (use same path resolution as save)
    if previous_file:
        if project_path:
            prev_path = Path(project_path) / stage / previous_file
            if prev_path.exists():
                prev_path.unlink()
        elif project_name:
            project_manager.load_project(project_name)
            prev_path = project_manager.get_project_path(project_name) / stage / previous_file
            if prev_path.exists():
                prev_path.unlink()
    # Save to project_path first (ensures I2I can find it), fall back to project_manager
    if project_path:
        stage_dir = Path(project_path) / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        dest = stage_dir / save_name
        dest.write_bytes(resp.content)
        return {"success": True, "path": str(dest)}
    elif project_name:
        project_manager.load_project(project_name)
        result = project_manager.save_approved_output(stage, save_name, resp.content)
        return result
    return {"success": False, "error": "No project specified"}

@app.post("/api/assets/upload-image")
async def upload_asset_image(project_name: str = Form(""), project_path: str = Form(""), asset_type: str = Form(...), asset_id: str = Form(...), file: UploadFile = File(...)):
    """Upload a character/location image from drag-drop or file picker."""
    if not file.filename:
        return {"success": False, "error": "No file provided"}
    ext = Path(file.filename).suffix or ".png"
    folder = f"{asset_type}s"  # e.g. "characters" or "locations"
    save_name = f"{asset_id}{ext}"
    content = await file.read()
    if project_path:
        stage_dir = Path(project_path) / folder
        stage_dir.mkdir(parents=True, exist_ok=True)
        dest = stage_dir / save_name
        dest.write_bytes(content)
        # Update orchestrator project_graph
        if orchestrator:
            pg = orchestrator.memory.project_graph
            if asset_type == "character":
                key = "character_assets"
                if asset_id not in pg.get(key, {}):
                    pg.setdefault(key, {})[asset_id] = {}
                pg[key][asset_id]["approved_image"] = f"{folder}/{save_name}"
                pg[key][asset_id]["approved"] = True
            elif asset_type == "location":
                key = "location_assets"
                if asset_id not in pg.get(key, {}):
                    pg.setdefault(key, {})[asset_id] = {}
                pg[key][asset_id]["approved_image"] = f"{folder}/{save_name}"
                pg[key][asset_id]["approved"] = True
            elif asset_type == "prop":
                key = "prop_assets"
                if asset_id not in pg.get(key, {}):
                    pg.setdefault(key, {})[asset_id] = {}
                pg[key][asset_id]["approved_image"] = f"{folder}/{save_name}"
                pg[key][asset_id]["approved"] = True
            elif asset_type == "character_sheet":
                key = "character_sheets"
                sheets = pg.setdefault(key, {})
                entry = sheets.setdefault(asset_id, {"character_id": asset_id, "generation_history": []})
                entry["sheet_image"] = f"{folder}/{save_name}"
                entry["generation_history"] = entry.get("generation_history", []) + [f"{folder}/{save_name}"]
                entry["approved"] = True
            elif asset_type == "location_sheet":
                key = "location_sheets"
                sheets = pg.setdefault(key, {})
                entry = sheets.setdefault(asset_id, {"location_id": asset_id, "generation_history": []})
                entry["sheet_image"] = f"{folder}/{save_name}"
                entry["generation_history"] = entry.get("generation_history", []) + [f"{folder}/{save_name}"]
                entry["approved"] = True
        return {"success": True, "path": f"{folder}/{save_name}"}
    elif project_name:
        project_manager.load_project(project_name)
        stage_dir = project_manager.get_project_path(project_name) / folder
        stage_dir.mkdir(parents=True, exist_ok=True)
        dest = stage_dir / save_name
        dest.write_bytes(content)
        return {"success": True, "path": f"{folder}/{save_name}"}
    return {"success": False, "error": "No project specified"}

@app.post("/api/storyboard/upload")
async def upload_storyboard_file(
    project_name: str = Form(""),
    project_path: str = Form(""),
    is_video: bool = Form(...),
    scene_index: int = Form(...),
    shot_index: int = Form(...),
    file: UploadFile = File(...)
):
    """Upload a storyboard shot image or video from file picker."""
    if not file.filename:
        return {"success": False, "error": "No file provided"}
    ext = Path(file.filename).suffix
    
    stage = "videos" if is_video else "scenes"
    card_name = f"scene_{scene_index + 1}_shot_{shot_index + 1}"
    safe = card_name.replace(" ", "_").replace("/", "_")
    save_name = f"{safe}{ext}"
    
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
            
    content = await file.read()
    if project_path:
        stage_dir = Path(project_path) / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        dest = stage_dir / save_name
        dest.write_bytes(content)
        
        # Now update the screenplayData in the orchestrator memory!
        if orchestrator:
            pg = orchestrator.memory.project_graph
            scene_graph = pg.get("scene_graph", [])
            if 0 <= scene_index < len(scene_graph):
                s = scene_graph[scene_index]
                if s.get("shots") and 0 <= shot_index < len(s["shots"]):
                    sh = s["shots"][shot_index]
                    if is_video:
                        sh["video_clip"] = save_name
                        sh["video_status"] = "approved"
                        sh.pop("_temp_video_clip", None)
                    else:
                        sh["storyboard_image"] = save_name
                        sh["storyboard_status"] = "approved"
                        sh.pop("_temp_storyboard_image", None)
            _save_orchestrator_state()
            
        return {"success": True, "path": str(dest), "filename": save_name}
    return {"success": False, "error": "No project path found"}

@app.post("/api/assets/approve-single")
async def approve_single_asset(request: Request):
    """Approve a single character or location asset, copying the file to a clean path and updating project graph."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    data = await request.json()
    logger.info("POST /api/assets/approve-single — id=%s, type=%s, image_path=%s", data.get("id"), data.get("type"), data.get("image_path"))
    asset_id = data.get("id")
    asset_type = data.get("type") # "char" or "loc" or "prop"
    image_path = data.get("image_path") # e.g. "characters/CHAR_001_12345.png" or URL
    project_path = data.get("project_path")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    
    if not asset_id or not asset_type or not image_path or not project_path:
        return {"success": False, "error": "Missing required fields"}
        
    if asset_type == "char":
        folder = "characters"
    elif asset_type == "loc":
        folder = "locations"
    else:
        folder = "props"
    
    # Resolve the source file path
    if image_path.startswith("http"):
        from urllib.parse import urlparse
        parsed = urlparse(image_path)
        path_parts = parsed.path.split("/saved-image/")
        if len(path_parts) > 1:
            image_path = path_parts[1]
            
    # Remove any query parameters
    image_path = image_path.split("?")[0]
    
    src_rel_path = image_path
    if not src_rel_path.startswith(folder + "/"):
        if src_rel_path.startswith("characters/") or src_rel_path.startswith("locations/") or src_rel_path.startswith("props/"):
            pass
        else:
            src_rel_path = f"{folder}/{src_rel_path}"
            
    src_file = Path(project_path) / src_rel_path
    if not src_file.exists():
        logger.warning("[Approve] Source file not found at %s", src_file)
        return {"success": False, "error": f"Source image not found: {src_rel_path}"}
        
    # Destination file
    dest_rel_path = f"{folder}/approved/{asset_id}.png"
    dest_file = Path(project_path) / dest_rel_path
    
    try:
        import shutil
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        logger.info("[Approve] Copied %s to %s", src_file, dest_file)
    except Exception as e:
        logger.error("[Approve] Copy failed: %s", e)
        return {"success": False, "error": f"Failed to copy file: {str(e)}"}
        
    # Update orchestrator project graph
    pg = orchestrator.memory.project_graph
    if asset_type == "char":
        key = "character_assets"
    elif asset_type == "loc":
        key = "location_assets"
    else:
        key = "prop_assets"
    asset_entry = pg.setdefault(key, {}).setdefault(asset_id, {})
    asset_entry["approved_image"] = dest_rel_path
    asset_entry["approved"] = True
    
    # Save orchestrator state
    _save_orchestrator_state()
    
    return {"success": True, "path": dest_rel_path}

@app.post("/api/assets/register-generation")
async def register_asset_generation(request: Request):
    """Register a new generated image in the orchestrator's project graph history."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    data = await request.json()
    logger.info("POST /api/assets/register-generation — id=%s, type=%s, path=%s", data.get("id"), data.get("type"), data.get("image_path"))
    asset_id = data.get("id")
    asset_type = data.get("type") # "char" or "loc" or "prop"
    image_path = data.get("image_path")
    
    if not asset_id or not asset_type or not image_path:
        return {"success": False, "error": "Missing required fields"}
        
    pg = orchestrator.memory.project_graph
    if asset_type == "char":
        key = "character_assets"
    elif asset_type == "loc":
        key = "location_assets"
    else:
        key = "prop_assets"
    
    asset_entry = pg.setdefault(key, {}).setdefault(asset_id, {})
    
    history = asset_entry.setdefault("generation_history", [])
    if image_path not in history:
        history.append(image_path)
    
    asset_entry["approved_image"] = image_path
    
    _save_orchestrator_state()
    return {"success": True}

@app.post("/api/assets/update-graph-entry")
async def update_asset_graph_entry(request: Request):
    """Update an asset's entry in the project graph (history and approved image)."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    data = await request.json()
    asset_id = data.get("id")
    asset_type = data.get("type") # "char", "loc", "prop", "char_sheet", "loc_sheet"
    history = data.get("generation_history", [])
    approved_image = data.get("approved_image", "")
    approved = data.get("approved", False)
    
    if not asset_id or not asset_type:
        return {"success": False, "error": "Missing required fields"}
        
    pg = orchestrator.memory.project_graph
    
    if asset_type == "char":
        key = "character_assets"
    elif asset_type == "loc":
        key = "location_assets"
    elif asset_type == "prop":
        key = "prop_assets"
    elif asset_type == "char_sheet":
        key = "character_sheets"
    elif asset_type == "loc_sheet":
        key = "location_sheets"
    else:
        return {"success": False, "error": f"Invalid asset type: {asset_type}"}
        
    asset_entry = pg.setdefault(key, {}).setdefault(asset_id, {})
    asset_entry["generation_history"] = history
    if asset_type in ("char_sheet", "loc_sheet"):
        asset_entry["sheet_image"] = approved_image
    else:
        asset_entry["approved_image"] = approved_image
    asset_entry["approved"] = approved
    
    _save_orchestrator_state()
    return {"success": True}

@app.post("/api/projects/save-video")
async def save_project_video(request: Request):
    """Save a generated video to project folder by downloading from ComfyUI."""
    data = await request.json()
    logger.info("POST /api/projects/save-video — filename=%s, card_name=%s", data.get("filename"), data.get("card_name"))
    filename = data.get("filename", "")
    subfolder = data.get("subfolder", "")
    project_path = data.get("project_path", "")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    card_name = data.get("card_name", "")
    project_name = data.get("project_name", "")
    if not filename:
        return {"success": False, "error": "No filename provided"}
    import requests
    try:
        params = {"filename": filename, "type": "output"}
        if subfolder:
            params["subfolder"] = subfolder
        resp = requests.get(f"{comfyui_client.host}/view", params=params, timeout=120)
        if resp.status_code != 200:
            return {"success": False, "error": "Failed to download video from ComfyUI"}
    except Exception as e:
        return {"success": False, "error": f"Download error: {str(e)}"}
    ext = Path(filename).suffix or ".mp4"
    safe = card_name.replace(" ", "_").replace("/", "_") if card_name else Path(filename).stem
    save_name = f"{safe}{ext}"
    stage = "videos"
    if project_path:
        stage_dir = Path(project_path) / stage
        stage_dir.mkdir(exist_ok=True)
        dest = stage_dir / save_name
        dest.write_bytes(resp.content)
        return {"success": True, "path": str(dest)}
    elif project_name:
        project_manager.load_project(project_name)
        result = project_manager.save_approved_output(stage, save_name, resp.content)
        return result
    return {"success": False, "error": "No project specified"}

@app.get("/api/projects")
async def get_projects():
    """Get all projects."""
    return {"success": True, "projects": project_manager.get_projects()}

@app.post("/api/projects/register")
async def register_project(request: Request):
    """Register an external project path in the persistent registry."""
    data = await request.json()
    name = data.get("name", "")
    path = data.get("path", "")
    if not name or not path:
        return {"success": False, "error": "name and path are required"}
    project_manager.register_project(name, path)
    return {"success": True}

@app.post("/api/projects/scan")
async def scan_for_projects(request: Request):
    """Recursively scan a directory for projects and auto-register them."""
    data = await request.json()
    scan_path = data.get("path", "")
    max_depth = data.get("max_depth", 5)
    if not scan_path:
        return {"success": False, "error": "path is required"}
    
    found = []
    scan_root = Path(scan_path)
    if not scan_root.exists():
        return {"success": True, "found": 0, "projects": []}
    
    def _scan(directory, depth):
        if depth > max_depth:
            return
        try:
            for entry in directory.iterdir():
                if entry.is_dir():
                    pf = entry / "project.json"
                    if pf.exists():
                        try:
                            with open(pf, 'r', encoding='utf-8') as f:
                                pd = json.load(f)
                            name = pd.get("name", entry.name)
                            project_manager.register_project(name, str(entry))
                            found.append({"name": name, "path": str(entry)})
                        except Exception:
                            pass
                    else:
                        _scan(entry, depth + 1)
        except PermissionError:
            pass
    
    _scan(scan_root, 0)
    return {"success": True, "found": len(found), "projects": found}

@app.get("/api/projects/discover")
async def discover_projects():
    """Auto-discover projects from known locations (localStorage paths, common dirs, etc.)."""
    discovered = []
    # Scan common parent directories where user might have saved projects
    scan_dirs = set()
    
    # 1. Get all currently registered paths and scan their parent directories
    registry = project_manager._load_registry()
    for entry in registry:
        p = Path(entry.get("path", ""))
        if p.exists() and p.parent.exists():
            scan_dirs.add(str(p.parent))
    
    # 2. Also scan the app's own projects directory
    scan_dirs.add(str(project_manager.projects_dir))
    
    # 3. Scan each unique parent directory (1-level deep)
    for scan_dir in scan_dirs:
        sd = Path(scan_dir)
        if not sd.exists():
            continue
        try:
            for entry in sd.iterdir():
                if entry.is_dir():
                    pf = entry / "project.json"
                    if pf.exists():
                        try:
                            with open(pf, 'r', encoding='utf-8') as f:
                                pd = json.load(f)
                            name = pd.get("name", entry.name)
                            project_manager.register_project(name, str(entry))
                            discovered.append({"name": name, "path": str(entry)})
                        except Exception:
                            pass
        except PermissionError:
            pass
    
    return {"success": True, "discovered": len(discovered), "projects": project_manager.get_projects()}

@app.get("/api/projects/check")
async def check_project(path: str):
    """Check if a path contains a valid project."""
    try:
        project_path = Path(path)
        if not project_path.exists():
            return {"exists": False}
        project_file = project_path / "project.json"
        if project_file.exists():
            with open(project_file, 'r') as f:
                data = json.load(f)
            return {"exists": True, "name": data.get("name", "Unknown"), "info": f"Created: {data.get('created_at', 'Unknown')}", "path": str(project_path)}
        # Also check if path contains name/project.json
        for sub in project_path.iterdir():
            if sub.is_dir():
                pf = sub / "project.json"
                if pf.exists():
                    with open(pf, 'r') as f:
                        data = json.load(f)
                    return {"exists": True, "name": data.get("name", sub.name), "info": f"Path: {sub}", "path": str(sub)}
    except Exception:
        pass
    return {"exists": False}

@app.post("/api/projects")
async def create_project(request: ProjectCreateRequest):
    """Create a new project."""
    logger.info("POST /api/projects — name=%s, template=%s", request.name, request.template_name)
    location = request.location
    if location and location.strip():
        location = location.strip()
    else:
        location = None
    result = project_manager.create_project(
        name=request.name,
        template_name=request.template_name,
        description=request.description,
        location=location
    )
    return result

@app.get("/api/projects/{name}")
async def get_project(name: str):
    """Get a project."""
    project = project_manager.load_project(name)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True, "project": project}

@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    """Delete a project."""
    result = project_manager.delete_project(name)
    return {"success": result}

@app.post("/api/projects/{name}/prompts/{stage}")
async def save_prompts(name: str, stage: str, content: str):
    """Save prompts for a stage."""
    project_manager.load_project(name)
    result = project_manager.save_prompt(stage, content)
    return {"success": result}

@app.post("/api/projects/{name}/state")
async def save_project_state(name: str, request: Request):
    """Save full project state including orchestrator continuity memory."""
    body = await request.json()
    state = body.get("state", {})
    project_path = body.get("project_path")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)

    # Guard against accidental state clearing from browser page reloads
    existing = project_manager.load_project_state(name, project_path)
    if not state.get("topicIdeas") and not state.get("screenplayData"):
        if existing and (existing.get("topicIdeas") or existing.get("screenplayData")):
            logger.warning("save_project_state ignored: prevented browser page overwrite of populated project data.")
            return {"success": True, "protected": True}

    # Sync orchestrator memory into state for persistence
    if orchestrator:
        if state:
            orchestrator.memory.project_info["genres"] = state.get("selectedGenres", [])
            orchestrator.memory.project_info["visual_style"] = state.get("selectedVisualStyle", "")
            orchestrator.memory.project_info["film_aesthetic"] = state.get("selectedFilmAesthetic", "")
            
        # FOOLPROOF GUARD: If the backend restarted and the orchestrator is empty, but the disk has data,
        # we MUST restore the orchestrator memory from disk before overwriting it!
        if existing and existing.get("_orchestrator_memory"):
            current_mem = orchestrator.to_dict()
            current_pg = current_mem.get("project_graph", {})
            existing_pg = existing.get("_orchestrator_memory", {}).get("project_graph", {})
            
            # If orchestrator lacks basic data (like bibles or assets) but disk has them, the backend likely lost its state.
            if not current_pg.get("character_bible") and not current_pg.get("location_bible") and (existing_pg.get("character_bible") or existing_pg.get("location_bible")):
                logger.warning(f"Orchestrator memory empty. Reloading {name} from disk before saving.")
                orchestrator.from_dict(existing.get("_orchestrator_memory"))
                project_manager.current_project = name

        state["_orchestrator_memory"] = orchestrator.to_dict()

    result = project_manager.save_project_state(name, state, project_path)
    return result

@app.get("/api/projects/{name}/state")
async def load_project_state(name: str, path: str = None):
    """Load full project state from disk, restoring orchestrator continuity memory."""
    result = project_manager.load_project_state(name, path)
    if result is None:
        if orchestrator:
            orchestrator.reset()
        # A missing state file is normal for a new/never-saved project — benign, not an error.
        # Only a state file that exists but fails to load is a real problem worth warning about.
        if path:
            state_file = Path(path) / "project_state.json"
        else:
            state_file = Path(project_manager.projects_dir) / name / "project_state.json"
        if state_file.exists():
            return {"success": False, "state": None, "error": "Project state file exists but could not be loaded (corrupt or unreadable)."}
        return {"success": True, "state": None, "empty": True}

    # Restore orchestrator continuity memory from saved state
    if orchestrator:
        if result.get("_orchestrator_memory"):
            orchestrator.from_dict(result["_orchestrator_memory"])
        else:
            orchestrator.reset()

    return {"success": True, "state": result}

@app.get("/api/projects/{name}/files/{stage}")
async def get_stage_files(name: str, stage: str):
    """Get files for a stage."""
    project_manager.load_project(name)
    files = project_manager.get_stage_files(stage)
    return {"success": True, "files": files}

@app.delete("/api/projects/{name}/file")
async def delete_project_file(name: str, request: Request):
    """Delete a file from a project stage."""
    data = await request.json()
    project_path = data.get("project_path")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    stage = data.get("stage", "")
    filename = data.get("filename", "")
    if not filename:
        return {"success": False, "error": "No filename provided"}
    try:
        if project_path:
            target = Path(project_path) / stage / filename
        else:
            project_manager.load_project(name)
            target = project_manager.get_project_path(name) / stage / filename
        if target.exists():
            target.unlink()
            return {"success": True}
        return {"success": False, "error": "File not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

class DeleteHistoryRequest(BaseModel):
    project_path: Optional[str] = None
    stage: str
    filenames: List[str]

@app.post("/api/projects/delete-history-files")
async def delete_history_files(req: DeleteHistoryRequest):
    project_path = req.project_path
    stage = req.stage
    filenames = req.filenames

    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)

    if not project_path:
        return {"success": False, "error": "No project path provided"}

    proj_dir = Path(project_path).resolve()
    if not proj_dir.exists() or not proj_dir.is_dir():
        return {"success": False, "error": "Project path does not exist"}

    deleted_history_dir = proj_dir / "deleted_history"
    deleted_history_dir.mkdir(exist_ok=True)

    moved_files = []
    failed_files = []

    import datetime
    for name in filenames:
        if not name:
            continue
        
        name_path = Path(name)
        if name_path.is_absolute():
            target_file = name_path.resolve()
        else:
            parts = name_path.parts
            if parts and parts[0] == stage:
                target_file = (proj_dir / name_path).resolve()
            else:
                target_file = (proj_dir / stage / name_path).resolve()
        
        try:
            target_file.relative_to(proj_dir)
        except ValueError:
            failed_files.append({"filename": name, "error": "Out of project boundary"})
            continue

        if not target_file.exists() or not target_file.is_file():
            continue
        
        stem = target_file.stem
        suffix = target_file.suffix
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        
        dest_filename = f"{stem}_{timestamp}{suffix}"
        dest_file = deleted_history_dir / dest_filename
        
        counter = 1
        while dest_file.exists():
            dest_filename = f"{stem}_{timestamp}_{counter}{suffix}"
            dest_file = deleted_history_dir / dest_filename
            counter += 1
            
        try:
            shutil.move(str(target_file), str(dest_file))
            moved_files.append(name)
        except Exception as e:
            logger.error("Failed to move file %s to deleted_history: %s", target_file, e)
            failed_files.append({"filename": name, "error": str(e)})

    return {
        "success": len(failed_files) == 0,
        "moved": moved_files,
        "failed": failed_files
    }

@app.post("/api/system/clear-cache")
async def clear_system_cache():
    """Clear memory caches (garbage collector, PyTorch CUDA cache, etc.) to free system resources."""
    import gc
    import sys
    try:
        gc.collect()
        # Clean PyTorch CUDA cache if torch is loaded
        if "torch" in sys.modules:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("Cleared PyTorch CUDA cache successfully.")
        return {"success": True, "message": "System cache cleared successfully."}
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/projects/{name}/clear-stage")
async def clear_project_stage(name: str, request: Request):
    """Delete all files and folders in a project stage directory, and reset its memory (to start completely fresh)."""
    data = await request.json()
    project_path = data.get("project_path")
    if not project_path:
        p_path = project_manager.get_current_project_path()
        if p_path:
            project_path = str(p_path)
    stage = data.get("stage", "")
    if not stage:
        return {"success": False, "error": "No stage directory specified"}
    try:
        if project_path:
            target_dir = Path(project_path) / stage
        else:
            project_manager.load_project(name)
            target_dir = project_manager.get_project_path(name) / stage
            
        if target_dir.exists() and target_dir.is_dir():
            import shutil, time as _time
            import gc as _gc
            # Reversible clear: MOVE stage contents into <project>/_trash/<stage>/<timestamp>/
            # instead of deleting, so a clear can be undone by hand. The newest
            # 5 trash batches per stage are kept; older ones auto-purge.
            # Windows-friendly: per-item move with retry (a viewer/browser
            # holding a handle briefly must not abort the whole clear).
            trash_root = target_dir.parent / "_trash" / stage
            trash_dir = trash_root / _time.strftime("%Y%m%d_%H%M%S")
            trash_dir.mkdir(parents=True, exist_ok=True)
            failed = []
            def _move_retry(src: Path, tries: int = 3):
                for i in range(tries):
                    try:
                        shutil.move(str(src), str(trash_dir / src.name))
                        return True
                    except OSError:
                        _gc.collect()
                        _time.sleep(0.4)
                return False
            for item in target_dir.iterdir():
                if not _move_retry(item):
                    failed.append(str(item))
            if failed:
                return {"success": False, "error": "Could not move " + str(len(failed)) + " file(s) to trash (locked by another program? Close image viewers and retry). First: " + failed[0]}
            try:
                batches = sorted(p for p in trash_root.iterdir() if p.is_dir())
                for old in batches[:-5]:
                    shutil.rmtree(old, ignore_errors=True)
            except Exception:
                pass

        # Update orchestrator project graph memory to match
        if orchestrator:
            pg = orchestrator.memory.project_graph
            if stage == "characters":
                pg["character_assets"] = {}
            elif stage == "locations":
                pg["location_assets"] = {}
            elif stage == "character_sheets":
                pg["character_sheets"] = {}
            elif stage == "location_sheets":
                pg["location_sheets"] = {}
            elif stage == "scenes":
                # Clear all storyboard images in memory
                for s in pg.get("scene_graph", []):
                    for sh in s.get("shots", []):
                        sh.pop("storyboard_image", None)
                        sh.pop("_temp_storyboard_image", None)
                        if sh.get("storyboard_status") in ["approved", "generated"]:
                            sh["storyboard_status"] = "enriched"
                if isinstance(orchestrator.memory.storyboard, list):
                    for entry in orchestrator.memory.storyboard:
                        for sh in entry.get("shots", []):
                            sh.pop("storyboard_image", None)
            elif stage == "videos":
                # Clear all video clips in memory
                for s in pg.get("scene_graph", []):
                    for sh in s.get("shots", []):
                        sh.pop("video_clip", None)
                        sh.pop("_temp_video_clip", None)
                        sh.pop("_temp_video_subfolder", None)
                        if sh.get("video_status") in ["approved", "generated"]:
                            sh["video_status"] = "enriched"
            _save_orchestrator_state()

        return {"success": True}
    except Exception as e:
        logger.error("Failed to clear project stage %s: %s", stage, e)
        return {"success": False, "error": str(e)}


@app.post("/api/projects/{name}/archive-screenplay")
async def archive_screenplay(name: str, request: Request):
    """Archive the current screenplay as XML before clearing."""
    import xml.etree.ElementTree as ET
    from datetime import datetime

    body = await request.json()
    screenplay = body.get("screenplay", {})
    project_path = body.get("project_path", "")

    if not screenplay:
        return {"success": False, "error": "No screenplay data to archive"}

    # Determine project directory
    proj_dir = Path(project_path) if project_path else (project_manager.output_dir / name)
    archives_dir = proj_dir / "archives"
    archives_dir.mkdir(parents=True, exist_ok=True)

    # Build XML
    root = ET.Element("screenplay")
    root.set("title", screenplay.get("title", "Untitled"))
    root.set("archived_at", datetime.now().isoformat())
    root.set("tone", screenplay.get("tone", ""))
    root.set("logline", screenplay.get("logline", ""))

    for scene in screenplay.get("scenes", []):
        scene_el = ET.SubElement(root, "scene")
        scene_el.set("id", str(scene.get("scene_id", scene.get("scene_number", ""))))
        scene_el.set("title", scene.get("scene_title", ""))
        scene_el.set("location", scene.get("location_id", ""))
        scene_el.set("time_of_day", scene.get("time_of_day", ""))
        scene_el.set("emotional_tone", scene.get("emotional_tone", ""))

        chars_el = ET.SubElement(scene_el, "characters")
        for ch in scene.get("characters_present", []):
            c_el = ET.SubElement(chars_el, "character")
            c_el.text = str(ch)

        for shot in scene.get("shots", []):
            shot_el = ET.SubElement(scene_el, "shot")
            for key, val in shot.items():
                if isinstance(val, (str, int, float, bool)):
                    child = ET.SubElement(shot_el, key.replace(" ", "_"))
                    child.text = str(val)
                elif isinstance(val, list):
                    child = ET.SubElement(shot_el, key.replace(" ", "_"))
                    child.text = ", ".join(str(v) for v in val)

    # Also store the raw JSON for full fidelity restoration
    raw_json_el = ET.SubElement(root, "raw_json")
    raw_json_el.text = json.dumps(screenplay, ensure_ascii=False)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in (screenplay.get("title", "untitled")[:30]))
    filename = f"screenplay_{safe_title}_{timestamp}.xml"
    filepath = archives_dir / filename

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(filepath), encoding="unicode", xml_declaration=True)

    return {"success": True, "filename": filename, "path": str(filepath)}


@app.get("/api/projects/{name}/screenplay-archives")
async def list_screenplay_archives(name: str, project_path: str = ""):
    """List archived screenplay XML files."""
    import xml.etree.ElementTree as ET

    proj_dir = Path(project_path) if project_path else (project_manager.output_dir / name)
    archives_dir = proj_dir / "archives"

    if not archives_dir.exists():
        return {"success": True, "archives": []}

    archives = []
    for f in sorted(archives_dir.glob("screenplay_*.xml"), reverse=True):
        try:
            tree = ET.parse(str(f))
            root = tree.getroot()
            archives.append({
                "filename": f.name,
                "title": root.get("title", "Untitled"),
                "archived_at": root.get("archived_at", ""),
                "scenes": len(root.findall("scene")),
            })
        except Exception:
            archives.append({"filename": f.name, "title": "(parse error)", "archived_at": "", "scenes": 0})

    return {"success": True, "archives": archives}


@app.post("/api/projects/{name}/restore-screenplay")
async def restore_screenplay_archive(name: str, request: Request):
    """Restore a screenplay from an archived XML file."""
    import xml.etree.ElementTree as ET

    body = await request.json()
    filename = body.get("filename", "")
    project_path = body.get("project_path", "")

    if not filename:
        return {"success": False, "error": "No filename specified"}

    proj_dir = Path(project_path) if project_path else (project_manager.output_dir / name)
    filepath = proj_dir / "archives" / filename

    if not filepath.exists():
        return {"success": False, "error": f"Archive file not found: {filename}"}

    try:
        tree = ET.parse(str(filepath))
        root = tree.getroot()
        raw_json_el = root.find("raw_json")
        if raw_json_el is not None and raw_json_el.text:
            screenplay = json.loads(raw_json_el.text)
            return {"success": True, "screenplay": screenplay}
        else:
            return {"success": False, "error": "Archive does not contain restorable JSON data"}
    except Exception as e:
        return {"success": False, "error": str(e)}


_thumbnail_cache = {}

def get_image_thumbnail_bytes(img_bytes: bytes, max_width: int = 280) -> bytes:
    from PIL import Image
    import io
    try:
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        if w > max_width:
            ratio = max_width / w
            new_size = (max_width, int(h * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80)
        return out.getvalue()
    except Exception as e:
        logger.error("Failed to generate thumbnail: %s", e)
        return img_bytes

@app.get("/api/projects/{name}/saved-image/{stage}/{filename:path}")
async def get_saved_project_image(name: str, stage: str, filename: str, path: str = None, thumbnail: bool = False):
    """Serve a saved project image with automatic extension fallback."""
    try:
        if path:
            img_path = Path(path) / stage / filename
        else:
            project_manager.load_project(name)
            img_path = project_manager.get_project_path(name) / stage / filename
            
        if not img_path.exists():
            for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]:
                alt_path = img_path.with_name(img_path.name + ext)
                if alt_path.exists():
                    img_path = alt_path
                    break
                    
        if not img_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "Image not found"})
        
        if thumbnail:
            cache_key = f"saved:{img_path}"
            if cache_key in _thumbnail_cache:
                return Response(content=_thumbnail_cache[cache_key], media_type="image/jpeg")
            with open(img_path, "rb") as f:
                img_bytes = f.read()
            thumb_bytes = get_image_thumbnail_bytes(img_bytes)
            _thumbnail_cache[cache_key] = thumb_bytes
            return Response(content=thumb_bytes, media_type="image/jpeg")

        return FileResponse(str(img_path), media_type="image/png")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/projects/{name}/saved-video/{filename:path}")
async def get_saved_project_video(name: str, filename: str, request: Request, path: str = None):
    """Serve a saved project video supporting range requests and extension fallback."""
    try:
        if path:
            vid_path = Path(path) / "videos" / filename
        else:
            project_manager.load_project(name)
            vid_path = project_manager.get_project_path(name) / "videos" / filename
            
        if not vid_path.exists():
            for ext in [".mp4", ".webm", ".mkv", ".mov", ".avi"]:
                alt_path = vid_path.with_name(vid_path.name + ext)
                if alt_path.exists():
                    vid_path = alt_path
                    break
                    
        if not vid_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "Video not found"})
            
        return FileResponse(
            str(vid_path), 
            media_type="video/mp4", 
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "Accept-Ranges": "bytes"
            }
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/projects/{name}/saved-audio/{filename:path}")
async def get_saved_project_audio(name: str, filename: str, path: str = None):
    """Serve a saved project audio file supporting immutable caching."""
    try:
        if path:
            audio_path = Path(path) / "audio" / filename
        else:
            project_manager.load_project(name)
            audio_path = project_manager.get_project_path(name) / "audio" / filename
            
        if not audio_path.exists():
            # Fallback directly in the project directory
            if path:
                audio_path = Path(path) / filename
            else:
                audio_path = project_manager.get_project_path(name) / filename
                
        if not audio_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "Audio file not found"})
            
        return FileResponse(
            str(audio_path), 
            media_type="audio/mpeg" if filename.endswith(".mp3") else "audio/wav",
            headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@app.post("/api/projects/{name}/upload-audio")
async def upload_project_audio(name: str, request: Request, path: str = None):
    """Upload an audio file into the project's audio/ directory for the timeline overlay track."""
    from fastapi import UploadFile, File
    try:
        form = await request.form()
        file = form.get("file")
        if file is None or not getattr(file, "filename", None):
            return JSONResponse(status_code=400, content={"success": False, "error": "No file provided"})

        safe_name = Path(file.filename).name.replace(" ", "_")
        if not safe_name:
            return JSONResponse(status_code=400, content={"success": False, "error": "Invalid filename"})

        if path:
            audio_dir = Path(path) / "audio"
        else:
            audio_dir = project_manager.get_project_path(name) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)

        target = audio_dir / safe_name
        counter = 1
        while target.exists():
            stem, ext = Path(safe_name).stem, Path(safe_name).suffix
            target = audio_dir / f"{stem}_{counter}{ext}"
            counter += 1

        content = await file.read()
        if len(content) > 200 * 1024 * 1024:
            return JSONResponse(status_code=413, content={"success": False, "error": "File too large (max 200 MB)"})
        with open(target, "wb") as f:
            f.write(content)

        return {"success": True, "filename": target.name, "path": f"audio/{target.name}"}
    except Exception as e:
        logger.error("Audio upload failed for %s: %s", name, e)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.post("/api/projects/{name}/extend-video")
async def extend_video(name: str, request: Request):
    """Extract the last frame of a video and use ComfyUI i2v workflow to extend it by 2-4 seconds."""
    import time
    import subprocess
    import shutil
    import requests
    
    try:
        data = await request.json()
        video_filename = data.get("filename", "")
        project_path = data.get("project_path", "")
        steps = int(data.get("steps", 8))
        seed = int(data.get("seed", -1))
        
        if not video_filename:
            return {"success": False, "error": "No filename specified"}
            
        proj_dir = Path(project_path) if project_path else (project_manager.output_dir / name)
        video_path = proj_dir / "videos" / "approved" / video_filename
        if not video_path.exists():
            video_path = proj_dir / video_filename  # fallback
        if not video_path.exists():
            return {"success": False, "error": f"Video not found: {video_filename}"}
            
        # Extract last frame using FFmpeg
        temp_img_name = f"last_frame_{int(time.time())}.png"
        temp_img_path = proj_dir / temp_img_name
        
        cmd = [
            "ffmpeg", "-y", "-sseof", "-1", "-i", str(video_path), 
            "-update", "1", "-q:v", "2", "-frames:v", "1", str(temp_img_path)
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except Exception as e:
            logger.error("Failed to run ffmpeg for frame extraction: %s", e)
            
        if not temp_img_path.exists():
            # Mock fallback if frame extraction failed
            new_filename = f"extended_{video_filename}"
            target_path = proj_dir / "videos" / "approved" / new_filename
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(video_path, target_path)
            return {"success": True, "filename": new_filename, "mocked": True}
            
        if seed == -1:
            seed = int(time.time() * 1000) % 1000000
            
        settings = load_settings()
        workflow_name = settings.get("workflows", {}).get("i2v", "") or DEFAULT_I2V_WORKFLOW
        
        # Trigger ComfyUI generation
        res = comfyui_client.generate_with_workflow(
            prompt="extend camera motion, continuous action",
            workflow_name=workflow_name,
            seed=seed,
            steps=steps,
            input_images=[str(temp_img_path)]
        )
        
        # Delete temp image
        if temp_img_path.exists():
            try:
                temp_img_path.unlink()
            except:
                pass
                
        if res.get("success"):
            new_filename = f"extended_{video_filename}"
            comfy_file = res["filename"]
            src_url = f"{comfyui_client.host}/view?filename={comfy_file}"
            if res.get("subfolder"):
                src_url += f"&subfolder={res['subfolder']}"
                
            resp = requests.get(src_url, timeout=60)
            if resp.status_code == 200:
                target_path = proj_dir / "videos" / "approved" / new_filename
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "wb") as f:
                    f.write(resp.content)
                return {"success": True, "filename": new_filename}
                
        # Fallback duplicate
        new_filename = f"extended_{video_filename}"
        target_path = proj_dir / "videos" / "approved" / new_filename
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video_path, target_path)
        return {"success": True, "filename": new_filename, "mocked": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/projects/{name}/generate-audio")
async def generate_audio_endpoint(name: str, request: Request):
    """Generate audio (VO, SFX, Music) based on a text prompt."""
    import time
    import math
    import struct
    import requests
    
    try:
        data = await request.json()
        prompt = data.get("prompt", "")
        audio_type = data.get("type", "sfx")  # sfx, vo, music
        project_path = data.get("project_path", "")
        
        if not prompt:
            return {"success": False, "error": "No prompt specified"}
            
        proj_dir = Path(project_path) if project_path else (project_manager.output_dir / name)
        filename = f"{audio_type}_{int(time.time())}.mp3"
        
        target_path = proj_dir / "audio" / filename
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Try calling ComfyUI AudioLDM/Stable Audio workflow if set
        settings = load_settings()
        workflow_name = settings.get("workflows", {}).get(f"audio_{audio_type}")
        
        if workflow_name:
            res = comfyui_client.generate_with_workflow(
                prompt=prompt,
                workflow_name=workflow_name
            )
            if res.get("success"):
                comfy_file = res["filename"]
                src_url = f"{comfyui_client.host}/view?filename={comfy_file}"
                resp = requests.get(src_url, timeout=60)
                if resp.status_code == 200:
                    with open(target_path, "wb") as f:
                        f.write(resp.content)
                    return {"success": True, "filename": filename, "path": f"audio/{filename}"}
                    
        # Python-native TTS/SFX/Music Fallback
        try:
            from gtts import gTTS
            tts = gTTS(text=prompt, lang='en')
            tts.save(str(target_path))
            return {"success": True, "filename": filename, "path": f"audio/{filename}"}
        except Exception:
            pass
            
        # Write synthesized WAV tone
        sample_rate = 22050
        duration = 2.0 if audio_type == "sfx" else 5.0
        num_samples = int(duration * sample_rate)
        
        audio_data = bytearray()
        for i in range(num_samples):
            t = i / sample_rate
            if audio_type == "sfx":
                freq = 440.0 - (t * 200.0) # Pitch bend
                val = math.sin(2.0 * math.pi * freq * t)
            elif audio_type == "music":
                val = 0.5 * math.sin(2.0 * math.pi * 261.63 * t) + 0.3 * math.sin(2.0 * math.pi * 329.63 * t)
            else:
                freq = 300.0 if (int(t * 4) % 2 == 0) else 0.0 # pulse beep
                val = math.sin(2.0 * math.pi * freq * t) if freq > 0 else 0.0
                
            sample = int(val * 32767)
            audio_data.extend(struct.pack("<h", sample))
            
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + len(audio_data), b"WAVE", b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16, b"data", len(audio_data)
        )
        wav_filename = filename.replace(".mp3", ".wav")
        target_wav_path = proj_dir / "audio" / wav_filename
        with open(target_wav_path, "wb") as f:
            f.write(header)
            f.write(audio_data)
            
        return {"success": True, "filename": wav_filename, "path": f"audio/{wav_filename}", "mocked": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/projects/{name}/approve")
async def approve_output(name: str, action: ApprovalAction):
    """Approve/reject/skip an output."""
    project_manager.load_project(name)

    if action.action == "approve":
        result = approval_workflow.approve(action.output_path)
    elif action.action == "reject":
        result = approval_workflow.reject(action.reason)
    elif action.action == "skip":
        result = approval_workflow.skip()
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    return result

@app.get("/api/approval/progress")
async def get_approval_progress():
    """Get approval workflow progress."""
    return {"success": True, "progress": approval_workflow.get_progress()}

@app.post("/api/approval/load")
async def load_approval_items(items: List[Dict]):
    """Load items for approval workflow."""
    approval_workflow.load_items(items)
    return {"success": True, "count": len(items)}

@app.get("/api/approval/current")
async def get_current_approval_item():
    """Get current item for approval."""
    item = approval_workflow.get_current()
    return {"success": True, "item": item}

@app.post("/api/llm/providers/{provider}/host")
async def set_llm_host(provider: str, host: str):
    """Set custom host for LLM provider."""
    return {"success": True, "message": f"Host set to {host}"}

@app.post("/api/comfyui/host")
async def set_comfyui_host(host: str):
    """Set ComfyUI host."""
    comfyui_client.set_host(host)
    return {"success": True, "message": f"ComfyUI host set to {host}"}

@app.post("/api/projects/{name}/export-xml")
async def export_project_timeline_xml(name: str, data: dict):
    """Export the NLE timeline as Apple FCP7 XML format."""
    try:
        project_path_str = data.get("project_path", "")
        timeline = data.get("timeline", [])
        timebase = int(data.get("timebase", 24))
        
        proj_dir = Path(project_path_str) if project_path_str else (project_manager.output_dir / name)
        videos_dir = proj_dir / "videos"
        
        # Build XML tree
        import xml.etree.ElementTree as ET
        
        root = ET.Element("xmeml")
        root.set("version", "4")
        
        sequence = ET.SubElement(root, "sequence")
        sequence.set("id", f"sequence-{name}")
        
        name_el = ET.SubElement(sequence, "name")
        name_el.text = f"{name} Assembled Timeline"
        
        # Calculate total duration in frames
        total_frames = 0
        for clip in timeline:
            in_frame = int(clip.get("in_frame", 0))
            out_frame = int(clip.get("out_frame", 240))
            total_frames += (out_frame - in_frame)
            
        dur_el = ET.SubElement(sequence, "duration")
        dur_el.text = str(total_frames)
        
        rate = ET.SubElement(sequence, "rate")
        tb = ET.SubElement(rate, "timebase")
        tb.text = str(timebase)
        ntsc = ET.SubElement(rate, "ntsc")
        ntsc.text = "FALSE"
        
        media = ET.SubElement(sequence, "media")
        video = ET.SubElement(media, "video")
        track = ET.SubElement(video, "track")
        
        # Add clips to the timeline video track
        current_time = 0
        for idx, clip in enumerate(timeline):
            filename = clip.get("filename", "")
            if not filename:
                continue
                
            in_frame = int(clip.get("in_frame", 0))
            out_frame = int(clip.get("out_frame", 240))
            duration = out_frame - in_frame
            
            clip_id = f"clip-{idx+1}"
            clipitem = ET.SubElement(track, "clipitem")
            clipitem.set("id", clip_id)
            
            c_name = ET.SubElement(clipitem, "name")
            c_name.text = filename
            
            # Duration in frames of the original source video
            src_file_path = videos_dir / filename
            src_duration_frames = 240 # Default fallback
            if src_file_path.exists():
                src_duration_frames = max(240, out_frame)
                
            c_dur = ET.SubElement(clipitem, "duration")
            c_dur.text = str(src_duration_frames)
            
            c_rate = ET.SubElement(clipitem, "rate")
            c_tb = ET.SubElement(c_rate, "timebase")
            c_tb.text = str(timebase)
            
            c_start = ET.SubElement(clipitem, "start")
            c_start.text = str(current_time)
            
            c_end = ET.SubElement(clipitem, "end")
            c_end.text = str(current_time + duration)
            
            c_in = ET.SubElement(clipitem, "in")
            c_in.text = str(in_frame)
            
            c_out = ET.SubElement(clipitem, "out")
            c_out.text = str(out_frame)
            
            # File reference
            file_node = ET.SubElement(clipitem, "file")
            file_node.set("id", f"file-{idx+1}")
            
            f_name = ET.SubElement(file_node, "name")
            f_name.text = filename
            
            # Path URL (absolute file:/// path)
            abs_url = f"file:///{str(src_file_path.absolute()).replace('\\', '/')}"
            f_path = ET.SubElement(file_node, "pathurl")
            f_path.text = abs_url
            
            f_rate = ET.SubElement(file_node, "rate")
            f_tb = ET.SubElement(f_rate, "timebase")
            f_tb.text = str(timebase)
            
            # Update current timeline head
            current_time += duration
            
        # Write XML output to file
        xml_filename = f"{name}_timeline_export.xml"
        xml_filepath = proj_dir / xml_filename
        
        # Serialize XML nicely
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(str(xml_filepath), encoding="utf-8", xml_declaration=True)
        
        return {
            "success": True, 
            "filename": xml_filename, 
            "path": str(xml_filepath),
            "xml_content": ET.tostring(root, encoding="unicode")
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/projects/open-folder")
async def open_project_folder(data: dict):
    """Open the project folder in the file explorer."""
    path = data.get("path", "")
    if not path:
        return {"success": False, "error": "No path provided"}
    try:
        import subprocess
        if platform.system() == "Windows":
            subprocess.Popen(["explorer", path])
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return {"success": True}
    except Exception as e:
        logger.error("Failed to open folder: %s", e)
        return {"success": False, "error": str(e)}

@app.get("/api/comfyui/view")
async def view_comfyui_image(request: Request, filename: str, subfolder: str = "", thumbnail: bool = False):
    """View an image or video from ComfyUI output.

    Media responses stream with Range support relayed from ComfyUI, so browsers
    can seek within generated videos (required for timeline scrubbing).
    """
    try:
        import requests
        host = comfyui_client.host
        params = {"filename": filename}
        if subfolder:
            params["subfolder"] = subfolder
            params["type"] = "output"

        is_video = filename.endswith(('.mp4', '.webm', '.gif'))

        if thumbnail and not is_video:
            cache_key = f"comfyui:{filename}:{subfolder}"
            if cache_key in _thumbnail_cache:
                return Response(content=_thumbnail_cache[cache_key], media_type="image/jpeg")
            response = requests.get(f"{host}/view", params=params, timeout=60)
            if response.status_code == 200:
                thumb_bytes = get_image_thumbnail_bytes(response.content)
                _thumbnail_cache[cache_key] = thumb_bytes
                return Response(content=thumb_bytes, media_type="image/jpeg")

        # Relay Range requests so video seeking works in the browser
        forward_headers = {}
        range_header = request.headers.get("range")
        if range_header:
            forward_headers["Range"] = range_header

        response = requests.get(f"{host}/view", params=params, headers=forward_headers, stream=True, timeout=60)
        ctype = response.headers.get("content-type", "video/mp4") if is_video else response.headers.get("content-type", "image/png")

        if range_header or is_video:
            from fastapi.responses import StreamingResponse
            relay_headers = {}
            for h in ("content-range", "accept-ranges", "content-length"):
                if h in response.headers:
                    relay_headers[h] = response.headers[h]
            return StreamingResponse(
                response.iter_content(chunk_size=64 * 1024),
                status_code=response.status_code,
                media_type=ctype,
                headers=relay_headers,
            )

        return Response(content=response.content, media_type=ctype)
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/comfyui/image-to-image")
async def image_to_image(request: Request):
    """Handle image-to-image generation via ComfyUI."""
    try:
        form = await request.form()
        
        image = form.get('image')
        prompt = form.get('prompt', '')
        img_type = form.get('type', 'character')
        
        if not image:
            return {"success": False, "message": "No image provided"}
        
        import base64
        from io import BytesIO
        image_data = await image.read()
        image_b64 = base64.b64encode(image_data).decode('utf-8')
        
        settings = load_settings()
        image_settings = settings.get('image', {})
        model = image_settings.get('model', 'juggernaut_xl.safetensors')
        width = image_settings.get('width', 1024)
        height = image_settings.get('height', 1024)
        
        result = comfyui_client.generate_image_ip2p(
            prompt=prompt,
            input_image=image_b64,
            model=model,
            width=width,
            height=height
        )
        
        if result.get('success'):
            return {"success": True, "message": "Image generated", "output": result.get('output')}
        else:
            return {"success": False, "message": result.get('error', 'Generation failed')}
            
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/comfyui/connect")
async def test_comfyui_connect(url: str = "http://localhost:8188"):
    """Test ComfyUI connection."""
    try:
        import requests
        response = requests.get(f"{url}/system_stats", timeout=5)
        if response.status_code == 200:
            return {"success": True, "message": "Connected to ComfyUI"}
        return {"success": False, "message": f"Status: {response.status_code}"}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/llm/models")
async def get_llm_models(provider: str, host: str = None, models_path: str = None):
    """Get available models for the provider."""
    models = []
    if provider == "ollama":
        base = host or "http://localhost:11434"
        try:
            import requests
            resp = requests.get(f"{base}/api/tags", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
        except:
            pass
    elif provider == "lm_studio":
        base = host or "http://localhost:1234"
        try:
            import requests
            resp = requests.get(f"{base}/v1/models", timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
        except:
            pass
    elif provider == "llama_cpp":
        # Try running server first
        base = host or "http://localhost:8080"
        try:
            import requests
            resp = requests.get(f"{base}/v1/models", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
        except:
            pass
        # Also scan local models folder for GGUF files
        if models_path:
            p = Path(models_path)
            if p.is_dir():
                gguf_files = list(p.glob("**/*.gguf"))
                local_models = sorted(set(str(f.relative_to(p)) for f in gguf_files))
                for m in local_models:
                    if m not in models:
                        models.append(m)
    elif provider == "app_llm":
        if llm_engine:
            models = llm_engine.get_models("app_llm")
        else:
            models_dir = Path(__file__).parent.parent / "models"
            if models_dir.exists():
                models = sorted(f.name for f in models_dir.iterdir() if f.suffix.lower() in (".gguf", ".bin"))
    return {"models": models}

@app.post("/api/llm/test")
async def test_llm_connection(data: dict):
    """Test LLM connection using the engine."""
    if not llm_engine:
        return {"success": False, "message": "LLM engine not available"}
    return llm_engine.test_connection(
        provider_id=data.get("provider", ""),
        host=data.get("host"),
        api_key=data.get("api_key")
    )

@app.post("/api/llm/download-model")
async def download_model(data: dict):
    """Download a GGUF model from HuggingFace."""
    url = data.get("url", "")
    models_path = data.get("models_path", "")
    if not url or not models_path:
        return {"success": False, "error": "URL and models_path required"}
    if "huggingface.co" not in url:
        return {"success": False, "error": "Only HuggingFace URLs supported"}
    try:
        from urllib.parse import urlparse
        import subprocess, sys, shutil
        p = Path(models_path)
        p.mkdir(parents=True, exist_ok=True)
        # Extract repo from URL: https://huggingface.co/username/repo
        parts = urlparse(url).path.strip("/").split("/")
        if len(parts) < 2:
            return {"success": False, "error": "Invalid HuggingFace URL"}
        repo = "/".join(parts[:2])
        dest = p / parts[-1]
        dest.mkdir(exist_ok=True)
        # Try using huggingface-hub if available
        try:
            import huggingface_hub
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=repo, local_dir=str(dest), local_dir_use_symlinks=False)
            return {"success": True, "message": f"Downloaded {repo} to {dest}"}
        except ImportError:
            pass
        # Fallback: try git clone
        if shutil.which("git"):
            clone_url = f"https://huggingface.co/{repo}"
            result = subprocess.run(["git", "clone", "--depth=1", clone_url, str(dest)],
                                    capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                return {"success": True, "message": f"Downloaded {repo} to {dest}"}
            return {"success": False, "error": result.stderr[:200]}
        return {"success": False, "error": "huggingface-hub not installed. Run: pip install huggingface-hub"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding='utf-8-sig'))
    return {}

def save_settings(settings: dict):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding='utf-8')

@app.get("/api/websearch")
async def web_search(q: str, num: int = 5):
    """Web search using DuckDuckGo."""
    try:
        import requests
        from bs4 import BeautifulSoup
        
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://duckduckgo.com/html/?q={q}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        results = []
        
        for item in soup.select('.result')[:num]:
            title = item.select_one('.result__title')
            snippet = item.select_one('.result__snippet')
            if title and snippet:
                results.append({
                    'title': title.get_text(strip=True),
                    'snippet': snippet.get_text(strip=True)
                })
        
        return {'results': results}
    except ImportError:
        return {'results': [], 'error': 'BeautifulSoup4 not installed. Run: pip install beautifulsoup4'}
    except Exception as e:
        return {'results': [], 'error': str(e)}

@app.get("/api/settings")
async def get_settings():
    """Get settings."""
    return load_settings()


# ---------------- ComfyUI live progress -> gen console ----------------

_comfy_prog_lock = threading.Lock()
_comfy_prog_label = {"model": "", "task": "gen"}


@app.get("/api/gen-progress")
async def gen_progress():
    """Current live progress line(s), one per active engine, for the console.

    ComfyUI: sampled from the websocket event cache in comfyui_client
    (step/percent of the running node). WanGP: active bridge jobs with their
    phase. The UI merges these into its active console lines in real time."""
    out = []
    try:
        p = comfyui_client.get_progress() or {}
        if p.get("running"):
            cur, mx = p.get("current", 0), p.get("max", 0)
            node = p.get("node_type") or ""
            pct = int(round(cur * 100.0 / mx)) if mx else None
            txt = f"{node or 'Executing'} step {cur}/{mx}" + (f" \u2014 {pct}%" if pct is not None else "")
            with _comfy_prog_lock:
                model = _comfy_prog_label.get("model") or ""
                task = _comfy_prog_label.get("task") or "gen"
            if model:
                txt = f"{model}: {txt}"
            out.append({"engine": "comfyui", "task": task, "phase": txt})
    except Exception:
        pass
    try:
        r = requests.get(f"{_wangp_bridge_host()}/health", timeout=3).json()
        for jid, j in (r.get("jobs") or {}).items():
            if jid == "count" or not isinstance(j, dict):
                continue
            if j.get("status") in ("done", "error"):
                continue
            # Normalize the phase the same way the UI does so both progress
            # sources produce identical text.
            ph = _re_mod.sub(r"[_\-]+", " ", str(j.get("phase") or j.get("status") or "working")).strip()
            ph = ph[:1].upper() + ph[1:] if ph else "Working"
            if j.get("step") and j.get("steps"):
                ph += f" (step {j['step']}/{j['steps']})"
            pct = j.get("pct")
            if isinstance(pct, (int, float)):
                while pct > 100:
                    pct /= 10
                ph += f" \u2014 {max(0, min(99, round(pct)))}%"
            task = (_gen_job_labels.get(str(jid)) or {}).get("task") or "video"
            out.append({"engine": "wangp", "task": task, "phase": ph})
    except Exception:
        pass
    return {"progress": out}


# ---------------- Generation event log (studio console) ----------------
# Ring buffer of generation activity across both engines (ComfyUI + WanGP),
# shown in the UI's Generation Console and served by GET /api/gen-log.

from collections import deque

_gen_events: deque = deque(maxlen=400)
_gen_event_seq = 0
_gen_job_labels: dict = {}   # job_id -> {"model": str, "task": str}
_wangp_pending_origin: dict = {}   # job_id -> origin/seed/model captured at submit


def _log_gen_event(engine: str, task: str, status: str, detail: str = "", job_id: str = ""):
    """Append one event; consecutive progress posts for a job update one line."""
    global _gen_event_seq
    if status == "progress" and job_id:
        for ev in reversed(_gen_events):
            if ev.get("job_id") == job_id and ev.get("status") == "progress":
                ev["ts"] = time.strftime("%H:%M:%S")
                ev["detail"] = (detail or "")[:220]
                return
    _gen_event_seq += 1
    _gen_events.append({
        "seq": _gen_event_seq,
        "ts": time.strftime("%H:%M:%S"),
        "engine": engine,
        "task": task,
        "status": status,
        "detail": (detail or "")[:220],
        "job_id": job_id,
    })
    logger.info("gen[%s/%s] %s %s", engine, task, status, detail)


def _gen_remember_job(job_id: str, model: str = "", task: str = ""):
    if not job_id:
        return
    info = _gen_job_labels.setdefault(job_id, {"model": "", "task": ""})
    if model:
        info["model"] = model
    if task:
        info["task"] = task
    if len(_gen_job_labels) > 200:
        _gen_job_labels.pop(next(iter(_gen_job_labels)))


def _classify_task(path: str) -> str:
    p = path.lower()
    if "i2v" in p or "t2v" in p or "/video" in p:
        return "video"
    if "audio" in p or "tts" in p:
        return "audio"
    if "i2i" in p or "t2i" in p or "/image" in p:
        return "image"
    return "gen"


@app.middleware("http")
async def _gen_event_middleware(request: Request, call_next):
    """Record engine activity for generation endpoints into the console log
    and the on-disk history gallery (origin, engine, model, seed)."""
    path = request.url.path
    ingest = path.startswith("/api/wangp/job/") and path.endswith("/ingest")
    watch = (
        path.startswith("/api/comfyui/generate/")
        or path.startswith("/api/wangp/generate/")
        or ingest
    )
    if not watch:
        return await call_next(request)
    engine = "wangp" if path.startswith("/api/wangp") else "comfyui"
    task = _classify_task(path)
    # Read the request body for origin/seed/model. Starlette caches it in
    # request._body, so the endpoint handler can still read it afterwards.
    req_meta = {}
    if request.method == "POST":
        try:
            rb = await request.body()
            rj = json.loads(rb) if rb else {}
            if isinstance(rj, dict):
                req_meta = rj
        except Exception:
            req_meta = {}
    # Origin: the scene/asset the generation started from (basename only).
    origin = ""
    src = req_meta.get("input_image") or req_meta.get("input_images") or ""
    if isinstance(src, list):
        src = src[0] if src else ""
    if src:
        origin = str(src).replace("\\", "/")
        m = _re_mod.search(r"scenes/([^/]+?)\.\w+$", origin, _re_mod.I)
        origin = m.group(1) if m else origin.rstrip("/").split("/")[-1]
        origin = origin[:120]
    # Which UI flow triggered it (best effort from the payload shape).
    source = str(req_meta.get("flow") or req_meta.get("source") or "")[:40]
    if not source:
        ol = origin.lower()
        if "shot_" in ol or "scene_" in ol:
            source = "storyboard"
        elif task == "audio" or "tts" in path:
            source = "tts"
        elif req_meta.get("input_images"):
            source = "consistent-shot"
        else:
            source = "direct"
    req_model = str(req_meta.get("model_type") or req_meta.get("workflow_name") or req_meta.get("model") or "")
    req_seed = req_meta.get("seed")
    if engine == "comfyui" and request.method == "POST":
        # Pre-log the queued line so the live progress endpoint has an active
        # line to attach to while the blocking handler runs, and point the
        # progress label at this task immediately (the model name follows on
        # response).
        with _comfy_prog_lock:
            _comfy_prog_label["task"] = task
        _log_gen_event(engine, task, "queued", "dispatched to ComfyUI")
    try:
        response = await call_next(request)
    except Exception as e:
        _log_gen_event(engine, task, "error", f"endpoint crashed: {e}")
        raise
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    try:
        data = json.loads(body) if body else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    jid = str(data.get("job_id") or "")
    if jid:
        task = (_gen_job_labels.get(jid) or {}).get("task") or task
    if engine == "comfyui" and data.get("success"):
        # ComfyUI endpoints block until the workflow finishes; rotate the live
        # label so the next run's progress line names the new workflow/model.
        model = ""
        if path.endswith("/generate/t2i"):
            model = str(data.get("workflow") or data.get("model") or "")
        elif path.endswith("/generate/i2v"):
            model = str(data.get("workflow") or data.get("model") or "")
        if model:
            model = str(model).replace(".json", "")
        with _comfy_prog_lock:
            if model:
                _comfy_prog_label["model"] = model
            _comfy_prog_label["task"] = task
    if ingest:
        if data.get("success"):
            fname = str(data.get("filename") or "output saved")
            _log_gen_event(engine, task, "done", fname, jid)
            pend = _wangp_pending_origin.pop(jid, {}) or {}
            _gen_hist_record(engine, task, fname, jid,
                             origin=pend.get("origin") or origin,
                             seed=pend.get("seed", req_seed),
                             model=pend.get("model") or req_model,
                             source=pend.get("source") or source)
        else:
            _log_gen_event(engine, task, "error", str(data.get("error") or "ingest failed"), jid)
    elif data.get("success") is False and data.get("error"):
        _log_gen_event(engine, task, "error", str(data.get("error")), jid)
    elif jid and (path.endswith("/generate/video") or path.endswith("/generate/image")):
        model = str(((data.get("settings") or {}).get("model_type")) or req_model)
        _gen_remember_job(jid, model=model, task=task)
        _wangp_pending_origin[jid] = {"origin": origin, "seed": req_seed,
                                      "model": model, "source": source}
        if len(_wangp_pending_origin) > 100:
            _wangp_pending_origin.pop(next(iter(_wangp_pending_origin)))
        _log_gen_event(engine, task, "queued", model or "queued on bridge", jid)
    else:
        status = "done" if data.get("success") else "error"
        detail = str(data.get("filename") or data.get("error") or "")
        _log_gen_event(engine, task, status, detail, jid)
        if status == "done":
            _gen_hist_record(engine, task, str(data.get("filename") or ""), jid,
                             origin=origin, seed=req_seed,
                             model=req_model or str(data.get("workflow") or ""),
                             source=source)
    return Response(content=body, status_code=response.status_code,
                    headers=dict(response.headers), media_type=response.media_type)


@app.post("/api/wangp/progress")
async def wangp_progress(data: dict = Body(...)):
    """Client-reported live WanGP job progress (phase/percent) for the console."""
    jid = str(data.get("job_id") or "")
    _gen_remember_job(jid, model=str(data.get("model") or ""), task=str(data.get("task") or ""))
    _log_gen_event("wangp", str(data.get("task") or "video"), "progress",
                   str(data.get("phase") or ""), jid)
    return {"ok": True}


@app.get("/api/gen-log")
async def gen_log():
    """Recent generation events (newest first) for the UI console."""
    return {"events": list(_gen_events)[::-1]}


# ---------------- Generation history (gallery) ----------------
# Every successful generation is recorded with its origin (source image /
# scene), engine, model, seed and which UI flow triggered it. Persisted to
# disk so the gallery survives studio restarts.

_gen_hist_lock = threading.Lock()
_gen_hist_path = Path(__file__).parent / "gen_history.json"


def _gen_hist_load() -> list:
    try:
        with open(_gen_hist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


_gen_hist: list = _gen_hist_load()


def _gen_hist_save():
    try:
        with open(_gen_hist_path, "w", encoding="utf-8") as f:
            json.dump(_gen_hist[-400:], f, ensure_ascii=False)
    except Exception as e:
        logger.warning("gen-history save failed: %s", e)


def _gen_hist_record(engine: str, task: str, filename: str, job_id: str = "",
                     origin: str = "", seed=None, model: str = "", source: str = ""):
    """Record one successful generation for the history gallery."""
    if not filename:
        return
    rec = {
        "id": f"{time.time():.3f}-{job_id or 'x'}",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "engine": engine,
        "task": task,
        "filename": str(filename)[:200],
        "job_id": job_id,
        "origin": str(origin or "")[:160],
        "seed": seed if isinstance(seed, (int, float, str)) else None,
        "model": str(model or "")[:80],
        "source": str(source or "")[:40],
    }
    with _gen_hist_lock:
        _gen_hist.append(rec)
        if len(_gen_hist) > 400:
            del _gen_hist[:len(_gen_hist) - 400]
        _gen_hist_save()


def _gen_item_url(filename: str) -> str:
    """Servable URL for a generated file (all ingest goes through ComfyUI's
    output folder, so the studio view proxy serves images, videos and audio)."""
    from urllib.parse import quote
    return f"/api/comfyui/view?filename={quote(str(filename))}"


@app.get("/api/gen-history")
async def gen_history():
    """Recent successful generations (newest first) with origin metadata."""
    with _gen_hist_lock:
        items = list(_gen_hist)[::-1]
    for it in items:
        it["url"] = _gen_item_url(it.get("filename", ""))
    return {"items": items}


@app.delete("/api/gen-history")
async def gen_history_clear():
    """Clear the generation history (gallery + disk file)."""
    with _gen_hist_lock:
        _gen_hist.clear()
        _gen_hist_save()
    return {"ok": True}


@app.delete("/api/gen-log")
async def gen_log_clear():
    """Clear the generation console log."""
    _gen_events.clear()
    _gen_job_labels.clear()
    return {"ok": True}


# ---------------- WanGP bridge (alternate generation engine) ----------------

def _wangp_bridge_host() -> str:
    s = load_settings()
    return (s.get("wangp") or {}).get("bridge_host") or "http://127.0.0.1:8189"


@app.get("/api/wangp/health")
async def wangp_health():
    """Proxy the WanGP bridge health (version, init state, job count)."""
    import requests as _rq
    try:
        r = _rq.get(f"{_wangp_bridge_host()}/health", timeout=5)
        return r.json()
    except Exception as e:
        return {"status": "unreachable", "error": str(e), "bridge": _wangp_bridge_host()}


@app.get("/api/wangp/models")
async def wangp_models(query: str = "", available: str = "", task: str = ""):
    """List WanGP models with local availability, optionally filtered by query,
    availability, or task kind (image | video | tts)."""
    import requests as _rq
    try:
        r = _rq.get(f"{_wangp_bridge_host()}/models", params={"query": query, "available": available, "task": task}, timeout=60)
        return r.json()
    except Exception as e:
        return {"models": [], "error": str(e)}


@app.post("/api/wangp/generate/image")
async def wangp_generate_image(request: Request):
    """Submit an image job to the WanGP bridge. Returns a job_id for polling."""
    import requests as _rq
    data = await request.json()
    try:
        r = _rq.post(f"{_wangp_bridge_host()}/generate", json=data, timeout=60)
        return r.json()
    except Exception as e:
        return {"success": False, "error": f"WanGP bridge unreachable: {e}"}


@app.get("/api/wangp/job/{job_id}")
async def wangp_job(job_id: str):
    """Poll a WanGP bridge job (status, phase, output files)."""
    import requests as _rq
    try:
        r = _rq.get(f"{_wangp_bridge_host()}/job/{job_id}", timeout=10)
        return r.json()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _comfy_lora_stack(task: str):
    """Normalized ComfyUI LoRA stack for a task from settings.comfyLoras.
    Accepts the list shape (image: [names], image_strengths: [..]) and the
    legacy single shape (image: 'name', image_strength: 1.0).
    Returns (names, strengths) — either may be None when nothing is set."""
    cl = load_settings().get("comfyLoras") or {}
    raw = cl.get(task)
    if not raw:
        return None, None
    names = raw if isinstance(raw, list) else [raw]
    names = [n for n in names if n]
    if not names:
        return None, None
    strengths = cl.get(f"{task}_strengths")
    if not isinstance(strengths, list) or len(strengths) < len(names):
        single = cl.get(f"{task}_strength")
        strengths = [float(single) if single is not None else 1.0] * len(names)
    try:
        strengths = [float(s) for s in strengths[:len(names)]]
    except (TypeError, ValueError):
        strengths = [1.0] * len(names)
    return names, strengths


def _comfyui_output_dir() -> Path | None:
    """Best-effort ComfyUI output folder: explicit setting, else derived from
    ComfyUI's /system_stats argv (its main.py path implies <dir>/output)."""
    s = load_settings()
    p = (s.get("comfyui") or {}).get("output_dir")
    if p:
        return Path(p)
    try:
        import requests as _rq
        st = _rq.get(f"{comfyui_client.host}/system_stats", timeout=5).json()
        argv = (st.get("system") or {}).get("argv") or []
        if argv:
            return Path(argv[0]).parent / "output"
    except Exception:
        pass
    return None


def _apply_stored_loras(payload: dict, task: str) -> dict:
    """Attach the user's saved WanGP LoRA selection + strength for this task.
    Request-supplied loras win over stored defaults."""
    if payload.get("loras"):
        return payload
    s = load_settings()
    sel = (s.get("wangpLoras") or {}).get(task) or []
    if sel:
        payload["loras"] = sel
        strength = (s.get("wangpLoras") or {}).get(f"{task}_strength")
        if strength is not None:
            try:
                payload["lora_strength"] = float(strength)
            except (TypeError, ValueError):
                pass
    return payload


@app.post("/api/wangp/generate/image/wait")
def wangp_generate_image_wait(data: dict = Body(...)):
    """Submit an image job to WanGP, wait for it, and ingest the result into
    ComfyUI's output folder so existing UI flows (preview via /view, upscale,
    save-image) work unchanged. Returns {success, filename, engine:'wangp'}.
    """
    data.setdefault("media", "image")
    return _wangp_wait_and_ingest(_apply_stored_loras(data, "image"), default_wait=1500)


def _wangp_video_payload(data: dict) -> dict:
    """Build a WanGP video job payload from the UI-shaped request body
    (input_image -> image_start frame injection)."""
    payload = {
        "prompt": data.get("prompt", ""),
        "media": "video",
        "wait_timeout": data.get("wait_timeout", 5400),
    }
    for key in ("model_type", "seed", "resolution", "video_length", "duration_seconds"):
        if data.get(key) is not None:
            payload[key] = data[key]
    input_image = data.get("input_image")
    if input_image:
        p = Path(input_image)
        if not p.is_absolute():
            p = Path(data.get("project_path", "")) / input_image
        payload["extra"] = {"image_start": [str(p)]}
    if data.get("ref_images"):
        payload["ref_images"] = data["ref_images"]
    return payload


@app.post("/api/wangp/generate/video")
async def wangp_generate_video(request: Request):
    """Submit-only i2v/t2v: returns {job_id} immediately so the UI can poll live
    progress via /api/wangp/job/{job_id} (phase/percent) and ingest the result
    with /api/wangp/job/{job_id}/ingest once done."""
    data = await request.json()
    bridge = _wangp_bridge_host()
    try:
        import requests as _rq
        sub = _rq.post(f"{bridge}/generate", json=_apply_stored_loras(_wangp_video_payload(data), "video"), timeout=60).json()
    except Exception as e:
        return {"success": False, "error": f"WanGP bridge unreachable: {e}", "engine": "wangp"}
    if not sub.get("job_id"):
        return {"success": False, "error": sub.get("error", "no job_id"), "engine": "wangp"}
    return {"success": True, "job_id": sub["job_id"], "engine": "wangp"}


@app.post("/api/wangp/generate/video/wait")
def wangp_generate_video_wait(data: dict = Body(...)):
    """i2v/t2v through WanGP: submit, wait, ingest the mp4 into ComfyUI's output
    folder. Accepts input_image (absolute or project-relative path) like the
    ComfyUI i2v endpoint; it is mapped to WanGP's image_start frame injection.
    Returns the ComfyUI-style {success, filename, subfolder} shape so existing
    video flows work unchanged.
    """
    return _wangp_wait_and_ingest(_apply_stored_loras(_wangp_video_payload(data), "video"), default_wait=5400)


@app.post("/api/wangp/generate/audio/wait")
def wangp_generate_audio_wait(data: dict = Body(...)):
    """TTS / audio generation through WanGP: submit, wait, ingest the file into
    ComfyUI's output folder. Returns {success, filename} for playback via /view.
    """
    payload = {
        "prompt": data.get("prompt", ""),
        "media": "audio",
        "wait_timeout": data.get("wait_timeout", 1500),
    }
    for key in ("model_type", "seed", "duration_seconds"):
        if data.get(key) is not None:
            payload[key] = data[key]
    if data.get("extra"):
        payload["extra"] = data["extra"]
    return _wangp_wait_and_ingest(_apply_stored_loras(payload, "tts"), default_wait=1500)


def _wangp_ingest_job(job: dict, job_id: str):
    """Copy a finished WanGP job's first output into ComfyUI's output folder
    (WanGP_NNNNN_ numbering) so it is servable via /api/comfyui/view. Returns
    the ComfyUI-shaped {success, filename, subfolder} result."""
    import shutil
    status = job.get("status")
    if status == "error":
        return {"success": False, "error": job.get("error", "WanGP job failed"),
                "engine": "wangp", "job_id": job_id}
    if status != "done" or not job.get("files"):
        err = job.get("error") or ("WanGP job finished but produced no output" if status == "done"
                                   else "WanGP job not finished yet")
        return {"success": False, "pending": status != "done", "status": status,
                "phase": job.get("phase"), "error": err, "engine": "wangp", "job_id": job_id}

    out_dir = _comfyui_output_dir()
    if not out_dir or not out_dir.exists():
        return {"success": False, "error": "ComfyUI output folder not found for ingest",
                "engine": "wangp", "job_id": job_id}
    src = Path(job["files"][0])
    if not src.exists():
        return {"success": False, "error": f"WanGP output missing: {src}", "engine": "wangp", "job_id": job_id}
    try:
        import re as _re
        existing = list(out_dir.glob("WanGP_*"))
        idx = 1
        for f in existing:
            m = _re.match(r"WanGP_(\d+)_", f.stem)
            if m:
                idx = max(idx, int(m.group(1)) + 1)
        dest = out_dir / f"WanGP_{idx:05d}_{src.stem[:40].replace(' ', '_')}{src.suffix or '.png'}"
        shutil.copyfile(src, dest)
    except Exception as e:
        return {"success": False, "error": f"Ingest failed: {e}", "engine": "wangp", "job_id": job_id}
    logger.info("WanGP ingest: %s -> %s", src.name, dest.name)
    return {"success": True, "filename": dest.name, "subfolder": "",
            "engine": "wangp", "provider_used": "wangp", "job_id": job_id}


@app.post("/api/wangp/job/{job_id}/ingest")
def wangp_job_ingest(job_id: str):
    """Ingest a (finished) WanGP job's output into ComfyUI's output folder.
    Companion to the submit-only /api/wangp/generate/video endpoint: the UI
    polls /api/wangp/job/{id} for live progress, then calls this once done."""
    bridge = _wangp_bridge_host()
    try:
        import requests as _rq
        job = _rq.get(f"{bridge}/job/{job_id}", timeout=15).json()
    except Exception as e:
        return {"success": False, "error": f"WanGP bridge unreachable: {e}",
                "engine": "wangp", "job_id": job_id}
    return _wangp_ingest_job(job, job_id)


def _wangp_wait_and_ingest(data: dict, default_wait: int):
    """Shared submit -> poll -> ingest-into-ComfyUI-output routine for WanGP jobs."""
    import requests as _rq
    bridge = _wangp_bridge_host()
    try:
        sub = _rq.post(f"{bridge}/generate", json=data, timeout=60).json()
    except Exception as e:
        return {"success": False, "error": f"WanGP bridge unreachable: {e}", "engine": "wangp"}
    job_id = sub.get("job_id")
    if not job_id:
        return {"success": False, "error": sub.get("error", "no job_id"), "engine": "wangp"}

    deadline = time.time() + int(data.get("wait_timeout", default_wait))
    job = {}
    while time.time() < deadline:
        try:
            job = _rq.get(f"{bridge}/job/{job_id}", timeout=15).json()
        except Exception:
            job = {}
        if job.get("status") in ("done", "error"):
            break
        time.sleep(3)

    if job.get("status") not in ("done", "error"):
        job = {**job, "error": "WanGP job did not finish in time"}
    return _wangp_ingest_job(job, job_id)


@app.get("/api/wangp/model/{model_type}")
async def wangp_model_detail(model_type: str):
    """Default settings + capability schema for one WanGP model (drives the UI)."""
    import requests as _rq
    try:
        r = _rq.get(f"{_wangp_bridge_host()}/model/{model_type}", timeout=60)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/wangp/loras")
async def wangp_loras():
    """WanGP's LoRA library grouped by family folder (ltx2, wan_i2v, minimax_h3...)."""
    import requests as _rq
    try:
        r = _rq.get(f"{_wangp_bridge_host()}/loras", timeout=30)
        return r.json()
    except Exception as e:
        return {"families": {}, "error": str(e)}


@app.get("/api/comfyui/loras")
async def comfyui_loras():
    """ComfyUI's LoRA list (relative names used by LoraLoader nodes)."""
    import requests as _rq
    try:
        r = _rq.get(f"{comfyui_client.host}/models/loras", timeout=15)
        names = r.json()
        return {"loras": names if isinstance(names, list) else []}
    except Exception as e:
        return {"loras": [], "error": str(e)}

@app.post("/api/settings")
async def save_settings_endpoint(data: dict):
    """Save settings, preserving genres and other non-overlapping keys."""
    logger.info("POST /api/settings — %d keys", len(data))
    existing = load_settings()
    for key in existing:
        if key not in data:
            data[key] = existing[key]
    # Ensure image_gen section exists
    if "image_gen" not in data:
        data["image_gen"] = existing.get("image_gen", {"provider": "comfyui", "model": "", "host": "http://localhost:8188", "apiKey": ""})
    # Ensure llm auto_start flag
    if "llm" not in data:
        data["llm"] = existing.get("llm", {"provider": "omniroute", "model": "auto", "host": "http://127.0.0.1:20128", "apiKey": "", "auto_start": True})
    # Ensure comfyui auto_start flag
    if "comfyui" not in data:
        data["comfyui"] = existing.get("comfyui", {"auto_start": True, "host": "http://localhost:8188", "port": 8188, "use_sage_attention": True, "path": "", "models_path": ""})
    save_settings(data)
    if data.get("comfyui", {}).get("host"):
        comfyui_client.set_host(data["comfyui"]["host"])
    # Auto-start local LLM if enabled
    if data.get("llm", {}).get("auto_start", False) and llm_engine:
        try:
            result = llm_engine.launch_local_llm(data["llm"]["provider"])
            if not result.get("success"):
                logger.warning("Auto-start LLM failed: %s", result.get("error"))
        except Exception as e:
            logger.warning("Auto-start LLM exception: %s", e)
    # Auto-start ComfyUI if enabled
    cui = data.get("comfyui", {})
    if cui.get("auto_start", False) and comfyui_client:
        try:
            status = comfyui_client.get_comfyui_status()
            if not status.get("running"):
                comfyui_client.launch_comfyui(
                    path=cui.get("path") or None,
                    host=cui.get("host", "0.0.0.0"),
                    port=cui.get("port", 8188),
                    use_sage_attention=cui.get("use_sage_attention", True)
                )
        except Exception:
            pass
    # Restart Telegram Bot service if token updated
    if telegram_bot:
        try:
            telegram_bot.stop()
            telegram_bot.start()
        except Exception as e:
            logger.warning("Failed to restart Telegram bot: %s", e)
    return {"success": True}

# Genre management
@app.get("/api/genres")
async def list_genres():
    """List all custom genres."""
    settings = load_settings()
    genres = settings.get("genres", [])
    for g in genres:
        img_path = GENRES_DIR / g["image"]
        g["has_image"] = img_path.exists()
    return {"genres": genres}

@app.post("/api/genres")
async def add_genre(name: str = Form(...), file: UploadFile = File(None), category: str = Form(None)):
    """Add a custom genre with optional image and category."""
    settings = load_settings()
    genres = settings.get("genres", [])
    
    # Check for duplicate
    for g in genres:
        if g["name"].lower() == name.lower():
            return {"success": False, "error": "Genre already exists"}
    
    image_filename = None
    if file and file.filename:
        ext = Path(file.filename).suffix or ".png"
        safe_name = name.lower().replace(" ", "_").replace("/", "_")
        image_filename = f"{safe_name}{ext}"
        dest = GENRES_DIR / image_filename
        content = await file.read()
        dest.write_bytes(content)
    
    genre_entry = {"name": name, "image": image_filename, "category": category or "Other"} if image_filename else {"name": name, "image": None, "category": category or "Other"}
    genres.append(genre_entry)
    settings["genres"] = genres
    save_settings(settings)
    return {"success": True, "genre": genre_entry}

@app.post("/api/genres/batch")
async def add_genres_batch(files: List[UploadFile] = File(...)):
    """Add multiple genres from a folder drop (names derived from filenames, categories from subfolder names)."""
    settings = load_settings()
    genres = settings.get("genres", [])
    added = []
    errors = []
    for file in files:
        original_name = Path(file.filename).stem
        name = original_name.replace("_", " ").replace("-", " ").title()
        parts = Path(file.filename).parts
        category = parts[-2] if len(parts) > 1 else "Other"
        for g in genres:
            if g["name"].lower() == name.lower():
                errors.append(f"{name}: already exists")
                break
        else:
            ext = Path(file.filename).suffix or ".png"
            safe_name = name.lower().replace(" ", "_").replace("/", "_")
            image_filename = f"{safe_name}{ext}"
            dest = GENRES_DIR / image_filename
            content = await file.read()
            dest.write_bytes(content)
            entry = {"name": name, "image": image_filename, "category": category}
            genres.append(entry)
            added.append(entry)
    settings["genres"] = genres
    save_settings(settings)
    return {"success": True, "added": added, "errors": errors, "count": len(added)}

@app.delete("/api/genres/{name}")
async def delete_genre(name: str):
    """Delete a custom genre."""
    settings = load_settings()
    genres = settings.get("genres", [])
    for i, g in enumerate(genres):
        if g["name"].lower() == name.lower():
            if g.get("image"):
                img_path = GENRES_DIR / g["image"]
                if img_path.exists():
                    img_path.unlink()
            genres.pop(i)
            settings["genres"] = genres
            save_settings(settings)
            return {"success": True}
    return {"success": False, "error": "Genre not found"}

@app.get("/api/genres/image/{filename}")
async def get_genre_image(filename: str):
    """Serve a genre image."""
    img_path = GENRES_DIR / filename
    if img_path.exists():
        return FileResponse(str(img_path))
    return JSONResponse(status_code=404, content={"error": "Image not found"})

# Visual style management
@app.get("/api/visual-styles")
async def list_visual_styles():
    settings = load_settings()
    styles = settings.get("visual_styles", [])
    for s in styles:
        if s.get("image"):
            img_path = VISUAL_STYLES_DIR / s["image"]
            s["has_image"] = img_path.exists()
        else:
            s["has_image"] = False
    return {"styles": styles}

@app.post("/api/visual-styles")
async def add_visual_style(name: str = Form(...), file: UploadFile = File(None)):
    settings = load_settings()
    styles = settings.get("visual_styles", [])
    for s in styles:
        if s["name"].lower() == name.lower():
            return {"success": False, "error": "Style already exists"}
    image_filename = None
    if file and file.filename:
        ext = Path(file.filename).suffix or ".png"
        safe_name = name.lower().replace(" ", "_").replace("/", "_")
        image_filename = f"{safe_name}{ext}"
        dest = VISUAL_STYLES_DIR / image_filename
        content = await file.read()
        dest.write_bytes(content)
    entry = {"name": name, "image": image_filename} if image_filename else {"name": name, "image": None}
    styles.append(entry)
    settings["visual_styles"] = styles
    save_settings(settings)
    return {"success": True, "style": entry}

@app.post("/api/visual-styles/batch")
async def add_visual_styles_batch(files: List[UploadFile] = File(...)):
    settings = load_settings()
    styles = settings.get("visual_styles", [])
    added = []; errors = []
    for file in files:
        original_name = Path(file.filename).stem
        name = original_name.replace("_", " ").replace("-", " ").title()
        for s in styles:
            if s["name"].lower() == name.lower():
                errors.append(f"{name}: already exists"); break
        else:
            ext = Path(file.filename).suffix or ".png"
            safe_name = name.lower().replace(" ", "_").replace("/", "_")
            dest = VISUAL_STYLES_DIR / f"{safe_name}{ext}"
            dest.write_bytes(await file.read())
            entry = {"name": name, "image": f"{safe_name}{ext}"}
            styles.append(entry); added.append(entry)
    settings["visual_styles"] = styles
    save_settings(settings)
    return {"success": True, "added": added, "errors": errors, "count": len(added)}

@app.delete("/api/visual-styles/{name}")
async def delete_visual_style(name: str):
    settings = load_settings()
    styles = settings.get("visual_styles", [])
    for i, s in enumerate(styles):
        if s["name"].lower() == name.lower():
            if s.get("image"):
                img_path = VISUAL_STYLES_DIR / s["image"]
                if img_path.exists():
                    img_path.unlink()
            styles.pop(i)
            settings["visual_styles"] = styles
            save_settings(settings)
            return {"success": True}
    return {"success": False, "error": "Style not found"}

@app.get("/api/visual-styles/image/{filename}")
async def get_visual_style_image(filename: str):
    img_path = VISUAL_STYLES_DIR / filename
    if img_path.exists():
        return FileResponse(str(img_path))
    return JSONResponse(status_code=404, content={"error": "Image not found"})

# Film aesthetic management
@app.get("/api/film-aesthetics")
async def list_film_aesthetics():
    settings = load_settings()
    aesthetics = settings.get("film_aesthetics", [])
    for a in aesthetics:
        if a.get("image"):
            img_path = FILM_AESTHETICS_DIR / a["image"]
            a["has_image"] = img_path.exists()
        else:
            a["has_image"] = False
    return {"aesthetics": aesthetics}

@app.post("/api/film-aesthetics")
async def add_film_aesthetic(name: str = Form(...), file: UploadFile = File(None)):
    settings = load_settings()
    aesthetics = settings.get("film_aesthetics", [])
    for a in aesthetics:
        if a["name"].lower() == name.lower():
            return {"success": False, "error": "Aesthetic already exists"}
    image_filename = None
    if file and file.filename:
        ext = Path(file.filename).suffix or ".png"
        safe_name = name.lower().replace(" ", "_").replace("/", "_")
        image_filename = f"{safe_name}{ext}"
        dest = FILM_AESTHETICS_DIR / image_filename
        content = await file.read()
        dest.write_bytes(content)
    entry = {"name": name, "image": image_filename} if image_filename else {"name": name, "image": None}
    aesthetics.append(entry)
    settings["film_aesthetics"] = aesthetics
    save_settings(settings)
    return {"success": True, "aesthetic": entry}

@app.post("/api/film-aesthetics/batch")
async def add_film_aesthetics_batch(files: List[UploadFile] = File(...)):
    settings = load_settings()
    aesthetics = settings.get("film_aesthetics", [])
    added = []; errors = []
    for file in files:
        original_name = Path(file.filename).stem
        name = original_name.replace("_", " ").replace("-", " ").title()
        for a in aesthetics:
            if a["name"].lower() == name.lower():
                errors.append(f"{name}: already exists"); break
        else:
            ext = Path(file.filename).suffix or ".png"
            safe_name = name.lower().replace(" ", "_").replace("/", "_")
            dest = FILM_AESTHETICS_DIR / f"{safe_name}{ext}"
            dest.write_bytes(await file.read())
            entry = {"name": name, "image": f"{safe_name}{ext}"}
            aesthetics.append(entry); added.append(entry)
    settings["film_aesthetics"] = aesthetics
    save_settings(settings)
    return {"success": True, "added": added, "errors": errors, "count": len(added)}

@app.delete("/api/film-aesthetics/{name}")
async def delete_film_aesthetic(name: str):
    settings = load_settings()
    aesthetics = settings.get("film_aesthetics", [])
    for i, a in enumerate(aesthetics):
        if a["name"].lower() == name.lower():
            if a.get("image"):
                img_path = FILM_AESTHETICS_DIR / a["image"]
                if img_path.exists():
                    img_path.unlink()
            aesthetics.pop(i)
            settings["film_aesthetics"] = aesthetics
            save_settings(settings)
            return {"success": True}
    return {"success": False, "error": "Aesthetic not found"}

@app.get("/api/film-aesthetics/image/{filename}")
async def get_film_aesthetic_image(filename: str):
    img_path = FILM_AESTHETICS_DIR / filename
    if img_path.exists():
        return FileResponse(str(img_path))
    return JSONResponse(status_code=404, content={"error": "Image not found"})

# Workflow management
WORKFLOWS_DIR = Path(__file__).parent / "workflows"

@app.get("/api/workflows")
async def list_workflows():
    """List uploaded workflow files."""
    Workflows_DIR = Path(__file__).parent / "workflows"
    if not Workflows_DIR.exists():
        return {"workflows": []}
    files = []
    for f in Workflows_DIR.iterdir():
        if f.suffix == ".json" and f.is_file() and not f.name.startswith("backup"):
            files.append({"name": f.stem, "filename": f.name, "size": f.stat().st_size})
    return {"workflows": files}

@app.post("/api/workflows/upload")
async def upload_workflow(request: Request):
    """Upload a workflow JSON file."""
    try:
        form = await request.form()
        file = form.get("file")
        if not file:
            return {"success": False, "error": "No file provided"}
        Workflows_DIR = Path(__file__).parent / "workflows"
        Workflows_DIR.mkdir(exist_ok=True)
        content = await file.read()
        # Validate JSON
        import json
        json.loads(content)
        filename = file.filename.replace("..", "").replace("/", "").replace("\\", "")
        dest = Workflows_DIR / filename
        dest.write_bytes(content)
        return {"success": True, "filename": filename}
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON file"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.delete("/api/workflows/{name}")
async def delete_workflow(name: str):
    """Delete a workflow file."""
    try:
        Workflows_DIR = Path(__file__).parent / "workflows"
        f = Workflows_DIR / f"{name}.json"
        if f.exists():
            f.unlink()
            return {"success": True}
        # Try with .json extension already
        f2 = Workflows_DIR / name
        if f2.exists():
            f2.unlink()
            return {"success": True}
        return {"success": False, "error": "File not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/agent-wizard/chat")
async def agent_wizard_chat(request: Request):
    """Interact with the Agentic Project Creator chatbot."""
    body = await request.json()
    message = body.get("message", "")
    mode = body.get("mode", "semi_automated")
    chat_id = body.get("chat_id", "web_session")
    
    if not agent_creator:
        raise HTTPException(status_code=500, detail="LLM Agent Engine not available")
        
    session = agent_creator.get_or_create_session(chat_id, mode)
    session.mode = mode
    
    res = agent_creator.handle_message(chat_id, message)
    return {
        "success": True,
        "response": res["response"],
        "state": res["state"],
        "progress": session.progress_status,
        "current_approval": session.data.get("current_approval")
    }

@app.get("/api/agent-wizard/status")
async def agent_wizard_status(chat_id: str = "web_session"):
    """Get the current state and build progress of the agent creator."""
    if not agent_creator:
        raise HTTPException(status_code=500, detail="LLM Agent Engine not available")
        
    session = agent_creator.get_or_create_session(chat_id)
    return {
        "success": True,
        "state": session.state,
        "progress": session.progress_status,
        "current_approval": session.data.get("current_approval")
    }

# === Film-Making Agent API ===

class FilmAgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "web_session"
    project_path: Optional[str] = None

@app.post("/api/film-agent/chat")
async def film_agent_chat(request: FilmAgentChatRequest):
    """Chat with the Film-Making Agent — an LLM-powered agent with professional film-making skills."""
    if not film_agent:
        raise HTTPException(status_code=500, detail="Film Agent not available — LLM engine not loaded")

    # Build project context if path provided
    project_context = None
    if request.project_path:
        state_file = Path(request.project_path) / "project_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    project_context = json.load(f)
                # Production tools need the on-disk path (not part of the state file itself)
                project_context["_project_path"] = request.project_path
            except Exception:
                pass

    res = film_agent.handle_message(
        session_id=request.session_id,
        message=request.message,
        project_context=project_context
    )

    return {
        "success": True,
        "response": res["response"],
        "tools_used": res.get("tools_used", []),
        "session_id": res["session_id"],
        "message_count": res["message_count"]
    }

@app.get("/api/film-agent/session/{session_id}")
async def film_agent_session_info(session_id: str):
    """Get film agent session info."""
    if not film_agent:
        raise HTTPException(status_code=500, detail="Film Agent not available")
    return film_agent.get_session_info(session_id)

@app.post("/api/film-agent/reset/{session_id}")
async def film_agent_reset(session_id: str):
    """Reset a film agent conversation session."""
    if not film_agent:
        raise HTTPException(status_code=500, detail="Film Agent not available")
    film_agent.reset_session(session_id)
    return {"success": True, "message": f"Session {session_id} reset"}

# === Executive Producer API ===

class EPChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = "ep_session"
    project_path: Optional[str] = None

@app.post("/api/executive-producer/chat")
async def executive_producer_chat(request: EPChatRequest):
    """Chat with the Executive Producer — orchestrates multiple sub-agents."""
    if not executive_producer:
        raise HTTPException(status_code=500, detail="Executive Producer not available — LLM engine not loaded")

    project_context = None
    if request.project_path:
        state_file = Path(request.project_path) / "project_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    project_context = json.load(f)
            except Exception:
                pass

    res = executive_producer.handle_message(
        session_id=request.session_id,
        message=request.message,
        project_context=project_context
    )

    return {
        "success": True,
        "response": res["response"],
        "delegations": res.get("delegations", []),
        "phase": res.get("phase", "vision"),
        "completed_phases": res.get("completed_phases", []),
        "session_id": res["session_id"]
    }

@app.get("/api/executive-producer/status/{session_id}")
async def executive_producer_status(session_id: str):
    """Get EP session status."""
    if not executive_producer:
        raise HTTPException(status_code=500, detail="Executive Producer not available")
    return executive_producer.get_session_status(session_id)

# === Production Package Parser API ===

class ParsePackageRequest(BaseModel):
    ep_output: str
    project_name: Optional[str] = None
    project_path: Optional[str] = None

@app.post("/api/production-package/parse")
async def parse_production_package(request: ParsePackageRequest):
    """Parse EP markdown output into structured screenplay, characters, locations, prompts."""
    from core.production_package_parser import parse_production_package, package_to_project_state
    try:
        package = parse_production_package(request.ep_output)
        state = package_to_project_state(package)
        return {
            "success": True,
            "package": package,
            "state": state,
            "summary": {
                "title": package.get("title", ""),
                "scenes": len(package.get("screenplay", {}).get("scenes", [])),
                "characters": len(package.get("characters", [])),
                "locations": len(package.get("locations", [])),
                "shots": sum(len(s.get('shots', [])) for s in package.get('screenplay', {}).get('scenes', [])),
                "prompts": len(package.get("ai_prompts", [])),
                "concepts": len(package.get("concepts", [])),
            }
        }
    except Exception as e:
        logger.error(f"Failed to parse production package: {e}")
        raise HTTPException(status_code=500, detail=f"Parse failed: {str(e)}")

class SavePackageRequest(BaseModel):
    ep_output: str
    project_name: str
    project_path: str
    merge: Optional[bool] = True  # merge with existing state or replace

@app.post("/api/production-package/save")
async def save_production_package(request: SavePackageRequest):
    """Parse EP output and save to project_state.json."""
    from core.production_package_parser import parse_production_package, package_to_project_state
    try:
        package = parse_production_package(request.ep_output)
        new_state = package_to_project_state(package)
        
        project_path = Path(request.project_path)
        state_file = project_path / "project_state.json"
        
        # Merge with existing state if requested
        existing_state = {}
        if request.merge and state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    existing_state = json.load(f)
            except Exception:
                pass
        
        # Merge: new data overwrites existing, but keeps fields not in new_state
        merged = {**existing_state, **new_state}
        
        # Also save raw EP output as a reference file
        ep_dir = project_path / "production_packages"
        ep_dir.mkdir(exist_ok=True)
        import hashlib
        short_hash = hashlib.md5(request.ep_output[:200].encode()).hexdigest()[:8]
        ep_file = ep_dir / f"ep_package_{short_hash}.md"
        with open(ep_file, "w", encoding="utf-8") as f:
            f.write(request.ep_output)
        
        # Save merged state
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, default=str)
        
        # Also push character/location data to orchestrator so Asset Studio page works
        if orchestrator and package.get("characters"):
            pg = orchestrator.memory.project_graph
            pg["character_bible"] = package["characters"]
            pg["location_bible"] = package.get("locations", [])
            if package.get("screenplay"):
                pg["screenplay"] = package["screenplay"]
            logger.info("Pushed %d chars, %d locs to orchestrator",
                       len(package["characters"]), len(package.get("locations", [])))
        
        return {
            "success": True,
            "state": merged,
            "summary": {
                "title": package.get("title", ""),
                "scenes": len(package.get("screenplay", {}).get("scenes", [])),
                "characters": len(package.get("characters", [])),
                "locations": len(package.get("locations", [])),
                "shots": sum(len(s.get('shots', [])) for s in package.get('screenplay', {}).get('scenes', [])),
                "prompts": len(package.get("ai_prompts", [])),
            },
            "saved_to": str(state_file),
            "ep_archive": str(ep_file),
        }
    except Exception as e:
        logger.error(f"Failed to save production package: {e}")
        raise HTTPException(status_code=500, detail=f"Save failed: {str(e)}")

@app.get("/api/film-agent/skills")
async def film_agent_skills():
    """List all available film-making skills/tools."""
    from core.film_agent import FILM_SKILLS
    skills = []
    for tool in FILM_SKILLS:
        func = tool.get("function", {})
        skills.append({
            "name": func.get("name"),
            "description": func.get("description"),
            "parameters": func.get("parameters", {}).get("properties", {})
        })
    return {"success": True, "skills": skills, "count": len(skills)}


def get_default_html() -> str:
    """Get default HTML if templates not available."""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Ultimate AI Film Studio</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a2e; color: #eee; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        h1 { text-align: center; padding: 40px 0; color: #00d4ff; }
        .loading { text-align: center; color: #888; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Ultimate AI Film Studio</h1>
        <p class="loading">Loading...</p>
    </div>
    <script>console.log('App loaded');</script>
</body>
</html>
    """


if __name__ == "__main__":
    import uvicorn
    try:
        port = int(os.environ.get("PORT", "") or 7860)
    except ValueError:
        port = 7860
    if port <= 0:
        # PORT=0 in the environment would make uvicorn bind a random free
        # port every launch — the app would seem to "vanish". Force default.
        port = 7860
    # Single-instance guard: two app instances sharing one ComfyUI and one
    # project file can silently corrupt state. Refuse to double-start.
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as _resp:
            logger.warning("Another instance is already running on port %s — exiting to protect project state.", port)
            print(f"Ultimate AI Film Studio is already running on http://127.0.0.1:{port}")
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        pass  # nothing answering — safe to start
    logger.info("Starting Ultimate AI Film Studio on http://127.0.0.1:%s", port)
    uvicorn.run(app, host="127.0.0.1", port=port)