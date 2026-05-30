import subprocess
import json
import os
from pathlib import Path

src_dir = Path("F:/001 Comfyui Easy installer/ComfyUI-Easy-Install/ComfyUI-Easy-Install/ComfyUI/output/video")

if not src_dir.exists():
    print(f"Source directory {src_dir} does not exist.")
    exit(1)

video_files = sorted([f for f in os.listdir(src_dir) if f.startswith("LTX-2_") and f.endswith(".mp4")])

for f in video_files:
    video_path = src_dir / f
    # Get metadata with ffprobe
    cmd = [
        "C:\\ffmpeg\\bin\\ffprobe.exe",
        "-v", "error",
        "-show_entries", "format_tags=prompt,workflow",
        "-of", "json",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        data = json.loads(res.stdout)
        tags = data.get("format", {}).get("tags", {})
        prompt_str = tags.get("prompt")
        
        input_image = None
        if prompt_str:
            prompt = json.loads(prompt_str)
            # Find LoadImage node or any node containing the image filename
            for node_id, node in prompt.items():
                class_type = node.get("class_type")
                inputs = node.get("inputs", {})
                if class_type == "LoadImage" and "image" in inputs:
                    input_image = inputs["image"]
                    break
                # Check for other nodes that might load images
                for k, v in inputs.items():
                    if isinstance(v, str) and (v.endswith(".png") or v.endswith(".jpg")):
                        input_image = v
                        break
            
        mtime = datetime_str = datetime_str = datetime_str = ""
        mtime_ts = video_path.stat().st_mtime
        mtime = os.path.split(str(video_path))[1]
        print(f"{f} (mtime: {mtime_ts}) -> Input Image: {input_image}")
    except Exception as e:
        print(f"Failed to parse {f}: {e}")
