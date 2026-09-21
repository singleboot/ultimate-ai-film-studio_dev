import json, os, re, sys, time, requests

B = 'http://127.0.0.1:7860'
PROJECT_PATH = r'F:\000000 test\00006\test03'
SEED = 42424242  # same seed as CHAR_001 for consistency
WORKFLOW = 'qwen_image_sheet_v1'

def get_turnaround_prompt(char_id):
    r = requests.post(f'{B}/api/orchestrator/generate-turnaround',
                      json={'character_id': char_id}, timeout=60).json()
    ta = (r.get('turnaround_request') or {}).get('prompt', '')
    return re.sub(r'\s*ZImage\s+Turbo\.?', '', ta, flags=re.I)

def get_instructions():
    d = json.load(open(os.path.join(PROJECT_PATH, 'project_state.json'), encoding='utf-8'))
    return d.get('projectSettings', {}).get('character_sheet_prompt', '')

def run(char_id):
    char_prompt = get_turnaround_prompt(char_id)
    final_prompt = char_prompt + "\n\n" + get_instructions()
    print(f'=== {char_id}: prompt len {len(final_prompt)} ===', flush=True)
    t0 = time.time()
    body = {
        'provider': 'comfyui', 'model': '', 'prompt': final_prompt, 'host': '',
        'workflow_name': WORKFLOW, 'api_key': '',
        'seed': SEED, 'steps': 4, 'cfg': 1.5, 'resolution': '1024x576',
        'project_path': PROJECT_PATH,
        'input_images': [f'characters/approved/{char_id}.png']
    }
    res = requests.post(f'{B}/api/image/generate', json=body, timeout=1800)
    data = res.json()
    print(f'[{char_id}] generate: success={data.get("success")} filename={data.get("filename")} '
          f'subfolder={data.get("subfolder")} err={data.get("error")} in {time.time()-t0:.0f}s', flush=True)
    if not data.get('success') or not data.get('filename'):
        return
    card_name = f'{char_id}_qwen_{SEED}'
    r = requests.post(f'{B}/api/projects/save-image', json={
        'project_name': 'test03', 'project_path': PROJECT_PATH,
        'stage': 'character_sheets', 'filename': data['filename'],
        'subfolder': data.get('subfolder') or '', 'card_name': card_name
    }, timeout=120).json()
    print(f'[{char_id}] save: success={r.get("success")}', flush=True)
    if r.get('success'):
        s = requests.post(f'{B}/api/orchestrator/save-character-sheet', json={
            'character_id': char_id,
            'sheet_image': f'character_sheets/{card_name}.png',
            'approved': False
        }, timeout=30).json()
        print(f'[{char_id}] register: {s.get("success")}', flush=True)

if __name__ == '__main__':
    requests.get(f'{B}/api/projects/test03/state?path={PROJECT_PATH.replace(chr(92), "/")}', timeout=30)
    for cid in sys.argv[1:] or ['CHAR_002', 'CHAR_003']:
        run(cid)
