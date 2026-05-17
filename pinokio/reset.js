module.exports = {
  run: [
    {
      method: "shell.run",
      params: {
        message: [
          "Remove-Item -Recurse -Force env",
        ]
      }
    }
  ]
}