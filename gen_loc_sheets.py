import json, os, sys, time, requests

B = 'http://127.0.0.1:7860'
PROJECT_PATH = r'F:\000000 test\00006\test03'
SEED = 42424242  # same seed as character sheets for consistency
WORKFLOW = '360_image_v1'  # dedicated 360 location sheet workflow (settings.workflows.loc_sheet)

def get_location_prompt(loc_id):
    d = requests.get(f'{B}/api/orchestrator/asset-studio', timeout=20).json()
    lb = d.get('location_bible', [])
    loc = next((l for l in lb if (l.get('location_id') or l.get('id')) == loc_id), None)
    if loc:
        name = loc.get('location_name') or loc.get('name') or loc_id
        type_ = loc.get('environment_type') or ''
        mood = loc.get('mood') or ''
        details = loc.get('visual_details') or loc.get('description') or ''
        arch = loc.get('architecture') or loc.get('architectural_style') or ''
        light = loc.get('lighting') or loc.get('lighting_style') or ''
        prompt = f"{name} — a {type_} environment"
        if mood: prompt += f" with a {mood} atmosphere"
        if details: prompt += f". {details}"
        if arch: prompt += f" Featuring {arch} architecture"
        if light: prompt += f" with {light} lighting"
        prompt += ". Comprehensive location reference board showing the full environment across multiple curated views capturing the spatial layout, atmosphere, and key architectural details."
    else:
        prompt = f"{loc_id}. Comprehensive location reference board showing the full environment across multiple curated views capturing the spatial layout, atmosphere, and key architectural details."
    return prompt

def get_instructions():
    d = json.load(open(os.path.join(PROJECT_PATH, 'project_state.json'), encoding='utf-8'))
    return d.get('projectSettings', {}).get('location_sheet_prompt', '')

def run(loc_id):
    location_prompt = get_location_prompt(loc_id)
    final_prompt = location_prompt + "\n\n" + get_instructions()
    print(f'=== {loc_id}: prompt len {len(final_prompt)} ===', flush=True)
    t0 = time.time()
    body = {
        'provider': 'comfyui', 'model': '', 'prompt': final_prompt, 'host': '',
        'workflow_name': WORKFLOW, 'api_key': '',
        'seed': SEED, 'steps': 4, 'cfg': 1.5, 'resolution': None,
        'project_path': PROJECT_PATH,
        'input_images': [f'locations/approved/{loc_id}.png']
    }
    res = requests.post(f'{B}/api/image/generate', json=body, timeout=1800)
    data = res.json()
    print(f'[{loc_id}] generate: success={data.get("success")} filename={data.get("filename")} '
          f'subfolder={data.get("subfolder")} err={data.get("error")} in {time.time()-t0:.0f}s', flush=True)
    if not data.get('success') or not data.get('filename'):
        return
    card_name = f'{loc_id}_qwen_{SEED}'
    r = requests.post(f'{B}/api/projects/save-image', json={
        'project_name': 'test03', 'project_path': PROJECT_PATH,
        'stage': 'location_sheets', 'filename': data['filename'],
        'subfolder': data.get('subfolder') or '', 'card_name': card_name
    }, timeout=120).json()
    print(f'[{loc_id}] save: success={r.get("success")}', flush=True)
    if r.get('success'):
        s = requests.post(f'{B}/api/orchestrator/save-location-sheet', json={
            'location_id': loc_id,
            'sheet_image': f'location_sheets/{card_name}.png',
            'approved': False
        }, timeout=30).json()
        print(f'[{loc_id}] register: {s.get("success")}', flush=True)

if __name__ == '__main__':
    # ensure project loaded so orchestrator writes persist
    requests.get(f'{B}/api/projects/test03/state?path={PROJECT_PATH.replace(chr(92), "/")}', timeout=30)
    for lid in sys.argv[1:] or ['LOC_001', 'LOC_002', 'LOC_003']:
        run(lid)
