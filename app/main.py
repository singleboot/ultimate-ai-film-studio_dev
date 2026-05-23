import os
import json
import shutil
import platform
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

try:
    from core.template_manager import TemplateManager
    from core.llm_engine import LLMEngine
    from core.project_manager import ProjectManager
    from core.approval_workflow import ApprovalWorkflow
    from core.image_engine import ImageEngine
    from core.orchestrator import CinematicOrchestrator, load_master_system_prompt
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

app = FastAPI(title="Ultimate AI Film Studio")

# Cleanup subprocesses on shutdown
@app.on_event("shutdown")
async def shutdown_event():
    if llm_engine:
        llm_engine.cleanup_subprocesses()

# Global exception handler - always return JSON
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
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
            print(f"Migrated {_name} to {_dir_var}")
        except Exception as e:
            print(f"Could not migrate {_name}: {e}")
            # Copy instead of move if cross-drive move failed
            if _old.is_file() and not _dir_var.exists():
                shutil.copy2(str(_old), str(_dir_var))
                print(f"Copied {_name} to {_dir_var}")
            elif _old.is_dir() and not _dir_var.exists():
                shutil.copytree(str(_old), str(_dir_var))
                print(f"Copied directory {_name} to {_dir_var}")

GENRES_DIR.mkdir(exist_ok=True)
VISUAL_STYLES_DIR.mkdir(exist_ok=True)
FILM_AESTHETICS_DIR.mkdir(exist_ok=True)

llm_engine = LLMEngine(settings_path=str(SETTINGS_FILE)) if HAS_LLM else None
project_manager = ProjectManager() if HAS_LLM else None
approval_workflow = ApprovalWorkflow() if HAS_LLM else None

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
                return HTMLResponse(content=f.read())
    except Exception as e:
        print(f"Error serving HTML: {e}")
    return HTMLResponse(content=get_default_html())

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

    result = llm_engine.generate(
        provider_id=request.provider or request.app_provider or "ollama",
        model=request.model or "llama3.1",
        prompt=request.prompt or "",
        system_prompt=system_prompt or request.system_prompt or "",
    )
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

@app.post("/api/comfyui/external-path")
async def set_comfyui_external_path(data: dict):
    """Set external ComfyUI models folder path."""
    if not comfyui_client:
        return {"success": False, "error": "ComfyUI client not available"}
    path = data.get("path", "")
    if not path:
        return comfyui_client.clear_external_models_path()
    return comfyui_client.set_external_models_path(path)

@app.get("/api/comfyui/external-path")
async def get_comfyui_external_path():
    """Get current external ComfyUI models path."""
    if not comfyui_client:
        return {"path": None}
    path = comfyui_client.get_external_models_path()
    return {"path": path}

@app.post("/api/comfyui/install")
async def install_comfyui(data: dict = None):
    """Install ComfyUI via git clone."""
    if not comfyui_client:
        return {"success": False, "error": "ComfyUI client not available"}
    target = data.get("target") if data else None
    return comfyui_client.install_comfyui(target_dir=target)

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
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_ideas(data)
    return result

@app.post("/api/orchestrator/screenplay")
def generate_screenplay(data: dict):
    """Stage 2: Generate master screenplay from selected idea."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_screenplay(data)
    return result

@app.post("/api/orchestrator/regenerate-idea")
def regenerate_idea(data: dict):
    """Regenerate a single idea."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.regenerate_single_idea(data)
    return result

@app.post("/api/orchestrator/idea-variants")
def generate_idea_variants(data: dict):
    """Generate 3 variants of an idea based on user change request."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_idea_variants(data)
    return result

@app.post("/api/orchestrator/locations")
def generate_locations(data: dict = None):
    """Stage 3: Generate reusable location assets."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_locations(data or {})
    return result

@app.post("/api/orchestrator/characters")
def generate_characters(data: dict = None):
    """Stage 4: Generate reusable character assets."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_characters(data or {})
    return result

@app.post("/api/orchestrator/storyboard")
def generate_storyboard(data: dict = None):
    """Stage 5: Generate storyboard with shots."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_storyboard(data or {})
    return result

@app.post("/api/orchestrator/video-prompts")
def generate_video_prompts(data: dict = None):
    """Stage 6: Generate LTX 2.3 video prompts."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.generate_video_prompts(data or {})
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
async def approve_characters(data: dict = None):
    """Approve characters, enabling storyboard generation."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.approve_characters(data or {})
    return result

@app.post("/api/orchestrator/approve-locations")
async def approve_locations(data: dict = None):
    """Approve locations, enabling storyboard generation."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    result = orchestrator.approve_locations(data or {})
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
        entry["generation_history"] = entry.get("generation_history", []) + [sheet_image]
    if approved:
        entry["approved"] = True
    sheets[character_id] = entry
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
        entry["generation_history"] = entry.get("generation_history", []) + [sheet_image]
    if approved:
        entry["approved"] = True
    sheets[location_id] = entry
    return {"success": True, "location_sheets": sheets}

@app.post("/api/orchestrator/sync-bibles")
def sync_bibles(data: dict):
    """Sync frontend character/location bible data into orchestrator project_graph."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.sync_bibles_from_frontend(
        character_bible=data.get("character_bible"),
        location_bible=data.get("location_bible"),
    )

@app.post("/api/orchestrator/generate-asset-prompt")
def generate_asset_prompt(data: dict):
    """Generate image_prompt for a character or location using LLM."""
    if not orchestrator:
        return {"success": False, "error": "Orchestrator not available"}
    return orchestrator.generate_asset_prompt(
        asset_type=data.get("type", ""),
        asset=data.get("asset", {}),
    )

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
        seed=data.get("seed")
    )

@app.get("/api/image/progress")
async def get_image_progress():
    """Get current image generation progress."""
    if not image_engine:
        return {"pct": 0, "status": "idle", "label": ""}
    return image_engine.get_gen_progress()

@app.post("/api/video/generate")
def generate_video_endpoint(data: dict):
    """Generate a video using the selected provider."""
    if not image_engine:
        return {"success": False, "error": "Image engine not available"}
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

@app.post("/api/comfyui/detect")
async def detect_comfyui():
    """Detect local ComfyUI installation."""
    return comfyui_client.detect_comfyui()

@app.get("/api/comfyui/status")
async def get_comfyui_status():
    """Check if ComfyUI is running."""
    return comfyui_client.get_comfyui_status()

@app.post("/api/comfyui/start")
async def start_comfyui(data: dict = None):
    """Launch ComfyUI as a subprocess."""
    path = data.get("path") if data else None
    return comfyui_client.launch_comfyui(path=path)

@app.post("/api/comfyui/stop")
async def stop_comfyui():
    """Stop ComfyUI subprocess."""
    return comfyui_client.stop_comfyui()

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
    workflow_name = settings.get("workflows", {}).get("t2i", "")
    if not workflow_name:
        return {"success": False, "error": "No T2I workflow assigned in Settings"}
    return comfyui_client.generate_with_workflow(prompt, workflow_name, seed=seed)

@app.post("/api/comfyui/generate/i2i")
async def generate_i2i_endpoint(request: Request):
    """Generate image using I2I workflow from settings with reference images."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    project_path = data.get("project_path", "")
    input_images = data.get("input_images", [])
    aspect_ratio = data.get("aspect_ratio")
    resolution = data.get("resolution")
    if not prompt:
        return {"success": False, "error": "No prompt provided"}
    settings = load_settings()
    workflow_name = settings.get("workflows", {}).get("i2i", "")
    if not workflow_name:
        return {"success": False, "error": "No I2I workflow assigned in Settings"}
    abs_paths = []
    for rel in input_images:
        p = Path(project_path) / rel
        if p.exists():
            abs_paths.append(str(p))
        else:
            print(f"[I2I] Image NOT FOUND: {p}")
    if abs_paths:
        print(f"[I2I] Resolved {len(abs_paths)}/{len(input_images)} images: {abs_paths}")
    else:
        print(f"[I2I] No input images resolved (requested {len(input_images)})")
    return comfyui_client.generate_with_workflow(prompt, workflow_name, seed=seed, input_images=abs_paths if abs_paths else None, aspect_ratio=aspect_ratio, resolution=resolution)

@app.post("/api/comfyui/generate/i2v")
async def generate_i2v_endpoint(request: Request):
    """Generate video using I2V workflow from settings with scene image input."""
    data = await request.json()
    prompt = data.get("prompt", "")
    seed = data.get("seed")
    project_path = data.get("project_path", "")
    input_image = data.get("input_image")
    scene_index = data.get("scene_index")
    print(f"\n[I2V] ===== REQUEST =====")
    print(f"[I2V] prompt='{prompt[:80]}...' seed={seed}")
    print(f"[I2V] project_path='{project_path}'")
    print(f"[I2V] input_image='{input_image}'")
    print(f"[I2V] scene_index={scene_index}")
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
            print(f"[I2V] Resolved input image: {abs_image}")
        else:
            stem = p.stem
            parent = p.parent
            print(f"[I2V] Input image NOT at {p}, trying alt extensions...")
            for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                alt = parent / f"{stem}{ext}"
                if alt.exists():
                    abs_image = str(alt)
                    print(f"[I2V] Found image with alt extension: {abs_image}")
                    break
            if not abs_image:
                print(f"[I2V] No alt extension found for {p}")
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
                    print(f"[I2V] Auto-discovered scene image: {abs_image}")
                    break
            if not abs_image:
                # Try any scene image
                scene_files = [c for c in candidates if c.stem.startswith("scene_")]
                if scene_files:
                    abs_image = str(scene_files[0])
                    print(f"[I2V] Auto-discovered first scene image: {abs_image}")
    if not abs_image:
        return {"success": False, "error": "Scene image not found. Make sure you approved the storyboard image first."}
    return comfyui_client.generate_with_workflow(prompt, workflow_name, seed=seed, input_images=[abs_image] if abs_image else None)

@app.post("/api/projects/save-image")
async def save_project_image(request: Request):
    """Save a generated image to project folder."""
    data = await request.json()
    stage = data.get("stage", "")
    filename = data.get("filename", "")
    project_path = data.get("project_path", "")
    card_name = data.get("card_name", "")
    project_name = data.get("project_name", "")
    previous_file = data.get("previous_file")
    import requests
    try:
        resp = requests.get(f"{comfyui_client.host}/view?filename={filename}", timeout=60)
        if resp.status_code != 200:
            return {"success": False, "error": "Failed to download from ComfyUI"}
    except Exception as e:
        return {"success": False, "error": f"Download error: {str(e)}"}
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
            prev_path = project_manager.projects_dir / project_name / stage / previous_file
            if prev_path.exists():
                prev_path.unlink()
    # Save to project_path first (ensures I2I can find it), fall back to project_manager
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
            else:
                key = "location_assets"
            if asset_id not in pg.get(key, {}):
                pg.setdefault(key, {})[asset_id] = {}
            pg[key][asset_id]["approved_image"] = f"{folder}/{save_name}"
            pg[key][asset_id]["approved"] = True
        return {"success": True, "path": f"{folder}/{save_name}"}
    elif project_name:
        project_manager.load_project(project_name)
        stage_dir = project_manager.projects_dir / project_name / folder
        stage_dir.mkdir(parents=True, exist_ok=True)
        dest = stage_dir / save_name
        dest.write_bytes(content)
        return {"success": True, "path": f"{folder}/{save_name}"}
    return {"success": False, "error": "No project specified"}

@app.post("/api/projects/save-video")
async def save_project_video(request: Request):
    """Save a generated video to project folder by downloading from ComfyUI."""
    data = await request.json()
    filename = data.get("filename", "")
    subfolder = data.get("subfolder", "")
    project_path = data.get("project_path", "")
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
            return {"exists": True, "name": data.get("name", "Unknown"), "info": f"Created: {data.get('created_at', 'Unknown')}"}
        # Also check if path contains name/project.json
        for sub in project_path.iterdir():
            if sub.is_dir():
                pf = sub / "project.json"
                if pf.exists():
                    with open(pf, 'r') as f:
                        data = json.load(f)
                    return {"exists": True, "name": data.get("name", sub.name), "info": f"Path: {sub}"}
    except Exception:
        pass
    return {"exists": False}

@app.post("/api/projects")
async def create_project(request: ProjectCreateRequest):
    """Create a new project."""
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

    # Sync orchestrator memory into state for persistence
    if orchestrator:
        state["_orchestrator_memory"] = orchestrator.to_dict()

    result = project_manager.save_project_state(name, state, project_path)
    return result

@app.get("/api/projects/{name}/state")
async def load_project_state(name: str, path: str = None):
    """Load full project state from disk, restoring orchestrator continuity memory."""
    result = project_manager.load_project_state(name, path)
    if result is None:
        return {"success": False, "state": None}

    # Restore orchestrator continuity memory from saved state
    if orchestrator and result.get("_orchestrator_memory"):
        orchestrator.from_dict(result["_orchestrator_memory"])

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
    stage = data.get("stage", "")
    filename = data.get("filename", "")
    if not filename:
        return {"success": False, "error": "No filename provided"}
    try:
        if project_path:
            target = Path(project_path) / stage / filename
        else:
            project_manager.load_project(name)
            target = project_manager.projects_dir / name / stage / filename
        if target.exists():
            target.unlink()
            return {"success": True}
        return {"success": False, "error": "File not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/projects/{name}/saved-image/{stage}/{filename:path}")
async def get_saved_project_image(name: str, stage: str, filename: str):
    """Serve a saved project image."""
    try:
        project_manager.load_project(name)
        img_path = project_manager.projects_dir / name / stage / filename
        if not img_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "Image not found"})
        return FileResponse(str(img_path), media_type="image/png")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

@app.get("/api/projects/{name}/saved-video/{filename:path}")
async def get_saved_project_video(name: str, filename: str):
    """Serve a saved project video."""
    try:
        project_manager.load_project(name)
        vid_path = project_manager.projects_dir / name / "videos" / filename
        if not vid_path.exists():
            return JSONResponse(status_code=404, content={"success": False, "error": "Video not found"})
        return FileResponse(str(vid_path), media_type="video/mp4")
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})

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

@app.get("/api/comfyui/view")
async def view_comfyui_image(filename: str, subfolder: str = ""):
    """View an image or video from ComfyUI output."""
    try:
        import requests
        host = comfyui_client.host
        params = {"filename": filename}
        if subfolder:
            params["subfolder"] = subfolder
            params["type"] = "output"
        response = requests.get(f"{host}/view", params=params, timeout=60)
        from fastapi.responses import Response
        ctype = response.headers.get("content-type", "video/mp4") if filename.endswith(('.mp4','.webm','.gif')) else response.headers.get("content-type", "image/png")
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
    existing = load_settings()
    for key in existing:
        if key not in data:
            data[key] = existing[key]
    # Ensure image_gen section exists
    if "image_gen" not in data:
        data["image_gen"] = existing.get("image_gen", {"provider": "comfyui", "model": "", "host": "http://localhost:8188", "apiKey": ""})
    # Ensure llm auto_start flag
    if "llm" not in data:
        data["llm"] = existing.get("llm", {"provider": "ollama", "model": "", "host": "http://localhost:11434", "apiKey": "", "auto_start": False})
    save_settings(data)
    if data.get("comfyui", {}).get("url"):
        comfyui_client.set_host(data["comfyui"]["url"])
    if data.get("comfyui", {}).get("path"):
        comfyui_client.set_comfyui_path(data["comfyui"]["path"])
    # Auto-start ComfyUI if enabled
    if data.get("comfyui", {}).get("auto_start", False):
        try:
            comfyui_client.launch_comfyui()
        except Exception:
            pass
    # Auto-start local LLM if enabled
    if data.get("llm", {}).get("auto_start", False) and llm_engine:
        try:
            llm_engine.launch_local_llm(data["llm"]["provider"])
        except Exception:
            pass
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
        img_path = VISUAL_STYLES_DIR / s["image"]
        s["has_image"] = img_path.exists()
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
        img_path = FILM_AESTHETICS_DIR / a["image"]
        a["has_image"] = img_path.exists()
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
    print(f"Starting Ultimate AI Film Studio on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)