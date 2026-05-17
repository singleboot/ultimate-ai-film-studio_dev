import json
import os
import requests
from typing import Dict, List, Optional, Any
from pathlib import Path


class LLMEngine:
    def __init__(self, config_path: str = None):
        if config_path is None:
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "llm_providers.json"
        self.config = self._load_config(config_path)
        self.current_provider = None
        self.current_model = None
        self.connection_status = {}

    def _load_config(self, config_path: str) -> Dict:
        """Load LLM provider configuration."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")
            return {"providers": {}}

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
        elif provider_id == "openrouter":
            return self._get_openrouter_models()
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

    def test_connection(self, provider_id: str) -> Dict:
        """Test connection to a provider."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        provider_type = provider.get("type")

        if provider_type == "local":
            if provider_id == "ollama":
                return self._test_ollama(provider)
            elif provider_id == "lm_studio":
                return self._test_lmstudio(provider)

        return {"success": True, "message": "Cloud provider - API key required"}

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

    def generate(self, provider_id: str, model: str, prompt: str, system_prompt: str = None, **kwargs) -> Dict:
        """Generate a response from the LLM."""
        provider = self.config.get("providers", {}).get(provider_id)
        if not provider:
            return {"success": False, "error": "Provider not found"}

        if provider_id == "ollama":
            return self._generate_ollama(provider, model, prompt, system_prompt, **kwargs)
        elif provider_id == "lm_studio":
            return self._generate_lmstudio(provider, model, prompt, system_prompt, **kwargs)
        elif provider_id == "openrouter":
            return self._generate_openrouter(provider, model, prompt, system_prompt, **kwargs)
        elif provider_id == "gemini":
            return self._generate_gemini(provider, model, prompt, system_prompt, **kwargs)

        return {"success": False, "error": "Unsupported provider"}

    def _generate_ollama(self, provider: Dict, model: str, prompt: str, system_prompt: str = None, **kwargs) -> Dict:
        """Generate using Ollama."""
        try:
            host = provider.get("host", "http://localhost:11434")
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False
            }
            if system_prompt:
                payload["system"] = system_prompt

            response = requests.post(f"{host}/api/generate", json=payload, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("response", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_lmstudio(self, provider: Dict, model: str, prompt: str, system_prompt: str = None, **kwargs) -> Dict:
        """Generate using LM Studio."""
        try:
            host = provider.get("host", "http://localhost:1234/v1")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": model,
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.7)
            }

            response = requests.post(f"{host}/chat/completions", json=payload, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_openrouter(self, provider: Dict, model: str, prompt: str, system_prompt: str = None, **kwargs) -> Dict:
        """Generate using OpenRouter."""
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return {"success": False, "error": "OpenRouter API key not set. Set OPENROUTER_API_KEY environment variable."}

        try:
            host = provider.get("host", "https://openrouter.ai/api/v1")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }

            payload = {
                "model": model,
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.7)
            }

            response = requests.post(f"{host}/chat/completions", json=payload, headers=headers, timeout=120)
            if response.status_code == 200:
                data = response.json()
                return {"success": True, "response": data.get("choices", [{}])[0].get("message", {}).get("content", "")}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": False, "error": "Generation failed"}

    def _generate_gemini(self, provider: Dict, model: str, prompt: str, system_prompt: str = None, **kwargs) -> Dict:
        """Generate using Gemini."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return {"success": False, "error": "Gemini API key not set. Set GEMINI_API_KEY environment variable."}

        try:
            host = provider.get("host", "https://generativelanguage.googleapis.com/v1beta")
            url = f"{host}/models/{model}:generateContent?key={api_key}"

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