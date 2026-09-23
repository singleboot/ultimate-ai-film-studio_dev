# -*- coding: utf-8 -*-
"""Pytest wrapper for the production smoke gate (app/smoke_check.py).

Run:  env/Scripts/python.exe -m pytest tests/test_smoke.py -v
The heavy checks run once per session and are shared across test functions.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))

import smoke_check  # noqa: E402


@pytest.fixture(scope="session")
def report():
    return smoke_check.run_full(skip_net=True)


def test_all_python_files_parse(report):
    assert report["python_syntax"] == [], \
        "Syntax errors: " + "; ".join(f"{e['file']}: {e['error']}" for e in report["python_syntax"])


def test_workflows_have_output_nodes(report):
    errs = report["workflows"]["errors"]
    assert errs == [], "Broken workflows: " + "; ".join(f"{e['file']}: {e['error']}" for e in errs)


def test_all_workflow_models_installed(report):
    errs = report["models"]["errors"]
    assert errs == [], "Missing models: " + "; ".join(f"{e['file']}: {e['error']}" for e in errs)


def test_project_state_save_is_atomic_and_recoverable(tmp_path):
    """Atomic saves + .bak recovery for project_state.json (Phase 1 hardening)."""
    from core.project_manager import ProjectManager  # type: ignore

    pm = ProjectManager(projects_dir=str(tmp_path))
    name = "smoke-test-project"
    project_dir = tmp_path / name
    project_dir.mkdir()

    # First save; second save leaves a .bak of the first good state
    assert pm.save_project_state(name, {"a": 1}, str(project_dir))["success"]
    assert pm.save_project_state(name, {"a": 2}, str(project_dir))["success"]

    bak = project_dir / "project_state.json.bak"
    assert bak.exists(), "expected .bak of previous good state"
    assert json.loads(bak.read_text(encoding="utf-8")) == {"a": 1}

    # Corrupt the primary file -> load must recover from the .bak copy.
    # The bak holds the previous good save, so the recovered state is {a:1}:
    # a crash mid-save costs the last write, never the whole project.
    (project_dir / "project_state.json").write_text("{corrupt!!", encoding="utf-8")
    loaded = pm.load_project_state(name, str(project_dir))
    assert loaded == {"a": 1}, "load should fall back to the previous good .bak state"
