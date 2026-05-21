import json
import os
import time
import requests
from typing import Dict, List, Optional, Any
from pathlib import Path


class ImageEngine:
    """Unified engine for image/video generation across multiple providers."""

    def __init__(self, config_path: str = None, comfyui_client=None, settings_path: str = None):
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "image_providers.json"
        self.config = self._load_config(config_path)
        self.comfyui = comfyui_client
        if settings_path:
            self._settings_path = Path(settings_path)
        else:
            self._settings_path = Path(__file__).parent.parent / "settings.json"

    def _load_config(self, config_path: str) -> Dict:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading image config: {e}")
            return {"providers": {}}

    def _get_api_key(self, provider_id: str, provider: Dict) -> Optional[str]:
        """Get API key from settings.json first, then env var."""
        try:
            if self._settings_path.exists():
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                img_settings = settings.get("image_gen", {})
                key = img_settings.get("apiKey", "")
                if key:
                    return key
        except Exception:
            pass
        env_var = provider.get("api_key_env", "")
        if env_var:
            return os.environ.get(env_var, "")
        return None

    def get_providers(self) -> List[Dict]:
        """Get list of available image/video providers."""
        providers = []
        for key, provider in self.config.get("providers", {}).items():
            providers.append({
                "id": key,
                "name": provider.get("name"),
                "type": provider.get("type"),
                "supports_image": provider.get("supports_image", False),
                "supports_video": provider.get("supports_video", False),
                "requires_api_key": provider.get("requires_api_key", False),
                "is_custom": provider.get("is_custom", False)
            })
        return providers

    def get_models(self, provider_id: str) -> List[str]:
        """Get available models for a provider."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return []
        return [provider.get("default_model", "")]

    def test_connection(self, provider_id: str, host: str = None, api_key: str = None) -> Dict:
        """Test connection to an image/video provider."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        if provider_id == "comfyui":
            if self.comfyui:
                if host:
                    self.comfyui.set_host(host)
                return self.comfyui.test_connection()
            return {"success": False, "error": "ComfyUI client not initialized"}

        p_type = provider.get("type", "")
        if p_type == "cloud":
            key = api_key or self._get_api_key(provider_id, provider)
            if provider.get("requires_api_key") and not key:
                return {"success": False, "error": f"API key required for {provider.get('name')}"}
            return {"success": True, "message": f"{provider.get('name')} configured"}

        return {"success": False, "error": "Connection test not available"}

    def generate_image(self, provider_id: str, model: str, prompt: str, host: str = None,
                       api_key: str = None, width: int = 1024, height: int = 1024,
                       workflow_name: str = None, input_images: List[str] = None,
                       aspect_ratio: str = None, resolution: str = None, seed: int = None,
                       **kwargs) -> Dict:
        """Generate an image using the selected provider."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        if provider_id == "comfyui":
            return self._generate_comfyui_image(provider, model, prompt, host,
                                                 workflow_name, input_images,
                                                 aspect_ratio, resolution, seed, **kwargs)
        elif provider_id == "openai_dalle":
            return self._generate_dalle(provider, model, prompt, api_key, width, height, **kwargs)
        elif provider_id == "stability":
            return self._generate_stability(provider, model, prompt, api_key, width, height, **kwargs)
        elif provider_id == "google_imagen":
            return self._generate_imagen(provider, model, prompt, api_key, width, height, **kwargs)
        elif provider_id == "fal":
            return self._generate_fal(provider, model, prompt, api_key, **kwargs)
        elif provider_id == "kei":
            return self._generate_kei(provider, model, prompt, api_key, **kwargs)
        elif provider_id == "custom":
            return self._generate_custom(provider, model, prompt, host, api_key, **kwargs)

        return {"success": False, "error": "Unsupported provider"}

    def generate_video(self, provider_id: str, model: str, prompt: str, host: str = None,
                       api_key: str = None, input_image: str = None,
                       workflow_name: str = None, **kwargs) -> Dict:
        """Generate a video using the selected provider."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        if provider_id == "comfyui":
            return self._generate_comfyui_video(provider, model, prompt, host,
                                                 workflow_name, input_image, **kwargs)
        elif provider_id == "runway":
            return self._generate_runway(provider, model, prompt, api_key, input_image, **kwargs)
        elif provider_id == "stability":
            return self._generate_stability_video(provider, model, prompt, api_key, **kwargs)
        elif provider_id == "fal":
            return self._generate_fal_video(provider, model, prompt, api_key, **kwargs)

        return {"success": False, "error": "Unsupported provider or video not available"}

    def _generate_comfyui_image(self, provider: Dict, model: str, prompt: str, host: str = None,
                                 workflow_name: str = None, input_images: List[str] = None,
                                 aspect_ratio: str = None, resolution: str = None,
                                 seed: int = None, **kwargs) -> Dict:
        """Generate image via ComfyUI."""
        if not self.comfyui:
            return {"success": False, "error": "ComfyUI client not initialized"}
        if host:
            self.comfyui.set_host(host)
        if workflow_name:
            return self.comfyui.generate_with_workflow(
                prompt=prompt, workflow_name=workflow_name,
                input_images=input_images, aspect_ratio=aspect_ratio,
                resolution=resolution, seed=seed
            )
        # Fall back to simple image generation
        return self.comfyui.generate_image(prompt, model, 1024, 1024, seed or -1)

    def _generate_comfyui_video(self, provider: Dict, model: str, prompt: str, host: str = None,
                                 workflow_name: str = None, input_image: str = None, **kwargs) -> Dict:
        """Generate video via ComfyUI."""
        if not self.comfyui:
            return {"success": False, "error": "ComfyUI client not initialized"}
        if host:
            self.comfyui.set_host(host)
        if workflow_name:
            input_images = [input_image] if input_image else None
            return self.comfyui.generate_with_workflow(
                prompt=prompt, workflow_name=workflow_name,
                input_images=input_images, **kwargs
            )
        return self.comfyui.generate_video(prompt, model, input_image=input_image)

    def _generate_dalle(self, provider: Dict, model: str, prompt: str, api_key: str = None,
                        width: int = 1024, height: int = 1024, **kwargs) -> Dict:
        """Generate image via OpenAI DALL-E."""
        key = api_key or self._get_api_key("openai_dalle", provider)
        if not key:
            return {"success": False, "error": "DALL-E API key required"}
        try:
            host = provider.get("host", "https://api.openai.com/v1")
            size = f"{width}x{height}" if width == height else "1024x1024"
            payload = {"model": model or "dall-e-3", "prompt": prompt, "n": 1, "size": size}
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            resp = requests.post(f"{host}/images/generations", json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                img_url = data.get("data", [{}])[0].get("url", "")
                if img_url:
                    img_resp = requests.get(img_url, timeout=30)
                    if img_resp.status_code == 200:
                        import base64
                        b64 = base64.b64encode(img_resp.content).decode()
                        return {"success": True, "image_data": b64, "filename": "dalle_output.png"}
            return {"success": False, "error": f"DALL-E API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_stability(self, provider: Dict, model: str, prompt: str, api_key: str = None,
                            width: int = 1024, height: int = 1024, **kwargs) -> Dict:
        """Generate image via Stability AI."""
        key = api_key or self._get_api_key("stability", provider)
        if not key:
            return {"success": False, "error": "Stability AI API key required"}
        try:
            host = provider.get("host", "https://api.stability.ai/v2beta")
            payload = {
                "model": model or "stable-image-ultra",
                "prompt": prompt,
                "output_format": "png",
                "width": width, "height": height
            }
            headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
            resp = requests.post(f"{host}/stable-image/generate/ultra", json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                b64 = data.get("image", "")
                if b64:
                    return {"success": True, "image_data": b64, "filename": "stability_output.png"}
            return {"success": False, "error": f"Stability API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_imagen(self, provider: Dict, model: str, prompt: str, api_key: str = None,
                         width: int = 1024, height: int = 1024, **kwargs) -> Dict:
        """Generate image via Google Imagen."""
        key = api_key or self._get_api_key("google_imagen", provider)
        if not key:
            return {"success": False, "error": "Google API key required"}
        try:
            host = provider.get("host", "https://us-central1-aiplatform.googleapis.com/v1")
            payload = {
                "model": model or "imagen-3.0-generate-001",
                "instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1}
            }
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            resp = requests.post(f"{host}/projects/-/locations/us-central1/publishers/google/models/{model}:predict",
                                 json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                predictions = data.get("predictions", [])
                if predictions and "bytesBase64Encoded" in predictions[0]:
                    b64 = predictions[0]["bytesBase64Encoded"]
                    return {"success": True, "image_data": b64, "filename": "imagen_output.png"}
            return {"success": False, "error": f"Imagen API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_fal(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        """Generate image via FAL.ai."""
        key = api_key or self._get_api_key("fal", provider)
        if not key:
            return {"success": False, "error": "FAL API key required"}
        try:
            host = provider.get("host", "https://fal.run")
            model_id = model or "fal-ai/flux-pro"
            headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
            payload = {"prompt": prompt}
            resp = requests.post(f"{host}/{model_id}", json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                img_url = data.get("images", [{}])[0].get("url", "") or data.get("image", {}).get("url", "")
                if img_url:
                    img_resp = requests.get(img_url, timeout=30)
                    if img_resp.status_code == 200:
                        import base64
                        b64 = base64.b64encode(img_resp.content).decode()
                        return {"success": True, "image_data": b64, "filename": "fal_output.png"}
            return {"success": False, "error": f"FAL API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_kei(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        """Generate image via Kei AI."""
        key = api_key or self._get_api_key("kei", provider)
        if not key:
            return {"success": False, "error": "Kei AI API key required"}
        try:
            host = provider.get("host", "https://api.kei.com/v1")
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model or "kei-image", "prompt": prompt}
            resp = requests.post(f"{host}/images/generations", json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                img_url = data.get("data", [{}])[0].get("url", "")
                if img_url:
                    img_resp = requests.get(img_url, timeout=30)
                    if img_resp.status_code == 200:
                        import base64
                        b64 = base64.b64encode(img_resp.content).decode()
                        return {"success": True, "image_data": b64, "filename": "kei_output.png"}
            return {"success": False, "error": f"Kei API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_custom(self, provider: Dict, model: str, prompt: str, host: str = None,
                         api_key: str = None, **kwargs) -> Dict:
        """Generate via custom API (OpenAI-compatible)."""
        host = host or ""
        if not host:
            return {"success": False, "error": "Custom API host URL required"}
        key = api_key or ""
        try:
            messages = [{"role": "user", "content": prompt}]
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            payload = {"model": model or "default", "messages": messages}
            resp = requests.post(f"{host.rstrip('/')}/chat/completions", json=payload, headers=headers, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"success": True, "response": text}
            return {"success": False, "error": f"Custom API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_runway(self, provider: Dict, model: str, prompt: str, api_key: str = None,
                         input_image: str = None, **kwargs) -> Dict:
        """Generate video via Runway ML."""
        key = api_key or self._get_api_key("runway", provider)
        if not key:
            return {"success": False, "error": "Runway API key required"}
        try:
            host = provider.get("host", "https://api.runwayml.com/v1")
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model or "gen3a_turbo", "prompt_text": prompt, "duration": 5}
            if input_image:
                import base64
                payload["image"] = input_image
            resp = requests.post(f"{host}/generations", json=payload, headers=headers, timeout=300)
            if resp.status_code == 200:
                data = resp.json()
                video_url = data.get("output", [{}])[0].get("url", "") or data.get("video", {}).get("url", "")
                if video_url:
                    return {"success": True, "video_url": video_url, "filename": "runway_output.mp4"}
                gen_id = data.get("id", "")
                if gen_id:
                    # Poll for completion
                    for _ in range(60):
                        time.sleep(5)
                        poll = requests.get(f"{host}/generations/{gen_id}", headers=headers, timeout=30)
                        if poll.status_code == 200:
                            pd = poll.json()
                            status = pd.get("status", "")
                            if status == "completed":
                                vurl = pd.get("output", [{}])[0].get("url", "")
                                if vurl:
                                    return {"success": True, "video_url": vurl, "filename": "runway_output.mp4"}
                                break
                            elif status == "failed":
                                return {"success": False, "error": f"Runway generation failed"}
                    return {"success": False, "error": "Runway generation timeout"}
            return {"success": False, "error": f"Runway API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_stability_video(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        """Generate video via Stability AI."""
        key = api_key or self._get_api_key("stability", provider)
        if not key:
            return {"success": False, "error": "Stability AI API key required"}
        try:
            host = provider.get("host", "https://api.stability.ai/v2beta")
            headers = {"Authorization": f"Bearer {key}"}
            payload = {"model": model or "stable-video-diffusion", "prompt": prompt}
            resp = requests.post(f"{host}/stable-video/generate", json=payload, headers=headers, timeout=300)
            if resp.status_code == 200:
                data = resp.json()
                video_url = data.get("video", {}).get("url", "")
                if video_url:
                    return {"success": True, "video_url": video_url, "filename": "stability_video.mp4"}
            return {"success": False, "error": f"Stability video API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_fal_video(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        """Generate video via FAL.ai."""
        key = api_key or self._get_api_key("fal", provider)
        if not key:
            return {"success": False, "error": "FAL API key required"}
        try:
            host = provider.get("host", "https://fal.run")
            model_id = model or "fal-ai/ltx-video"
            headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
            payload = {"prompt": prompt}
            resp = requests.post(f"{host}/{model_id}", json=payload, headers=headers, timeout=300)
            if resp.status_code == 200:
                data = resp.json()
                video_url = data.get("video", {}).get("url", "") or data.get("output", [{}])[0].get("url", "")
                if video_url:
                    return {"success": True, "video_url": video_url, "filename": "fal_video.mp4"}
            return {"success": False, "error": f"FAL video API error: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
