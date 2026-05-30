import json
import os
import re
import uuid
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

class ProjectCreatorSession:
    def __init__(self, chat_id: str, mode: str = "semi_automated"):
        self.chat_id = chat_id
        self.state = "welcome"  # welcome, concept, aesthetics, building, awaiting_approval, complete
        self.mode = mode        # automated, semi_automated
        self.data = {
            "title": "",
            "path": "",
            "genre": "",
            "tone": "",
            "visual_style": "",
            "film_aesthetic": "",
            "screenplay_data": None,
            "char_data": [],
            "location_data": [],
            "pending_approvals": [],
            "current_approval": None
        }
        self.progress_status = ""
        self.created_at = datetime.now().isoformat()

class AgentProjectCreator:
    def __init__(self, project_manager, llm_engine):
        self.pm = project_manager
        self.llm = llm_engine
        self.sessions: Dict[str, ProjectCreatorSession] = {}

    def get_or_create_session(self, chat_id: str, mode: str = "semi_automated") -> ProjectCreatorSession:
        if chat_id not in self.sessions:
            self.sessions[chat_id] = ProjectCreatorSession(chat_id, mode)
        return self.sessions[chat_id]

    def reset_session(self, chat_id: str) -> ProjectCreatorSession:
        self.sessions[chat_id] = ProjectCreatorSession(chat_id)
        return self.sessions[chat_id]

    def handle_message(self, chat_id: str, message: str) -> Dict[str, Any]:
        """Process incoming chat messages and advance state machine."""
        session = self.get_or_create_session(chat_id)
        msg = message.strip()

        if msg.lower() == "/reset" or msg.lower() == "reset":
            session = self.reset_session(chat_id)
            return {
                "response": "Hello! I am your AI Film Producer Assistant. Let's build a new cinematic project together.\n\nFirst, **what is the title of your movie**?",
                "state": session.state
            }

        # Global commands
        if msg.lower().startswith("/mode "):
            mode_choice = msg.split(" ")[1].lower()
            if mode_choice in ["automated", "fully_automated", "full"]:
                session.mode = "automated"
                return {"response": f"Mode changed to **Fully Automated**. I will generate all assets autonomously without stopping.", "state": session.state}
            else:
                session.mode = "semi_automated"
                return {"response": f"Mode changed to **Semi-Automated**. I will pause and request approval for screenplays and key assets.", "state": session.state}

        if session.state == "welcome":
            if not msg:
                return {"response": "Please enter a title for your project.", "state": session.state}
            session.data["title"] = msg
            
            # Auto-assign directory
            safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in msg)
            session.data["path"] = str(Path(self.pm.projects_dir) / safe_name)
            
            session.state = "concept"
            return {
                "response": f"Got it, the title is **\"{msg}\"**.\n\nNow, tell me about the **genre, logline, or core story idea** (e.g. 'A sci-fi thriller about a crew exploring a black hole', 'A gritty detective noir in 1940s Tokyo').",
                "state": session.state
            }

        elif session.state == "concept":
            session.data["genre"] = msg
            session.state = "aesthetics"
            return {
                "response": "Awesome. Next, describe the **visual style or tone** you want (e.g. 'Moody, high-contrast, neon lights', 'Warm golden hour, vintage film grain', 'Gritty, dark cyberpunk').",
                "state": session.state
            }

        elif session.state == "aesthetics":
            session.data["visual_style"] = msg
            session.state = "building"
            
            # Start background generation thread
            thread = threading.Thread(target=self._run_project_generation, args=(session,), daemon=True)
            thread.start()
            
            return {
                "response": "Perfect! I have all the details. I am now starting the project generation in the background.\n\nI will notify you here with updates.",
                "state": session.state
            }

        elif session.state == "awaiting_approval":
            # Handle inline text replies if Telegram button clicks are not used
            approval_msg = msg.lower()
            curr = session.data["current_approval"]
            if not curr:
                session.state = "building"
                return {"response": "No pending approval item. Continuing...", "state": session.state}
            
            if "approve" in approval_msg or approval_msg == "yes" or approval_msg == "y" or approval_msg == "ok":
                return self.approve_current_item(chat_id)
            elif "regen" in approval_msg or "redo" in approval_msg or approval_msg == "no":
                # Check for feedback prompt
                feedback = msg if len(msg) > 10 else None
                return self.regenerate_current_item(chat_id, feedback)
            else:
                return {
                    "response": "Please type **Approve** to accept this asset, or **Regen** to create it again (optionally append feedback, e.g. 'Regen make the character look older').",
                    "state": session.state
                }

        elif session.state == "complete":
            return {
                "response": f"Your project **\"{session.data['title']}\"** is complete! You can open it in the Web interface.",
                "state": session.state
            }

        return {"response": "State mismatch. Type **/reset** to restart.", "state": session.state}

    def approve_current_item(self, chat_id: str) -> Dict[str, Any]:
        session = self.sessions.get(chat_id)
        if not session or not session.data["current_approval"]:
            return {"response": "No active item to approve."}
        
        curr = session.data["current_approval"]
        item_type = curr.get("type")
        
        if item_type == "screenplay":
            session.data["screenplay_data"] = curr.get("data")
            session.progress_status = "Approved screenplay. Extracting characters..."
        elif item_type == "character":
            session.data["char_data"].append(curr.get("data"))
        elif item_type == "location":
            session.data["location_data"].append(curr.get("data"))
            
        session.data["current_approval"] = None
        
        # Advance the generation queue
        thread = threading.Thread(target=self._run_project_generation, args=(session,), daemon=True)
        thread.start()
        
        return {
            "response": f"✅ Approved and added {item_type} to film bible. Continuing setup...",
            "state": session.state
        }

    def regenerate_current_item(self, chat_id: str, feedback: str = None) -> Dict[str, Any]:
        session = self.sessions.get(chat_id)
        if not session or not session.data["current_approval"]:
            return {"response": "No active item to regenerate."}
            
        curr = session.data["current_approval"]
        item_type = curr.get("type")
        
        session.progress_status = f"Regenerating {item_type}..."
        session.state = "building"
        session.data["current_approval"] = None
        
        # Tweak the generation cue by passing feedback
        thread = threading.Thread(target=self._run_project_generation, args=(session, feedback), daemon=True)
        thread.start()
        
        return {
            "response": f"🔄 Regenerating {item_type} with your feedback... Please wait.",
            "state": session.state
        }

    def _run_project_generation(self, session: ProjectCreatorSession, feedback: str = None):
        """Sequential background builder that respects the interactive mode constraints."""
        try:
            # Stage 1: Generate Screenplay if missing
            if not session.data["screenplay_data"]:
                session.progress_status = "Drafting screenplay scenes..."
                screenplay = self._generate_screenplay_llm(session, feedback)
                if not screenplay:
                    session.progress_status = "Failed to generate screenplay."
                    return
                
                if session.mode == "semi_automated":
                    session.data["current_approval"] = {
                        "type": "screenplay",
                        "data": screenplay,
                        "description": f"Title: {screenplay.get('title')}\nScenes: {len(screenplay.get('scenes', []))}"
                    }
                    session.state = "awaiting_approval"
                    session.progress_status = "Awaiting screenplay approval."
                    return
                else:
                    session.data["screenplay_data"] = screenplay

            # Stage 2: Extract & Generate Characters
            screenplay = session.data["screenplay_data"]
            if not session.data["char_data"] and not session.data["pending_approvals"]:
                session.progress_status = "Extracting cast bible from script..."
                chars = self._extract_characters_llm(screenplay)
                session.data["pending_approvals"] = [{"type": "character", "data": c} for c in chars]
            
            # Process character portraits
            while session.data["pending_approvals"]:
                next_item = session.data["pending_approvals"].pop(0)
                if session.mode == "semi_automated":
                    session.data["current_approval"] = next_item
                    session.state = "awaiting_approval"
                    session.progress_status = f"Awaiting approval for character: {next_item['data'].get('name')}"
                    return
                else:
                    session.data["char_data"].append(next_item["data"])

            # Stage 3: Extract & Generate Locations
            if not session.data["location_data"] and not session.data.get("_locations_extracted"):
                session.progress_status = "Scanning locations..."
                locs = self._extract_locations_llm(screenplay)
                session.data["pending_approvals"] = [{"type": "location", "data": l} for l in locs]
                session.data["_locations_extracted"] = True

            while session.data["pending_approvals"]:
                next_item = session.data["pending_approvals"].pop(0)
                if session.mode == "semi_automated":
                    session.data["current_approval"] = next_item
                    session.state = "awaiting_approval"
                    session.progress_status = f"Awaiting approval for location: {next_item['data'].get('name')}"
                    return
                else:
                    session.data["location_data"].append(next_item["data"])

            # Stage 4: Compile & Save Project State
            session.progress_status = "Finalizing project folder structure..."
            self._save_project_files(session)
            session.state = "complete"
            session.progress_status = "Creation complete!"
        except Exception as e:
            session.progress_status = f"Error during auto-build: {str(e)}"
            session.state = "welcome"

    def _generate_screenplay_llm(self, session: ProjectCreatorSession, feedback: str = None) -> Optional[Dict]:
        title = session.data["title"]
        genre = session.data["genre"]
        style = session.data["visual_style"]
        
        prompt = f"""You are a professional film producer. Write a script outline for a short film titled "{title}".
Genre/Concept: {genre}
Visual style: {style}
{f"Feedback adjustment request: {feedback}" if feedback else ""}

Provide your response in JSON format. The JSON MUST follow this strict structure:
{{
  "title": "{title}",
  "tone": "Gritty, suspenseful",
  "logline": "Logline of the movie",
  "scenes": [
    {{
      "scene_id": 1,
      "scene_number": 1,
      "scene_title": "INT. SPACESHIP COCKPIT - NIGHT",
      "location_id": "Spaceship Cockpit",
      "time_of_day": "Night",
      "synopsis": "Brief description of the action.",
      "characters_present": ["Captain Vance", "Pilot Reed"],
      "shots": [
        {{
          "shot_id": "S1-1",
          "shot_type": "CU",
          "action": "Vance staring into the abyss of the black hole.",
          "camera_language": ["Tight Close-up", "Low angle"],
          "lighting_language": ["Contrast", "Neon cyan highlights"],
          "emotion": "Terror",
          "visual_motifs": ["Blinking status monitors"]
        }}
      ]
    }}
  ]
}}
Ensure the screenplay has at least 3-4 scenes with detailed shot descriptions. Return ONLY the raw JSON block without markdown packaging or quotes.
"""
        # Call the active LLM model
        settings = self._load_settings()
        prov = settings.get("llm", {}).get("provider", "ollama")
        model = settings.get("llm", {}).get("model", "gemma4:e4b")
        
        res = self.llm.generate(prov, model, prompt)
        if res.get("success"):
            return self._parse_json_safely(res["response"])
        return None

    def _extract_characters_llm(self, screenplay: Dict) -> List[Dict]:
        prompt = f"""Given this movie screenplay script, extract all key characters and write a detailed physical profile for image generation for each one.
Screenplay:
{json.dumps(screenplay, indent=2)}

Return your response in JSON format. The JSON MUST be a simple list of characters:
[
  {{
    "name": "Captain Vance",
    "description": "A rugged astronaut in his late 40s, grey-flecked beard, intense eyes, wearing a dark worn spacesuit.",
    "role": "Main Captain",
    "clothing": "Heavy dark-gray spacesuit with tactical straps"
  }}
]
Return ONLY the raw JSON list without markdown wrappers.
"""
        settings = self._load_settings()
        prov = settings.get("llm", {}).get("provider", "ollama")
        model = settings.get("llm", {}).get("model", "gemma4:e4b")
        
        res = self.llm.generate(prov, model, prompt)
        if res.get("success"):
            return self._parse_json_safely(res["response"]) or []
        return []

    def _extract_locations_llm(self, screenplay: Dict) -> List[Dict]:
        prompt = f"""Given this movie screenplay, extract all distinct locations and create scenic prompts for each location.
Screenplay:
{json.dumps(screenplay, indent=2)}

Return your response in JSON format. The JSON MUST be a simple list:
[
  {{
    "name": "Spaceship Cockpit",
    "details": "High-tech spaceship bridge filled with glowing neon cyan monitors, tight claustrophobic metal walls, pilot seats.",
    "type": "Interior Space Capsule"
  }}
]
Return ONLY the raw JSON list without markdown wrappers.
"""
        settings = self._load_settings()
        prov = settings.get("llm", {}).get("provider", "ollama")
        model = settings.get("llm", {}).get("model", "gemma4:e4b")
        
        res = self.llm.generate(prov, model, prompt)
        if res.get("success"):
            return self._parse_json_safely(res["response"]) or []
        return []

    def _save_project_files(self, session: ProjectCreatorSession):
        title = session.data["title"]
        proj_path = Path(session.data["path"])
        proj_path.mkdir(parents=True, exist_ok=True)
        
        # Save project.json config
        project_config = {
            "name": title,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        with open(proj_path / "project.json", "w", encoding="utf-8") as f:
            json.dump(project_config, f, indent=2)
            
        # Compile project_state.json
        state = {
            "projectSettings": {
                "characters": len(session.data["char_data"]),
                "locations": len(session.data["location_data"]),
                "scenes": len(session.data["screenplay_data"].get("scenes", [])),
                "totalShots": sum(len(s.get("shots", [])) for s in session.data["screenplay_data"].get("scenes", [])),
                "aspectRatio": "16:9",
                "imgResolution": "1024x576"
            },
            "screenplayData": session.data["screenplay_data"],
            "charData": session.data["char_data"],
            "locationData": session.data["location_data"],
            "topicIdeas": [
                {
                    "title": title,
                    "genres": [session.data["genre"]],
                    "synopsis": session.data["screenplay_data"].get("logline", "")
                }
            ],
            "selectedIdeaIndex": 0
        }
        with open(proj_path / "project_state.json", "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

        # Register project in backend registry
        self.pm.register_project(title, str(proj_path))

    def _parse_json_safely(self, text: str) -> Any:
        try:
            # Clean markdown codeblocks
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            return json.loads(cleaned)
        except Exception:
            # Regex fallback to extract JSON block
            m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        return None

    def _load_settings(self) -> Dict:
        settings_path = Path(__file__).parent.parent / "settings.json"
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
