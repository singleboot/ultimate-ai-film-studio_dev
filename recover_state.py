import json
import os

project_path = "F:\\01_PROJECT\\FIFA_V1\\WORLD CUP\\project_state.json"

with open(project_path, 'r', encoding='utf-8') as f:
    state = json.load(f)

# Rebuild _orchestrator_memory
if "_orchestrator_memory" not in state:
    state["_orchestrator_memory"] = {}

mem = state["_orchestrator_memory"]

mem["ideas"] = state.get("topicIdeas", [])
mem["selected_idea_index"] = state.get("selectedIdeaIndex", -1)
mem["screenplay"] = state.get("screenplayData", {})

# Set up project_graph
if "project_graph" not in mem:
    mem["project_graph"] = {
        "character_bible": [],
        "location_bible": [],
        "prop_bible": [],
        "scene_graph": [],
        "shots": {},
        "character_assets": {},
        "location_assets": {},
        "character_sheets": {},
        "location_sheets": {},
        "approvals": {
            "characters_approved": state.get("_assetStudioApprovals", {}).get("characters", False),
            "locations_approved": state.get("_assetStudioApprovals", {}).get("locations", False),
            "props_approved": False
        },
        "locks": {}
    }

pg = mem["project_graph"]

# Restore character_bible and location_bible
pg["character_bible"] = state.get("charData", [])
pg["location_bible"] = state.get("locationData", [])

# Restore character_assets (maps character_id to image path)
for ch in pg["character_bible"]:
    if ch.get("savedImage"):
        pg["character_assets"][ch.get("character_id")] = ch.get("savedImage")

for loc in pg["location_bible"]:
    if loc.get("savedImage"):
        pg["location_assets"][loc.get("location_id")] = loc.get("savedImage")

with open(project_path, 'w', encoding='utf-8') as f:
    json.dump(state, f, indent=2)

print("Project state successfully recovered!")
