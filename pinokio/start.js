module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        venv: "D:\\Pinokio_new\\api\\ultimate-ai-film-studio\\env",
        path: "D:\\Pinokio_new\\api\\ultimate-ai-film-studio\\app",
        message: [
          "python main.py",
        ],
        on: [{
          "event": "/(http:\\/\\/\\S+)/",
          "done": true
        }]
      }
    },
    {
      method: "local.set",
      params: {
        url: "{{input.event[0]}}"
      }
    }
  ]
}