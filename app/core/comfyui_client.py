import json
import os
import time
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any
from threading import Thread


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
            response = requests.get(f"{self.host}/api/get_checkpoints", timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get("checkpoints", [])
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
                    return data.get("name")
        except Exception as e:
            print(f"Error uploading image: {e}")
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