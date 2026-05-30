import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Any


class TemplateManager:
    def __init__(self, templates_dir: str = None):
        if templates_dir is None:
            base_dir = Path(__file__).parent.parent
            templates_dir = base_dir / "config" / "templates"
        self.templates_dir = Path(templates_dir)
        self.templates: Dict[str, Any] = {}
        self._load_templates()

    def _load_templates(self):
        """Load all template JSON files from the templates directory."""
        if not self.templates_dir.exists():
            return

        for template_file in self.templates_dir.glob("*.json"):
            try:
                with open(template_file, 'r', encoding='utf-8') as f:
                    template_data = json.load(f)
                    template_name = template_file.stem
                    self.templates[template_name] = template_data
            except Exception as e:
                print(f"Error loading template {template_file}: {e}")

    def get_templates(self) -> List[Dict]:
        """Get list of all available templates."""
        return [
            {
                "name": name,
                "description": data.get("description", ""),
                "category": data.get("category", "general"),
                "variables": data.get("variables", [])
            }
            for name, data in self.templates.items()
        ]

    def get_template(self, name: str) -> Optional[Dict]:
        """Get a specific template by name."""
        return self.templates.get(name)

    def get_template_stages(self, name: str) -> List[Dict]:
        """Get all stages for a template."""
        template = self.get_template(name)
        if template:
            return template.get("stages", [])
        return []

    def get_template_variables(self, name: str) -> List[Dict]:
        """Get variables for a template."""
        template = self.get_template(name)
        if template:
            return template.get("variables", [])
        return []

    def render_prompt(self, template_name: str, stage_name: str, variables: Dict) -> str:
        """Render a prompt template with given variables."""
        template = self.get_template(template_name)
        if not template:
            raise ValueError(f"Template '{template_name}' not found")

        stages = template.get("stages", [])
        stage = None
        for s in stages:
            if s.get("name") == stage_name:
                stage = s
                break

        if not stage:
            raise ValueError(f"Stage '{stage_name}' not found in template '{template_name}'")

        prompt_template = stage.get("prompt_template", "")
        return self._substitute_variables(prompt_template, variables)

    def _substitute_variables(self, template: str, variables: Dict) -> str:
        """Replace {{VARIABLE}} placeholders with actual values."""
        result = template
        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            result = result.replace(placeholder, str(value))
        return result

    def save_template(self, name: str, template_data: Dict):
        """Save a new template or update existing one."""
        self.templates[name] = template_data
        file_path = self.templates_dir / f"{name}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(template_data, f, indent=2)

    def delete_template(self, name: str) -> bool:
        """Delete a template."""
        if name not in self.templates:
            return False

        file_path = self.templates_dir / f"{name}.json"
        if file_path.exists():
            file_path.unlink()

        del self.templates[name]
        return True

    def export_template(self, name: str) -> Optional[str]:
        """Export a template as JSON string."""
        template = self.get_template(name)
        if template:
            return json.dumps(template, indent=2)
        return None

    def import_template(self, json_str: str) -> bool:
        """Import a template from JSON string."""
        try:
            template_data = json.loads(json_str)
            name = template_data.get("name")
            if not name:
                return False
            self.save_template(name, template_data)
            return True
        except Exception:
            return False