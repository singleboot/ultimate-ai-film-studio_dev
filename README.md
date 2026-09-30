# Ultimate AI Film Studio

A prompt-to-media pipeline with human-in-the-loop approval workflow - generate characters, locations, scenes, and videos through LLM-powered prompts and ComfyUI integration.

## Features

- **Template System**: Use built-in templates (including Short Film Automation v1) or create custom prompt templates
- **Multi-Provider LLM Support**: Connect to Ollama, LM Studio, OpenRouter, Gemini, and more
- **ComfyUI Integration**: Generate images and videos through ComfyUI workflows
- **Approval Workflow**: Review and approve/reject generated content before saving to project
- **Project Management**: Organize generated content in structured project folders

## Requirements

- Python 3.10+
- ComfyUI (local or remote)
- LLM runtime (Ollama, LM Studio, or API keys for cloud providers)

## Installation

1. Create a virtual environment: `python -m venv env`
2. Activate it and install dependencies: `pip install -r requirements.txt`

## Usage

1. Click "Start" to launch the web interface
2. Select a template or create your own
3. Configure LLM and ComfyUI settings
4. Generate prompts and media
5. Review and approve generated content

## Configuration

### LLM Providers

Configure in `app/config/llm_providers.json`:
- Ollama (local)
- LM Studio (local)
- llama.cpp (local)
- OpenRouter (cloud)
- Gemini (cloud)

### ComfyUI

Configure in `app/config/comfyui_workflows.json`:
- Image Generation: ZImage Turbo, HiDream, FLUX, etc.
- Image Edit: Flux2 Klein, Qwen Image Edit, etc.
- Video Generation: LTX 2.3, Wan 2.1, etc.

## Project Structure

```
ultimate-ai-film-studio/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── core/                # Core modules
│   │   ├── template_manager.py
│   │   ├── llm_engine.py
│   │   ├── comfyui_client.py
│   │   ├── project_manager.py
│   │   └── approval_workflow.py
│   ├── ui/                  # Web UI
│   │   ├── templates/
│   │   └── static/
│   └── config/              # Configuration files
│       ├── templates/       # Built-in templates
│       ├── llm_providers.json
│       └── comfyui_workflows.json
└── README.md
```

## API Endpoints

- `GET /api/templates` - List all templates
- `GET /api/providers` - List LLM providers
- `POST /api/generate` - Generate content with LLM
- `GET /api/comfyui/categories` - Get ComfyUI workflows
- `POST /api/comfyui/generate/image` - Generate image
- `GET /api/projects` - List projects
- `POST /api/projects` - Create new project
- `POST /api/approve` - Approve/reject generated content

## MCP Servers (agent control)

The studio can be driven entirely by an MCP Agent — chat with an agent and it
produces a whole film. Two stdio MCP servers ship in `app/tools/`:

- **`studio_mcp_server.py`** — the film pipeline: create project, ideas,
  screenplay, characters/locations, approvals, character sheets, storyboard,
  shot images, shot videos, audio, timeline bake and FCP7 XML export (27 tools).
  Requires the studio backend on port 7860; run it with a Python that has the
  `mcp` package (e.g. WanGP's venv). See [app/tools/STUDIO_MCP.md](app/tools/STUDIO_MCP.md).
- **`wangp_mcp_server.py`** — raw WanGP image/video generation against the
  bridge on port 8189.

Example client registration (Claude Desktop):

```json
"studio": {
  "command": "D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe",
  "args": ["F:/MY APP/ULTIMATE AI STUDIO/ultimate-ai-film-studio-V11/app/tools/studio_mcp_server.py"],
  "env": { "STUDIO_URL": "http://127.0.0.1:7860" }
}
```

## License

MIT