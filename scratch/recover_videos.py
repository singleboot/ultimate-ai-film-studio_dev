import json
import shutil
import os
from pathlib import Path

def recover():
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
    
    # We have LTX_2.3_i2v_00001_.mp4 to LTX_2.3_i2v_00006_.mp4
    video_files = sorted([f for f in os.listdir(src_dir) if f.startswith("LTX_2.3_i2v_") and f.endswith(".mp4")])
    print(f"Discovered videos to copy: {video_files}")
    
    # Map them to the shots sequentially
    video_idx = 0
    copied_count = 0
    
    for scene_idx, scene in enumerate(scenes):
        for shot_idx, shot in enumerate(scene.get("shots", [])):
            if video_idx >= len(video_files):
                break
                
            src_file = src_dir / video_files[video_idx]
            dest_filename = f"scene_{scene_idx+1}_shot_{shot_idx+1}.mp4"
            dest_file = dest_dir / dest_filename
            
            # Copy
            shutil.copy2(src_file, dest_file)
            print(f"Copied {src_file.name} to {dest_filename}")
            
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
            
            # Avoid duplicate registrations
            state["savedAssets"] = [a for a in state["savedAssets"] if a.get("filename") != dest_filename]
            state["savedAssets"].append(asset_entry)
            
            video_idx += 1
            copied_count += 1
            
        if video_idx >= len(video_files):
            break
            
    # Save state back
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
        
    print(f"Successfully recovered {copied_count} videos and updated state.")

if __name__ == "__main__":
    recover()
