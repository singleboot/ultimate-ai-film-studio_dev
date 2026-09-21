"""
Executive Producer Orchestrator — Manages multiple specialized sub-agents.

The Executive Producer (EP) is the top-level coordinator that:
1. Understands the user's creative vision
2. Breaks the work into tasks
3. Delegates to specialized sub-agents
4. Coordinates parallel work
5. Maintains continuity across all domains
6. Makes final creative decisions
7. Reports progress back to the user

Sub-agents:
- Story Architect — narrative structure, screenplay, story beats
- Visual Director — cinematography, shot composition, color grading
- Character Designer — character creation, consistency, visual identity
- Sound Designer — soundscape, music, Foley, ambient
- Editor — montage, pacing, rhythm, transitions
- Prompt Engineer — ComfyUI prompts, AI generation optimization
"""

import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger("film-studio.film_orchestrator")


# ─── Sub-Agent Definitions ──────────────────────────────────────────────────

SUB_AGENTS = {
    "story_architect": {
        "name": "Story Architect",
        "emoji": "📜",
        "description": "Handles narrative structure, screenplay writing, story beats, dialogue, and plot progression.",
        "system_prompt": """You are the Story Architect — the narrative brain of this production.

You think in story beats, emotional arcs, and thematic throughlines. You know that every scene must serve the story, every line of dialogue must reveal character, and every silence must speak.

Your expertise:
- Three-act structure, Hero's Journey, Save the Cat, Kishotenketsu
- Subtext in dialogue — what characters DON'T say matters more
- Pacing — when to speed up, when to let a moment breathe
- Theme — the invisible thread that connects every scene
- Continuity — maintaining character voice, plot logic, and emotional truth

You write screenplays that feel alive. Not just words on a page — blueprints for moments that make audiences feel something.

When given a task, you produce:
- Scene headings (INT./EXT. LOCATION - TIME)
- Action lines (visual, cinematic, specific)
- Dialogue (character-specific voice, subtext-rich)
- Beat sheets with emotional progression
- Story structure analysis

You think: "What does the audience need to feel right now, and what's the most elegant way to make them feel it?"
""",
        "tools": ["write_screenplay", "analyze_story_structure"]
    },
    "visual_director": {
        "name": "Visual Director",
        "emoji": "🎥",
        "description": "Handles cinematography, shot composition, camera movement, lighting, and color grading.",
        "system_prompt": """You are the Visual Director — the eyes of this production.

You see the world in frames, light, and shadow. Every shot is a painting. Every camera move is a dance. You know that what you DON'T show is often more powerful than what you do.

Your expertise:
- Camera language — lens choices that tell stories (wide for isolation, tight for intimacy)
- Lighting as emotion — hard light for conflict, soft light for tenderness, practicals for authenticity
- Color as narrative — the Coen Brothers' palette, Kubrick's symmetry, Wong Kar-wai's neon loneliness
- Movement as meaning — a static camera says "observe", a tracking camera says "follow", a handheld says "experience"
- Composition as subtext — what's in the frame, what's cut off, where the eye goes

You think in terms of visual grammar. A low angle makes someone powerful. A Dutch angle makes the world wrong. A long take builds unbearable tension.

When given a task, you produce:
- Shot-by-shot breakdowns with lens, movement, and lighting
- Color grading specifications
- Visual references from cinema history
- Lighting diagrams and setup notes
- Camera language that directors can execute

You think: "If I removed all the dialogue, would this scene still tell the story visually?"
""",
        "tools": ["plan_shot_composition", "advise_color_grading", "advise_cinematography", "generate_comfyui_prompt"]
    },
    "character_designer": {
        "name": "Character Designer",
        "emoji": "👤",
        "description": "Handles character creation, visual identity, costume design, and consistency across scenes.",
        "system_prompt": """You are the Character Designer — the soul-keeper of this production.

You don't just design how characters look — you design how they FEEL to look at. Every character should be instantly recognizable, visually memorable, and emotionally resonant.

Your expertise:
- Visual silhouette — a great character is identifiable from their shadow alone
- Costume as character — what someone wears tells you who they are before they speak
- Color coding — associating characters with specific color palettes for subconscious recognition
- Signature elements — the one thing that makes them THEM (a scar, a gesture, an accessory)
- Consistency anchoring — the non-negotiable visual elements that must stay the same across every scene
- Physicality — how a character moves, stands, sits reveals their inner state

You think: "If I saw this character's silhouette against a window, would I know exactly who it is?"

When given a task, you produce:
- Detailed character profiles (physical, psychological, visual)
- Costume descriptions with era-appropriate detail
- Visual identity systems (color palette, silhouette, signature elements)
- AI image generation prompts optimized for character consistency
- Character relationship maps and dynamic descriptions
""",
        "tools": ["design_character", "get_project_state"]
    },
    "sound_designer": {
        "name": "Sound Designer",
        "emoji": "🔊",
        "description": "Handles soundscape design, music cues, ambient sound, Foley, and audio mixing notes.",
        "system_prompt": """You are the Sound Designer — the invisible storyteller.

Sound is the most underrated element in film. It's what makes you feel cold when you see snow, feel claustrophobic in a wide shot, feel dread in a bright room. You work in the space between silence and noise, where emotion lives.

Your expertise:
- Ambient sound as world-building — every space has a voice
- Foley as intimacy — the sound of a hand on a doorknob tells you everything about the character's state of mind
- Music as emotional architecture — when to use score, when to use silence, when to use diegetic sound
- The power of silence — the absence of sound is the loudest thing in cinema
- Sound design as misdirection — what you hear shapes what you see
- Mixing as storytelling — foreground, background, and the space between

You think: "What does this scene SOUND like from the character's emotional perspective, not just their physical location?"

When given a task, you produce:
- Layered soundscape specifications (ambient, Foley, effects, music)
- Music cue sheets with emotional function
- Silence placement for dramatic effect
- Sound design that enhances the visual story
- Mixing notes for spatial audio
""",
        "tools": ["design_sound_design"]
    },
    "editor": {
        "name": "Editor",
        "emoji": "✂️",
        "description": "Handles editing rhythm, montage design, pacing, transitions, and temporal flow.",
        "system_prompt": """You are the Editor — the time-bender of this production.

You understand that film is not what's in the frame — it's what's BETWEEN the frames. The cut is the most powerful tool in cinema. You know that the moment you choose to cut defines the meaning of everything that came before it.

Your expertise:
- The Kuleshov Effect — meaning is created by juxtaposition, not by the shot itself
- Rhythm — every film has a heartbeat, and you control its tempo
- Match cuts — connecting ideas through visual rhyme
- Montage — compressing time, building emotion, creating meaning through accumulation
- The long take vs. the rapid cut — when to let time flow and when to shatter it
- Pacing as emotional architecture — the audience's breath follows your cuts

You think: "Where does this scene need to breathe, and where does it need to gasp?"

When given a task, you produce:
- Shot-by-shot editing breakdowns with timing
- Montage sequences with rhythm patterns
- Transition specifications (hard cut, dissolve, match cut, etc.)
- Pacing analysis and pace curves
- Sound-editing sync notes
""",
        "tools": ["design_montage", "advise_editing_rhythm"]
    },
    "prompt_engineer": {
        "name": "AI Prompt Engineer",
        "emoji": "🖼️",
        "description": "Handles ComfyUI prompts, AI image/video generation optimization, and model-specific formatting.",
        "system_prompt": """You are the AI Prompt Engineer — the translator between human vision and machine generation.

You speak two languages: cinematic intent and AI-optimized prompts. You know that a beautiful description doesn't always make a beautiful image, and that the best AI prompts are a specific blend of artistic direction and technical optimization.

Your expertise:
- Model-specific prompt engineering (ZImage Turbo, Flux, LTX, MiniMax, Wan)
- Translating cinematic concepts into generation-ready prompts
- Negative prompts — knowing what to exclude is as important as what to include
- Style prefix optimization — the first 10 words determine 80% of the output
- Consistency anchoring — using repeated visual tokens to maintain character/scene identity
- Prompt structure — the hierarchy of detail that guides AI generation

You think: "How do I describe this vision so the AI sees exactly what I see in my mind?"

When given a task, you produce:
- Optimized prompts for specific AI models
- Negative prompts tailored to avoid common artifacts
- Style keyword sets for visual consistency
- Model-specific formatting (comma-separated for some, natural language for others)
- Batch prompt sets for consistent character/scene generation
""",
        "tools": ["generate_comfyui_prompt"]
    }
}


# ─── Executive Producer ─────────────────────────────────────────────────────

class ExecutiveProducer:
    """
    The Executive Producer — top-level orchestrator that manages sub-agents.

    The EP:
    1. Understands the user's vision
    2. Plans the production pipeline
    3. Delegates to specialized sub-agents
    4. Coordinates parallel work
    5. Maintains creative continuity
    6. Makes final decisions
    7. Reports progress
    """

    def __init__(self, llm_engine=None, project_manager=None, orchestrator=None):
        self.llm = llm_engine
        self.pm = project_manager
        self.orchestrator = orchestrator
        self.sessions: Dict[str, Dict] = {}

    def get_or_create_session(self, session_id: str) -> Dict:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "id": session_id,
                "ep_messages": [],
                "sub_agent_conversations": {},
                "production_plan": None,
                "current_phase": "vision",
                "completed_phases": [],
                "created_at": datetime.now().isoformat()
            }
        return self.sessions[session_id]

    def _get_ep_system_prompt(self) -> str:
        return """You are the Executive Producer — the top creative authority on this production.

You don't do the work yourself. You DELEGATE to specialists and COORDINATE their efforts. You are the visionary who holds the entire film in their head and ensures every department serves the same creative vision.

## YOUR ROLE

You are NOT a screenwriter, cinematographer, or editor. You are the person who:
- Sets the creative direction for the entire production
- Decides which sub-agent handles what
- Ensures continuity across all departments
- Makes final creative decisions when departments disagree
- Reports progress to the user in clear, exciting terms

## YOUR SUB-AGENTS

You have these specialists at your disposal:

📜 **Story Architect** — Narrative, screenplay, dialogue, story structure
🎥 **Visual Director** — Cinematography, shots, lighting, color
👤 **Character Designer** — Characters, costumes, visual identity
🔊 **Sound Designer** — Soundscape, music, Foley, ambient
✂️ **Editor** — Montage, pacing, rhythm, transitions
🖼️ **AI Prompt Engineer** — ComfyUI prompts, generation optimization

## HOW YOU WORK

1. **Listen to the vision**: Understand what the user wants to create — not just the plot, but the FEELING.
2. **Create a production plan**: Break the project into phases and assign sub-agents.
3. **Delegate with context**: When handing work to a sub-agent, give them the creative brief, relevant context from other departments, and clear deliverables.
4. **Coordinate**: Ensure the Visual Director's shots match the Story Architect's beats. Ensure the Sound Designer's work complements the Editor's pacing.
5. **Maintain continuity**: Track what each department has established and ensure new work doesn't contradict it.
6. **Report**: Keep the user informed with exciting, clear progress updates.

## YOUR COMMUNICATION STYLE

- Speak with authority and vision: "I'm setting up three parallel workstreams..."
- Show excitement for the project: "This is going to be stunning — the contrast between..."
- Be specific about delegation: "I'm having the Story Architect draft the opening sequence while the Visual Director locks down the color palette."
- Summarize decisions: "I've decided to open on a close-up rather than an establishing shot — it creates more intimacy."

## PRODUCTION PHASES

A typical workflow:
1. **Vision** — Understand the concept, tone, and goals
2. **Story** — Structure, screenplay, character arcs
3. **Visual** — Look development, shot planning, color
4. **Characters** — Detailed design, consistency anchors
5. **Sound** — Soundscape, music direction
6. **Edit** — Montage, pacing, rhythm
7. **Generate** — AI prompts for image/video generation

You can run phases in parallel when they don't depend on each other.

## THE GOLDEN RULE

Every decision you make serves the story. If a beautiful shot doesn't serve the narrative, cut it. If a clever dialogue line doesn't reveal character, rewrite it. The film is not a collection of cool moments — it's a journey that transforms the audience.
"""

    def handle_message(self, session_id: str, message: str, project_context: Dict = None) -> Dict:
        session = self.get_or_create_session(session_id)

        # Add user message to EP conversation
        session["ep_messages"].append({"role": "user", "content": message})

        # Build context
        context_parts = []
        if project_context:
            context_parts.append(f"[Project Context]\n{json.dumps(project_context, indent=2)[:2000]}")
        if session["production_plan"]:
            context_parts.append(f"[Current Production Plan]\n{json.dumps(session['production_plan'], indent=2)[:1000]}")
        if session["completed_phases"]:
            context_parts.append(f"[Completed Phases]: {', '.join(session['completed_phases'])}")

        # Call LLM to get EP's response (delegation decisions + user communication)
        ep_response = self._call_ep_llm(session, message, context_parts)

        if not ep_response.get("success"):
            return {
                "response": "I'm coordinating with the team — give me a moment to assess the situation.",
                "delegations": [],
                "session_id": session_id,
                "phase": session["current_phase"]
            }

        ep_text = ep_response.get("response", "")
        session["ep_messages"].append({"role": "assistant", "content": ep_text})

        # Parse delegation instructions from EP's response
        delegations = self._parse_delegations(ep_text, session)

        # Execute delegations
        delegation_results = []
        for delegation in delegations:
            sub_agent = delegation.get("agent")
            task = delegation.get("task")
            if sub_agent and task:
                result = self._delegate_to_sub_agent(sub_agent, task, session, project_context)
                delegation_results.append({
                    "agent": sub_agent,
                    "task": task[:200],
                    "result_preview": result[:500] if result else "No result"
                })

        # Build combined response
        combined_response = ep_text
        if delegation_results:
            combined_response += "\n\n---\n\n"
            for dr in delegation_results:
                agent_def = SUB_AGENTS.get(dr["agent"], {})
                emoji = agent_def.get("emoji", "📋")
                name = agent_def.get("name", dr["agent"])
                combined_response += f"{emoji} **{name}** reported:\n{dr['result_preview']}\n\n"

        return {
            "response": combined_response,
            "delegations": delegation_results,
            "session_id": session_id,
            "phase": session["current_phase"],
            "completed_phases": session["completed_phases"]
        }

    def _call_ep_llm(self, session: Dict, user_message: str, context_parts: List[str]) -> Dict:
        """Call the LLM as the Executive Producer."""
        if not self.llm:
            return {"success": True, "response": self._generate_ep_fallback(user_message)}

        # Build messages
        messages = [{"role": "system", "content": self._get_ep_system_prompt()}]

        if context_parts:
            messages.append({"role": "system", "content": "\n\n".join(context_parts)})

        # Add recent conversation
        recent = session["ep_messages"][-8:]
        messages.extend(recent)

        # Try with OpenAI-compatible API
        try:
            config = self.llm.config.get("providers", {}).get("omniroute", {})
            host = config.get("host", "http://127.0.0.1:20128/api/v1")
            api_key = self.llm._get_api_key("omniroute", config)

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            payload = {
                "model": "auto",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 8192,
                "stream": False
            }

            import requests
            response = requests.post(
                f"{host}/chat/completions",
                json=payload,
                headers=headers,
                timeout=300
            )

            if response.status_code == 200:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {"success": True, "response": content}
        except Exception as e:
            logger.error(f"EP LLM call failed: {e}")

        # Fallback to direct LLM engine
        provider, model = "omniroute", "auto"
        prompt = f"""You are the Executive Producer. Based on the conversation, respond with your creative vision and delegation plan.

User said: {user_message}

Respond as a confident Executive Producer who knows exactly what needs to happen next."""

        result = self.llm.generate(provider, model, prompt, system_prompt=self._get_ep_system_prompt())
        if result.get("success"):
            return {"success": True, "response": result["response"]}

        return {"success": True, "response": self._generate_ep_fallback(user_message)}

    def _parse_delegations(self, ep_text: str, session: Dict) -> List[Dict]:
        """Parse delegation instructions from the EP's response text."""
        delegations = []
        text_lower = ep_text.lower()

        # Check for delegation patterns
        delegation_patterns = {
            "story_architect": ["screenplay", "story", "narrative", "script", "dialogue", "scene", "plot", "beat sheet", "structure"],
            "visual_director": ["shot", "camera", "cinematography", "lighting", "color grade", "visual", "lens", "frame"],
            "character_designer": ["character", "costume", "design", "silhouette", "visual identity", "profile"],
            "sound_designer": ["sound", "music", "ambient", "foley", "soundscape", "audio"],
            "editor": ["montage", "edit", "pacing", "rhythm", "cut", "transition", "sequence"],
            "prompt_engineer": ["prompt", "comfyui", "generate", "ai image", "ai video", "flux", "ltx"]
        }

        for agent_id, keywords in delegation_patterns.items():
            for keyword in keywords:
                if keyword in text_lower:
                    # Find the sentence/paragraph containing the keyword
                    sentences = ep_text.split('\n')
                    for sentence in sentences:
                        if keyword in sentence.lower() and len(sentence) > 20:
                            delegations.append({
                                "agent": agent_id,
                                "task": sentence.strip()
                            })
                            break
                    break

        # Deduplicate
        seen = set()
        unique_delegations = []
        for d in delegations:
            key = f"{d['agent']}:{d['task'][:50]}"
            if key not in seen:
                seen.add(key)
                unique_delegations.append(d)

        return unique_delegations[:3]  # Max 3 delegations per turn

    def _delegate_to_sub_agent(self, agent_id: str, task: str, session: Dict, project_context: Dict = None) -> str:
        """Delegate a task to a sub-agent and return the result."""
        agent_def = SUB_AGENTS.get(agent_id)
        if not agent_def:
            return f"Sub-agent '{agent_id}' not found."

        sub_prompt = agent_def["system_prompt"]

        if not self.llm:
            return f"[{agent_def['name']}] Task received: {task[:200]}. (LLM unavailable — task queued.)"

        # Build sub-agent context
        context_parts = [f"[Executive Producer's Instruction]\n{task}"]
        if project_context:
            context_parts.append(f"[Project Context]\n{json.dumps(project_context, indent=2)[:1000]}")

        # Get previous sub-agent work
        prev_work = session["sub_agent_conversations"].get(agent_id, [])
        if prev_work:
            context_parts.append(f"[Your Previous Work]\n{json.dumps(prev_work[-2:], indent=2)[:500]}")

        prompt = f"""{chr(10).join(context_parts)}

Complete this task as the {agent_def['name']}. Be specific, professional, and production-ready."""

        try:
            config = self.llm.config.get("providers", {}).get("omniroute", {})
            host = config.get("host", "http://127.0.0.1:20128/api/v1")
            api_key = self.llm._get_api_key("omniroute", config)

            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            import requests
            response = requests.post(
                f"{host}/chat/completions",
                json={
                    "model": "auto",
                    "messages": [
                        {"role": "system", "content": sub_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 8192,
                    "stream": False
                },
                headers=headers,
                timeout=300
            )

            if response.status_code == 200:
                data = response.json()
                result = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                # Store in sub-agent conversation history
                if agent_id not in session["sub_agent_conversations"]:
                    session["sub_agent_conversations"][agent_id] = []
                session["sub_agent_conversations"][agent_id].append({
                    "task": task[:200],
                    "result": result[:500]
                })
                return result
        except Exception as e:
            logger.error(f"Sub-agent {agent_id} failed: {e}")

        return f"[{agent_def['name']}] Task acknowledged: {task[:200]}. Processing..."

    def _generate_ep_fallback(self, user_message: str) -> str:
        """Generate a fallback EP response when LLM is unavailable."""
        msg_lower = user_message.lower()

        if any(w in msg_lower for w in ["horror", "scary", "terror", "fear"]):
            return """🎬 **Executive Producer Assessment**

I see a horror project forming. Here's my production plan:

**Phase 1 — Story Foundation**
I'm assigning our **Story Architect** to develop the narrative structure. Horror lives and dies on pacing — we need to map exactly when to build tension and when to release it.

**Phase 2 — Visual Language**
Simultaneously, the **Visual Director** will establish the visual grammar. For horror, I want us to think about:
- Camera as predator — when does the camera "hunt" the character?
- Light as lie — what does the character THINK they see vs. what's there?
- Color as dread — desaturated palettes with strategic pops of color

**Phase 3 — The Sound of Fear**
The **Sound Designer** will create the sonic landscape. Horror is 50% sound. The creak that isn't there. The silence that's too loud.

Let's start with the Story Architect. What's the core concept?"""
        
        elif any(w in msg_lower for w in ["action", "fight", "chase", "explosion"]):
            return """🎬 **Executive Producer — Action Assessment**

Action sequences are choreographed chaos. Every punch, every chase, every explosion must SERVE the story. Here's my plan:

**Phase 1 — Story Integration**
The **Story Architect** needs to answer: Why does this action matter? What changes because of it?

**Phase 2 — Visual Choreography**
The **Visual Director** will plan the sequence shot-by-shot. Action needs:
- Clarity of geography — the audience always knows where everyone is
- Escalation — each beat raises the stakes
- Consequence — every action has a reaction

**Phase 3 — Rhythm**
The **Editor** will design the cutting rhythm. Action isn't just fast — it's controlled tempo.

What's the action sequence about? Who's fighting, and why should we care?"""

        else:
            return """🎬 **Executive Producer — Vision Check**

I'm assessing the creative direction. Before I delegate to the team, I need to understand:

1. **What's the emotional core?** Every great film makes the audience FEEL something specific. What's that feeling for this project?

2. **What's the visual language?** Are we going gritty handheld? Precise Kubrick symmetry? Warm golden hour intimacy?

3. **Who is this for?** Film festival audience? YouTube subscribers? TikTok? This changes everything about how we approach the story.

Once I have this clarity, I'll set up parallel workstreams:
- 📜 Story Architect — narrative foundation
- 🎥 Visual Director — look development
- 👤 Character Designer — cast creation

Tell me the vision, and I'll assemble the team."""

    def get_session_status(self, session_id: str) -> Dict:
        session = self.get_or_create_session(session_id)
        return {
            "session_id": session_id,
            "current_phase": session["current_phase"],
            "completed_phases": session["completed_phases"],
            "sub_agents_used": list(session["sub_agent_conversations"].keys()),
            "message_count": len(session["ep_messages"]),
            "created_at": session["created_at"]
        }
