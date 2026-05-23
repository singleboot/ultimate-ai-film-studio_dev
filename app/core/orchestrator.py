import json
import os
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple


SKILL_PATH = Path(__file__).parent.parent.parent / "skills" / "cinematic_story_architect.md"


def load_master_system_prompt() -> str:
    """Load the cinematic_story_architect skill as the master system prompt."""
    if SKILL_PATH.exists():
        return SKILL_PATH.read_text(encoding="utf-8")
    return "# AI Film Production Engine\nYou are an AI film production engine."


class CinematicMemory:
    """Persistent cinematic memory that stores all production assets across modules.

    This is the single source of truth for continuity. Every module reads from
    and writes to this memory object. No module regenerates data from scratch.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.project_info: Dict[str, Any] = {}
        self.ideas: List[Dict] = []
        self.selected_idea_index: int = -1
        self.screenplay: Dict[str, Any] = {}
        self.characters: List[Dict] = []
        self.locations: List[Dict] = []
        self.storyboard: List[Dict] = []
        self.video_prompts: List[Dict] = []
        self.selected_characters: int = 3
        self.selected_locations: int = 3
        self.selected_scenes: int = 10
        self.selected_total_shots: int = 30
        self.project_graph: Dict[str, Any] = self._new_project_graph()

    @staticmethod
    def _new_project_graph() -> Dict:
        return {
            "project_id": str(uuid.uuid4())[:8],
            "idea": {},
            "screenplay": {},
            "character_bible": [],
            "location_bible": [],
            "scene_graph": [],
            "shots": {},
            "storyboards": {},
            "videos": {},
            "variants": {},
            "timeline": [],
            "locks": {"characters": {}, "locations": {}, "shots": {}},
            "approvals": {"characters_approved": False, "locations_approved": False},
            "character_assets": {},
            "location_assets": {},
            "character_sheets": {},
            "location_sheets": {},
        }

    @property
    def selected_idea(self) -> Optional[Dict]:
        if 0 <= self.selected_idea_index < len(self.ideas):
            return self.ideas[self.selected_idea_index]
        return None

    def to_dict(self) -> Dict:
        return {
            "project_info": self.project_info,
            "ideas": self.ideas,
            "selected_idea_index": self.selected_idea_index,
            "screenplay": self.screenplay,
            "characters": self.characters,
            "locations": self.locations,
            "storyboard": self.storyboard,
            "video_prompts": self.video_prompts,
            "selected_characters": self.selected_characters,
            "selected_locations": self.selected_locations,
            "selected_scenes": self.selected_scenes,
            "selected_total_shots": self.selected_total_shots,
            "project_graph": self.project_graph,
        }

    def from_dict(self, data: Dict):
        self.project_info = data.get("project_info", {})
        self.ideas = data.get("ideas", [])
        self.selected_idea_index = data.get("selected_idea_index", -1)
        self.screenplay = data.get("screenplay", {})
        self.characters = data.get("characters", [])
        self.locations = data.get("locations", [])
        self.storyboard = data.get("storyboard", [])
        self.video_prompts = data.get("video_prompts", [])
        self.selected_characters = data.get("selected_characters", 3)
        self.selected_locations = data.get("selected_locations", 3)
        self.selected_scenes = data.get("selected_scenes", 10)
        self.selected_total_shots = data.get("selected_total_shots", 30)
        raw = data.get("project_graph", None)
        if raw:
            self.project_graph = raw
        else:
            self.project_graph = self._new_project_graph()


class CinematicOrchestrator:
    """Orchestrates the full AI film production pipeline.

    Each method corresponds to a pipeline stage. Methods consume the current
    cinematic memory and extend it with new production assets.
    """

    def __init__(self, llm_engine=None):
        self.llm_engine = llm_engine
        self.memory = CinematicMemory()
        self._master_system_prompt = load_master_system_prompt()
        self._lock = threading.Lock()
        self._progress = {"pct": 0, "title": "Ready", "sub": "", "finished": False, "error": ""}

    def set_progress(self, pct: int, title: str = None, sub: str = None):
        with self._lock:
            self._progress["pct"] = min(100, max(0, pct))
            self._progress["finished"] = pct >= 100
            if title:
                self._progress["title"] = title
            if sub is not None:
                self._progress["sub"] = sub

    def get_progress(self) -> Dict:
        with self._lock:
            return dict(self._progress)

    def _reset_progress(self):
        with self._lock:
            self._progress = {"pct": 0, "title": "Starting...", "sub": "", "finished": False, "error": ""}

    def get_master_system_prompt(self) -> str:
        return self._master_system_prompt

    def get_continuity_context(self) -> str:
        """Build the continuity context string from current memory."""
        parts = []
        pg = self.memory.project_graph

        if self.memory.project_info:
            info = self.memory.project_info
            parts.append("=== PROJECT CONTEXT ===")
            for k, v in info.items():
                if v:
                    parts.append(f"{k}: {v}")

        sp = pg.get("screenplay", {}) or self.memory.screenplay
        if sp:
            parts.append("\n=== SCREENPLAY CONTINUITY ===")
            parts.append(f"Title: {sp.get('title', '')}")
            parts.append(f"Logline: {sp.get('logline', '')}")
            parts.append(f"Tone: {sp.get('tone', '')}")

            scene_graph = pg.get("scene_graph", []) or sp.get("scenes", [])
            for s in scene_graph:
                sid = s.get("scene_id", "?")
                stitle = s.get("scene_title", "")
                loc = s.get("location_id", "?")
                shots = s.get("shots", [])
                parts.append(f"  Scene {sid}: {stitle} @ {loc} ({len(shots)} shots)")
                for c in s.get("characters_present", []):
                    parts.append(f"    Character: {c}")
                for sh in shots:
                    parts.append(f"    Shot {sh.get('shot_id', '?')}: {sh.get('shot_type', '')} - {sh.get('camera_language', '')}")

        cb = pg.get("character_bible", []) or self.memory.characters
        if cb:
            parts.append("\n=== CHARACTER BIBLE ===")
            for ch in cb:
                ch_id = ch.get("character_id", ch.get("id", "?"))
                parts.append(f"  [{ch_id}] {ch.get('character_name', ch.get('full_name', ''))} - {ch.get('role', '')}")

        lb = pg.get("location_bible", []) or self.memory.locations
        if lb:
            parts.append("\n=== LOCATION BIBLE ===")
            for loc in lb:
                loc_id = loc.get("location_id", loc.get("id", "?"))
                parts.append(f"  [{loc_id}] {loc.get('location_name', loc.get('name', ''))} - {loc.get('environment_type', loc.get('type', ''))}")

        storyboards = pg.get("storyboards", {})
        if storyboards:
            parts.append("\n=== STORYBOARD ASSETS ===")
            parts.append(f"  {len(storyboards)} shots enriched")
        elif self.memory.storyboard:
            parts.append("\n=== STORYBOARD ASSETS ===")
            for sb in self.memory.storyboard:
                parts.append(f"  Scene {sb.get('scene_id', '?')}: {sb.get('shot_count', 0)} shots")

        locks = pg.get("locks", {})
        lock_entries = []
        for cat in ["characters", "locations", "shots"]:
            for k, v in locks.get(cat, {}).items():
                for field, locked in v.items():
                    if locked:
                        lock_entries.append(f"  [{cat}] {k}.{field} = (CANNOT CHANGE)")
        if lock_entries:
            parts.append("\n=== CONTINUITY LOCKS ===")
            parts.extend(lock_entries)

        return "\n".join(parts)

    def _call_llm(self, prompt: str, system_suffix: str = "", json_output: bool = True) -> Dict:
        """Call the LLM with the master system prompt plus any stage-specific suffix."""
        if not self.llm_engine:
            return {"success": False, "error": "LLM engine not available"}

        system_prompt = self._master_system_prompt
        if system_suffix:
            system_prompt = system_prompt + "\n\n" + system_suffix

        # Resolve provider/model/host: project_info → global settings → auto-detect → defaults
        provider = self.memory.project_info.get("llm_provider", "")
        model = self.memory.project_info.get("llm_model", "")
        host = ""
        try:
            sp = getattr(self.llm_engine, '_settings_path', None)
            if sp and sp.exists():
                import json
                gs = json.loads(sp.read_text(encoding='utf-8'))
                llm_cfg = gs.get("llm", {})
                if not provider:
                    provider = llm_cfg.get("provider", "ollama")
                if not model:
                    model = llm_cfg.get("model", "")
                host = llm_cfg.get("host", "")
        except Exception:
            pass
        if not provider:
            provider = "ollama"
        if not model:
            try:
                import requests
                oh = host or "http://localhost:11434"
                r = requests.get(f"{oh}/api/tags", timeout=5)
                if r.status_code == 200:
                    tags = r.json().get("models", [])
                    if tags:
                        model = tags[0].get("name", "llama3.1")
            except Exception:
                model = "llama3.1"

        result = self.llm_engine.generate(
            provider_id=provider,
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            host=host or None,
        )

        if result.get("success"):
            text = result["response"]
            if json_output:
                json_data = self._extract_json(text)
                if json_data is not None:
                    return {"success": True, "data": json_data, "raw": text}
                return {"success": True, "data": None, "raw": text, "warning": "Could not extract JSON from response"}
            return {"success": True, "data": text, "raw": text}

        return result

    def _extract_json(self, text: str) -> Optional[Any]:
        """Extract JSON from LLM response, handling markdown code fences."""
        import re
        # Try ```json ... ``` block first
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        # Try finding a standalone JSON array or object
        for pattern in [r"(\[.*?\])", r"(\{.*?\})"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass
        return None

    # === PIPELINE STAGES ===

    def generate_ideas(self, params: Dict) -> Dict:
        """Stage 1: Generate 5 cinematic story ideas with exact count enforcement."""
        self._reset_progress()
        self.set_progress(5, "Gathering sources...")
        self.memory.project_info.update(params)

        char_count = params.get("character_count") or params.get("max_characters", 3)
        loc_count = params.get("location_count") or params.get("max_locations", 3)
        scene_count = params.get("scene_count") or params.get("scenes", 10)
        shot_count = params.get("total_shot_count") or params.get("max_total_shots", 30)
        self.memory.selected_characters = int(char_count)
        self.memory.selected_locations = int(loc_count)
        self.memory.selected_scenes = int(scene_count)
        self.memory.selected_total_shots = int(shot_count)

        self.set_progress(10, "Processing inputs...")

        def val(v, default=""):
            return v if v else default

        genres_str = ", ".join(params.get("genres", [])) if params.get("genres") else "None"
        visual_style = val(params.get("visual_style"), "None")
        film_aesthetic = val(params.get("film_aesthetic"), "None")
        era = val(params.get("era"), "None")
        custom_story = val(params.get("custom_prompt"), "None")
        dialogue_enabled = str(params.get("dialogue_enabled", True))
        aspect_ratio = val(params.get("aspect_ratio"), "16:9")
        image_resolution = val(params.get("image_resolution"), "1024x576")
        video_resolution = val(params.get("video_resolution"), "1280x720")

        prompt = f"""You are the IDEA GENERATION MODULE inside a modular AI Film Production System.

Your responsibility is to generate cinematic, production-compatible film concepts based on structured user constraints.

You are NOT generating random story ideas.

You are generating:
# PRODUCIBLE CINEMATIC CONCEPTS

optimized for:
- screenplay expansion
- character generation
- location generation
- storyboard generation
- AI image generation
- LTX 2.3 video generation

All outputs must feel:
- cinematic
- emotionally engaging
- visually generatable
- continuity-friendly
- production realistic

---

# USER INPUTS

GENRES:
{genres_str}

VISUAL_STYLE:
{visual_style}

FILM_AESTHETIC:
{film_aesthetic}

ERA:
{era}

CUSTOM_STORY_INPUT:
{custom_story}

STORY_SOURCE_URL:
None

DIALOGUE_ENABLED:
{dialogue_enabled}

ASPECT_RATIO:
{aspect_ratio}

IMAGE_RESOLUTION:
{image_resolution}

VIDEO_RESOLUTION:
{video_resolution}

MAXIMUM_CHARACTERS:
{char_count}

MAXIMUM_LOCATIONS:
{loc_count}

MAXIMUM_SCENES:
{scene_count}

MAXIMUM_TOTAL_SHOTS:
{shot_count}

---

# CINEMATIC GENERATION RULES

You must intelligently blend:
- genre
- visual style
- film aesthetic
- era
- emotional tone
- cinematic pacing

The generated concepts must feel:
- professionally screenwritten
- visually cinematic
- emotionally compelling
- structurally producible

---

# IMPORTANT PRODUCTION CONSTRAINTS

You MUST obey:

- Character count cannot exceed MAXIMUM_CHARACTERS
- Location count cannot exceed MAXIMUM_LOCATIONS
- Scene count MUST be EXACTLY MAXIMUM_SCENES
- Total shot count cannot exceed MAXIMUM_TOTAL_SHOTS

The AI must intelligently determine:
- shots per scene
- pacing structure
- narrative segmentation

based on:
- genre
- emotional pacing
- dialogue density
- cinematic tone

---

# CINEMATIC PACING LOGIC

Different genres require different pacing.

Examples:

HORROR:
- slower pacing
- atmospheric shots
- tension-building scenes

ACTION:
- faster pacing
- dynamic scene progression
- higher shot density

DRAMA:
- emotionally focused scenes
- restrained visual pacing
- dialogue-heavy storytelling

THRILLER:
- escalating suspense
- tension-focused scene rhythm

SCI-FI:
- cinematic worldbuilding
- environmental establishment

ROMANCE:
- emotional intimacy
- performance-driven scenes

The pacing structure should feel naturally cinematic.

---

# DIALOGUE RULES

IF DIALOGUE_ENABLED = TRUE:
- concepts should support cinematic dialogue
- strong interpersonal conflict
- emotionally expressive scenes

IF DIALOGUE_ENABLED = FALSE:
- concepts should rely on visual storytelling
- environmental storytelling
- cinematic atmosphere
- body language
- silent emotional progression

---

# STORY QUALITY RULES

Each concept must contain:
- a strong emotional hook
- a visually iconic cinematic identity
- a memorable central conflict
- clear character motivations
- visually generatable scenes
- strong atmosphere
- production realism

Avoid:
- generic ideas
- repetitive concepts
- overcomplicated lore
- excessive characters
- unnecessary locations
- random visual chaos

---

# CONTINUITY AWARENESS

The generated concepts will later expand into:
- screenplay modules
- character assets
- location assets
- storyboard systems
- video generation systems

Therefore:
- characters must feel reusable
- locations must feel visually distinct
- concepts must support continuity
- visual identities must remain stable

---

# OUTPUT REQUIREMENTS

Generate EXACTLY 5 cinematic film concepts.

Each concept MUST contain:

1. TITLE

2. LOGLINE

3. GENRE_BLEND

4. TONE

5. VISUAL_IDENTITY

6. FILM_AESTHETIC_INTERPRETATION

7. CHARACTER_COUNT

8. LOCATION_COUNT

9. ESTIMATED_SCENE_COUNT

10. ESTIMATED_TOTAL_SHOTS

11. SHOT_DISTRIBUTION_PER_SCENE

Example:
Scene 1 → 2 shots
Scene 2 → 3 shots
Scene 3 → 2 shots

12. MAIN_CHARACTERS (array of strings)

13. MAIN_LOCATIONS (array of strings)

14. EMOTIONAL_HOOK

15. CINEMATIC_HOOK

16. ENDING_STYLE

17. DIALOGUE_DENSITY

18. PRODUCTION_COMPLEXITY

19. THUMBNAIL_MOMENT

20. SHORT_SYNOPSIS

---

# FINAL SYSTEM RULE

You are designing:
# CINEMATICALLY INTELLIGENT FILM CONCEPTS

not random AI stories.

Every concept must feel:
- filmable
- visually powerful
- emotionally memorable
- modular
- production-compatible
- AI-generation ready"""

        self.set_progress(50, "Generating ideas with LLM...", "Sending to AI model")

        system_suffix = """You MUST respond with ONLY a JSON array of exactly 5 objects. Each object must have these keys:
- title (string)
- logline (string)
- genre_blend (string)
- tone (string)
- visual_identity (string)
- film_aesthetic_interpretation (string)
- character_count (number)
- location_count (number)
- estimated_scene_count (number)
- estimated_total_shots (number)
- shot_distribution_per_scene (object, e.g. {"Scene 1": 3, "Scene 2": 2})
- main_characters (array of strings)
- main_locations (array of strings)
- emotional_hook (string)
- cinematic_hook (string)
- ending_style (string)
- dialogue_density (string)
- production_complexity (string)
- thumbnail_moment (string)
- short_synopsis (string)"""

        result = self._call_llm(prompt, system_suffix=system_suffix)

        self.set_progress(80, "Validating results...")

        if result.get("success") and result.get("data") and isinstance(result["data"], list):
            valid, issues = self._validate_idea_counts(result["data"])
            if not valid:
                self.set_progress(85, "Count mismatch, retrying with correction...")
                correction = f"The previous output had count issues: {'; '.join(issues)}. Regenerate ensuring EXACT counts: scenes={scene_count}, characters={char_count}, locations={loc_count}, shots={shot_count}."
                result = self._call_llm(prompt + "\n\n" + correction, system_suffix=system_suffix)

            if result.get("success") and result.get("data") and isinstance(result["data"], list):
                valid2, issues2 = self._validate_idea_counts(result["data"])
                if not valid2:
                    for idea in result["data"]:
                        esc = idea.get("estimated_scene_count", 0)
                        if esc != self.memory.selected_scenes:
                            idea["estimated_scene_count"] = self.memory.selected_scenes
                        ets = idea.get("estimated_total_shots", 0)
                        if ets > self.memory.selected_total_shots:
                            idea["estimated_total_shots"] = self.memory.selected_total_shots
                        cc = idea.get("character_count", 0)
                        if cc > self.memory.selected_characters:
                            idea["character_count"] = self.memory.selected_characters
                        lc = idea.get("location_count", 0)
                        if lc > self.memory.selected_locations:
                            idea["location_count"] = self.memory.selected_locations
                self.memory.ideas = result["data"]
                self.set_progress(100, "Ideas generated!", f"{len(result['data'])} concepts ready")
                return {"success": True, "ideas": result["data"], "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def generate_screenplay(self, params: Dict) -> Dict:
        """Stage 2: Generate master screenplay with shot nodes + character/location bible."""
        self._reset_progress()
        self.set_progress(5, "Starting screenplay + shot design...")

        idea_index = params.get("idea_index", self.memory.selected_idea_index)
        if 0 <= idea_index < len(self.memory.ideas):
            self.memory.selected_idea_index = idea_index
        else:
            self.set_progress(0, "Failed", "Invalid idea index")
            return {"success": False, "error": "Invalid idea index"}

        idea = self.memory.selected_idea
        if not idea:
            self.set_progress(0, "Failed", "No idea selected")
            return {"success": False, "error": "No idea selected"}

        char_count = params.get("character_count") or params.get("max_characters", self.memory.selected_characters)
        loc_count = params.get("location_count") or params.get("max_locations", self.memory.selected_locations)
        scene_count = params.get("scene_count") or params.get("scenes", self.memory.selected_scenes)
        shot_count = params.get("total_shot_count") or params.get("max_total_shots", self.memory.selected_total_shots)

        self.memory.selected_characters = int(char_count)
        self.memory.selected_locations = int(loc_count)
        self.memory.selected_scenes = int(scene_count)
        self.memory.selected_total_shots = int(shot_count)

        self.set_progress(15, "Building screenplay prompt...")

        genre_blend = idea.get("genre_blend", "")
        tone = idea.get("tone", "")

        prompt_parts = [
            "Based on the selected story idea, generate a complete cinematic screenplay with shot-level design.",
            "",
            f"Title: {idea.get('title', 'Untitled')}",
            f"Logline: {idea.get('logline', '')}",
            f"Genre Blend: {genre_blend}",
            f"Tone: {tone}",
            f"Visual Identity: {idea.get('visual_identity', '')}",
            f"Film Aesthetic: {self.memory.project_info.get('film_aesthetic', '')}",
            f"Era: {self.memory.project_info.get('era', '')}",
            f"Dialogue Enabled: {str(self.memory.project_info.get('dialogue_enabled', True))}",
            f"EXACTLY {char_count} characters required",
            f"EXACTLY {loc_count} locations required",
            f"EXACTLY {scene_count} scenes required",
            f"EXACTLY {shot_count} total shots required (sum of all scene shots)",
            "",
            "=== RULE: SCREENPLAY + SHOT DESIGN ===",
            "The screenplay module is SCREENPLAY + SHOT DESIGN. Not just narrative writing.",
            "Generate actual cinematic shot nodes for every scene — NOT estimated_shots numbers.",
            "",
            "Each shot must be:",
            "- visually distinct",
            "- support storyboard generation",
            "- support Flux2 Klein image generation",
            "- support LTX 2.3 video generation",
            "- contain cinematic intent",
        ]

        pacing_guide = self._get_pacing_guide(genre_blend, tone)
        if pacing_guide:
            prompt_parts.append(f"\nCinematic Pacing Guide:\n{pacing_guide}")

        system_suffix = f"""You MUST respond with ONLY a JSON object. The object must have:
- title (string)
- logline (string)
- tone (string)
- dialogue_enabled (boolean)
- character_bible (array of objects, EXACTLY {char_count} characters, each with:)
  - character_id (string, e.g. "CHAR_001")
  - full_name (string)
  - role (string, e.g. protagonist, antagonist, supporting)
  - age (string)
  - physical_appearance (string)
  - clothing (string, era-appropriate)
  - personality (string)
  - emotional_traits (string)
  - signature_items (string)
  - cinematic_presence (string)
  - visual_identity (string, how they look on screen)
  - costume_continuity (string)
  - image_prompt (string, optimized for ZImage Turbo)
- location_bible (array of objects, EXACTLY {loc_count} locations, each with:)
  - location_id (string, e.g. "LOC_001")
  - location_name (string)
  - environment_type (string)
  - architecture_style (string)
  - mood (string)
  - lighting_style (string)
  - cinematic_features (string)
  - environmental_storytelling (string)
  - image_prompt (string, optimized for ZImage Turbo)
- scenes (array of scene objects, EXACTLY {scene_count} scenes, each with:)
  - scene_id (string, e.g. "SC_001")
  - scene_title (string)
  - scene_number (number)
  - location_id (string, matching one from location_bible)
  - synopsis (string)
  - emotional_tone (string)
  - characters_present (array of character_id strings matching character_bible)
  - dialogue (array of dialogue lines, each with speaker and text; empty array if dialogue_enabled is false)
  - shots (array of shot nodes, sum of ALL shots across ALL scenes must be EXACTLY {shot_count})
    - shot_id (string, e.g. "SHOT_001")
    - shot_number (number)
    - shot_type (string, e.g. Wide Shot, Medium Shot, Close-Up, Over-the-Shoulder, Tracking Shot, Low Angle, POV Shot, Insert Shot, Establishing Shot)
    - camera_language (string, how the camera moves and frames)
    - lighting_language (string, lighting conditions for this shot)
    - emotion (string, emotional intent of the shot)
    - characters_present (array of character_id strings)
    - dialogue (array, direct dialogue in this shot)
    - action (string, what happens in this shot)
    - visual_motifs (array of strings)
    - motion_opportunities (array of strings)
    - environmental_motion (array of strings)
    - audio_notes (array of strings)
    - continuity_notes (string)
    - cinematic_notes (string)
    - storyboard_status (string, always "pending")

IMPORTANT CONSTRAINTS:
- Total shots across ALL scenes MUST BE EXACTLY {shot_count}
- Number of UNIQUE character_id in character_bible MUST BE EXACTLY {char_count}
- Number of UNIQUE location_id in location_bible MUST BE EXACTLY {loc_count}
- Number of scenes MUST BE EXACTLY {scene_count}
- Each scene's location_id must match one from location_bible
- Each shot's characters_present must reference character_id from character_bible
- Intelligently allocate shots based on emotional pacing, genre, and story complexity
- Each shot must feel visually distinct and cinematically intentional
- Shot types must vary across the scene (avoid repetition)
- Dialogue array is empty array [] if dialogue_enabled is false"""

        self.set_progress(50, "Generating screenplay with LLM...")

        result = self._call_llm("\n".join(prompt_parts), system_suffix=system_suffix)

        self.set_progress(80, "Validating counts...")

        if result.get("success") and result.get("data"):
            data = result["data"]
            valid, issues = self._validate_screenplay_counts(data)
            if not valid:
                self.set_progress(85, "Count mismatch, retrying with correction...")
                correction = f"Count check failed: {'; '.join(issues)}. Regenerate ensuring exact counts."
                result = self._call_llm(
                    "\n".join(prompt_parts) + "\n\nPREVIOUS ERROR: " + correction,
                    system_suffix=system_suffix
                )
                if result.get("success") and result.get("data"):
                    data = result["data"]
                    valid, issues = self._validate_screenplay_counts(data)
                    if not valid:
                        scenes = data.get("scenes", [])
                        while len(scenes) < self.memory.selected_scenes:
                            scenes.append(scenes[-1] if scenes else {"scene_id": "SC_FILL", "scene_title": "Replacement Scene", "shots": []})
                        while len(scenes) > self.memory.selected_scenes:
                            scenes.pop()
                        data["scenes"] = scenes

            self.memory.screenplay = data
            # Also populate project_graph
            pg = self.memory.project_graph
            pg["screenplay"] = data
            pg["character_bible"] = data.get("character_bible", [])
            pg["location_bible"] = data.get("location_bible", [])
            pg["scene_graph"] = data.get("scenes", [])
            # Build flat shots dict
            shots_dict = {}
            for s in data.get("scenes", []):
                for sh in s.get("shots", []):
                    shots_dict[sh["shot_id"]] = sh
            pg["shots"] = shots_dict

            scene_count_actual = len(data.get("scenes", []))
            shot_count_actual = sum(len(s.get("shots", [])) for s in data.get("scenes", []))
            self.set_progress(100, "Screenplay + shot design complete!", f"{scene_count_actual} scenes, {shot_count_actual} shots")
            return {"success": True, "screenplay": data, "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def _get_pacing_guide(self, genre_blend: str, tone: str) -> str:
        """Return cinematic pacing guidance based on genre and tone."""
        guides = []
        gl = (genre_blend + " " + tone).lower()
        if any(g in gl for g in ["horror", "thriller", "suspense"]):
            guides.append("- HORROR/THRILLER: slower pacing, atmospheric shots, tension-building scenes")
        if any(g in gl for g in ["action", "adventure", "war"]):
            guides.append("- ACTION: faster pacing, dynamic scene progression, higher shot density")
        if any(g in gl for g in ["drama", "romance"]):
            guides.append("- DRAMA/ROMANCE: emotionally focused scenes, restrained visual pacing, dialogue-heavy")
        if any(g in gl for g in ["sci-fi", "fantasy", "science fiction"]):
            guides.append("- SCI-FI/FANTASY: cinematic worldbuilding, environmental establishment shots")
        if any(g in gl for g in ["comedy"]):
            guides.append("- COMEDY: brisk pacing, comedic timing, shot-reaction-shot patterns")
        if not guides:
            guides.append("- BALANCED: mix of establishing, medium, and close-up shots for emotional rhythm")
        guides.append("- Intentionally distribute shots across scenes for narrative pacing")
        guides.append("- Earlier scenes may have fewer shots (setup), climactic scenes more (payoff)")
        return "\n".join(guides)

    def _validate_idea_counts(self, ideas: List[Dict]) -> Tuple[bool, List[str]]:
        """Validate that generated ideas respect exact count constraints."""
        issues = []
        for i, idea in enumerate(ideas):
            cc = idea.get("character_count", 0)
            if cc > self.memory.selected_characters:
                issues.append(f"Idea {i+1}: character_count {cc} > {self.memory.selected_characters}")
            lc = idea.get("location_count", 0)
            if lc > self.memory.selected_locations:
                issues.append(f"Idea {i+1}: location_count {lc} > {self.memory.selected_locations}")
            esc = idea.get("estimated_scene_count", 0)
            if esc != self.memory.selected_scenes:
                issues.append(f"Idea {i+1}: estimated_scene_count {esc} != {self.memory.selected_scenes}")
            ets = idea.get("estimated_total_shots", 0)
            if ets > self.memory.selected_total_shots:
                issues.append(f"Idea {i+1}: estimated_total_shots {ets} > {self.memory.selected_total_shots}")
        return (len(issues) == 0, issues)

    def _validate_screenplay_counts(self, data: Dict) -> Tuple[bool, List[str]]:
        """Validate that screenplay respects exact count constraints (shot node schema)."""
        issues = []
        scenes = data.get("scenes", [])
        if len(scenes) != self.memory.selected_scenes:
            issues.append(f"Scenes: got {len(scenes)}, expected {self.memory.selected_scenes}")

        # Shot nodes count (NOT estimated_shots)
        total_shots = sum(len(s.get("shots", [])) for s in scenes)
        if total_shots != self.memory.selected_total_shots:
            issues.append(f"Total shots: got {total_shots}, expected {self.memory.selected_total_shots}")

        # Character bible count
        cb = data.get("character_bible", [])
        if len(cb) != self.memory.selected_characters:
            issues.append(f"Character bible: got {len(cb)} entries, expected {self.memory.selected_characters}")

        # Location bible count
        lb = data.get("location_bible", [])
        if len(lb) != self.memory.selected_locations:
            issues.append(f"Location bible: got {len(lb)} entries, expected {self.memory.selected_locations}")

        # Unique scene location_ids match location_bible
        loc_ids_in_bible = set(l.get("location_id", "") for l in lb)
        for s in scenes:
            lid = s.get("location_id", "")
            if lid and lid not in loc_ids_in_bible:
                issues.append(f"Scene {s.get('scene_id', '?')} location_id '{lid}' not in location_bible")

        # Shot IDs must be unique
        all_shot_ids = []
        for s in scenes:
            for sh in s.get("shots", []):
                all_shot_ids.append(sh.get("shot_id", ""))
        if len(all_shot_ids) != len(set(all_shot_ids)):
            issues.append("Duplicate shot_id found across scenes")

        return (len(issues) == 0, issues)

    def regenerate_single_idea(self, params: Dict) -> Dict:
        """Regenerate a single idea from scratch using the same prompt template."""
        self._reset_progress()
        self.set_progress(10, "Regenerating idea...")
        result = self.generate_ideas(params)
        return result

    def generate_idea_variants(self, params: Dict) -> Dict:
        """Generate 3 variants of an existing idea based on user change request."""
        self._reset_progress()
        self.set_progress(10, "Preparing variant generation...")

        original_idea = params.get("idea", {})
        user_change = params.get("user_change", "")
        if not user_change.strip():
            return {"success": False, "error": "Please describe what you'd like to change"}

        self.set_progress(30, "Sending to LLM...")

        prompt = f"""You are a CINEMATIC IDEA VARIATION ENGINE.

Given an original film concept and a user's requested change, generate 3 distinct variants.

ORIGINAL CONCEPT:
{json.dumps(original_idea, indent=2)}

USER CHANGE REQUEST:
{user_change}

---

Generate EXACTLY 3 variants of the original concept that address the user's request.

Each variant must follow the same schema as the original concept with these keys:
- title (string)
- logline (string)
- genre_blend (string)
- tone (string)
- visual_identity (string)
- film_aesthetic_interpretation (string)
- character_count (number)
- location_count (number)
- estimated_scene_count (number)
- estimated_total_shots (number)
- shot_distribution_per_scene (object)
- main_characters (array of strings)
- main_locations (array of strings)
- emotional_hook (string)
- cinematic_hook (string)
- ending_style (string)
- dialogue_density (string)
- production_complexity (string)
- thumbnail_moment (string)
- short_synopsis (string)

Each variant MUST preserve the same character_count, location_count, estimated_scene_count, and estimated_total_shots as the original.
Each variant must feel meaningfully different from the original and from each other.
All outputs must remain cinematic, producible, and emotionally engaging."""

        system_suffix = """You MUST respond with ONLY a JSON array of exactly 3 objects. Each object must have all the keys listed above. The output must be valid JSON only."""

        result = self._call_llm(prompt, system_suffix=system_suffix)

        if result.get("success") and result.get("data") and isinstance(result["data"], list):
            self.set_progress(100, "Variants ready!", "3 variants generated")
            return {"success": True, "variants": result["data"], "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def generate_locations(self, params: Dict = None) -> Dict:
        """Stage 3: Extract from location_bible if present, else LLM fallback."""
        self._reset_progress()
        self.set_progress(10, "Checking location bible...")

        pg = self.memory.project_graph
        bible = pg.get("location_bible", [])
        if bible:
            self.set_progress(50, "Extracting from location bible...")
            self.memory.locations = bible
            pg["location_assets"] = {}
            for loc in bible:
                lid = loc.get("location_id", loc.get("id", ""))
                pg["location_assets"][lid] = {
                    "location_id": lid,
                    "approved_image": "",
                    "generation_history": [],
                    "variants": [],
                    "locked": False,
                    "approved": False,
                }
            self.set_progress(100, "Locations extracted from bible!", f"{len(bible)} locations ready")
            return {"success": True, "locations": bible, "from_bible": True}

        # Fallback: LLM generation (legacy path)
        screenplay = self.memory.screenplay
        if not screenplay:
            self.set_progress(0, "Failed", "No screenplay and no location bible")
            return {"success": False, "error": "No screenplay generated yet"}

        scenes = screenplay.get("scenes", [])
        unique_loc_ids = list(dict.fromkeys(s.get("location_id", "") for s in scenes if s.get("location_id")))
        count = min(len(unique_loc_ids), self.memory.selected_locations)

        self.set_progress(30, "Generating locations via LLM...")

        prompt_parts = [
            f"Based on the screenplay, generate exactly {count} reusable location assets as a JSON array.",
            "",
            f"Film Title: {screenplay.get('title', '')}",
            f"Tone: {screenplay.get('tone', '')}",
            f"Era: {self.memory.project_info.get('era', '')}",
            f"Visual Style: {self.memory.project_info.get('visual_style', '')}",
            f"Film Aesthetic: {self.memory.project_info.get('film_aesthetic', '')}",
            f"Location IDs to generate: {', '.join(unique_loc_ids)}",
            "",
            "For each scene, consider:",
        ]
        for s in scenes:
            prompt_parts.append(f"  {s.get('scene_id', '?')}: {s.get('scene_title', '')} at {s.get('location_id', '?')} - {s.get('synopsis', '')}")

        system_suffix = f"""You MUST respond with ONLY a JSON array of exactly {count} location objects. Each object must have:
- location_id (string, matching one from the screenplay)
- location_name (string, descriptive name)
- environment_type (string)
- architecture_style (string)
- mood (string)
- lighting_style (string)
- cinematic_features (string)
- environmental_storytelling (string)
- image_prompt (string, optimized for ZImage Turbo - describe the REUSABLE environment, NOT a scene)"""

        result = self._call_llm("\n".join(prompt_parts), system_suffix=system_suffix)

        if result.get("success") and result.get("data") and isinstance(result["data"], list):
            self.memory.locations = result["data"]
            self.set_progress(100, "Locations generated!", f"{len(result['data'])} ready")
            return {"success": True, "locations": result["data"], "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def generate_characters(self, params: Dict = None) -> Dict:
        """Stage 4: Extract from character_bible if present, else LLM fallback."""
        self._reset_progress()
        self.set_progress(10, "Checking character bible...")

        pg = self.memory.project_graph
        bible = pg.get("character_bible", [])
        if bible:
            self.set_progress(50, "Extracting from character bible...")
            self.memory.characters = bible
            pg["character_assets"] = {}
            for ch in bible:
                cid = ch.get("character_id", ch.get("id", ""))
                pg["character_assets"][cid] = {
                    "character_id": cid,
                    "approved_image": "",
                    "generation_history": [],
                    "variants": [],
                    "locked": False,
                    "approved": False,
                }
            self.set_progress(100, "Characters extracted from bible!", f"{len(bible)} characters ready")
            return {"success": True, "characters": bible, "from_bible": True}

        # Fallback: LLM generation (legacy path)
        screenplay = self.memory.screenplay
        if not screenplay:
            self.set_progress(0, "Failed", "No screenplay and no character bible")
            return {"success": False, "error": "No screenplay generated yet"}

        scenes = screenplay.get("scenes", [])
        unique_names = list(dict.fromkeys(
            name for s in scenes for name in s.get("characters_present", [])
        ))
        count = min(len(unique_names), self.memory.selected_characters)

        self.set_progress(30, "Generating characters via LLM...")

        prompt_parts = [
            f"Based on the screenplay, generate exactly {count} reusable character assets as a JSON array.",
            "",
            f"Film Title: {screenplay.get('title', '')}",
            f"Tone: {screenplay.get('tone', '')}",
            f"Era: {self.memory.project_info.get('era', '')}",
            f"Visual Style: {self.memory.project_info.get('visual_style', '')}",
            f"Film Aesthetic: {self.memory.project_info.get('film_aesthetic', '')}",
            "",
            "Character names from screenplay:",
        ]
        for name in unique_names:
            prompt_parts.append(f"  - {name}")

        prompt_parts.append("")
        prompt_parts.append("Scene context:")
        for s in scenes:
            prompt_parts.append(f"  {s.get('scene_id', '?')}: {s.get('scene_title', '')}")
            for name in s.get("characters_present", []):
                prompt_parts.append(f"    Present: {name}")

        system_suffix = f"""You MUST respond with ONLY a JSON array of exactly {count} character objects. Each object must have:
- character_id (string, e.g. "CH_1", "CH_2")
- character_name (string)
- role (string, e.g. protagonist, antagonist, supporting)
- age (string)
- physical_appearance (string)
- clothing (string, era-appropriate)
- personality (string)
- emotional_traits (string)
- signature_items (string)
- cinematic_presence (string)
- image_prompt (string, optimized for ZImage Turbo - describe the REUSABLE character, NOT a scene-specific moment)"""

        result = self._call_llm("\n".join(prompt_parts), system_suffix=system_suffix)

        if result.get("success") and result.get("data") and isinstance(result["data"], list):
            self.memory.characters = result["data"]
            self.set_progress(100, "Characters generated!", f"{len(result['data'])} ready")
            return {"success": True, "characters": result["data"], "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def generate_storyboard(self, params: Dict = None) -> Dict:
        """Stage 5: Shot enrichment pipeline. NEVER creates shots, only enriches existing ones."""
        self._reset_progress()
        self.set_progress(5, "Starting shot enrichment...")

        pg = self.memory.project_graph
        scene_graph = pg.get("scene_graph", [])
        if not scene_graph:
            scene_graph = self.memory.screenplay.get("scenes", [])
        if not scene_graph:
            self.set_progress(0, "Failed", "No scene graph available")
            return {"success": False, "error": "No scenes in screenplay"}

        # Collect all shots to enrich
        all_shots = []
        for s in scene_graph:
            for sh in s.get("shots", []):
                all_shots.append({
                    "scene_id": s.get("scene_id", ""),
                    "scene_title": s.get("scene_title", ""),
                    "location_id": s.get("location_id", ""),
                    "shot": sh,
                })

        if not all_shots:
            self.set_progress(0, "Failed", "No shot nodes in scene graph")
            return {"success": False, "error": "No shots in screenplay (has the new screenplay prompt been used?)"}

        self.set_progress(20, f"Enriching {len(all_shots)} shots...")

        # Check approvals
        approvals = pg.get("approvals", {})
        if not approvals.get("characters_approved", False) or not approvals.get("locations_approved", False):
            self.set_progress(0, "Blocked", "Characters and locations must be approved first")
            return {"success": False, "error": "Characters and locations must be approved before storyboard generation"}

        continuity = self.get_continuity_context()

        # Build per-shot enrichment requests (batch in groups to avoid huge LLM calls)
        enriched_shots = {}
        batch_size = 5
        for i in range(0, len(all_shots), batch_size):
            batch = all_shots[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(all_shots) + batch_size - 1) // batch_size
            pct = 20 + int(60 * (i / len(all_shots)))
            self.set_progress(pct, f"Enriching shots (batch {batch_num}/{total_batches})...")

            batch_prompt_parts = [
                "You are the SHOT ENRICHMENT PIPELINE inside an AI Film Production System.",
                "",
                "Your ONLY job is to enrich existing cinematic shot nodes with storyboard-ready fields.",
                "You MUST NOT create new shots, delete shots, reorder shots, or alter continuity.",
                "",
                "=== CONTINUITY ASSETS ===",
                continuity,
                "",
                "For each shot node below, enrich it with:",
                "- storyboard_prompt (Flux2 Klein-optimized prompt referencing approved character/location assets)",
                "- cinematic_composition (string)",
                "- lighting_enrichment (string)",
                "- framing_enrichment (string)",
                "- camera_direction_enrichment (string)",
                "- lens_suggestion (string)",
                "- atmosphere (string)",
                "",
                "RULES:",
                "- NEVER change shot_id, shot_number, shot_type, or any existing field",
                "- NEVER redesign characters or locations — reference approved assets only",
                "- NEVER add shots, remove shots, or change shot order",
                "- Each prompt must feel like a specific cinematic moment from that exact shot",
                "- Use the shot's existing camera_language, lighting_language, emotion, action as foundation",
                "- Refer to character_bible and location_bible for appearance continuity",
                "",
                "SHOTS TO ENRICH:",
            ]

            for entry in batch:
                shot = entry["shot"]
                batch_prompt_parts.append(
                    f"\n--- Shot: {shot.get('shot_id', '?')} ---"
                    f"\nScene: {entry.get('scene_id', '?')} - {entry.get('scene_title', '')}"
                    f"\nLocation: {entry.get('location_id', '?')}"
                    f"\nShot Type: {shot.get('shot_type', '')}"
                    f"\nCamera: {shot.get('camera_language', '')}"
                    f"\nLighting: {shot.get('lighting_language', '')}"
                    f"\nEmotion: {shot.get('emotion', '')}"
                    f"\nAction: {shot.get('action', '')}"
                    f"\nCharacters: {json.dumps(shot.get('characters_present', []))}"
                    f"\nDialogue: {json.dumps(shot.get('dialogue', []))}"
                    f"\nVisual Motifs: {json.dumps(shot.get('visual_motifs', []))}"
                    f"\nMotion Opportunities: {json.dumps(shot.get('motion_opportunities', []))}"
                    f"\nContinuity Notes: {shot.get('continuity_notes', '')}"
                    f"\nCinematic Notes: {shot.get('cinematic_notes', '')}"
                )

            system_suffix = """You MUST respond with ONLY a JSON object where keys are shot_ids and values are enriched shot data objects. Each enriched shot object must have:
- shot_id (string, MUST match input)
- storyboard_prompt (string, Flux2 Klein optimized)
- cinematic_composition (string)
- lighting_enrichment (string)
- framing_enrichment (string)
- camera_direction_enrichment (string)
- lens_suggestion (string)
- atmosphere (string)
- image_generated (boolean, always false)
- storyboard_status (string, "enriched")

Output example format:
{
  "SHOT_001": {
    "shot_id": "SHOT_001",
    "storyboard_prompt": "...",
    "cinematic_composition": "...",
    ...
  }
}"""

            result = self._call_llm("\n".join(batch_prompt_parts), system_suffix=system_suffix)

            if result.get("success") and result.get("data"):
                batch_enriched = result["data"]
                if isinstance(batch_enriched, dict):
                    enriched_shots.update(batch_enriched)
                elif isinstance(batch_enriched, list):
                    for item in batch_enriched:
                        if isinstance(item, dict) and item.get("shot_id"):
                            enriched_shots[item["shot_id"]] = item

        self.set_progress(90, "Merging enriched data into shot nodes...")

        # Merge enrichment back into scene_graph shots
        enriched_count = 0
        for s in scene_graph:
            for sh in s.get("shots", []):
                sid = sh.get("shot_id", "")
                if sid in enriched_shots:
                    enrichment = enriched_shots[sid]
                    for key in ["storyboard_prompt", "cinematic_composition", "lighting_enrichment",
                                 "framing_enrichment", "camera_direction_enrichment", "lens_suggestion",
                                 "atmosphere", "storyboard_status", "image_generated"]:
                        if key in enrichment:
                            sh[key] = enrichment[key]
                    sh["storyboard_status"] = "enriched"
                    enriched_count += 1

        pg["scene_graph"] = scene_graph
        pg["storyboards"] = enriched_shots

        # Also build backward-compat storyboard list
        self.memory.storyboard = []
        for s in scene_graph:
            entry = {
                "scene_id": s.get("scene_id", ""),
                "scene_title": s.get("scene_title", ""),
                "location_reference": s.get("location_id", ""),
                "shot_count": len(s.get("shots", [])),
                "shots": [{
                    "shot_number": sh.get("shot_number", 0),
                    "shot_id": sh.get("shot_id", ""),
                    "camera_direction": sh.get("camera_direction_enrichment", sh.get("shot_type", "")),
                    "lens_suggestion": sh.get("lens_suggestion", ""),
                    "lighting": sh.get("lighting_enrichment", sh.get("lighting_language", "")),
                    "atmosphere": sh.get("atmosphere", ""),
                    "cinematic_composition": sh.get("cinematic_composition", ""),
                    "image_prompt": sh.get("storyboard_prompt", ""),
                } for sh in s.get("shots", [])]
            }
            self.memory.storyboard.append(entry)

        self.set_progress(100, "Shot enrichment complete!", f"{enriched_count} shots enriched")
        return {
            "success": True,
            "storyboard": self.memory.storyboard,
            "enriched_count": enriched_count,
            "total_shots": len(all_shots),
        }

    def generate_video_prompts(self, params: Dict = None) -> Dict:
        """Stage 6: Generate LTX 2.3 video prompts from storyboard."""
        storyboard = self.memory.storyboard
        if not storyboard:
            return {"success": False, "error": "No storyboard generated yet"}

        screenplay = self.memory.screenplay
        continuity = self.get_continuity_context()

        # Build dialogue context from screenplay
        dialogue_context = ""
        if screenplay:
            for s in screenplay.get("scenes", []):
                if s.get("dialogue"):
                    dialogue_context += f"\n{s.get('scene_id')}: {s.get('scene_title')}\n"
                    for d in s.get("dialogue", []):
                        dialogue_context += f"  {d.get('speaker', '')}: {d.get('text', '')}\n"

        prompt_parts = [
            "Generate LTX 2.3 video prompts as a JSON array based on the storyboard and screenplay.",
            "",
            "=== CONTINUITY ===",
            continuity,
            "",
            "=== DIALOGUE REFERENCE ===",
            dialogue_context if dialogue_context else "(No dialogue - use visual storytelling)",
            "",
            "For each storyboard scene, generate ONE video prompt per shot.",
            "The storyboard image will serve as KEYFRAME ZERO.",
            "Video prompts must ONLY describe motion, camera movement, and environmental dynamics.",
            "DO NOT redescribe static appearance (characters, locations).",
        ]

        system_suffix = """You MUST respond with ONLY a JSON array. Each element must have:
- scene_id (string, matching storyboard)
- scene_title (string)
- shots (array of video prompt objects, one per shot from the storyboard, each with:)
  - shot_number (number)
  - subject_motion (string, describe ONLY character/object movement)
  - camera_motion (string, e.g. dolly push, orbit, handheld, tracking, static)
  - environmental_motion (string, e.g. drifting dust, rain, smoke, wind, flickering lights)
  - audio (string, describe dialogue delivery, ambience, or silence)
  - ltx_prompt (string, the COMPLETE LTX 2.3 prompt combining all motion elements)

LTX PROMPT RULES:
- Prioritize: subject motion, camera motion, environmental motion
- When dialogue is enabled: include spoken dialogue and vocal delivery
- Do NOT describe static visual elements that already exist in the KEYFRAME ZERO image
- Keep prompts concise and motion-focused"""

        result = self._call_llm("\n".join(prompt_parts), system_suffix=system_suffix)

        if result.get("success") and result.get("data") and isinstance(result["data"], list):
            self.memory.video_prompts = result["data"]
            return {"success": True, "video_prompts": result["data"], "raw": result.get("raw")}

        return result

    def create_shot_variant(self, params: Dict) -> Dict:
        """Generate 3 non-destructive variants of a shot node."""
        self._reset_progress()
        self.set_progress(10, "Preparing shot variant generation...")

        shot_id = params.get("shot_id", "")
        user_instruction = params.get("user_instruction", "")
        if not shot_id:
            return {"success": False, "error": "shot_id required"}

        pg = self.memory.project_graph
        shot = pg.get("shots", {}).get(shot_id)
        if not shot:
            return {"success": False, "error": f"Shot {shot_id} not found"}

        # Find scene context
        scene_ctx = ""
        for s in pg.get("scene_graph", []):
            for sh in s.get("shots", []):
                if sh.get("shot_id") == shot_id:
                    scene_ctx = f"Scene {s.get('scene_id', '?')}: {s.get('scene_title', '')} @ {s.get('location_id', '?')}"
                    break

        self.set_progress(30, "Sending to LLM...")

        prompt = f"""You are a SHOT VARIATION ENGINE. Generate 3 non-destructive variants of a cinematic shot node.

ORIGINAL SHOT:
{json.dumps(shot, indent=2)}

SCENE CONTEXT:
{scene_ctx}

USER CHANGE REQUEST:
{user_instruction}

CONTINUITY CONTEXT:
{self.get_continuity_context()}

Generate 3 distinct variants that address the user's request while preserving:
- shot_id (keep original)
- shot_number (keep original)
- characters_present (keep original)
- dialogue (keep original)
- scene relationships (keep original)

Each variant may differ in:
- shot_type
- camera_language
- lighting_language
- emotion
- action
- visual_motifs
- motion_opportunities
- environmental_motion
- audio_notes
- continuity_notes
- cinematic_notes

Each variant must be meaningfully different from the original and from each other."""

        system_suffix = """You MUST respond with ONLY a JSON array of exactly 3 variant objects. Each variant object must have:
- variant_id (string, e.g. "VAR_001")
- parent_shot_id (string, the original shot_id)
- variant_number (number)
- user_instruction (string, the user's change request)
- shot_type (string)
- camera_language (string)
- lighting_language (string)
- emotion (string)
- action (string)
- visual_motifs (array of strings)
- motion_opportunities (array of strings)
- environmental_motion (array of strings)
- audio_notes (array of strings)
- continuity_notes (string)
- cinematic_notes (string)
- storyboard_status (string, "variant")
- storyboard_prompt (string, Flux2 Klein optimized, if applicable)
- cinematic_composition (string)
- lighting_enrichment (string)
- framing_enrichment (string)
- camera_direction_enrichment (string)
- lens_suggestion (string)
- atmosphere (string)"""

        result = self._call_llm(prompt, system_suffix=system_suffix)

        if result.get("success") and result.get("data"):
            variants = result["data"] if isinstance(result["data"], list) else [result["data"]]
            # Store variants in project_graph
            for var in variants:
                if isinstance(var, dict) and var.get("variant_id"):
                    pg["variants"][var["variant_id"]] = var
            self.set_progress(100, "Shot variants ready!", f"{len(variants)} variants generated")
            return {"success": True, "variants": variants, "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def set_lock(self, params: Dict) -> Dict:
        """Lock/unlock a field on a character, location, or shot."""
        category = params.get("category", "")  # characters, locations, shots
        item_id = params.get("item_id", "")
        field = params.get("field", "")  # e.g. appearance, costume, composition, camera
        locked = params.get("locked", True)

        if category not in ("characters", "locations", "shots"):
            return {"success": False, "error": f"Invalid category: {category}"}

        pg = self.memory.project_graph
        locks = pg.setdefault("locks", {})
        cat_locks = locks.setdefault(category, {})
        item_locks = cat_locks.setdefault(item_id, {})
        item_locks[field] = bool(locked)
        return {"success": True, "lock_state": {category: {item_id: item_locks}}}

    def get_locks(self) -> Dict:
        """Get all lock states."""
        return {"success": True, "locks": self.memory.project_graph.get("locks", {})}

    def approve_characters(self, params: Dict = None) -> Dict:
        """Mark characters as approved, enabling storyboard generation."""
        pg = self.memory.project_graph
        approvals = pg.setdefault("approvals", {})
        approvals["characters_approved"] = True
        return {"success": True, "approvals": approvals}

    def approve_locations(self, params: Dict = None) -> Dict:
        """Mark locations as approved, enabling storyboard generation."""
        pg = self.memory.project_graph
        approvals = pg.setdefault("approvals", {})
        approvals["locations_approved"] = True
        return {"success": True, "approvals": approvals}

    def get_approvals(self) -> Dict:
        """Get current approval state."""
        return {"success": True, "approvals": self.memory.project_graph.get("approvals", {})}

    def generate_turnaround(self, params: Dict) -> Dict:
        """Generate a turnaround sheet for an approved character (ZImage Turbo)."""
        character_id = params.get("character_id", "")
        if not character_id:
            return {"success": False, "error": "character_id required"}

        pg = self.memory.project_graph
        approvals = pg.get("approvals", {})
        if not approvals.get("characters_approved", False):
            return {"success": False, "error": "Characters must be approved before generating turnaround sheets"}

        # Find character in bible
        bible = pg.get("character_bible", [])
        character = None
        for ch in bible:
            if ch.get("character_id") == character_id or ch.get("id") == character_id:
                character = ch
                break
        if not character:
            character = next((c for c in self.memory.characters if c.get("character_id") == character_id), None)
        if not character:
            return {"success": False, "error": f"Character {character_id} not found in bible"}

        return {
            "success": True,
            "turnaround_request": {
                "character_id": character_id,
                "character_name": character.get("full_name", character.get("character_name", "")),
                "prompt": f"Turnaround reference sheet of {character.get('full_name', character.get('character_name', ''))} — {character.get('physical_appearance', '')}, {character.get('clothing', '')}. Four views: front, three-quarter, profile, back. Clean background, consistent lighting, professional character turnaround reference sheet. ZImage Turbo.",
            }
        }

    def get_asset_studio_state(self) -> Dict:
        """Get full asset studio state for frontend."""
        pg = self.memory.project_graph
        return {
            "success": True,
            "character_bible": pg.get("character_bible", []),
            "location_bible": pg.get("location_bible", []),
            "character_assets": pg.get("character_assets", {}),
            "location_assets": pg.get("location_assets", {}),
            "character_sheets": pg.get("character_sheets", {}),
            "location_sheets": pg.get("location_sheets", {}),
            "approvals": pg.get("approvals", {}),
            "locks": pg.get("locks", {}),
        }

    def get_shot(self, shot_id: str) -> Dict:
        """Get enriched shot data by shot_id."""
        pg = self.memory.project_graph
        shot = pg.get("shots", {}).get(shot_id)
        if not shot:
            return {"success": False, "error": f"Shot {shot_id} not found"}

        # Find scene context
        scene_ctx = None
        for s in pg.get("scene_graph", []):
            for sh in s.get("shots", []):
                if sh.get("shot_id") == shot_id:
                    scene_ctx = {
                        "scene_id": s.get("scene_id", ""),
                        "scene_title": s.get("scene_title", ""),
                        "location_id": s.get("location_id", ""),
                        "synopsis": s.get("synopsis", ""),
                        "emotional_tone": s.get("emotional_tone", ""),
                    }
                    break
            if scene_ctx:
                break

        # Get variants for this shot
        shot_variants = [v for v in pg.get("variants", {}).values() if v.get("parent_shot_id") == shot_id]

        result = {
            "success": True,
            "shot": shot,
            "scene": scene_ctx,
            "variants": shot_variants,
            "locks": pg.get("locks", {}).get("shots", {}).get(shot_id, {}),
            "storyboard_enrichment": pg.get("storyboards", {}).get(shot_id, {}),
        }
        return result

    def regenerate_shot(self, params: Dict) -> Dict:
        """Regenerate a single shot, preserving continuity and locked fields."""
        self._reset_progress()
        self.set_progress(10, "Preparing shot regeneration...")

        shot_id = params.get("shot_id", "")
        if not shot_id:
            return {"success": False, "error": "shot_id required"}

        pg = self.memory.project_graph
        shot = pg.get("shots", {}).get(shot_id)
        if not shot:
            return {"success": False, "error": f"Shot {shot_id} not found"}

        # Find scene context
        scene_ctx = None
        scene_graph = pg.get("scene_graph", [])
        for s in scene_graph:
            for sh in s.get("shots", []):
                if sh.get("shot_id") == shot_id:
                    scene_ctx = s
                    break
            if scene_ctx:
                break

        if not scene_ctx:
            return {"success": False, "error": "Shot scene context not found"}

        locks = pg.get("locks", {}).get("shots", {}).get(shot_id, {})
        lock_hints = "\n".join([f"- {k}: CANNOT CHANGE (locked)" for k, v in locks.items() if v]) if locks else "- No locked fields"

        self.set_progress(40, "Regenerating shot via LLM...")

        prompt = f"""You are a SHOT REGENERATION ENGINE. Regenerate the following cinematic shot while preserving continuity.

SHOT TO REGENERATE:
{json.dumps(shot, indent=2)}

SCENE CONTEXT:
{json.dumps(scene_ctx, indent=2, default=str)}

CONTINUITY:
{self.get_continuity_context()}

LOCKS:
{lock_hints}

REGENERATION RULES:
- Preserve shot_id, shot_number, characters_present, dialogue exactly as-is
- Preserve locked fields (marked CANNOT CHANGE)
- The shot_type, camera_language, lighting_language, emotion, action, and all cinematic fields can be reinterpreted
- Maintain scene continuity — do not create narrative contradictions
- All outputs must remain cinematic and producible

Generate a fresh cinematic interpretation of this shot that feels meaningfully different while being continuous."""

        system_suffix = """You MUST respond with ONLY a JSON object with the regenerated shot data. The object must have:
- shot_id (string, MUST match original)
- shot_number (number, MUST match original)
- shot_type (string)
- camera_language (string)
- lighting_language (string)
- emotion (string)
- characters_present (array, MUST match original)
- dialogue (array, MUST match original)
- action (string)
- visual_motifs (array of strings)
- motion_opportunities (array of strings)
- environmental_motion (array of strings)
- audio_notes (array of strings)
- continuity_notes (string)
- cinematic_notes (string)
- storyboard_status (string, "regenerated")"""

        result = self._call_llm(prompt, system_suffix=system_suffix)

        if result.get("success") and result.get("data"):
            regenerated = result["data"]
            # Preserve locked fields from original
            for field, val in locks.items():
                if val and field in shot:
                    regenerated[field] = shot[field]
            # Preserve protected fields
            for field in ["shot_id", "shot_number", "characters_present", "dialogue"]:
                if field in shot:
                    regenerated[field] = shot[field]
            # Update shot in scene_graph and shots dict
            for s in scene_graph:
                for i, sh in enumerate(s.get("shots", [])):
                    if sh.get("shot_id") == shot_id:
                        s["shots"][i] = regenerated
                        break
            pg["shots"][shot_id] = regenerated
            self.set_progress(100, "Shot regenerated!", f"{shot_id} updated")
            return {"success": True, "shot": regenerated, "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def approve_shot(self, params: Dict) -> Dict:
        """Mark a shot as approved for timeline."""
        shot_id = params.get("shot_id", "")
        if not shot_id:
            return {"success": False, "error": "shot_id required"}
        pg = self.memory.project_graph
        shot = pg.get("shots", {}).get(shot_id)
        if not shot:
            return {"success": False, "error": f"Shot {shot_id} not found"}
        shot["storyboard_status"] = "approved"
        shot["approved"] = True
        pg["shots"][shot_id] = shot
        # Also update in scene_graph
        for s in pg.get("scene_graph", []):
            for i, sh in enumerate(s.get("shots", [])):
                if sh.get("shot_id") == shot_id:
                    s["shots"][i]["storyboard_status"] = "approved"
                    s["shots"][i]["approved"] = True
                    break
        return {"success": True, "shot": shot}

    def generate_shot_image(self, params: Dict) -> Dict:
        """Generate storyboard image prompt for a shot (Flux2 Klein). Returns the prompt for frontend to execute."""
        shot_id = params.get("shot_id", "")
        if not shot_id:
            return {"success": False, "error": "shot_id required"}
        pg = self.memory.project_graph
        shot = pg.get("shots", {}).get(shot_id)
        if not shot:
            return {"success": False, "error": f"Shot {shot_id} not found"}
        enrichment = pg.get("storyboards", {}).get(shot_id, {})
        prompt = enrichment.get("storyboard_prompt", shot.get("storyboard_prompt", ""))
        if not prompt:
            # Build a prompt from available data
            chars = ", ".join(shot.get("characters_present", [])) or "characters"
            action = shot.get("action", "scene")
            camera = shot.get("camera_language", "cinematic shot")
            lighting = shot.get("lighting_language", "cinematic lighting")
            emotion = shot.get("emotion", "dramatic")
            prompt = f"{camera} of {chars}, {action}, {lighting}, {emotion} atmosphere, cinematic composition, film still, Flux2 Klein"
        shot["image_generated"] = True
        shot["storyboard_status"] = "enriched"
        pg["shots"][shot_id] = shot
        return {
            "success": True,
            "shot_id": shot_id,
            "prompt": prompt,
            "shot": shot,
        }

    def generate_shot_from_scene(self, params: Dict) -> Dict:
        """Generate 3 shot variants from scene context (no existing shot needed)."""
        self._reset_progress()
        self.set_progress(10, "Preparing shot generation from scene...")

        scene_id = params.get("scene_id", "")
        scene_title = params.get("scene_title", "")
        location_id = params.get("location_id", "")
        characters_present = params.get("characters_present", [])
        emotional_tone = params.get("emotional_tone", "")
        synopsis = params.get("synopsis", "")
        shot_number = params.get("shot_number", 1)
        user_instruction = params.get("user_instruction", "").strip()
        if not user_instruction:
            user_instruction = "Vary the seed and generate 3 creative cinematic alternatives with different camera placements, lighting setups, and compositions."

        self.set_progress(30, "Sending to LLM...")

        prompt = f"""You are a SHOT GENERATION ENGINE. Create 3 distinct cinematic shot variants from scene context.

SCENE CONTEXT:
- Scene ID: {scene_id}
- Scene Title: {scene_title}
- Location: {location_id}
- Characters: {', '.join(characters_present) if isinstance(characters_present, list) else str(characters_present)}
- Emotional Tone: {emotional_tone}
- Synopsis: {synopsis}
- Shot Number: {shot_number} of scene

USER INSTRUCTION:
{user_instruction}

CONTINUITY CONTEXT:
{self.get_continuity_context()}

Generate 3 distinct shot variants. Each variant must be a complete shot object with these fields:
- shot_type (string: Wide, Medium, Close-Up, Extreme Close-Up, Over-the-Shoulder, Two-Shot, POV, Dutch Angle, Crane, Drone, Tracking, etc.)
- camera_language (string describing camera position, movement, angle)
- lighting_language (string describing lighting setup, mood, color scheme)
- emotion (string describing the emotional quality of the shot)
- action (string describing the physical action in the shot)
- characters_present (array of character names/IDs present in the shot)
- visual_motifs (array of recurring visual themes)
- motion_opportunities (array of dynamic motion elements)
- environmental_motion (array of environmental movements)
- audio_notes (array of sound design notes)
- continuity_notes (string describing how this shot connects to adjacent shots)
- cinematic_notes (string with directorial notes)

Each variant must be meaningfully different from the others in camera, lighting, and composition."""

        self.set_progress(50, "Generating 3 shot variants...")

        system_suffix = f"""Generate exactly 3 variants.

Output format: JSON object with a single key "variants" containing an array of exactly 3 shot objects.

Example:
{{
  "variants": [
    {{
      "shot_type": "Wide",
      "camera_language": "Low angle tracking shot from street level, rain hitting lens",
      "lighting_language": "Neon backlight with rain-diffused street lamps",
      "emotion": "Isolated, observed",
      "action": "Protagonist walks slowly through puddles, collar up",
      "characters_present": ["KAI"],
      "visual_motifs": ["reflections", "neon", "rain"],
      "motion_opportunities": ["rain falling", "slow walk"],
      "environmental_motion": ["neon flicker", "steam rising"],
      "audio_notes": ["steady rain", "distant traffic hum"],
      "continuity_notes": "Opens the scene, establishes location",
      "cinematic_notes": "Slow zoom in as character approaches camera"
    }}
  ]
}}"""

        result = self._call_llm(prompt, system_suffix=system_suffix)

        self.set_progress(80, "Processing variants...")

        variants = []
        llm_error = None
        if result.get("success") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                variants = data.get("variants", [data])
            elif isinstance(data, list):
                variants = data[:3]
            if not variants:
                llm_error = "LLM returned no variant data"
            else:
                # Assign variant_ids
                for i, v in enumerate(variants):
                    v["variant_id"] = f"VAR_{scene_id}_{shot_number}_{i+1}"
                    v["parent_shot"] = f"{scene_id}_Shot_{shot_number}"
                    v["variant_number"] = i + 1

                if len(variants) < 3:
                    self.set_progress(85, "Not enough variants, retrying...")
                    result = self._call_llm(
                        prompt + f"\n\nPREVIOUS ERROR: Only generated {len(variants)} variants. Generate exactly 3.",
                        system_suffix=system_suffix
                    )
                    if result.get("success") and result.get("data"):
                        data = result["data"]
                        if isinstance(data, dict):
                            variants2 = data.get("variants", [data])
                        elif isinstance(data, list):
                            variants2 = data[:3]
                        if variants2:
                            for i, v in enumerate(variants2):
                                v["variant_id"] = f"VAR_{scene_id}_{shot_number}_{i+1}"
                                v["parent_shot"] = f"{scene_id}_Shot_{shot_number}"
                                v["variant_number"] = i + 1
                            if len(variants2) >= 3:
                                variants = variants2
                            else:
                                llm_error = f"Retry generated {len(variants2)} variants, need 3"
                        else:
                            llm_error = "Retry also returned no data"
        else:
            llm_error = result.get("error", "LLM call failed")

        if llm_error:
            self.set_progress(0, "Failed", llm_error)
            return {"success": False, "error": llm_error}

        self.set_progress(100, "Shot variants ready!")
        return {"success": True, "variants": variants[:3]}

    def generate_scene_shots(self, params: Dict) -> Dict:
        """Generate shot nodes for a single scene using the master prompt structure."""
        self._reset_progress()
        self.set_progress(10, "Preparing scene shot generation...")

        scene_id = params.get("scene_id", "SC_001")
        scene_title = params.get("scene_title", "")
        location_id = params.get("location_id", "")
        characters_present = params.get("characters_present", [])
        emotional_tone = params.get("emotional_tone", "")
        synopsis = params.get("synopsis", "")
        time_of_day = params.get("time_of_day", "Day")
        shot_count = int(params.get("shot_count", 1))
        dialogue_enabled = params.get("dialogue_enabled", True)

        self.set_progress(20, f"Generating {shot_count} shot nodes for {scene_id}...")

        prompt = f"""You are a CINEMATIC SHOT DESIGN ENGINE. Generate exactly {shot_count} cinematic shot nodes for a single scene.

SCENE CONTEXT:
- Scene ID: {scene_id}
- Scene Title: {scene_title}
- Location: {location_id}
- Characters Present: {', '.join(characters_present) if isinstance(characters_present, list) else str(characters_present)}
- Emotional Tone: {emotional_tone}
- Time of Day: {time_of_day}
- Synopsis: {synopsis}
- Dialogue Enabled: {str(dialogue_enabled)}

GENERATION RULES:
- Generate EXACTLY {shot_count} shot nodes
- Each shot must be visually distinct from the others
- Shot types must vary (Wide, Medium, Close-Up, OTS, Tracking, Low Angle, POV, Insert, Establishing, etc.)
- Each shot must have a unique cinematic intent
- Shots should tell a visual story that matches the emotional tone

Each shot node must have EXACTLY these fields:
- shot_id (string, e.g. "{scene_id}_SHOT_1")
- shot_number (number, starting from 1)
- shot_type (string)
- camera_language (string, camera position, movement, angle, lens)
- lighting_language (string, lighting setup, mood, color)
- emotion (string, emotional quality of the shot)
- characters_present (array of strings, character names/IDs in this shot)
- dialogue (array of objects with speaker and text; empty array if dialogue_enabled is false)
- action (string, physical action in the shot)
- visual_motifs (array of strings)
- motion_opportunities (array of strings)
- environmental_motion (array of strings)
- audio_notes (array of strings)
- continuity_notes (string)
- cinematic_notes (string)
- storyboard_status (string, always "pending")

IMPORTANT: Total shots MUST be exactly {shot_count}."""

        system_suffix = f"""Output ONLY a JSON object with a single key "shots" containing an array of exactly {shot_count} shot objects.

Example:
{{
  "shots": [
    {{
      "shot_id": "{scene_id}_SHOT_1",
      "shot_number": 1,
      "shot_type": "Wide Establishing Shot",
      "camera_language": "Static wide frame from low angle, rain streaking across lens",
      "lighting_language": "Neon backlight with rain-diffused street lamps, high contrast",
      "emotion": "Isolated, foreboding",
      "characters_present": ["{characters_present[0] if isinstance(characters_present, list) and characters_present else 'PROTAGONIST'}"],
      "dialogue": [],
      "action": "The protagonist stands alone at the edge of the street, rain cascading off their coat",
      "visual_motifs": ["neon reflections", "rain", "isolation"],
      "motion_opportunities": ["slow rain fall", "character breathing visible"],
      "environmental_motion": ["flickering neon sign", "steam rising from grate"],
      "audio_notes": ["steady rain", "distant traffic hum", "occasional thunder"],
      "continuity_notes": "Opens the scene, establishes location and mood",
      "cinematic_notes": "Slow push-in as character takes first step forward",
      "storyboard_status": "pending"
    }}
  ]
}}"""

        result = self._call_llm(prompt, system_suffix=system_suffix)

        self.set_progress(80, "Processing shot nodes...")

        shots = []
        llm_error = None
        if result.get("success") and result.get("data"):
            data = result["data"]
            if isinstance(data, dict):
                shots = data.get("shots", [])
            elif isinstance(data, list):
                shots = data
            if not shots and result.get("raw"):
                llm_error = "LLM returned no shot data"
        else:
            llm_error = result.get("error", "LLM call failed")

        if shots and len(shots) != shot_count:
            self.set_progress(85, f"Shot count mismatch ({len(shots)} vs {shot_count}), retrying...")
            correction = f"PREVIOUS ERROR: Generated {len(shots)} shots instead of exactly {shot_count}. Regenerate with EXACTLY {shot_count} shots."
            result = self._call_llm(
                prompt + "\n\n" + correction,
                system_suffix=system_suffix
            )
            if result.get("success") and result.get("data"):
                data = result["data"]
                if isinstance(data, dict):
                    shots = data.get("shots", [])
                elif isinstance(data, list):
                    shots = data
                if not shots:
                    llm_error = "Retry also returned no shot data"

        if llm_error:
            self.set_progress(0, "Failed", llm_error)
            return {"success": False, "error": llm_error}

        # Ensure shot_id and shot_number consistency
        for i, sh in enumerate(shots):
            if not sh.get("shot_id"):
                sh["shot_id"] = f"{scene_id}_SHOT_{i+1}"
            if not sh.get("shot_number"):
                sh["shot_number"] = i + 1
            if not sh.get("storyboard_status"):
                sh["storyboard_status"] = "pending"

        # Persist to project_graph for cross-refresh survival
        pg = self.memory.project_graph
        if scene_id and shots:
            for sh in shots:
                pg["shots"][sh.get("shot_id")] = sh
            for si, sc in enumerate(pg.get("scene_graph", [])):
                if sc.get("scene_id") == scene_id:
                    pg["scene_graph"][si]["shots"] = shots
                    break
            else:
                pg["scene_graph"].append({"scene_id": scene_id, "shots": shots})

        self.set_progress(100, "Scene shots generated!")
        return {"success": True, "shots": shots}

    def reset(self):
        """Reset the orchestrator memory."""
        self.memory.reset()

    def to_dict(self) -> Dict:
        return self.memory.to_dict()

    def from_dict(self, data: Dict):
        self.memory.from_dict(data)

    def sync_shots_from_screenplay(self, screenplay: Dict) -> Dict:
        """Sync screenplayData shots into project_graph so approve/lock endpoints find them."""
        scenes = screenplay.get("scenes", []) if isinstance(screenplay, dict) else []
        if not scenes:
            return {"success": False, "error": "No scenes in screenplay"}
        pg = self.memory.project_graph
        count = 0
        for s in scenes:
            sid = s.get("scene_id")
            if not sid:
                continue
            for sh in s.get("shots", []):
                sid2 = sh.get("shot_id")
                if sid2:
                    pg["shots"][sid2] = sh
                    count += 1
            # Update scene_graph
            found = False
            for si, sc in enumerate(pg.get("scene_graph", [])):
                if sc.get("scene_id") == sid:
                    pg["scene_graph"][si]["shots"] = s.get("shots", [])
                    found = True
                    break
            if not found:
                pg["scene_graph"].append({"scene_id": sid, "shots": s.get("shots", [])})
        return {"success": True, "count": count}

    def generate_asset_prompt(self, asset_type: str, asset: Dict) -> Dict:
        """Generate an image_prompt for a character or location using the LLM."""
        info = self.memory.project_info
        genre = info.get("genres", "") or info.get("genre", "")
        vs = info.get("visual_style", "")
        fa = info.get("film_aesthetic", "")
        era = info.get("era", "")
        proj_context = f"Genre: {genre}; Visual Style: {vs}; Film Aesthetic: {fa}; Era: {era}"

        if asset_type == "char":
            name = asset.get("full_name", asset.get("character_name", asset.get("name", "")))
            role = asset.get("role", "")
            personality = asset.get("personality", asset.get("description", ""))
            appearance = asset.get("physical_appearance", "")
            clothing = asset.get("clothing_continuity", asset.get("clothing", ""))
            identity = asset.get("visual_identity", "")
            prompt_text = f"""Project context: {proj_context}

You are a cinema visual AI. Given this character, write a detailed visual image generation prompt for a cinematic character reference image.

Character: {name}
Role: {role}
Personality/traits: {personality}
Physical appearance: {appearance}
Clothing/costume: {clothing}
Visual identity: {identity}

Write a single, rich image generation prompt that describes how this character should look in a cinematic character reference shot. Include: facial features, hairstyle, costume details, body type, posture, lighting, mood, and background. Return ONLY the prompt text, no explanation, no JSON."""
        else:
            name = asset.get("location_name", asset.get("environment_type", asset.get("name", "")))
            arch = asset.get("architecture_style", "")
            mood = asset.get("mood", "")
            lighting = asset.get("lighting_style", "")
            period = asset.get("time_period", "")
            desc = asset.get("description", "")
            prompt_text = f"""Project context: {proj_context}

You are a cinema visual AI. Given this location, write a detailed visual image generation prompt for a cinematic environment reference image.

Location: {name}
Architecture: {arch}
Mood: {mood}
Lighting: {lighting}
Time period: {period}
Description: {desc}

Write a single, rich image generation prompt that describes how this location should look in a cinematic environment reference shot. Include: architectural details, lighting conditions, color palette, atmosphere, camera angle, and mood. Return ONLY the prompt text, no explanation, no JSON."""

        result = self._call_llm(prompt_text, system_suffix="You output short, image-generation-ready visual prompts only.", json_output=False)
        if result.get("success"):
            prompt = result["data"].strip().strip('"').strip("'") if result.get("data") else ""
            if prompt:
                # Persist the prompt to project_graph so it survives page refresh
                aid = asset.get("character_id") or asset.get("id") or asset.get("location_id") or asset.get("_id", "")
                if aid:
                    bible_key = "character_bible" if asset_type == "char" else "location_bible"
                    pg = self.memory.project_graph
                    for entry in pg.get(bible_key, []):
                        eid = entry.get("character_id") or entry.get("id") or entry.get("location_id") or entry.get("_id", "")
                        if eid == aid:
                            entry["image_prompt"] = prompt
                            break
                return {"success": True, "prompt": prompt}
            return {"success": False, "error": "LLM returned empty response. Check your LLM provider/model settings."}
        return {"success": False, "error": result.get("error", "LLM call failed")}

    def sync_bibles_from_frontend(self, character_bible: List = None, location_bible: List = None) -> Dict:
        """Sync frontend character/location bible data into project_graph when backend memory is empty."""
        pg = self.memory.project_graph
        if character_bible and len(character_bible) > 0:
            if not pg.get("character_bible") or len(pg["character_bible"]) == 0:
                pg["character_bible"] = character_bible
        if location_bible and len(location_bible) > 0:
            if not pg.get("location_bible") or len(pg["location_bible"]) == 0:
                pg["location_bible"] = location_bible
        return {
            "success": True,
            "character_bible": pg.get("character_bible", []),
            "location_bible": pg.get("location_bible", []),
        }
