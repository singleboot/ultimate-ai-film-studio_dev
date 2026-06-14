import json

project_path = "F:\\01_PROJECT\\FIFA_V1\\WORLD CUP\\project_state.json"

with open(project_path, 'r', encoding='utf-8') as f:
    state = json.load(f)

pg = state.get("_orchestrator_memory", {}).get("project_graph", {})

for ch in pg.get("character_bible", []):
    img = ch.get("savedImage")
    cid = ch.get("character_id")
    if img and cid:
        pg["character_assets"][cid] = {
            "generation_history": [img],
            "approved_image": img,
            "approved": True
        }

for loc in pg.get("location_bible", []):
    img = loc.get("savedImage")
    lid = loc.get("location_id")
    if img and lid:
        pg["location_assets"][lid] = {
            "generation_history": [img],
            "approved_image": img,
            "approved": True
        }

with open(project_path, 'w', encoding='utf-8') as f:
    json.dump(state, f, indent=2)

print("Fixed character_assets object format!")
