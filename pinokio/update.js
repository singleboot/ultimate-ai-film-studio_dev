module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "app",
        message: [
          "uv pip install --upgrade fastapi uvicorn jinja2 requests pillow pydantic",
        ]
      }
    }
  ]
}