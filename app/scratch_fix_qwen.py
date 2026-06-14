import json
from pathlib import Path
from typing import Dict

def fix():
    client_file = Path(r"f:\MY APP\ultimate-ai-film-studio-v10\app\core\comfyui_client.py")
    lines = client_file.read_text('utf-8').splitlines()
    for i, line in enumerate(lines):
        if 'nd.get("class_type") == "TextEncodeQwenImageEditPlus" and "prompt" in inputs' in line:
            # We want to replace the next two lines:
            # inputs["prompt"] = prompt
            # debug_log.append(...)
            
            new_code = """
                            orig_prompt = inputs["prompt"]
                            if "Transform" in orig_prompt or "input image" in orig_prompt.lower() or "source image" in orig_prompt.lower():
                                inputs["prompt"] = orig_prompt.rstrip() + "\\n\\nSubject Description: " + prompt
                                debug_log.append(f"Combined TextEncodeQwenImageEditPlus {nid} with original prompt")
                            else:
                                inputs["prompt"] = prompt
                                debug_log.append(f"Overwrote TextEncodeQwenImageEditPlus {nid}: '{prompt[:50]}...'")
"""
            # Replace
            lines[i+1:i+3] = new_code.strip('\n').split('\n')
            break
            
    client_file.write_text('\n'.join(lines), 'utf-8')

fix()
