"""
Production Package Parser (v2)
==============================
Parses Executive Producer markdown output into structured data
that matches the frontend's screenplayData, charData, locationData format.

v2: Handles collapsed text (0 newlines) from EP responses,
    extracts characters/locations/shots more robustly.
"""

import re
import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ─── Normalization ────────────────────────────────────────────────────────────

def _normalize_ep_text(ep_output: str) -> str:
    """
    Aggressively normalize collapsed EP text (possibly 0 newlines)
    into something regex-friendly.
    """
    text = ep_output

    # If already well-formed, skip heavy normalization
    if text.count('\n') >= 50:
        return text

    # ── Phase 1: Insert newlines before heading markers ──
    # ####, ###, ##, # headings
    text = re.sub(r'(?<=\S)\s*(#{1,4}\s)', r'\n\n\1', text)

    # ── Phase 2: Insert newlines before bullet / list items ──
    # * NAME (AGE): or * description (uppercase start after *)
    text = re.sub(r'(?<=\S)\s*\*\s+([A-Z])', r'\n* \1', text)
    # - bullet items
    text = re.sub(r'(?<=\S)\s*-\s+\d+\.', r'\n- ', text)
    text = re.sub(r'(?<=\S)\s*-\s+[A-Z]', r'\n- ', text)

    # ── Phase 3: Insert newlines before key structural markers ──
    for marker in ['CHARACTERS:', 'END OF SCREENPLAY', '---', '[SHOT',
                    'PHASE 1', 'PHASE 2', 'PHASE 3', 'PHASE 4', 'PHASE 5',
                    'ACT I', 'ACT II', 'ACT III', 'ACT IV',
                    '===', 'SCREENPLAY', 'CINEMATOGRAPHY',
                    'CHARACTER BIBLE', 'SHOT BEAT', 'SHOT LIST',
                    'VISUAL DIRECTION', 'AI GENERATION', 'COMFYUI',
                    'HERO SHOT', 'PRODUCTION CHARACTER']:
        # Add newline before marker if preceded by non-newline
        text = re.sub(r'(?<=\S)\s*(' + re.escape(marker) + ')', r'\n\n\1', text)

    # ── Phase 4: Insert newlines before SCENE N ──
    text = re.sub(r'(?<=\S)\s*(SCENE\s+\d+)', r'\n\n\1', text, flags=re.IGNORECASE)

    # ── Phase 5: Insert newlines before INT./EXT. ──
    text = re.sub(r'(?<=\S)\s*((?:INT|EXT)\.)', r'\n\1', text)

    # ── Phase 6: Insert newlines after sentence-ending before UPPERCASE NAME ──
    # e.g., "...validation.* MARCUS" → "...validation.\n* MARCUS"
    text = re.sub(r'([.!?])\s*\*\s+([A-Z][A-Z\s]{2,20})\s*\(', r'\1\n* \2(', text)
    # "...description.* THE TRANSMITTER"
    text = re.sub(r'([.!?])\s*\*\s+([A-Z])', r'\1\n* \2', text)

    # ── Phase 7: Clean up excessive whitespace ──
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)

    return text


# ─── Main Parser ──────────────────────────────────────────────────────────────

def parse_production_package(ep_output: str) -> Dict[str, Any]:
    """
    Parse an EP's full production package markdown into structured data.

    Returns:
        {
            "screenplay": { title, logline, tone, dialogue_enabled, scenes: [...] },
            "characters": [ { character_id, character_name, role, ... } ],
            "locations": [ { location_id, location_name, ... } ],
            "shots": [ { shot_id, scene_number, shot_number, ... } ],
            "ai_prompts": [ { name, prompt, scene_ref } ],
            "concepts": [ { title, logline, ... } ],
            "title": str,
            "logline": str,
        }
    """
    # Normalize the text
    text = _normalize_ep_text(ep_output)

    result = {
        "screenplay": None,
        "characters": [],
        "locations": [],
        "shots": [],
        "ai_prompts": [],
        "concepts": [],
        "title": "",
        "logline": "",
    }

    # Extract project title
    title_match = re.search(r'PRODUCTION\s+GREENLIGHT[:\s]*["\u201c\u201d]+([^"\n*]+)["\u201c\u201d]', text)
    if not title_match:
        title_match = re.search(r'(?:PROJECT|Title)[:\s]*["\u201c\u201d]+([^"\n*]+)["\u201c\u201d]', text)
    if not title_match:
        title_match = re.search(r'(?:PROJECT|Title|PRODUCTION)[:\s]*([^\n*"\u201c\u201d]+)', text)
    if title_match:
        result["title"] = title_match.group(1).strip().strip('*').strip()

    # Extract logline
    logline_match = re.search(r'(?:Logline|logline|LOGLINE)[:\s*]*([^.\n]+)', text)
    if logline_match:
        result["logline"] = logline_match.group(1).strip().strip('*').strip()

    # Parse each section
    result["concepts"] = _parse_concepts(text)
    result["screenplay"] = _parse_screenplay(text, result["title"], result["logline"])
    result["characters"] = _parse_characters(text)
    result["locations"] = _parse_locations(text, result["screenplay"])
    result["shots"] = _parse_shot_table(text)
    result["ai_prompts"] = _parse_ai_prompts(text)

    # If no standalone shots found, collect from scenes
    if not result["shots"] and result["screenplay"]:
        for scene in result["screenplay"].get("scenes", []):
            result["shots"].extend(scene.get("shots", []))

    return result


# ─── Concepts ─────────────────────────────────────────────────────────────────

def _parse_concepts(text: str) -> List[Dict]:
    """Parse concept blocks from PHASE 1 or standalone concept sections."""
    concepts = []

    # Try ### CONCEPT N: *TITLE*
    pattern = r'###\s*CONCEPT\s+(\d+)[.:]\s*\*?\*?([^*\n]+)\*?\*?'
    matches = list(re.finditer(pattern, text, re.IGNORECASE))

    if not matches:
        # Try CONCEPT N: *TITLE* without ###
        pattern = r'CONCEPT\s+(\d+)[.:]\s*\*?\*?([^*\n]+)\*?\*?'
        matches = list(re.finditer(pattern, text, re.IGNORECASE))

    for idx, m in enumerate(matches):
        num = int(m.group(1))
        title = m.group(2).strip().rstrip('*').strip()

        # Get text block after this concept until next concept or section end
        block_start = m.end()
        block_end = matches[idx + 1].start() if idx + 1 < len(matches) else text.find('---', block_start)
        if block_end == -1 or block_end <= block_start:
            block_end = min(block_start + 2000, len(text))
        block = text[block_start:block_end]

        logline = _extract_field(block, r'(?:Logline|logline)[:\s]*([^\n]+)')
        visual = _extract_field(block, r'(?:Visual Potential)[:\s]*([^\n]+)')
        emotion = _extract_field(block, r'(?:Emotional Impact)[:\s]*([^\n]+)')

        concepts.append({
            "number": num,
            "title": title,
            "logline": logline,
            "visual_potential": visual,
            "emotional_impact": emotion,
            "selected": False,
        })

    # Mark selected concept
    selected_match = re.search(
        r'EXECUTIVE SELECTION.*?(?:CONCEPT\s+(\d+)|\*\*([^*]+)\*\*)',
        text, re.IGNORECASE
    )
    if selected_match:
        sel_num = selected_match.group(1)
        sel_title = selected_match.group(2)
        for c in concepts:
            if (sel_num and str(c["number"]) == sel_num) or \
               (sel_title and c["title"].lower() in sel_title.lower()):
                c["selected"] = True
                break

    return concepts


# ─── Screenplay / Scenes ──────────────────────────────────────────────────────

def _parse_screenplay(text: str, title: str, logline: str) -> Dict:
    """Parse screenplay scenes from EP output."""
    scenes = []

    # Find screenplay section start — prioritize first SCENE 1 (most reliable)
    sp_start = -1
    # First try: find first SCENE 1 heading (most reliable marker)
    ie_match = re.search(r'(?:#{0,4}\s*)?SCENE\s+1\b', text, re.IGNORECASE)
    if ie_match:
        sp_start = ie_match.start()
    if sp_start == -1:
        # Look for CHARACTERS: which always precedes the screenplay
        ch_match = re.search(r'CHARACTERS:', text, re.IGNORECASE)
        if ch_match:
            sp_start = ch_match.start()
    if sp_start == -1:
        # Try PHASE 2
        idx = text.find('PHASE 2')
        if idx != -1:
            sp_start = idx
    if sp_start == -1:
        # Look for first INT./EXT. line
        ie_match = re.search(r'((?:INT|EXT)\.\s+[^\n]+)', text, re.IGNORECASE)
        if ie_match:
            sp_start = ie_match.start()

    if sp_start == -1:
        return {"title": title or "Untitled Film", "logline": logline,
                "tone": "", "dialogue_enabled": True, "scenes": []}

    # Find screenplay section end
    sp_end = len(text)
    for marker in ['PHASE 3', 'PHASE 4', 'PHASE 5', 'CHARACTER BIBLE',
                    'CINEMATOGRAPHY', 'VISUAL DIRECTION', 'AI GENERATION',
                    'SHOT BEAT', 'SHOT LIST']:
        idx = text.find(marker, sp_start + 100)
        if idx != -1 and idx < sp_end:
            sp_end = idx

    screenplay_text = text[sp_start:sp_end]

    # Extract tone
    tone = ""
    tone_match = re.search(r'(?:Tone|tone|Mood|mood)[:\s]*([^\n]+)', screenplay_text)
    if tone_match:
        tone = tone_match.group(1).strip().strip('*').strip()

    # Parse scenes: handle multiple formats
    scene_patterns = [
        # SCENE N\nINT./EXT. LOCATION (normalized, separate lines)
        r'SCENE\s+(\d+)\s*\n\s*((?:INT|EXT)\.?\s*[^\n]+)',
        # #### SCENE N: LOCATION - TIME (single line)
        r'(?:#{1,4}\s*)?SCENE\s+(\d+)\s*[:\s]+((?:INT|EXT)\.[^\n]*?)(?=SCENE\s+\d+|CHARACTERS:|---|\Z)',
        # SCENE N: LOCATION - TIME (collapsed)
        r'SCENE\s+(\d+)\s*[:\s]+(.*?)(?=####\s*SCENE|SCENE\s+\d+|\Z)',
    ]

    for pat in scene_patterns:
        matches = list(re.finditer(pat, screenplay_text, re.IGNORECASE | re.DOTALL))
        if len(matches) >= 3:  # At least 3 scenes found
            break

    if not matches:
        # Fallback: split by --- or SCENE markers
        parts = re.split(r'(?:---|\n\n(?=SCENE\s+\d+))', screenplay_text)
        for i, part in enumerate(parts[1:], 1):
            if len(part.strip()) > 50:
                scenes.append(_build_scene_dict(i, f"Scene {i}", part, tone))
        return {"title": title or "Untitled Film", "logline": logline,
                "tone": tone, "dialogue_enabled": True, "scenes": scenes}

    for idx, m in enumerate(matches):
        scene_num = int(m.group(1))
        scene_header_raw = m.group(0).strip()
        scene_content = m.group(2).strip() if m.lastindex >= 2 else ""

        # Get the block of text for this scene
        block_start = m.end()
        block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(screenplay_text)
        block = screenplay_text[block_start:block_end].strip()

        # If scene_content is short (just the header), use block as content
        if len(scene_content) < len(block):
            full_text = scene_content + "\n" + block
        else:
            full_text = scene_content

        # Extract location from header
        location = _extract_location_from_header(scene_header_raw)

        # Extract time of day
        time_of_day = "Night"
        for tod in ['DAY', 'NIGHT', 'DAWN', 'DUSK', 'MORNING', 'EVENING', 'CONTINUOUS']:
            if tod in scene_header_raw.upper():
                time_of_day = tod.capitalize()
                break

        # Extract characters present
        chars_present = _extract_characters_present(full_text)

        # Extract shots from block
        block_shots = _extract_shots_from_block(full_text, scene_num)

        # Extract synopsis
        synopsis = _extract_synopsis(full_text)

        # Extract emotional tone
        emotional_tone = ""
        tone_match2 = re.search(r'(?:Emotional\s+(?:Beat|Tone)|Mood)[:\s]*([^\n]+)', full_text)
        if tone_match2:
            emotional_tone = tone_match2.group(1).strip()

        scenes.append(_build_scene_dict(
            scene_num, location or f"Scene {scene_num}", full_text, tone,
            chars_present=chars_present, shots=block_shots,
            synopsis=synopsis, emotional_tone=emotional_tone,
            time_of_day=time_of_day,
        ))

    return {
        "title": title or "Untitled Film",
        "logline": logline,
        "tone": tone,
        "dialogue_enabled": True,
        "scenes": scenes,
    }


def _extract_location_from_header(header: str) -> str:
    """Extract location from a scene header like 'SCENE 1 INT. VAULT - NIGHT'."""
    loc = re.sub(r'^.*?SCENE\s+\d+\s*[:\s]*', '', header, flags=re.IGNORECASE).strip()
    # Remove INT./EXT. prefix
    loc = re.sub(r'^(?:INT|EXT)\.?\s*', '', loc, flags=re.IGNORECASE).strip()
    # Remove time-of-day suffix
    for tm in ['NIGHT', 'DAY', 'DAWN', 'DUSK', 'MORNING', 'EVENING', 'CONTINUOUS']:
        idx = loc.upper().find(tm)
        if idx > 0:
            loc = loc[:idx].strip()
            break
    # Remove trailing dashes
    loc = re.sub(r'\s*[-–—]+\s*$', '', loc).strip()
    return loc


def _extract_characters_present(text: str) -> List[str]:
    """Extract character names mentioned in a scene block."""
    chars = []
    # Look for ALL-CAPS names (2+ words or single word 3+ chars)
    for c in re.findall(r'\b([A-Z][A-Z]{2,}(?:\s+[A-Z][A-Z]{2,})*)\s*[\(:]', text):
        name = c.strip()
        if name not in chars and name not in ('THE', 'AND', 'BUT', 'INT', 'EXT',
                                                'SHOT', 'SCENE', 'NOTE', 'DESC',
                                                'ACT', 'END', 'FADE', 'CUT',
                                                'CONT', 'DAYS', 'NIGHTS'):
            chars.append(name)
    # Also look for **NAME** patterns
    for c in re.findall(r'\*\*([A-Z][A-Z\s]{2,20})\*\*', text):
        name = c.strip()
        if name not in chars and name not in ('THE', 'AND', 'BUT'):
            chars.append(name)
    return chars[:5]


def _extract_shots_from_block(text: str, scene_num: int) -> List[Dict]:
    """Extract [SHOT N: TYPE] markers from a scene block."""
    shots = []
    for sn, stype in re.findall(r'\[SHOT\s+(\d+)[:\s]*([^\]]+)\]', text):
        shots.append({
            'shot_id': f'SC_{str(scene_num).zfill(3)}_SHOT_{sn}',
            'shot_number': int(sn),
            'scene_number': scene_num,
            'shot_type': stype.strip().rstrip('*').strip(),
            'camera_angle': stype.strip().rstrip('*').strip(),
            'movement': '',
            'description': '',
            'storyboard_status': 'pending',
        })
    return shots


def _extract_synopsis(text: str) -> str:
    """Extract a synopsis from scene text (first ~60 words of action lines)."""
    # Remove character dialogue markers
    clean = re.sub(r'\*{0,2}[A-Z]{3,}\s*\(.*?\)\s*\*{0,2}\s*', '', text)
    clean = re.sub(r'\*{0,2}[A-Z]{3,}\s*:', '', clean)
    clean = re.sub(r'\[SHOT\s+\d+[^\]]*\]', '', clean)
    clean = re.sub(r'#{1,4}\s*', '', clean)
    words = clean.split()[:60]
    return ' '.join(words).strip()


def _build_scene_dict(scene_num, location, full_text, tone,
                       chars_present=None, shots=None, synopsis="",
                       emotional_tone="", time_of_day="Night"):
    """Build a standardized scene dictionary."""
    return {
        "scene_id": f"SC_{str(scene_num).zfill(3)}",
        "scene_number": scene_num,
        "scene_title": location,
        "location_id": location,
        "characters_present": chars_present or [],
        "emotional_tone": emotional_tone or tone or "Dramatic",
        "time_of_day": time_of_day,
        "synopsis": synopsis or full_text[:300].strip(),
        "estimated_shots": len(shots) if shots else 3,
        "shots": shots or [],
        "raw_text": full_text[:2000],
    }


# ─── Characters ───────────────────────────────────────────────────────────────

def _parse_characters(text: str) -> List[Dict]:
    """
    Parse characters from EP output using multiple strategies.
    The EP may use various formats:
      - CHARACTER N: *NAME* ...
      - * NAME (AGE): description
      - **NAME** (AGE): description
      - CHARACTERS: * NAME (AGE): ... * NAME2 (AGE): ...
    """
    characters = []
    seen_names = set()

    # ── Strategy 1: CHARACTER N: *NAME* blocks ──
    char_pattern1 = r'(?:###?\s*)?CHARACTER\s+(\d+)[:\s*]*\*?\*?([^*\n]+)\*?\*?'
    for m in re.finditer(char_pattern1, text, re.IGNORECASE):
        name = m.group(2).strip().rstrip('*').strip()
        if name and len(name) > 1:
            _add_character(characters, seen_names, name, text, m.end())

    # ── Strategy 2: * NAME (AGE): description ──
    # This is the most common EP format
    # Use a pattern that stops before the next * NAME or next section
    char_pattern2 = r'\*\s+([A-Z][A-Z\s]{2,30})\s*\((\d+)\)[:\s]+([^*]{5,300})'
    for m in re.finditer(char_pattern2, text):
        name = m.group(1).strip()
        age = int(m.group(2))
        desc = m.group(3).strip().rstrip('.').strip()
        if name not in seen_names and name not in ('THE TRANSMITTER',):
            _add_character(characters, seen_names, name, text, m.end(), age=age, desc=desc)

    # ── Strategy 3: **NAME** (AGE): description ──
    char_pattern3 = r'\*\*([A-Z][A-Z\s]{2,30})\*\*\s*\((\d+)\)[:\s]+([^*]{5,300})'
    for m in re.finditer(char_pattern3, text):
        name = m.group(1).strip()
        age = int(m.group(2))
        desc = m.group(3).strip().rstrip('.').strip()
        if name not in seen_names:
            _add_character(characters, seen_names, name, text, m.end(), age=age, desc=desc)

    # ── Strategy 4: NAME (AGE): description (uppercase, no asterisk) ──
    char_pattern4 = r'(?<!\w)([A-Z][A-Z]{2,15}(?:\s+[A-Z][A-Z]{2,15})*)\s*\((\d+)\)[:\s]+([^\n]{5,300})'
    for m in re.finditer(char_pattern4, text):
        name = m.group(1).strip()
        age = int(m.group(2))
        desc = m.group(3).strip().rstrip('.').strip()
        if name not in seen_names and name not in ('THE', 'AND', 'BUT', 'INT', 'EXT',
                                                      'SHOT', 'SCENE', 'NOTE', 'DESC',
                                                      'ACT', 'END', 'FADE', 'CUT'):
            _add_character(characters, seen_names, name, text, m.end(), age=age, desc=desc)

    # ── Strategy 5: NAME: description (no age, entity/creature) ──
    # Always run — catches entities like THE TRANSMITTER that have no age
    _NOT_CHARS = {'THE', 'AND', 'BUT', 'INT', 'EXT', 'SHOT', 'SCENE', 'END',
                   'ROLE', 'DATE', 'PROJECT', 'PRODUCTION', 'STATUS', 'SUBJECT',
                   'FROM', 'LOGLINE', 'LEAD', 'VISUAL', 'DIRECTOR', 'CAMERA',
                   'SHOTS', 'BEATS', 'NOTE', 'DESC', 'ACT', 'FADE', 'CUT',
                   'CONT', 'DAYS', 'NIGHTS', 'HERO'}
    char_pattern5 = r'\*\s+([A-Z][A-Z]{2,20}(?:\s+[A-Z]{2,20})*)[:\s]+([^*]{10,300})'
    for m in re.finditer(char_pattern5, text):
        name = m.group(1).strip()
        desc = m.group(2).strip().rstrip('.').strip()
        if name not in seen_names and name not in _NOT_CHARS:
            _add_character(characters, seen_names, name, text, m.end(), desc=desc)

    # Deduplicate by name
    unique = []
    seen_final = set()
    for c in characters:
        key = c['character_name'].upper().strip()
        if key not in seen_final:
            seen_final.add(key)
            unique.append(c)

    return unique


def _add_character(characters, seen_names, name, text, block_start,
                    age=0, desc=""):
    """Add a character to the list with extracted details."""
    seen_names.add(name)

    # Try to extract more details from the block after this character
    block = text[block_start:block_start + 1500]

    role = _extract_field(block, r'(?:Role)[:\s]*([^\n]+)')
    personality = _extract_field(block, r'(?:Personality)[:\s]*([^\n]+)')
    height = _extract_field(block, r'(?:Height)[:\s]*([^\n]+)')
    visual_identity = _extract_field(block, r'(?:Visual Identity)[:\s]*([^\n]+)')

    # AI Prompt
    ai_prompt = ""
    prompt_match = re.search(
        r'(?:AI\s+(?:Turnaround\s+)?Prompt|ComfyUI\s+Prompt)[:\s]*`?([^`]+)`?',
        block, re.IGNORECASE
    )
    if prompt_match:
        ai_prompt = prompt_match.group(1).strip()

    physical_parts = []
    if height:
        physical_parts.append(f"Height: {height}")
    if visual_identity:
        physical_parts.append(visual_identity)

    char_id = f"CHAR_{str(len(characters) + 1).zfill(3)}"

    characters.append({
        "character_id": char_id,
        "character_name": name,
        "full_name": name,
        "role": role or "Character",
        "age": age,
        "physical_appearance": " | ".join(physical_parts) if physical_parts else desc[:200],
        "personality": personality or desc[:200],
        "visual_identity": visual_identity or "",
        "ai_prompt": ai_prompt,
        "description": desc[:500] if desc else "",
    })


# ─── Locations ────────────────────────────────────────────────────────────────

def _parse_locations(text: str, screenplay: Dict) -> List[Dict]:
    """Extract locations from screenplay scenes and visual direction."""
    locations = []
    seen = set()

    # From screenplay scenes
    if screenplay and screenplay.get("scenes"):
        for scene in screenplay["scenes"]:
            loc = scene.get("location_id", "")
            if loc and loc not in seen:
                seen.add(loc)
                locations.append({
                    "location_id": f"LOC_{str(len(locations) + 1).zfill(3)}",
                    "location_name": loc,
                    "environment_type": _guess_env_type(loc, text),
                    "mood": scene.get("emotional_tone", ""),
                    "description": scene.get("synopsis", "")[:200],
                })

    # From visual direction section
    vis_start = -1
    for marker in ['VISUAL DIRECTION', 'PHASE 4', 'CINEMATOGRAPHY']:
        idx = text.find(marker)
        if idx != -1:
            vis_start = idx
            break
    if vis_start != -1:
        vis_end = len(text)
        for marker in ['PHASE 5', 'AI PROMPT', 'AI GENERATION', 'COMFYUI']:
            idx = text.find(marker, vis_start + 50)
            if idx != -1 and idx < vis_end:
                vis_end = idx

        vis_text = text[vis_start:vis_end]
        # Look for location names in visual section
        loc_patterns = [
            r'(?:Location|Setting|Place)[:\s]+([^\n]+)',
            r'((?:The\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Factory|Church|Basement|Apartment|Studio|House|Building|Facility|Vault|Corridor|Room|Office|Lab))',
        ]
        for pat in loc_patterns:
            for m in re.finditer(pat, vis_text):
                loc_name = m.group(1).strip() if m.lastindex else m.group(0).strip()
                if loc_name and loc_name not in seen and len(loc_name) > 3:
                    seen.add(loc_name)
                    locations.append({
                        "location_id": f"LOC_{str(len(locations) + 1).zfill(3)}",
                        "location_name": loc_name,
                        "environment_type": _guess_env_type(loc_name, text),
                        "mood": "Atmospheric",
                        "description": "",
                    })

    return locations


def _guess_env_type(location_name: str, text: str) -> str:
    """Guess if a location is interior or exterior."""
    name_lower = location_name.lower()
    if any(w in name_lower for w in ['int.', 'interior', 'room', 'vault', 'office',
                                       'lab', 'studio', 'corridor', 'hall', 'basement',
                                       'apartment', 'factory']):
        return "Interior"
    if any(w in name_lower for w in ['ext.', 'exterior', 'outdoor', 'street', 'field',
                                       'forest', 'beach', 'rooftop', 'yard']):
        return "Exterior"
    # Check context
    idx = text.find(location_name)
    if idx != -1:
        context = text[max(0, idx - 100):idx + 100]
        if 'INT.' in context:
            return "Interior"
        if 'EXT.' in context:
            return "Exterior"
    return "Interior"


# ─── Shot Table ───────────────────────────────────────────────────────────────

def _parse_shot_table(text: str) -> List[Dict]:
    """Parse shot table from PHASE 4 or standalone shot list."""
    shots = []

    # Find shot table section
    shot_start = -1
    for marker in ['SHOT BEAT', 'SHOT LIST', 'CINEMATOGRAPHY PLAN', 'VISUAL DIRECTION']:
        idx = text.find(marker)
        if idx != -1:
            shot_start = idx
            break
    if shot_start == -1:
        return shots

    shot_end = len(text)
    for marker in ['PHASE 5', 'AI GENERATION', 'AI PROMPT', 'COMFYUI', 'PROMPT']:
        idx = text.find(marker, shot_start + 50)
        if idx != -1 and idx < shot_end:
            shot_end = idx

    shot_text = text[shot_start:shot_end]

    # Parse markdown table rows: | N | Sc N | Type | Camera | Description |
    table_pattern = r'\|\s*(\d+)\s*\|\s*(?:Sc\s*)?(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'
    table_rows = re.findall(table_pattern, shot_text)
    for row_match in table_rows:
        shot_num = int(row_match[0])
        scene_num = int(row_match[1])
        shots.append({
            "shot_id": f"SC_{str(scene_num).zfill(3)}_SHOT_{shot_num}",
            "shot_number": shot_num,
            "scene_number": scene_num,
            "shot_type": row_match[2].strip(),
            "camera_angle": row_match[3].strip(),
            "movement": row_match[3].strip(),
            "description": row_match[4].strip(),
            "storyboard_status": "pending",
        })

    # Fallback: parse individual shot descriptions
    if not shots:
        for m in re.finditer(r'(?:Shot|SHOT)\s+(\d+)[:\s]*([^\n]+)', shot_text):
            shots.append({
                "shot_id": f"SC_001_SHOT_{m.group(1)}",
                "shot_number": int(m.group(1)),
                "scene_number": 1,
                "shot_type": "Medium Shot",
                "camera_angle": "",
                "movement": "",
                "description": m.group(2).strip(),
                "storyboard_status": "pending",
            })

    return shots


# ─── AI Prompts ───────────────────────────────────────────────────────────────

def _parse_ai_prompts(text: str) -> List[Dict]:
    """Parse AI generation prompts from PHASE 5 or standalone sections."""
    prompts = []

    prompt_start = -1
    for marker in ['AI GENERATION PROMPT', 'AI PROMPT', 'COMFYUI', 'HERO SHOT']:
        idx = text.find(marker)
        if idx != -1:
            prompt_start = idx
            break
    if prompt_start == -1:
        return prompts

    prompt_text = text[prompt_start:]

    # Extract code blocks (```...```)
    code_blocks = re.findall(r'```(?:text)?\n?(.*?)```', prompt_text, re.DOTALL)

    for i, block in enumerate(code_blocks):
        block = block.strip()
        if len(block) > 20:
            # Try to find name from section header
            before = prompt_text[:prompt_text.find(block)] if block in prompt_text else ""
            name_match = re.search(r'(?:HERO SHOT|SHOT|PROMPT)\s*(\d+)[:\s]*([^\n]*)', before[-500:] if len(before) > 500 else before)
            name = name_match.group(0).strip() if name_match else f"Prompt {i + 1}"

            prompts.append({
                "name": name,
                "prompt": block,
                "scene_ref": "",
                "type": "comfyui",
            })

    return prompts


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_field(text: str, pattern: str) -> str:
    """Extract a single field value from text using regex."""
    m = re.search(pattern, text)
    if m:
        return m.group(1).strip().strip('*').strip()
    return ""


# ─── Convert to Project State ─────────────────────────────────────────────────

def package_to_project_state(package: Dict) -> Dict:
    """
    Convert a parsed production package into a project_state dict
    that can be saved to project_state.json and loaded by the frontend.
    """
    state = {}

    # Screenplay data
    if package.get("screenplay"):
        sp = package["screenplay"]
        # Assign standalone shots to scenes if needed
        if package.get("shots"):
            shots_by_scene = {}
            for shot in package["shots"]:
                sn = shot.get("scene_number", 1)
                if sn not in shots_by_scene:
                    shots_by_scene[sn] = []
                shots_by_scene[sn].append(shot)

            for scene in sp.get("scenes", []):
                sn = scene.get("scene_number", 0)
                if sn in shots_by_scene and not scene.get("shots"):
                    scene["shots"] = shots_by_scene[sn]
                    scene["estimated_shots"] = len(shots_by_scene[sn])

        state["screenplayData"] = sp

    # Character data
    if package.get("characters"):
        state["charData"] = package["characters"]

    # Location data
    if package.get("locations"):
        state["locationData"] = package["locations"]

    # Image/video prompt data from AI prompts
    if package.get("ai_prompts"):
        state["imagePromptData"] = [
            {
                "prompt": p.get("prompt", ""),
                "name": p.get("name", ""),
                "scene_ref": p.get("scene_ref", ""),
            }
            for p in package["ai_prompts"]
        ]

    # Topic ideas from concepts
    if package.get("concepts"):
        state["topicIdeas"] = [
            {
                "title": c.get("title", ""),
                "logline": c.get("logline", ""),
                "visual_potential": c.get("visual_potential", ""),
                "emotional_impact": c.get("emotional_impact", ""),
                "selected": c.get("selected", False),
                "character_count": len(package.get("characters", [])),
                "location_count": len(package.get("locations", [])),
                "estimated_scene_count": len(package.get("screenplay", {}).get("scenes", [])),
                "estimated_total_shots": len(package.get("shots", [])),
            }
            for c in package["concepts"]
        ]
        # Set selected idea index
        for i, idea in enumerate(state["topicIdeas"]):
            if idea.get("selected"):
                state["selectedIdeaIndex"] = i
                break

    return state
