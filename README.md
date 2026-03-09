# 🧠 LocalMind

**Your private, local AI coding assistant.** Powered by [Ollama](https://ollama.com) + [Qwen 2.5 Coder](https://huggingface.co/Qwen/Qwen2.5-Coder-32B-Instruct) — runs entirely on your machine. No API keys, no cloud, no cost.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Ollama](https://img.shields.io/badge/Ollama-Local%20AI-purple)
![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ Features

- 💬 **ChatGPT-like interface** — sleek dark mode UI with markdown & code highlighting
- 🔒 **100% private** — nothing leaves your machine, ever
- 🧑‍💻 **Built for coders** — syntax highlighting, copy-to-clipboard, language labels
- 🔄 **Model switching** — swap between any Ollama model on the fly
- 📝 **Conversation history** — saved locally in SQLite
- 🎨 **Customizable** — system prompts, model preferences, and more
- 📱 **Responsive** — works on desktop and mobile browsers

## 📋 Requirements

- **Windows 10/11**
- **Python 3.10+** ([download](https://python.org))
- **~20GB disk space** for the AI model (one-time download)

## 🚀 Quick Install

```powershell
# 1. Clone the repo
git clone https://github.com/SamDeiter/LocalMind.git
cd LocalMind

# 2. Run the installer (installs Ollama, downloads the AI model, sets up Python)
.\install.ps1
```

That's it! The installer handles everything:

- ✅ Installs [Ollama](https://ollama.com) (if not already installed)
- ✅ Downloads the Qwen 2.5 Coder 32B model (~20GB, one-time)
- ✅ Sets up a Python virtual environment
- ✅ Creates a desktop shortcut

## 🏃 Usage

**Option 1:** Double-click the **LocalMind** shortcut on your Desktop.

**Option 2:** Run from terminal:

```powershell
.\start.ps1
```

Then open **http://localhost:8000** in your browser.

## 💡 Tips

- Press **Shift+Enter** for multi-line input
- Press **Ctrl+N** for a new conversation
- Click the **⚙️ Settings** button to customize the system prompt
- Use the **model dropdown** to switch between installed models

### Installing Additional Models

```powershell
# Smaller, faster model for quick tasks
ollama pull qwen2.5-coder:7b

# General-purpose large model
ollama pull llama3.1:70b

# Fast general-purpose model
ollama pull mistral:7b
```

Any model you pull will automatically appear in the model dropdown.

## 🗑️ Uninstall

```powershell
.\uninstall.ps1
```

The uninstaller will:

- Stop the Ollama service
- Optionally remove downloaded models (~20GB)
- Remove the Python virtual environment
- Remove the desktop shortcut
- **Keep the project folder** (you can reinstall later)

## 🏗️ Project Structure

```
LocalMind/
├── install.ps1           # One-command setup
├── uninstall.ps1         # Clean removal
├── start.ps1             # Quick launch
├── README.md             # This file
├── LICENSE               # MIT License
├── backend/
│   ├── server.py         # FastAPI backend (proxies to Ollama)
│   └── requirements.txt  # Python dependencies
└── frontend/
    ├── index.html        # Chat UI
    ├── style.css         # Dark mode design system
    └── app.js            # Client-side logic
```

## 🛠️ Development

Want to hack on LocalMind? Just open the project folder in your IDE:

```powershell
# Start the backend in development mode
cd backend
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

The frontend is vanilla HTML/CSS/JS — no build step required. Just edit and refresh.

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

**Made with 🧠 by [Sam Deiter](https://github.com/SamDeiter)**
