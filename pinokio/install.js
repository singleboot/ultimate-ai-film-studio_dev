module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        message: [
          "cd ..; python -m venv env",
        ]
      }
    },
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "../app",
        message: [
          "uv pip install -r requirements.txt",
        ]
      }
    }
  ]
}