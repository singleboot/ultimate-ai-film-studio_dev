import requests
import json
import time

url = "http://127.0.0.1:7860/api/image/generate"
payload = {
    "provider": "comfyui",
    "workflow_name": "360_image_v1",
    "prompt": "The Chronarium Hub",
    "input_images": ["F:\\01_PROJECT\\FIFA_V1\\WORLD CUP\\locations\\approved\\LOC_001.png"],
    "project_path": "F:\\01_PROJECT\\FIFA_V1\\WORLD CUP"
}
headers = {"Content-Type": "application/json"}

print("Starting generation...")
start = time.time()
response = requests.post(url, json=payload, headers=headers)
print(f"Elapsed: {time.time() - start:.1f}s")
print(response.status_code)
print(response.json())
