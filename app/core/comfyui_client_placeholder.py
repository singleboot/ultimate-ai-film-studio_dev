"""
Placeholder ComfyUI client - returns errors indicating ComfyUI is not connected.
Actual ComfyUI integration can be enabled when ComfyUI is running.
"""

class ComfyUIClient:
    def __init__(self, config_path: str = None):
        self.host = "http://localhost:8188"
        self.client_id = "ultimate_ai_film_studio"
        self.queue = []
        self.history = []

    def test_connection(self):
        return {"success": False, "error": "ComfyUI not connected. Start ComfyUI to enable image/video generation."}

    def get_categories(self):
        return {}

    def get_available_checkpoints(self):
        return []

    def generate_image(self, prompt, model, width=1024, height=1024, seed=-1, **kwargs):
        return {"success": False, "error": "ComfyUI not connected. Start ComfyUI to generate images."}

    def generate_video(self, prompt, model, input_image=None, width=1280, height=720, frames=81, fps=24, **kwargs):
        return {"success": False, "error": "ComfyUI not connected. Start ComfyUI to generate videos."}

    def set_host(self, host):
        self.host = host