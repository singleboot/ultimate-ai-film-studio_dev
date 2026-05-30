import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


class ProjectManager:
    def __init__(self, projects_dir: str = None):
        if projects_dir is None:
            base_dir = Path(__file__).parent.parent
            projects_dir = base_dir / "projects"
        self.projects_dir = Path(projects_dir)
        self.projects_dir.mkdir(exist_ok=True)
        self.current_project = None
        self._registry_file = Path(__file__).parent.parent / "config" / "project_registry.json"
        self._registry_file.parent.mkdir(parents=True, exist_ok=True)

    # ── Project Registry (tracks all project locations, including external) ──

    def _load_registry(self) -> List[Dict]:
        """Load the project registry from disk."""
        if self._registry_file.exists():
            try:
                with open(self._registry_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_registry(self, entries: List[Dict]):
        """Save the project registry to disk."""
        try:
            with open(self._registry_file, 'w', encoding='utf-8') as f:
                json.dump(entries, f, indent=2)
        except Exception:
            pass

    def register_project(self, name: str, path: str):
        """Register a project path in the persistent registry."""
        entries = self._load_registry()
        norm_path = str(Path(path).resolve())
        # Update existing entry or add new
        found = False
        for entry in entries:
            if str(Path(entry.get("path", "")).resolve()) == norm_path:
                entry["name"] = name
                entry["last_opened"] = datetime.now().isoformat()
                found = True
                break
        if not found:
            entries.append({
                "name": name,
                "path": norm_path,
                "registered_at": datetime.now().isoformat(),
                "last_opened": datetime.now().isoformat()
            })
        self._save_registry(entries)

    def unregister_project(self, path: str):
        """Remove a project from the registry."""
        entries = self._load_registry()
        norm_path = str(Path(path).resolve())
        entries = [e for e in entries if str(Path(e.get("path", "")).resolve()) != norm_path]
        self._save_registry(entries)

    # ── Project CRUD ──

    def create_project(self, name: str, template_name: str = None, description: str = "", location: str = None) -> Dict:
        """Create a new project."""
        if location:
            project_path = Path(location) / name
        else:
            project_path = self.projects_dir / name
        
        if project_path.exists():
            return {"success": False, "error": "Project already exists"}

        project_path.mkdir(parents=True)
        (project_path / "prompts").mkdir(exist_ok=True)
        (project_path / "characters").mkdir(exist_ok=True)
        (project_path / "locations").mkdir(exist_ok=True)
        (project_path / "scenes" / "images").mkdir(parents=True, exist_ok=True)
        (project_path / "scenes" / "videos").mkdir(parents=True, exist_ok=True)
        (project_path / "exports").mkdir(exist_ok=True)

        project_data = {
            "name": name,
            "description": description,
            "template": template_name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "variables": {},
            "stages": {
                "characters": {"completed": 0, "total": 0},
                "locations": {"completed": 0, "total": 0},
                "scenes": {"completed": 0, "total": 0},
                "videos": {"completed": 0, "total": 0}
            }
        }

        project_file = project_path / "project.json"
        with open(project_file, 'w', encoding='utf-8') as f:
            json.dump(project_data, f, indent=2)

        self.current_project = name
        # Register in persistent registry
        self.register_project(name, str(project_path))
        return {"success": True, "project_path": str(project_path)}

    def get_projects(self) -> List[Dict]:
        """Get list of all projects (built-in + externally registered)."""
        seen_paths = set()
        projects = []

        # 1. Scan built-in projects directory
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir() and (project_dir / "project.json").exists():
                try:
                    with open(project_dir / "project.json", 'r', encoding='utf-8') as f:
                        project_data = json.load(f)
                        norm = str(project_dir.resolve())
                        seen_paths.add(norm)
                        projects.append({
                            "name": project_data.get("name"),
                            "path": str(project_dir),
                            "description": project_data.get("description"),
                            "created_at": project_data.get("created_at"),
                            "updated_at": project_data.get("updated_at")
                        })
                except Exception:
                    pass

        # 2. Include externally registered projects
        registry = self._load_registry()
        for entry in registry:
            entry_path = Path(entry.get("path", ""))
            norm = str(entry_path.resolve())
            if norm in seen_paths:
                continue
            # Verify the project still exists on disk
            pf = entry_path / "project.json"
            if pf.exists():
                try:
                    with open(pf, 'r', encoding='utf-8') as f:
                        project_data = json.load(f)
                    seen_paths.add(norm)
                    projects.append({
                        "name": project_data.get("name", entry.get("name")),
                        "path": str(entry_path),
                        "description": project_data.get("description", ""),
                        "created_at": project_data.get("created_at"),
                        "updated_at": project_data.get("updated_at")
                    })
                except Exception:
                    pass

        return sorted(projects, key=lambda x: x.get("updated_at") or "", reverse=True)

    def load_project(self, name: str) -> Optional[Dict]:
        """Load a project."""
        project_path = self.projects_dir / name / "project.json"
        if not project_path.exists():
            return None

        try:
            with open(project_path, 'r', encoding='utf-8') as f:
                project_data = json.load(f)
                self.current_project = name
                return project_data
        except Exception:
            return None

    def save_project(self, project_data: Dict) -> bool:
        """Save project data."""
        if not self.current_project:
            return False

        project_path = self.projects_dir / self.current_project / "project.json"
        project_data["updated_at"] = datetime.now().isoformat()

        try:
            with open(project_path, 'w', encoding='utf-8') as f:
                json.dump(project_data, f, indent=2)
            return True
        except Exception:
            return False

    def get_current_project_path(self) -> Optional[Path]:
        """Get the path to the current project."""
        if not self.current_project:
            return None
        return self.projects_dir / self.current_project

    def save_prompt(self, stage: str, content: str) -> bool:
        """Save a prompt to the project."""
        if not self.current_project:
            return False

        prompt_file = self.projects_dir / self.current_project / "prompts" / f"{stage}.txt"
        try:
            with open(prompt_file, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception:
            return False

    def get_prompts(self, stage: str) -> str:
        """Get saved prompts for a stage."""
        if not self.current_project:
            return ""

        prompt_file = self.projects_dir / self.current_project / "prompts" / f"{stage}.txt"
        if prompt_file.exists():
            try:
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    return f.read()
            except Exception:
                pass
        return ""

    def save_approved_output(self, stage: str, filename: str, content: bytes) -> Dict:
        """Save an approved output (image/video) to the project."""
        if not self.current_project:
            return {"success": False, "error": "No project loaded"}

        stage_dir = self.projects_dir / self.current_project / stage
        stage_dir.mkdir(exist_ok=True)

        output_path = stage_dir / filename
        try:
            with open(output_path, 'wb') as f:
                f.write(content)
            return {"success": True, "path": str(output_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_file(self, stage: str, filename: str, source_path: str) -> Dict:
        """Save a file from source path to project."""
        if not self.current_project:
            return {"success": False, "error": "No project loaded"}

        stage_dir = self.projects_dir / self.current_project / stage
        stage_dir.mkdir(exist_ok=True)

        dest_path = stage_dir / filename
        try:
            shutil.copy2(source_path, dest_path)
            return {"success": True, "path": str(dest_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_stage_files(self, stage: str) -> List[Dict]:
        """Get list of files in a stage directory."""
        if not self.current_project:
            return []

        stage_dir = self.projects_dir / self.current_project / stage
        if not stage_dir.exists():
            return []

        files = []
        for file in stage_dir.iterdir():
            if file.is_file():
                files.append({
                    "name": file.name,
                    "path": str(file),
                    "size": file.stat().st_size,
                    "modified": datetime.fromtimestamp(file.stat().st_mtime).isoformat()
                })
        return sorted(files, key=lambda x: x.get("modified", ""), reverse=True)

    def update_stage_progress(self, stage: str, completed: int, total: int):
        """Update progress for a stage."""
        if not self.current_project:
            return

        project_data = self.load_project(self.current_project)
        if project_data and "stages" in project_data:
            project_data["stages"][stage] = {
                "completed": completed,
                "total": total
            }
            self.save_project(project_data)

    def update_variables(self, variables: Dict):
        """Update project variables."""
        if not self.current_project:
            return

        project_data = self.load_project(self.current_project)
        if project_data:
            project_data["variables"] = variables
            self.save_project(project_data)

    def delete_project(self, name: str) -> bool:
        """Delete a project."""
        project_path = self.projects_dir / name
        if not project_path.exists():
            return False

        try:
            shutil.rmtree(project_path)
            if self.current_project == name:
                self.current_project = None
            return True
        except Exception:
            return False

    def save_project_state(self, name: str, state_data: Dict, project_path_str: str = None) -> Dict:
        """Save full project state (charData, locationData, sceneData, etc.)."""
        if project_path_str:
            project_path = Path(project_path_str)
        else:
            project_path = self.projects_dir / name
        if not project_path.exists():
            return {"success": False, "error": "Project path not found"}
        state_file = project_path / "project_state.json"
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, indent=2, default=str)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def load_project_state(self, name: str, project_path_str: str = None) -> Optional[Dict]:
        """Load full project state from disk."""
        if project_path_str:
            state_file = Path(project_path_str) / "project_state.json"
        else:
            state_file = self.projects_dir / name / "project_state.json"
        if not state_file.exists():
            return None
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def export_project(self, name: str, export_path: str) -> bool:
        """Export project as ZIP."""
        import zipfile
        project_path = self.projects_dir / name
        if not project_path.exists():
            return False

        try:
            with zipfile.ZipFile(export_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(project_path):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(project_path)
                        zipf.write(file_path, arcname)
            return True
        except Exception:
            return False