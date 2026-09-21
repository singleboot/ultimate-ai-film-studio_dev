import json, os, sys, time, requests, shutil

B = 'http://127.0.0.1:7860'
PROJECT_PATH = r'F:\000000 test\00006\test03'
SEED = 42424242

def get_turnaround_prompt(char_id):
    r = requests.post(f'{B}/api/orchestrator/generate-turnaround',
                      json={'character_id': char_id}, timeout=30).json()
    ta = (r.get('turnaround_request') or {}).get('prompt', '')
    ta = ta.replace(r'\s*ZImage\s+Turbo\.?', '', regex=False) if False else ta
    import re
    ta = re.sub(r'\s*ZImage\s+Turbo\.?', '', ta, flags=re.I)
    return ta

def get_instructions():
    d = json.load(open(os.path.join(PROJECT_PATH, 'project_state.json'), encoding='utf-8'))
    return d.get('projectSettings', {}).get('character_sheet_prompt', '')

def build_final_prompt():
    char_prompt = get_turnaround_prompt('CHAR_001')
    instructions = get_instructions()
    fp = char_prompt + "\n\n" + instructions
    # style suffix: visual_style / film_aesthetic are null in project_info -> skip
    return fp

def generate(workflow_name, final_prompt, tag, steps=4, cfg=1.5, resolution='1024x576'):
    print(f'\n=== [{tag}] workflow={workflow_name} seed={SEED} steps={steps} cfg={cfg} res={resolution} ===', flush=True)
    t0 = time.time()
    body = {
        'provider': 'comfyui', 'model': '', 'prompt': final_prompt, 'host': '',
        'workflow_name': workflow_name, 'api_key': '',
        'seed': SEED, 'steps': steps, 'cfg': cfg, 'resolution': resolution,
        'project_path': PROJECT_PATH,
        'input_images': ['characters/approved/CHAR_001.png']
    }
    # Long timeout: qwen may cold-load (~20 min)
    try:
        res = requests.post(f'{B}/api/image/generate', json=body, timeout=1800)
        res.raise_for_status()
        data = res.json()
    except requests.exceptions.ReadTimeout:
        # sync endpoint keeps running server-side; poll progress instead
        print(f'[{tag}] client timeout — polling server-side job...', flush=True)
        data = poll_server_job(tag)
    except Exception as e:
        print(f'[{tag}] request error: {e}', flush=True)
        data = {'success': False, 'error': str(e)}
    elapsed = time.time() - t0
    print(f'[{tag}] generate returned in {elapsed:.0f}s: success={data.get("success")} '
          f'filename={data.get("filename")} subfolder={data.get("subfolder")} err={data.get("error")}', flush=True)
    return data

def poll_server_job(tag):
    # poll /api/image/progress until done/error
    while True:
        try:
            p = requests.get(f'{B}/api/image/progress', timeout=10).json()
        except Exception:
            p = {}
        st = p.get('status')
        if st == 'done':
            # generation finished; figure out output filename from queue history later
            return {'success': True, 'status': 'done'}
        if st == 'error':
            return {'success': False, 'error': p.get('label') or 'generation error'}
        time.sleep(15)

def save_and_register(data, tag, card_suffix):
    filename = data.get('filename')
    if not data.get('success') or not filename:
        print(f'[{tag}] SKIP save (no filename)', flush=True)
        return None
    card_name = f'CHAR_001_{card_suffix}_{SEED}'
    r = requests.post(f'{B}/api/projects/save-image', json={
        'project_name': 'test03', 'project_path': PROJECT_PATH,
        'stage': 'character_sheets', 'filename': filename,
        'subfolder': data.get('subfolder') or '',
        'card_name': card_name
    }, timeout=60).json()
    print(f'[{tag}] save-image: success={r.get("success")} saved={r.get("saved_path") or r.get("path") or r.get("filename")}', flush=True)
    if r.get('success'):
        sheet_path = f'character_sheets/{card_name}.png'
        s = requests.post(f'{B}/api/orchestrator/save-character-sheet', json={
            'character_id': 'CHAR_001', 'sheet_image': sheet_path, 'approved': False
        }, timeout=30).json()
        print(f'[{tag}] save-character-sheet: success={s.get("success")}', flush=True)
        return sheet_path
    return None

if __name__ == '__main__':
    final_prompt = build_final_prompt()
    # Reuse a previously saved prompt file so all legs share the exact same text
    if os.path.exists('compare_final_prompt.txt'):
        final_prompt = open('compare_final_prompt.txt', encoding='utf-8').read().strip()
    print(f'final prompt len: {len(final_prompt)}', flush=True)
    with open('compare_final_prompt.txt', 'w', encoding='utf-8') as f:
        f.write(final_prompt)

    workflow = sys.argv[1] if len(sys.argv) > 1 else 'klein'
    if workflow == 'klein':
        data = generate('image_flux2_klein_charactersheet_v2', final_prompt, 'KLEIN')
        save_and_register(data, 'KLEIN', 'klein')
    elif workflow == 'qwen':
        data = generate('qwen_image_sheet_v1', final_prompt, 'QWEN')
        save_and_register(data, 'QWEN', 'qwen')
    else:
        # Krea2 identity edit: native 8 steps / cfg 1, latent size-matched to input
        data = generate('krea2_identity_edit_v1', final_prompt, 'KREA2', steps=8, cfg=1.0, resolution=None)
        save_and_register(data, 'KREA2', 'krea2')
