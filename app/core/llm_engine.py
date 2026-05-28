import json
import os
import subprocess
import time
import requests
import shutil
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any


class LLMEngine:
    def __init__(self, config_path: str = None, settings_path: str = None):
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "llm_providers.json"
        self.config = self._load_config(config_path)
        self.current_provider = None
        self.current_model = None
        self.connection_status = {}
        self._subprocesses: Dict[str, subprocess.Popen] = {}
        self._downloads = {}
        if settings_path:
            self._settings_path = Path(settings_path)
        else:
            self._settings_path = Path(__file__).parent.parent / "settings.json"
        # App LLM models directory
        self._models_dir = Path(__file__).parent.parent.parent / "models"
        self._models_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self, config_path: str) -> Dict:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"providers": {}}

    def _get_api_key(self, provider_id: str, provider: Dict) -> Optional[str]:
        try:
            if self._settings_path.exists():
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                llm_settings = settings.get("llm", {})
                key = llm_settings.get("apiKey", "")
                if key:
                    return key
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
                "requires_api_key": provider.get("requires_api_key", False)
            })
        return providers

    def get_models(self, provider_id: str) -> List[str]:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return []

        if provider_id == "ollama":
            return self._get_ollama_models(provider)
        elif provider_id == "lm_studio":
            return self._get_lmstudio_models(provider)
        elif provider_id == "llama_cpp":
            return self._get_llamacpp_models(provider)
        elif provider_id == "openai":
            return self._get_openai_models(provider)
        elif provider_id == "openrouter":
            return self._get_openrouter_models()
        elif provider_id == "claude":
            return self._get_claude_models()
        elif provider_id == "gemini":
            return self._get_gemini_models()
        elif provider_id == "opencode_zen":
            return self._get_opencode_zen_models(provider)
        elif provider_id == "nvidia_nim":
            return self._get_nvidia_nim_models(provider)
        elif provider_id == "app_llm":
            return self._get_app_llm_models()

        return [provider.get("default_model", "")]

    def _get_ollama_models(self, provider: Dict) -> List[str]:
        try:
            host = provider.get("host", "http://localhost:11434")
            response = requests.get(f"{host}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return [model.get("name") for model in data.get("models", [])]
        except Exception:
            pass
        return [provider.get("default_model", "llama3.1")]

    def _get_lmstudio_models(self, provider: Dict) -> List[str]:
        try:
            host = provider.get("host", "http://localhost:1234")
            response = requests.get(f"{host}/v1/models", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return [model.get("id") for model in data.get("data", [])]
        except Exception:
            pass
        return [provider.get("default_model", "llama-3.1-8b")]

    def _get_llamacpp_models(self, provider: Dict) -> List[str]:
        try:
            host = provider.get("host", "http://localhost:8080")
            response = requests.get(f"{host}/v1/models", timeout=5)
            if response.status_code == 200:
                data = response.json()
                return [model.get("id") for model in data.get("data", [])]
        except Exception:
            pass
        return [provider.get("default_model", "models/mistral-7b.gguf")]

    def _get_openai_models(self, provider: Dict) -> List[str]:
        api_key = self._get_api_key("openai", provider)
        if api_key:
            try:
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", []) if not m["id"].startswith("ft:")]
                    chat_models = [m for m in models if any(x in m for x in ["gpt-4o", "gpt-4", "gpt-3.5", "o1", "o3"])]
                    return chat_models[:20] if chat_models else models[:20]
            except Exception:
                pass
        return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]

    def _get_claude_models(self) -> List[str]:
        return [
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
            "claude-3-5-haiku-20241022"
        ]

    def _get_openrouter_models(self) -> List[str]:
        return [
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-3-opus",
            "openai/gpt-4o",
            "openai/gpt-4-turbo",
            "google/gemini-pro-1.5",
            "meta-llama/llama-3.1-70b-instruct"
        ]

    def _get_gemini_models(self) -> List[str]:
        return [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash"
        ]

    def _get_opencode_zen_models(self, provider: Dict) -> List[str]:
        host = provider.get("host", "https://opencode.ai/zen/v1")
        api_key = self._get_api_key("opencode_zen", provider)
        try:
            headers = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = requests.get(f"{host}/models", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return [m.get("id", m.get("name", "")) for m in data if m.get("id") or m.get("name")]
                return list(data.keys()) if isinstance(data, dict) else []
        except Exception:
            pass
        return ["deepseek-v4-flash-free", "gpt-5.2-codex", "claude-sonnet-4-6", "gemini-3-pro"]

    def _get_nvidia_nim_models(self, provider: Dict) -> List[str]:
        host = provider.get("host", "https://integrate.api.nvidia.com/v1")
        api_key = self._get_api_key("nvidia_nim", provider)
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            resp = requests.get(f"{host}/models", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                models = []
                for m in data.get("data", []):
                    mid = m.get("id", "")
                    if mid:
                        models.append(mid)
                return models[:30] if models else []
        except Exception:
            pass
        return ["meta/llama-3.1-70b-instruct", "mistralai/mistral-7b-instruct-v0.3"]

    def _get_app_llm_models(self) -> List[str]:
        """Scan local models/ folder for .gguf files."""
        models = []
        for f in self._models_dir.iterdir():
            if f.suffix.lower() in (".gguf", ".gguf", ".bin"):
                models.append(f.name)
        return sorted(models)

    # === Model Management ===

    def get_installed_models(self) -> List[Dict]:
        """List all models in the models/ folder with details."""
        models = []
        for f in self._models_dir.iterdir():
            if f.suffix.lower() in (".gguf", ".gguf", ".bin", ".pt", ".pth", ".safetensors"):
                size_gb = f.stat().st_size / (1024**3) if f.is_file() else 0
                models.append({
                    "name": f.name,
                    "path": str(f),
                    "size_bytes": f.stat().st_size,
                    "size_gb": round(size_gb, 2),
                    "modified": f.stat().st_mtime
                })
        return sorted(models, key=lambda x: x["name"])

    def search_huggingface_models(self, query: str = "", size_filter: str = "",
                                   quant_filter: str = "", limit: int = 30) -> List[Dict]:
        """Search HuggingFace for GGUF models."""
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            search_term = query or "gguf"
            results = api.list_models(
                search=search_term,
                task="text-generation",
                library=["gguf"],
                sort="downloads",
                direction=-1,
                limit=limit * 2
            )
            models = []
            for model in results:
                if len(models) >= limit:
                    break
                try:
                    files = api.list_repo_files(model.modelId)
                    gguf_files = [f for f in files if f.endswith(".gguf")]
                    if not gguf_files:
                        continue
                    for fname in gguf_files:
                        try:
                            meta = api.model_info(model.modelId, files_metadata=True)
                            file_meta = None
                            for sibling in meta.siblings:
                                if sibling.rfilename == fname:
                                    file_meta = sibling
                                    break
                            size = file_meta.size if file_meta and file_meta.size else 0
                        except Exception:
                            size = 0
                        size_gb = round(size / (1024**3), 2) if size > 0 else 0
                        quant = "Unknown"
                        for q in ["Q2_K", "Q3_K", "Q4_K_M", "Q4_K", "Q5_K_M", "Q5_K", "Q6_K", "Q8_0", "F16"]:
                            if q.lower() in fname.lower():
                                quant = q
                                break
                        param_size = ""
                        for s in ["70B", "40B", "34B", "30B", "13B", "8B", "7B", "3B", "1B"]:
                            if s.lower().replace("b", "") in model.modelId.lower().replace("b", "") or s.lower() in fname.lower():
                                param_size = s
                                break
                        if size_filter:
                            fs = size_filter.replace("B", "")
                            if param_size and fs:
                                try:
                                    psize = int(param_size.replace("B", ""))
                                    fsize = int(fs)
                                    if psize != fsize:
                                        continue
                                except ValueError:
                                    pass
                        if quant_filter and quant_filter not in quant:
                            continue
                        if query and query.lower() not in model.modelId.lower() and query.lower() not in fname.lower():
                            continue
                        models.append({
                            "repo": model.modelId,
                            "filename": fname,
                            "size_bytes": size,
                            "size_gb": size_gb,
                            "quantization": quant,
                            "parameter_size": param_size,
                            "downloads": getattr(model, "downloads", 0) or 0
                        })
                except Exception:
                    continue
            return models[:limit]
        except ImportError:
            return [{"error": "huggingface-hub not installed. Run: pip install huggingface-hub"}]
        except Exception as e:
            return [{"error": str(e)}]

    def get_download_status(self, filename: str) -> Dict:
        """Get the current progress of a background model download."""
        dest_path = self._models_dir / filename
        if dest_path.exists() and filename not in self._downloads:
            return {
                "status": "completed",
                "progress": 100,
                "downloaded_bytes": dest_path.stat().st_size,
                "total_bytes": dest_path.stat().st_size
            }
        return self._downloads.get(filename, {"status": "idle", "progress": 0})

    def download_model_from_hf(self, repo: str, filename: str) -> Dict:
        """Download a GGUF model file from HuggingFace to models/ folder in background."""
        dest_path = self._models_dir / filename
        if dest_path.exists():
            return {"success": True, "message": f"Already downloaded: {filename}", "path": str(dest_path)}

        if filename in self._downloads and self._downloads[filename]["status"] == "downloading":
            return {"success": True, "message": "Download already in progress"}

        self._downloads[filename] = {
            "status": "downloading",
            "progress": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "error": None
        }

        def download_thread():
            temp_path = dest_path.with_suffix('.download')
            try:
                url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
                self._models_dir.mkdir(parents=True, exist_ok=True)

                response = requests.get(url, stream=True, allow_redirects=True, timeout=30)
                if response.status_code != 200:
                    self._downloads[filename] = {
                        "status": "failed",
                        "progress": 0,
                        "error": f"HTTP error {response.status_code}"
                    }
                    return

                total_size = int(response.headers.get('content-length', 0))
                self._downloads[filename]["total_bytes"] = total_size

                downloaded = 0
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=1024*1024):  # 1MB chunks
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            self._downloads[filename]["downloaded_bytes"] = downloaded
                            if total_size > 0:
                                self._downloads[filename]["progress"] = int((downloaded / total_size) * 100)

                if total_size > 0 and downloaded < total_size:
                    raise Exception("Connection closed prematurely during download")

                if temp_path.exists():
                    if dest_path.exists():
                        dest_path.unlink()
                    temp_path.rename(dest_path)

                self._downloads[filename] = {
                    "status": "completed",
                    "progress": 100,
                    "downloaded_bytes": total_size,
                    "total_bytes": total_size
                }
            except Exception as e:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                self._downloads[filename] = {
                    "status": "failed",
                    "progress": 0,
                    "error": str(e)
                }

        t = threading.Thread(target=download_thread, daemon=True)
        t.start()
        return {"success": True, "message": "Download started in background"}

    def copy_model_to_folder(self, source_path: str) -> Dict:
        """Copy a model file from user-selected path into models/ folder."""
        src = Path(source_path)
        if not src.exists():
            return {"success": False, "error": "Source file not found"}
        if src.suffix.lower() not in (".gguf", ".bin", ".pt", ".pth", ".safetensors"):
            return {"success": False, "error": "Unsupported model format. Use .gguf, .bin, .pt, .pth, or .safetensors"}
        dest = self._models_dir / src.name
        if dest.exists():
            base = dest.stem
            ext = dest.suffix
            counter = 1
            while dest.exists():
                dest = self._models_dir / f"{base}_{counter}{ext}"
                counter += 1
        try:
            shutil.copy2(str(src), str(dest))
            return {"success": True, "message": f"Copied to {dest.name}", "path": str(dest)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_model(self, filename: str) -> Dict:
        """Delete a model file from models/ folder."""
        f = self._models_dir / filename
        if not f.exists():
            return {"success": False, "error": "File not found"}
        try:
            f.unlink()
            return {"success": True, "message": f"Deleted {filename}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # === Generation ===

    def test_connection(self, provider_id: str, host: str = None, api_key: str = None) -> Dict:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        if host:
            provider = dict(provider)
            provider["host"] = host

        provider_type = provider.get("type")
        if provider_type == "local":
            if provider_id == "ollama":
                return self._test_ollama(provider)
            elif provider_id == "lm_studio":
                return self._test_lmstudio(provider)
            elif provider_id == "llama_cpp" or provider_id == "app_llm":
                return self._test_llamacpp(provider)
        else:
            key = api_key or self._get_api_key(provider_id, provider)
            if not key:
                return {"success": False, "error": f"API key required for {provider.get('name', provider_id)}"}
            return {"success": True, "message": f"{provider.get('name', provider_id)} configured"}

        return {"success": False, "error": "Connection test not available"}

    def _test_ollama(self, provider: Dict) -> Dict:
        try:
            host = provider.get("host", "http://localhost:11434")
            response = requests.get(f"{host}/api/tags", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected to Ollama"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def _test_lmstudio(self, provider: Dict) -> Dict:
        try:
            host = provider.get("host", "http://localhost:1234")
            response = requests.get(f"{host}/v1/models", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected to LM Studio"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def _test_llamacpp(self, provider: Dict) -> Dict:
        try:
            host = provider.get("host", "http://localhost:8080")
            response = requests.get(f"{host}/v1/models", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def generate(self, provider_id: str, model: str, prompt: str, system_prompt: str = None,
                 host: str = None, images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}
        if host:
            provider = dict(provider)
            provider["host"] = host

        handlers = {
            "ollama": self._generate_ollama,
            "lm_studio": self._generate_lmstudio,
            "llama_cpp": self._generate_llamacpp,
            "openai": self._generate_openai,
            "claude": self._generate_claude,
            "openrouter": self._generate_openrouter,
            "gemini": self._generate_gemini,
            "opencode_zen": self._generate_openai_compat,
            "nvidia_nim": self._generate_openai_compat,
            "app_llm": self._generate_openai_compat,
        }
        handler = handlers.get(provider_id)
        if handler:
            return handler(provider, model, prompt, system_prompt, images=images, api_key=api_key, **kwargs)
        return {"success": False, "error": "Unsupported provider"}

    def _generate_openai_compat(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                                 images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        """Generic OpenAI-compatible generation for OpenCode Zen, NVIDIA NIM, and App LLM."""
        key = api_key or self._get_api_key(provider.get("id", ""), provider)
        host = provider.get("host", "")
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if images:
                import base64
                parts = [{"type": "text", "text": prompt}]
                for img_b64 in images:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
                messages.append({"role": "user", "content": parts})
            else:
                messages.append({"role": "user", "content": prompt})

            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"

            payload = {"model": model, "messages": messages, "temperature": kwargs.get("temperature", 0.7)}
            response = requests.post(f"{host}/chat/completions", json=payload, headers=headers, timeout=300)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API key"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_ollama(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, **kwargs) -> Dict:
        try:
            host = provider.get("host", "http://localhost:11434")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            user_content = {"role": "user", "content": prompt}
            if images:
                user_content["images"] = images
            messages.append(user_content)

            payload = {"model": model, "messages": messages, "stream": False}
            response = requests.post(f"{host}/api/chat", json=payload, timeout=300)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_lmstudio(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                           **kwargs) -> Dict:
        try:
            host = provider.get("host", "http://localhost:1234/v1")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {"model": model, "messages": messages, "temperature": kwargs.get("temperature", 0.7)}
            response = requests.post(f"{host}/chat/completions", json=payload, timeout=300)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_llamacpp(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                           images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        try:
            host = provider.get("host", "http://localhost:8080")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            user_content = {"role": "user", "content": prompt}
            if images:
                import base64
                parts = [{"type": "text", "text": prompt}]
                for img_b64 in images:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
                user_content["content"] = parts
            messages.append(user_content)

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = {"model": model, "messages": messages, "temperature": kwargs.get("temperature", 0.7)}
            response = requests.post(f"{host}/v1/chat/completions", json=payload, headers=headers, timeout=300)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_openai(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        key = api_key or self._get_api_key("openai", provider)
        if not key:
            return {"success": False, "error": "OpenAI API key required"}
        try:
            host = provider.get("host", "https://api.openai.com/v1")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if images:
                parts = [{"type": "text", "text": prompt}]
                for img_b64 in images:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
                messages.append({"role": "user", "content": parts})
            else:
                messages.append({"role": "user", "content": prompt})
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model, "messages": messages, "temperature": kwargs.get("temperature", 0.7)}
            response = requests.post(f"{host}/chat/completions", json=payload, headers=headers, timeout=300)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid OpenAI API key"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_claude(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        key = api_key or self._get_api_key("claude", provider)
        if not key:
            return {"success": False, "error": "Claude API key required"}
        try:
            host = provider.get("host", "https://api.anthropic.com/v1")
            content_blocks = []
            if images:
                import base64
                content_blocks.append({"type": "text", "text": prompt})
                for img_b64 in images:
                    content_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64}
                    })
            else:
                content_blocks.append({"type": "text", "text": prompt})
            payload = {
                "model": model,
                "max_tokens": kwargs.get("max_tokens", 4096),
                "messages": [{"role": "user", "content": content_blocks}]
            }
            if system_prompt:
                payload["system"] = system_prompt
            headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
            response = requests.post(f"{host}/messages", json=payload, headers=headers, timeout=300)
            if response.status_code == 200:
                data = response.json()
                text = "".join(block.get("text", "") for block in data.get("content", []))
                return {"success": True, "response": text}
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid Claude API key"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_openrouter(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                             images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        key = api_key or self._get_api_key("openrouter", provider)
        if not key:
            return {"success": False, "error": "OpenRouter API key required"}
        try:
            host = provider.get("host", "https://openrouter.ai/api/v1")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            if images:
                parts = [{"type": "text", "text": prompt}]
                for img_b64 in images:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}})
                messages.append({"role": "user", "content": parts})
            else:
                messages.append({"role": "user", "content": prompt})
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"model": model, "messages": messages, "temperature": kwargs.get("temperature", 0.7)}
            response = requests.post(f"{host}/chat/completions", json=payload, headers=headers, timeout=300)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_gemini(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        key = api_key or self._get_api_key("gemini", provider)
        if not key:
            return {"success": False, "error": "Gemini API key required"}
        try:
            host = provider.get("host", "https://generativelanguage.googleapis.com/v1beta")
            url = f"{host}/models/{model}:generateContent?key={key}"
            if images:
                import base64
                parts = [{"text": prompt}]
                for img_b64 in images:
                    parts.append({"inline_data": {"mime_type": "image/png", "data": img_b64}})
                contents = [{"parts": parts}]
            else:
                contents = [{"parts": [{"text": prompt}]}]
            if system_prompt:
                contents.insert(0, {"parts": [{"text": system_prompt}]})
            payload = {"contents": contents}
            response = requests.post(url, json=payload, timeout=300)
            if response.status_code == 200:
                data = response.json()
                text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return {"success": True, "response": text}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    # === Subprocess Management for Local LLMs ===

    def detect_local_llm(self, provider_id: str) -> Dict:
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"installed": False, "error": "Provider not found"}
        binary = provider.get("binary", "")
        if not binary:
            return {"installed": False, "error": "No binary configured"}
        search_paths = [binary]

        # Add local project bin directory
        project_root = Path(__file__).parent.parent.parent
        project_bin = project_root / "bin"
        if os.name == 'nt':
            search_paths.append(str(project_bin / f"{binary}.exe"))
        else:
            search_paths.append(str(project_bin / binary))

        if os.name == 'nt':
            search_paths.extend([
                os.path.expandvars(f"%USERPROFILE%\\AppData\\Local\\Programs\\{binary}.exe"),
                os.path.expandvars(f"%LOCALAPPDATA%\\{binary}\\{binary}.exe"),
                f"C:\\Program Files\\{binary}\\{binary}.exe",
                f"C:\\Program Files (x86)\\{binary}\\{binary}.exe",
            ])
        else:
            search_paths.extend([
                f"/usr/local/bin/{binary}",
                f"/usr/bin/{binary}",
                f"/opt/{binary}/{binary}",
                os.path.expanduser(f"~/{binary}/{binary}"),
            ])
        for path in search_paths:
            if os.path.isfile(path) or os.path.isfile(path + ".exe"):
                return {"installed": True, "path": path if os.path.isfile(path) else path + ".exe"}
        try:
            if os.name == 'nt':
                result = subprocess.run(["where", binary], capture_output=True, text=True, timeout=5)
            else:
                result = subprocess.run(["which", binary], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return {"installed": True, "path": result.stdout.strip().split("\n")[0]}
        except Exception:
            pass
        return {"installed": False, "error": f"{binary} not found in PATH"}

    def get_local_status(self, provider_id: str) -> Dict:
        proc = self._subprocesses.get(provider_id)
        if proc and proc.poll() is None:
            return {"running": True, "pid": proc.pid, "model_name": self._get_provider_model(provider_id)}
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"running": False, "model_name": ""}
        health = provider.get("health_endpoint", "")
        host = provider.get("host", "")
        if health and host:
            try:
                resp = requests.get(f"{host}{health}", timeout=3)
                if resp.status_code == 200:
                    return {"running": True, "pid": None, "model_name": self._get_provider_model(provider_id)}
            except Exception:
                pass
        return {"running": False, "model_name": ""}

    def _get_provider_model(self, provider_id: str) -> str:
        """Get the currently configured model name for a provider."""
        try:
            if self._settings_path.exists():
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                return settings.get("llm", {}).get("model", "") or provider_id
        except Exception:
            pass
        return provider_id

    def _get_gpu_layers(self) -> Optional[int]:
        try:
            if self._settings_path.exists():
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                val = settings.get("llm", {}).get("n_gpu_layers", -1)
                return int(val)
        except Exception:
            pass
        return -1

    def launch_local_llm(self, provider_id: str, model_path: str = None) -> Dict:
        status = self.get_local_status(provider_id)
        if status.get("running"):
            return {"success": True, "message": "Already running", "pid": status.get("pid")}
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}
        start_cmd = provider.get("start_cmd", [])
        if not start_cmd:
            return {"success": False, "error": "No start command configured"}
        cmd = list(start_cmd)

        # Substitute absolute path if detected
        detect = self.detect_local_llm(provider_id)
        if detect.get("installed") and detect.get("path"):
            cmd[0] = detect["path"]
        elif provider_id in ("llama_cpp", "app_llm"):
            return {"success": False, "error": f"{cmd[0]} not found. Install it first."}

        if provider_id == "llama_cpp" and model_path:
            if not os.path.exists(model_path):
                return {"success": False, "error": f"Model file not found: {model_path}"}
            cmd.extend(["--model", model_path])
        if provider_id == "app_llm" and model_path:
            full_path = str(self._models_dir / model_path) if not os.path.isabs(model_path) else model_path
            if not os.path.exists(full_path):
                return {"success": False, "error": f"Model file not found: {model_path}. Please download it first from settings."}
            cmd.extend(["--model", full_path])
        # Append GPU layers for llama.cpp/app_llm
        gpu_layers = self._get_gpu_layers()
        if gpu_layers is not None and provider_id in ("llama_cpp", "app_llm"):
            cmd.extend(["--n-gpu-layers", str(gpu_layers)])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self._subprocesses[provider_id] = proc
            health = provider.get("health_endpoint", "")
            host = provider.get("host", "")
            timeout = 60
            start = time.time()
            while time.time() - start < timeout:
                if health and host:
                    try:
                        resp = requests.get(f"{host}{health}", timeout=2)
                        if resp.status_code == 200:
                            return {"success": True, "message": f"{provider.get('name')} started", "pid": proc.pid}
                    except Exception:
                        pass
                time.sleep(1)
            return {"success": True, "message": f"{provider.get('name')} starting", "pid": proc.pid}
        except FileNotFoundError:
            return {"success": False, "error": f"{cmd[0]} not found. Install it first."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def stop_local_llm(self, provider_id: str) -> Dict:
        proc = self._subprocesses.get(provider_id)
        if proc:
            try:
                if os.name == 'nt':
                    proc.terminate()
                else:
                    os.kill(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
                self._subprocesses.pop(provider_id, None)
                return {"success": True, "message": "Stopped"}
            except Exception as e:
                try:
                    proc.kill()
                    self._subprocesses.pop(provider_id, None)
                    return {"success": True, "message": "Force stopped"}
                except Exception:
                    return {"success": False, "error": str(e)}
        provider = self.config.get("providers", {}).get(provider_id)
        if provider_id == "ollama":
            try:
                subprocess.run(["ollama", "stop"], capture_output=True, timeout=10)
                return {"success": True, "message": "Ollama stopped"}
            except Exception:
                pass
        return {"success": True, "message": "Not running"}

    def cleanup_subprocesses(self):
        for provider_id in list(self._subprocesses.keys()):
            self.stop_local_llm(provider_id)

    def __del__(self):
        self.cleanup_subprocesses()
