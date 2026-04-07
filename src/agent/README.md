# LocalMind Agent

A local-first AI agent that runs entirely on your machine. No API keys, no cloud calls.

Built on research from 8 papers on small language models, agentic architectures, and memory systems (see `docs/research_summary.md`).

## Hardware Requirements

| Tier | RAM | GPU | Model | Experience |
|------|-----|-----|-------|------------|
| Minimum | 8 GB | CPU-only | Qwen2.5-Coder 7B (Q4) | Functional, slower |
| Recommended | 16 GB | 8 GB VRAM | Qwen2.5-Coder 14B (Q4) | Good for daily use |
| Optimal | 32 GB | 16+ GB VRAM | Qwen2.5-Coder 32B (Q4) | Near-cloud quality |

## Quick Start

### 1. Install Ollama

```bash
# Windows: download from https://ollama.ai
# macOS:
brew install ollama
# Linux:
curl -fsSL https://ollama.ai/install.sh | sh
```

### 2. Pull a Model

```bash
# For 8 GB RAM (minimum):
ollama pull qwen2.5-coder:7b

# For 16 GB RAM (recommended):
ollama pull qwen2.5-coder:14b

# For 32 GB+ RAM (optimal):
ollama pull qwen2.5-coder:32b
```

### 3. Install Dependencies

```bash
pip install httpx
# Optional but recommended:
pip install duckduckgo-search psutil
```

### 4. Run the Agent

```bash
# From the project root:
python -m src.agent.core
```

The agent auto-detects your hardware and selects the best model.

## Architecture

```
src/agent/
├── config.py          # Model registry, hardware detection, settings
├── core.py            # ReAct agent loop — the main entry point
├── tools/
│   ├── base.py        # Tool base class (JSON Schema definitions)
│   ├── file_ops.py    # File read/write/search (sandboxed)
│   ├── shell.py       # Safe shell execution (allowlisted commands)
│   └── web.py         # Web fetch and search (no API keys)
└── memory/
    ├── store.py       # SQLite-backed persistent memory (FTS5)
    ├── context.py     # Context window management + summarization
    └── retrieval.py   # Memory retrieval with recency weighting
```

## How It Works

The agent uses a **ReAct loop** (Reason + Act):

1. **Observe**: receives your message + relevant memories
2. **Think**: the LLM reasons about what to do
3. **Act**: calls a tool (file search, shell command, web fetch)
4. **Observe**: gets the tool result
5. **Repeat** until it has enough information to answer

### Example

```
You: Find all Python files that import requests and check for error handling

Agent: I'll search for Python files importing requests, then check each one.

[tool: file_search] pattern="*.py" content_pattern="import requests"
Found 4 files.

[tool: file_read] reading src/api/client.py...
[tool: file_read] reading src/scraper/fetch.py...

Found 4 files importing requests:
- src/api/client.py ✓ has try/except for RequestException
- src/scraper/fetch.py ✗ no error handling (lines 23, 45)
- scripts/download.py ✗ raw requests.get without error handling
- tests/test_api.py ✓ handled in test assertions

Recommendation: Add try/except for requests.exceptions.RequestException
in fetch.py and download.py.
```

## Model Selection

The agent auto-selects based on your hardware, but you can override:

```python
from src.agent.core import Agent

# Auto-detect (recommended)
agent = Agent()

# Force a specific model
agent = Agent(model="qwen2.5-coder:14b")

# Use llama.cpp instead of Ollama
agent = Agent(model="my-model", ollama_url="http://localhost:8080")
```

## Memory System

Memories persist across sessions in SQLite (`~/LocalMind_Workspace/agent_memory.db`):

- **Semantic**: facts, preferences, instructions
- **Episodic**: conversation history summaries
- **Procedural**: learned tool usage patterns

Memories are auto-retrieved based on relevance to the current query.

## Safety

- File operations are sandboxed to `~/LocalMind_Workspace`
- Shell commands use an allowlist (grep, git, python, etc.)
- Destructive commands (`rm -rf /`, `mkfs`, etc.) are blocked
- Tool execution has timeout enforcement (default: 30s)

## Integration with LocalMind

This agent module is designed to work standalone or as part of the larger LocalMind system. The existing `backend/tools/` system uses a compatible tool interface, so tools can be shared between both.

## Research Papers

See `docs/research_summary.md` for the full analysis of 8 papers that informed this architecture, and `docs/architecture_decision_record.md` for the rationale behind key design choices.
