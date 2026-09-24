# -*- coding: utf-8 -*-
"""StudioBridge — gives the FilmAgent real hands inside the studio.

The agent can now generate storyboard images and videos for individual shots,
approve them, and inspect the ComfyUI queue — all through tracked background
jobs so chat stays responsive while the GPU works.

Design notes:
- Jobs run in daemon threads calling ComfyUIClient.generate_with_workflow
  (blocking inside the thread is fine; the chat loop is never blocked).
- Results are written back into project_state.json via load-merge-atomic-save
  (same crash-safe semantics as project saves). The web UI keeps its own
  in-memory copy, so it needs a refresh to see agent-made changes — the agent
  is told to mention that.
- No cloud deps, no new pip packages.
"""
import io
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("film-studio.studio_bridge")


class StudioBridge:
    """Tracks agent-triggered production jobs and executes them on the GPU."""

    def __init__(self, comfyui_client=None, image_engine=None, project_manager=None):
        self.comfyui = comfyui_client
        self.image_engine = image_engine
        self.pm = project_manager
        self.jobs = {}   # job_id -> dict
        self._lock = threading.Lock()

    # ------------------------------------------------------------ settings --
    def _style_dna(self, project_path: str) -> str:
        """The project's locked Style DNA sentence, or '' when unset."""
        try:
            state = self._load_state(project_path)
            return ((state.get("projectSettings") or {}).get("styleDna") or "").strip()
        except Exception:
            return ""

    def _workflow_for(self, kind: str) -> str:
        try:
            settings_path = Path(__file__).parent.parent / "data" / "settings.json"
            if not settings_path.exists():
                import os
                base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "UltimateAIFilmStudio"
                settings_path = base / "settings.json"
            d = json.loads(io.open(settings_path, encoding="utf-8").read())
            wf = (d.get("workflows") or {}).get(kind, "")
            if wf:
                return wf
        except Exception as e:
            logger.warning("StudioBridge settings read failed: %s", e)
        defaults = {"t2i": "image_krea2_turbo_t2i_v2", "i2v": "video_ltx2_5_i2v"}
        return defaults.get(kind, "")

    def _provider(self):
        """(provider_id, model) from settings — mirrors the image engine defaults."""
        try:
            import os
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "UltimateAIFilmStudio"
            d = json.loads(io.open(base / "settings.json", encoding="utf-8").read())
            ig = d.get("image_gen") or {}
            return ig.get("provider", "comfyui"), ig.get("model", "")
        except Exception:
            return "comfyui", ""

    # --------------------------------------------------------- state utils --
    def _load_state(self, project_path: str) -> dict:
        f = Path(project_path) / "project_state.json"
        if not f.exists():
            return {}
        try:
            return json.loads(io.open(f, encoding="utf-8").read())
        except Exception:
            bak = Path(project_path) / "project_state.json.bak"
            if bak.exists():
                return json.loads(io.open(bak, encoding="utf-8").read())
            return {}

    def _save_state_atomic(self, project_path: str, state: dict):
        f = Path(project_path) / "project_state.json"
        bak = Path(project_path) / "project_state.json.bak"
        try:
            if f.exists():
                bak.write_bytes(f.read_bytes())
            tmp = f.with_suffix(".json.tmp")
            with io.open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, default=str)
                fh.flush()
                import os
                os.fsync(fh.fileno())
            tmp.replace(f)
            return True
        except Exception as e:
            logger.error("Atomic state save failed: %s", e)
            return False

    def _get_shot(self, state: dict, scene_idx: int, shot_idx: int):
        scenes = ((state.get("screenplayData") or {}).get("scenes")) or []
        if scene_idx < 0 or scene_idx >= len(scenes):
            return None, None
        shots = scenes[scene_idx].get("shots") or []
        if shot_idx < 0 or shot_idx >= len(shots):
            return None, None
        return scenes[scene_idx], shots[shot_idx]

    def _write_shot_fields(self, project_path: str, scene_idx: int, shot_idx: int, fields: dict) -> bool:
        state = self._load_state(project_path)
        scene, shot = self._get_shot(state, scene_idx, shot_idx)
        if not shot:
            return False
        shot.update(fields)
        return self._save_state_atomic(project_path, state)

    # ------------------------------------------------------------ job utils --
    def _job_snapshot(self, job_id: str) -> dict:
        with self._lock:
            j = self.jobs.get(job_id)
            if not j:
                return {"error": f"Unknown job {job_id}"}
            snap = {k: j.get(k) for k in ("job_id", "type", "shot_id", "scene", "shot", "status", "workflow", "error", "result", "queued_at", "finished_at", "elapsed_s")}
            return snap

    def jobs_snapshot(self, limit: int = 20) -> list:
        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.get("queued_at", ""), reverse=True)[:limit]
        return [{k: j.get(k) for k in ("job_id", "type", "shot_id", "status", "error", "result", "elapsed_s")} for j in jobs]

    # ------------------------------------------------------- image pipeline --
    def generate_shot_image(self, scene_idx: int, shot_idx: int, project_path: str, prompt_override: str = None) -> dict:
        state = self._load_state(project_path)
        scene, shot = self._get_shot(state, scene_idx, shot_idx)
        if not shot:
            return {"error": f"Scene {scene_idx} shot {shot_idx} not found in the project on disk."}
        prompt = prompt_override or shot.get("storyboard_prompt") or shot.get("action")
        if not prompt:
            return {"error": "Shot has no storyboard_prompt or action text to generate from."}

        job_id = uuid.uuid4().hex[:10]
        with self._lock:
            self.jobs[job_id] = {
                "job_id": job_id, "type": "image", "shot_id": shot.get("shot_id", "?"),
                "scene": scene_idx, "shot": shot_idx, "status": "queued",
                "workflow": self._workflow_for("t2i"), "queued_at": datetime.now().isoformat(timespec="seconds"),
            }
        t = threading.Thread(target=self._run_image_job, args=(job_id, scene_idx, shot_idx, project_path, prompt), daemon=True)
        t.start()
        return {
            "job_id": job_id, "status": "queued", "shot_id": shot.get("shot_id"),
            "workflow": self.jobs[job_id]["workflow"],
            "note": "Generating in the background. Check with get_generation_progress. The web UI needs a refresh to show agent-made changes.",
        }

    def _run_image_job(self, job_id, scene_idx, shot_idx, project_path, prompt):
        with self._lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = time.time()
        t0 = time.time()
        try:
            provider, model = self._provider()
            wf = self.jobs[job_id]["workflow"]
            style_dna = self._style_dna(project_path)
            full_prompt = (prompt + "\n\nSTYLE (apply exactly this look to every shot): " + style_dna) if (style_dna and style_dna not in prompt) else prompt
            res = self.image_engine.generate_image(
                provider_id=provider, model=model, prompt=full_prompt,
                workflow_name=wf, seed=None, resolution="1024x576",
            ) if self.image_engine else {"success": False, "error": "image engine unavailable"}
            if not res.get("success"):
                raise RuntimeError(res.get("error", "generation failed"))
            filename = res.get("filename")
            if not filename:
                raise RuntimeError("no filename returned")

            # Download into the project's scenes/ dir as the saved storyboard image
            saved = self._download_output(filename, Path(project_path) / "scenes", f"{self.jobs[job_id]['shot_id']}.png")
            if not saved:
                raise RuntimeError("failed to save output into the project")
            ok = self._write_shot_fields(project_path, scene_idx, shot_idx, {
                "storyboard_image": saved, "storyboard_status": "generated",
                "_temp_storyboard_image": None,
            })
            with self._lock:
                self.jobs[job_id].update(status="done", result=saved, elapsed_s=round(time.time() - t0, 1))
            logger.info("StudioBridge image job %s done -> %s (%.0fs, state_write=%s)", job_id, saved, time.time() - t0, ok)
        except Exception as e:
            with self._lock:
                self.jobs[job_id].update(status="error", error=str(e), elapsed_s=round(time.time() - t0, 1))
            logger.error("StudioBridge image job %s failed: %s", job_id, e)

    def _download_output(self, comfy_filename: str, dest_dir: Path, dest_name: str):
        """Download a ComfyUI output file into the project dir. Returns saved filename or None."""
        try:
            import requests
            dest_dir.mkdir(parents=True, exist_ok=True)
            r = requests.get(f"{self.comfyui.host}/view", params={"filename": comfy_filename, "type": "output"}, timeout=180)
            if r.status_code != 200:
                return None
            dest = dest_dir / dest_name
            dest.write_bytes(r.content)
            return dest_name
        except Exception as e:
            logger.error("Download failed for %s: %s", comfy_filename, e)
            return None

    # ------------------------------------------------------- video pipeline --
    def generate_shot_video(self, scene_idx: int, shot_idx: int, project_path: str, prompt_override: str = None) -> dict:
        state = self._load_state(project_path)
        scene, shot = self._get_shot(state, scene_idx, shot_idx)
        if not shot:
            return {"error": f"Scene {scene_idx} shot {shot_idx} not found on disk."}
        if not shot.get("storyboard_image"):
            return {"error": "This shot has no approved storyboard image yet — generate the image first; i2v needs the keyframe."}
        prompt = prompt_override or shot.get("video_prompt") or shot.get("action")
        if not prompt:
            return {"error": "Shot has no video_prompt or action text."}

        keyframe = Path(project_path) / "scenes" / shot["storyboard_image"]
        if not keyframe.exists():
            return {"error": f"Keyframe file missing on disk: {keyframe}"}

        job_id = uuid.uuid4().hex[:10]
        with self._lock:
            self.jobs[job_id] = {
                "job_id": job_id, "type": "video", "shot_id": shot.get("shot_id", "?"),
                "scene": scene_idx, "shot": shot_idx, "status": "queued",
                "workflow": self._workflow_for("i2v"), "queued_at": datetime.now().isoformat(timespec="seconds"),
            }
        t = threading.Thread(target=self._run_video_job, args=(job_id, scene_idx, shot_idx, project_path, prompt, str(keyframe)), daemon=True)
        t.start()
        return {
            "job_id": job_id, "status": "queued", "shot_id": shot.get("shot_id"),
            "workflow": self.jobs[job_id]["workflow"],
            "note": "Video render takes minutes. Check with get_generation_progress. The web UI needs a refresh to show it.",
        }

    def _run_video_job(self, job_id, scene_idx, shot_idx, project_path, prompt, keyframe_path):
        with self._lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = time.time()
        t0 = time.time()
        try:
            wf = self.jobs[job_id]["workflow"]
            res = self.comfyui.generate_with_workflow(prompt, wf, input_images=[keyframe_path])
            if not res.get("success"):
                raise RuntimeError(res.get("error", "video generation failed"))
            filename = res.get("filename")
            if not filename:
                raise RuntimeError("no filename returned")
            sub = (res.get("subfolder") or "").strip("/")
            src = filename if not sub else f"{sub}/{filename}"
            saved = self._download_output(src, Path(project_path) / "videos", f"{self.jobs[job_id]['shot_id']}.mp4")
            if not saved:
                raise RuntimeError("failed to save video into the project")
            ok = self._write_shot_fields(project_path, scene_idx, shot_idx, {
                "video_clip": saved, "video_status": "rendered", "_temp_video_clip": None,
            })
            with self._lock:
                self.jobs[job_id].update(status="done", result=saved, elapsed_s=round(time.time() - t0, 1))
            logger.info("StudioBridge video job %s done -> %s (%.0fs, state_write=%s)", job_id, saved, time.time() - t0, ok)
        except Exception as e:
            with self._lock:
                self.jobs[job_id].update(status="error", error=str(e), elapsed_s=round(time.time() - t0, 1))
            logger.error("StudioBridge video job %s failed: %s", job_id, e)

    # ------------------------------------------------------------ approvals --
    def approve_shot_image(self, scene_idx: int, shot_idx: int, project_path: str) -> dict:
        state = self._load_state(project_path)
        scene, shot = self._get_shot(state, scene_idx, shot_idx)
        if not shot:
            return {"error": "Shot not found on disk."}
        if not shot.get("storyboard_image"):
            return {"error": "Nothing to approve — no storyboard image on this shot yet."}
        ok = self._write_shot_fields(project_path, scene_idx, shot_idx, {"storyboard_status": "approved"})
        if not ok:
            return {"error": "Failed to write approval to disk."}
        return {"success": True, "shot_id": shot.get("shot_id"), "status": "approved"}

    def set_shot_video_prompt(self, scene_idx: int, shot_idx: int, project_path: str, prompt: str) -> dict:
        if not prompt or not prompt.strip():
            return {"error": "Empty prompt."}
        state = self._load_state(project_path)
        scene, shot = self._get_shot(state, scene_idx, shot_idx)
        if not shot:
            return {"error": "Shot not found on disk."}
        ok = self._write_shot_fields(project_path, scene_idx, shot_idx, {"video_prompt": prompt.strip()})
        if not ok:
            return {"error": "Failed to write prompt to disk."}
        return {"success": True, "shot_id": shot.get("shot_id"), "video_prompt": prompt.strip()}

    # ------------------------------------------------------------- inspect --
    def list_storyboard(self, project_path: str, scene_idx: int = None) -> dict:
        state = self._load_state(project_path)
        scenes = ((state.get("screenplayData") or {}).get("scenes")) or []
        out = []
        for si, sc in enumerate(scenes):
            if scene_idx is not None and si != scene_idx:
                continue
            for hi, sh in enumerate(sc.get("shots") or []):
                out.append({
                    "scene": si, "shot": hi, "shot_id": sh.get("shot_id"),
                    "image_status": sh.get("storyboard_status"),
                    "has_image": bool(sh.get("storyboard_image")),
                    "has_video": bool(sh.get("video_clip") or sh.get("_temp_video_clip")),
                    "has_video_prompt": bool(sh.get("video_prompt")),
                    "action": (sh.get("action") or "")[:110],
                })
        return {"shots": out, "count": len(out)}

    def get_shot_detail(self, scene_idx: int, shot_idx: int, project_path: str) -> dict:
        state = self._load_state(project_path)
        scene, shot = self._get_shot(state, scene_idx, shot_idx)
        if not shot:
            return {"error": "Shot not found on disk."}
        keep = ["shot_id", "shot_type", "action", "dialogue", "storyboard_prompt", "storyboard_status",
                "storyboard_image", "video_prompt", "video_status", "video_clip", "camera_language",
                "camera_motion", "lens_suggestion", "lighting_language", "emotion", "atmosphere",
                "cinematic_composition", "characters_present", "_duration_sec"]
        return {"scene": scene_idx, "shot": shot_idx, **{k: shot.get(k) for k in keep}}

    def queue_status(self) -> dict:
        try:
            q = self.comfyui.get_queue() if self.comfyui else {}
            running = (q.get("queue_running") or [])
            pending = (q.get("queue_pending") or [])
            with self._lock:
                mine = [j for j in self.jobs.values() if j.get("status") in ("queued", "running")]
            return {
                "comfyui_running": len(running), "comfyui_pending": len(pending),
                "agent_jobs_active": len(mine),
                "agent_jobs": [{"job_id": j["job_id"], "type": j["type"], "shot_id": j.get("shot_id"), "status": j["status"]} for j in mine],
            }
        except Exception as e:
            return {"error": f"queue check failed: {e}"}
