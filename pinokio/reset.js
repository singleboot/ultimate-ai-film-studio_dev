module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        message: [
          "cd ..; Remove-Item -Recurse -Force env",
        ]
      }
    }
  ]
}