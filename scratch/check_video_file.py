import subprocess
import json
from pathlib import Path

video_path = "F:\\000000 test\\MyProject_v4\\MyProject_v4\\videos\\scene_1_shot_1.mp4"

print("Exists:", Path(video_path).exists())
print("Size:", Path(video_path).stat().st_size if Path(video_path).exists() else "N/A")

# Read first 16 bytes
if Path(video_path).exists():
    with open(video_path, "rb") as f:
        print("Header bytes:", f.read(32))

try:
    cmd = [
        "C:\\ffmpeg\\bin\\ffprobe.exe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,pix_fmt",
        "-of", "json",
        video_path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    print("ffprobe stdout:")
    print(res.stdout)
    print("ffprobe stderr:")
    print(res.stderr)
except Exception as e:
    print("ffprobe failed or timed out:", e)
