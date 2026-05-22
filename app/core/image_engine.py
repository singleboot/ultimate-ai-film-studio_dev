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
        """Get API key: check if shared with LLM first, then settings, then env var."""
        try:
            if self._settings_path.exists():
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                # If provider shares key with LLM (OpenRouter), use LLM key
                if provider.get("share_llm_key"):
                    llm_key = settings.get("llm", {}).get("apiKey", "")
                    if llm_key:
                        return llm_key
                img_key = settings.get("image_gen", {}).get("apiKey", "")
                if img_key:
                    return img_key
        except Exception:
            pass
        env_var = provider.get("api_key_env", "")
        if env_var:
            return os.environ.get(env_var, "")
        return None

    def get_providers(self) -> List[Dict]:
        providers = []
        for key, provider in self.config.get("providers", {}).items():
            providers.append({
                "id": key,
                "name": provider.get("name"),
                "type": provider.get("type"),
                "supports_image": provider.get("supports_image", False),
                "supports_video": provider.get("supports_video", False),
                "requires_api_key": provider.get("requires_api_key", False),
                "is_custom": provider.get("is_custom", False),
                "share_llm_key": provider.get("share_llm_key", False)
            })
        return providers

    def get_models(self, provider_id: str) -> List[str]:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return []
        return [provider.get("default_model", "")]

    def test_connection(self, provider_id: str, host: str = None, api_key: str = None) -> Dict:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        if provider_id == "comfyui" or provider_id == "comfyui_cloud":
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
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        handlers = {
            "comfyui": self._generate_comfyui_image,
            "comfyui_cloud": self._generate_comfyui_image,
            "openai_dalle": self._generate_dalle,
            "stability": self._generate_stability,
            "google_imagen": self._generate_imagen,
            "fal": self._generate_fal,
            "kei": self._generate_kei,
            "openrouter": self._generate_openai_image,
            "replicate": self._generate_replicate_image,
            "together": self._generate_openai_image,
            "deepinfra": self._generate_openai_image,
            "ideogram": self._generate_ideogram,
            "custom": self._generate_custom,
        }
        handler = handlers.get(provider_id)
        if handler:
            return handler(provider, model, prompt, host, api_key, width, height,
                           workflow_name, input_images, aspect_ratio, resolution, seed, **kwargs)
        return {"success": False, "error": "Unsupported provider"}

    def generate_video(self, provider_id: str, model: str, prompt: str, host: str = None,
                       api_key: str = None, input_image: str = None,
                       workflow_name: str = None, **kwargs) -> Dict:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        handlers = {
            "comfyui": self._generate_comfyui_video,
            "comfyui_cloud": self._generate_comfyui_video,
            "runway": self._generate_runway,
            "stability": self._generate_stability_video,
            "fal": self._generate_fal_video,
            "openrouter": self._generate_openai_video,
            "replicate": self._generate_replicate_video,
            "deepinfra": self._generate_openai_video,
        }
        handler = handlers.get(provider_id)
        if handler:
            return handler(provider, model, prompt, host, api_key, input_image, workflow_name, **kwargs)
        return {"success": False, "error": "Unsupported provider or video not available"}

    def _generate_comfyui_image(self, provider: Dict, model: str, prompt: str, host: str = None,
                                 workflow_name: str = None, input_images: List[str] = None,
                                 aspect_ratio: str = None, resolution: str = None,
                                 seed: int = None, **kwargs) -> Dict:
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
        return self.comfyui.generate_image(prompt, model, 1024, 1024, seed or -1)

    def _generate_comfyui_video(self, provider: Dict, model: str, prompt: str, host: str = None,
                                 workflow_name: str = None, input_image: str = None, **kwargs) -> Dict:
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

    def _generate_openai_image(self, provider: Dict, model: str, prompt: str, host: str = None,
                                api_key: str = None, **kwargs) -> Dict:
        """Generate image via OpenAI-compatible provider (OpenRouter, Together, DeepInfra)."""
        key = api_key or self._get_api_key(provider.get("id", ""), provider)
        if not key:
            return {"success": False, "error": f"API key required for {provider.get('name')}"}
        host = host or provider.get("host", "")
        model_id = model or provider.get("default_model", "")
        try:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model_id, "prompt": prompt, "n": 1, "size": "1024x1024"}
            resp = requests.post(f"{host.rstrip('/')}/images/generations", json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                img_url = data.get("data", [{}])[0].get("url", "")
                if img_url:
                    img_resp = requests.get(img_url, timeout=30)
                    if img_resp.status_code == 200:
                        import base64
                        b64 = base64.b64encode(img_resp.content).decode()
                        return {"success": True, "image_data": b64, "filename": f"{provider.get('id','img')}_output.png"}
            return {"success": False, "error": f"{provider.get('name')} error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_openai_video(self, provider: Dict, model: str, prompt: str, host: str = None,
                                api_key: str = None, **kwargs) -> Dict:
        """Generate video via OpenAI-compatible provider."""
        key = api_key or self._get_api_key(provider.get("id", ""), provider)
        if not key:
            return {"success": False, "error": f"API key required for {provider.get('name')}"}
        host = host or provider.get("host", "")
        model_id = model or provider.get("default_model", "")
        try:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model_id, "prompt": prompt}
            resp = requests.post(f"{host.rstrip('/')}/video/generations", json=payload, headers=headers, timeout=300)
            if resp.status_code == 200:
                data = resp.json()
                video_url = data.get("data", [{}])[0].get("url", "") or data.get("video", {}).get("url", "")
                if video_url:
                    return {"success": True, "video_url": video_url, "filename": f"{provider.get('id','vid')}_output.mp4"}
            return {"success": False, "error": f"{provider.get('name')} video error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_replicate_image(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        """Generate image via Replicate."""
        key = api_key or self._get_api_key("replicate", provider)
        if not key:
            return {"success": False, "error": "Replicate API key required"}
        try:
            host = provider.get("host", "https://api.replicate.com/v1")
            model_id = model or "black-forest-labs/flux-dev"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"input": {"prompt": prompt}}
            resp = requests.post(f"{host}/models/{model_id}/predictions", json=payload, headers=headers, timeout=30)
            if resp.status_code == 201:
                data = resp.json()
                pred_url = data.get("urls", {}).get("get", "")
                if pred_url:
                    for _ in range(60):
                        time.sleep(5)
                        poll = requests.get(pred_url, headers=headers, timeout=30)
                        if poll.status_code == 200:
                            pd = poll.json()
                            if pd.get("status") == "succeeded":
                                output = pd.get("output", [])
                                img_url = output[0] if isinstance(output, list) and output else output.get("url", "")
                                if img_url:
                                    img_resp = requests.get(img_url, timeout=30)
                                    if img_resp.status_code == 200:
                                        import base64
                                        b64 = base64.b64encode(img_resp.content).decode()
                                        return {"success": True, "image_data": b64, "filename": "replicate_output.png"}
                                break
                            elif pd.get("status") == "failed":
                                return {"success": False, "error": "Replicate generation failed"}
                    return {"success": False, "error": "Replicate generation timeout"}
            return {"success": False, "error": f"Replicate error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_replicate_video(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        key = api_key or self._get_api_key("replicate", provider)
        if not key:
            return {"success": False, "error": "Replicate API key required"}
        try:
            host = provider.get("host", "https://api.replicate.com/v1")
            model_id = model or "black-forest-labs/flux-pro"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"input": {"prompt": prompt}}
            resp = requests.post(f"{host}/models/{model_id}/predictions", json=payload, headers=headers, timeout=30)
            if resp.status_code == 201:
                data = resp.json()
                pred_url = data.get("urls", {}).get("get", "")
                if pred_url:
                    for _ in range(120):
                        time.sleep(5)
                        poll = requests.get(pred_url, headers=headers, timeout=30)
                        if poll.status_code == 200:
                            pd = poll.json()
                            if pd.get("status") == "succeeded":
                                output = pd.get("output", "")
                                if output:
                                    return {"success": True, "video_url": output if isinstance(output, str) else output[0], "filename": "replicate_video.mp4"}
                                break
                            elif pd.get("status") == "failed":
                                return {"success": False, "error": "Replicate video generation failed"}
                    return {"success": False, "error": "Replicate video generation timeout"}
            return {"success": False, "error": f"Replicate video error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_ideogram(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
        """Generate image via Ideogram."""
        key = api_key or self._get_api_key("ideogram", provider)
        if not key:
            return {"success": False, "error": "Ideogram API key required"}
        try:
            host = provider.get("host", "https://api.ideogram.ai")
            headers = {"Api-Key": key, "Content-Type": "application/json"}
            payload = {
                "image_request": {
                    "prompt": prompt,
                    "model": model or "V_2",
                    "aspect_ratio": "1:1",
                    "magic_prompt_option": "AUTO"
                }
            }
            resp = requests.post(f"{host}/generate", json=payload, headers=headers, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                images = data.get("data", [])
                if images:
                    img_url = images[0].get("url", "")
                    if img_url:
                        img_resp = requests.get(img_url, timeout=30)
                        if img_resp.status_code == 200:
                            import base64
                            b64 = base64.b64encode(img_resp.content).decode()
                            return {"success": True, "image_data": b64, "filename": "ideogram_output.png"}
            return {"success": False, "error": f"Ideogram error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_custom(self, provider: Dict, model: str, prompt: str, host: str = None,
                         api_key: str = None, **kwargs) -> Dict:
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
                                return {"success": False, "error": "Runway generation failed"}
                    return {"success": False, "error": "Runway generation timeout"}
            return {"success": False, "error": f"Runway API error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_stability_video(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
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
            return {"success": False, "error": f"Stability video API error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _generate_fal_video(self, provider: Dict, model: str, prompt: str, api_key: str = None, **kwargs) -> Dict:
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
            return {"success": False, "error": f"FAL video API error: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
