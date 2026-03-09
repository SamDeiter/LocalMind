# Personal AI Coding Assistant — "LocalMind"

A fully local, private AI coding and work assistant powered by Ollama + Qwen2.5-Coder, with a polished web UI. Hosted on GitHub for easy sharing and updates.

## User Review Required

> [!IMPORTANT]
> **Project Name:** I'm calling it "LocalMind" for now — happy to change it to whatever you want.

> [!IMPORTANT]
> **Model Choice:** Starting with **Qwen2.5-Coder 32B** as default. Ollama lets you pull additional models later with one command if you want to experiment.

> [!IMPORTANT]
> **Ollama Installation:** The install script will download and install Ollama automatically. Ollama is free, open-source, and runs as a background service on Windows. It stores models in `%USERPROFILE%\.ollama`. The uninstall script will give you the option to remove models too.

---

## Proposed Changes

### GitHub Repository

#### [NEW] GitHub Repo: `SamDeiter/LocalMind`

- Public repo with README, LICENSE (MIT), and `.gitignore`
- Clear install instructions in README so anyone can clone and use it

---

### Install / Uninstall Scripts

#### [NEW] `install.ps1`

One-command setup script that:

1. Checks if Ollama is installed → downloads & installs if not
2. Pulls `qwen2.5-coder:32b` model (~20GB download, one-time)
3. Installs Python dependencies (`pip install -r requirements.txt`)
4. Creates a desktop shortcut to launch the assistant
5. Prints a success message with URL to open

#### [NEW] `uninstall.ps1`

Clean removal script that:

1. Stops the Ollama service
2. Optionally removes downloaded models (reclaim ~20GB)
3. Removes the Python virtual environment
4. Removes the desktop shortcut
5. Does **NOT** delete the project folder itself (per your rules)

#### [NEW] `start.ps1`

Quick-launch script:

1. Starts Ollama service if not running
2. Launches the Python backend
3. Opens the web UI in your default browser

---

### Python Backend

#### [NEW] `backend/server.py`

FastAPI server that:

- Proxies chat requests to Ollama's local API
- Streams responses via Server-Sent Events (SSE) for real-time typing effect
- Manages conversation history in SQLite
- Lists available models from Ollama

#### [NEW] `backend/requirements.txt`

- `fastapi`, `uvicorn`, `httpx`, `sqlite3` (stdlib)

---

### Web UI (Frontend)

#### [NEW] `frontend/index.html`

Single-page app (no build step required) with:

- **Chat interface** — markdown rendering + code syntax highlighting (highlight.js)
- **Model selector** dropdown — switch between any installed Ollama models
- **System prompt editor** — customize the assistant's personality
- **Conversation sidebar** — browse past conversations
- **Dark mode** — sleek, modern design with glassmorphism
- **Copy code button** — one-click copy on code blocks
- **Responsive** — works on desktop and your Pixel 9 Pro Fold's browser

#### [NEW] `frontend/style.css`

Premium dark-mode design system with:

- Custom color palette, gradients, animations
- Code block styling with language labels
- Smooth streaming text animation

#### [NEW] `frontend/app.js`

Client-side logic:

- WebSocket/SSE connection to backend for streaming
- Markdown + code rendering
- Conversation management (new, switch, delete)
- Model switching
- Local storage for preferences

---

### Documentation

#### [NEW] `README.md`

- Project description and screenshots
- One-line install command
- Usage guide
- Model recommendations for different hardware
- Uninstall instructions
- Contributing guide

---

## Project Structure

```
LocalMind/
├── install.ps1           # One-command setup
├── uninstall.ps1         # Clean removal
├── start.ps1             # Quick launch
├── README.md
├── LICENSE
├── .gitignore
├── backend/
│   ├── server.py         # FastAPI backend
│   └── requirements.txt
└── frontend/
    ├── index.html        # Chat UI
    ├── style.css         # Dark mode design
    └── app.js            # Client logic
```

## Verification Plan

### Automated Tests

1. **Install script test:** Run `install.ps1` and verify:
   - Ollama is installed and running (`ollama --version`)
   - Model is pulled (`ollama list` shows `qwen2.5-coder:32b`)
   - Python deps installed (`pip list` shows fastapi, uvicorn, httpx)

2. **Backend test:** Start `server.py` and verify:
   - `http://localhost:8000/api/models` returns list of models
   - `http://localhost:8000/api/chat` returns streamed response
   - Conversation history is persisted in SQLite

3. **Uninstall script test:** Run `uninstall.ps1` and verify:
   - Ollama service is stopped
   - Desktop shortcut is removed
   - Project folder still exists (not deleted)

### Manual Verification

1. **End-to-end flow:** Open the web UI, type a coding question (e.g., "write a Python function to reverse a linked list"), verify:
   - Response streams in real-time
   - Code is syntax-highlighted
   - Copy button works
   - Response is actually good code
2. **Model switching:** Change model in dropdown, send a message, verify the response comes from the new model
3. **Mobile test:** Open the URL on your Pixel 9 Pro Fold browser, verify it's usable
4. **GitHub push:** Verify repo is live at `github.com/SamDeiter/LocalMind`
