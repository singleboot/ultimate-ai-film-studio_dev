# Studio MCP Server — let an Agent produce an entire film

`studio_mcp_server.py` is a stdio MCP server that wraps the studio's own HTTP
API. Any MCP-capable agent (Codebuff, Claude Desktop, LM Studio, …) gets 27
tools covering the full production line:

```
film_create_project → film_generate_ideas → film_generate_screenplay
→ film_generate_locations → film_generate_characters → film_approve_all_assets
→ film_generate_character_sheet (per character) → film_generate_storyboard
→ film_generate_shot_image (per shot) → film_generate_shot_video (per shot)
→ film_generate_audio → film_bake_timeline → film_export_xml
```

## Requirements

- Studio backend running: `python app/main.py` (port 7860)
- A Python interpreter with the `mcp` package for the server process
  (the studio venv has none; WanGP's venv does)
- Local LLM running for idea/screenplay/sheet stages; ComfyUI and/or the
  WanGP bridge for GPU stages

## Register with your MCP client

`claude_desktop_config.json` (or the equivalent in your client):

```json
{
  "mcpServers": {
    "studio": {
      "command": "D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe",
      "args": ["F:/MY APP/ULTIMATE AI STUDIO/ultimate-ai-film-studio-V11/app/tools/studio_mcp_server.py"],
      "env": { "STUDIO_URL": "http://127.0.0.1:7860" }
    }
  }
}
```

The WanGP MCP (`wangp_mcp_server.py`) stays separate: `studio` drives the
film pipeline, `wangp` drives raw WanGP jobs.

## Tool groups

| Group | Tools |
|---|---|
| Status/meta | `studio_health`, `film_bible`, `film_list_projects` |
| Production | `film_create_project`, `film_generate_ideas`, `film_generate_screenplay`, `film_generate_locations`, `film_generate_characters` |
| Asset studio | `film_asset_studio`, `film_generate_asset_image`, `film_save_asset_image`, `film_approve_asset`, `film_approve_all_assets`, `film_describe_character`, `film_generate_character_sheet`, `film_save_character_sheet` |
| Storyboard | `film_generate_storyboard`, `film_generate_video_prompts`, `film_shots`, `film_generate_shot_image`, `film_generate_shot_video`, `film_approve_shot` |
| Finishing | `film_generate_audio`, `film_bake_timeline`, `film_export_xml` |
| WanGP direct | `film_wangp_generate_image`, `film_wangp_generate_video` |

## How it addresses projects

Every tool accepts optional `project_path` / `project_name`. When given, the
server calls `GET /api/projects/{name}/state?path=...` on the backend, which
makes that project the *active* one and restores its orchestrator continuity
memory — exactly like opening it in the web UI. No active project anywhere?
Call `film_create_project` first, or tell the agent the project name/path.

## Tips for agents

- Call `studio_health` first; `film_bible` summarizes where the film stands.
- `film_generate_ideas` → pick one → `film_generate_screenplay` with its index.
- GPU tools block until the render finishes (they poll
  `/api/image/progress` or the WanGP job); a full shot can take minutes.
- `film_bake_timeline` wants `{"file": "SHOT_0101.mp4", "duration": 5}`
  entries matching files in the project's `videos/` folder.
