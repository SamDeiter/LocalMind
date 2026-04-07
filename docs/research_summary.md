# Research Summary: Building Local AI Agents

Analysis of 8 papers on small language models, agentic architectures, memory systems, and tool calling — with specific implications for the LocalMind agent.

---

## Paper 1: SLMs for Agentic Systems Survey
**arXiv:2510.03847** — Raghav Sharma, Manan Mehta (Oct 2025)

**Key takeaway**: Small language models (1–12B parameters) are viable for agentic tool calling when paired with guided decoding (XGrammar, Outlines) and strict JSON Schema enforcement. SLMs match or surpass LLMs on tool use at **10–100x lower token cost**. Evaluation via BFCL v3/v4 and StableToolBench.

### Architectural Patterns

- **SLM-default routing**: A 3B-8B SLM is the **first model to touch every request** — tool selection, entity extraction, JSON generation. Confidence scoring with verifier cascades determines when to escalate to LLM fallback. ~80% of agentic tasks are simple extraction/routing/tool calling.
- **Guided decoding**: XGrammar (context-independent token prechecking) and Outlines (post-token incremental validation via FSM/pushdown automata). With guided decoding, SLMs achieve **schema validity rates above 99%**.
- **Validator-first tool execution**: After guided decoding produces structurally valid output, semantic validators check correctness, looping or escalating to human-in-the-loop on failure.
- **Serving stacks compared**:
  - **vLLM**: PagedAttention, continuous batching, supports INT8/INT4/GPTQ/AWQ/FP8. Best for high-concurrency interactive apps.
  - **SGLang**: RadixAttention (caches repeated prompt patterns in KV-cache). Best for structured output generation and agentic workflows needing precise control.
  - **TensorRT-LLM**: Maximum NVIDIA performance — 8% faster than vLLM at 1 request, 13% faster at 50 concurrent. Requires engine compilation. NVIDIA-only.
  - **Ollama** (not in paper, our choice): Easiest local setup, native tool calling, GPU auto-offload. Best for single-user local deployment.

### Model Recommendations (from paper + BFCL benchmarks)

| Model | Params | Tool Calling Accuracy | Best For |
|-------|--------|-----------------------|----------|
| **Phi-4-Mini** | 3.8B | **>=97% on BFCL-v4** | Function calling — matches 70B models |
| Qwen-2.5-7B | 7.6B | High (strong tool calling) | Balanced code + tool calling |
| Gemma-2-9B | 9B | Good | General mid-range |
| Llama-3.2-3B | 3B | Moderate | Lightweight routing/extraction |
| Ministral-8B | 8B | Good | Mid-range agentic |
| xLAM-2-3b-fc-r | 3B | 65.74% overall (81% live) | Compact function calling |

### Hardware Benchmarks (from paper)

| GPU | Model | Quantization | Throughput |
|-----|-------|-------------|------------|
| RTX 4090 | Llama-2-7B | INT4 AWQ | 52 → **194 tok/s** |
| NVIDIA T4 | Various | 4-bit GPTQ | -41% VRAM, -82% speed (dequant overhead) |
| A100 80GB | 175B model | 3-bit | ~14.1 tok/s |

**Practical quantization guidance**: W4A16 (4-bit weights, 16-bit activations) when latency/memory dominate; W8A8 when server throughput is the goal.

### Evaluation Metrics
- **CPS (cost per successful task)**: Total inference cost divided by successful task completions
- **Schema Validity Rate**: % of tool calls that parse as valid JSON matching the schema
- **Executable Call Rate**: % of tool calls that execute without runtime errors
- **p50/p95 Latency**: Median and tail latencies for time-to-first-token
- **Energy per Request**: Important for edge/mobile deployment
- Paper finding: guided decoding libraries (XGrammar, Outlines) paired with strict JSON Schema raise schema validity from ~60% to ~95% for 7B models

### Specific Models Evaluated
Phi-4-Mini, Qwen-2.5-7B, Gemma-2-9B, Llama-3.2-1B/3B, Ministral-3B/8B, Apple on-device 3B, DeepSeek-R1-Distill

### Code-Relevant Insights
- Ollama's native tool calling via `/api/chat` with `tools` parameter works well for Qwen2.5 and Llama 3.x
- For models without native tool calling, text-based extraction with regex is reliable at 7B+ scale
- Schema enforcement via JSON mode is critical — without it, 7B models produce invalid JSON ~40% of the time
- SLM-default/LLM-fallback with "uncertainty-aware routing and verifier cascades" is the recommended production pattern

---

## Paper 2: Small Language Models are the Future of Agentic AI (NVIDIA)
**arXiv:2506.02153** — Peter Belcak, Greg Heinrich, Diao, Fu, Dong, Muralidharan, Lin, Molchanov

**Key takeaway**: NVIDIA proposes a 6-step LLM-to-SLM conversion algorithm. Case studies show **60–70% of LLM queries in real agent systems are replaceable with SLMs** (MetaGPT: ~60%, Cradle GUI automation: ~70%). Serving 7B SLMs is **10–30x cheaper** than 70B+ LLMs in latency, energy, and FLOPs.

### The 6-Step SLM Transition Methodology
1. **S1 — Secure Usage Data Collection**: Log all non-HCI agent calls — input prompts, output responses, tool call contents. Encrypted pipelines with role-based access controls.
2. **S2 — Data Curation & Filtering**: Collect **10k–100k examples minimum**. Remove PII/PHI with automated tools, paraphrase sensitive data to preserve information while obscuring entities.
3. **S3 — Task Clustering**: Apply unsupervised clustering on prompts and agent actions. Common clusters: intent recognition, data extraction, document summarization, tool-specific code generation.
4. **S4 — SLM Selection**: Evaluate candidates on instruction following, reasoning, context window, benchmark scores, licensing, and deployment footprint (memory/compute).
5. **S5 — Specialized Fine-tuning**: LoRA/QLoRA for parameter efficiency. **Full fine-tuning for SLMs requires only a few GPU-hours** (vs. weeks for LLMs). Knowledge distillation recommended to transfer LLM nuances. 10k–100k examples sufficient.
6. **S6 — Iteration & Refinement**: Retrain periodically with new data to maintain performance and adapt to evolving usage patterns.

### Router Design Patterns
- **Heterogeneous agentic systems**: Every agent code invocation can choose any language model — different models for different subtasks
- **Language Model Agency**: LLM acts as orchestrator + human interface
- **Code Agency**: Dedicated controller code orchestrates; LM fills HCI role optionally
- Our existing `_estimate_complexity()` implements the complexity-based routing; `LoadMonitor.pick_best_model()` implements loaded-model reuse

### Key Benchmark Findings from the Paper
- **Phi-2 (2.7B)**: Matches 30B models on commonsense reasoning/code generation at **15x faster** execution
- **Phi-3 Small (7B)**: Matches/exceeds 70B model code generation; parity on language understanding
- **NVIDIA Nemotron-H (2/4.8/9B)**: Code-generation accuracy comparable to dense 30B LLMs at an **order-of-magnitude fraction** of inference FLOPs
- **Hymba-1.5B**: **3.5x greater token throughput** than comparable transformers; outperforms 13B models on instruction following
- **xLAM-2-8B**: State-of-the-art tool calling, surpassing GPT-4o and Claude 3.5
- **DeepSeek-R1-Distill-Qwen-7B**: Outperforms Claude-3.5-Sonnet and GPT-4o on reasoning benchmarks

### LoRA/QLoRA Adaptation
- Full-parameter fine-tuning for SLMs needs only a few GPU-hours (vs. weeks for LLMs)
- LoRA/QLoRA reduces computational costs further for resource-constrained environments
- Knowledge distillation: train specialist SLM to mimic LLM outputs on task-specific datasets

---

## Paper 3: SLMs for Efficient Agentic Tool Calling
**arXiv:2512.15943** — Polaris Jhandi, Owais Kazi, Shreyas Subramanian, Neel Sendas

**Key takeaway**: A fine-tuned **350M parameter model** (facebook/opt-350m) achieves **77.55% pass rate** on ToolBench evaluation — massively outperforming ChatGPT-CoT (26%), ToolLLaMA-DFS (30.18%), and ToolLLaMA-CoT (16.27%). Training data quality matters more than model size.

### Fine-Tuning Recipe (from the paper)

- **Base model**: facebook/opt-350m (intentionally tiny to prove the point)
- **Method**: Supervised Fine-Tuning (SFT) via Hugging Face TRL trainer
- **Training**: Single epoch only
- **Tasks**: Document summarization, query answering, structured data interpretation

### Benchmark Results (ToolBench)

| Model | Pass Rate |
|-------|-----------|
| **Fine-tuned SLM (350M)** | **77.55%** |
| ChatGPT-CoT | 26.00% |
| ToolLLaMA-DFS | 30.18% |
| ToolLLaMA-CoT | 16.27% |

### Implications for LocalMind
- Even sub-1B models can be competitive for tool calling with proper fine-tuning
- Domain adaptation (training on your specific tools) is the key differentiator
- Single-epoch SFT is sufficient — no need for complex RLHF pipelines
- The gap between fine-tuned SLMs and prompted LLMs is enormous (77% vs 26%)

---

## Paper 4: Production-Grade Agentic AI Workflows
**arXiv:2512.08769**

**Key takeaway**: Production agents need workflow decomposition, safety guardrails, and structured error recovery. The paper introduces patterns for MCP integration and multi-step orchestration.

### Workflow Decomposition
- **Sequential**: Tasks executed one after another (simplest, most reliable)
- **Parallel fan-out**: Independent subtasks run simultaneously, results merged
- **Conditional branching**: Different paths based on tool results
- **ReAct loop**: The default for agentic use — interleave reasoning and action

### Safety/Governance Patterns
- **Tool allowlisting**: Only expose tools the agent needs for the current task
- **Output validation**: Check tool call JSON against schema before execution
- **Sandboxing**: File operations restricted to workspace directory
- **Rate limiting**: Cap tool calls per turn (prevent infinite loops)
- **Human-in-the-loop**: Gate destructive actions behind approval

### MCP (Model Context Protocol) Integration
- Standardizes tool definitions as JSON Schema
- Enables tool sharing across different agent frameworks
- Compatible with Ollama's native tool calling format

### Error Recovery
- **Retry with backoff**: For transient failures (network, timeout)
- **Fallback tools**: Alternative tools when primary fails
- **Graceful degradation**: Return partial results rather than failing completely

---

## Paper 5: ToolMaker — LLM Agents Making Agent Tools
**arXiv:2502.11705** — Georg Wölflein, Dyke Ferber, Daniel Truhn, Ognjen Arandjelović, Jakob Nikolas Kather

**Key takeaway**: A framework that autonomously transforms GitHub repos into LLM-compatible tools. Given a GitHub URL + short task description, it installs dependencies, generates code, and debugs via closed-loop self-correction. **Correctly implements 80% of tasks** — substantially outperforming current software engineering agents. Evaluated across 15 complex tasks with 100+ unit tests.

### Tool Creation Pipeline
1. **Input**: GitHub URL + short task description
2. **Dependency installation**: Autonomously installs required packages
3. **Code generation**: Generates implementation matching the task spec
4. **Testing**: Runs against unit test suite
5. **Self-correction**: Closed-loop debugging — feeds errors back to LLM for iterative fixing
6. **Output**: Working, validated LLM-compatible tool

### Performance
- **80% task completion** across 15 complex computational tasks spanning various domains
- Evaluated with over 100 unit tests
- Substantially outperforms current state-of-the-art software engineering agents

### Relevance to LocalMind
- Our `backend/tools/base.py` → `src/agent/tools/base.py` pattern makes this directly applicable
- Any `Tool` subclass with `name`, `description`, `parameters`, `_execute()` can be auto-registered
- Future: let the agent write new tools as Python files in `tools/` and hot-reload them

---

## Paper 6: EnCompass — Search Over Execution Paths
**arXiv:2512.03571** — Zhening Li, Armando Solar-Lezama, Yisong Yue, Stephan Zheng (NeurIPS 2025)

**Key takeaway**: Instead of linear execution, agents should search over a tree of execution paths. A Python `@encompass.compile` decorator compiles agent workflows into searchable execution trees with `branchpoint()` markers. Reduces code modifications by **3–6x** vs plain Python. ARC benchmark: **38.7% vs 24% baseline** with GPT-4o.

### Branchpoint Annotation Pattern

- `@encompass.compile` decorator marks functions containing `branchpoint()` statements
- `branchpoint()` marks "locations of unreliability" where LLM calls occur
- These signal where the execution tree branches into multiple possible paths
- Shared mutable data via `NoCopy` type lets branches accumulate knowledge across attempts

### Search Algorithms (built-in)

- **Depth-first search (DFS)**
- **Beam search**: interpolates between global and local sampling
- **Best-of-NN variants**: global sampling with N parallel attempts
- **Best-first search**: score-guided exploration
- **parallel_bfs**: multithreaded breadth-first using `ThreadPoolExecutor`
- Custom algorithms via the `Checkpoint` class

### Performance Results

- **Code Translation**: Beam search with "coarse + fine" granularity outperformed simpler strategies (p<0.03)
- **ARC Tasks**: Global best-of-NN with N=36 achieved **38.7% accuracy vs 24% baseline** on GPT-4o
- **Reflexion**: Best-first search scaling exceeded vanilla refinement loops
- **Dev efficiency**: 3–6x fewer code modifications vs plain Python implementations

### Application to LocalMind

- Current agent loop is linear (iterate until done or max iterations)
- Future: add `branchpoint()` tracking — save context state before each tool call, explore alternatives on failure
- The `NoCopy` pattern (shared feedback across branches) maps well to our `ContextManager`

---

## Paper 7: Adaptation of Agentic AI Survey
**arXiv:2512.16301** — Pengcheng Jiang, Jiacheng Lin, Zhiyi Shi, Zifeng Wang + 30 co-authors (incl. Yejin Choi, Jiawei Han, Jimeng Sun)

**Key takeaway**: Agents improve through four paradigms: A1/A2 for agent adaptation, T1/T2 for tool adaptation. A1 uses tool-execution feedback; A2 uses agent-output quality. Evaluated across deep research, software development, computer use, and drug discovery.

### A1 — Tool-Execution-Signaled Adaptation

- Learning signal: feedback from whether tool calls succeed or fail
- Methods: supervised fine-tuning, preference optimization, RL with verifiable rewards
- The agent improves its tool selection and parameter formatting based on execution outcomes

### A2 — Agent-Output-Signaled Adaptation

- Learning signal: quality of the agent's final output (user feedback, task completion)
- Methods: same training approaches as A1, but optimizing for output quality not just tool success
- Enables learning higher-level strategies, not just tool mechanics

### T1/T2 — Tool Adaptation

- **T1 (agent-agnostic)**: Pre-trained tool modules that work with any agent
- **T2 (agent-supervised)**: Tools that train their own memory systems using agent outputs — "adaptive memory architectures"

### Practical Implementation for LocalMind

- Save procedural memories: "When user asks about X, tool Y with parameters Z works well"
- Track tool call success/failure rates per tool per task type
- Reinforce successful patterns: increase `relevance_score` for memories used in successful interactions
- Our `MemoryRetriever.reinforce()` method implements the A2 adaptation signal

---

## Paper 8: Memory in the Age of AI Agents
**arXiv:2512.13564** — Yuyang Hu, Shichun Liu, Yanwei Yue, Guibin Zhang + 43 co-authors (incl. Shuicheng Yan)

**Key takeaway**: Agent memory is not RAG. The paper defines a three-dimensional taxonomy across **forms** (token/parametric/latent), **functions** (factual/experiential/working), and **dynamics** (formation/evolution/retrieval). Memory, RAG, and context engineering are explicitly distinguished as separate domains.

### Three-Dimensional Memory Taxonomy

**By Form:**

- **Token-level memory**: Stored as explicit text tokens (our SQLite approach)
- **Parametric memory**: Encoded in model weights (via fine-tuning)
- **Latent memory**: Compressed representations (embeddings, hidden states)

**By Function:**

- **Factual memory**: Facts, knowledge, preferences → our `semantic` category
- **Experiential memory**: Events, interactions, outcomes → our `episodic` category
- **Working memory**: Current task context → our `ContextManager`

**By Dynamics:**

- **Formation**: How memories are created (explicit save vs automatic extraction)
- **Evolution**: How memories change over time (consolidation, decay, reinforcement)
- **Retrieval**: How memories are found (search, relevance scoring, recency weighting)

### Memory vs RAG vs Context Engineering

- **Context engineering**: What goes in the prompt right now. Ephemeral. Our `ContextManager`.
- **RAG**: Retrieval from external documents. The documents are static, the queries change.
- **Memory**: Agent's own accumulated knowledge. Both the data and its relevance evolve over time.
- The paper explicitly states these are distinct domains, not variations of the same thing.

### Persistent Memory Architecture

- **Storage**: Token-level memory in structured stores (our SQLite + FTS5 approach)
- **Retrieval**: Combine text relevance with recency and access frequency
- **Consolidation**: Periodically merge similar memories, prune stale ones
- **Capacity management**: Summarize old episodic memories into semantic ones as memory grows

### How LocalMind Implements This

- `MemoryStore`: Full taxonomy with SQLite + FTS5, matching token-level factual/experiential storage
- `ContextManager.inject_memories()`: Working memory management, injecting into system prompt
- `MemoryRetriever.reinforce()`: Memory evolution via relevance score adjustment
- Future: add memory consolidation (merge similar semantic memories) and periodic pruning

---

## Cross-Paper Synthesis: Key Patterns for LocalMind

### 1. Model Selection: SLM-First
Papers 1, 2, 3 all converge on: **start small, escalate only when needed**. For LocalMind:
- **Compact**: Phi-4-Mini (3.8B) — **>=97% BFCL-v4 accuracy**, matching 70B models on function calling. Only 6GB RAM needed.
- **Default**: Qwen2.5-Coder-7B (works on 8GB RAM) — best code generation + tool calling balance
- **Upgrade**: Qwen2.5-Coder-14B (16GB RAM) — significantly better multi-step reasoning
- **Optimal**: Qwen2.5-Coder-32B (32GB RAM) — near-cloud quality across all tasks

### 2. Tool Calling: Schema Enforcement is Non-Negotiable
Papers 1, 3, 4 agree: without JSON Schema enforcement, small models produce invalid tool calls 30-40% of the time. Our approach:
- Pass `tools` parameter to Ollama for native tool calling
- Fall back to text extraction with regex for models that don't support native calls
- Validate all tool calls against schema before execution

### 3. Memory: Three Types, One Store
Paper 8's taxonomy + Paper 7's adaptation patterns → our three-layer memory:
- Episodic: auto-saved after each interaction
- Semantic: saved when user shares facts/preferences
- Procedural: saved when tool patterns prove successful (reinforcement from Paper 7)

### 4. Safety: Defense in Depth
Paper 4's governance patterns → our multi-layer safety:
- Workspace sandboxing (file ops restricted to ~/LocalMind_Workspace)
- Command allowlisting (shell tool only runs approved commands)
- Timeout enforcement (no runaway tool calls)
- Iteration limits (no infinite agent loops)

### 5. Future Directions
- **Paper 5 (ToolMaker)**: Let the agent create new tools dynamically
- **Paper 6 (EnCompass)**: Add branchpoint tracking for smarter error recovery
- **Paper 2 (NVIDIA S5)**: Fine-tune on our specific tool schemas with QLoRA
- **Paper 7 (Adaptation)**: Track tool success rates and reinforce good patterns
