# Contributing to LocalMind

Thank you for your interest in contributing to LocalMind! This guide will help you get started.

## 🏗️ Project Architecture

```
LocalMind/
├── backend/
│   ├── server.py          # App shell: DB, config, router registration
│   ├── routes/            # API endpoint routers (modular)
│   │   ├── chat.py        # Main agent loop + streaming + tool calling
│   │   ├── conversations.py # Conversation CRUD + export
│   │   ├── memory.py      # Memory toggle, list, delete
│   │   ├── files.py       # Sandboxed file browser
│   │   ├── tools.py       # Code execution, approvals, dependencies
│   │   └── documents.py   # RAG document upload/index/delete
│   ├── tools/             # AI tool implementations (auto-discovered)
│   ├── model_router.py    # Multi-model routing (7B vs 32B)
│   └── gemini_client.py   # Optional cloud model fallback
├── frontend/              # Vanilla HTML/CSS/JS (no build step)
├── scripts/               # Utility scripts
│   ├── bump_build.py      # Auto-increment version/build number
│   ├── generate_docs.py   # Generate API docs from docstrings
│   └── create_shortcut.ps1 # Create desktop shortcut (Windows)
├── docs/                  # Project documentation
├── run.py                 # Server launcher (dev/prod modes)
└── version.json           # Build number + semver tracking
```

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com/) installed and running
- At least one model pulled (e.g., `ollama pull qwen2.5-coder:7b`)

### Setup
```bash
# Clone the repo
git clone https://github.com/SamDeiter/LocalMind.git
cd LocalMind

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For testing

# Create .env from example
copy .env.example .env
# Edit .env with your API keys (if using Gemini cloud features)
```

### Running
```bash
# Development mode (auto-reload, debug logging)
python run.py

# Production mode (multi-worker, INFO logging)
python run.py --prod

# Run tests
python run.py --test
```

## 📝 Code Standards

### Docstrings
- Every module must have a module-level docstring explaining its purpose
- Every function/method must have a docstring explaining **what** and **why**
- Use Google-style docstrings for args/returns when helpful

### Comments
- Comment the **why**, not the **what**
- Section dividers: `# ── Section Name ─────────`
- Inline comments for non-obvious logic only

### Generating API Docs
```bash
python scripts/generate_docs.py
# Opens at docs/api/backend/index.html
```

## 🔒 Security Rules

1. **NEVER** hardcode API keys — use `.env` only
2. **NEVER** commit `.env` files
3. Run the security scan before every push:
   ```bash
   python -c "import re,os;[print(f'⚠ {p}:{i}') for p in ['backend','scripts'] for r,_,fs in os.walk(p) for f in fs if f.endswith('.py') for i,l in enumerate(open(os.path.join(r,f),errors='replace'),1) if re.search(r'AIza|sk-[A-Z]',l)]"
   ```

## 🧪 Testing

```bash
# Run all tests
python run.py --test

# Run specific test file
python -m pytest tests/test_chat.py -v

# Run with coverage (when configured)
python -m pytest tests/ --cov=backend
```

## 📦 Adding a New Tool

1. Create `backend/tools/your_tool.py`
2. Subclass `BaseTool` from `backend/tools/base.py`
3. Implement `execute()` method
4. The tool is auto-discovered by `ToolRegistry` — no registration needed!

## 📦 Adding a New Router

1. Create `backend/routes/your_router.py`
2. Create an `APIRouter` with prefix and tags
3. Add a `configure()` function for dependency injection
4. Import and mount in `server.py`

## 🏷️ Versioning

- Version is tracked in `version.json`
- Build number auto-increments on each server start
- Bump semver manually: `python scripts/bump_build.py --patch|--minor|--major`
