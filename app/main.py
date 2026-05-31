import os
import json
import shutil
import platform
import logging
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response, StreamingResponse
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
    from core.project_manager import ProjectManager
    from core.approval_workflow import ApprovalWorkflow
    from core.image_engine import ImageEngine
    from core.orchestrator import CinematicOrchestrator, load_master_system_prompt
    from core.agent_project_creator import AgentProjectCreator
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
                "provider": "app_llm",
                "model": "gemma-4-E2B-it-Q4_K_M.gguf",
                "host": "http://localhost:8081",
                "apiKey": "",
                "auto_start": True,
                "n_gpu_layers": -1
            }
            changed = True
        else:
            llm_settings = settings["llm"]
            if not llm_settings.get("provider"):
                llm_settings["provider"] = "app_llm"
                llm_settings["host"] = "http://localhost:8081"
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
project_manager = ProjectManager() if HAS_LLM else None
approval_workflow = ApprovalWorkflow() if HAS_LLM else None
agent_creator = AgentProjectCreator(project_manager, llm_engine) if HAS_LLM else None
telegram_bot = TelegramBotService(str(SETTINGS_FILE), agent_creator) if HAS_LLM else None

# ComfyUI client - ALWAYS use placeholder to avoid auto-connect issues
# User can manually connect ComfyUI when they want to use it
from core.comfyui_client import ComfyUIClient
comfyui_client = ComfyUIClient()
image_engine = ImageEngine(comfyui_client=comfyui_client, settings_path=str(SETTINGS_FILE)) if HAS_LLM else None
orchestrator = CinematicOrchestrator(llm_engine=llm_engine) if HAS_LLM else None

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
    """Serve the modular timeline NLE UI."""
    try:
        html_file = base_dir / "ui" / "templates" / "timeline.html"
        if html_file.exists():
            with open(html_file, 'r', encoding='utf-8') as f:
                headers = {
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
                return HTMLResponse(content=f.read(), headers=headers)
    except Exception as e:
        logger.error("Error serving Timeline HTML: %s", e)
    return HTMLResponse(content="<h1>Timeline file not found</h1>")


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

    provider_id = request.provider or request.app_provider or "app_llm"
    default_model = "gemma-4-E2B-it-Q4_K_M.gguf" if provider_id == "app_llm" else "llama3.1"
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
async def get_llm_status(provider: str):
    """Check if a local LLM is running."""
    return llm_engine.get_local_status(provider) if llm_engine else {"running": False}

@app.get("/api/gpu/status")
async def get_gpu_status():
    """Check if GPU (CUDA) is available for llama.cpp."""
    if llm_engine:
        return {"gpu_available": llm_engine._gpu_available()}
    return {"gpu_available": False}

@app.get("/api/system/stats")
async def get_system_stats():
    """Get system resource usage statistics (CPU, RAM, GPU, GPU Temp)."""
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

@app.get("/api/orchestrator/asset-studio")
async def get_asset_studio():
    """Get full asset studio state (bibles, assets, approvals, locks)."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.get_asset_studio_state()

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
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_shot_from_scene(data)
    return result

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

@app.get("/api/image/progress")
async def get_image_progress():
    """Get current image generation progress."""
    if not image_engine:
        return {"pct": 0, "status": "idle", "label": ""}
    return image_engine.get_gen_progress()

@app.post("/api/image/interrupt")
async def interrupt_image_generation():
    """Interrupt the current ComfyUI generation/queue."""
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

    return image_engine.generate_video(
        provider_id=data.get("provider", "comfyui"),
        model=data.get("model", ""),
        prompt=data.get("prompt", ""),
        host=data.get("host"),
        api_key=data.get("api_key"),
        input_image=data.get("input_image"),
        workflow_name=data.get("workflow_name")
    )

# === ComfyUI Subprocess Management ===

@app.get("/api/comfyui/status")
async def get_comfyui_status():
    """Check if ComfyUI is running."""
    return comfyui_client.get_comfyui_status()

@app.get("/api/comfyui/categories")
async def get_comfyui_categories():
    """Get ComfyUI workflow categories."""
    return {"success": True, "categories": comfyui_client.get_categories()}

@app.post("/api/comfyui/test")
async def test_comfyui():
    """Test ComfyUI connection."""
    return comfyui_client.test_connection()

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

@app.post("/api/comfyui/generate/t2i")
async def generate_t2i_endpoint(request: Request):
    """Generate image using T2I workflow from settings."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    settings = load_settings()
    workflow_name = data.get("workflow_name") or settings.get("workflows", {}).get("t2i", "")
    if not workflow_name:
        return {"success": False, "error": "No T2I workflow assigned in Settings"}
    logger.info("POST /api/comfyui/generate/t2i — workflow=%s, seed=%s", workflow_name, seed)
    resolution = data.get("resolution")
    return comfyui_client.generate_with_workflow(prompt, workflow_name, seed=seed, resolution=resolution)

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
    workflow_name = settings.get("workflows", {}).get("i2v", "")
    if not workflow_name:
        return {"success": False, "error": "No I2V workflow assigned in Settings"}
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
    return comfyui_client.generate_with_workflow(prompt, workflow_name, seed=seed, steps=steps, input_images=[abs_image] if abs_image else None)

@app.post("/api/projects/save-image")
async def save_project_image(request: Request):
    """Save a generated image to project folder."""
    data = await request.json()
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
    import requests
    import time
    resp = None
    last_error = ""
    for attempt in range(5):
        try:
            url = f"{comfyui_client.host}/view?filename={filename}"
            if subfolder:
                url += f"&subfolder={subfolder}"
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
    if not state.get("topicIdeas") and not state.get("screenplayData"):
        existing = project_manager.load_project_state(name, project_path)
        if existing and (existing.get("topicIdeas") or existing.get("screenplayData")):
            logger.warning("save_project_state ignored: prevented browser page overwrite of populated project data.")
            return {"success": True, "protected": True}

    # Sync orchestrator memory into state for persistence
    if orchestrator:
        if state:
            orchestrator.memory.project_info["genres"] = state.get("selectedGenres", [])
            orchestrator.memory.project_info["visual_style"] = state.get("selectedVisualStyle", "")
            orchestrator.memory.project_info["film_aesthetic"] = state.get("selectedFilmAesthetic", "")
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
        return {"success": False, "state": None}

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
            import shutil
            # Delete everything inside target_dir but keep target_dir itself
            for item in target_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

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
        workflow_name = settings.get("workflows", {}).get("i2v", "video_ltx2_3_i2v_v2.json")
        
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
async def view_comfyui_image(filename: str, subfolder: str = "", thumbnail: bool = False):
    """View an image or video from ComfyUI output."""
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

        response = requests.get(f"{host}/view", params=params, timeout=60)
        ctype = response.headers.get("content-type", "video/mp4") if is_video else response.headers.get("content-type", "image/png")
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
        data["llm"] = existing.get("llm", {"provider": "app_llm", "model": "gemma-4-E2B-it-Q4_K_M.gguf", "host": "http://localhost:8081", "apiKey": "", "auto_start": True})
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
        if f.suffix == ".json":
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
    port = int(os.environ.get("PORT", 7860))
    logger.info("Starting Ultimate AI Film Studio on http://127.0.0.1:%s", port)
    uvicorn.run(app, host="127.0.0.1", port=port)