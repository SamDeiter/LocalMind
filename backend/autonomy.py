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
import time
from pathlib import Path

import httpx

from backend.model_router import get_autonomy_models, get_startup_model
from backend.proposals import ProposalManager, PROPOSALS_DIR
from backend.code_editor import edit_single_file, identify_target_files
from backend.git_ops import git_run, revert_file, run_tests

logger = logging.getLogger("localmind.autonomy")

LOG_FILE = Path.home() / "LocalMind_Workspace" / "autonomy_log.jsonl"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AutonomyEngine:
    """Background scheduler for autonomous LocalMind operations."""

    CHAT_COOLDOWN = 30

    # Risk levels that auto-execute in autonomous mode
    AUTO_APPROVE_RISKS = {"low", "medium"}

    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url
        models = get_autonomy_models()
        self.reflection_model = models.get("reflection", "qwen2.5-coder:7b")
        self.editing_model = models.get("editing", "qwen2.5-coder:7b")
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

        # Delegate proposal management
        self.proposals = ProposalManager()

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
        """Every 5min: review codebase and log proposals."""
        await asyncio.sleep(60)
        while True:
            try:
                try:
                    await asyncio.wait_for(self._manual_reflection_event.wait(), timeout=300)
                    self._manual_reflection_event.clear()
                    logger.info("⚡ Executing manual reflection")
                except asyncio.TimeoutError:
                    pass

                if self.enabled and not self.is_user_active():
                    self._emit_activity("reflecting", "Step 1/2: Analyzing project structure...")
                    await self._run_reflection()
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

    async def _run_reflection(self):
        """Ask the AI to reflect on its own codebase and log proposals."""
        try:
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

            self._emit_activity("reflecting", f"Step 2/2: Generating proposals with {self.reflection_model}...")

            # Category distribution for diversity
            existing_proposals = self.proposals.list_proposals()
            category_counts = {}
            for p in existing_proposals:
                cat = p.get("category", "unknown")
                category_counts[cat] = category_counts.get(cat, 0) + 1

            focus_categories = ["performance", "feature", "ux", "security", "code_quality"]
            category_weights = [(cat, category_counts.get(cat, 0)) for cat in focus_categories]
            category_weights.sort(key=lambda x: x[1])
            focus_category = category_weights[0][0]

            # Anti-repeat: recent + failed titles
            all_blocked = self.proposals.get_anti_repeat_titles()
            anti_repeat = ""
            if all_blocked:
                anti_repeat = (
                    "\nALREADY PROPOSED OR FAILED (do NOT repeat or rephrase these):\n"
                    + "\n".join(f"  - {t}" for t in all_blocked)
                    + "\nDo NOT propose variations of the above topics.\n"
                )

            # Suppress over-represented categories
            suppress = ""
            dominant_cat = max(category_counts, key=category_counts.get) if category_counts else None
            if dominant_cat and category_counts.get(dominant_cat, 0) > len(existing_proposals) * 0.4:
                suppress = f"\nDo NOT propose anything in the \"{dominant_cat}\" category — we already have too many.\n"

            async with httpx.AsyncClient(timeout=120.0) as client:
                prompt = (
                    "You are LocalMind, reviewing your OWN codebase to find improvements.\n\n"
                    "HERE ARE THE ACTUAL FILES IN THIS PROJECT:\n"
                    f"{file_list}\n\n"
                    f"FOCUS AREA: Look specifically for \"{focus_category}\" improvements.\n"
                    f"{anti_repeat}"
                    f"{suppress}"
                    "RULES:\n"
                    "- Only suggest changes to files listed above\n"
                    "- Be specific — reference real file names\n"
                    "- DO NOT invent files like 'auth.py' or 'database.py' that don't exist\n"
                    "- Think creatively — suggest something NOVEL, not just error handling\n\n"
                    "Output a JSON object with keys: title, category "
                    "(performance/feature/bugfix/ux/security/code_quality), "
                    "description, files_affected (list of real filenames from above), "
                    "effort (small/medium/large), priority (low/medium/high/critical).\n"
                    "Only output the JSON, nothing else."
                )

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.reflection_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 400, "num_ctx": 4096},
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
                            logger.info(f"💡 Auto-reflection logged: {saved.get('title', '?')}")

                    except (json.JSONDecodeError, IndexError):
                        self._log("reflection_parse_failed", {"response": response_text[:200]})

                self.status["reflection"]["last_run"] = time.time()

        except Exception as exc:
            logger.warning(f"Reflection failed: {exc}")
            self.status["reflection"]["last_run"] = time.time()

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

    async def _execution_loop(self):
        """Every 3 min: pick an approved proposal and execute it."""
        await asyncio.sleep(10)
        while True:
            try:
                try:
                    await asyncio.wait_for(self._manual_execution_event.wait(), timeout=180)
                    self._manual_execution_event.clear()
                    logger.info("⚡ Executing manual task run")
                except asyncio.TimeoutError:
                    pass

                if self.enabled and not self.is_user_active():
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
        """Find the highest-priority approved proposal and execute it."""
        proposals = self.proposals.list_proposals("approved")
        if not proposals:
            return False

        # Sort by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        proposals.sort(key=lambda p: priority_order.get(p.get("priority", "medium"), 2))
        proposal = proposals[0]

        filepath = PROPOSALS_DIR / f"{proposal['id']}_{proposal['category']}.json"

        try:
            self._emit_activity("executing",
                                f"Starting: {proposal['title']}",
                                proposal_id=proposal["id"],
                                proposal_title=proposal.get("title", ""))
            self._log("proposal_execution_start", {
                "id": proposal["id"], "title": proposal["title"]
            })

            # Step 1: Identify target files
            files_affected = await identify_target_files(
                proposal, self.ollama_url, self.editing_model, self._emit_activity
            )

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
                success = await edit_single_file(
                    target_file, proposal,
                    self.ollama_url, self.editing_model,
                    log_fn=self._log, emit_activity=self._emit_activity
                )
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

                proposal["status"] = "completed"
                proposal["execution_result"] = f"✅ Applied to {len(edits_applied)} file(s), tests passed"
                proposal["execution_finished_at"] = time.time()
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

        self.status["execution"]["last_run"] = time.time()

    def get_status(self) -> dict:
        """Return the full autonomy status for the API."""
        return {
            **self.status,
            "uptime_seconds": round(time.time() - self.status["started_at"])
            if self.status["started_at"] else 0,
        }
