import json
import os
import time
import logging
import subprocess
import signal
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any
from threading import Thread

logger = logging.getLogger("comfyui_client")


class ComfyUIClient:
    def __init__(self, config_path: str = None):
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "comfyui_workflows.json"
        self.config = self._load_config(config_path)
        self.host = self.config.get("connection", {}).get("local", {}).get("host", "http://localhost:8188")
        self.client_id = "ultimate_ai_film_studio"
        self.queue = []
        self.history = []
        self._proc: Optional[subprocess.Popen] = None
        self._comfyui_path: Optional[str] = None

    def _load_config(self, config_path: str) -> Dict:
        """Load ComfyUI workflow configuration."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"categories": {}}

    def set_host(self, host: str):
        """Set ComfyUI host URL."""
        self.host = host

    def test_connection(self) -> Dict:
        """Test connection to ComfyUI."""
        try:
            response = requests.get(f"{self.host}/system_stats", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected to ComfyUI"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def get_categories(self) -> Dict:
        """Get all available workflow categories."""
        categories = self.config.get("categories", {})
        result = {}
        for cat_key, cat_data in categories.items():
            result[cat_key] = {
                "name": cat_data.get("name"),
                "models": {}
            }
            if "models" in cat_data:
                for model_key, model_data in cat_data["models"].items():
                    result[cat_key]["models"][model_key] = {
                        "name": model_data.get("name"),
                        "params": model_data.get("params", [])
                    }
            elif "types" in cat_data:
                result[cat_key]["types"] = {}
                for type_key, type_data in cat_data["types"].items():
                    result[cat_key]["types"][type_key] = {
                        "name": type_data.get("name"),
                        "models": {}
                    }
                    for model_key, model_data in type_data.get("models", {}).items():
                        result[cat_key]["types"][type_key]["models"][model_key] = {
                            "name": model_data.get("name"),
                            "params": model_data.get("params", [])
                        }
        return result

    def get_workflow_info(self, category: str, model: str, video_type: str = None) -> Dict:
        """Get workflow information for a specific model."""
        categories = self.config.get("categories", {})

        if category in categories:
            cat_data = categories[category]
            if "models" in cat_data and model in cat_data["models"]:
                return cat_data["models"][model]
            if "types" in cat_data and video_type in cat_data["types"]:
                types_data = cat_data["types"][video_type]
                if "models" in types_data and model in types_data["models"]:
                    return types_data["models"][model]

        return {}

    def get_available_checkpoints(self) -> List[str]:
        """Get list of available checkpoint models from ComfyUI."""
        try:
            response = requests.get(f"{self.host}/api/object_info/CheckpointLoaderSimple", timeout=10)
            if response.status_code == 200:
                data = response.json()
                ckpt_info = data.get("CheckpointLoaderSimple", {})
                inputs = ckpt_info.get("input", {})
                required = inputs.get("required", {})
                ckpt_list = required.get("ckpt_name", [])
                if ckpt_list and isinstance(ckpt_list[0], list):
                    return ckpt_list[0]
                return ckpt_list if isinstance(ckpt_list, list) else []
        except Exception as e:
            print(f"Error getting checkpoints: {e}")
        return []

    def queue_prompt(self, workflow: Dict) -> Optional[str]:
        """Queue a prompt for generation."""
        try:
            prompt_payload = {
                "prompt": workflow,
                "client_id": self.client_id
            }
            response = requests.post(f"{self.host}/prompt", json=prompt_payload, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data.get("prompt_id")
        except Exception as e:
            print(f"Error queueing prompt: {e}")
        return None

    def get_progress(self) -> Dict:
        """Get current generation progress from ComfyUI."""
        try:
            resp = requests.get(f"{self.host}/progress", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "running": data.get("running", False),
                    "current": data.get("current", 0),
                    "max": data.get("max", 25)
                }
        except Exception:
            pass
        return {"running": False, "current": 0, "max": 0}

    def get_queue(self) -> Dict:
        """Get current queue status."""
        try:
            response = requests.get(f"{self.host}/queue", timeout=5)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return {"queue_running": [], "queue_pending": []}

    def get_history(self, prompt_id: str) -> Optional[Dict]:
        """Get history for a prompt."""
        try:
            response = requests.get(f"{self.host}/history/{prompt_id}", timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception:
            pass
        return None

    def get_output(self, prompt_id: str, timeout: int = 300) -> Optional[Dict]:
        """Wait for and get output from a prompt."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            history = self.get_history(prompt_id)
            if history and prompt_id in history:
                prompt_data = history[prompt_id]
                if "outputs" in prompt_data and prompt_data["outputs"]:
                    return prompt_data["outputs"]
                # Check for error messages in status
                if "status" in prompt_data:
                    status = prompt_data["status"]
                    if "messages" in status:
                        for msg_type, msg_data in status["messages"]:
                            if msg_type == "execution_error" or msg_type == "error":
                                return {"_error": str(msg_data)}
                # No outputs and no pending - might be done with error
                if "outputs" in prompt_data:
                    return prompt_data["outputs"]
            time.sleep(2)
        return None

    def upload_image(self, image_path: str) -> Optional[str]:
        """Upload an image to ComfyUI."""
        try:
            with open(image_path, 'rb') as f:
                files = {'image': f}
                response = requests.post(f"{self.host}/upload/image", files=files, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    name = data.get("name")
                    print(f"[Upload] '{image_path}' -> ComfyUI input as '{name}'")
                    return name
                else:
                    print(f"[Upload] Failed: HTTP {response.status_code} - {response.text[:200]}")
        except Exception as e:
            print(f"[Upload] Error uploading {image_path}: {e}")
        return None

    def download_output(self, filename: str, output_dir: str) -> Optional[str]:
        """Download output file from ComfyUI."""
        try:
            response = requests.get(f"{self.host}/view?filename={filename}", timeout=60)
            if response.status_code == 200:
                output_path = Path(output_dir) / filename
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                return str(output_path)
        except Exception as e:
            print(f"Error downloading output: {e}")
        return None

    def list_outputs(self) -> List[str]:
        """List available output files."""
        try:
            response = requests.get(f"{self.host}/files?filename=output", timeout=10)
            if response.status_code == 200:
                data = response.json()
                return [f.get("name") for f in data.get("names", [])]
        except Exception:
            pass
        return []

    def generate_image(self, prompt: str, model: str, width: int = 1024, height: int = 1024, seed: int = -1, **kwargs) -> Dict:
        """Generate an image using a simple workflow."""
        if seed == -1:
            import time
            seed = int(time.time() * 1000) % 1000000

        workflow = self._create_image_workflow(prompt, model, width, height, seed, **kwargs)
        
        if not workflow:
            return {"success": False, "error": "Failed to create workflow - ComfyUI not available or no models installed"}
        
        prompt_id = self.queue_prompt(workflow)
        if not prompt_id:
            return {"success": False, "error": "Failed to queue prompt - check ComfyUI connection and model availability"}

        output = self.get_output(prompt_id, timeout=300)
        if not output:
            return {"success": False, "error": "Generation timeout - ComfyUI may be busy or model is slow"}

        for node_id, node_output in output.items():
            if "images" in node_output:
                images = node_output["images"]
                if images:
                    return {"success": True, "filename": images[0].get("filename")}

        return {"success": False, "error": "No output generated - check ComfyUI output folder"}

    def _create_image_workflow(self, prompt: str, model: str, width: int, height: int, seed: int, **kwargs) -> Dict:
        """Create a basic image generation workflow."""
        # Use the model name directly - user must select a model that's installed
        # If model contains .safetensors, use it as checkpoint name
        # Otherwise, assume it's already a valid checkpoint name
        checkpoint_name = model if model else "model.safetensors"
        
        workflow = {}
        
        # 1. Load checkpoint
        workflow["1"] = {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint_name}
        }
        
        # 2. Positive prompt
        workflow["2"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 0]}
        }
        
        # 3. Negative prompt
        workflow["3"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "low quality, blurry, distorted, watermark, text, logo", "clip": ["1", 1]}
        }
        
        # 4. Empty latent
        workflow["4"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            }
        }
        
        # 5. Sampler
        workflow["5"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 25,
                "cfg": 7,
                "sampler_name": "euler",
                "scheduler": "normal",
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0]
            }
        }
        
        # 6. VAE Decode
        workflow["6"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]}
        }
        
        # 7. Save Image
        workflow["7"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "ultimate_ai_film_studio", "images": ["6", 0]}
        }
        
        return workflow

    def generate_with_workflow(self, prompt: str, workflow_name: str, negative_prompt: str = None, seed: int = None, input_images: List[str] = None, aspect_ratio: str = None, resolution: str = None, steps: int = None) -> Dict:
        """Generate using a custom workflow JSON from the workflows folder.
        UAIImageSlot nodes get their image_path set directly from input_images paths.
        Unused slots default to empty string (1x1 black image = bypass)."""
        base = Path(__file__).parent.parent / "workflows"
        workflow_path = base / workflow_name
        if not workflow_path.exists():
            workflow_path = base / (workflow_name + ".json")
        if not workflow_path.exists():
            return self.generate_image(prompt, "model.safetensors", 1024, 1024)

        try:
            with open(workflow_path, 'r') as f:
                workflow = json.load(f)

            debug_log = []
            print(f"[generate_with_workflow] workflow={workflow_name}, prompt='{prompt[:60]}...'")
            print(f"[generate_with_workflow] seed={seed}, steps={steps}, input_images={input_images}, resolution={resolution}")
            # Phase 1: set prompt text
            # If the workflow has a PrimitiveStringMultiline node, use it as the prompt source
            # (only overwrite CLIPTextEncode direct strings when there's no PrimitiveStringMultiline)
            has_psm = any(
                isinstance(n, dict) and n.get("class_type") == "PrimitiveStringMultiline"
                for n in workflow.values()
            )
            debug_log.append(f"has PrimitiveStringMultiline: {has_psm}")
            if has_psm:
                # Update the PSM value — prompt flows through connection to CLIPTextEncode
                for nid, nd in workflow.items():
                    if isinstance(nd, dict) and nd.get("class_type") == "PrimitiveStringMultiline" and "value" in nd.get("inputs", {}):
                        nd["inputs"]["value"] = prompt
                        debug_log.append(f"Set PrimitiveStringMultiline {nid} value: '{prompt[:50]}...'")
                        break
            else:
                # No PSM — trace LTXVConditioning to find the positive CLIPTextEncode
                # This avoids overwriting the negative CLIPTextEncode
                positive_nid = None
                for nid, nd in workflow.items():
                    if not isinstance(nd, dict):
                        continue
                    if nd.get("class_type") == "LTXVConditioning":
                        pos = nd.get("inputs", {}).get("positive")
                        if isinstance(pos, list) and len(pos) >= 1:
                            candidate = str(pos[0])
                            if isinstance(workflow.get(candidate), dict) and workflow[candidate].get("class_type") == "CLIPTextEncode":
                                positive_nid = candidate
                                break
                if positive_nid:
                    workflow[positive_nid]["inputs"]["text"] = prompt
                    debug_log.append(f"Overwrote positive CLIPTextEncode {positive_nid}: '{prompt[:50]}...'")
                else:
                    debug_log.append("No LTXVConditioning found, overwriting all CLIPTextEncode string nodes")
                    for nid, nd in workflow.items():
                        if not isinstance(nd, dict):
                            continue
                        inputs = nd.get("inputs", {})
                        if nd.get("class_type") == "CLIPTextEncode" and "text" in inputs and isinstance(inputs["text"], str):
                            inputs["text"] = prompt
                            debug_log.append(f"Overwrote CLIPTextEncode {nid}: '{prompt[:50]}...'")

            # Phase 2: inject image paths into UAIImageSlot nodes
            slot_nodes = []
            for node_id, node_data in workflow.items():
                if not isinstance(node_data, dict):
                    continue
                if node_data.get("class_type") == "UAIImageSlot":
                    slot_nodes.append(node_id)
            slot_nodes.sort(key=int)
            debug_log.append(f"Found {len(slot_nodes)} UAIImageSlot nodes: {slot_nodes}")
            if input_images and len(input_images) == 1 and len(slot_nodes) > 1:
                debug_log.append(f"Duplicating single input image across all {len(slot_nodes)} UAIImageSlot nodes")
                input_images = input_images * len(slot_nodes)
            for idx, nid in enumerate(slot_nodes):
                if input_images and idx < len(input_images):
                    img_path = input_images[idx]
                    debug_log.append(f"Slot {nid} <- {img_path}")
                    workflow[nid]["inputs"]["image_path"] = img_path
                else:
                    debug_log.append(f"Slot {nid} <- (empty, bypass)")
                    workflow[nid]["inputs"]["image_path"] = ""

            # Phase 2b: inject image into LoadImage nodes
            if not slot_nodes and input_images:
                load_image_nodes = []
                for node_id, node_data in workflow.items():
                    if not isinstance(node_data, dict):
                        continue
                    if node_data.get("class_type") == "LoadImage":
                        load_image_nodes.append(node_id)
                load_image_nodes.sort(key=int)
                debug_log.append(f"Found {len(load_image_nodes)} LoadImage nodes: {load_image_nodes}")
                if input_images and len(input_images) == 1 and len(load_image_nodes) > 1:
                    debug_log.append(f"Duplicating single input image across all {len(load_image_nodes)} LoadImage nodes")
                    input_images = input_images * len(load_image_nodes)
                for idx, nid in enumerate(load_image_nodes):
                    if idx < len(input_images):
                        img_path = input_images[idx]
                        if not Path(img_path).exists():
                            err = f"Input image not found: {img_path}"
                            logger.error(err)
                            return {"success": False, "error": err}
                        uploaded_name = self.upload_image(img_path)
                        if not uploaded_name:
                            err = f"Failed to upload input image to ComfyUI: {img_path}"
                            logger.error(err)
                            return {"success": False, "error": err}
                        debug_log.append(f"LoadImage {nid} <- uploaded: {uploaded_name}")
                        workflow[nid]["inputs"]["image"] = uploaded_name
                    else:
                        debug_log.append(f"LoadImage {nid} <- no input image provided")

            # Phase 3: set seed
            if seed is not None:
                for node_id, node_data in workflow.items():
                    if not isinstance(node_data, dict):
                        continue
                    ct = node_data.get("class_type", "")
                    inputs = node_data.get("inputs", {})
                    if ct == "KSampler" and "seed" in inputs:
                        inputs["seed"] = seed
                    if ct == "RandomNoise" and "noise_seed" in inputs:
                        inputs["noise_seed"] = seed

            # Phase 3b: set steps
            if steps is not None:
                for node_id, node_data in workflow.items():
                    if not isinstance(node_data, dict):
                        continue
                    ct = node_data.get("class_type", "")
                    inputs = node_data.get("inputs", {})
                    if "steps" in inputs:
                        try:
                            inputs["steps"] = int(steps)
                            debug_log.append(f"Set steps for {ct} {node_id} to {steps}")
                        except (ValueError, TypeError):
                            pass

            # Phase 4: override resolution
            if resolution:
                try:
                    parts = resolution.lower().split("x")
                    if len(parts) == 2:
                        w, h = int(parts[0]), int(parts[1])
                        debug_log.append(f"Overriding resolution to {w}x{h}")
                        for node_id, node_data in workflow.items():
                            if not isinstance(node_data, dict):
                                continue
                            ct = node_data.get("class_type", "")
                            inputs = node_data.get("inputs", {})
                            if ct in ("EmptyFlux2LatentImage", "Flux2Scheduler"):
                                inputs["width"] = w
                                inputs["height"] = h
                                debug_log.append(f"{ct} {node_id} -> {w}x{h}")
                except (ValueError, IndexError):
                    debug_log.append(f"Failed to parse resolution: {resolution}")

            # Phase 5: LoRA substitution
            try:
                obj_info = requests.get(f"{self.host}/object_info/LoraLoader", timeout=10).json()
                lora_field = obj_info.get("LoraLoader", {}).get("input", {}).get("required", {}).get("lora_name", [])
                if isinstance(lora_field, list) and len(lora_field) > 0 and isinstance(lora_field[0], list):
                    avail_loras = lora_field[0]
                else:
                    avail_loras = lora_field if isinstance(lora_field, list) else []
            except Exception as e:
                debug_log.append(f"LoRA fetch error: {e}")
                avail_loras = []
            for node_id, node_data in workflow.items():
                if not isinstance(node_data, dict):
                    continue
                ct = node_data.get("class_type", "")
                if ct == "LoraLoader":
                    needed = node_data["inputs"].get("lora_name", "")
                    if needed and needed not in avail_loras and isinstance(avail_loras, list) and avail_loras:
                        subfolder = needed.rsplit("\\", 1)[0] if "\\" in needed else ""
                        candidates = [l for l in avail_loras if subfolder and l.startswith(subfolder + "\\")]
                        if not candidates:
                            candidates = avail_loras
                        substitute = candidates[0]
                        debug_log.append(f"LoraLoader substitution: '{needed}' -> '{substitute}' (strength=0)")
                        node_data["inputs"]["lora_name"] = substitute
                        node_data["inputs"]["strength_model"] = 0.0
                        node_data["inputs"]["strength_clip"] = 0.0
                    elif needed in avail_loras:
                        debug_log.append(f"LoraLoader '{needed}' found, keeping as-is")
                elif ct == "LoraLoaderModelOnly":
                    needed = node_data["inputs"].get("lora_name", "")
                    if needed and needed not in avail_loras and isinstance(avail_loras, list) and avail_loras:
                        subfolder = needed.rsplit("\\", 1)[0] if "\\" in needed else ""
                        candidates = [l for l in avail_loras if subfolder and l.startswith(subfolder + "\\")]
                        if not candidates:
                            candidates = avail_loras
                        substitute = candidates[0]
                        debug_log.append(f"LoraLoaderModelOnly substitution: '{needed}' -> '{substitute}' (strength=0)")
                        node_data["inputs"]["lora_name"] = substitute
                        node_data["inputs"]["strength_model"] = 0.0
                    elif needed in avail_loras:
                        debug_log.append(f"LoraLoaderModelOnly '{needed}' found, keeping as-is")

            # Log final state of key nodes before queueing
            for nid in ["269", "149", "121", "110", "320:319", "320:303", "320:313"]:
                if nid in workflow:
                    n = workflow[nid]
                    inp = n.get("inputs", {})
                    ct = n.get("class_type", "")
                    if "image" in inp:
                        debug_log.append(f"[final] {nid} ({ct}): image='{inp['image']}'")
                    elif "value" in inp:
                        debug_log.append(f"[final] {nid} ({ct}): value='{str(inp['value'])[:60]}'")
                    elif "text" in inp:
                        debug_log.append(f"[final] {nid} ({ct}): text='{str(inp['text'])[:60]}'")
            prompt_id = self.queue_prompt(workflow)
            if not prompt_id:
                return {"success": False, "error": "Failed to queue workflow on ComfyUI"}

            logger.info("ComfyUI queued [%s] id=%s", workflow_name, prompt_id)

            gstart = time.time()
            output = self.get_output(prompt_id, timeout=600)
            elapsed = time.time() - gstart
            if not output:
                logger.warning("ComfyUI timeout [%s] after %.0fs", workflow_name, elapsed)
                return {"success": False, "error": "Generation timeout"}

            for nid, nout in output.items():
                if "images" in nout and nout["images"]:
                    img = nout["images"][0]
                    fname = img.get("filename", "unknown")
                    logger.info("ComfyUI done [%s] → %s [%.1fs]", workflow_name, fname, elapsed)
                    return {"success": True, "filename": fname, "subfolder": img.get("subfolder", "")}
                if "gifs" in nout and nout["gifs"]:
                    gif = nout["gifs"][0]
                    fname = gif.get("filename", "unknown")
                    logger.info("ComfyUI done [%s] → %s [%.1fs]", workflow_name, fname, elapsed)
                    return {"success": True, "filename": fname, "subfolder": gif.get("subfolder", "")}

            if isinstance(output, dict) and "_error" in output:
                logger.error("ComfyUI error [%s]: %s", workflow_name, output['_error'])
                return {"success": False, "workflow_errored": True, "error": f"ComfyUI error: {output['_error']}"}

            logger.warning("ComfyUI no output [%s]", workflow_name)
            return {"success": False, "error": "No output generated"}
        except Exception as e:
            return {"success": False, "error": f"Workflow error: {str(e)}"}

    def _upload_placeholder(self) -> Optional[str]:
        """Upload a small blank placeholder image to ComfyUI for LoadImage nodes."""
        try:
            from PIL import Image
            import io
            img = Image.new('RGB', (64, 64), color=(72, 72, 72))
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            resp = requests.post(f"{self.host}/upload/image", files={'image': ('blank_input.png', buf, 'image/png')}, timeout=15)
            if resp.status_code == 200:
                name = resp.json().get("name")
                if name:
                    return name
                # Sometimes ComfyUI returns subfolder/name
                if "subfolder" in resp.json():
                    sub = resp.json()["subfolder"]
                    if sub:
                        return sub + "/" + name
            # Try uploading to a predictable name
            buf.seek(0)
            resp = requests.post(f"{self.host}/upload/image", files={'image': ('placeholder.png', buf, 'image/png')}, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("name")
        except ImportError:
            # Fallback if PIL not available
            try:
                import struct, zlib
                def _png(w, h, r, g, b):
                    def chunk(t, d):
                        c = t + d
                        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
                    raw = b''
                    for _ in range(h):
                        raw += b'\x00' + bytes([r, g, b] * w)
                    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
                png = _png(64, 64, 72, 72, 72)
                resp = requests.post(f"{self.host}/upload/image", files={'image': ('blank_input.png', png, 'image/png')}, timeout=15)
                if resp.status_code == 200:
                    return resp.json().get("name")
            except Exception:
                pass
        except Exception:
            pass
        return None

    def generate_video(self, prompt: str, model: str, input_image: str = None, width: int = 1280, height: int = 720, frames: int = 81, fps: int = 24, **kwargs) -> Dict:
        """Generate a video using a simple workflow."""
        workflow = self._create_video_workflow(prompt, model, input_image, width, height, frames, fps, **kwargs)
        prompt_id = self.queue_prompt(workflow)
        if not prompt_id:
            return {"success": False, "error": "Failed to queue prompt"}

        output = self.get_output(prompt_id, timeout=600)
        if not output:
            return {"success": False, "error": "Generation timeout"}

        for node_id, node_output in output.items():
            if "images" in node_output:
                images = node_output["images"]
                if images:
                    return {"success": True, "filename": images[0].get("filename")}

        return {"success": False, "error": "No output generated"}

    def generate_image_ip2p(self, prompt: str, input_image: str, model: str = None, width: int = 1024, height: int = 1024, seed: int = -1, denoise: float = 0.7, **kwargs) -> Dict:
        """Generate image using Image-to-Image (IP2P) approach."""
        if seed == -1:
            import time
            seed = int(time.time() * 1000) % 1000000
        
        checkpoint_name = model if model else "juggernaut_xl.safetensors"
        
        workflow = {}
        
        # 1. Load checkpoint
        workflow["1"] = {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint_name}
        }
        
        # 2. Load input image
        workflow["2"] = {
            "class_type": "LoadImage",
            "inputs": {"image": "input_image.png"}
        }
        
        # 3. Positive prompt
        workflow["3"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 0]}
        }
        
        # 4. Negative prompt
        workflow["4"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "blurry, low quality, distorted, deformed", "clip": ["1", 0]}
        }
        
        # 5. VAE
        workflow["5"] = {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "ae.safetensors"}
        }
        
        # 6. KSampler with image input
        workflow["6"] = {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 25,
                "cfg": 7,
                "sampler_name": "euler",
                "scheduler": "normal",
                "positive": ["3", 0],
                "negative": ["4", 0],
                "latent_image": ["9", 0]
            }
        }
        
        # 7. VAE Encode (for image)
        workflow["7"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["2", 0], "vae": ["5", 0]}
        }
        
        # 8. Empty latent
        workflow["8"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1}
        }
        
        # 9. Blend latent (image + noise) - simplified approach
        workflow["9"] = {
            "class_type": "LatentAdd",
            "inputs": {"latent1": ["8", 0], "latent2": ["7", 0]}
        }
        
        # 10. VAE Decode
        workflow["10"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["5", 0]}
        }
        
        # 11. Save Image
        workflow["11"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "ultimate_ai_ip2p", "images": ["10", 0]}
        }
        
        # Upload the input image first
        try:
            import base64
            img_data = base64.b64decode(input_image)
            upload_url = f"{self.host}/upload/image"
            files = {'image': ('input_image.png', img_data, 'image/png')}
            resp = requests.post(upload_url, files=files, timeout=30)
        except Exception as e:
            return {"success": False, "error": f"Failed to upload image: {str(e)}"}
        
        prompt_id = self.queue_prompt(workflow)
        if not prompt_id:
            return {"success": False, "error": "Failed to queue prompt - ComfyUI may not support this model for image-to-image"}
        
        output = self.get_output(prompt_id, timeout=300)
        if not output:
            return {"success": False, "error": "Generation timeout - this model may not support image input. Use Text-to-Image instead."}
        
        # Check for ComfyUI error in output
        if "_error" in output:
            error_detail = str(output["_error"])
            if "does not support image" in error_detail.lower():
                return {"success": False, "error": "Cannot read reference image - this model does not support image input. Please use Text-to-Image or switch to an image-capable model."}
            return {"success": False, "error": f"ComfyUI error: {error_detail}"}
        
        for node_id, node_output in output.items():
            if "images" in node_output:
                images = node_output["images"]
                if images:
                    return {"success": True, "filename": images[0].get("filename")}
        
        return {"success": False, "error": "No output generated - this model does not support image input. Use Text-to-Image instead."}

    def _create_video_workflow(self, prompt: str, model: str, input_image: str = None, width: int = 1280, height: int = 720, frames: int = 81, fps: int = 24, **kwargs) -> Dict:
        """Create a basic video generation workflow."""
        workflow = {}
        return workflow

    def interrupt(self) -> bool:
        """Interrupt current generation."""
        try:
            response = requests.post(f"{self.host}/interrupt", timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def clear_queue(self) -> bool:
        """Clear the queue."""
        try:
            response = requests.post(f"{self.host}/queue", json={"clear": True}, timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    # === Subprocess Management ===

    def set_comfyui_path(self, path: str):
        """Set the ComfyUI installation path."""
        self._comfyui_path = path
        self._use_sage_attention = os.path.isfile(
            os.path.join(path, "ComfyUI", "main.py") if path else ""
        )

    def get_portable_path(self) -> Optional[str]:
        """Auto-detect the Easy-Install portable ComfyUI path."""
        base = Path(__file__).parent.parent.parent
        candidates = [
            base / "comfyui" / "ComfyUI",
            base / "ComfyUI" / "ComfyUI",
            base / "comfyui",
        ]
        for c in candidates:
            main_py = c / "main.py" if c.name == "ComfyUI" else (c / "ComfyUI" / "main.py")
            if main_py.is_file():
                return str(c.parent) if c.name == "ComfyUI" else str(c)
        # Check for python_embedded as evidence of Easy-Install
        for c in candidates:
            pipy = c / "python_embedded" / "python.exe"
            if pipy.is_file():
                ci = c / "ComfyUI" / "main.py"
                if ci.is_file():
                    return str(c)
        return None

    def detect_comfyui(self) -> Dict:
        """Detect if ComfyUI is installed on the system."""
        # Check portable (Easy-Install) first
        portable = self.get_portable_path()
        if portable:
            ci_main = os.path.join(portable, "ComfyUI", "main.py")
            pipy = os.path.join(portable, "python_embedded", "python.exe")
            direct_main = os.path.join(portable, "main.py")
            if os.path.isfile(ci_main):
                return {"installed": True, "path": ci_main.rstrip(os.sep + "main.py"),
                        "portable": True, "python_embedded": pipy if os.path.isfile(pipy) else None}
            if os.path.isfile(direct_main):
                return {"installed": True, "path": portable, "portable": False, "python_embedded": None}

        search_dirs = [
            self._comfyui_path,
            os.environ.get("COMFYUI_PATH", ""),
            os.path.join(os.path.expanduser("~"), "ComfyUI"),
            os.path.join(os.path.expanduser("~"), "comfyui"),
            r"C:\ComfyUI",
            r"C:\Program Files\ComfyUI",
            os.path.join(os.path.expanduser("~"), "Documents", "ComfyUI"),
        ]
        search_dirs = [d for d in search_dirs if d]

        for d in search_dirs:
            main_py = os.path.join(d, "main.py")
            if os.path.isfile(main_py):
                return {"installed": True, "path": d, "portable": False, "python_embedded": None}

        # Try `where` / `which` on git clone scenario
        try:
            if os.name == 'nt':
                result = subprocess.run(["where", "main.py"], capture_output=True, text=True, timeout=5, shell=True)
            else:
                result = subprocess.run(["find", "/", "-name", "main.py", "-path", "*/ComfyUI/*"],
                                       capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    if "ComfyUI" in line and "main.py" in line:
                        return {"installed": True, "path": str(Path(line).parent),
                                "portable": False, "python_embedded": None}
        except Exception:
            pass

        return {"installed": False, "error": "ComfyUI not found. Install from https://github.com/comfyanonymous/ComfyUI"}

    def get_comfyui_status(self) -> Dict:
        """Check if ComfyUI is currently running."""
        if self._proc and self._proc.poll() is None:
            return {"running": True, "pid": self._proc.pid, "source": "subprocess"}
        try:
            resp = requests.get(f"{self.host}/system_stats", timeout=3)
            if resp.status_code == 200:
                return {"running": True, "pid": None, "source": "http"}
        except requests.ConnectionError:
            return {"running": False, "error": f"Connection refused at {self.host}"}
        except requests.Timeout:
            return {"running": False, "error": f"Timeout connecting to {self.host}"}
        except Exception as e:
            return {"running": False, "error": str(e)}
        return {"running": False, "error": "Unknown - server returned non-200"}

    def launch_comfyui(self, path: str = None, host: str = "0.0.0.0", port: int = 8188,
                       use_sage_attention: bool = True) -> Dict:
        """Launch ComfyUI as a subprocess."""
        status = self.get_comfyui_status()
        if status.get("running"):
            return {"success": True, "message": "ComfyUI already running", "pid": status.get("pid")}

        comfy_path = path or self._comfyui_path
        if not comfy_path:
            detect = self.detect_comfyui()
            if detect.get("installed"):
                comfy_path = detect["path"]
                self._comfyui_path = comfy_path
                if detect.get("python_embedded"):
                    self._python_embedded = detect["python_embedded"]
            else:
                return {"success": False,
                        "error": "ComfyUI not found. Set the path in Settings or install it."}

        try:
            # Determine launch method
            import math
            pi_exe = getattr(self, '_python_embedded', None)

            # Check for portable Easy-Install structure
            if not pi_exe and comfy_path:
                possible_pe = os.path.join(comfy_path, "python_embedded", "python.exe")
                if os.path.isfile(possible_pe):
                    pi_exe = possible_pe
                    self._python_embedded = possible_pe
                elif os.path.isfile(os.path.join(os.path.dirname(comfy_path), "python_embedded", "python.exe")):
                    pi_exe = os.path.join(os.path.dirname(comfy_path), "python_embedded", "python.exe")
                    self._python_embedded = pi_exe

            # Locate main.py
            if os.path.isfile(os.path.join(comfy_path, "main.py")):
                main_py = os.path.join(comfy_path, "main.py")
            elif os.path.isfile(os.path.join(comfy_path, "ComfyUI", "main.py")):
                main_py = os.path.join(comfy_path, "ComfyUI", "main.py")
                cwd = os.path.join(comfy_path, "ComfyUI")
            elif os.path.isdir(comfy_path) and os.path.isfile(os.path.join(os.path.dirname(comfy_path), "ComfyUI", "main.py")):
                main_py = os.path.join(os.path.dirname(comfy_path), "ComfyUI", "main.py")
                cwd = os.path.join(os.path.dirname(comfy_path), "ComfyUI")
            else:
                return {"success": False, "error": "ComfyUI main.py not found"}

            if 'cwd' not in dir() or not cwd:
                cwd = comfy_path if os.path.isdir(comfy_path) else os.path.dirname(comfy_path)
            if not cwd:
                cwd = os.path.dirname(main_py)

            if pi_exe and os.path.isfile(pi_exe):
                cmd = [pi_exe, "-s", main_py, "--listen", host, "--port", str(port)]
                if use_sage_attention:
                    cmd.append("--use-sage-attention")
                cmd.append("--highvram")
            else:
                cmd = ["python", main_py, "--listen", host, "--port", str(port)]
                if use_sage_attention:
                    cmd.append("--use-sage-attention")

            logger.info(f"Launching ComfyUI: {' '.join(cmd)}")
            self._proc = subprocess.Popen(
                cmd, cwd=cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self._comfyui_path = comfy_path

            # Wait for startup
            timeout_val = 120
            start = time.time()
            while time.time() - start < timeout_val:
                try:
                    resp = requests.get(f"{self.host}/system_stats", timeout=2)
                    if resp.status_code == 200:
                        return {"success": True, "message": "ComfyUI started", "pid": self._proc.pid}
                except Exception:
                    pass
                time.sleep(2)

            return {"success": True, "message": "ComfyUI starting (may take longer)",
                    "pid": self._proc.pid}
        except FileNotFoundError:
            return {"success": False, "error": "Python not found. Ensure Python is in PATH."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def stop_comfyui(self) -> Dict:
        """Stop ComfyUI subprocess."""
        if self._proc:
            try:
                if os.name == 'nt':
                    self._proc.terminate()
                else:
                    os.kill(self._proc.pid, signal.SIGTERM)
                self._proc.wait(timeout=15)
                self._proc = None
                return {"success": True, "message": "ComfyUI stopped"}
            except Exception as e:
                try:
                    self._proc.kill()
                    self._proc = None
                    return {"success": True, "message": "ComfyUI force stopped"}
                except Exception:
                    return {"success": False, "error": str(e)}
        return {"success": True, "message": "Not running"}

    # === Model Path Management ===

    def set_external_models_path(self, external_path: str) -> Dict:
        """Set an external ComfyUI models folder via extra_model_paths.yaml."""
        if not external_path or not os.path.isdir(external_path):
            return {"success": False, "error": "Invalid external models path"}

        base = Path(__file__).parent.parent
        extra_yaml = base / "extra_model_paths.yaml"

        comfy_path = self._comfyui_path or base.parent / "comfyui"
        # Create the YAML config to redirect model paths
        yaml_content = f"""# ComfyUI extra model paths - managed by Ultimate AI Film Studio
{comfy_path}:
    base_path: {comfy_path}

    checkpoints: {external_path}/checkpoints/
    configs: {external_path}/configs/
    loras: {external_path}/loras/
    loras_1: {external_path}/loras/
    upscale_models: {external_path}/upscale_models/
    clip_vision: {external_path}/clip_vision/
    clip: {external_path}/clip/
    text_encoders: {external_path}/text_encoders/
    diffusion_models: {external_path}/diffusion_models/
    vae: {external_path}/vae/
    controlnet: {external_path}/controlnet/
    gligen: {external_path}/gligen/
    hypernetworks: {external_path}/hypernetworks/
    style_models: {external_path}/style_models/
    embeddings: {external_path}/embeddings/
    unet: {external_path}/unet/
    ipadapter: {external_path}/ipadapter/
    photomaker: {external_path}/photomaker/
    ultralytics: {external_path}/ultralytics/
    sams: {external_path}/sams/
    llm: {external_path}/llm/
"""
        try:
            extra_yaml.write_text(yaml_content, encoding='utf-8')
            self._external_models_path = external_path
            return {"success": True, "message": f"External models path set to {external_path}", "path": external_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_external_models_path(self) -> Optional[str]:
        """Get the current external models path from settings or extra_model_paths.yaml."""
        if hasattr(self, '_external_models_path') and self._external_models_path:
            return self._external_models_path
        base = Path(__file__).parent.parent
        extra_yaml = base / "extra_model_paths.yaml"
        if extra_yaml.exists():
            try:
                content = extra_yaml.read_text(encoding='utf-8')
                for line in content.split('\n'):
                    if 'base_path:' in line:
                        parts = line.split(':')
                        if len(parts) > 1:
                            path = parts[1].strip()
                            if os.path.isdir(path):
                                return path
            except Exception:
                pass
        return None

    def clear_external_models_path(self) -> Dict:
        """Remove the extra_model_paths.yaml file."""
        base = Path(__file__).parent.parent
        extra_yaml = base / "extra_model_paths.yaml"
        if extra_yaml.exists():
            try:
                extra_yaml.unlink()
                self._external_models_path = None
                return {"success": True, "message": "External model path cleared"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        return {"success": True, "message": "No external path configured"}

    # === Model Downloader ===

    COMFYUI_MODEL_TYPES = {
        "checkpoints": {"folder": "checkpoints", "extensions": [".safetensors", ".ckpt", ".pt"]},
        "diffusion_models": {"folder": "diffusion_models", "extensions": [".safetensors", ".ckpt", ".pt"]},
        "loras": {"folder": "loras", "extensions": [".safetensors", ".ckpt"]},
        "vae": {"folder": "vae", "extensions": [".safetensors", ".pt"]},
        "text_encoders": {"folder": "text_encoders", "extensions": [".safetensors", ".pt"]},
        "clip": {"folder": "clip", "extensions": [".safetensors", ".pt"]},
        "clip_vision": {"folder": "clip_vision", "extensions": [".safetensors", ".pt"]},
        "controlnet": {"folder": "controlnet", "extensions": [".safetensors", ".pt", ".pth"]},
        "upscale_models": {"folder": "upscale_models", "extensions": [".safetensors", ".pt", ".pth"]},
        "unet": {"folder": "unet", "extensions": [".safetensors", ".pt"]},
        "ipadapter": {"folder": "ipadapter", "extensions": [".safetensors", ".pt"]},
    }

    def search_huggingface_models(self, query: str = "", model_type: str = "checkpoints",
                                   category: str = "", limit: int = 30) -> List[Dict]:
        """Search HuggingFace for ComfyUI-compatible models."""
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            search_term = query or model_type
            results = api.list_models(
                search=search_term,
                sort="downloads",
                direction=-1,
                limit=limit * 2
            )
            models = []
            seen_repos = set()
            for model in results:
                if len(models) >= limit:
                    break
                repo_id = model.modelId
                if repo_id in seen_repos:
                    continue
                seen_repos.add(repo_id)
                try:
                    files = api.list_repo_files(repo_id)
                except Exception:
                    continue
                type_info = self.COMFYUI_MODEL_TYPES.get(model_type, self.COMFYUI_MODEL_TYPES["checkpoints"])
                extensions = type_info["extensions"]
                matching_files = [f for f in files if any(f.endswith(ext) for ext in extensions)]
                for fname in matching_files:
                    if len(models) >= limit:
                        break
                    size = 0
                    try:
                        meta = api.model_info(repo_id, files_metadata=True)
                        for sibling in meta.siblings:
                            if sibling.rfilename == fname and sibling.size:
                                size = sibling.size
                                break
                    except Exception:
                        pass
                    size_gb = round(size / (1024**3), 2) if size > 0 else 0
                    models.append({
                        "repo": repo_id,
                        "filename": fname,
                        "size_bytes": size,
                        "size_gb": size_gb,
                        "model_type": model_type,
                        "downloads": getattr(model, "downloads", 0) or 0
                    })
            return models
        except ImportError:
            return [{"error": "huggingface-hub not installed"}]
        except Exception as e:
            return [{"error": str(e)}]

    def download_model(self, repo: str, filename: str, model_type: str = "checkpoints") -> Dict:
        """Download a model file from HuggingFace to the correct ComfyUI folder."""
        try:
            from huggingface_hub import hf_hub_download

            type_info = self.COMFYUI_MODEL_TYPES.get(model_type, self.COMFYUI_MODEL_TYPES["checkpoints"])
            target_folder = type_info["folder"]

            comfy_path = self._comfyui_path
            if not comfy_path:
                detect = self.detect_comfyui()
                if detect.get("installed"):
                    comfy_path = detect["path"]
                else:
                    return {"success": False, "error": "ComfyUI path not found"}

            dest_dir = Path(comfy_path) / "models" / target_folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / filename

            if dest_path.exists():
                return {"success": True, "message": f"Already downloaded: {filename}", "path": str(dest_path)}

            downloaded = hf_hub_download(
                repo_id=repo,
                filename=filename,
                local_dir=str(dest_dir),
                local_dir_use_symlinks=False,
                resume_download=True
            )
            return {"success": True, "message": f"Downloaded {filename} to {target_folder}", "path": downloaded}
        except ImportError:
            return {"success": False, "error": "huggingface-hub not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_installed_comfyui_models(self, model_type: str = "checkpoints") -> List[Dict]:
        """List installed models in a ComfyUI models subfolder."""
        comfy_path = self._comfyui_path
        if not comfy_path:
            detect = self.detect_comfyui()
            if detect.get("installed"):
                comfy_path = detect["path"]
            else:
                return []
        type_info = self.COMFYUI_MODEL_TYPES.get(model_type, self.COMFYUI_MODEL_TYPES["checkpoints"])
        target_folder = type_info["folder"]
        extensions = type_info["extensions"]
        model_dir = Path(comfy_path) / "models" / target_folder
        if not model_dir.exists():
            return []
        models = []
        for f in model_dir.rglob("*"):
            if f.suffix.lower() in extensions:
                size_gb = f.stat().st_size / (1024**3) if f.is_file() else 0
                models.append({
                    "name": f.name,
                    "path": str(f),
                    "relative_path": str(f.relative_to(model_dir)),
                    "size_bytes": f.stat().st_size,
                    "size_gb": round(size_gb, 2),
                    "model_type": model_type
                })
        return sorted(models, key=lambda x: x["name"])

    def install_comfyui(self, target_dir: str = None) -> Dict:
        """Clone ComfyUI from GitHub into the project directory."""
        if target_dir is None:
            target_dir = str(Path(__file__).parent.parent.parent / "comfyui")
        if os.path.isdir(os.path.join(target_dir, "main.py")):
            return {"success": True, "message": f"ComfyUI already installed at {target_dir}", "path": target_dir}
        try:
            result = subprocess.run(
                ["git", "clone", "https://github.com/comfyanonymous/ComfyUI.git", target_dir],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                return {"success": True, "message": "ComfyUI installed successfully", "path": target_dir}
            return {"success": False, "error": result.stderr[:200]}
        except FileNotFoundError:
            return {"success": False, "error": "Git not found. Install Git first."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_comfyui(self) -> Dict:
        """Pull latest changes for ComfyUI."""
        comfy_path = self._comfyui_path
        if not comfy_path:
            detect = self.detect_comfyui()
            if detect.get("installed"):
                comfy_path = detect["path"]
            else:
                return {"success": False, "error": "ComfyUI not found"}
        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=comfy_path,
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                return {"success": True, "message": result.stdout[:200]}
            return {"success": False, "error": result.stderr[:200]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def __del__(self):
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass