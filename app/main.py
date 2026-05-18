import os
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Dict, List, Optional, Any

try:
    from core.template_manager import TemplateManager
    from core.llm_engine import LLMEngine
    from core.project_manager import ProjectManager
    from core.approval_workflow import ApprovalWorkflow
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

app = FastAPI(title="Ultimate AI Film Studio")

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
llm_engine = LLMEngine() if HAS_LLM else None
project_manager = ProjectManager() if HAS_LLM else None
approval_workflow = ApprovalWorkflow() if HAS_LLM else None

# ComfyUI client - ALWAYS use placeholder to avoid auto-connect issues
# User can manually connect ComfyUI when they want to use it
from core.comfyui_client import ComfyUIClient
comfyui_client = ComfyUIClient()

class GenerateRequest(BaseModel):
    provider: str
    model: str
    prompt: str
    template_name: Optional[str] = None
    stage_name: Optional[str] = None
    variables: Optional[Dict] = None

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
async def generate(request: GenerateRequest):
    """Generate content using LLM."""
    system_prompt = None
    if request.template_name and request.stage_name and request.variables:
        try:
            prompt = template_manager.render_prompt(
                request.template_name,
                request.stage_name,
                request.variables
            )
        except Exception as e:
            prompt = request.prompt
    else:
        prompt = request.prompt

    result = llm_engine.generate(
        provider_id=request.provider,
        model=request.model,
        prompt=prompt,
        system_prompt=system_prompt
    )

    return result

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
async def generate_image(
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

@app.get("/api/projects/{name}/files/{stage}")
async def get_stage_files(name: str, stage: str):
    """Get files for a stage."""
    project_manager.load_project(name)
    files = project_manager.get_stage_files(stage)
    return {"success": True, "files": files}

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
async def view_comfyui_image(filename: str):
    """View an image from ComfyUI output."""
    try:
        import requests
        response = requests.get(f"http://localhost:8188/view?filename={filename}")
        from fastapi.responses import Response
        return Response(content=response.content, media_type="image/png")
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
async def get_llm_models(provider: str):
    """Get available models for the provider."""
    models = []
    if provider == "ollama":
        try:
            import requests
            resp = requests.get("http://localhost:11434/api/tags", timeout=15)
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
        except:
            pass
    elif provider == "lm_studio":
        try:
            import requests
            resp = requests.get("http://localhost:1234/v1/models", timeout=15)
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
        except:
            pass
    return {"models": models}

@app.post("/api/llm/test")
async def test_llm_connection(data: dict):
    """Test LLM connection."""
    provider = data.get("provider", "")
    host = data.get("host", "http://localhost:11434")
    model = data.get("model", "")
    
    try:
        import requests
        if provider == "ollama":
            resp = requests.post(f"{host}/api/generate", 
                json={"model": model, "prompt": "Hello", "stream": False}, timeout=30)
            if resp.status_code == 200:
                return {"success": True}
        elif provider == "lm_studio":
            resp = requests.post(f"{host}/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "Hi"}]}, timeout=30)
            if resp.status_code == 200:
                return {"success": True}
        return {"success": False, "message": "Connection failed"}
    except Exception as e:
        return {"success": False, "message": str(e)}

# Settings storage
SETTINGS_FILE = Path(__file__).parent / "settings.json"

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text())
    return {}

def save_settings(settings: dict):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

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
    """Save settings."""
    save_settings(data)
    return {"success": True}
    return {"success": True}


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