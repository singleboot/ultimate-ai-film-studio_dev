import sys
from pathlib import Path

# Add app to path
app_dir = Path("f:/MY APP/ultimate-ai-film-studio-v10/app")
sys.path.append(str(app_dir))

from core.llm_engine import LLMEngine
import requests

engine = LLMEngine(settings_path="C:/Users/avik/AppData/Roaming/UltimateAIFilmStudio/settings.json")
print("Settings path:", engine._settings_path)
print("Config providers keys:", engine.config.get("providers", {}).keys())

provider = "app_llm"
prov_cfg = engine.config.get("providers", {}).get(provider, {})
print("prov_cfg:", prov_cfg)

health_ep = prov_cfg.get("health_endpoint", "")
print("health_ep:", health_ep)

# Get host from settings
import json
with open("C:/Users/avik/AppData/Roaming/UltimateAIFilmStudio/settings.json", "r", encoding="utf-8") as f:
    gs = json.load(f)
host = gs.get("llm", {}).get("host", "")
print("Settings host:", host)

if host:
    host = host.replace("localhost", "127.0.0.1")
print("Normalized host:", host)

prov_host = host or prov_cfg.get("host", "")
print("prov_host:", prov_host)

url = f"{prov_host}{health_ep}"
print("Checking URL:", url)
try:
    resp = requests.get(url, timeout=2)
    print("Response status:", resp.status_code)
    print("Response JSON:", resp.json())
except Exception as e:
    print("Request failed:", e)
