# Implementation Plan: Asset Generation Architecture

## Overview
Upgrade the character + location T2I generation pipeline to the full architecture spec.
Generates reusable cinematic assets (master continuity references), not random AI art.
Uses ZIMAGE TURBO → Flux2 Klein → LTX 2.3 pipeline.

---

## Phase 1: Frontend T2I Prompt Builders (Tier 1)
**Files:** `app/ui/templates/index.html`

### 1a: Add `buildLocationMasterPrompt` function
Create a new function mirroring `buildCharacterMasterPrompt` but for locations, matching the spec's LOCATION T2I MASTER PROMPT template.

Input fields to use:
```
location_name, environment_type, mood, architecture_style, era, film_aesthetic,
visual_style, cinematic_features, environmental_storytelling, lighting_style
```

Prompt structure per spec:
- Cinematic environment reference image
- Visual embodiment of mood
- Architectural identity
- Era + cinematic inspiration + rendering style
- Cinematic features emphasis
- Environmental storytelling
- Continuity-safe environment consistency rules
- Wide composition, clear depth
- Lighting style
- "Avoid" rules (random characters, action scenes, etc.)
- Output style tags

### 1b: Update `assetGenerateImage` at line 6038
Change the location branch from:
```javascript
const prompt = type === 'char'
    ? buildCharacterMasterPrompt(asset)
    : (asset.image_prompt || '');
```
to:
```javascript
const prompt = type === 'char'
    ? buildCharacterMasterPrompt(asset)
    : buildLocationMasterPrompt(asset);
```

### 1c: Align `buildCharacterMasterPrompt` with spec
Add `project_visual_language` to the prompt (compose from genre + visual style + film aesthetic). Update "Avoid" rules to match spec exactly. Add rendering philosophy from spec.

---

## Phase 2: Bible Field Expansion (Tier 2)
**Files:** `app/core/orchestrator.py`, `app/ui/templates/index.html`

### 2a: Add new fields to LLM system prompt for character bible generation
In `orchestrator.py` around line 719-732, add to the LLM's requested character fields:
- `emotional_wounds` — underlying traumas/drives
- `motivations` — what drives the character
- `signature_behavior` — distinctive gestures/mannerisms

Also add `time_period` to location bible generation (currently missing from LLM prompt).

### 2b: Update `charData`/`locationData` mapping in `loadAssetStudioData`
Around line 4961-4978, pass through ALL bible fields instead of dropping most:
```javascript
charData = data.character_bible.map(ch => ({ ...ch }));
locationData = data.location_bible.map(loc => ({ ...loc }));
```

### 2c: Update card rendering to show new fields
In `renderCharacterAssets` and `renderLocationAssets`, add displays for the new fields (`emotional_wounds`, `motivations`, `signature_behavior` for chars; `time_period` for locs) similar to how `personality` and `physical_appearance` are shown.

---

## Phase 3: Backend Prompt Builder Enhancement (Tier 3)
**Files:** `app/core/orchestrator.py`

### 3a: Replace character prompt template (lines 2066-2077)
Replace the simple LLM prompt with the architecture doc's CHARACTER T2I MASTER PROMPT template:
- Full-body character reference
- Personality + emotional wounds
- Visual identity
- Clothing continuity
- Era + film aesthetic + visual style
- Reusable cinematic asset framing
- Detailed continuity rules
- "Avoid" rules

### 3b: Replace location prompt template (lines 2085-2096)
Replace the simple LLM prompt with the architecture doc's LOCATION T2I MASTER PROMPT template:
- Environment reference image
- Mood + architecture
- Era + film aesthetic + visual style
- Cinematic features + environmental storytelling
- Continuity-safe environment rules
- Wide composition
- "Avoid" rules

---

## Phase 4: Variant System (Tier 3)
**Files:** `app/core/orchestrator.py`, `app/ui/templates/index.html`

### 4a: Backend endpoint `POST /api/orchestrator/generate-variant`
New method `generate_asset_variant(asset_type, asset, current_image, variant_instruction, lock_rules)`:
1. Builds a prompt that includes: current asset data + variant instruction + locked continuity rules
2. Calls LLM to generate a variant of the `image_prompt` that preserves continuity
3. Returns 3 variant prompts

### 4b: Frontend UI for variants
- `showAssetVariantInput(id, type)` — shows input field on card
- `generateAssetVariant(id, type, instruction)` — calls backend, creates variant cards
- Variants displayed as sub-cards under the main asset card
- User can approve/reject individual variants

### 4c: Variant data structure
Update `project_graph` to store variants per asset:
```json
{
  "character_id": "CHAR_001",
  "variants": [
    {"id": "v1", "prompt": "...", "image": "...", "instruction": "more rain"},
    {"id": "v2", "prompt": "...", "image": "...", "instruction": "darker"}
  ]
}
```

---

## Phase 5: Turnaround/Reference Sheets (Tier 3)
**Files:** `app/core/orchestrator.py`, `app/ui/templates/index.html`

### 5a: Auto-generate on approval+lock
When an asset is approved AND locked, auto-queue turnaround sheet generation:
- Front/3/4 view, back view, detail close-ups
- System-controlled prompt (user does NOT edit)
- Stored in `project_graph["character_sheets"]` / `["location_sheets"]`

### 5b: Sheet display in frontend
After sheets are generated, show them in a dedicated expandable section on the asset card. Sheets are read-only (system-controlled).

---

## Phase 6: Variant/Approval End-to-End Wiring
**Files:** `app/core/orchestrator.py`, `app/main.py`

### 6a: Approval lock gates
After approval:
- `locks["characters"][char_id] = true`
- Locked assets can't have their `image_prompt` edited
- Variants are still allowed (non-destructive)
- Storyboard generation checks `approvals.characters_approved && approvals.locations_approved`

### 6b: Persistence for variants + sheets
Add `_save_orchestrator_state()` calls after variant/sheet generation to persist new data immediately.

---

## File Change Summary

| File | What Changes |
|------|-------------|
| `app/core/orchestrator.py` | Add `generate_asset_variant()`, add `generate_turnaround_sheet()`, update `generate_asset_prompt()` with spec prompts, expand LLM system prompt for new bible fields |
| `app/main.py` | Add `POST /api/orchestrator/generate-variant`, `POST /api/orchestrator/generate-sheet` endpoints |
| `app/ui/templates/index.html` | Add `buildLocationMasterPrompt()`, align `buildCharacterMasterPrompt()`, update `loadAssetStudioData()` mapping, update card rendering, add variant UI, add sheet display, update `assetGenerateImage()` |

## Edge Cases

1. **Missing fields** — If `emotional_wounds` or `motivations` are empty in the bible, the prompt builder should skip that section gracefully (use `'—'` placeholder or omit)
2. **Approval lock conflict** — If user tries to edit `image_prompt` on a locked asset, show a warning
3. **Variant without base image** — If no generated image exists, variants should still generate prompts (show "No image — prompt only" state)
4. **Sheet generation failure** — If turnaround sheet generation fails, preserve the approved asset and log the error (don't block the pipeline)
5. **Race between variant and save** — Same fix as prompt persistence: call `_save_orchestrator_state()` immediately after variant/sheet generation
