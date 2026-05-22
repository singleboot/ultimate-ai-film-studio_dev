import json
import os
import threading
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

        if self.memory.project_info:
            info = self.memory.project_info
            parts.append("=== PROJECT CONTEXT ===")
            for k, v in info.items():
                if v:
                    parts.append(f"{k}: {v}")

        if self.memory.screenplay:
            sp = self.memory.screenplay
            parts.append("\n=== SCREENPLAY CONTINUITY ===")
            parts.append(f"Title: {sp.get('title', '')}")
            parts.append(f"Logline: {sp.get('logline', '')}")
            parts.append(f"Tone: {sp.get('tone', '')}")
            scenes = sp.get("scenes", [])
            for s in scenes:
                parts.append(f"  Scene {s.get('scene_id', '?')}: {s.get('scene_title', '')} @ {s.get('location_id', '?')}")
                for c in s.get("characters_present", []):
                    parts.append(f"    Character: {c}")

        if self.memory.characters:
            parts.append("\n=== CHARACTER ASSETS ===")
            for ch in self.memory.characters:
                parts.append(f"  [{ch.get('character_id', '?')}] {ch.get('character_name', '')} - {ch.get('role', '')}")

        if self.memory.locations:
            parts.append("\n=== LOCATION ASSETS ===")
            for loc in self.memory.locations:
                parts.append(f"  [{loc.get('location_id', '?')}] {loc.get('location_name', '')} - {loc.get('environment_type', '')}")

        if self.memory.storyboard:
            parts.append("\n=== STORYBOARD ASSETS ===")
            for sb in self.memory.storyboard:
                parts.append(f"  Scene {sb.get('scene_id', '?')}: {sb.get('shot_count', 0)} shots")

        return "\n".join(parts)

    def _call_llm(self, prompt: str, system_suffix: str = "", json_output: bool = True) -> Dict:
        """Call the LLM with the master system prompt plus any stage-specific suffix."""
        if not self.llm_engine:
            return {"success": False, "error": "LLM engine not available"}

        system_prompt = self._master_system_prompt
        if system_suffix:
            system_prompt = system_prompt + "\n\n" + system_suffix

        result = self.llm_engine.generate(
            provider_id=self.memory.project_info.get("llm_provider", "ollama"),
            model=self.memory.project_info.get("llm_model", "llama3.1"),
            prompt=prompt,
            system_prompt=system_prompt,
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
- Total shot count cannot exceed MAXIMUM_TOTAL_SHOTS

The AI must intelligently determine:
- number of scenes
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
                correction = f"The previous output had count issues: {'; '.join(issues)}. Regenerate ensuring exact counts."
                result = self._call_llm(prompt + "\n\n" + correction, system_suffix=system_suffix)

            if result.get("success") and result.get("data") and isinstance(result["data"], list):
                self.memory.ideas = result["data"]
                self.set_progress(100, "Ideas generated!", f"{len(result['data'])} concepts ready")
                return {"success": True, "ideas": result["data"], "raw": result.get("raw")}

        self.set_progress(0, "Failed", result.get("error", "Generation failed"))
        return result

    def generate_screenplay(self, params: Dict) -> Dict:
        """Stage 2: Generate master screenplay with exact count enforcement."""
        self._reset_progress()
        self.set_progress(5, "Starting screenplay generation...")

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
            f"Based on the selected story idea, generate a full screenplay as a JSON object.",
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
            f"EXACTLY {shot_count} total shots required (sum of all scene estimated_shots)",
        ]

        pacing_guide = self._get_pacing_guide(genre_blend, tone)
        if pacing_guide:
            prompt_parts.append(f"\nCinematic Pacing Guide:\n{pacing_guide}")

        system_suffix = f"""You MUST respond with ONLY a JSON object. The object must have:
- title (string)
- logline (string)
- tone (string)
- dialogue_enabled (boolean)
- scenes (array of scene objects, each with:)
  - scene_id (string, e.g. "S1", "S2")
  - scene_title (string)
  - scene_number (number)
  - location_id (string, placeholder like "LOC_1")
  - characters_present (array of character name strings)
  - synopsis (string, what happens in this scene)
  - emotional_tone (string)
  - dialogue (array of dialogue lines, each with speaker and text; empty array if dialogue_enabled is false)
  - estimated_shots (number, 1-5)

IMPORTANT CONSTRAINTS — MUST BE EXACT:
- Total estimated_shots across ALL scenes MUST BE EXACTLY {shot_count}
- Number of UNIQUE character names across all scenes MUST BE EXACTLY {char_count}
- Number of UNIQUE location_ids across all scenes MUST BE EXACTLY {loc_count}
- Number of scenes MUST BE EXACTLY {scene_count}
- Intelligently allocate shots based on emotional pacing, genre, and story complexity
- The sum of all scene estimated_shots must equal {shot_count} — count carefully"""

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

            self.memory.screenplay = data
            self.set_progress(100, "Screenplay generated!", f"{len(data.get('scenes', []))} scenes")
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
            if esc > self.memory.selected_scenes:
                issues.append(f"Idea {i+1}: estimated_scene_count {esc} > {self.memory.selected_scenes}")
            ets = idea.get("estimated_total_shots", 0)
            if ets > self.memory.selected_total_shots:
                issues.append(f"Idea {i+1}: estimated_total_shots {ets} > {self.memory.selected_total_shots}")
        return (len(issues) == 0, issues)

    def _validate_screenplay_counts(self, data: Dict) -> Tuple[bool, List[str]]:
        """Validate that screenplay respects exact count constraints."""
        issues = []
        scenes = data.get("scenes", [])
        if len(scenes) != self.memory.selected_scenes:
            issues.append(f"Scenes: got {len(scenes)}, expected {self.memory.selected_scenes}")
        total_shots = sum(s.get("estimated_shots", 0) for s in scenes)
        if total_shots != self.memory.selected_total_shots:
            issues.append(f"Total shots: got {total_shots}, expected {self.memory.selected_total_shots}")
        unique_chars = set()
        for s in scenes:
            for c in s.get("characters_present", []):
                unique_chars.add(c)
        if len(unique_chars) != self.memory.selected_characters:
            issues.append(f"Unique characters: got {len(unique_chars)}, expected {self.memory.selected_characters}")
        unique_locs = set()
        for s in scenes:
            lid = s.get("location_id", "")
            if lid:
                unique_locs.add(lid)
        if len(unique_locs) != self.memory.selected_locations:
            issues.append(f"Unique locations: got {len(unique_locs)}, expected {self.memory.selected_locations}")
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
        """Stage 3: Generate reusable location assets from screenplay."""
        screenplay = self.memory.screenplay
        if not screenplay:
            return {"success": False, "error": "No screenplay generated yet"}

        scenes = screenplay.get("scenes", [])
        unique_loc_ids = list(dict.fromkeys(s.get("location_id", "") for s in scenes if s.get("location_id")))
        count = min(len(unique_loc_ids), self.memory.selected_locations)

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
            return {"success": True, "locations": result["data"], "raw": result.get("raw")}

        return result

    def generate_characters(self, params: Dict = None) -> Dict:
        """Stage 4: Generate reusable character assets from screenplay."""
        screenplay = self.memory.screenplay
        if not screenplay:
            return {"success": False, "error": "No screenplay generated yet"}

        scenes = screenplay.get("scenes", [])
        unique_names = list(dict.fromkeys(
            name for s in scenes for name in s.get("characters_present", [])
        ))
        count = min(len(unique_names), self.memory.selected_characters)

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
            return {"success": True, "characters": result["data"], "raw": result.get("raw")}

        return result

    def generate_storyboard(self, params: Dict = None) -> Dict:
        """Stage 5: Generate storyboard with shots for each scene."""
        screenplay = self.memory.screenplay
        if not screenplay:
            return {"success": False, "error": "No screenplay generated yet"}

        scenes = screenplay.get("scenes", [])
        if not scenes:
            return {"success": False, "error": "No scenes in screenplay"}

        continuity = self.get_continuity_context()

        prompt_parts = [
            "Generate a storyboard as a JSON array based on the screenplay and existing production assets.",
            "",
            "=== CONTINUITY ASSETS ===",
            continuity,
            "",
            "For each scene in the screenplay, determine the shot breakdown and generate image prompts.",
            "Each storyboard entry inherits character and location continuity.",
        ]

        system_suffix = """You MUST respond with ONLY a JSON array. Each element corresponds to one screenplay scene and must have:
- scene_id (string, matching screenplay)
- scene_title (string)
- location_reference (string, the location_id used)
- character_references (array of character_id strings)
- emotional_state (string)
- scene_action (string, what happens)
- shot_count (number)
- shots (array of shot objects, each with:)
  - shot_number (number)
  - camera_direction (string, e.g. Medium Wide Shot, Close-Up, Over-the-Shoulder)
  - lens_suggestion (string)
  - lighting (string)
  - atmosphere (string)
  - cinematic_composition (string)
  - image_prompt (string, optimized for Flux2 Klein Image-to-Image - MUST reference established character appearance and location without redesigning them)

IMPORTANT:
- Do NOT redesign characters or locations
- Total shots across ALL scenes in the storyboard MUST NOT exceed the screenplay's estimated total
- Each shot prompt must feel like a specific cinematic moment, not a generic character/location description"""

        result = self._call_llm("\n".join(prompt_parts), system_suffix=system_suffix)

        if result.get("success") and result.get("data") and isinstance(result["data"], list):
            self.memory.storyboard = result["data"]
            return {"success": True, "storyboard": result["data"], "raw": result.get("raw")}

        return result

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

    def reset(self):
        """Reset the orchestrator memory."""
        self.memory.reset()

    def to_dict(self) -> Dict:
        return self.memory.to_dict()

    def from_dict(self, data: Dict):
        self.memory.from_dict(data)
