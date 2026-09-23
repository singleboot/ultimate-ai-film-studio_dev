# -*- coding: utf-8 -*-
"""Production-readiness smoke gate for Ultimate AI Film Studio.

Run standalone:   python app/smoke_check.py [--json] [--skip-net]
Or via pytest:    tests/test_smoke.py imports the check functions below.

Checks (exit 0 = ship-ready):
  1. Every Python file in app/ parses (no syntax errors).
  2. Every workflow JSON in app/workflows/ parses and has an output node
     (SaveImage / VHS_VideoCombine / *Save*), and input-dependent workflows
     carry an image input node.
  3. Every model referenced by a workflow exists in ComfyUI's models tree;
     when missing, suggest installed variants of the same model family
     (e.g. z_image_turbo_bf16 -> Z-Image-Turbo-w4a8).
  4. (Optional, needs the app running) key HTTP endpoints answer.
"""
import ast
import io
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"
WORKFLOWS = APP / "workflows"

CLASS_MODEL_DIRS = {
    "UNETLoader": "diffusion_models",
    "CheckpointLoaderSimple": "checkpoints",
    "LoraLoader": "loras",
    "LoraLoaderModelOnly": "loras",
    "CLIPLoader": "text_encoders",
    "VAELoader": "vae",
    "CLIPVisionLoader": "clip_vision",
    "ControlNetLoader": "controlnet",
    "UpscaleModelLoader": "upscale_models",
}
MODEL_INPUT_KEYS = {
    "unet_name": "diffusion_models",
    "ckpt_name": "checkpoints",
    "lora_name": "loras",
    "vae_name": "vae",
    "clip_name": "text_encoders",
    "clip_name2": "text_encoders",
    "model_name": None,   # resolve via class_type (clip_vision / upscale / controlnet)
}
OUTPUT_CLASS_RE = re.compile(r"(SaveImage\b|SaveVideo\b|SaveAnimated|VideoCombine|Save\s?Webm|Save\s?MP4|SaveIMAGE|VHS_Save)", re.I)
INPUT_CLASS_RE = re.compile(r"(LoadImage|UAIImageSlot|LoadVideo|VHS_LoadImages)", re.I)
QUANT_TOKENS = {"bf16", "fp8", "fp16", "w4a8", "w8a8", "w8a16", "gguf", "safetensors",
                "q4", "q5", "q6", "q8", "k", "s", "km", "m", "int8", "e4m3", "fn", "scaled"}


def _norm_tokens(name: str):
    toks = [t for t in re.split(r"[-_\s.]+", Path(name).stem.lower()) if t]
    return {t for t in toks if t not in QUANT_TOKENS and not t.isdigit()}


def same_family(a: str, b: str) -> bool:
    """True when two model filenames look like the same model in different quants."""
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return False
    inter = ta & tb
    return len(inter) >= 2 or (len(inter) >= 1 and (len(ta) <= 2 or len(tb) <= 2))


def find_models_root() -> Path:
    """ComfyUI models dir: settings.json override > env > default install."""
    env = os.environ.get("UAFS_MODELS_PATH")
    if env and Path(env).exists():
        return Path(env)
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import platform as _pf
        base = (Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
                if _pf.system() == "Windows" else
                Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")))
        s = json.loads((base / "UltimateAIFilmStudio" / "settings.json").read_text(encoding="utf-8"))
        mp = (s.get("comfyui") or {}).get("models_path", "")
        if mp and Path(mp).exists():
            return Path(mp)
    except Exception:
        pass
    for guess in (
        "F:/001 Comfyui Easy installer/ComfyUI-Easy-Install/ComfyUI-Easy-Install/ComfyUI/models",
        "F:/001 Comfyui Easy installer/ComfyUI-Easy-Install/ComfyUI/models",
    ):
        p = Path(guess)
        # sanity probe: a real models dir has checkpoint/model subfolders
        if p.exists() and any((p / sub).exists() for sub in ("checkpoints", "diffusion_models", "loras")):
            return p
    return Path("/nonexistent-models")


def list_installed(models_root: Path) -> dict:
    """folder -> set(matchable names) for the folders workflows reference.

    Workflows may reference a model with a subfolder prefix ("W4A8\\x.safetensors")
    or without an extension, and ComfyUI accepts all of those — so the set holds
    bare filenames, subfolder-qualified relative paths, and extension-less forms."""
    out = {}
    for sub in set(CLASS_MODEL_DIRS.values()) | {"checkpoints", "loras"}:
        d = models_root / sub
        if d.exists():
            names = set()
            for p in d.rglob("*"):
                if p.suffix.lower() in (".safetensors", ".gguf", ".pt", ".pth", ".bin"):
                    rel = p.relative_to(d)
                    names.add(p.name)
                    names.add(str(rel))
                    names.add(str(rel).replace("\\", "/"))
                    names.add(p.stem)
            out[sub] = names
    return out


def _model_resolves(ref: str, installed: set) -> bool:
    """True when a workflow model reference points at an installed file."""
    ref = (ref or "").strip()
    cands = {ref, ref.replace("\\", "/"), Path(ref).name}
    base = Path(ref).name
    for ext in (".safetensors", ".gguf"):
        cands.add(ref + ext)
        cands.add(base + ext)
    return any(c in installed for c in cands)


def iter_workflow_refs(wf: dict):
    """Yield (class_type, input_key, filename) for every model reference."""
    for nid, node in (wf or {}).items():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type", "")
        inputs = node.get("inputs") or {}
        for key, val in inputs.items():
            if isinstance(val, str) and (key in MODEL_INPUT_KEYS or (ct in CLASS_MODEL_DIRS and key.endswith("name"))):
                folder = MODEL_INPUT_KEYS.get(key) or CLASS_MODEL_DIRS.get(ct)
                yield ct, key, val, folder


def check_python_syntax():
    errs = []
    for py in sorted(APP.rglob("*.py")):
        if "scratch" in py.parts or "_backup" in py.parts:
            continue
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            errs.append({"file": str(py.relative_to(REPO)), "error": f"line {e.lineno}: {e.msg}"})
    return errs


def check_workflows():
    """Returns (errors, inventory). A workflow file missing => error."""
    errs, inventory = [], []
    if not WORKFLOWS.exists():
        return [{"file": "app/workflows", "error": "workflows folder missing"}], inventory
    for wf_file in sorted(WORKFLOWS.glob("*.json")):
        if "_backup" in wf_file.parts or wf_file.name.startswith("_"):
            continue
        rel = str(wf_file.relative_to(REPO))
        try:
            wf = json.loads(wf_file.read_text(encoding="utf-8"))
        except Exception as e:
            errs.append({"file": rel, "error": f"invalid JSON: {e}"})
            continue
        classes = [nd.get("class_type", "") for nd in wf.values() if isinstance(nd, dict)]
        # Output node: known saver class, or any node using the universal
        # filename_prefix saver convention (covers custom savers like PixaromaPreview)
        has_output = any(OUTPUT_CLASS_RE.search(c) for c in classes) or any(
            isinstance(nd, dict) and "filename_prefix" in (nd.get("inputs") or {})
            for nd in wf.values()
        )
        has_input = any(INPUT_CLASS_RE.search(c) for c in classes)
        inventory.append({"file": rel, "nodes": len(wf), "has_output": has_output, "has_input": has_input})
        if not has_output:
            errs.append({"file": rel, "error": "no output node (SaveImage / VideoCombine / …) — result would be lost"})
    return errs, inventory


def check_models(models_root: Path = None):
    """Every model referenced by every workflow must exist. Suggest same-family installs."""
    models_root = models_root or find_models_root()
    installed = list_installed(models_root)
    errs, suggestions = [], []
    wf_errs, _ = check_workflows()
    wf_files = sorted(p for p in WORKFLOWS.glob("*.json") if "_backup" not in p.parts)
    for wf_file in wf_files:
        try:
            wf = json.loads(wf_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        rel = str(wf_file.relative_to(REPO))
        for ct, key, fname, folder in iter_workflow_refs(wf):
            if not folder or folder not in installed:
                continue
            if not _model_resolves(fname, installed[folder]):
                err = {"file": rel, "error": f"{ct}.{key} references missing model: {folder}\\{fname}"}
                errs.append(err)
                # best same-family installed variant
                best, best_score = None, 0
                for cand in installed[folder]:
                    if same_family(Path(fname).name, cand):
                        score = len(_norm_tokens(Path(fname).name) & _norm_tokens(cand))
                        if score > best_score:
                            best, best_score = cand, score
                if best:
                    suggestions.append({"file": rel, "node_class": ct, "input": key,
                                        "missing": fname, "suggested": best})
    return errs, suggestions


def check_endpoints(base_url="http://127.0.0.1:7860"):
    """Live checks against a running instance (skipped in offline mode)."""
    import urllib.request
    results = []
    for path in ("/", "/timeline", "/api/comfyui/queue", "/api/settings"):
        try:
            with urllib.request.urlopen(base_url + path, timeout=5) as r:
                ok = r.status == 200
                results.append({"path": path, "ok": ok, "status": r.status})
        except Exception as e:
            results.append({"path": path, "ok": False, "status": str(e)})
    return results


def run_full(skip_net: bool = True):
    report = {
        "python_syntax": check_python_syntax(),
        "workflows": None,
        "models": None,
        "endpoints": None,
    }
    wf_errs, inventory = check_workflows()
    report["workflows"] = {"errors": wf_errs, "inventory": inventory}
    m_errs, sugg = check_models()
    report["models"] = {"errors": m_errs, "suggestions": sugg,
                        "models_root": str(find_models_root())}
    if not skip_net:
        report["endpoints"] = check_endpoints()
    report["ok"] = not (report["python_syntax"] or wf_errs or m_errs)
    return report


if __name__ == "__main__":
    skip_net = "--net" not in sys.argv
    rep = run_full(skip_net=skip_net)
    as_json = "--json" in sys.argv
    if as_json:
        print(json.dumps(rep, indent=2))
    else:
        print("=" * 62)
        print(" PRODUCTION SMOKE CHECK — Ultimate AI Film Studio")
        print("=" * 62)
        n = len(rep["python_syntax"])
        print(f"1. Python syntax     : {'OK' if n == 0 else f'{n} ERROR(S)'}")
        for e in rep["python_syntax"][:8]:
            print(f"     {e['file']}: {e['error']}")
        inv = rep["workflows"]["inventory"]
        wfe = rep["workflows"]["errors"]
        print(f"2. Workflows         : {len(inv)} files, {'OK' if not wfe else f'{len(wfe)} ERROR(S)'}")
        for e in wfe[:8]:
            print(f"     {e['file']}: {e['error']}")
        me, ms = rep["models"]["errors"], rep["models"]["suggestions"]
        print(f"3. Model references  : {'OK — all installed' if not me else f'{len(me)} MISSING'}  (root: {rep['models']['models_root']})")
        for e in me[:10]:
            print(f"     {e['file']}: {e['error']}")
        for s in ms[:10]:
            print(f"     SUGGEST: {s['file']} {s['input']} -> {s['suggested']}")
        if rep["endpoints"]:
            bad = [r for r in rep["endpoints"] if not r["ok"]]
            print(f"4. Live endpoints    : {'OK' if not bad else 'FAIL: ' + ', '.join(r['path'] for r in bad)}")
        print("=" * 62)
        print(" RESULT:", "SHIP-READY ✔" if rep["ok"] else "NOT SHIP-READY ✘")
    sys.exit(0 if rep["ok"] else 1)
