# -*- coding: utf-8 -*-
"""Studio MCP Server — lets any MCP Agent produce an entire film.

Exposes the Ultimate AI Film Studio's full production pipeline as MCP tools.
Runs on stdio so any MCP client (Codebuff, Claude Desktop, LM Studio, ...)
can drive it: create a project, generate ideas -> screenplay -> characters/
locations -> approve -> generate character sheets -> storyboard -> shot images
-> i2v videos -> audio -> bake an export. Every tool talks HTTP to the studio
backend at http://127.0.0.1:7860, so exactly one process (the studio server)
owns the orchestrator memory, the projects on disk and the GPU.

Requires: the studio backend running (python app/main.py), a local LLM
(idea/screenplay stages) and ComfyUI/WanGP for the GPU stages. Start the
server with a Python that has the `mcp` package, e.g. WanGP's venv:

    D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe \
        "F:/MY APP/ULTIMATE AI STUDIO/ultimate-ai-film-studio-V11/app/tools/studio_mcp_server.py"

Register with an MCP client (claude_desktop_config.json example):

    "studio": {
      "command": "D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe",
      "args": ["F:/MY APP/ULTIMATE AI STUDIO/ultimate-ai-film-studio-V11/app/tools/studio_mcp_server.py"],
      "env": { "STUDIO_URL": "http://127.0.0.1:7860" }
    }

Recommended agent flow (each stage needs the previous one):

    film_create_project -> film_generate_ideas -> film_generate_screenplay
    -> film_generate_locations -> film_generate_characters
    -> film_approve_all_assets -> [film_generate_character_sheet ...]
    -> film_generate_storyboard -> film_generate_shot_image (per shot)
    -> film_generate_shot_video (per shot) -> film_bake_timeline
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

STUDIO = os.environ.get("STUDIO_URL", "http://127.0.0.1:7860")
DEFAULT_WANGP_IMAGE_MODEL = "qwen_image_21_7B"
DEFAULT_WANGP_VIDEO_MODEL = "ltx2_22B_distilled"
# Same defaults the in-app agent bridge uses when settings.workflows is unset.
DEFAULT_T2I_WORKFLOW = "image_krea2_turbo_t2i_v2"
DEFAULT_I2V_WORKFLOW = "video_ltx2_5_i2v"

mcp = FastMCP("studio")


# ----------------------------------------------------------------- plumbing --
def _call(method: str, path: str, payload=None, timeout: float = 60):
    """HTTP JSON call against the studio backend. Returns the parsed dict."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        STUDIO.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {}
        err = body.get("error") or body.get("detail") or f"HTTP {e.code}"
        return {"success": False, "error": f"{err} ({e.code})"}
    except Exception as e:
        return {"success": False, "error": f"studio unreachable at {STUDIO}: {e}"}


def _out(res, keep=None):
    """Trim a studio response to a compact JSON string for the agent context."""
    if keep:
        if isinstance(res, dict):
            res = {k: res.get(k) for k in keep}
        elif isinstance(res, list):
            res = [{k: r.get(k) for k in keep if isinstance(r, dict)} for r in res]
    return json.dumps(res, ensure_ascii=False, default=str)


def _ping(path: str = "/", timeout: float = 15) -> bool:
    """True when the studio answers that path with HTTP 200 (any body)."""
    try:
        with urllib.request.urlopen(STUDIO.rstrip("/") + path, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _poll_image(label: str, max_seconds: int = 1800) -> dict:
    """Poll /api/image/progress until a ComfyUI image job leaves 'running'."""
    deadline = time.time() + max_seconds
    last = {}
    while time.time() < deadline:
        last = _call("GET", "/api/image/progress", timeout=15)
        st = str(last.get("status", ""))
        if st not in ("running", "starting", "pending"):
            break
        time.sleep(3)
    last["waited_for"] = label
    return last


def _wangp_wait(job_id: str, max_seconds: int = 3600) -> dict:
    """Poll a WanGP job via the studio proxy until done/error."""
    deadline = time.time() + max_seconds
    last = {}
    while time.time() < deadline:
        last = _call("GET", f"/api/wangp/job/{job_id}", timeout=15)
        if last.get("status") in ("done", "error"):
            break
        time.sleep(5)
    return last


def _wangp_ingest(job_id: str) -> dict:
    """Pull a finished WanGP output into ComfyUI's output folder so the
    normal save/ingest flows work on it."""
    return _call("POST", f"/api/wangp/job/{job_id}/ingest", {}, timeout=120)


def _persist_memory(project_name: str, project_path: str = "") -> bool:
    """Persist the live orchestrator memory into project_state.json.

    The ideas/screenplay/locations/characters stage endpoints mutate the
    in-memory orchestrator but don't auto-save (only storyboard/video-prompts
    do), so agent-driven stages would be lost on a backend restart without
    this. The current disk state is read straight from the file — NOT via
    GET /state, whose missing-file branch resets the orchestrator and would
    wipe the very memory we're saving. The backend merges live memory into
    the posted state, so browser fields are preserved.
    """
    if not project_name:
        return False
    state = {}
    if project_path:
        try:
            with open(os.path.join(project_path, "project_state.json"), encoding="utf-8") as fh:
                state = json.load(fh)
        except Exception:
            state = {}
    q = urllib.parse.quote(project_name)
    res = _call("POST", f"/api/projects/{q}/state",
                {"state": state, "project_path": project_path}, timeout=60)
    return bool(res.get("success"))


def _gen_wait_ingest(endpoint: str, payload: dict, media: str,
                     max_seconds: int) -> dict:
    """Shared WanGP generate -> poll -> ingest -> save-into-project routine."""
    sub = _call("POST", endpoint, payload, timeout=90)
    job_id = sub.get("job_id")
    if not job_id:
        return {"success": False, "error": sub.get("error", "no job_id from WanGP bridge")}
    job = _wangp_wait(job_id, max_seconds=max_seconds)
    if job.get("status") != "done":
        return {"success": False, "job_id": job_id, "error": job.get("error", f"job status: {job.get('status')}")}
    ing = _wangp_ingest(job_id)
    return {"success": bool(ing.get("success")), "job_id": job_id, **{k: ing.get(k) for k in ("filename", "subfolder", "path", "error")}}


_PROJECT_CACHE: dict[str, str] = {}  # name -> absolute path (resolved this session)


def _resolve_project(name: str) -> str:
    """Find the absolute folder for a project name (cached).

    Tries GET /api/projects/{name} first (default projects dir), then falls
    back to the persistent registry list — external projects registered from
    arbitrary folders only appear there.
    """
    if name in _PROJECT_CACHE:
        return _PROJECT_CACHE[name]
    p = _call("GET", f"/api/projects/{urllib.parse.quote(name)}", timeout=15)
    path = ((p.get("project") or {}).get("path")) or ""
    if not path:
        listing = _call("GET", "/api/projects", timeout=30)
        entries = listing.get("projects", listing) if isinstance(listing, dict) else listing
        for entry in (entries or []):
            if isinstance(entry, dict) and entry.get("name", "").lower() == name.lower():
                path = entry.get("path", "")
                break
    if not path:
        # Not in the registry yet (e.g. just created in the default projects
        # dir): discover registers those with absolute paths, then re-list.
        _call("GET", "/api/projects/discover", timeout=60)
        listing = _call("GET", "/api/projects", timeout=30)
        entries = listing.get("projects", listing) if isinstance(listing, dict) else listing
        for entry in (entries or []):
            if isinstance(entry, dict) and entry.get("name", "").lower() == name.lower():
                path = entry.get("path", "")
                break
    if path:
        _PROJECT_CACHE[name] = path
    return path


def _use_project(project_path: str = "", project_name: str = "") -> tuple[str, str]:
    """Resolve (name, path) and make it the backend's current project.

    GET /api/projects/{name}/state?path=... is the server-side switch: it
    restores the orchestrator continuity memory from that folder (a fresh
    project without a state file yet is fine — the switch still happens).
    """
    if project_path and not project_name:
        for n, pp in _PROJECT_CACHE.items():
            if pp == project_path:
                project_name = n
                break
        if not project_name:
            project_name = os.path.basename(project_path.rstrip("/\\"))
    if not project_name:
        return "", ""
    path = project_path or _resolve_project(project_name)
    if path:
        _PROJECT_CACHE[project_name] = path
        # Side effect on the backend: current project + orchestrator memory.
        import urllib.request as _u
        try:
            with _u.urlopen(f"{STUDIO.rstrip('/')}/api/projects/{urllib.parse.quote(project_name)}/state?path={urllib.parse.quote(path)}", timeout=30) as r:
                r.read()
        except Exception:
            pass
    return project_name, path


def _project_path(project_path: str = "", project_name: str = "") -> str:
    """Resolve the absolute project folder and select it server-side."""
    if project_path:
        return _use_project(project_path=project_path, project_name=project_name)[1]
    name, path = _use_project(project_name=project_name)
    return path


# ------------------------------------------------------------- meta / status --
@mcp.tool()
def studio_health() -> str:
    """Check the studio backend, ComfyUI, WanGP bridge and LLM in one call.
    Call this first: every other tool requires the backend at STUDIO_URL."""
    comfy = _call("GET", "/api/comfyui/status", timeout=15)
    wangp = _call("GET", "/api/wangp/health", timeout=15)
    llm = _call("GET", "/api/llm/status?provider=llama_cpp", timeout=15)
    return _out({
        "studio": {"ok": _ping("/"), "url": STUDIO},
        "comfyui": {"running": comfy.get("running", comfy.get("connected"))},
        "wangp_bridge": {"status": wangp.get("status"), "jobs": (wangp.get("jobs") or {}).get("count")},
        "llm": llm,
    })


@mcp.tool()
def film_bible(genres: list[str] | None = None, visual_style: str = "",
               film_aesthetic: str = "", era: str = "",
               include_production_summary: bool = True,
               project_path: str = "", project_name: str = "") -> str:
    """Read a project's creative bible: ideas, screenplay outline, characters,
    locations, approvals, style DNA and shot inventory. Use it to plan or
    resume work on a film. Pass project_name/project_path to switch projects;
    omit both to read the backend's active project. Returns a compact summary
    when include_production_summary is true, otherwise the full memory."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    state = _call("GET", "/api/orchestrator/memory", timeout=30)
    mem = state.get("memory") or {}
    pg = mem.get("project_graph", {}) or {}
    if not include_production_summary:
        return _out(state.get("memory") or {"note": "no orchestrator memory"})
    scr = pg.get("screenplay") or {}
    scenes = pg.get("scene_graph") or []
    shots = []
    for si, sc in enumerate(scenes):
        for hi, sh in enumerate(sc.get("shots", []) or []):
            shots.append({"scene": si, "shot": hi, "shot_id": sh.get("shot_id"),
                          "status": sh.get("storyboard_status", "")})
    ch_bible = pg.get("character_bible") or []
    loc_bible = pg.get("location_bible") or []
    return _out({
        "project_info": mem.get("project_info", {}),
        "ideas": [i.get("title") for i in (mem.get("ideas") or [])],
        "selected_idea_index": mem.get("selected_idea_index"),
        "screenplay_title": scr.get("title"),
        "outline": scr.get("outline") or scr.get("logline"),
        "characters": [{"id": c.get("character_id"), "name": c.get("name")} for c in ch_bible],
        "locations": [{"id": l.get("location_id"), "name": l.get("name")} for l in loc_bible],
        "approvals": pg.get("approvals", {}),
        "character_sheets": list((pg.get("character_sheets") or {}).keys()),
        "style_dna": pg.get("style_dna"),
        "scenes": len(scenes),
        "shots": shots[:40],
        "total_shots": len(shots),
    })


# --------------------------------------------------------------- production --
@mcp.tool()
def film_create_project(name: str, description: str = "",
                        location: str = "", template_name: str = "") -> str:
    """Create a new film project on disk and make it the backend's active
    project (subsequent stages write into it). Returns the project path."""
    payload = {"name": name, "description": description, "template_name": template_name or None}
    if location:
        payload["location"] = location
    res = _call("POST", "/api/projects", payload)
    if not res.get("success"):
        return _out(res)
    # create_project returns only {success, message}; locate the new folder via
    # the registry and make it the active project on the backend.
    path = _resolve_project(name)
    if path:
        _call("POST", "/api/projects/register", {"name": name, "path": path})
        _, _ = _use_project(project_name=name)
        return _out({"success": True, "name": name, "path": path, "active": True})
    return _out({"success": True, "name": name, "path": "", "note": "created; path not resolved"})


@mcp.tool()
def film_list_projects() -> str:
    """List every known film project with its name and folder path."""
    res = _call("GET", "/api/projects", timeout=30)
    return _out(res.get("projects", res))


@mcp.tool()
def film_generate_ideas(genres: list[str] | None = None, visual_style: str = "",
                        film_aesthetic: str = "", era: str = "",
                        custom_prompt: str = "", dialogue_enabled: bool = True,
                        character_count: int = 3, location_count: int = 3,
                        scene_count: int = 10, total_shot_count: int = 30,
                        aspect_ratio: str = "16:9", image_resolution: str = "1024x576",
                        video_resolution: str = "1280x720",
                        project_path: str = "", project_name: str = "") -> str:
    """Stage 1 — brainstorm 5 cinematic story ideas from genres/style/prompt.
    Requires the local LLM. The chosen idea is picked later by index in
    film_generate_screenplay.    Pass project_name to target a project."""
    pctx = _use_project(project_path, project_name) if (project_path or project_name) else ("", "")
    payload = {"genres": genres or [], "visual_style": visual_style,
               "film_aesthetic": film_aesthetic, "era": era,
               "custom_prompt": custom_prompt, "dialogue_enabled": dialogue_enabled,
               "character_count": character_count, "location_count": location_count,
               "scene_count": scene_count, "total_shot_count": total_shot_count,
               "aspect_ratio": aspect_ratio, "image_resolution": image_resolution,
               "video_resolution": video_resolution}
    res = _call("POST", "/api/orchestrator/ideas", payload, timeout=1800)
    ideas = res.get("ideas") or []
    if res.get("success"):
        _persist_memory(pctx[0], pctx[1])
    return _out({"success": res.get("success"), "count": len(ideas),
                 "ideas": [{"index": i, "title": it.get("title"), "logline": it.get("logline") or it.get("premise")}
                           for i, it in enumerate(ideas)], "error": res.get("error")})


@mcp.tool()
def film_generate_screenplay(idea_index: int, character_count: int = 3,
                             location_count: int = 3, scene_count: int = 10,
                             total_shot_count: int = 30,
                             project_path: str = "", project_name: str = "") -> str:
    """Stage 2 — expand the chosen idea into a master screenplay with scene
    graph, shot nodes and character/location bibles. Long LLM call.
    Pass project_name to target a project."""
    pctx = _use_project(project_path, project_name) if (project_path or project_name) else ("", "")
    res = _call("POST", "/api/orchestrator/screenplay",
                {"idea_index": idea_index, "character_count": character_count,
                 "location_count": location_count, "scene_count": scene_count,
                 "total_shot_count": total_shot_count}, timeout=5400)
    if res.get("success"):
        _persist_memory(pctx[0], pctx[1])
    pg = (res.get("memory") or {}).get("project_graph", {}) if isinstance(res.get("memory"), dict) else {}
    return _out({"success": res.get("success"),
                 "title": (res.get("screenplay") or {}).get("title") or pg.get("screenplay", {}).get("title"),
                 "scenes": len(pg.get("scene_graph", []) or []),
                 "characters": [c.get("character_id") for c in (pg.get("character_bible") or [])],
                 "locations": [l.get("location_id") for l in (pg.get("location_bible") or [])],
                 "error": res.get("error")})


@mcp.tool()
def film_generate_locations(project_path: str = "", project_name: str = "") -> str:
    """Stage 3 — write image prompts for every location in the bibles.
    Pass project_name to target a project."""
    pctx = _use_project(project_path, project_name) if (project_path or project_name) else ("", "")
    res = _call("POST", "/api/orchestrator/locations", {}, timeout=3600)
    if res.get("success"):
        _persist_memory(pctx[0], pctx[1])
    return _out(res, keep=("success", "error", "location_bible"))


@mcp.tool()
def film_generate_characters(project_path: str = "", project_name: str = "") -> str:
    """Stage 4 — write image prompts for every character in the bibles.
    Pass project_name to target a project."""
    pctx = _use_project(project_path, project_name) if (project_path or project_name) else ("", "")
    res = _call("POST", "/api/orchestrator/characters", {}, timeout=3600)
    if res.get("success"):
        _persist_memory(pctx[0], pctx[1])
    return _out(res, keep=("success", "error", "character_bible"))


@mcp.tool()
def film_asset_studio(project_path: str = "", project_name: str = "") -> str:
    """Inspect the asset studio: character/location bibles with prompts,
    approval state, generated assets and locks. Pass project_name/project_path
    to switch projects; omit both for the backend's active project."""
    p = _project_path(project_path, project_name) if (project_path or project_name) else ""
    res = _call("GET", "/api/orchestrator/asset-studio" + (f"?path={urllib.parse.quote(p)}" if p else ""), timeout=30)
    return _out(res)


@mcp.tool()
def film_generate_asset_image(asset_type: str, asset_id: str, project_path: str = "",
                              seed: int | None = None, steps: int | None = None,
                              cfg: float | None = None, workflow_name: str = "",
                              resolution: str = "1024x576") -> str:
    """Render a character or location image with ComfyUI from its locked
    image_prompt. asset_type: 'character' or 'location'. The output lands in
    ComfyUI's output folder — save it with film_save_asset_image."""
    p = _project_path(project_path)
    if not p:
        return _out({"success": False, "error": "no active project — call film_create_project or open one in the UI"})
    studio = _call("GET", "/api/orchestrator/asset-studio", timeout=30)
    pg = ((studio.get("memory") or {}).get("project_graph")) if isinstance(studio.get("memory"), dict) else None
    pg = pg or {}
    bible_key = "character_bible" if asset_type.startswith("char") else "location_bible"
    entry = next((a for a in pg.get(bible_key, []) or []
                  if (a.get("character_id") or a.get("location_id")) == asset_id), None)
    prompt = (entry or {}).get("image_prompt") or ""
    if not prompt:
        return _out({"success": False, "error": f"no image_prompt for {asset_id} — run film_generate_characters/locations first"})
    settings = _call("GET", "/api/settings", timeout=15)
    wf = workflow_name or ((settings.get("workflows") or {}).get("t2i", "")) or DEFAULT_T2I_WORKFLOW
    payload = {"provider": "comfyui", "prompt": prompt, "seed": seed, "steps": steps,
               "cfg": cfg, "resolution": resolution, "project_path": p,
               "workflow_name": wf, "input_images": []}
    res = _call("POST", "/api/image/generate", payload, timeout=120)
    if not res.get("success"):
        return _out(res)
    prog = _poll_image("asset image", max_seconds=1800)
    filename = res.get("filename") or prog.get("filename") or (prog.get("result") or {}).get("filename") or ""
    return _out({"success": prog.get("status") != "error", "asset_id": asset_id,
                 "workflow": wf, "filename": filename, "subfolder": res.get("subfolder", ""),
                 "progress": prog})


@mcp.tool()
def film_save_asset_image(asset_type: str, asset_id: str, filename: str,
                          subfolder: str = "", card_name: str = "",
                          project_path: str = "") -> str:
    """Save a finished ComfyUI render into the project (characters/ or
    locations/). filename/subfolder come from the generation result."""
    p = _project_path(project_path)
    if not p:
        return _out({"success": False, "error": "no active project"})
    stage = "characters" if asset_type.startswith("char") else "locations"
    res = _call("POST", "/api/projects/save-image",
                {"project_path": p, "stage": stage, "filename": filename,
                 "subfolder": subfolder, "card_name": card_name or asset_id})
    return _out(res)


@mcp.tool()
def film_approve_asset(asset_type: str, asset_id: str, image_path: str,
                       project_path: str = "") -> str:
    """Approve one generated asset image (copies it to <folder>/approved/<id>.png
    and marks it approved in the project graph). asset_type: 'char'|'character',
    'loc'|'location' or 'prop'; image_path is project-relative, e.g.
    'characters/CHAR_001_123.png'."""
    t = {"char": "char", "character": "char", "loc": "loc", "location": "loc",
         "prop": "prop"}.get(asset_type.lower().strip(), "")
    if t not in ("char", "loc", "prop"):
        return _out({"success": False, "error": "asset_type must be char|loc|prop"})
    p = _project_path(project_path)
    if not p:
        return _out({"success": False, "error": "no active project"})
    res = _call("POST", "/api/assets/approve-single",
                {"id": asset_id, "type": t, "image_path": image_path, "project_path": p})
    return _out(res)


@mcp.tool()
def film_approve_all_assets(approve_characters: bool = True,
                            approve_locations: bool = True,
                            project_path: str = "", project_name: str = "") -> str:
    """Mark characters and/or locations as approved in bulk (enables storyboard
    generation and locks their prompts). Call after approving the individual
    images you want, or on its own to accept the whole bible.
    Pass project_name to target a project."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    out = {}
    if approve_characters:
        out["characters"] = _call("POST", "/api/orchestrator/approve-characters", {})
    if approve_locations:
        out["locations"] = _call("POST", "/api/orchestrator/approve-locations", {})
    return _out(out)


@mcp.tool()
def film_describe_character(character_id: str, project_path: str = "") -> str:
    """Vision-describe a character's approved image with the local VLM and
    store the description on the character. Needed before
    film_generate_character_sheet (the sheet is drawn from this description)."""
    p = _project_path(project_path)
    res = _call("POST", "/api/orchestrator/describe-character-image",
                {"character_id": character_id, "project_path": p}, timeout=300)
    desc = res.get("description") or res.get("data") or res.get("raw") or ""
    return _out({"success": res.get("success"), "character_id": character_id,
                 "description": desc, "error": res.get("error")})


@mcp.tool()
def film_generate_character_sheet(character_id: str, project_path: str = "",
                                  seed: int | None = None, steps: int = 20,
                                  cfg: float = 1.0, workflow_name: str = "",
                                  resolution: str = "2560x1440") -> str:
    """Vision-driven character sheet loop: describe the approved portrait,
    generate a turnaround prompt, render the multi-view sheet with ComfyUI and
    save it as character_sheets/<id>_<seed>.png. Long GPU call."""
    p = _project_path(project_path)
    if not p:
        return _out({"success": False, "error": "no active project"})
    d = _call("POST", "/api/orchestrator/describe-character-image",
              {"character_id": character_id, "project_path": p}, timeout=300)
    desc = d.get("description") or d.get("data") or d.get("raw") or ""
    if not desc:
        return _out({"success": False, "error": f"could not describe {character_id}: {d.get('error', 'empty description')}"})
    t = _call("POST", "/api/orchestrator/generate-turnaround",
              {"character_id": character_id, "image_description": desc}, timeout=300)
    prompt = t.get("turnaround_request", {}).get("prompt", "")
    if not prompt:
        return _out({"success": False, "error": t.get("error", "no turnaround prompt generated")})
    settings = _call("GET", "/api/settings", timeout=15)
    wf = workflow_name or ((settings.get("image_gen") or {}).get("sheet_workflow")
                           or (settings.get("workflows") or {}).get("charsheet", ""))
    payload = {"provider": "comfyui", "prompt": prompt, "seed": seed, "steps": steps,
               "cfg": cfg, "resolution": resolution, "project_path": p,
               "workflow_name": wf, "input_images": []}
    res = _call("POST", "/api/image/generate", payload, timeout=120)
    if not res.get("success"):
        return _out({"success": False, "error": res.get("error"), "turnaround_prompt": prompt})
    prog = _poll_image("character sheet", max_seconds=2400)
    filename = res.get("filename") or prog.get("filename") or (prog.get("result") or {}).get("filename") or ""
    saved = {}
    if filename:
        saved = _call("POST", "/api/projects/save-image",
                      {"project_path": p, "stage": "character_sheets",
                       "filename": filename, "subfolder": prog.get("subfolder", ""),
                       "card_name": f"{character_id}_{seed or 'auto'}"})
    return _out({"success": bool(saved.get("success")), "character_id": character_id,
                 "turnaround_prompt": prompt, "progress_status": prog.get("status"),
                 "filename": filename, "saved": saved})


@mcp.tool()
def film_save_character_sheet(character_id: str, sheet_image: str,
                              approved: bool = False, portrait_image: str = "",
                              project_path: str = "", project_name: str = "") -> str:
    """Register a character sheet in the project graph; with approved=true it
    is copied to character_sheets/approved/<id>.png (final sheet for the film).
    Pass project_name to target a project."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    res = _call("POST", "/api/orchestrator/save-character-sheet",
                {"character_id": character_id, "sheet_image": sheet_image,
                 "approved": approved, "portrait_image": portrait_image})
    if approved:
        _call("POST", "/api/orchestrator/approve-characters", {})
    return _out(res, keep=("success", "error"))


@mcp.tool()
def film_generate_storyboard(project_path: str = "", project_name: str = "") -> str:
    """Stage 5 — enrich every shot with storyboard prompts, camera/lens/light
    language and continuity notes. Requires the screenplay stage. Long LLM
    call. Pass project_name to target a project."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    res = _call("POST", "/api/orchestrator/storyboard", {}, timeout=5400)
    return _out(res, keep=("success", "error", "progress", "message"))


@mcp.tool()
def film_generate_video_prompts(project_path: str = "", project_name: str = "") -> str:
    """Stage 6 — write LTX-style video prompts for every shot. Long LLM call.
    Pass project_name to target a project."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    res = _call("POST", "/api/orchestrator/video-prompts", {}, timeout=5400)
    return _out(res, keep=("success", "error", "progress", "message"))


@mcp.tool()
def film_shots(scene: int | None = None, status: str = "",
               project_path: str = "", project_name: str = "") -> str:
    """List the storyboard shots (scene/shot index, id, status, action text).
    Filter by scene index or by status ('none','generated','approved').
    Pass project_name/project_path to switch projects; omit both for the
    backend's active project."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    state = _call("GET", "/api/orchestrator/memory", timeout=30)
    pg = (state.get("memory") or {}).get("project_graph", {}) or {}
    rows = []
    for si, sc in enumerate(pg.get("scene_graph", []) or []):
        if scene is not None and si != scene:
            continue
        for hi, sh in enumerate(sc.get("shots", []) or []):
            st = sh.get("storyboard_status", "") or "none"
            if status and st != status:
                continue
            rows.append({"scene": si, "shot": hi, "shot_id": sh.get("shot_id"),
                         "status": st,
                         "action": (sh.get("action") or "")[:120],
                         "has_storyboard_prompt": bool(sh.get("storyboard_prompt"))})
    return _out({"count": len(rows), "shots": rows})


@mcp.tool()
def film_generate_shot_image(scene: int, shot: int, project_path: str = "",
                             prompt_override: str = "", seed: int | None = None,
                             steps: int | None = None, cfg: float | None = None,
                             workflow_name: str = "", resolution: str = "1024x576",
                             save: bool = True, card_name: str = "",
                             project_name: str = "") -> str:
    """Render one storyboard image with ComfyUI (t2i workflow + Style DNA).
    The prompt is the shot's storyboard_prompt unless overridden. With
    save=true the result is written to scenes/<shot_id>.png and the shot is
    marked 'generated'. Long GPU call. Pass project_name to target a project."""
    p = _project_path(project_path, project_name)
    if not p:
        return _out({"success": False, "error": "no active project"})
    state = _call("GET", "/api/orchestrator/memory", timeout=30)
    pg = (state.get("memory") or {}).get("project_graph", {}) or {}
    scenes = pg.get("scene_graph", []) or []
    if scene < 0 or scene >= len(scenes):
        return _out({"success": False, "error": f"scene {scene} out of range (0..{len(scenes)-1})"})
    shots = scenes[scene].get("shots", []) or []
    if shot < 0 or shot >= len(shots):
        return _out({"success": False, "error": f"shot {shot} out of range (0..{len(shots)-1})"})
    sh = shots[shot]
    prompt = prompt_override or sh.get("storyboard_prompt") or sh.get("action") or ""
    if not prompt:
        return _out({"success": False, "error": f"shot {sh.get('shot_id')} has no storyboard_prompt/action — run film_generate_storyboard"})
    settings = _call("GET", "/api/settings", timeout=15)
    wf = workflow_name or ((settings.get("workflows") or {}).get("t2i", "")) or DEFAULT_T2I_WORKFLOW
    res = _call("POST", "/api/image/generate",
                {"provider": "comfyui", "prompt": prompt, "seed": seed, "steps": steps,
                 "cfg": cfg, "resolution": resolution, "project_path": p,
                 "workflow_name": wf, "input_images": []}, timeout=120)
    if not res.get("success"):
        return _out(res)
    prog = _poll_image("shot image", max_seconds=1800)
    filename = res.get("filename") or prog.get("filename") or (prog.get("result") or {}).get("filename") or ""
    saved = {}
    if save and filename:
        saved = _call("POST", "/api/projects/save-image",
                      {"project_path": p, "stage": "scenes",
                       "filename": filename, "subfolder": prog.get("subfolder", ""),
                       "card_name": card_name or sh.get("shot_id")})
    return _out({"success": bool(saved.get("success", True)), "shot_id": sh.get("shot_id"),
                 "prompt": prompt[:400], "filename": filename, "saved": saved,
                 "progress_status": prog.get("status"), "error": prog.get("error") or saved.get("error")})


@mcp.tool()
def film_generate_shot_video(scene: int, shot: int, project_path: str = "",
                             prompt_override: str = "", seed: int | None = None,
                             engine: str = "wangp", model_type: str = "",
                             resolution: str = "1280x720", video_length: int | None = None,
                             duration_seconds: float | None = None, steps: int | None = None,
                             save: bool = True, card_name: str = "",
                             project_name: str = "") -> str:
    """Animate one shot's saved storyboard image into a video clip.
    engine 'wangp' (default, LTX-2.5 with audio) uses image-to-video with the
    scenes/<shot_id>.png keyframe; engine 'comfyui' uses the i2v workflow.
    With save=true the finished clip is stored as videos/<shot_id>.mp4.
    Very long GPU call (minutes). Pass project_name to target a project."""
    p = _project_path(project_path, project_name)
    if not p:
        return _out({"success": False, "error": "no active project"})
    state = _call("GET", "/api/orchestrator/memory", timeout=30)
    pg = (state.get("memory") or {}).get("project_graph", {}) or {}
    scenes = pg.get("scene_graph", []) or []
    if scene < 0 or scene >= len(scenes):
        return _out({"success": False, "error": f"scene {scene} out of range"})
    shots = scenes[scene].get("shots", []) or []
    if shot < 0 or shot >= len(shots):
        return _out({"success": False, "error": f"shot {shot} out of range"})
    sh = shots[shot]
    shot_id = sh.get("shot_id")
    keyframe = os.path.join(p, "scenes", f"{shot_id}.png")
    if not os.path.exists(keyframe):
        return _out({"success": False, "error": f"missing keyframe {keyframe} — run film_generate_shot_image first"})
    prompt = prompt_override or sh.get("video_prompt") or sh.get("action") or ""
    if not prompt:
        return _out({"success": False, "error": f"shot {shot_id} has no video_prompt/action — run film_generate_video_prompts"})
    if engine == "wangp":
        settings = _call("GET", "/api/settings", timeout=15)
        payload = {"prompt": prompt, "media": "video",
                   "model_type": model_type or DEFAULT_WANGP_VIDEO_MODEL,
                   "resolution": resolution, "project_path": p,
                   "input_image": keyframe, "wait_timeout": 5400}
        for k, v in (("seed", seed), ("video_length", video_length), ("duration_seconds", duration_seconds), ("steps", steps)):
            if v is not None:
                payload[k] = v
        sub = _call("POST", "/api/wangp/generate/video", payload, timeout=90)
        job_id = sub.get("job_id")
        if not job_id:
            return _out({"success": False, "error": sub.get("error", "WanGP bridge did not accept the job")})
        job = _wangp_wait(job_id, max_seconds=5400)
        if job.get("status") != "done":
            return _out({"success": False, "job_id": job_id, "error": job.get("error", f"job status: {job.get('status')}")})
        ing = _wangp_ingest(job_id)
        if not ing.get("success"):
            return _out(ing)
        filename, subfolder = ing.get("filename", ""), ing.get("subfolder", "")
    else:
        settings = _call("GET", "/api/settings", timeout=15)
        wf = ((settings.get("workflows") or {}).get("i2v", "")) or DEFAULT_I2V_WORKFLOW
        res = _call("POST", "/api/video/generate",
                    {"provider": "comfyui", "prompt": prompt, "input_image": keyframe,
                     "workflow_name": wf, "project_path": p, "seed": seed}, timeout=60)
        if not res.get("success"):
            return _out(res)
        filename, subfolder = res.get("filename", ""), res.get("subfolder", "")
        if not filename:
            prog = _poll_image("shot video", max_seconds=3600)
            filename = prog.get("filename") or (prog.get("result") or {}).get("filename") or ""
    saved = {}
    if save and filename:
        saved = _call("POST", "/api/projects/save-video",
                      {"project_path": p, "filename": filename, "subfolder": subfolder,
                       "card_name": card_name or shot_id})
    return _out({"success": bool(saved.get("success", True)), "shot_id": shot_id,
                 "engine": engine, "job_id": job_id if engine == "wangp" else None,
                 "filename": filename, "saved": saved, "error": saved.get("error")})


@mcp.tool()
def film_approve_shot(shot_id: str, project_path: str = "", project_name: str = "") -> str:
    """Mark a shot approved for the timeline after its image/video look right.
    Pass project_name to target a project."""
    if project_path or project_name:
        _project_path(project_path, project_name)
    res = _call("POST", "/api/orchestrator/shot-approve", {"shot_id": shot_id})
    return _out(res, keep=("success", "error"))


@mcp.tool()
def film_generate_audio(prompt: str, audio_type: str = "sfx", project_path: str = "",
                        project_name: str = "") -> str:
    """Generate an audio clip (audio_type: 'sfx', 'vo' or 'music') from a text
    prompt into the project's audio/ folder."""
    p = _project_path(project_path, project_name)
    if not p:
        return _out({"success": False, "error": "no active project"})
    name = project_name or os.path.basename(p)
    res = _call("POST", f"/api/projects/{urllib.parse.quote(name)}/generate-audio",
                {"prompt": prompt, "type": audio_type, "project_path": p}, timeout=300)
    return _out(res)


@mcp.tool()
def film_bake_timeline(timeline: list[dict], clips: list[dict] | None = None,
                       project_path: str = "", project_name: str = "",
                       width: int = 1280, height: int = 720, fps: float = 24.0,
                       shot_audio: bool = False) -> str:
    """Bake the finished film: concatenate the shot videos (timeline entries
    like {'file': 'SHOT_0101.mp4', 'duration': 5}) from the project's videos/
    folder, optionally mix ♪ audio clips ({'file','start','duration','gain',
    'fadeIn','fadeOut'}), and write an export MP4 to exports/. This is the
    final step — returns the export file name."""
    p = _project_path(project_path, project_name)
    if not p:
        return _out({"success": False, "error": "no active project"})
    name = project_name or os.path.basename(p)
    payload = {"project_path": p, "timeline": timeline, "clips": clips or [],
               "width": width, "height": height, "fps": fps, "shot_audio": shot_audio}
    res = _call("POST", f"/api/projects/{urllib.parse.quote(name)}/bake-audio", payload, timeout=1800)
    return _out(res)


@mcp.tool()
def film_export_xml(timeline: list[dict], project_path: str = "", project_name: str = "",
                    timebase: int = 24) -> str:
    """Export the timeline as Apple FCP7 XML for real NLEs (Premiere/Resolve/
    Final Cut). timeline entries: {'file', 'in_frame', 'out_frame'}."""
    p = _project_path(project_path, project_name)
    if not p:
        return _out({"success": False, "error": "no active project"})
    name = project_name or os.path.basename(p)
    res = _call("POST", f"/api/projects/{urllib.parse.quote(name)}/export-xml",
                {"project_path": p, "timeline": timeline, "timebase": timebase}, timeout=60)
    return _out(res)


# ------------------------------------------------------------------ WanGP --
@mcp.tool()
def film_wangp_generate_image(prompt: str, project_path: str = "",
                              seed: int | None = None, resolution: str = "1280x720",
                              steps: int | None = None, model_type: str = "",
                              save: bool = True, card_name: str = "") -> str:
    """Text-to-image via WanGP (Qwen Image 2.1): submit, wait, ingest into
    ComfyUI output and optionally save into the project's images/ stage.
    Use for hero frames, posters or concept art outside the shot pipeline."""
    p = _project_path(project_path)
    if not p:
        return _out({"success": False, "error": "no active project"})
    payload = {"prompt": prompt, "media": "image",
               "model_type": model_type or DEFAULT_WANGP_IMAGE_MODEL,
               "resolution": resolution, "project_path": p, "wait_timeout": 1500}
    if seed is not None:
        payload["seed"] = seed
    if steps is not None:
        payload["steps"] = steps
    r = _gen_wait_ingest("/api/wangp/generate/image/wait", payload, "image", 1500)
    if r.get("success") and save and r.get("filename"):
        saved = _call("POST", "/api/projects/save-image",
                      {"project_path": p, "stage": "images", "filename": r["filename"],
                       "subfolder": r.get("subfolder", ""), "card_name": card_name or "wangp_image"})
        r["saved"] = saved
    return _out(r)


@mcp.tool()
def film_wangp_generate_video(prompt: str, ref_image: str, project_path: str = "",
                              seed: int | None = None, resolution: str = "1280x720",
                              video_length: int | None = None,
                              duration_seconds: float | None = None,
                              model_type: str = "", save: bool = True,
                              card_name: str = "") -> str:
    """Image-to-video via WanGP (LTX-2.5 22B distilled, native audio): submit,
    wait, ingest, optionally save into videos/. ref_image = absolute path to
    the start frame. Very long GPU call."""
    p = _project_path(project_path)
    if not p:
        return _out({"success": False, "error": "no active project"})
    payload = {"prompt": prompt, "media": "video",
               "model_type": model_type or DEFAULT_WANGP_VIDEO_MODEL,
               "resolution": resolution, "project_path": p,
               "input_image": ref_image, "wait_timeout": 5400}
    if seed is not None:
        payload["seed"] = seed
    if video_length is not None:
        payload["video_length"] = video_length
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    r = _gen_wait_ingest("/api/wangp/generate/video/wait", payload, "video", 5400)
    if r.get("success") and save and r.get("filename"):
        saved = _call("POST", "/api/projects/save-video",
                      {"project_path": p, "filename": r["filename"],
                       "subfolder": r.get("subfolder", ""), "card_name": card_name or "wangp_video"})
        r["saved"] = saved
    return _out(r)


if __name__ == "__main__":
    mcp.run()
