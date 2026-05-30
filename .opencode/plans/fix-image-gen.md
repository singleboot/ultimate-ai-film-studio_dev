# Fix: Image Generation Not Starting

## Bug 1: Positional argument mismatch in `app/core/image_engine.py`

**Location:** Line 134-136

**What's wrong:**
```python
handler(provider, model, prompt, host, api_key, width, height,
        workflow_name, input_images, aspect_ratio, resolution, seed, **kwargs)
```

This passes all args **positionally**, but handler signatures have different parameter orders. For `_generate_comfyui_image`:

| Position | Value passed | Actually received as |
|----------|-------------|-------------------|
| 4 | `api_key` (`""`) | → `workflow_name` |
| 5 | `width` (`1024`) | → `input_images` |
| 6 | `height` (`1024`) | → `aspect_ratio` |
| 7 | `workflow_name` (`"image_z_image_turbo"`) | → `resolution` |
| 8 | `input_images` (`None`) | → `seed` |

**Effect:** `workflow_name` always receives empty string (the api_key), so the check `if workflow_name:` at line 188 is always False. The configured T2I workflow is never used.

**Fix:** Change to keyword arguments:

```python
handler(provider=provider, model=model, prompt=prompt, host=host,
        api_key=api_key, width=width, height=height,
        workflow_name=workflow_name, input_images=input_images,
        aspect_ratio=aspect_ratio, resolution=resolution, seed=seed, **kwargs)
```

This way each handler only picks up the kwargs it actually declares in its signature; extras go to `**kwargs`.

---

## Bug 2: Missing `_generate_sdwebui` method in `app/core/image_engine.py`

**Location:** Line 119

`"sdwebui": self._generate_sdwebui` is registered in the handlers dict but the method doesn't exist. If someone uses the "sdwebui" provider, it will crash with AttributeError when executing the dict literal.

**Fix:** Either:
- Add a `_generate_sdwebui` stub method
- Or remove the entry from the handlers dict
- Or implement it properly

---

## Optional: Remove `_save_orchestrator_state` from `app/main.py`

**Location:** Lines 568-595

The `_save_orchestrator_state()` helper and its call in `generate_asset_prompt` is redundant with the frontend's `scheduleSaveState()` + `beforeunload` sync XHR. It adds latency to prompt generation. Remove the helper and the call.
