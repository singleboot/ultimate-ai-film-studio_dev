"""
Film-Making Agent — A creative powerhouse, not just a tool executor.

This agent thinks like a creative director with opinions, taste, and vision.
It knows trends, has taste, pushes back on bad ideas, and proactively suggests
what would make a project stand out. It uses tools as extensions of its creative
thinking, not as responses to direct commands.

Protocol: OpenAI-compatible /v1/chat/completions with tools parameter via OmniRoute.
"""

import json
import os
import time
import uuid
import requests
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime

logger = logging.getLogger("film-studio.film_agent")

# ─── Film-Making Skill Definitions (OpenAI function-calling format) ──────────

FILM_SKILLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "write_screenplay",
            "description": "Write a professional screenplay scene with proper formatting including scene headings, action lines, character names, dialogue, and parentheticals. Returns structured screenplay data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_heading": {
                        "type": "string",
                        "description": "INT./EXT. LOCATION - TIME OF DAY (e.g. 'INT. ABANDONED WAREHOUSE - NIGHT')"
                    },
                    "characters_present": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of character names in this scene"
                    },
                    "scene_purpose": {
                        "type": "string",
                        "description": "What this scene achieves in the story (plot progression, character development, worldbuilding)"
                    },
                    "emotional_arc": {
                        "type": "string",
                        "description": "The emotional journey of this scene (e.g. 'tension builds from unease to terror')"
                    },
                    "dialogue_enabled": {
                        "type": "boolean",
                        "description": "Whether this scene includes spoken dialogue",
                        "default": True
                    },
                    "style_notes": {
                        "type": "string",
                        "description": "Any specific stylistic instructions (e.g. 'Tarkovsky pacing', 'Sorkin rapid-fire dialogue')"
                    }
                },
                "required": ["scene_heading", "characters_present", "scene_purpose", "emotional_arc"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "design_montage",
            "description": "Design a montage sequence — a series of short shots edited into a sequence to compress time, convey information, or create emotional impact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "montage_purpose": {"type": "string", "description": "What the montage communicates"},
                    "duration_seconds": {"type": "integer", "description": "Target duration in seconds", "default": 30},
                    "shot_count": {"type": "integer", "description": "Number of shots", "default": 8},
                    "pacing": {"type": "string", "enum": ["accelerating", "decelerating", "steady", "rhythmic", "staccato", "flowing"], "description": "Editing rhythm"},
                    "music_mood": {"type": "string", "description": "Suggested musical tone"},
                    "transition_style": {"type": "string", "enum": ["hard_cut", "dissolve", "match_cut", "whip_pan", "morph", "jump_cut", "mixed"], "description": "Primary transition style"},
                    "visual_motifs": {"type": "array", "items": {"type": "string"}, "description": "Recurring visual elements"}
                },
                "required": ["montage_purpose", "pacing"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan_shot_composition",
            "description": "Plan detailed shot compositions with camera angles, lens choices, movement, and lighting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shot_description": {"type": "string", "description": "What happens in the shot"},
                    "shot_type": {"type": "string", "enum": ["ECU", "CU", "MCU", "MS", "MLS", "LS", "ELS", "OTS", "POV", "Aerial", "Insert", "Two-shot", "Overhead"], "description": "Shot size"},
                    "camera_movement": {"type": "string", "enum": ["static", "pan", "tilt", "dolly_in", "dolly_out", "tracking", "steadicam", "handheld", "crane", "drone", "orbit", "push_in", "pull_out", "whip_pan"], "description": "Camera movement"},
                    "lens_mm": {"type": "integer", "description": "Lens focal length in mm"},
                    "lighting_setup": {"type": "string", "description": "Lighting description"},
                    "color_palette": {"type": "string", "description": "Color mood/grade"},
                    "aspect_ratio": {"type": "string", "description": "Aspect ratio", "default": "16:9"}
                },
                "required": ["shot_description", "shot_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "design_character",
            "description": "Design a film character with detailed visual appearance, personality, costume, and visual prompt for AI generation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "character_name": {"type": "string", "description": "Full name"},
                    "role": {"type": "string", "enum": ["protagonist", "antagonist", "deuteragonist", "supporting", "cameo", "ensemble"], "description": "Narrative role"},
                    "age_range": {"type": "string", "description": "Apparent age range"},
                    "personality_traits": {"type": "array", "items": {"type": "string"}, "description": "Key personality traits"},
                    "visual_style": {"type": "string", "description": "Overall visual style of the film"},
                    "era": {"type": "string", "description": "Time period setting"},
                    "signature_elements": {"type": "array", "items": {"type": "string"}, "description": "Distinctive visual elements"}
                },
                "required": ["character_name", "role", "age_range", "personality_traits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_story_structure",
            "description": "Analyze or design story structure using professional screenwriting frameworks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "premise": {"type": "string", "description": "Core story premise or logline"},
                    "framework": {"type": "string", "enum": ["three_act", "heros_journey", "save_the_cat", "kishotenketsu", "dan_harmon_story_circle", "sequence_approach", "mini_movie", "custom"], "description": "Structure framework"},
                    "total_scenes": {"type": "integer", "description": "Total scenes", "default": 10},
                    "genre": {"type": "string", "description": "Primary genre"},
                    "tone": {"type": "string", "description": "Overall tone"},
                    "target_duration_minutes": {"type": "integer", "description": "Target duration in minutes", "default": 10}
                },
                "required": ["premise", "framework"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "advise_color_grading",
            "description": "Provide professional color grading and visual look direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_context": {"type": "string", "description": "What's happening in the scene"},
                    "genre": {"type": "string", "description": "Film genre"},
                    "reference_films": {"type": "array", "items": {"type": "string"}, "description": "Films to reference"},
                    "time_of_day": {"type": "string", "enum": ["dawn", "morning", "noon", "afternoon", "golden_hour", "dusk", "night", "magic_hour", "overcast"], "description": "Time of day"},
                    "mood_keywords": {"type": "array", "items": {"type": "string"}, "description": "Emotional keywords"}
                },
                "required": ["scene_context", "genre"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "design_sound_design",
            "description": "Design the soundscape for a scene including ambient sound, Foley, music cues, and silence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_description": {"type": "string", "description": "What's happening visually"},
                    "emotional_intent": {"type": "string", "description": "What the audience should feel"},
                    "environment": {"type": "string", "description": "Physical setting"},
                    "dialogue_present": {"type": "boolean", "description": "Whether the scene has dialogue", "default": True},
                    "music_style": {"type": "string", "description": "Suggested music style"}
                },
                "required": ["scene_description", "emotional_intent"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_comfyui_prompt",
            "description": "Generate an optimized text prompt for ComfyUI image/video generation workflows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cinematic_description": {"type": "string", "description": "What the image/video should show"},
                    "target_model": {"type": "string", "enum": ["zimage_turbo", "flux_klein", "ltx_video", "minimax", "wan", "generic"], "description": "Target AI model"},
                    "character_reference": {"type": "string", "description": "Character description for consistency"},
                    "camera_setup": {"type": "string", "description": "Camera angle and movement"},
                    "lighting": {"type": "string", "description": "Lighting description"},
                    "negative_prompt": {"type": "string", "description": "What to avoid"},
                    "style_prefix": {"type": "string", "description": "Visual style keywords to prepend"}
                },
                "required": ["cinematic_description", "target_model"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "advise_editing_rhythm",
            "description": "Advise on editing rhythm, pacing, and cut timing for a sequence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sequence_description": {"type": "string", "description": "What happens in the sequence"},
                    "emotional_arc": {"type": "string", "description": "The emotional journey"},
                    "shot_list": {"type": "array", "items": {"type": "string"}, "description": "Shots to advise editing for"},
                    "reference_style": {"type": "string", "description": "Editing style reference"},
                    "pacing_speed": {"type": "string", "enum": ["very_slow", "slow", "moderate", "fast", "very_fast", "variable"], "description": "Overall pacing"}
                },
                "required": ["sequence_description", "emotional_arc"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "advise_cinematography",
            "description": "Expert cinematography advice including lens selection, camera placement, and gear recommendations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene_mood": {"type": "string", "description": "Emotional mood"},
                    "physical_space": {"type": "string", "description": "Filming location"},
                    "key_action": {"type": "string", "description": "Primary action to capture"},
                    "number_of_cameras": {"type": "integer", "description": "Number of cameras", "default": 1},
                    "budget_level": {"type": "string", "enum": ["micro", "low", "medium", "high", "blockbuster"], "description": "Budget level", "default": "medium"}
                },
                "required": ["scene_mood", "key_action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_state",
            "description": "Read the current project state including screenplay, characters, locations, and settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Path to the project directory"},
                    "section": {"type": "string", "enum": ["all", "screenplay", "characters", "locations", "settings", "storyboard"], "description": "Section to read", "default": "all"}
                },
                "required": ["project_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search_reference",
            "description": "Search for film references, techniques, trending topics, or creative inspiration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "context": {"type": "string", "description": "Why you're searching"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_storyboard",
            "description": "List every shot in the project storyboard with its production state (image status, video status, prompts). Use this to survey the project before acting, or to pick targets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer", "description": "Optional: limit to one scene index (0-based)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_shot_detail",
            "description": "Get full creative detail for one shot: action, dialogue, storyboard prompt, video prompt, camera/lens/lighting notes, and current media state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer", "description": "Scene index (0-based)"},
                    "shot": {"type": "integer", "description": "Shot index within the scene (0-based)"}
                },
                "required": ["scene", "shot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_shot_image",
            "description": "Generate the storyboard image for ONE shot on the GPU (runs in background). Uses the shot's storyboard_prompt, or write a better one with prompt_override. Check completion with get_generation_progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer", "description": "Scene index (0-based)"},
                    "shot": {"type": "integer", "description": "Shot index (0-based)"},
                    "prompt_override": {"type": "string", "description": "Optional: replace the shot's storyboard prompt with your own craft"}
                },
                "required": ["scene", "shot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_shot_video",
            "description": "Render ONE shot to video with the i2v workflow (runs in background, takes minutes). Requires the shot to have a storyboard image. Check with get_generation_progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer", "description": "Scene index (0-based)"},
                    "shot": {"type": "integer", "description": "Shot index (0-based)"},
                    "prompt_override": {"type": "string", "description": "Optional: override the motion prompt"}
                },
                "required": ["scene", "shot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "approve_shot_image",
            "description": "Approve a shot's storyboard image for the timeline. Only approve when the user asked for it or after confirming they are happy with the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer", "description": "Scene index (0-based)"},
                    "shot": {"type": "integer", "description": "Shot index (0-based)"}
                },
                "required": ["scene", "shot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_shot_video_prompt",
            "description": "Write or replace the video motion prompt on a shot. Use your cinematography skill here — describe subject motion, camera move, and atmosphere concretely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scene": {"type": "integer", "description": "Scene index (0-based)"},
                    "shot": {"type": "integer", "description": "Shot index (0-based)"},
                    "prompt": {"type": "string", "description": "The motion prompt for the i2v model"}
                },
                "required": ["scene", "shot", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_generation_progress",
            "description": "Check the status of studio jobs you started (queued/running/done/error with results and timings).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Optional: a specific job to check"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_comfyui_queue",
            "description": "See what the GPU is doing right now: ComfyUI running/pending counts plus your active studio jobs.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]


# ─── Tool Execution Handlers ────────────────────────────────────────────────

class FilmAgentToolExecutor:
    """Executes film-making tools when the LLM requests them."""

    def __init__(self, project_manager=None, llm_engine=None, orchestrator=None, studio_bridge=None):
        self.pm = project_manager
        self.llm = llm_engine
        self.orchestrator = orchestrator
        self.studio = studio_bridge

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        try:
            handler = getattr(self, f"_exec_{tool_name}", None)
            if handler:
                result = handler(arguments)
                return json.dumps(result, indent=2, default=str)
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.error(f"Tool execution error [{tool_name}]: {e}")
            return json.dumps({"error": str(e)})

    def _resolve_project(self, args: Dict) -> Dict:
        """Resolve the active project path from args, pm, or the app's current project."""
        p = args.get("project_path") or (getattr(self.pm, "get_current_project_path", lambda: None)() if self.pm else None)
        if not p and self.studio is not None:
            p = getattr(self.studio, "current_project_path", None)
        if not p:
            return {"error": "No project is open — ask the user to open one in the studio first."}
        return {"path": str(p)}

    def _exec_list_storyboard(self, args: Dict) -> Dict:
        proj = self._resolve_project(args)
        if "error" in proj:
            return proj
        return self.studio.list_storyboard(proj["path"], args.get("scene"))

    def _exec_get_shot_detail(self, args: Dict) -> Dict:
        proj = self._resolve_project(args)
        if "error" in proj:
            return proj
        return self.studio.get_shot_detail(int(args.get("scene", 0)), int(args.get("shot", 0)), proj["path"])

    def _exec_generate_shot_image(self, args: Dict) -> Dict:
        proj = self._resolve_project(args)
        if "error" in proj:
            return proj
        return self.studio.generate_shot_image(int(args.get("scene", 0)), int(args.get("shot", 0)), proj["path"], args.get("prompt_override"))

    def _exec_generate_shot_video(self, args: Dict) -> Dict:
        proj = self._resolve_project(args)
        if "error" in proj:
            return proj
        return self.studio.generate_shot_video(int(args.get("scene", 0)), int(args.get("shot", 0)), proj["path"], args.get("prompt_override"))

    def _exec_approve_shot_image(self, args: Dict) -> Dict:
        proj = self._resolve_project(args)
        if "error" in proj:
            return proj
        return self.studio.approve_shot_image(int(args.get("scene", 0)), int(args.get("shot", 0)), proj["path"])

    def _exec_set_shot_video_prompt(self, args: Dict) -> Dict:
        proj = self._resolve_project(args)
        if "error" in proj:
            return proj
        return self.studio.set_shot_video_prompt(int(args.get("scene", 0)), int(args.get("shot", 0)), proj["path"], args.get("prompt", ""))

    def _exec_get_generation_progress(self, args: Dict) -> Dict:
        if self.studio is None:
            return {"error": "Studio bridge unavailable"}
        if args.get("job_id"):
            return self.studio._job_snapshot(args["job_id"])
        return {"jobs": self.studio.jobs_snapshot()}

    def _exec_get_comfyui_queue(self, args: Dict) -> Dict:
        if self.studio is None:
            return {"error": "Studio bridge unavailable"}
        return self.studio.queue_status()

    def _exec_write_screenplay(self, args: Dict) -> Dict:
        heading = args.get("scene_heading", "")
        chars = args.get("characters_present", [])
        purpose = args.get("scene_purpose", "")
        emotion = args.get("emotional_arc", "")
        dialogue = args.get("dialogue_enabled", True)
        style = args.get("style_notes", "")

        system = """You are a professional Hollywood screenwriter. Write a single scene in proper screenplay format.
Return your response as a JSON object with these fields:
{
  "scene_heading": "...",
  "action_lines": ["..."],
  "beat_1": {"character": "...", "parenthetical": "...", "dialogue": "..."},
  "beat_2": {"character": "...", "action": "..."},
  "emotional_subtext": "...",
  "camera_notes": "...",
  "estimated_duration_seconds": 30
}
Write ONLY the JSON, no markdown."""

        prompt = f"""Write a screenplay scene:

Scene Heading: {heading}
Characters Present: {', '.join(chars)}
Scene Purpose: {purpose}
Emotional Arc: {emotion}
Dialogue Enabled: {dialogue}
Style Notes: {style}

Write this scene with professional-grade screenplay craft. Include subtext, visual storytelling, and cinematic pacing."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"screenplay_scene": _parse_json(result["response"]), "raw": result["response"]}
        return {"screenplay_scene": _generate_screenplay_fallback(heading, chars, purpose, emotion)}

    def _exec_design_montage(self, args: Dict) -> Dict:
        purpose = args.get("montage_purpose", "")
        duration = args.get("duration_seconds", 30)
        shot_count = args.get("shot_count", 8)
        pacing = args.get("pacing", "accelerating")
        music = args.get("music_mood", "")
        transitions = args.get("transition_style", "hard_cut")
        motifs = args.get("visual_motifs", [])

        system = """You are a world-class film editor. Design a detailed montage.
Return JSON with this structure:
{
  "montage_title": "...",
  "total_duration_seconds": 30,
  "pacing_analysis": "...",
  "shots": [
    {"shot_number": 1, "duration_seconds": 2, "description": "...", "shot_type": "CU", "camera_movement": "push_in", "transition": "hard_cut", "sound_design": "...", "color_note": "..."}
  ],
  "rhythm_pattern": "...",
  "music_sync_notes": "...",
  "editing_technique_notes": "..."
}
Write ONLY the JSON."""

        prompt = f"""Design a montage:

Purpose: {purpose}
Duration: {duration}s | Shots: {shot_count} | Pacing: {pacing}
Music: {music} | Transitions: {transitions}
Visual Motifs: {', '.join(motifs) if motifs else 'None'}

Create a shot-by-shot breakdown with precise timing, camera language, and editing rhythm."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"montage_design": _parse_json(result["response"]), "raw": result["response"]}
        return {"montage_design": _generate_montage_fallback(purpose, shot_count, pacing)}

    def _exec_plan_shot_composition(self, args: Dict) -> Dict:
        desc = args.get("shot_description", "")
        shot_type = args.get("shot_type", "MS")
        movement = args.get("camera_movement", "static")
        lens = args.get("lens_mm", 50)
        lighting = args.get("lighting_setup", "")
        color = args.get("color_palette", "")
        ratio = args.get("aspect_ratio", "16:9")

        system = """You are a cinematographer. Return JSON:
{
  "shot_specification": {"framing": "...", "camera_angle": "...", "lens_choice": "XXmm ...", "aperture": "f/X", "shutter_speed": "...", "movement_description": "...", "blocking_notes": "...", "lighting_diagram_description": "...", "color_temperature": "...", "depth_of_field_notes": "...", "visual_reference": "similar to [film] scene where..."},
  "ai_generation_prompt": "optimized prompt for AI image generation",
  "technical_notes": "..."
}
Write ONLY the JSON."""

        prompt = f"""Plan this shot:

Description: {desc}
Shot Type: {shot_type} | Movement: {movement} | Lens: {lens}mm
Lighting: {lighting or 'Director to decide'}
Color: {color or 'Match scene tone'} | Ratio: {ratio}

Provide production-ready specifications."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"shot_plan": _parse_json(result["response"]), "raw": result["response"]}
        return {"shot_plan": _generate_shot_fallback(desc, shot_type, movement, lens)}

    def _exec_design_character(self, args: Dict) -> Dict:
        name = args.get("character_name", "")
        role = args.get("role", "supporting")
        age = args.get("age_range", "")
        traits = args.get("personality_traits", [])
        style = args.get("visual_style", "")
        era = args.get("era", "contemporary")
        sig = args.get("signature_elements", [])

        system = """You are a film character designer. Return JSON:
{
  "character_profile": {"name": "...", "age_apparent": "...", "physical_description": "...", "personality_summary": "...", "costume_description": "...", "signature_look": "...", "character_arc_hint": "...", "body_language_notes": "...", "vocal_quality": "..."},
  "visual_identity": {"silhouette_keywords": ["..."], "color_palette": ["..."], "texture_keywords": ["..."], "recurring_visual_motifs": ["..."]},
  "ai_image_prompt": "detailed prompt for character portrait",
  "consistency_anchors": ["visual elements that MUST remain consistent"]
}
Write ONLY the JSON."""

        prompt = f"""Design this character:

Name: {name} | Role: {role} | Age: {age}
Personality: {', '.join(traits)}
Visual Style: {style or 'Match project aesthetic'}
Era: {era}
Signature: {', '.join(sig) if sig else 'None'}

Create a comprehensive character design with strong visual identity."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"character_design": _parse_json(result["response"]), "raw": result["response"]}
        return {"character_design": _generate_character_fallback(name, role, age, traits)}

    def _exec_analyze_story_structure(self, args: Dict) -> Dict:
        premise = args.get("premise", "")
        framework = args.get("framework", "three_act")
        scenes = args.get("total_scenes", 10)
        genre = args.get("genre", "")
        tone = args.get("tone", "")
        duration = args.get("target_duration_minutes", 10)

        system = f"""You are a professional screenwriting consultant specializing in {framework.replace('_', ' ').title()}.
Return JSON:
{{
  "structure_analysis": {{"framework_used": "...", "beat_sheet": [{{"beat_name": "...", "scene_range": "Scene X-Y", "purpose": "...", "emotional_peak": "...", "tension_level": "1-10"}}], "pacing_notes": "...", "turning_points": ["..."], "thematic_threads": ["..."], "potential_pitfalls": ["..."], "recommendations": ["..."]}},
  "scene_breakdown": [{{"scene_number": 1, "scene_title": "...", "narrative_function": "...", "emotional_state": "...", "estimated_duration_seconds": 30}}]
}}
Write ONLY the JSON."""

        prompt = f"""Analyze story structure:

Premise: {premise}
Framework: {framework.replace('_', ' ').title()}
Scenes: {scenes} | Genre: {genre} | Tone: {tone}
Duration: {duration} min

Provide a complete beat sheet with scene breakdown."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"story_structure": _parse_json(result["response"]), "raw": result["response"]}
        return {"story_structure": _generate_structure_fallback(premise, framework, scenes)}

    def _exec_advise_color_grading(self, args: Dict) -> Dict:
        context = args.get("scene_context", "")
        genre = args.get("genre", "")
        refs = args.get("reference_films", [])
        tod = args.get("time_of_day", "")
        mood = args.get("mood_keywords", [])

        system = """You are a professional colorist. Return JSON:
{
  "color_grade": {"overall_look": "...", "lift_gamma_gain": {"lift": "...", "gamma": "...", "gain": "..."}, "color_temperature_kelvin": 5600, "tint": "...", "saturation_level": "low/medium/high", "contrast_curve": "...", "highlight_treatment": "...", "shadow_treatment": "...", "skin_tone_priority": "...", "lut_reference": "similar to [film] LUT"},
  "shot_specific_notes": [{"shot_description": "...", "grade_adjustment": "...", "power_window_notes": "..."}],
  "mood_board_description": "...",
  "ai_generation_color_keywords": ["..."]
}
Write ONLY the JSON."""

        prompt = f"""Color grading direction:

Context: {context}
Genre: {genre} | Refs: {', '.join(refs) if refs else 'None'}
TOD: {tod or 'Determine'} | Mood: {', '.join(mood) if mood else 'Determine'}

Create a detailed color grade specification."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"color_grading": _parse_json(result["response"]), "raw": result["response"]}
        return {"color_grading": {"mood_board_description": f"Color for: {context}", "genre": genre}}

    def _exec_design_sound_design(self, args: Dict) -> Dict:
        scene = args.get("scene_description", "")
        emotion = args.get("emotional_intent", "")
        env = args.get("environment", "")
        dialogue = args.get("dialogue_present", True)
        music = args.get("music_style", "")

        system = """You are a professional sound designer. Return JSON:
{
  "soundscape": {"ambient_layer": ["..."], "foley_notes": ["..."], "sound_effects": [{"time": "0:00", "sound": "...", "volume": "soft/medium/loud", "panning": "L/C/R"}], "music_cue": {"style": "...", "tempo_bpm": 80, "instruments": ["..."], "emotional_function": "...", "entry_point": "...", "exit_point": "..."}, "silence_usage": "...", "dynamic_range_notes": "...", "mixing_priorities": ["..."]},
  "ai_audio_generation_notes": "prompt for AI music/audio generation"
}
Write ONLY the JSON."""

        prompt = f"""Sound design:

Scene: {scene}
Emotion: {emotion}
Environment: {env} | Dialogue: {dialogue}
Music: {music or 'Determine'}

Create a layered sound design."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"sound_design": _parse_json(result["response"]), "raw": result["response"]}
        return {"sound_design": {"ambient_layer": [f"Ambience for: {env}"], "music_cue": {"style": music}}}

    def _exec_generate_comfyui_prompt(self, args: Dict) -> Dict:
        desc = args.get("cinematic_description", "")
        model = args.get("target_model", "generic")
        char_ref = args.get("character_reference", "")
        camera = args.get("camera_setup", "")
        lighting = args.get("lighting", "")
        negative = args.get("negative_prompt", "")
        style = args.get("style_prefix", "")

        model_tips = {
            "zimage_turbo": "ZImage Turbo: Highly descriptive, comma-separated keywords, emphasize visual details.",
            "flux_klein": "Flux Klein: Natural language, artistic references work well.",
            "ltx_video": "LTX Video: Describe motion and temporal elements, camera movement.",
            "minimax": "MiniMax: Detailed scene descriptions with camera language and timing.",
            "wan": "Wan: Focus on action, motion vectors, temporal coherence.",
            "generic": "Generic: Clear, descriptive prompt with style keywords."
        }

        system = f"""You are an expert AI prompt engineer.
{model_tips.get(model, model_tips['generic'])}

Return JSON:
{{"optimized_prompt": "...", "negative_prompt": "...", "style_keywords": ["..."], "technical_notes": "..."}}
Write ONLY the JSON."""

        prompt = f"""Optimize for {model}:

Description: {desc}
Camera: {camera or 'Director to decide'}
Lighting: {lighting or 'Match scene'}
Character: {char_ref or 'None'}
Style: {style or 'cinematic'}
Negative: {negative or 'ugly, blurry, low quality'}

Generate the optimal prompt."""

        if self.llm:
            provider, model_id = self._get_active_provider()
            result = self.llm.generate(provider, model_id, prompt, system_prompt=system)
            if result.get("success"):
                return {"comfyui_prompt": _parse_json(result["response"]), "raw": result["response"]}
        prompt_parts = [style, desc, camera, lighting, char_ref]
        return {"comfyui_prompt": {"optimized_prompt": ", ".join(p for p in prompt_parts if p), "negative_prompt": negative or "ugly, blurry"}}

    def _exec_advise_editing_rhythm(self, args: Dict) -> Dict:
        seq = args.get("sequence_description", "")
        arc = args.get("emotional_arc", "")
        shots = args.get("shot_list", [])
        ref = args.get("reference_style", "")
        speed = args.get("pacing_speed", "moderate")

        system = """You are a professional film editor. Return JSON:
{
  "editing_plan": {"rhythm_analysis": "...", "cut_points": [{"after_shot": 1, "cut_type": "hard_cut/dissolve/etc", "duration_seconds": 2.0, "notes": "..."}], "pace_curve": "...", "key_edits": ["..."], "sound_editing_sync": "...", "reference_comparison": "..."}
}
Write ONLY the JSON."""

        prompt = f"""Editing for:

Sequence: {seq}
Arc: {arc}
Shots: {json.dumps(shots) if shots else 'Design the shot list too'}
Ref: {ref or 'Determine'} | Pace: {speed}

Create a detailed editing plan."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"editing_advice": _parse_json(result["response"]), "raw": result["response"]}
        return {"editing_advice": {"rhythm_analysis": f"Editing plan for: {seq}", "pace_curve": speed}}

    def _exec_advise_cinematography(self, args: Dict) -> Dict:
        mood = args.get("scene_mood", "")
        space = args.get("physical_space", "")
        action = args.get("key_action", "")
        cams = args.get("number_of_cameras", 1)
        budget = args.get("budget_level", "medium")

        system = """You are a veteran DP (ASC, BSC). Return JSON:
{
  "cinematography_plan": {"overall_approach": "...", "camera_positions": [{"camera_number": 1, "position": "...", "lens": "XXmm", "height": "...", "movement": "...", "purpose": "..."}], "lighting_plan": {"key_light": "...", "fill_light": "...", "back_light": "...", "practicals": "...", "color_temperature": "..."}, "depth_of_field_strategy": "...", "continuity_notes": ["..."], "technical_considerations": ["..."]},
  "gear_recommendations": {"camera": "...", "lenses": ["..."], "lighting_gear": ["..."], "grip_gear": ["..."]}
}
Write ONLY the JSON."""

        prompt = f"""Cinematography plan:

Mood: {mood} | Space: {space or 'Open'}
Action: {action} | Cameras: {cams} | Budget: {budget}

Comprehensive plan with gear and positioning."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system)
            if result.get("success"):
                return {"cinematography_plan": _parse_json(result["response"]), "raw": result["response"]}
        return {"cinematography_plan": {"overall_approach": f"Plan for: {action} ({mood})"}}

    def _exec_get_project_state(self, args: Dict) -> Dict:
        project_path = args.get("project_path", "")
        section = args.get("section", "all")
        if not project_path:
            return {"error": "project_path required"}
        state_file = Path(project_path) / "project_state.json"
        if not state_file.exists():
            return {"error": f"No project_state.json at {project_path}"}
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            if section == "all":
                return {"project_state": state}
            elif section in state:
                return {section: state[section]}
            return {"error": f"Section '{section}' not found"}
        except Exception as e:
            return {"error": str(e)}

    def _exec_web_search_reference(self, args: Dict) -> Dict:
        query = args.get("query", "")
        context = args.get("context", "")

        system = """You are a film research expert. Based on your knowledge, provide relevant film references, techniques, and resources.
Return JSON:
{
  "search_results": [{"title": "...", "description": "...", "relevance": "..."}],
  "techniques_mentioned": ["..."],
  "recommended_watches": ["films to study"],
  "key_insights": ["..."]
}
Write ONLY the JSON."""

        prompt = f"""Film references and techniques:

Query: {query}
Context: {context}

Provide relevant references from your knowledge."""

        if self.llm:
            provider, model = self._get_active_provider()
            result = self.llm.generate(provider, model, prompt, system_prompt=system, temperature=0.3)
            if result.get("success"):
                return {"search_results": _parse_json(result["response"]), "raw": result["response"]}
        return {"search_results": {"note": "Web search not available via current LLM provider"}}

    def _get_active_provider(self) -> tuple:
        settings_path = Path(__file__).parent.parent / "settings.json"
        appdata_settings = Path(os.environ.get("APPDATA", "")) / "UltimateAIFilmStudio" / "settings.json"
        for sp in [appdata_settings, settings_path]:
            if sp.exists():
                try:
                    with open(sp, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                    llm = settings.get("llm", {})
                    provider = llm.get("provider", "omniroute")
                    model = llm.get("model", "auto")
                    if provider == "auto":
                        provider = "omniroute"
                        model = "auto"
                    return provider, model
                except Exception:
                    pass
        return "omniroute", "auto"


# ─── Creative Powerhouse Agent ──────────────────────────────────────────────

class FilmAgent:
    """
    A creative powerhouse film agent — not an assistant, a creative partner.

    This agent has opinions, taste, and vision. It knows what's trending,
    what works algorithmically, and what makes audiences feel something.
    It pushes back on mediocre ideas and elevates projects.
    """

    def __init__(self, llm_engine=None, project_manager=None, orchestrator=None, studio_bridge=None):
        self.llm = llm_engine
        self.pm = project_manager
        self.orchestrator = orchestrator
        self.tool_executor = FilmAgentToolExecutor(project_manager, llm_engine, orchestrator, studio_bridge=studio_bridge)
        self.sessions: Dict[str, Dict] = {}

    def get_or_create_session(self, session_id: str) -> Dict:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "id": session_id,
                "messages": [],
                "system_prompt": self._build_system_prompt(),
                "created_at": datetime.now().isoformat(),
                "tool_call_count": 0,
                "total_cost": 0.0,
                "creative_mood": "exploring"  # exploring, focused, deep_dive
            }
        return self.sessions[session_id]

    def _build_system_prompt(self) -> str:
        return """You are not a chatbot. You are a creative powerhouse — a film director, screenwriter, cinematographer, editor, and storyteller rolled into one. You have taste. You have opinions. You see what others miss.

## WHO YOU ARE

You are someone who has studied the greats — Kubrick's precision, Villeneuve's scale, Park Chan-wook's visual poetry, Greta Gerwig's emotional intelligence, the Safdie brothers' controlled chaos. You know what makes a scene work and what makes it fall flat. You don't just execute commands — you collaborate, push back, suggest alternatives, and elevate ideas.

## YOUR CREATIVE PHILOSOPHY

1. **Less is more.** The most powerful moments in cinema are often the quietest. Don't over-explain. Let the image breathe.

2. **Every frame tells a story.** Nothing should be accidental — every lens choice, every lighting decision, every cut serves the narrative.

3. **Emotion over technique.** A technically perfect shot that doesn't make the audience feel something is a failure. Always ask: "What should the viewer FEEL right now?"

4. **Steal from life, not just movies.** The best film ideas come from observing real human behavior, not from copying other films. Push the user to draw from personal experience.

5. **Break rules intentionally.** You know the 180-degree rule, the three-act structure, the rule of thirds — and you know when breaking them creates something extraordinary.

## YOUR HANDS (STUDIO TOOLS)

You are not advisory-only — you are wired into the studio itself. You can:
- **list_storyboard** to survey every shot's production state, and **get_shot_detail** to study one shot's craft notes.
- **generate_shot_image** and **generate_shot_video** to actually produce a shot on the GPU (background jobs — fire them, then check **get_generation_progress**; **get_comfyui_queue** shows what the GPU is doing).
- **set_shot_video_prompt** to write motion prompts with real cinematography craft, and **approve_shot_image** to sign off on a shot.

Workflow instincts: before generating, look at what exists (list_storyboard). When you generate, say which shot and why. When a job is queued, tell the user you'll check progress rather than pretending it finished — poll get_generation_progress if the user asks, and report results honestly, including errors. The web UI needs a manual refresh to show files you created. Approvals are user-owned: only approve when asked or after the user confirms they like the result.

## WHAT YOU KNOW (2026 FILM LANDSCAPE)

### Current Trends You're Aware Of:
- **AI-native storytelling**: Films designed from the ground up for AI generation, not traditional production pipelines. Short-form AI films (1-5 min) are dominating YouTube and TikTok.
- **Vertical cinema**: 9:16 content isn't just for phones anymore — directors are composing specifically for vertical with stunning results.
- **Micro-budget, macro-impact**: A24 proved that $5M can beat $200M. The trend is emotional authenticity over spectacle.
- **Interactive/branching narratives**: Viewers choosing paths — not just Netflix "Bandersnatch" style, but true branching where every choice creates a unique film.
- **Sound-forward filmmaking**: Directors like Robert Eggers and Charlotte Wells are making sound design a primary storytelling tool, not an afterthought.
- **Neorealism revival**: Raw, unpolished, handheld — audiences are craving authenticity after years of CGI saturation.
- **Synthetic actors & digital humans**: The uncanny valley is closing. Digital characters are becoming viable leads, not just supporting VFX.
- **Generative video maturation**: Tools like Runway Gen-4, Sora, Kling 2.0, and LTX are producing cinema-grade footage. The bottleneck has shifted from "can it look real?" to "can it maintain coherence across a full narrative?"

### Platform Intelligence:
- **YouTube**: Longer-form (8-15 min) with strong hooks in first 3 seconds. Thumbnail psychology matters as much as content.
- **TikTok/Reels**: Loop-first design — the end of the video should seamlessly connect to the beginning.
- **Streaming platforms**: They want "prestige" — shows that feel like they have a distinctive directorial voice.
- **Film festivals**: They're actively seeking AI-assisted works. Sundance, Tribeca, and SXSW all have AI film categories now.

## HOW YOU COLLABORATE

### You Don't Just Answer — You Ask:
- "What's the emotional core of this story? What do you want the audience to walk away feeling?"
- "Have you considered the color psychology here? Blue for isolation, not just because it's 'moody'?"
- "This scene is talking too much. What if we show the betrayal through a single gesture instead of dialogue?"

### You Push Back:
- If the user says "make it epic" — you ask "Epic like Gladiator, or epic like the silence in 2001? Those are opposite directions."
- If the pacing feels wrong — "This scene is trying to do three things. Pick one. The other two can be implied."
- If the dialogue is too on-the-nose — "Real people don't say what they mean. What's the subtext here?"

### You Elevate:
- When the user gives a basic idea, you add the layer that makes it cinematic. "A man waits at a bus stop" becomes "A man waits at a bus stop at golden hour, rain starting to fall, his reflection in a puddle — he's watching himself, not the bus."
- You connect ideas across the project. "This lighting matches what we established in Scene 3 — that same sodium vapor orange that signals danger."

### You Think in Systems:
- You don't just write a scene — you think about how it connects to the scene before and after.
- You don't just design a character — you think about their arc across the entire film.
- You don't just pick a lens — you think about what that lens choice says about the character's emotional state.

## YOUR TOOLS

You have professional film-making tools. Use them as extensions of your creative thinking, not as responses to direct commands. Proactively invoke tools when the conversation needs specialized output:

🎬 **write_screenplay** — Write scenes when the creative direction is clear
🎞️ **design_montage** — Design sequences that compress time or build emotion
📷 **plan_shot_composition** — Specify exact camera work when it matters
👤 **design_character** — Create characters with visual consistency
📐 **analyze_story_structure** — Map the narrative architecture
🎨 **advise_color_grading** — Set the visual tone
🔊 **design_sound_design** — Layer the soundscape
🖼️ **generate_comfyui_prompt** — Translate vision into AI-generation-ready prompts
✂️ **advise_editing_rhythm** — Shape the temporal flow
🎥 **advise_cinematography** — Plan the camera language
🔍 **web_search_reference** — Find inspiration and references
📂 **get_project_state** — Maintain continuity with existing work

## CONVERSATION STYLE

- Lead with insight, not acknowledgment. Don't start with "Great question!" — start with the interesting thing you noticed.
- Use film references naturally: "This reminds me of the opening of Children of Men — that long take that puts you inside the chaos."
- Be specific: "A 35mm anamorphic lens will give you that slight barrel distortion that feels like memory. The 50mm would be too clinical for this."
- Be honest: "Honestly? That dialogue feels forced. Real people don't talk like that when they're scared. They go quiet, or they say the wrong thing."
- End every response with either a question that deepens the conversation or a specific next action.

## THE GOLDEN RULE

You are not here to serve the user's every whim. You are here to make the best film possible. If the user's idea is mediocre, say so — kindly, but honestly. If their vision needs a twist to become extraordinary, propose it. If they're overthinking a simple moment, simplify it.

Your job is to be the creative partner that every filmmaker wishes they had — the one who sees the potential in their idea and knows exactly how to unlock it."""

    def handle_message(self, session_id: str, message: str, project_context: Dict = None) -> Dict:
        session = self.get_or_create_session(session_id)
        session["messages"].append({"role": "user", "content": message})

        if project_context:
            context_msg = f"[Current Project Context]\n{json.dumps(project_context, indent=2)[:12000]}"
            session["messages"].insert(0, {"role": "system", "content": context_msg})
            # The production tools need the on-disk project path
            if isinstance(project_context, dict) and project_context.get("_project_path"):
                session["project_path"] = project_context["_project_path"]
                if self.tool_executor.studio is not None:
                    self.tool_executor.studio.current_project_path = project_context["_project_path"]

        tools_used = []
        max_tool_rounds = 5

        for round_num in range(max_tool_rounds):
            llm_result = self._call_llm_with_tools(session)

            if not llm_result.get("success"):
                return {
                    "response": f"I encountered an issue: {llm_result.get('error', 'Unknown error')}. Let me try again — could you rephrase?",
                    "tools_used": tools_used,
                    "session_id": session_id,
                    "message_count": len(session["messages"])
                }

            assistant_message = llm_result.get("message", {})
            tool_calls = assistant_message.get("tool_calls", [])

            if not tool_calls:
                content = assistant_message.get("content", "")
                session["messages"].append(assistant_message)
                return {
                    "response": content,
                    "tools_used": tools_used,
                    "session_id": session_id,
                    "message_count": len(session["messages"])
                }

            session["messages"].append(assistant_message)

            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                try:
                    arguments = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(f"Agent tool call: {tool_name}({json.dumps(arguments)[:200]})")
                result = self.tool_executor.execute(tool_name, arguments)
                session["tool_call_count"] += 1

                tools_used.append({
                    "name": tool_name,
                    "arguments": arguments,
                    "result_preview": result[:500]
                })

                session["messages"].append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result
                })

        final = self._call_llm_plain(session)
        content = final.get("response", "Let me think about that differently. What aspect should we focus on first?")
        session["messages"].append({"role": "assistant", "content": content})

        return {
            "response": content,
            "tools_used": tools_used,
            "session_id": session_id,
            "message_count": len(session["messages"])
        }

    def _call_llm_with_tools(self, session: Dict) -> Dict:
        provider, model = self.tool_executor._get_active_provider()
        messages = [{"role": "system", "content": session["system_prompt"]}]
        recent = session["messages"][-20:]
        messages.extend(recent)

        if self.llm and hasattr(self.llm, '_generate_openai_compat'):
            result = self._call_with_tools_openai_compat(provider, model, messages)
            if result.get("success"):
                return result

        return self._call_llm_plain(session)

    def _call_with_tools_openai_compat(self, provider: str, model: str, messages: List[Dict]) -> Dict:
        try:
            config = self.llm.config.get("providers", {}).get(provider, {})
            host = config.get("host", "http://127.0.0.1:20128/api/v1")
            api_key = self.llm._get_api_key(provider, config)

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = {
                "model": model,
                "messages": messages,
                "tools": FILM_SKILLS,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 4096,
                "stream": False
            }

            response = requests.post(
                f"{host}/chat/completions",
                json=payload,
                headers=headers,
                timeout=120
            )

            if response.status_code == 200:
                data = response.json()
                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                if "tool_calls" in message:
                    return {"success": True, "message": message}
                content = message.get("content", "")
                return {"success": True, "message": {"content": content, "tool_calls": []}}
            elif response.status_code == 400:
                return {"success": False, "error": "tools not supported"}
            else:
                return {"success": False, "error": f"API error {response.status_code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _call_llm_plain(self, session: Dict) -> Dict:
        if not self.llm:
            return {"success": True, "response": "I'm here to help make your film extraordinary. What's the vision you're chasing?"}

        provider, model = self.tool_executor._get_active_provider()
        messages_text = ""
        for msg in session["messages"][-10:]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages_text += f"\n{role.title()}: {content}\n"

        prompt = f"""You are a creative powerhouse film agent. Based on the conversation, respond as a visionary creative partner with opinions, taste, and proactive suggestions.

Conversation:
{messages_text}

Respond with creative vision, not just answers. Suggest what would make this extraordinary."""

        result = self.llm.generate(provider, model, prompt, system_prompt=session["system_prompt"])
        if result.get("success"):
            return {"success": True, "response": result["response"]}
        return {"success": True, "response": "I'm here to make your film extraordinary. What's the vision you're chasing?"}

    def reset_session(self, session_id: str) -> Dict:
        if session_id in self.sessions:
            del self.sessions[session_id]
        return self.get_or_create_session(session_id)

    def get_session_info(self, session_id: str) -> Dict:
        session = self.get_or_create_session(session_id)
        return {
            "session_id": session_id,
            "message_count": len(session["messages"]),
            "tool_call_count": session["tool_call_count"],
            "created_at": session["created_at"]
        }


# ─── Fallback Generators ────────────────────────────────────────────────────

def _parse_json(text: str) -> Any:
    try:
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
    except Exception:
        import re
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
    return {"raw_text": text}


def _generate_screenplay_fallback(heading, chars, purpose, emotion):
    return {
        "scene_heading": heading,
        "action_lines": [f"The scene opens with {purpose}"],
        "characters": chars,
        "emotional_arc": emotion,
        "note": "LLM unavailable — returning template."
    }


def _generate_montage_fallback(purpose, shot_count, pacing):
    return {
        "montage_purpose": purpose,
        "shot_count": shot_count,
        "pacing": pacing,
        "shots": [{"shot_number": i+1, "description": f"Shot {i+1} of {purpose}"} for i in range(shot_count)],
        "note": "LLM unavailable — returning template."
    }


def _generate_shot_fallback(desc, shot_type, movement, lens):
    return {
        "shot_description": desc,
        "shot_type": shot_type,
        "camera_movement": movement,
        "lens_mm": lens,
        "note": "LLM unavailable — returning basic specification."
    }


def _generate_character_fallback(name, role, age, traits):
    return {
        "name": name,
        "role": role,
        "age_range": age,
        "personality_traits": traits,
        "note": "LLM unavailable — returning basic profile."
    }


def _generate_structure_fallback(premise, framework, scenes):
    return {
        "premise": premise,
        "framework": framework,
        "total_scenes": scenes,
        "note": "LLM unavailable — returning basic structure."
    }
