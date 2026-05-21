import json
import os
import subprocess
import time
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any
from pathlib import Path


class LLMEngine:
    def __init__(self, config_path: str = None, settings_path: str = None):
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "llm_providers.json"
        self.config = self._load_config(config_path)
        self.current_provider = None
        self.current_model = None
        self.connection_status = {}
        # Track subprocesses for local engines
        self._subprocesses: Dict[str, subprocess.Popen] = {}
        if settings_path:
            self._settings_path = Path(settings_path)
        else:
            self._settings_path = Path(__file__).parent.parent / "settings.json"

    def _load_config(self, config_path: str) -> Dict:
        """Load LLM provider configuration."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"providers": {}}

    def _get_api_key(self, provider_id: str, provider: Dict) -> Optional[str]:
        """Get API key: settings.json first, then env var as fallback."""
        # Check settings.json first
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
        # Fallback to env var
        env_var = provider.get("api_key_env", "")
        if env_var:
            return os.environ.get(env_var, "")
        return None

    def get_providers(self) -> List[Dict]:
        """Get list of available LLM providers."""
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
        """Get available models for a provider."""
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

        return [provider.get("default_model", "")]

    def _get_ollama_models(self, provider: Dict) -> List[str]:
        """Get models from local Ollama instance."""
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
        """Get models from LM Studio."""
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
        """Get models from llama.cpp server."""
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
        """Get models from OpenAI API."""
        api_key = self._get_api_key("openai", provider)
        if api_key:
            try:
                headers = {"Authorization": f"Bearer {api_key}"}
                resp = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["id"] for m in data.get("data", []) if not m["id"].startswith("ft:")]
                    # Prioritize chat models
                    chat_models = [m for m in models if any(x in m for x in ["gpt-4o", "gpt-4", "gpt-3.5", "o1", "o3"])]
                    return chat_models[:20] if chat_models else models[:20]
            except Exception:
                pass
        return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]

    def _get_claude_models(self) -> List[str]:
        """Get available Claude models."""
        return [
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
            "claude-3-5-haiku-20241022"
        ]

    def _get_openrouter_models(self) -> List[str]:
        """Get popular models from OpenRouter."""
        return [
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-3-opus",
            "openai/gpt-4o",
            "openai/gpt-4-turbo",
            "google/gemini-pro-1.5",
            "meta-llama/llama-3.1-70b-instruct"
        ]

    def _get_gemini_models(self) -> List[str]:
        """Get available Gemini models."""
        return [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash"
        ]

    def test_connection(self, provider_id: str, host: str = None, api_key: str = None) -> Dict:
        """Test connection to a provider."""
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
            elif provider_id == "llama_cpp":
                return self._test_llamacpp(provider)
        else:
            key = api_key or self._get_api_key(provider_id, provider)
            if not key:
                return {"success": False, "error": f"API key required for {provider.get('name', provider_id)}"}
            return {"success": True, "message": f"{provider.get('name', provider_id)} configured"}

        return {"success": False, "error": "Connection test not available"}

    def _test_ollama(self, provider: Dict) -> Dict:
        """Test Ollama connection."""
        try:
            host = provider.get("host", "http://localhost:11434")
            response = requests.get(f"{host}/api/tags", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected to Ollama"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def _test_lmstudio(self, provider: Dict) -> Dict:
        """Test LM Studio connection."""
        try:
            host = provider.get("host", "http://localhost:1234")
            response = requests.get(f"{host}/v1/models", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected to LM Studio"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def _test_llamacpp(self, provider: Dict) -> Dict:
        """Test llama.cpp connection."""
        try:
            host = provider.get("host", "http://localhost:8080")
            response = requests.get(f"{host}/v1/models", timeout=5)
            if response.status_code == 200:
                return {"success": True, "message": "Connected to llama.cpp"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Connection failed"}

    def generate(self, provider_id: str, model: str, prompt: str, system_prompt: str = None,
                 host: str = None, images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        """Generate a response from the LLM."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}
        if host:
            provider = dict(provider)
            provider["host"] = host

        if provider_id == "ollama":
            return self._generate_ollama(provider, model, prompt, system_prompt, images=images, **kwargs)
        elif provider_id == "lm_studio":
            return self._generate_lmstudio(provider, model, prompt, system_prompt, images=images, **kwargs)
        elif provider_id == "llama_cpp":
            return self._generate_llamacpp(provider, model, prompt, system_prompt, images=images, api_key=api_key, **kwargs)
        elif provider_id == "openai":
            return self._generate_openai(provider, model, prompt, system_prompt, images=images, api_key=api_key, **kwargs)
        elif provider_id == "claude":
            return self._generate_claude(provider, model, prompt, system_prompt, images=images, api_key=api_key, **kwargs)
        elif provider_id == "openrouter":
            return self._generate_openrouter(provider, model, prompt, system_prompt, images=images, api_key=api_key, **kwargs)
        elif provider_id == "gemini":
            return self._generate_gemini(provider, model, prompt, system_prompt, images=images, api_key=api_key, **kwargs)

        return {"success": False, "error": "Unsupported provider"}

    def _generate_ollama(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, **kwargs) -> Dict:
        """Generate using Ollama."""
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
            response = requests.post(f"{host}/api/chat", json=payload, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_lmstudio(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                           **kwargs) -> Dict:
        """Generate using LM Studio (OpenAI-compatible API)."""
        try:
            host = provider.get("host", "http://localhost:1234/v1")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {"model": model, "messages": messages, "temperature": kwargs.get("temperature", 0.7)}
            response = requests.post(f"{host}/chat/completions", json=payload, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_llamacpp(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                           images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        """Generate using llama.cpp (OpenAI-compatible API)."""
        try:
            host = provider.get("host", "http://localhost:8080")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            user_content = {"role": "user", "content": prompt}
            if images:
                # llama.cpp supports base64 images in content array
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
            response = requests.post(f"{host}/v1/chat/completions", json=payload, headers=headers, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_openai(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        """Generate using OpenAI API."""
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
            response = requests.post(f"{host}/chat/completions", json=payload, headers=headers, timeout=120)
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
        """Generate using Claude (Anthropic) API."""
        key = api_key or self._get_api_key("claude", provider)
        if not key:
            return {"success": False, "error": "Claude API key required"}

        try:
            host = provider.get("host", "https://api.anthropic.com/v1")

            # Build content blocks
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
            response = requests.post(f"{host}/messages", json=payload, headers=headers, timeout=120)
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
        """Generate using OpenRouter."""
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
            response = requests.post(f"{host}/chat/completions", json=payload, headers=headers, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_gemini(self, provider: Dict, model: str, prompt: str, system_prompt: str = None,
                         images: List[str] = None, api_key: str = None, **kwargs) -> Dict:
        """Generate using Gemini."""
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
                    parts.append({
                        "inline_data": {"mime_type": "image/png", "data": img_b64}
                    })
                contents = [{"parts": parts}]
            else:
                contents = [{"parts": [{"text": prompt}]}]

            if system_prompt:
                contents.insert(0, {"parts": [{"text": system_prompt}]})

            payload = {"contents": contents}
            response = requests.post(url, json=payload, timeout=120)
            if response.status_code == 200:
                data = response.json()
                text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return {"success": True, "response": text}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    # === Subprocess Management for Local LLMs ===

    def detect_local_llm(self, provider_id: str) -> Dict:
        """Detect if a local LLM binary is installed."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"installed": False, "error": "Provider not found"}

        binary = provider.get("binary", "")
        if not binary:
            return {"installed": False, "error": "No binary configured"}

        # Check common paths
        search_paths = [binary]
        if os.name == 'nt':  # Windows
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

        # Try `which` / `where` command
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
        """Check if a local LLM is currently running."""
        # Check if we have a tracked subprocess
        proc = self._subprocesses.get(provider_id)
        if proc and proc.poll() is None:
            return {"running": True, "pid": proc.pid}

        # Check by health endpoint
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"running": False}

        health = provider.get("health_endpoint", "")
        host = provider.get("host", "")
        if health and host:
            try:
                resp = requests.get(f"{host}{health}", timeout=3)
                if resp.status_code == 200:
                    return {"running": True, "pid": None}
            except Exception:
                pass

        return {"running": False}

    def launch_local_llm(self, provider_id: str, model_path: str = None) -> Dict:
        """Launch a local LLM as a subprocess."""
        # Check if already running
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

        # For llama.cpp, append model path if provided
        if provider_id == "llama_cpp" and model_path:
            cmd.extend(["--model", model_path])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self._subprocesses[provider_id] = proc

            # Wait for health check
            health = provider.get("health_endpoint", "")
            host = provider.get("host", "")
            timeout = 30
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

            # Started but health check timed out - still return success
            return {"success": True, "message": f"{provider.get('name')} starting", "pid": proc.pid}
        except FileNotFoundError:
            return {"success": False, "error": f"{start_cmd[0]} not found. Install it first."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def stop_local_llm(self, provider_id: str) -> Dict:
        """Stop a local LLM subprocess."""
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

        # Try to stop via provider-specific method
        provider = self.config.get("providers", {}).get(provider_id)
        if provider_id == "ollama":
            try:
                subprocess.run(["ollama", "stop"], capture_output=True, timeout=10)
                return {"success": True, "message": "Ollama stopped"}
            except Exception:
                pass

        return {"success": True, "message": "Not running"}

    def cleanup_subprocesses(self):
        """Stop all tracked subprocesses (call on app shutdown)."""
        for provider_id in list(self._subprocesses.keys()):
            self.stop_local_llm(provider_id)

    def __del__(self):
        self.cleanup_subprocesses()
