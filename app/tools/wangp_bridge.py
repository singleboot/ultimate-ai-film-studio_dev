"""WanGP Bridge — a FastAPI sidecar that exposes WanGP's native Python API over HTTP.

Lets the studio (and the MCP server) drive WanGP generation the same way the
ComfyUI client does: submit a job, poll progress, collect output files.

Run with WanGP's own venv (it needs WanGP's deps):

    D:/01_PINOKIO/api/wan_sep2026.git/app/venv/Scripts/python.exe \
        app/tools/wangp_bridge.py --port 8189

Design notes:
- Jobs run in background threads; the API returns a job_id immediately so long
  first-time model downloads don't block the caller.
- Before each submission we ask the resident ComfyUI (port 8188) to free its
  models so both engines can share the 12 GB card (the "accept swaps" strategy).
- Job state is kept in memory for the lifetime of the process (last 50 jobs).
"""
import argparse
import json
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

WANGP_ROOT = Path(r"D:\01_PINOKIO\api\wan_sep2026.git\app")
COMFY_FREE_URL = "http://127.0.0.1:8188/free"
MAX_JOBS = 50

app = FastAPI(title="WanGP Bridge")
_state = {
    "session": None,
    "init_error": None,
    "jobs": {},          # job_id -> dict
    "jobs_order": [],    # job_ids, oldest first
    "lock": threading.Lock(),
    "version": None,
}


class GenerateRequest(BaseModel):
    prompt: str
    seed: int | None = None
    resolution: str | None = "1280x720"
    steps: int | None = None
    model_type: str | None = None          # default chosen per media kind
    media: str = "image"                   # "image" | "video" | "audio"
    ref_images: list[str] | None = None    # absolute paths (i2i / i2v)
    video_length: int | str | None = None  # frame count, or seconds like "10s"
    duration_seconds: float | None = None
    loras: list[str] | None = None         # absolute/relative paths to .safetensors
    lora_strength: float | None = None     # applied to all activated loras (default 1.0)
    extra: dict | None = None              # raw WanGP settings passthrough

    @property
    def media_norm(self) -> str:
        m = (self.media or "image").lower()
        if m in ("tts", "audio"):
            return "audio"
        return m


def _session():
    if _state["session"] is None:
        if _state["init_error"]:
            raise HTTPException(503, f"WanGP init failed earlier: {_state['init_error']}")
        try:
            import sys
            sys.path.insert(0, str(WANGP_ROOT))
            from shared import api
            _state["session"] = api.init(
                root=str(WANGP_ROOT),
                console_output=False,
                console_isatty=False,
                cli_args=["--attention", "sdpa", "--profile", "4"],
            )
            import wgp  # noqa: E402  (already imported by api.init; version probe)
            _state["version"] = getattr(wgp, "WanGP_version", "?")
        except Exception as e:  # pragma: no cover
            _state["init_error"] = f"{e}\n{traceback.format_exc()[-800:]}"
            raise HTTPException(503, f"WanGP init failed: {e}")
    return _state["session"]


def _free_comfy_memory():
    """Best-effort: ask the resident ComfyUI to unload models before we render."""
    try:
        import requests
        requests.post(COMFY_FREE_URL, json={"unload_models": True, "free_memory": True}, timeout=10)
    except Exception:
        pass


def _record_job(job_id, info):
    with _state["lock"]:
        _state["jobs"][job_id] = info
        _state["jobs_order"].append(job_id)
        while len(_state["jobs_order"]) > MAX_JOBS:
            old = _state["jobs_order"].pop(0)
            _state["jobs"].pop(old, None)


def _default_settings(sess, media, model_type):
    if model_type:
        return {"model_type": model_type}
    return {
        "image": {"model_type": "qwen_image_21_7B"},
        # LTX 2.5 Distilled 22B: video + real generated audio, 8 steps, much faster
        # than the MiniMax H3 variants on a 12GB card (user pick after timing both).
        "video": {"model_type": "ltx2_25_22B_distilled"},
        # Zero-shot text-only TTS (small download); voice-clone models that need a
        # reference clip (IndexTTS2/2.5, MiniMax H3 Voice Clone) stay in the picker.
        "audio": {"model_type": "qwen3_tts_customvoice"},
    }.get(media, {"model_type": "qwen_image_21_7B"})


def _run_job(job_id: str, settings: dict):
    info = _state["jobs"][job_id]
    try:
        sess = _session()
        _free_comfy_memory()
        info["status"] = "submitted"
        job = sess.submit_task(settings)
        info["status"] = "running"
        last_phase = None
        # Documented pattern: the events generator terminates when the job ends.
        for event in job.events.iter(timeout=0.5):
            if event.kind == "progress":
                d = event.data
                phase = str(getattr(d, "phase", "") or "")
                info["phase"] = phase
                info["pct"] = round(float(getattr(d, "progress", 0) or 0) * 100, 1)
                info["step"] = f"{getattr(d, 'current_step', '')}/{getattr(d, 'total_steps', '')}"
                if phase != last_phase:
                    info.setdefault("log", []).append(f"phase: {phase}")
                    last_phase = phase
            elif event.kind == "stream":
                line = str(getattr(event.data, "text", "") or "")
                if line.strip() and ("ownload" in line or "GB" in line):
                    info.setdefault("log", []).append(line[:160])
        res = job.result()
        info["done_at"] = time.time()
        if res.success:
            info["status"] = "done"
            info["files"] = list(res.generated_files or [])
        else:
            info["status"] = "error"
            info["error"] = "; ".join(str(e.message) for e in (res.errors or []))[:800]
    except Exception as e:
        info["status"] = "error"
        info["error"] = f"{e}"[:800]


@app.get("/health")
def health():
    ok = _state["session"] is not None
    return {"status": "ok" if ok else "init-pending", "version": _state["version"],
            "init_error": _state["init_error"], "jobs": len(_state["jobs"])}


@app.post("/generate")
def generate(req: GenerateRequest):
    # NOTE: do NOT touch the session here. First-use WanGP init can take minutes
    # (runtime import + kernels); doing it on the request thread made the submit
    # POST time out while the studio waited. The worker thread resolves the
    # session instead, so submission is always instant.
    settings = {"model_type": (req.model_type or _default_settings(None, req.media_norm, None)["model_type"])}
    settings["prompt"] = req.prompt
    if req.resolution and req.media_norm != "audio":
        settings["resolution"] = req.resolution
    if req.steps:
        settings["num_inference_steps"] = req.steps
    if req.seed is not None:
        settings["seed"] = req.seed
    if req.video_length:
        settings["video_length"] = req.video_length
    if req.duration_seconds:
        settings["duration_seconds"] = req.duration_seconds
    if req.ref_images:
        settings["image_refs"] = req.ref_images
    if req.loras:
        # Resolve relative names against WanGP's loras root (WanGP resolves
        # remaining relative paths against the caller's CWD, which is wrong here).
        lora_paths = []
        for lp in req.loras:
            p = Path(lp)
            if not p.is_absolute():
                p = WANGP_ROOT / "loras" / lp
            lora_paths.append(str(p))
        settings["activated_loras"] = lora_paths
        # per-index multipliers string, e.g. "1.0" applies to every lora
        strength = req.lora_strength if req.lora_strength is not None else 1.0
        settings["loras_multipliers"] = " ".join([str(strength)] * len(req.loras))
    if req.extra:
        settings.update(req.extra)

    job_id = f"j{int(time.time()*1000)}"
    _record_job(job_id, {
        "job_id": job_id, "status": "queued", "settings": settings,
        "started_at": time.time(), "pct": 0.0, "phase": "", "log": [],
    })
    threading.Thread(target=_run_job, args=(job_id, settings), daemon=True).start()
    return {"job_id": job_id, "status": "queued", "settings": settings}


@app.get("/job/{job_id}")
def job_status(job_id: str):
    with _state["lock"]:
        info = _state["jobs"].get(job_id)
        if not info:
            raise HTTPException(404, "unknown job")
        return {k: v for k, v in info.items() if k != "cancel"}


@app.get("/models")
def models(query: str = "", family: str = "", available: str = "", task: str = ""):
    """List WanGP models with availability. task filters by output kind:
    image (image, no video), video (video output), tts (audio only)."""
    sess = _session()
    kwargs = {"include_availability": True}
    if query:
        kwargs["query"] = query
    if family:
        kwargs["family"] = family
    recs = sess.list_model_metadata(**kwargs)
    out = []
    for r in recs:
        av = r.get("availability") or {}
        status = av.get("status")
        if available == "1" and status != "available":
            continue
        md = r  # metadata record: family_label / outputs / inputs live at top level
        outputs = md.get("outputs") or []
        inputs = md.get("inputs") or []
        if task == "image" and not ("image" in outputs and "video" not in outputs):
            continue
        if task == "video" and "video" not in outputs:
            continue
        if task == "tts" and not ("audio" in outputs and "video" not in outputs):
            continue
        out.append({
            "model_type": r.get("model_type"),
            "name": r.get("name"),
            "status": status,
            "family": md.get("family_label") or md.get("family") or "",
            "outputs": outputs,
            "inputs": inputs,
        })
    return {"models": out}


@app.get("/loras")
def loras():
    """List LoRA files under WanGP's loras folder, grouped by subfolder."""
    root = WANGP_ROOT / "loras"
    groups = {}
    if root.exists():
        for p in sorted(root.rglob("*.safetensors")):
            rel = p.relative_to(root).as_posix()
            fam = rel.split("/")[0] if "/" in rel else "(root)"
            groups.setdefault(fam, []).append({
                "path": str(p),
                "rel": rel,
                "size_mb": round(p.stat().st_size / 1e6, 1),
            })
    return {"families": groups}


@app.get("/model/{model_type}")
def model_detail(model_type: str):
    """Default settings + capability schema for one WanGP model (drives studio UI)."""
    sess = _session()
    try:
        defaults = sess.get_default_settings(model_type)
    except Exception as e:
        return {"error": f"unknown model_type: {model_type} ({e})"}
    schema = sess.get_model_schema(model_type) or {}
    md = schema.get("metadata") or {}
    defaults.pop("settings_version", None)
    return {
        "model_type": model_type,
        "name": md.get("name") or model_type,
        "family": md.get("family_label") or md.get("family") or "",
        "outputs": md.get("outputs") or [],
        "inputs": md.get("inputs") or [],
        "media_inputs": md.get("media_inputs") or {},
        "defaults": defaults,
        "availability": sess.get_model_availability(model_type).get("status"),
    }


@app.post("/free-comfy")
def free_comfy():
    _free_comfy_memory()
    return {"ok": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8189)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--eager", action="store_true", help="initialize WanGP at startup")
    args = parser.parse_args()
    if args.eager:
        try:
            _session()
            print(f"[wangp_bridge] WanGP v{_state['version']} session ready", flush=True)
        except Exception as e:
            print(f"[wangp_bridge] eager init failed: {e}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
