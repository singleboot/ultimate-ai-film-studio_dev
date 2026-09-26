"""LLM agents — named prompt-engineering roles used to refine generation prompts
before they are dispatched to ComfyUI workflows.

Each agent wraps a system prompt and calls the shared LLMEngine, so it works with
whatever provider/model the user has configured (Ollama, llama.cpp, OpenAI, ...).
"""

import json
from pathlib import Path
from typing import Dict, List, Optional


class LLMAgent:
    def __init__(self, agent_id: str, name: str, description: str, system_prompt: str):
        self.agent_id = agent_id
        self.name = name
        self.description = description
        self.system_prompt = system_prompt

    def to_dict(self) -> Dict:
        return {
            "id": self.agent_id,
            "name": self.name,
            "description": self.description,
        }


# System prompt for the text-to-image prompt engineer. Mirrors the LLM agent
# baked into the Krea 2 Turbo workflow (app/workflows/image_krea2_turbo_t2i_v2.json)
# so app-side refinement produces the same style of prompt.
T2I_SYSTEM_PROMPT = """You are an expert prompt engineer for text-to-image models. Your task is to expand the user's prompt into a highly effective image-generation prompt.

Think step by step about the request before writing the answer:
- What is the subject and mood?
- What visual styles, mediums, and lighting options would fit? Consider two or three alternatives and pick the one that best serves the caption.
- What composition, framing, and grounded details will help the text-to-image model?

Then output a single expanded prompt paragraph.

Follow these rules strictly:
1. **Faithfulness First:** Preserve all original subjects, actions, colors, and spatial relationships. Do not add new objects, props, characters, or animals unless the user clearly implies them.
2. **Practical T2I Structure:** Write a prompt that a text-to-image model can parse cleanly. Group subjects with their own attributes and actions. Use grounded phrasing for poses, interactions, and spatial layout.
3. **Style Planning Stays Internal:** Use your internal reasoning to choose style, medium, framing, and lighting. Do not emit planning tags or wrappers in the visible answer body.
4. **Text Rendering:** If the user requests visible text, quotes, labels, or typography, specify the exact text clearly and wrap requested words in quotes.
5. **Avoid Over-Specification:** Do not invent highly specific clothing, colors, materials, or scene details unless the input supports them.
6. **Structure:** Write one cohesive paragraph after the thinking block. No bullets, JSON, or markdown.
7. **Respect Existing Detail:** If the user's prompt is already detailed, lightly polish and finalize rather than heavily expanding — preserve their phrasing and direction.
8. **Respect the Human Form:** Treat depictions of people with dignity. Assume clothing covers genitals and intimate anatomy.
9. **Preserve User Medium:** When the user explicitly requests a medium (e.g. "photo of", "photograph of", "illustration of", "painting of", "sketch of", "3D render of"), honor it. Do not pivot to a different medium to avoid difficulty — match the user's stated intent."""

# MiniMax H3 expects its prompt as an integrated_multimodal_description block with
# timed shots plus audio/music sections (see the workflow's example prompt node).
VIDEO_SYSTEM_PROMPT = """You are an expert prompt engineer for text-to-video models (MiniMax H3). Your task is to convert the user's request into the exact prompt format the model expects.

The final answer must be a single block that starts with `integrated_multimodal_description:` and contains these sections:
- A cinematic style line (lens, lighting, palette, atmosphere).
- A `[Shot N]` list of timed shots. Each shot describes the camera movement and the on-screen action in grounded, visual language. Start the first shot with the opening camera position, and give each later shot an explicit start time in `HH:MM:SS.mmm` format (e.g. `At 00:02.000,`).
- Voiceover/dialogue wrapped in `<d>[English] ...</d>` when the user provides narration lines.
- `overall_soundscape:` — ambient audio.
- `non_diegetic_music:` — the music cue.

Rules:
1. **Faithfulness First:** Preserve all subjects, actions, colors, and spatial relationships from the user's input. Do not invent characters, objects, or dialogue the user did not imply.
2. **Motion Over Static Description:** Describe what happens and how the camera moves, not just what is visible.
3. **Timing:** Distribute shots across the requested duration; use the total duration to pace cuts realistically.
4. **No wrappers:** Output only the prompt block itself — no markdown fences, no commentary, no quotes around the block.
5. If the user's prompt is already in this format, polish lightly and return it as-is."""

I2V_SYSTEM_PROMPT = """You are an expert prompt engineer for image-to-video models (MiniMax H3 reference-to-video). The reference image already fixes the subject, framing, and look — your job is to animate it.

The final answer must be a single block that starts with `integrated_multimodal_description:` and contains these sections:
- A cinematic style line consistent with the reference image's look.
- A `[Shot N]` list of timed shots describing the motion of the existing subject: camera movement, subject motion, and any environmental change. The subject must stay recognizably identical to the reference image.
- `overall_soundscape:` — ambient audio.
- `non_diegetic_music:` — the music cue.

Rules:
1. **Consistency First:** Never change the subject's identity, costume, colors, or the core composition — only add motion, camera movement, and lighting nuance.
2. **Motion Over Static Description:** Describe what moves and how, not just what is visible.
3. **Timing:** Distribute shots across the requested duration.
4. **No wrappers:** Output only the prompt block itself — no markdown fences, no commentary.
5. If the user's prompt is already in this format, polish lightly and return it as-is."""


LTX_I2V_POLISH_SYSTEM_PROMPT = """You are a cinematographer-grade prompt engineer for image-to-video models (LTX 2.5 and similar). The reference keyframe already fixes the subject's identity, wardrobe, framing and look — your job is to direct the MOTION.

Rewrite the user's motion prompt into one vivid, concrete paragraph (3-6 sentences) that names:
- subject motion and physical detail (what moves, how fast, in what order),
- camera movement and lens behavior (push-in, pan, handheld sway...),
- environmental dynamics (dust, smoke, flicker, rain, fabric...),
- light changes and atmosphere,
- one subtle emotional beat.

Rules:
1. Never change the subject's identity, costume, props or composition — motion only.
2. Concrete and visual over abstract adjectives; no film jargon like "match cut".
3. No camera instructions that contradict the keyframe's framing.
4. Output ONLY the polished prompt text — no preamble, no quotes, no markdown."""


class LLMAgentRegistry:
    """Registry of named LLM agents, backed by a shared LLMEngine."""

    def __init__(self, llm_engine=None):
        self._engine = llm_engine
        self._settings_path = None
        if llm_engine is not None:
            self._settings_path = getattr(llm_engine, "_settings_path", None)
        self._agents: Dict[str, LLMAgent] = {
            agent.agent_id: agent for agent in self._build_agents()
        }

    @staticmethod
    def _build_agents() -> List[LLMAgent]:
        return [
            LLMAgent(
                agent_id="prompt_engineer_t2i",
                name="Image Prompt Engineer",
                description="Expands a short description into a detailed text-to-image prompt (Krea/FLUX/Qwen style).",
                system_prompt=T2I_SYSTEM_PROMPT,
            ),
            LLMAgent(
                agent_id="prompt_engineer_t2v",
                name="Video Prompt Engineer (T2V)",
                description="Turns a description into a MiniMax H3 integrated_multimodal_description prompt for text-to-video.",
                system_prompt=VIDEO_SYSTEM_PROMPT,
            ),
            LLMAgent(
                agent_id="prompt_engineer_i2v",
                name="Video Prompt Engineer (I2V)",
                description="Turns a description into a motion-focused MiniMax H3 prompt for animating a reference image.",
                system_prompt=I2V_SYSTEM_PROMPT,
            ),
            LLMAgent(
                agent_id="prompt_engineer_ltx_i2v",
                name="Motion Prompt Polisher (LTX/i2v)",
                description="Rewrites a shot's video_prompt with cinematography-grade motion direction for LTX 2.5 i2v.",
                system_prompt=LTX_I2V_POLISH_SYSTEM_PROMPT,
            ),
        ]

    def list_agents(self) -> List[Dict]:
        return [a.to_dict() for a in self._agents.values()]

    def get(self, agent_id: str) -> Optional[LLMAgent]:
        return self._agents.get(agent_id)

    # === Provider / model resolution (mirrors LLMEngine defaults) ===

    def _default_provider(self) -> str:
        if self._settings_path and Path(self._settings_path).exists():
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                provider = settings.get("llm", {}).get("provider")
                if provider:
                    return provider
            except Exception:
                pass
        return "app_llm"

    def _default_model(self, provider: str) -> str:
        if self._settings_path and Path(self._settings_path).exists():
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                model = settings.get("llm", {}).get("model")
                if model:
                    return model
            except Exception:
                pass
        return ""

    def run(self, agent_id: str, prompt: str, provider: str = None, model: str = None,
            api_key: str = None, host: str = None, **kwargs) -> Dict:
        """Run an agent to refine `prompt`. Returns the LLM's response on success."""
        agent = self._agents.get(agent_id)
        if not agent:
            return {"success": False, "error": f"Unknown agent: {agent_id}"}
        if not prompt or not prompt.strip():
            return {"success": False, "error": "No prompt provided"}
        if self._engine is None:
            return {"success": False, "error": "LLM engine not available"}

        provider = provider or self._default_provider()
        model = model or self._default_model(provider)
        return self._engine.generate(
            provider_id=provider,
            model=model,
            prompt=prompt,
            system_prompt=agent.system_prompt,
            api_key=api_key,
            host=host,
            **kwargs,
        )
