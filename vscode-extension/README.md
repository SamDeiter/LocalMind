# LocalMind VS Code Extension

Connect VS Code to your local LocalMind AI task worker.

## Features

- Send selected code to the AI for analysis, explanation, or refactoring
- Ask freeform questions from the command palette
- Register your workspace as a LocalMind project
- Status bar indicator showing server connectivity
- Auto-reconnect with 30-second health polling

## Requirements

- LocalMind server running locally (default: `http://localhost:8000`)
- VS Code 1.85.0 or later

## Install from Source

```bash
cd vscode-extension
npm install
npm run compile
```

Then press **F5** in VS Code to launch an Extension Development Host, or package with:

```bash
npx vsce package
code --install-extension localmind-0.1.0.vsix
```

## Configuration

| Setting                 | Default                  | Description                         |
|-------------------------|--------------------------|-------------------------------------|
| `localmind.serverUrl`   | `http://localhost:8000`  | LocalMind server URL                |
| `localmind.autoConnect` | `true`                   | Auto-connect on startup             |

## Commands

| Command                              | Keybinding              | Description                        |
|--------------------------------------|-------------------------|------------------------------------|
| LocalMind: Send Selection to AI      | `Ctrl+Shift+L`          | Send selected text to the AI       |
| LocalMind: Ask a Question            |                         | Open input box to ask the AI       |
| LocalMind: Register Current Project  |                         | Register workspace with LocalMind  |
| LocalMind: Show Status               |                         | Show server status and models      |
