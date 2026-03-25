"""
autonomy.py — LocalMind Autonomy Engine (Core)
===============================================
Background scheduler that makes LocalMind work independently.

Runs periodic tasks in the background without blocking chat:
  1. Health Check    (60s)   — Keep Ollama alive, pre-warm model
  2. Self-Reflect    (5m)    — Review codebase, log proposals
  3. Execute Proposals (3m)  — Pick approved proposals and self-edit
  4. Auto-Test       (after edits) — Run pytest, log results

Modules:
  - proposals.py   — Proposal CRUD, dedup, approval
  - code_editor.py — AI-powered file editing (4-layer matching)
  - git_ops.py     — Git operations, file revert, test runner
"""

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path

import httpx

from backend.model_router import get_autonomy_models, get_startup_model
from backend.proposals import ProposalManager, PROPOSALS_DIR
from backend.code_editor import edit_single_file, identify_target_files, is_scope_achievable
from backend.git_ops import git_run, revert_file, run_tests
from backend.research_engine import (
    FailureAnalyzer, SuccessTracker, CodebaseScanner,
    PerformanceProfiler, ExternalResearcher
)
from backend.self_improver import SelfImprover
from backend.meta_critic import MetaCritic
from backend.priority_queue import PriorityQueue
from backend.todo_harvester import get_todos_for_prompt

logger = logging.getLogger("localmind.autonomy")

LOG_FILE = Path.home() / "LocalMind_Workspace" / "autonomy_log.jsonl"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AutonomyEngine:
    """Background scheduler for autonomous LocalMind operations."""

    CHAT_COOLDOWN = 30
    MAX_ACTIVE_PROPOSALS = 10       # Don't reflect if this many are queued
    CIRCUIT_BREAKER_THRESHOLD = 3   # Consecutive failures before cooldown
    CIRCUIT_BREAKER_COOLDOWN = 1800 # 30 minutes cooldown
    BACKOFF_BASE = 180              # 3 min base execution interval
    BACKOFF_MAX = 1800              # 30 min max backoff
    REFLECTION_FUTILITY_MAX = 5    # Consecutive rejected reflections before long sleep
    REFLECTION_BACKOFF_MAX = 1800  # 30 min max reflection backoff

    # Risk levels that auto-execute in autonomous mode
    AUTO_APPROVE_RISKS = {"low", "medium", "high", "critical"}

    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url
        models = get_autonomy_models()
        self.reflection_model = models.get("reflection", "qwen2.5-coder:7b")
        self.editing_model = models.get("editing", "qwen2.5-coder:32b")
        self.default_model = self.reflection_model
        self.startup_model = get_startup_model()

        self.enabled = True
        self.mode = "supervised"
        self.tasks = []
        self._last_chat_time = 0.0
        self._activity_subscribers: list[asyncio.Queue] = []
        self._recent_events: list[dict] = []
        self._start_time: float = time.time()
        self._manual_reflection_event = asyncio.Event()
        self._manual_execution_event = asyncio.Event()

        # Circuit breaker + backoff state
        self._consecutive_failures: int = 0
        self._circuit_open_until: float = 0.0  # timestamp when cooldown ends
        self._current_backoff: int = self.BACKOFF_BASE

        # Reflection futility tracking
        self._reflection_rejections: int = 0
        self._reflection_backoff: int = 300  # starts at 5 min (normal)

        # Auto-research: periodically mine arXiv for proposals
        self.auto_research_enabled: bool = True
        self._auto_research_interval: int = 1800  # 30 min

        # Delegate proposal management
        self.proposals = ProposalManager()

        # Research pipeline — data-driven self-improvement
        self.failure_analyzer = FailureAnalyzer()
        self.success_tracker = SuccessTracker()
        self.codebase_scanner = CodebaseScanner()
        self.performance_profiler = PerformanceProfiler()
        self.external_researcher = ExternalResearcher()
        self.self_improver = SelfImprover(emit_activity=self._emit_activity)
        self.meta_critic = MetaCritic(
            ollama_url=self.ollama_url,
            model=self.reflection_model,
            emit_activity=self._emit_activity,
        )
        self.priority_queue = PriorityQueue()

        # Status tracking
        self.status = {
            "enabled": True,
            "mode": "supervised",
            "started_at": None,
            "current_activity": None,
            "health_check": {"last_run": None, "ollama_ok": False, "model_loaded": False},
            "reflection": {"last_run": None, "proposals_logged": 0},
            "execution": {"last_run": None, "proposals_executed": 0, "last_result": None},
            "auto_test": {"last_run": None, "passed": 0, "failed": 0},
        }

    def notify_chat_activity(self):
        """Called by the chat route whenever the user sends a message."""
        self._last_chat_time = time.time()

    def is_user_active(self) -> bool:
        """Returns True if the user chatted within CHAT_COOLDOWN seconds."""
        return (time.time() - self._last_chat_time) < self.CHAT_COOLDOWN

    # ── Live Activity Feed ──────────────────────────────────────────

    def subscribe_activity(self) -> asyncio.Queue:
        """Create a new subscriber queue for SSE activity events."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._activity_subscribers.append(q)
        return q

    def unsubscribe_activity(self, q: asyncio.Queue):
        """Remove a subscriber queue."""
        if q in self._activity_subscribers:
            self._activity_subscribers.remove(q)

    def _emit_activity(self, action: str, detail: str = "", **extra):
        """Push a live activity event to all SSE subscribers."""
        event = {
            "ts": time.time(),
            "time": time.strftime("%H:%M:%S"),
            "action": action,
            "detail": detail,
            "model": extra.get("model", self.default_model),
            "ideas": self.status["reflection"]["proposals_logged"],
            "applied": self.status["execution"]["proposals_executed"],
            **extra,
        }
        self.status["current_activity"] = event
        self._recent_events.append(event)
        if len(self._recent_events) > 30:
            self._recent_events = self._recent_events[-30:]

        for q in self._activity_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def set_mode(self, mode: str) -> str:
        """Switch between 'supervised' and 'autonomous' mode."""
        if mode not in ("supervised", "autonomous"):
            raise ValueError(f"Invalid mode: {mode}")
        self.mode = mode
        self.status["mode"] = mode
        self._log("mode_changed", {"mode": mode})
        self._emit_activity("mode_changed", f"Switched to {mode} mode")
        logger.info(f"🤖 Autonomy mode: {mode}")
        return mode

    def _get_success_rate(self) -> int:
        """Calculate the live success rate (0-100) from completed/failed proposals."""
        proposals = self.proposals.list_proposals("all")
        completed = sum(1 for p in proposals if p.get("status") == "completed")
        failed = sum(1 for p in proposals if p.get("status") == "failed")
        total = completed + failed
        return round((completed / total) * 100) if total > 0 else 50  # default 50% when no data

    def _log(self, event: str, data: dict = None):
        """Append a structured log entry to autonomy_log.jsonl."""
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.time(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "event": event,
                **(data or {}),
            }
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.warning(f"Failed to write autonomy log: {exc}")

    def trigger_reflection(self):
        """Manually trigger the reflection cycle."""
        self._manual_reflection_event.set()
        logger.info("💡 Manual reflection event set")

    def trigger_execution(self):
        """Manually trigger the execution cycle."""
        self._manual_execution_event.set()
        logger.info("⚙️ Manual execution event set")

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self):
        """Start all background loops."""
        self.status["started_at"] = time.time()
        self._log("engine_started")
        logger.info("🤖 Autonomy Engine started")

        self.tasks = [
            asyncio.create_task(self._health_loop(), name="health"),
            asyncio.create_task(self._reflection_loop(), name="reflection"),
            asyncio.create_task(self._execution_loop(), name="execution"),
            asyncio.create_task(self._digest_loop(), name="digest"),
            asyncio.create_task(self._auto_research_loop(), name="auto_research"),
        ]

    async def stop(self):
        """Stop all background loops."""
        for task in self.tasks:
            task.cancel()
        self.tasks = []
        self._log("engine_stopped")
        logger.info("🤖 Autonomy Engine stopped")

    def toggle(self) -> bool:
        """Toggle the engine on/off."""
        self.enabled = not self.enabled
        self.status["enabled"] = self.enabled
        self._log("engine_toggled", {"enabled": self.enabled})
        logger.info(f"🤖 Autonomy Engine {'enabled' if self.enabled else 'paused'}")
        return self.enabled

    # ── Health Check Loop ────────────────────────────────────────

    async def _health_loop(self):
        """Every 60s: ping Ollama, pre-warm model if needed."""
        await asyncio.sleep(5)
        while True:
            try:
                if self.enabled and not self.is_user_active():
                    await self._check_health()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Health loop error: {exc}")
                await asyncio.sleep(60)

    async def _check_health(self):
        """Ping Ollama and check if a model is loaded."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.ollama_url}/api/tags")
                ollama_ok = resp.status_code == 200

                ps_resp = await client.get(f"{self.ollama_url}/api/ps")
                ps_data = ps_resp.json()
                models_loaded = len(ps_data.get("models", [])) > 0

                self.status["health_check"] = {
                    "last_run": time.time(),
                    "ollama_ok": ollama_ok,
                    "model_loaded": models_loaded,
                }

                if ollama_ok and not models_loaded:
                    logger.info("No model in VRAM — pre-warming the default model")
                    await self._prewarm_model()
                elif models_loaded:
                    logger.debug("Model already loaded in VRAM — skipping pre-warm")

        except Exception:
            self.status["health_check"]["ollama_ok"] = False
            self.status["health_check"]["model_loaded"] = False
            self.status["health_check"]["last_run"] = time.time()

    async def _prewarm_model(self):
        """Send a tiny prompt to Ollama to load the model into VRAM."""
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                logger.info(f"🔥 Pre-warming model: {self.startup_model}")
                self._log("prewarm_start", {"model": self.startup_model})
                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.startup_model,
                        "prompt": "Hello",
                        "stream": False,
                        "options": {"num_predict": 1},
                    },
                )
                if resp.status_code == 200:
                    self._log("prewarm_done", {"model": self.startup_model})
                    logger.info(f"✅ Model pre-warmed: {self.startup_model}")
                else:
                    logger.warning(f"Pre-warm failed: {resp.status_code}")
        except Exception as exc:
            logger.warning(f"Pre-warm failed: {exc}")

    # ── Self-Reflection Loop ─────────────────────────────────────

    async def _reflection_loop(self):
        """Every 5min (with futility backoff): review codebase and log proposals."""
        await asyncio.sleep(60)
        while True:
            try:
                try:
                    await asyncio.wait_for(
                        self._manual_reflection_event.wait(),
                        timeout=self._reflection_backoff,
                    )
                    self._manual_reflection_event.clear()
                    logger.info("⚡ Executing manual reflection")
                except asyncio.TimeoutError:
                    pass

                if self.enabled and not self.is_user_active():
                    # Proposal cap: skip reflection if too many are queued
                    active_count = self.proposals.count_active()
                    if active_count >= self.MAX_ACTIVE_PROPOSALS:
                        logger.info(f"Reflection skipped — {active_count} active proposals (cap: {self.MAX_ACTIVE_PROPOSALS})")
                        self._emit_activity("idle", f"Skipping reflection: {active_count} active proposals (cap: {self.MAX_ACTIVE_PROPOSALS})")
                    else:
                        # Clean up stale proposals before reflecting
                        self.proposals.cleanup_stale()
                        self._emit_activity("reflecting", "Step 1/2: Analyzing project structure...")
                        proposal_saved = await self._run_reflection()

                        if proposal_saved:
                            # Success — reset futility counter
                            self._reflection_rejections = 0

                            # Adaptive scheduling: faster when succeeding, slower when struggling
                            success_rate = self._get_success_rate()
                            if success_rate >= 70:
                                self._reflection_backoff = 180   # 3 min — engine is hot
                                self._current_backoff = max(120, self._current_backoff // 2)
                            elif success_rate >= 40:
                                self._reflection_backoff = 300   # 5 min — normal
                                self._current_backoff = self.BACKOFF_BASE
                            else:
                                self._reflection_backoff = 600   # 10 min — cool down
                                self._current_backoff = min(self._current_backoff * 2, self.BACKOFF_MAX)

                            self._emit_activity("idle",
                                f"Waiting for next cycle (success rate: {success_rate}%, "
                                f"reflect: {self._reflection_backoff // 60}m, execute: {self._current_backoff // 60}m)")
                        else:
                            # Futility — proposal was rejected/duplicate/banned
                            self._reflection_rejections += 1
                            if self._reflection_rejections >= self.REFLECTION_FUTILITY_MAX:
                                self._reflection_backoff = min(
                                    self._reflection_backoff * 2,
                                    self.REFLECTION_BACKOFF_MAX,
                                )
                                wait_min = self._reflection_backoff // 60
                                self._emit_activity(
                                    "idle",
                                    f"Out of new ideas — {self._reflection_rejections} rejections in a row. "
                                    f"Sleeping {wait_min} min before next attempt.",
                                )
                                logger.info(
                                    f"Reflection futility: {self._reflection_rejections} rejections, "
                                    f"backoff now {self._reflection_backoff}s"
                                )
                            else:
                                self._emit_activity("idle", "Waiting for next reflection cycle")
                else:
                    if self.is_user_active():
                        logger.debug("Reflection skipped — user is active")
                    else:
                        logger.debug("Reflection skipped — engine disabled")

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Reflection loop error: {exc}")
                self._emit_activity("error", f"Reflection failed: {exc}")
                await asyncio.sleep(60)

    # ── Auto-Research Loop ────────────────────────────────────────

    _AUTO_RESEARCH_QUERIES = {
        "performance": "software performance optimization techniques",
        "code_quality": "code refactoring best practices",
        "security": "software security vulnerability detection",
        "feature": "AI code generation agents",
        "ux": "developer experience user interface design",
    }

    async def _auto_research_loop(self):
        """Every 30min: search arXiv for papers and generate proposals."""
        import random
        await asyncio.sleep(300)  # Wait 5 min after startup

        while True:
            try:
                await asyncio.sleep(self._auto_research_interval)

                if not self.enabled or not self.auto_research_enabled:
                    continue
                if self.is_user_active():
                    continue

                # Pick a random category to research
                category = random.choice(list(self._AUTO_RESEARCH_QUERIES.keys()))
                query = self._AUTO_RESEARCH_QUERIES[category]

                self._emit_activity("researching", f"Auto-research: searching arXiv for '{query}'")
                logger.info(f"📚 Auto-research: querying arXiv for '{query}'")

                try:
                    from backend.research_engine import AcademicResearcher
                    researcher = AcademicResearcher()
                    papers = await researcher.search_arxiv(query, max_results=3)
                except Exception as e:
                    logger.warning(f"Auto-research arXiv search failed: {e}")
                    self._emit_activity("idle", f"Auto-research failed: {e}")
                    continue

                if not papers:
                    self._emit_activity("idle", "Auto-research: no papers found")
                    continue

                # Pick the top paper and generate a proposal
                paper = papers[0]
                self._emit_activity(
                    "researching",
                    f"Generating proposal from: {paper.get('title', '?')[:60]}...",
                )

                try:
                    from backend.routes.research_routes import generate_paper_proposal
                    result = await generate_paper_proposal(
                        title=paper.get("title", ""),
                        abstract=paper.get("abstract", ""),
                        url=paper.get("url", ""),
                    )
                    if result.get("proposal"):
                        title = result["proposal"].get("title", "?")
                        self._emit_activity("idle", f"📄 Auto-research proposal: {title}")
                        logger.info(f"📄 Auto-research generated proposal: {title}")
                    else:
                        self._emit_activity("idle", f"Auto-research: {result.get('error', 'no proposal')}")
                except Exception as e:
                    logger.warning(f"Auto-research proposal generation failed: {e}")
                    self._emit_activity("idle", f"Auto-research proposal failed: {e}")

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Auto-research loop error: {exc}")
                await asyncio.sleep(120)

    def _sample_code_snippets(self, real_files: list[str], count: int = 3) -> str:
        """Read project files weighted by TODO count + git recency.

        Smart sampling: files with TODOs or recent git changes are
        3x more likely to be selected, producing higher-value proposals.
        """
        import random
        project_root = Path(__file__).parent.parent
        code_exts = {".py", ".js", ".html", ".css"}
        candidates = [f for f in real_files if Path(f).suffix in code_exts]
        if not candidates:
            return ""

        # Build weights: files with TODOs or recent git activity get 3x weight
        weights = []
        try:
            from backend.todo_harvester import harvest_todos
            todo_files = {t["file"] for t in harvest_todos(str(project_root))}
        except Exception:
            todo_files = set()

        try:
            result = subprocess.run(
                ["git", "log", "--diff-filter=M", "-5", "--name-only", "--format="],
                capture_output=True, text=True, cwd=str(project_root), timeout=5
            )
            recent_files = {l.strip() for l in result.stdout.splitlines() if l.strip()}
        except Exception:
            recent_files = set()

        for f in candidates:
            w = 1
            if f in todo_files:
                w += 2
            if f in recent_files:
                w += 2
            weights.append(w)

        sampled = []
        pop = list(candidates)
        w_pop = list(weights)
        for _ in range(min(count, len(pop))):
            if not pop:
                break
            chosen = random.choices(pop, weights=w_pop, k=1)[0]
            idx = pop.index(chosen)
            sampled.append(pop.pop(idx))
            w_pop.pop(idx)

        snippets = []
        for filepath in sampled:
            try:
                full_path = project_root / filepath
                content = full_path.read_text(encoding="utf-8", errors="replace")
                lines = content.split("\n")
                if len(lines) > 150:  # Skip very large files
                    continue
                numbered = []
                for i, line in enumerate(lines[:100], 1):
                    numbered.append(f"{i:>4}| {line}")
                preview = "\n".join(numbered)
                if len(lines) > 100:
                    preview += f"\n     ... ({len(lines) - 100} more lines)"
                snippets.append(f"### {filepath} ({len(lines)} lines)\n```\n{preview}\n```")
            except (OSError, UnicodeDecodeError):
                continue

        if not snippets:
            return ""

        return (
            "\nCODE SAMPLES (read these carefully, propose changes based on what you see):\n"
            + "\n\n".join(snippets)
            + "\n"
        )

    async def _run_reflection(self) -> bool:
        """Ask the AI to reflect on its own codebase and log proposals.

        Returns True if a proposal was successfully saved, False otherwise.
        """
        try:
            # Self-improvement: analyze performance and tune brain config
            try:
                changes = self.self_improver.optimize()
                if changes:
                    logger.info(f"🧬 Self-improvement: {len(changes)} config change(s)")
            except Exception as si_exc:
                logger.warning(f"Self-improvement failed: {si_exc}")

            # Gather actual file listing
            project_root = Path(__file__).parent.parent
            real_files = []
            for ext in ("*.py", "*.js", "*.html", "*.css", "*.json", "*.md"):
                for f in project_root.rglob(ext):
                    rel = f.relative_to(project_root)
                    skip = any(part.startswith(".") or part in ("node_modules", "__pycache__", "memory_db", "browser_recordings") for part in rel.parts)
                    if not skip:
                        real_files.append(str(rel).replace("\\", "/"))

            file_list = "\n".join(f"  - {f}" for f in sorted(real_files)[:60])

            # Code-aware reflection: sample actual code
            code_snippets = self._sample_code_snippets(real_files)

            # TODO harvester: find actionable items from code comments
            todo_context = get_todos_for_prompt()

            self._emit_activity("reflecting", f"Step 2/2: Generating proposals with {self.reflection_model}...")

            # Category distribution for diversity
            existing_proposals = self.proposals.list_proposals()
            category_counts = {}
            for p in existing_proposals:
                cat = p.get("category", "unknown")
                category_counts[cat] = category_counts.get(cat, 0) + 1

            focus_categories = ["performance", "feature", "ux", "security", "code_quality", "bugfix"]

            # Guardrail 2: Skip categories with low success rate
            blocked_cats = self.self_improver.get_blocked_categories()
            if blocked_cats:
                available = [c for c in focus_categories if c not in blocked_cats]
                if available:
                    logger.info(f"🚫 Blocked categories (low success): {blocked_cats}")
                    self._emit_activity(
                        "thinking",
                        f"Skipping low-success categories: {', '.join(blocked_cats)}",
                        thinking_type="category_gate",
                    )
                    focus_categories = available
                else:
                    logger.warning("All categories blocked — using full list as fallback")

            category_weights = [(cat, category_counts.get(cat, 0)) for cat in focus_categories]
            category_weights.sort(key=lambda x: x[1])
            focus_category = category_weights[0][0]

            # Anti-repeat: recent + failed titles
            all_blocked = self.proposals.get_anti_repeat_titles()
            anti_repeat = ""
            if all_blocked:
                anti_repeat = (
                    "\nALREADY PROPOSED (do NOT repeat, rephrase, or create variations):\n"
                    + "\n".join(f"  ❌ {t}" for t in list(all_blocked)[:20])
                    + "\n\nIMPORTANT: If your idea is similar to ANY of the above, STOP and think of something completely different.\n"
                )

            # Suppress over-represented categories
            suppress = ""
            if category_counts:
                dominant_cat = max(category_counts, key=category_counts.get)
                if category_counts.get(dominant_cat, 0) > max(3, len(existing_proposals) * 0.3):
                    suppress = f"\nBLOCKED CATEGORY: Do NOT propose anything about \"{dominant_cat}\" — it is overrepresented.\n"

            async with httpx.AsyncClient(timeout=120.0) as client:
                # ── Research Pipeline: inject evidence into prompt ──
                lessons_block = self.failure_analyzer.get_lessons_for_prompt()
                stats_block = self.success_tracker.get_stats_for_prompt()
                try:
                    scan_block = self.codebase_scanner.get_findings_for_prompt()
                except Exception as scan_exc:
                    logger.warning(f"Codebase scan failed: {scan_exc}")
                    scan_block = ""
                    
                try:
                    perf_block = self.performance_profiler.get_findings_for_prompt()
                except Exception as perf_exc:
                    logger.warning(f"Performance profiling failed: {perf_exc}")
                    perf_block = ""
                    
                try:
                    ext_block = await self.external_researcher.get_findings_for_prompt(focus_category)
                except Exception as ext_exc:
                    logger.warning(f"External research failed: {ext_exc}")
                    ext_block = ""

                research_context = ""
                if lessons_block:
                    research_context += lessons_block + "\n"
                if stats_block:
                    research_context += stats_block + "\n"
                if scan_block:
                    research_context += scan_block + "\n"
                if perf_block:
                    research_context += perf_block + "\n"
                if ext_block:
                    research_context += ext_block + "\n"

                # ── Thinking: broadcast what research found ──
                research_parts = []
                if lessons_block:
                    lesson_count = lessons_block.count("\u26a0\ufe0f")
                    research_parts.append(f"{lesson_count} lessons from past failures")
                if stats_block:
                    research_parts.append("execution track record loaded")
                if scan_block:
                    complexity_count = scan_block.count("\ud83d\udcd0")
                    smell_count = scan_block.count("\ud83d\udd0d")
                    research_parts.append(f"{complexity_count} complex functions, {smell_count} code smells")
                if perf_block:
                    research_parts.append("performance metrics collected")
                if ext_block:
                    research_parts.append(f"best practices for '{focus_category}'")

                if research_parts:
                    self._emit_activity("thinking",
                                        f"Research gathered: {', '.join(research_parts)}",
                                        thinking_type="research",
                                        research_summary=research_context[:500])
                else:
                    self._emit_activity("thinking",
                                        "No prior research data yet — first reflection cycle",
                                        thinking_type="research")

                # Get brain config context (self-taught intelligence)
                brain_context = ""
                try:
                    brain_context = self.self_improver.get_config_for_prompt()
                except Exception:
                    pass

                # User priority injection
                priority_context = self.priority_queue.get_prompt_injection()

                # Dynamic banned topics from brain config
                banned_list = self.self_improver.config.get("banned_patterns", [])
                banned_str = ", ".join(f"'{b}'" for b in banned_list[:10])

                prompt = (
                    "You are LocalMind, an AI assistant reviewing your OWN codebase.\n\n"
                    "HERE ARE THE ACTUAL FILES IN THIS PROJECT:\n"
                    f"{file_list}\n\n"
                    f"{code_snippets}"
                    f"{todo_context}"
                    f"{brain_context}"
                    f"{research_context}"
                    f"{priority_context}"
                    f"REQUIRED CATEGORY: Your proposal MUST be in the \"{focus_category}\" category.\n"
                    f"{anti_repeat}"
                    f"{suppress}"
                    "RULES (follow ALL of them):\n"
                    "1. ONLY suggest changes to files listed above — do NOT invent files.\n"
                    "2. Be SPECIFIC — describe the exact code change (e.g. 'add gzip compression to /api/documents response').\n"
                    "3. Your title must describe the SPECIFIC change, NOT a vague topic like 'Improve Error Handling'.\n"
                    f"4. BANNED TOPICS: {banned_str} — these are EXHAUSTED.\n"
                    "5. Base your proposal on the CODE SAMPLES and CODEBASE SCAN data above when available.\n"
                    "6. Look for: missing error handling, TODOs/FIXMEs, dead code, slow patterns, "
                    "missing input validation, hardcoded values, duplicate logic.\n\n"
                    "Output a JSON object with keys: title, category "
                    "(performance/feature/bugfix/ux/security/code_quality), "
                    "description, files_affected (list of real filenames from above), "
                    "effort (small/medium/large), priority (low/medium/high/critical).\n"
                    "Only output the JSON, nothing else."
                )

                # ── Thinking: broadcast what the AI is being asked ──
                self._emit_activity("thinking",
                                    f"Asking {self.reflection_model}: 'Generate a {focus_category} proposal from {len(real_files)} files'",
                                    thinking_type="prompt",
                                    focus_category=focus_category,
                                    files_scanned=len(real_files))

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.reflection_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 400, "num_ctx": 8192},
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    response_text = data.get("response", "")

                    try:
                        json_text = response_text.strip()
                        if "```" in json_text:
                            json_text = json_text.split("```")[1]
                            if json_text.startswith("json"):
                                json_text = json_text[4:]
                            json_text = json_text.strip()

                        proposal = json.loads(json_text)

                        # Extra filter: reject proposals with banned keywords in title
                        title_lower = proposal.get("title", "").lower()
                        banned_phrases = self.self_improver.config.get("banned_patterns", [])
                        if any(phrase in title_lower for phrase in banned_phrases):
                            logger.info(f"Rejected banned-topic proposal: {proposal.get('title', '?')}")
                            self._emit_activity("info", f"Rejected banned topic: {proposal.get('title', '?')}")
                        else:
                            # ── Meta-Cognition: Critique-Backtrack-Refine ──
                            critique = await self.meta_critic.review(proposal, file_list=real_files)

                            if not critique.approved:
                                logger.info(f"Critic rejected: {proposal.get('title', '?')} — {critique.reason}")
                                self._emit_activity("info", f"Critic rejected: {proposal.get('title', '?')} ({critique.reason})")
                            else:
                                # Use refined proposal if critic improved it
                                if critique.refinement:
                                    proposal = critique.refinement

                                saved = self.proposals.save(
                                    proposal,
                                    mode=self.mode,
                                    auto_approve_risks=self.AUTO_APPROVE_RISKS,
                                    log_fn=self._log,
                                    emit_activity=self._emit_activity,
                                )
                                if saved:
                                    self.status["reflection"]["proposals_logged"] += 1
                                    self._log("reflection_done", {"proposal": saved.get("title", "?")})
                                    self._emit_activity("proposal_created", f"New proposal: {saved.get('title', '?')}",
                                                        proposal_id=saved.get("id", ""),
                                                        category=saved.get("category", ""))
                                    self._emit_activity("thinking",
                                                        f"AI proposed: '{saved.get('title', '?')}' [{saved.get('category', '?')}] — {saved.get('description', '')[:120]}",
                                                        thinking_type="response",
                                                        proposal_title=saved.get("title", ""),
                                                        proposal_description=saved.get("description", ""),
                                                        files_affected=saved.get("files_affected", []))
                                    logger.info(f"Auto-reflection logged: {saved.get('title', '?')}")
                                    return True

                    except (json.JSONDecodeError, IndexError):
                        self._log("reflection_parse_failed", {"response": response_text[:200]})

                self.status["reflection"]["last_run"] = time.time()
                return False

        except Exception as exc:
            logger.warning(f"Reflection failed: {exc}")
            self.status["reflection"]["last_run"] = time.time()
            return False

    # ── Proposal API Wrappers ────────────────────────────────────

    def list_proposals(self, status_filter: str = "all") -> list[dict]:
        return self.proposals.list_proposals(status_filter)

    def approve_proposal(self, proposal_id: str):
        result = self.proposals.approve(proposal_id)
        if result:
            self._log("proposal_approved", {"id": proposal_id, "title": result.get("title", "?")})
        return result

    def deny_proposal(self, proposal_id: str):
        result = self.proposals.deny(proposal_id)
        if result:
            self._log("proposal_denied", {"id": proposal_id, "title": result.get("title", "?")})
        return result

    def retry_proposal(self, proposal_id: str):
        return self.proposals.retry(proposal_id, emit_activity=self._emit_activity)

    # ── Execution Loop ───────────────────────────────────────────

    # ── Digest Auto-Schedule ──────────────────────────────────────
    async def _digest_loop(self):
        """Every 6 hours: generate a daily digest summary."""
        await asyncio.sleep(60)  # Let things warm up first
        while True:
            try:
                if self.enabled:
                    try:
                        from backend.digest import generate_digest
                        digest = generate_digest()
                        if digest:
                            self._emit_activity("completed", f"📊 Daily digest generated")
                            logger.info("📊 Daily digest generated")
                    except Exception as exc:
                        logger.warning(f"Digest generation failed: {exc}")
                await asyncio.sleep(6 * 3600)  # 6 hours
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Digest loop error: {exc}")
                await asyncio.sleep(3600)  # Retry in 1h

    # ── Execution Loop ───────────────────────────────────────────

    async def _execution_loop(self):
        """Every 3 min (with backoff): pick an approved proposal and execute it."""
        await asyncio.sleep(10)
        while True:
            try:
                try:
                    await asyncio.wait_for(
                        self._manual_execution_event.wait(),
                        timeout=self._current_backoff,
                    )
                    self._manual_execution_event.clear()
                    logger.info("⚡ Executing manual task run")
                except asyncio.TimeoutError:
                    pass

                if self.enabled and not self.is_user_active():
                    # Circuit breaker check
                    if self._circuit_open_until > time.time():
                        remaining = int(self._circuit_open_until - time.time())
                        self._emit_activity(
                            "cooldown",
                            f"Circuit breaker active — cooling down for {remaining}s "
                            f"after {self.CIRCUIT_BREAKER_THRESHOLD} consecutive failures",
                        )
                        logger.info(f"Circuit breaker: {remaining}s remaining")
                        continue

                    self._emit_activity("checking", "Looking for approved proposals to execute...")
                    executed_count = 0
                    while True:
                        had_work = await self._execute_next_proposal()
                        if not had_work:
                            break
                        executed_count += 1
                        await asyncio.sleep(5)
                        if not self.enabled or self.is_user_active():
                            break
                    if executed_count > 0:
                        logger.info(f"Executed {executed_count} proposal(s) this cycle")
                    else:
                        self._emit_activity("idle", "No approved proposals to execute")
                else:
                    if self.is_user_active():
                        logger.debug("Execution skipped -- user is active")
                    else:
                        logger.debug("Execution skipped -- engine disabled")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Execution loop error: {exc}")
                self._emit_activity("error", f"Execution failed: {exc}")
                await asyncio.sleep(60)

    async def _execute_next_proposal(self):
        """Find the highest-priority approved proposal and execute it.

        Prerequisite-aware: skips proposals whose 'follows' prerequisite
        hasn't completed yet. After success, auto-approves chained successors.
        """
        proposals = self.proposals.list_proposals("approved")
        if not proposals:
            return False

        # Sort by priority, then filter by prerequisite readiness
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        proposals.sort(key=lambda p: priority_order.get(p.get("priority", "medium"), 2))

        # Prerequisite check: skip proposals with unmet dependencies
        proposal = None
        for p in proposals:
            if self.proposals.is_prerequisite_met(p):
                proposal = p
                break
            else:
                logger.debug(f"Skipping {p['id']}: prerequisite {p.get('follows')} not met")
        if proposal is None:
            return False

        filepath = PROPOSALS_DIR / f"{proposal['id']}_{proposal['category']}.json"

        # Scope guard: skip proposals too broad for the model
        if not is_scope_achievable(proposal):
            self._emit_activity("info", f"Skipped (too broad): {proposal['title']}")
            proposal["status"] = "skipped"
            proposal["error"] = "Proposal scope too broad for automated editing"
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
            self.status["execution"]["last_run"] = time.time()
            return False

        try:
            exec_start_time = time.time()
            exec_total_tokens = 0

            self._emit_activity("executing",
                                f"Starting: {proposal['title']}",
                                proposal_id=proposal["id"],
                                proposal_title=proposal.get("title", ""))
            self._log("proposal_execution_start", {
                "id": proposal["id"], "title": proposal["title"]
            })

            # Step 1: Identify target files
            files_affected, targeting_tokens = await identify_target_files(
                proposal, self.ollama_url, self.editing_model, self._emit_activity
            )
            exec_total_tokens += targeting_tokens

            if not files_affected:
                self.proposals.mark_failed(
                    proposal,
                    "Could not determine which files to edit",
                    filepath
                )
                self._log("proposal_execution_failed", {"id": proposal["id"], "error": "no target files"})
                self.status["execution"]["last_run"] = time.time()
                self._emit_activity("error", "Failed: Could not determine target files")
                return False

            # ── Thinking: broadcast file targeting ──
            self._emit_activity("thinking",
                                f"Targeting {len(files_affected)} file(s): {', '.join(files_affected[:3])}",
                                thinking_type="targeting",
                                files_affected=files_affected,
                                proposal_title=proposal.get("title", ""))

            # Step 2: Create git safety branch
            branch_name = f"self-improve/{proposal['id']}-{proposal['category']}"
            git_run(["checkout", "-b", branch_name])
            self._log("git_branch_created", {"branch": branch_name})
            self._emit_activity("git", f"Created safety branch: {branch_name}",
                                proposal_title=proposal.get("title", ""))

            # Step 3: For each target file, read → AI → write
            edits_applied = []
            for target_file in files_affected[:3]:
                self._emit_activity("writing", f"Editing: {target_file}",
                                    file=target_file,
                                    task_description=proposal.get("description", ""),
                                    proposal_title=proposal.get("title", ""))
                success, edit_tokens = await edit_single_file(
                    target_file, proposal,
                    self.ollama_url, self.editing_model,
                    log_fn=self._log, emit_activity=self._emit_activity
                )
                exec_total_tokens += edit_tokens
                if success:
                    edits_applied.append(target_file)

            if not edits_applied:
                git_run(["checkout", "main"])
                git_run(["branch", "-D", branch_name])
                self.proposals.mark_failed(
                    proposal,
                    "AI could not generate valid edits",
                    filepath
                )
                self._log("proposal_execution_failed", {"id": proposal["id"], "error": "no edits applied"})
                self.status["execution"]["last_run"] = time.time()
                self._emit_activity("error", f"Failed: AI could not generate valid edits for {proposal['title']}")
                return False

            # Step 4: Run tests
            self._emit_activity("testing", f"Running tests to verify: {proposal['title']}",
                                proposal_title=proposal.get("title", ""))
            test_passed, test_output = await run_tests()

            # Update test stats
            self.status["auto_test"]["last_run"] = time.time()

            if test_passed:
                self._emit_activity("committing", f"Committing: {proposal['title']}",
                                    proposal_title=proposal.get("title", ""))
                commit_msg = (
                    f"[autonomy] {proposal['title']}\n\n"
                    f"Proposal ID: {proposal['id']}\n"
                    f"Category: {proposal['category']}\n"
                    f"Files: {', '.join(edits_applied)}"
                )
                git_run(["add", "-A"])
                git_run(["commit", "-m", commit_msg])

                # Auto-merge to main so improvements actually land
                merge_result = git_run(["checkout", "main"])
                if merge_result is not None:
                    git_run(["merge", branch_name, "--no-ff", "-m",
                             f"Merge autonomy: {proposal['title']}"])
                    git_run(["branch", "-d", branch_name])
                    self._emit_activity("merged", f"Merged to main: {proposal['title']}")
                    logger.info(f"🔀 Auto-merged {branch_name} → main")

                proposal["status"] = "completed"
                proposal["execution_result"] = f"✅ Applied to {len(edits_applied)} file(s), tests passed"
                proposal["execution_finished_at"] = time.time()
                proposal["execution_duration"] = round(time.time() - exec_start_time, 1)
                proposal["total_tokens"] = exec_total_tokens
                proposal["model_used"] = self.editing_model
                proposal["files_edited"] = edits_applied
                proposal["branch"] = branch_name
                filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

                self.status["execution"]["proposals_executed"] += 1
                self.status["execution"]["last_result"] = proposal["title"]
                self._log("proposal_execution_done", {
                    "id": proposal["id"],
                    "title": proposal["title"],
                    "files": edits_applied,
                    "branch": branch_name,
                })
                self._emit_activity("completed",
                                    f"✨ COMPLETED: {proposal['title']} (Changes applied & tests passed)",
                                    proposal_id=proposal["id"],
                                    files=edits_applied,
                                    branch=branch_name)
                logger.info(f"✅ Proposal executed: {proposal['title']} → branch {branch_name}")

                # Research pipeline: record success
                self.success_tracker.record_outcome(proposal, success=True)

                # Reset circuit breaker + backoff on success
                self._consecutive_failures = 0
                self._current_backoff = self.BACKOFF_BASE

                # Auto-approve chained proposals that depend on this one
                chained = self.proposals.get_chained_proposals(proposal["id"])
                for chained_proposal, chained_path in chained:
                    chained_proposal["status"] = "approved"
                    chained_proposal["auto_approved_reason"] = f"Predecessor {proposal['id']} completed"
                    chained_path.write_text(json.dumps(chained_proposal, indent=2), encoding="utf-8")
                    self._emit_activity("auto_approved",
                                        f"🔗 Auto-approved chained: {chained_proposal['title']}",
                                        proposal_id=chained_proposal["id"])
                    logger.info(f"🔗 Auto-approved chained proposal: {chained_proposal['title']}")

                return True

            else:
                # Tests failed — revert only the edited files (not entire repo)
                for target_file in edits_applied:
                    revert_file(target_file)

                # Clean up the feature branch (don't use 'git checkout -- .'  
                # which would try to reset locked ChromaDB/binary files)
                git_run(["checkout", "main"])
                git_run(["branch", "-D", branch_name])

                short_error = test_output[:200] if test_output else "Unknown test failure"
                proposal["execution_duration"] = round(time.time() - exec_start_time, 1)
                proposal["total_tokens"] = exec_total_tokens
                proposal["model_used"] = self.editing_model
                self.proposals.mark_failed(
                    proposal,
                    f"Tests failed after applying edits:\n{short_error}",
                    filepath
                )
                self._log("proposal_execution_reverted", {
                    "id": proposal["id"],
                    "title": proposal["title"],
                    "error": short_error,
                })
                self._emit_activity("reverted",
                                    f"❌ REVERTED: {proposal['title']} (Automated tests failed - check logs for details)",
                                    proposal_id=proposal["id"])
                logger.warning(f"⚠️ Proposal reverted: {proposal['title']} (tests failed)")

                # Research pipeline: analyze failure and record outcome
                self.failure_analyzer.analyze_failure(proposal, test_output or "")
                self.success_tracker.record_outcome(proposal, success=False)

                # Progressive backoff + circuit breaker
                self._consecutive_failures += 1
                self._current_backoff = min(
                    self._current_backoff * 2, self.BACKOFF_MAX
                )
                logger.info(
                    f"Backoff increased to {self._current_backoff}s "
                    f"(consecutive failures: {self._consecutive_failures})"
                )
                if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
                    self._circuit_open_until = time.time() + self.CIRCUIT_BREAKER_COOLDOWN
                    self._emit_activity(
                        "cooldown",
                        f"🛑 Circuit breaker tripped after {self._consecutive_failures} "
                        f"consecutive failures — cooling down for {self.CIRCUIT_BREAKER_COOLDOWN // 60} min",
                    )
                    logger.warning(
                        f"Circuit breaker tripped: {self._consecutive_failures} consecutive failures"
                    )

        except Exception as exc:
            proposal["status"] = "failed"
            proposal["error"] = str(exc)
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

            try:
                git_run(["checkout", "main"])
            except Exception:
                pass

            self._log("proposal_execution_failed", {
                "id": proposal["id"],
                "error": str(exc),
            })
            logger.error(f"❌ Proposal execution failed: {exc}")

            # Progressive backoff + circuit breaker for exceptions too
            self._consecutive_failures += 1
            self._current_backoff = min(
                self._current_backoff * 2, self.BACKOFF_MAX
            )
            if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
                self._circuit_open_until = time.time() + self.CIRCUIT_BREAKER_COOLDOWN
                self._emit_activity(
                    "cooldown",
                    f"🛑 Circuit breaker tripped after {self._consecutive_failures} "
                    f"consecutive failures — cooling down for {self.CIRCUIT_BREAKER_COOLDOWN // 60} min",
                )

        self.status["execution"]["last_run"] = time.time()

    def get_status(self) -> dict:
        """Return the full autonomy status for the API."""
        return {
            **self.status,
            "uptime_seconds": round(time.time() - self.status["started_at"])
            if self.status["started_at"] else 0,
        }
