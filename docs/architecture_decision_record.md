# Architecture Decision Record: LocalMind Agent

## ADR-001: Model Selection

### Decision
Default to **Qwen2.5-Coder** family, with automatic tier selection based on hardware.

### Context
Papers 1 (arXiv:2510.03847), 2 (arXiv:2506.02153), and 3 (arXiv:2512.15943) all evaluate SLMs for tool calling. Qwen2.5-Coder consistently outperforms same-size alternatives (Llama, Phi, DeepSeek) on structured output and function calling benchmarks.

### Options Considered

| Model | Params | Tool Calling | Reasoning | Availability |
|-------|--------|-------------|-----------|-------------|
| **Qwen2.5-Coder-7B** | 7.6B | Best in class | Good | Ollama ✓ |
| Llama-3.1-8B | 8B | Good | Good | Ollama ✓ |
| Phi-4-14B | 14B | Good | Best in class | Ollama ✓ |
| DeepSeek-R1-7B | 7.6B | Weak | Excellent | Ollama ✓ |
| **Qwen2.5-Coder-14B** | 14.7B | Excellent | Very good | Ollama ✓ |
| **Qwen2.5-Coder-32B** | 32.5B | Near-GPT-4 | Excellent | Ollama ✓ |

### Rationale
- Qwen2.5-Coder-7B achieves 72% on BFCL (Berkeley Function Calling Leaderboard) — competitive with 70B general models
- After domain fine-tuning (Paper 3), 7B reaches 78% — matching GPT-4's 76% on single-turn calls
- The model family scales cleanly: 7B → 14B → 32B with consistent improvement
- All sizes are available on Ollama with quantized variants

### Consequences
- Users with <8GB RAM can still run the agent (7B Q4 model)
- The auto-selection logic in `config.py` makes this transparent
- Alternative: Phi-4 for reasoning-heavy, non-tool-calling tasks

---

## ADR-002: Serving Stack — Ollama

### Decision
Default to **Ollama** as the model serving backend, with llama.cpp as a secondary option.

### Context
Paper 1 compares serving stacks: Ollama, vLLM, SGLang, TensorRT-LLM.

### Options Considered

| Backend | Setup | Throughput | Tool Calling | Local Focus |
|---------|-------|-----------|-------------|------------|
| **Ollama** | 1 command | Good | Native support | Built for local ✓ |
| vLLM | Complex | Best | Via guided decoding | Server-oriented |
| SGLang | Complex | Very good | Best structured gen | Research-oriented |
| TensorRT-LLM | Very complex | Highest | Manual | NVIDIA-only |
| llama.cpp | Moderate | Good | Via grammar | Lightweight ✓ |

### Rationale
- Ollama is the only backend that installs in one command on all platforms
- Native tool calling support for Qwen2.5, Llama 3.x, and other models
- Model management (pull, list, remove) built in
- GPU detection and offloading automatic
- The existing LocalMind backend already uses Ollama — zero migration cost

### Consequences
- Slightly lower throughput than vLLM for batch scenarios (not relevant for single-user local agent)
- For advanced users who want vLLM/SGLang, the `AGENT_BACKEND` env var allows switching
- llama.cpp support added as fallback for minimal-dependency environments

### Future
- If guided decoding becomes critical (schema validity < 80%), consider SGLang
- vLLM integration for multi-agent parallel execution scenarios

---

## ADR-003: Agent Architecture — Single ReAct Loop

### Decision
Use a **single-agent ReAct loop** (Reason + Act) rather than multi-agent orchestration.

### Context
Papers 4 (arXiv:2512.08769) and 6 (arXiv:2512.03571) discuss orchestration patterns. Paper 4 covers sequential, parallel, and conditional workflows. Paper 6 introduces branchpoint tracking.

### Options Considered
1. **Single ReAct agent**: One LLM loop that reasons and calls tools iteratively
2. **Multi-agent**: Separate planner, executor, and critic agents
3. **Workflow engine**: Pre-defined workflow graphs with conditional routing
4. **EnCompass tree search**: Branchpoint tracking with backtracking

### Rationale
- Single ReAct is the simplest pattern that works for 90% of tasks
- Multi-agent requires multiple LLM calls per step — too expensive for local models
- Workflow engines are rigid — poor fit for open-ended user requests
- EnCompass tree search is promising but adds significant complexity
- Paper 4 explicitly recommends: "Start with ReAct, add complexity only when needed"

### Consequences
- Limited to sequential tool execution (no parallel fan-out)
- Max iterations cap prevents infinite loops
- Simple to understand, debug, and extend

### Future Enhancements
- Add branchpoint tracking (Paper 6) for smarter error recovery
- Add parallel tool execution for independent subtasks
- Consider planner/executor split for complex multi-step tasks

---

## ADR-004: Memory System — SQLite + FTS5

### Decision
Use **SQLite with FTS5** full-text search for persistent memory, organized by the episodic/semantic/procedural taxonomy.

### Context
Paper 8 (arXiv:2512.13564) defines a memory taxonomy and architecture. Paper 7 (arXiv:2512.16301) adds adaptation patterns (reinforcement learning from tool execution).

### Options Considered

| Approach | Retrieval Quality | Dependencies | Complexity |
|----------|-----------------|-------------|-----------|
| **SQLite + FTS5** | Good | Zero (stdlib) | Low |
| SQLite + embeddings | Better | sentence-transformers, torch | High |
| ChromaDB | Best | chromadb, torch | Medium |
| File-based JSON | Basic | Zero | Very low |
| Redis | Good | redis-server | Medium |

### Rationale
- FTS5 is built into Python's sqlite3 — zero additional dependencies
- For a local agent with hundreds to low-thousands of memories, FTS5 retrieval quality is sufficient
- Embedding-based retrieval adds 500MB+ of dependencies (sentence-transformers + torch)
- Paper 8 notes that for personal agent memories (not large document corpora), keyword-based retrieval with recency weighting performs within 10% of embedding-based approaches
- SQLite WAL mode gives good concurrent read performance

### Memory Taxonomy (from Paper 8)
- **Episodic**: Conversation history, auto-saved after each interaction
- **Semantic**: User facts, preferences, instructions — high-value, long-lived
- **Procedural**: Tool usage patterns, learned workflows — reinforced by success/failure

### Retrieval Strategy
- Combined score: `text_relevance × 0.7 + recency × 0.3`
- Access counting: frequently accessed memories are presumed more valuable
- Relevance reinforcement (Paper 7): boost memories that lead to successful tool executions

### Consequences
- No semantic similarity search (can't find "automobile" when searching "car")
- Mitigated by saving memories with multiple keywords/synonyms
- Future: add optional embedding layer for users with GPU headroom

---

## ADR-005: Tool Calling — JSON Schema + Text Extraction Fallback

### Decision
Use **Ollama native tool calling** with JSON Schema definitions, falling back to **regex-based text extraction** for models that don't support native calls.

### Context
Paper 1 and Paper 3 evaluate tool calling approaches. Paper 3 shows that schema enforcement improves accuracy by 20-30%.

### Approach
1. Register all tools with JSON Schema parameter definitions
2. Pass schemas to Ollama via the `tools` parameter in `/api/chat`
3. If the model returns native `tool_calls`, use them directly
4. If the model embeds tool calls in text, extract using:
   a. Fenced blocks: ````tool_call\n{...}\n````
   b. Bare JSON objects matching `{"name": "...", "arguments": {...}}`
   c. Balanced-brace JSON extraction for malformed output

### Rationale
- Ollama's native tool calling works well for Qwen2.5 and Llama 3.x
- Smaller/older models may not support native calls but can still produce JSON in text
- The dual approach (native + text extraction) maximizes model compatibility
- JSON Schema validation before execution prevents malformed tool calls from causing errors

### Safety Measures
- Validate tool name exists in registry before execution
- Validate arguments match expected types from schema
- Timeout enforcement per tool call
- Max iterations cap prevents infinite tool call loops

---

## ADR-006: Safety Model — Defense in Depth

### Decision
Implement **multiple independent safety layers** rather than relying on any single mechanism.

### Context
Paper 4 (arXiv:2512.08769) dedicates a section to safety/governance in production agents. Key principle: "No single safety mechanism is sufficient."

### Safety Layers

| Layer | Mechanism | Protects Against |
|-------|-----------|-----------------|
| **Workspace sandbox** | All file ops resolve within ~/LocalMind_Workspace | Arbitrary file access |
| **Command allowlist** | Shell tool only runs listed commands | Dangerous system commands |
| **Pattern blocklist** | Regex blocks `rm -rf /`, `mkfs`, etc. | Destructive patterns |
| **Tool timeout** | 30s default, 120s max | Runaway processes |
| **Iteration cap** | Max 10 agent loop iterations | Infinite loops |
| **Output truncation** | Tool output capped at 5000-10000 chars | Context window flooding |

### Rationale
- Local agents have direct system access — safety must be built in, not bolted on
- Allowlist > blocklist for shell commands (fail-closed rather than fail-open)
- Timeouts prevent a single bad tool call from hanging the entire agent

### Future
- Add human-in-the-loop approval for destructive file operations
- Consider capability-based security (tools request specific permissions)
- Log all tool executions for audit trail
