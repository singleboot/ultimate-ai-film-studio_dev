"""WanGP MCP Server — exposes the studio's WanGP bridge as MCP tools.

Runs on stdio so any MCP client (Codebuff, Claude Desktop, Deepy Prime, ...)
can drive WanGP generation. It intentionally does NOT import WanGP itself:
it talks HTTP to the wangp_bridge sidecar (127.0.0.1:8189), so exactly one
process owns the WanGP session and the GPU.

Recommended interpreter: WanGP's venv (it ships the mcp SDK):

    D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe \
        app/tools/wangp_mcp_server.py

Register with an MCP client (claude_desktop_config.json example):

    "wangp": {
      "command": "D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe",
      "args": ["F:/MY APP/ULTIMATE AI STUDIO/ultimate-ai-film-studio-V11/app/tools/wangp_mcp_server.py"]
    }
"""
import json
import time
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

BRIDGE = "http://127.0.0.1:8189"
DEFAULT_IMAGE_MODEL = "qwen_image_21_7B"
DEFAULT_VIDEO_MODEL = "ltx2_22B_distilled"

mcp = FastMCP("wangp")


def _call(method: str, path: str, payload: dict | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        BRIDGE + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wait_job(job_id: str, max_seconds: int = 3600) -> dict:
    """Poll a bridge job until done/error; returns the final job record."""
    deadline = time.time() + max_seconds
    last = {}
    while time.time() < deadline:
        try:
            last = _call("GET", f"/job/{job_id}")
        except Exception as e:
            last = {"status": "error", "error": f"poll failed: {e}"}
            break
        if last.get("status") in ("done", "error"):
            break
        time.sleep(3)
    return last


@mcp.tool()
def wangp_generate_image(
    prompt: str,
    seed: int | None = None,
    resolution: str = "1280x720",
    steps: int | None = None,
    model_type: str | None = None,
    wait: bool = True,
) -> str:
    """Generate an image with WanGP (default: Qwen Image 2.1 7B).

    Returns the job_id immediately when wait=false, otherwise waits and returns
    the generated file paths. WanGP style-model settings can be passed later via
    the extra_settings tool once needed.
    """
    payload = {"prompt": prompt, "media": "image", "resolution": resolution,
               "model_type": model_type or DEFAULT_IMAGE_MODEL}
    if seed is not None:
        payload["seed"] = seed
    if steps:
        payload["steps"] = steps
    submit = _call("POST", "/generate", payload, timeout=60)
    job_id = submit.get("job_id")
    if not wait:
        return json.dumps({"job_id": job_id, "status": "queued"})
    final = _wait_job(job_id)
    return json.dumps({"job_id": job_id, **{k: final.get(k) for k in ("status", "files", "error", "pct", "phase")}})


@mcp.tool()
def wangp_generate_video(
    prompt: str,
    seed: int | None = None,
    resolution: str = "1280x720",
    steps: int | None = None,
    video_length: int | None = None,
    duration_seconds: float | None = None,
    ref_image: str | None = None,
    model_type: str | None = None,
    wait: bool = False,
) -> str:
    """Generate a video with WanGP (default: LTX 2.5 22B distilled, which renders audio).

    ref_image: absolute path to a keyframe for image-to-video. Video renders are
    long, so wait=false (default) returns the job_id for wangp_job_status polling.
    """
    payload = {"prompt": prompt, "media": "video", "resolution": resolution,
               "model_type": model_type or DEFAULT_VIDEO_MODEL}
    if seed is not None:
        payload["seed"] = seed
    if steps:
        payload["steps"] = steps
    if video_length:
        payload["video_length"] = video_length
    if duration_seconds:
        payload["duration_seconds"] = duration_seconds
    if ref_image:
        payload["ref_images"] = [ref_image]
    submit = _call("POST", "/generate", payload, timeout=60)
    job_id = submit.get("job_id")
    if not wait:
        return json.dumps({"job_id": job_id, "status": "queued"})
    final = _wait_job(job_id, max_seconds=7200)
    return json.dumps({"job_id": job_id, **{k: final.get(k) for k in ("status", "files", "error", "pct", "phase")}})


@mcp.tool()
def wangp_job_status(job_id: str) -> str:
    """Poll a WanGP bridge job by id: status, progress phase/percent, output files, error."""
    try:
        return json.dumps(_call("GET", f"/job/{job_id}"))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def wangp_models(query: str = "", only_available: bool = False) -> str:
    """List WanGP models (optionally filtered by name query) with local download status."""
    try:
        path = "/models"
        sep = "?"
        if query:
            path += f"{sep}query={urllib.parse.quote(query)}"
            sep = "&"
        if only_available:
            path += f"{sep}available=1"
        data = _call("GET", path)
        models = data.get("models", [])
        if only_available:
            models = [m for m in models if m.get("status") == "available"]
        return json.dumps(models[:60])
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def wangp_health() -> str:
    """Check the WanGP bridge: version, init state, job count."""
    try:
        return json.dumps(_call("GET", "/health"))
    except Exception as e:
        return json.dumps({"error": f"bridge unreachable: {e}"})


@mcp.tool()
def wangp_free_comfy() -> str:
    """Ask the resident ComfyUI to unload its models (frees VRAM for WanGP)."""
    try:
        return json.dumps(_call("POST", "/free-comfy", {}, timeout=20))
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    mcp.run()
