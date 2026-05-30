import json
import shutil
import os
import subprocess
from pathlib import Path

def recover_by_metadata():
    src_dir = Path("F:/001 Comfyui Easy installer/ComfyUI-Easy-Install/ComfyUI-Easy-Install/ComfyUI/output/video")
    dest_dir = Path("F:/000000 test/MyProject_v4/MyProject_v4/videos")
    state_file = Path("F:/000000 test/MyProject_v4/MyProject_v4/project_state.json")
    
    if not src_dir.exists():
        print(f"Source directory {src_dir} does not exist.")
        return
        
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Load state
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
        
    scenes = state.get("screenplayData", {}).get("scenes", [])
    
    # Scan all video files in src_dir and parse metadata
    video_files = sorted([f for f in os.listdir(src_dir) if f.startswith("LTX-2_") and f.endswith(".mp4")])
    
    # We will build a mapping of: (scene_idx, shot_idx) -> (video_filename, mtime)
    # So that if there are duplicates, we keep the newest one
    mapping = {}
    
    print("Scanning video files metadata...")
    for f in video_files:
        video_path = src_dir / f
        cmd = [
            "C:\\ffmpeg\\bin\\ffprobe.exe",
            "-v", "error",
            "-show_entries", "format_tags=prompt",
            "-of", "json",
            str(video_path)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            data = json.loads(res.stdout)
            prompt_str = data.get("format", {}).get("tags", {}).get("prompt")
            
            input_image = None
            if prompt_str:
                prompt = json.loads(prompt_str)
                for node_id, node in prompt.items():
                    class_type = node.get("class_type")
                    inputs = node.get("inputs", {})
                    if class_type == "LoadImage" and "image" in inputs:
                        input_image = inputs["image"]
                        break
                    for k, v in inputs.items():
                        if isinstance(v, str) and (v.endswith(".png") or v.endswith(".jpg")):
                            input_image = v
                            break
            
            if input_image and input_image.startswith("scene_") and "_shot_" in input_image:
                # Parse scene number and shot number, e.g. "scene_1_shot_1.png"
                parts = input_image.split(".")[0].split("_")
                # parts: ['scene', '1', 'shot', '1']
                try:
                    s_idx = int(parts[1]) - 1
                    sh_idx = int(parts[3]) - 1
                    
                    # Store, keeping the one with larger index or newer timestamp
                    mtime = video_path.stat().st_mtime
                    key = (s_idx, sh_idx)
                    if key not in mapping or mtime > mapping[key]["mtime"]:
                        mapping[key] = {
                            "src_filename": f,
                            "mtime": mtime
                        }
                except Exception as ex:
                    print(f"Error parsing input image name '{input_image}': {ex}")
        except Exception as e:
            print(f"Failed to check {f}: {e}")
            
    print(f"\nDiscovered mapping matches for {len(mapping)} shots:")
    for key, val in sorted(mapping.items()):
        print(f"  Scene {key[0]+1} Shot {key[1]+1} -> {val['src_filename']}")
        
    # Copy files and update project state
    copied_count = 0
    for key, val in mapping.items():
        scene_idx, shot_idx = key
        if scene_idx < len(scenes) and shot_idx < len(scenes[scene_idx].get("shots", [])):
            shot = scenes[scene_idx]["shots"][shot_idx]
            
            src_filename = val["src_filename"]
            src_file = src_dir / src_filename
            dest_filename = f"scene_{scene_idx+1}_shot_{shot_idx+1}.mp4"
            dest_file = dest_dir / dest_filename
            
            # Copy
            shutil.copy2(src_file, dest_file)
            
            # Update state properties
            shot["video_clip"] = dest_filename
            shot["video_status"] = "approved"
            shot["_temp_video_clip"] = None
            shot["_temp_video_subfolder"] = None
            
            # Register in savedAssets
            asset_entry = {
                "type": "video",
                "name": f"scene_{scene_idx+1}_shot_{shot_idx+1}",
                "filename": dest_filename,
                "timestamp": int(dest_file.stat().st_mtime * 1000)
            }
            if "savedAssets" not in state:
                state["savedAssets"] = []
            
            state["savedAssets"] = [a for a in state["savedAssets"] if a.get("filename") != dest_filename]
            state["savedAssets"].append(asset_entry)
            
            copied_count += 1
            
    # Save state back
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
        
    print(f"\nSuccessfully recovered {copied_count} videos using metadata matching.")

if __name__ == "__main__":
    recover_by_metadata()
