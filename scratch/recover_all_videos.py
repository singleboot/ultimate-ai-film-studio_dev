import json
import shutil
import os
from pathlib import Path
from datetime import datetime

def recover_all():
    src_dir = Path("F:/001 Comfyui Easy installer/ComfyUI-Easy-Install/ComfyUI-Easy-Install/ComfyUI/output/video")
    dest_dir = Path("F:/000000 test/MyProject_v4/MyProject_v4/videos")
    state_file = Path("F:/000000 test/MyProject_v4/MyProject_v4/project_state.json")
    
    if not src_dir.exists():
        print(f"Source directory {src_dir} does not exist.")
        return
        
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Backup project state
    backup_file = state_file.with_suffix(".json.bak")
    shutil.copy2(state_file, backup_file)
    print(f"Backed up project state to {backup_file}")
    
    # Load state
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
        
    scenes = state.get("screenplayData", {}).get("scenes", [])
    
    # We want LTX-2_00016_.mp4 to LTX-2_00045_.mp4 (30 files for 30 shots)
    video_files = []
    for i in range(16, 46):
        filename = f"LTX-2_{i:05d}_.mp4"
        if (src_dir / filename).exists():
            video_files.append(filename)
        else:
            print(f"Warning: Expected video file {filename} does not exist in source directory.")
            
    print(f"Discovered {len(video_files)} videos for recovery: {video_files}")
    
    # Map them to the shots sequentially
    video_idx = 0
    copied_count = 0
    
    for scene_idx, scene in enumerate(scenes):
        for shot_idx, shot in enumerate(scene.get("shots", [])):
            if video_idx >= len(video_files):
                print("No more video files available to map.")
                break
                
            src_filename = video_files[video_idx]
            src_file = src_dir / src_filename
            dest_filename = f"scene_{scene_idx+1}_shot_{shot_idx+1}.mp4"
            dest_file = dest_dir / dest_filename
            
            # Copy
            shutil.copy2(src_file, dest_file)
            print(f"Copied {src_filename} to {dest_filename}")
            
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
    recover_all()
