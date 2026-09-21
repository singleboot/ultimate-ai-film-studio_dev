"""
A/B/C Test: Portrait vs Sheet vs Both character references for storyboard generation.
Uses krea2_identity_edit_v1 which takes 2 images: character ref + background.
"""
import requests, shutil, os, time

BASE = "http://127.0.0.1:7860"
PROJECT = r"J:\0002 MY Channels\Haunted Horror Echoes\2026\AI APP PROJECTS\09 SEP\horror 01\horror 02\horror 02"
SCENES_DIR = os.path.join(PROJECT, "scenes")
COMPARISON_DIR = os.path.join(PROJECT, "ref_type_comparison")
os.makedirs(COMPARISON_DIR, exist_ok=True)

SEED = 42
SHOT_FILE = os.path.join(SCENES_DIR, "scene_1_shot_1.png")

# Location background (SC_001 uses LOC_001)
BG = "locations/approved/LOC_001.png"

# Character references
PORTRAIT = "characters/approved/CHAR_001.png"
SHEET = "character_sheets/approved/CHAR_001.png"

PROMPT = (
    "A cinematic high-angle wide shot of Etta Vane, a pale gaunt teenage girl with deep-set "
    "dark circles under hollow green eyes, thin frame. She sits alone in her subterranean "
    "basement surrounded by glowing static monitors, fine-tuning her reel-to-reel recorder. "
    "Wide Shot. Cinematic composition, detailed, film still."
)

tests = [
    ("portrait", "test_A_portrait.png", [PORTRAIT, BG]),
    ("sheet",    "test_B_sheet.png",    [SHEET, BG]),
    ("both",     "test_C_both.png",     [PORTRAIT, SHEET, BG]),
]

results = []

for ref_type, out_name, input_imgs in tests:
    print(f"\n{'='*60}")
    print(f"TEST: {ref_type.upper()} reference")
    print(f"  Input images: {input_imgs}")
    print(f"{'='*60}")
    
    t0 = time.time()
    resp = requests.post(f"{BASE}/api/orchestrator/generate-consistent-shot", json={
        "prompt": PROMPT,
        "seed": SEED,
        "steps": 8,
        "cfg": 1.0,
        "project_path": PROJECT,
        "input_images": input_imgs,
        "aspect_ratio": "16:9",
        "resolution": "1024x576",
    }, timeout=180)
    elapsed = time.time() - t0
    
    data = resp.json()
    if data.get("success"):
        print(f"  Generation time: {elapsed:.1f}s")
        print(f"  Filename: {data.get('filename', 'N/A')}")
        
        if os.path.exists(SHOT_FILE):
            dest = os.path.join(COMPARISON_DIR, out_name)
            shutil.copy2(SHOT_FILE, dest)
            size = os.path.getsize(dest)
            print(f"  Copied to: {out_name} ({size} bytes)")
            results.append({"type": ref_type, "file": out_name, "size": size, "time": elapsed, "images": input_imgs})
        else:
            print(f"  WARNING: {SHOT_FILE} not found!")
            results.append({"type": ref_type, "error": "file not found"})
    else:
        print(f"  FAILED: {data.get('error', 'Unknown error')}")
        results.append({"type": ref_type, "error": data.get('error')})
    
    time.sleep(2)

print(f"\n{'='*60}")
print(f"COMPARISON RESULTS")
print(f"{'='*60}")
for r in results:
    if "error" in r:
        print(f"  {r['type']}: ERROR - {r['error']}")
    else:
        print(f"  {r['type']}: {r['time']:.1f}s, {r['size']} bytes")
        print(f"    Images: {r['images']}")

print(f"\nImages saved to: {COMPARISON_DIR}")
