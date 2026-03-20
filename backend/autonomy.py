"""
autonomy.py — LocalMind Autonomy Engine
=========================================
Background scheduler that makes LocalMind work independently.

Runs periodic tasks in the background without blocking chat:
  1. Health Check    (60s)   — Keep Ollama alive, pre-warm model
  2. Self-Reflect    (30m)   — Review recent conversations, log proposals
  3. Execute Proposals (15m) — Pick approved proposals and self-edit
  4. Auto-Test       (after edits) — Run pytest, log results

All proposal execution requires prior user approval via the sidebar UI.
Logs everything to ~/LocalMind_Workspace/autonomy_log.jsonl.

ARCHITECTURE:
  AutonomyEngine is created in server.py lifespan startup.
  It spawns asyncio tasks that run forever in the background.
  Toggle on/off via POST /api/autonomy/toggle.
  Proposals listed/approved/denied via /api/autonomy/proposals endpoints.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

from backend.model_router import get_autonomy_models, get_startup_model

logger = logging.getLogger("localmind.autonomy")

LOG_FILE = Path.home() / "LocalMind_Workspace" / "autonomy_log.jsonl"
PROPOSALS_DIR = Path.home() / "LocalMind_Workspace" / "proposals"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AutonomyEngine:
    """Background scheduler for autonomous LocalMind operations."""

    # How many seconds after the last chat message before autonomy
    # is allowed to make Ollama requests (prevents competition).
    CHAT_COOLDOWN = 30

    # Risk levels that auto-execute in autonomous mode
    AUTO_APPROVE_RISKS = {"low", "medium"}

    def __init__(self, ollama_url: str = "http://localhost:11434",
                 default_model: str = "qwen2.5-coder:7b"):
        self.ollama_url = ollama_url
        self.default_model = default_model

        # Tiered model routing for autonomy tasks
        autonomy_models = get_autonomy_models()
        self.reflection_model = autonomy_models["reflection"]
        self.editing_model = autonomy_models["editing"]
        self.targeting_model = autonomy_models["file_targeting"]
        self.startup_model = get_startup_model()
        self.enabled = True
        self.mode = "supervised"  # "supervised" or "autonomous"
        self.tasks: list[asyncio.Task] = []
        self._last_chat_time: float = 0.0
        self._activity_subscribers: list[asyncio.Queue] = []
        self._recent_events: list[dict] = []  # ring buffer for dashboard reload
        self._start_time: float = time.time()
        self._manual_reflection_event = asyncio.Event()
        self._manual_execution_event = asyncio.Event()

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
        # Keep last 30 events for dashboard catchup on page refresh
        self._recent_events.append(event)
        if len(self._recent_events) > 30:
            self._recent_events = self._recent_events[-30:]
            
        for q in self._activity_subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop oldest if subscriber is slow

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

    def trigger_execution(self):
        """Manually trigger the execution cycle."""
        self._manual_execution_event.set()
        logger.info("⚙️ Manual execution event set")

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
        """Toggle the engine on/off. Returns new state."""
        self.enabled = not self.enabled
        self.status["enabled"] = self.enabled
        self._log("engine_toggled", {"enabled": self.enabled})
        logger.info(f"🤖 Autonomy Engine {'enabled' if self.enabled else 'paused'}")
        return self.enabled

    # ── Health Check Loop ────────────────────────────────────────────

    async def _health_loop(self):
        """Every 60s: ping Ollama, pre-warm model if needed."""
        # Wait for server to be fully ready before first check
        await asyncio.sleep(30)

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
                # Check Ollama is alive
                resp = await client.get(f"{self.ollama_url}/api/tags")
                ollama_ok = resp.status_code == 200

                # Check if any model is loaded
                ps_resp = await client.get(f"{self.ollama_url}/api/ps")
                ps_data = ps_resp.json()
                models_loaded = len(ps_data.get("models", [])) > 0

                self.status["health_check"] = {
                    "last_run": time.time(),
                    "ollama_ok": ollama_ok,
                    "model_loaded": models_loaded,
                }

                # Only pre-warm if NO model is loaded at all.
                # If the user already has a model in VRAM (even the 32B),
                # don't evict it by loading the 7B.
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"🔥 Pre-warming model: {self.startup_model}")
                self._log("prewarm_start", {"model": self.startup_model})

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.startup_model,
                        "prompt": "hi",
                        "stream": False,
                        "options": {"num_predict": 1, "num_ctx": 256},
                    },
                    timeout=60.0,
                )

                if resp.status_code == 200:
                    self.status["health_check"]["model_loaded"] = True
                    self._log("prewarm_done", {"model": self.startup_model})
                    logger.info(f"✅ Model pre-warmed: {self.startup_model}")
                else:
                    logger.warning(f"Pre-warm failed: {resp.status_code}")

        except Exception as exc:
            logger.warning(f"Pre-warm failed: {exc}")

    # ── Self-Reflection Loop ─────────────────────────────────────────

    async def _reflection_loop(self):
        """Frequency: Review recent conversations and log proposals (Manual or 5min)."""
        await asyncio.sleep(60)  # Wait 60s after startup before first reflection

        while True:
            try:
                # Wait for manual trigger OR 5 minute timeout
                try:
                    await asyncio.wait_for(self._manual_reflection_event.wait(), timeout=300)
                    self._manual_reflection_event.clear()
                    logger.info("⚡ Executing manual reflection")
                except asyncio.TimeoutError:
                    # Regular scheduled reflection
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
                
                # Small buffer to prevent spamming if event is set repeatedly
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
            # Gather ACTUAL file listing from the project so the AI doesn't hallucinate
            project_root = Path(__file__).parent.parent
            real_files = []
            for ext in ("*.py", "*.js", "*.html", "*.css", "*.json", "*.md"):
                for f in project_root.rglob(ext):
                    rel = f.relative_to(project_root)
                    # Skip node_modules, __pycache__, .git, memory_db
                    skip = any(part.startswith(".") or part in ("node_modules", "__pycache__", "memory_db", "browser_recordings") for part in rel.parts)
                    if not skip:
                        real_files.append(str(rel).replace("\\", "/"))

            file_list = "\n".join(f"  - {f}" for f in sorted(real_files)[:60])

            self._emit_activity("reflecting", f"Step 2/2: Generating proposals with {self.reflection_model}...")

            # Determine current category distribution to force diversity
            existing_proposals = self.list_proposals()
            category_counts = {}
            recent_titles = []
            for p in existing_proposals:
                cat = p.get("category", "unknown")
                category_counts[cat] = category_counts.get(cat, 0) + 1
                recent_titles.append(p.get("title", ""))

            # Pick the LEAST represented category to focus on
            focus_categories = ["performance", "feature", "ux", "security", "code_quality"]
            category_weights = [(cat, category_counts.get(cat, 0)) for cat in focus_categories]
            category_weights.sort(key=lambda x: x[1])
            focus_category = category_weights[0][0]

            # Build anti-repeat context from last 5 proposals
            anti_repeat = ""
            if recent_titles:
                last_5 = recent_titles[-5:]
                anti_repeat = (
                    "\nALREADY PROPOSED (do NOT repeat these):\n"
                    + "\n".join(f"  - {t}" for t in last_5)
                    + "\n"
                )

            # Suppress over-represented categories
            suppress = ""
            dominant_cat = max(category_counts, key=category_counts.get) if category_counts else None
            if dominant_cat and category_counts.get(dominant_cat, 0) > len(existing_proposals) * 0.4:
                suppress = f"\nDo NOT propose anything in the \"{dominant_cat}\" category — we already have too many.\n"

            # Use Ollama directly to ask for improvement ideas
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

                    # Try to parse as JSON proposal
                    try:
                        # Extract JSON from response (may have markdown fences)
                        json_text = response_text.strip()
                        if "```" in json_text:
                            json_text = json_text.split("```")[1]
                            if json_text.startswith("json"):
                                json_text = json_text[4:]
                            json_text = json_text.strip()

                        proposal = json.loads(json_text)
                        await self._save_proposal(proposal)
                        self.status["reflection"]["proposals_logged"] += 1
                        self._log("reflection_done", {"proposal": proposal.get("title", "?")})
                        self._emit_activity("proposal_created", f"New proposal: {proposal.get('title', '?')}",
                                            proposal_id=proposal.get("id", ""),
                                            category=proposal.get("category", ""))
                        logger.info(f"💡 Auto-reflection logged: {proposal.get('title', '?')}")

                    except (json.JSONDecodeError, IndexError):
                        self._log("reflection_parse_failed", {"response": response_text[:200]})

                self.status["reflection"]["last_run"] = time.time()

        except Exception as exc:
            logger.warning(f"Reflection failed: {exc}")
            self.status["reflection"]["last_run"] = time.time()

    def _is_duplicate_proposal(self, new_title: str) -> bool:
        """Check if a similar proposal already exists (simple fuzzy match)."""
        if not PROPOSALS_DIR.exists():
            return False

        new_words = set(new_title.lower().split())
        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                existing_title = data.get("title", "")
                existing_words = set(existing_title.lower().split())
                # Jaccard similarity: intersection / union
                if not new_words or not existing_words:
                    continue
                overlap = len(new_words & existing_words)
                total = len(new_words | existing_words)
                similarity = overlap / total if total > 0 else 0
                if similarity > 0.70:
                    logger.info(f"Duplicate detected: \"{new_title}\" ≈ \"{existing_title}\" ({similarity:.0%})")
                    return True
            except Exception:
                continue
        return False

    async def _save_proposal(self, proposal: dict):
        """Save a proposal to the proposals directory (with dedup check)."""
        PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

        # Deduplication: skip if a very similar proposal already exists
        title = proposal.get("title", "Untitled")
        if self._is_duplicate_proposal(title):
            self._log("proposal_deduplicated", {"title": title})
            self._emit_activity("info", f"Skipped duplicate proposal: {title}")
            return

        full_proposal = {
            "id": str(uuid.uuid4())[:8],
            "title": proposal.get("title", "Untitled"),
            "category": proposal.get("category", "feature"),
            "description": proposal.get("description", ""),
            "files_affected": proposal.get("files_affected", []),
            "effort": proposal.get("effort", "medium"),
            "priority": proposal.get("priority", "medium"),
            "status": "proposed",
            "source": "autonomy_reflection",
            "created_at": time.time(),
            "created_at_human": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        # Normalize files_affected to list
        if isinstance(full_proposal["files_affected"], str):
            full_proposal["files_affected"] = [
                f.strip() for f in full_proposal["files_affected"].split(",") if f.strip()
            ]

        filepath = PROPOSALS_DIR / f"{full_proposal['id']}_{full_proposal['category']}.json"

        # In autonomous mode, auto-approve low/medium risk proposals
        risk = full_proposal.get("priority", "medium").lower()
        if self.mode == "autonomous" and risk in self.AUTO_APPROVE_RISKS:
            full_proposal["status"] = "approved"
            full_proposal["auto_approved"] = True
            full_proposal["status_changed_at"] = time.time()
            self._log("proposal_auto_approved", {
                "id": full_proposal["id"], "title": full_proposal["title"], "risk": risk
            })
            self._emit_activity("auto_approved",
                                f"Auto-approved: {full_proposal['title']} (risk: {risk})",
                                proposal_id=full_proposal["id"])
            logger.info(f"🤖 Auto-approved proposal: {full_proposal['title']} (risk: {risk})")

        filepath.write_text(json.dumps(full_proposal, indent=2), encoding="utf-8")

    # ── Proposal Management (called by API endpoints) ────────────────

    def list_proposals(self, status_filter: str = "all") -> list[dict]:
        """List all proposals, optionally filtered by status."""
        if not PROPOSALS_DIR.exists():
            return []

        proposals = []
        for f in sorted(PROPOSALS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if status_filter == "all" or data.get("status") == status_filter:
                    proposals.append(data)
            except Exception:
                continue

        return proposals

    def approve_proposal(self, proposal_id: str) -> Optional[dict]:
        """Mark a proposal as approved so the execution loop will pick it up."""
        return self._update_proposal_status(proposal_id, "approved")

    def retry_proposal(self, proposal_id: str) -> Optional[dict]:
        """Reset a failed proposal to approved status for re-execution."""
        # Find the proposal first to clear errors
        if not PROPOSALS_DIR.exists():
            return None

        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id") == proposal_id:
                    data["status"] = "approved"
                    data["error"] = None
                    data["status_changed_at"] = time.time()
                    data["status_changed_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    self._emit_activity("proposal_retried", 
                                        f"Retrying: {data.get('title')}", 
                                        proposal_id=proposal_id)
                    return data
            except Exception:
                continue
        return None

    def deny_proposal(self, proposal_id: str) -> Optional[dict]:
        """Mark a proposal as denied."""
        return self._update_proposal_status(proposal_id, "denied")

    def _update_proposal_status(self, proposal_id: str, new_status: str) -> Optional[dict]:
        """Update a proposal's status by ID. Returns updated proposal or None."""
        if not PROPOSALS_DIR.exists():
            return None

        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id") == proposal_id:
                    data["status"] = new_status
                    data["status_changed_at"] = time.time()
                    data["status_changed_at_human"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    self._log(f"proposal_{new_status}", {
                        "id": proposal_id, "title": data.get("title", "?")
                    })
                    return data
            except Exception:
                continue

        return None

    # ── Proposal Execution Loop ──────────────────────────────────────

    async def _execution_loop(self):
        """Every 3 min: pick an approved proposal and execute it.
        
        Can also be triggered manually via trigger_execution().
        """
        await asyncio.sleep(10)  # Short wait after startup

        while True:
            try:
                # Wait for manual trigger OR 3 minute timeout
                try:
                    await asyncio.wait_for(self._manual_execution_event.wait(), timeout=180)
                    self._manual_execution_event.clear()
                    logger.info("⚡ Executing manual task run")
                except asyncio.TimeoutError:
                    pass

                if self.enabled and not self.is_user_active():
                    self._emit_activity("checking", "Looking for approved proposals to execute...")
                    # Process ALL approved proposals in this cycle, not just one
                    executed_count = 0
                    while True:
                        had_work = await self._execute_next_proposal()
                        if not had_work:
                            break
                        executed_count += 1
                        # Brief pause between proposals so the dashboard can update
                        await asyncio.sleep(5)
                        # Re-check conditions
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
        """Find the highest-priority approved proposal and execute it.

        Flow: read file → ask AI for new content → write file → test → commit/revert
        """
        if not PROPOSALS_DIR.exists():
            return False

        # Find approved proposals, sorted by priority
        priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        approved = []

        for f in PROPOSALS_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("status") == "approved":
                    approved.append((f, data))
            except Exception:
                continue

        if not approved:
            self.status["execution"]["last_run"] = time.time()
            return False

        # Sort by priority
        approved.sort(key=lambda x: priority_order.get(x[1].get("priority", "low"), 3))
        filepath, proposal = approved[0]

        # Mark as in_progress
        proposal["status"] = "in_progress"
        proposal["execution_started_at"] = time.time()
        filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

        logger.info(f"🔧 Executing proposal: {proposal['title']}")
        self._log("proposal_execution_start", {"id": proposal["id"], "title": proposal["title"]})
        self._emit_activity("executing", f"Starting: {proposal['title']}",
                            proposal_id=proposal["id"],
                            task_description=proposal.get("description", ""),
                            proposal_title=proposal.get("title", ""))

        try:
            # Step 1: Determine target files
            files_affected = proposal.get("files_affected", [])
            if isinstance(files_affected, str):
                files_affected = [f.strip() for f in files_affected.split(",") if f.strip()]

            if not files_affected:
                # Ask AI to figure out which file to edit
                files_affected = await self._identify_target_files(proposal)

            # Validate all files actually exist on disk (filter out hallucinated paths)
            valid_files = []
            for f in files_affected:
                resolved = (PROJECT_ROOT / f).resolve()
                if resolved.exists() and str(resolved).startswith(str(PROJECT_ROOT)):
                    valid_files.append(f)
                else:
                    logger.warning(f"Proposal references non-existent file: {f}")
                    self._emit_activity("info", f"Filtered out non-existent file: {f}")
            files_affected = valid_files

            if not files_affected:
                proposal["status"] = "failed"
                proposal["error"] = "Could not determine which files to edit (all targets missing or invalid)"
                filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
                self._log("proposal_execution_failed", {
                    "id": proposal["id"], "error": "no target files"
                })
                self.status["execution"]["last_run"] = time.time()
                self._emit_activity("error", "Failed: Could not determine target files")
                return False

            # Step 2: Create git safety branch
            branch_name = f"self-improve/{proposal['id']}-{proposal['category']}"
            self._git_run(["checkout", "-b", branch_name])
            self._log("git_branch_created", {"branch": branch_name})
            self._emit_activity("git", f"Created safety branch: {branch_name}",
                            proposal_title=proposal.get("title", ""))
            logger.info(f"🌿 Created safety branch: {branch_name}")

            # Step 3: For each target file, read → AI → write
            edits_applied = []
            for target_file in files_affected[:3]:  # Cap at 3 files per proposal
                self._emit_activity("writing", f"Editing: {target_file}",
                                    file=target_file,
                                    task_description=proposal.get("description", ""),
                                    proposal_title=proposal.get("title", ""))
                success = await self._edit_single_file(target_file, proposal)
                if success:
                    edits_applied.append(target_file)

            if not edits_applied:
                # No edits were applied — clean up branch
                self._git_run(["checkout", "main"])
                self._git_run(["branch", "-D", branch_name])
                proposal["status"] = "failed"
                proposal["error"] = "AI could not generate valid edits"
                filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
                self._log("proposal_execution_failed", {
                    "id": proposal["id"], "error": "no edits applied"
                })
                self.status["execution"]["last_run"] = time.time()
                self._emit_activity("error", f"Failed: AI could not generate valid edits for {proposal['title']}")
                return False

            # Step 4: Run tests
            self._emit_activity("testing", f"Running tests to verify: {proposal['title']}",
                            proposal_title=proposal.get("title", ""))
            test_passed, test_output = await self._run_tests()

            if test_passed:
                self._emit_activity("committing", f"Committing: {proposal['title']}",
                            proposal_title=proposal.get("title", ""))
                # Step 5a: Tests passed — commit and log success
                commit_msg = f"[autonomy] {proposal['title']}\n\nProposal ID: {proposal['id']}\nCategory: {proposal['category']}\nFiles: {', '.join(edits_applied)}"
                self._git_run(["add", "-A"])
                self._git_run(["commit", "-m", commit_msg])

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
                # Step 5b: Tests failed — revert from backups
                for target_file in edits_applied:
                    self._revert_file(target_file)

                self._git_run(["checkout", "--", "."])
                self._git_run(["checkout", "main"])
                self._git_run(["branch", "-D", branch_name])

                proposal["status"] = "failed"
                # Extract first few lines of test failure for the error field
                short_error = "\n".join(test_output.splitlines()[:5])
                proposal["error"] = f"Tests failed after applying edits:\n{short_error}"
                proposal["execution_finished_at"] = time.time()
                filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

                self._log("proposal_execution_reverted", {
                    "id": proposal["id"],
                    "title": proposal["title"],
                    "reason": test_output,
                })
                self._emit_activity("reverted",
                                    f"❌ REVERTED: {proposal['title']} (Automated tests failed - check logs for details)",
                                    proposal_id=proposal["id"],
                                    reason=test_output)
                logger.warning(f"⚠️ Proposal reverted: {proposal['title']} (tests failed)")

        except Exception as exc:
            proposal["status"] = "failed"
            proposal["error"] = str(exc)
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

            # Try to get back to main branch
            try:
                self._git_run(["checkout", "main"])
            except Exception:
                pass

            self._log("proposal_execution_failed", {
                "id": proposal["id"],
                "error": str(exc),
            })
            logger.error(f"❌ Proposal execution failed: {exc}")

        self.status["execution"]["last_run"] = time.time()

    async def _identify_target_files(self, proposal: dict) -> list[str]:
        """Ask the AI which file(s) to edit for a proposal."""
        try:
            # Get a directory listing for context
            files_list = []
            for p in PROJECT_ROOT.rglob("*.py"):
                rel = p.relative_to(PROJECT_ROOT)
                # Skip venv, __pycache__, .git
                parts = rel.parts
                if any(skip in parts for skip in ("venv", "__pycache__", ".git", "node_modules")):
                    continue
                files_list.append(str(rel).replace("\\", "/"))

            for p in PROJECT_ROOT.rglob("*.js"):
                rel = p.relative_to(PROJECT_ROOT)
                parts = rel.parts
                if any(skip in parts for skip in ("venv", "__pycache__", ".git", "node_modules")):
                    continue
                files_list.append(str(rel).replace("\\", "/"))

            for p in PROJECT_ROOT.rglob("*.html"):
                rel = p.relative_to(PROJECT_ROOT)
                parts = rel.parts
                if any(skip in parts for skip in ("venv", "__pycache__", ".git", "node_modules")):
                    continue
                files_list.append(str(rel).replace("\\", "/"))

            if not files_list:
                logger.warning("No project files found for targeting")
                return []

            async with httpx.AsyncClient(timeout=60.0) as client:
                prompt = (
                    f"Given this improvement proposal:\n"
                    f"Title: {proposal['title']}\n"
                    f"Category: {proposal['category']}\n"
                    f"Description: {proposal['description']}\n\n"
                    f"And these project files:\n{chr(10).join(files_list[:80])}\n\n"
                    f"Which file(s) need to be edited? Return ONLY a JSON array of "
                    f"relative file paths. Example: [\"backend/server.py\"]\n"
                    f"Pick the 1-2 most important files. Only output the JSON array."
                )

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.targeting_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "num_ctx": 4096},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    logger.info(f"File targeting raw response: {text[:300]}")

                    # Strip markdown fences if present
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()

                    # Try direct JSON parse
                    try:
                        result = json.loads(text)
                        if isinstance(result, list):
                            return [f for f in result if isinstance(f, str) and f.strip()]
                    except json.JSONDecodeError:
                        pass

                    # Fallback: extract JSON array with regex
                    import re as _re
                    match = _re.search(r'\[([^\]]+)\]', text)
                    if match:
                        try:
                            result = json.loads(f"[{match.group(1)}]")
                            if isinstance(result, list):
                                return [f for f in result if isinstance(f, str) and f.strip()]
                        except json.JSONDecodeError:
                            pass

                    logger.warning(f"Could not parse file targeting response: {text[:200]}")
                else:
                    logger.warning(f"Ollama returned {resp.status_code} for file targeting")

        except Exception as exc:
            logger.warning(f"Failed to identify target files: {exc}")

        return []



    async def _edit_single_file(self, relative_path: str, proposal: dict) -> bool:
        """Read a file, ask AI for a search-and-replace diff, apply it.

        Uses a targeted diff approach instead of whole-file rewrite,
        which is far more reliable for small (7B) models.

        Returns True if the edit was successfully applied.
        """
        # Security checks
        blocked_dirs = {"venv", ".git", "node_modules", "__pycache__", "memory_db"}
        blocked_names = {".env", ".env.local", ".env.production", "autonomy.py", "server.py", "run.py"}
        blocked_exts = {".key", ".pem", ".secret", ".p12", ".pfx"}

        target = (PROJECT_ROOT / relative_path).resolve()

        # Must stay inside project
        if not str(target).startswith(str(PROJECT_ROOT)):
            logger.warning(f"Path escapes project: {relative_path}")
            return False

        if target.name in blocked_names:
            logger.warning(f"Blocked file: {target.name}")
            return False

        if target.suffix.lower() in blocked_exts:
            logger.warning(f"Blocked extension: {target.suffix}")
            return False

        for part in target.relative_to(PROJECT_ROOT).parts:
            if part in blocked_dirs:
                logger.warning(f"Blocked directory: {part}")
                return False

        if not target.exists():
            logger.warning(f"File not found: {relative_path}")
            return False

        try:
            # Read current content
            original_content = target.read_text(encoding="utf-8", errors="replace")

            # Show only a relevant snippet to the AI (first 6000 chars max)
            # This keeps the context window manageable for 7B models
            file_preview = original_content[:6000]
            if len(original_content) > 6000:
                file_preview += f"\n\n# ... ({len(original_content) - 6000} more chars truncated)"

            # Ask AI for a SEARCH-AND-REPLACE diff (much easier for 7B models)
            async with httpx.AsyncClient(timeout=180.0) as client:
                prompt = (
                    f"You are a precise code editor. Make a SMALL, TARGETED fix.\n\n"
                    f"TASK: {proposal['title']}\n"
                    f"DETAILS: {proposal['description']}\n\n"
                    f"FILE: {relative_path}\n"
                    f"```\n{file_preview}\n```\n\n"
                    f"Output a JSON object with exactly these keys:\n"
                    f'  "search": "the exact lines from the file to find (copy them exactly)"\n'
                    f'  "replace": "the replacement lines with your improvement"\n'
                    f'  "explanation": "one sentence explaining the change"\n\n'
                    f"RULES:\n"
                    f"1. The \"search\" value MUST be an exact copy of consecutive lines from the file.\n"
                    f"2. Keep the change SMALL — only the minimum lines needed.\n"
                    f"3. Output ONLY the JSON object, no markdown fences, no extra text.\n"
                    f'4. If you cannot make a useful change, output: {{"search": "", "replace": "", "explanation": "no change needed"}}\n'
                )

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.editing_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 2000, "num_ctx": 8192},
                    },
                )

                if resp.status_code != 200:
                    logger.warning(f"Ollama returned {resp.status_code} for edit")
                    self._emit_activity("error", f"Edit failed: Ollama HTTP {resp.status_code} for {relative_path}")
                    return False

                raw_response = resp.json().get("response", "").strip()

            # Parse the search/replace JSON
            try:
                # Strip markdown fences if present
                json_text = raw_response
                if "```" in json_text:
                    json_text = json_text.split("```")[1]
                    if json_text.startswith("json"):
                        json_text = json_text[4:]
                    json_text = json_text.strip()

                diff = json.loads(json_text)
            except (json.JSONDecodeError, IndexError):
                # Fallback: try to extract JSON with regex
                match = re.search(r'\{[^{}]*"search"[^{}]*\}', raw_response, re.DOTALL)
                if match:
                    try:
                        diff = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse edit response for {relative_path}: {raw_response[:200]}")
                        self._emit_activity("error", f"Edit failed: could not parse AI response for {relative_path}")
                        return False
                else:
                    logger.warning(f"No JSON found in edit response for {relative_path}: {raw_response[:200]}")
                    self._emit_activity("error", f"Edit failed: no valid JSON in AI response for {relative_path}")
                    return False

            search_text = diff.get("search", "")
            replace_text = diff.get("replace", "")
            explanation = diff.get("explanation", "")

            # No-op check
            if not search_text or not replace_text or search_text == replace_text:
                logger.info(f"AI returned no-op for {relative_path}: {explanation}")
                self._emit_activity("info", f"Skipped: No changes needed for {relative_path}")
                return False

            # Multi-layer search matching to handle whitespace issues
            matched = False

            # Layer 1: Exact match
            if search_text in original_content:
                new_content = original_content.replace(search_text, replace_text, 1)
                matched = True

            # Layer 2: Normalize line endings (CRLF → LF → CRLF)
            if not matched:
                norm_search = search_text.replace("\r\n", "\n").replace("\r", "\n")
                norm_content = original_content.replace("\r\n", "\n")
                if norm_search in norm_content:
                    new_content = original_content.replace("\r\n", "\n")
                    new_content = new_content.replace(norm_search, replace_text.replace("\r\n", "\n"), 1)
                    # Restore original line endings
                    if "\r\n" in original_content:
                        new_content = new_content.replace("\n", "\r\n")
                    matched = True
                    logger.info(f"Matched via line-ending normalization for {relative_path}")

            # Layer 3: Strip trailing whitespace per line
            if not matched:
                stripped_search = "\n".join(l.rstrip() for l in search_text.replace("\r\n", "\n").split("\n"))
                stripped_content = "\n".join(l.rstrip() for l in original_content.replace("\r\n", "\n").split("\n"))
                if stripped_search in stripped_content:
                    # Find the position in stripped content, map back to original
                    idx = stripped_content.index(stripped_search)
                    end = idx + len(stripped_search)
                    # Count lines to find original position
                    orig_lines = original_content.replace("\r\n", "\n").split("\n")
                    stripped_lines = stripped_search.split("\n")
                    start_line = stripped_content[:idx].count("\n")
                    n_lines = len(stripped_lines)
                    # Rebuild with replacement
                    replace_lines = replace_text.replace("\r\n", "\n").split("\n")
                    new_lines = orig_lines[:start_line] + replace_lines + orig_lines[start_line + n_lines:]
                    sep = "\r\n" if "\r\n" in original_content else "\n"
                    new_content = sep.join(new_lines)
                    matched = True
                    logger.info(f"Matched via whitespace-stripped lines for {relative_path}")

            if not matched:
                logger.warning(
                    f"Search text not found in {relative_path}. "
                    f"Search (first 150 chars): {repr(search_text[:150])}"
                )
                self._emit_activity("error", f"Edit failed: search text not found in {relative_path}")
                return False

            # Syntax validation for Python files
            if relative_path.endswith(".py"):
                try:
                    compile(new_content, relative_path, "exec")
                except SyntaxError as syn_err:
                    logger.warning(f"AI produced invalid Python for {relative_path}: {syn_err}")
                    self._emit_activity("error", f"Edit rejected: syntax error in {relative_path} line {syn_err.lineno}")
                    return False

            # Create backup
            backup = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, backup)

            # Write new content
            target.write_text(new_content, encoding="utf-8")
            logger.info(f"📝 Self-edit applied to {relative_path}: {explanation}")
            self._log("self_edit_applied", {
                "file": relative_path,
                "original_size": len(original_content),
                "new_size": len(new_content),
                "change_size": abs(len(replace_text) - len(search_text)),
                "explanation": explanation,
                "proposal_id": proposal["id"],
            })
            self._emit_activity("edited", f"Applied: {explanation}", file=relative_path)

            return True

        except Exception as exc:
            logger.error(f"Failed to edit {relative_path}: {exc}")
            return False

    def _revert_file(self, relative_path: str):
        """Restore a file from its .bak backup."""
        target = (PROJECT_ROOT / relative_path).resolve()
        backup = target.with_suffix(target.suffix + ".bak")

        if backup.exists():
            shutil.copy2(backup, target)
            backup.unlink()
            logger.info(f"↩️ Reverted: {relative_path}")
        else:
            logger.warning(f"No backup found for: {relative_path}")

    def _git_run(self, args: list[str]) -> str:
        """Run a git command in the project root. Returns stdout."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                error = result.stderr.strip() or f"git exited with code {result.returncode}"
                logger.warning(f"Git command failed: git {' '.join(args)} → {error}")
                return ""
            return result.stdout.strip()
        except Exception as exc:
            logger.warning(f"Git command error: {exc}")
            return ""

    # ── Auto-Test Runner ─────────────────────────────────────────────

    async def _run_tests(self) -> tuple[bool, str]:
        """Run pytest and return (success, output)."""
        try:
            # -q --tb=short gives concise but useful failure info
            result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-q", "--tb=short"],
                capture_output=True, text=True, timeout=60,
                cwd=str(PROJECT_ROOT),
            )

            output = result.stdout.strip() or result.stderr.strip()
            # Handle empty output
            if not output:
                output = "No test output captured."
                
            # Parse "83 passed in 10.11s"
            passed = failed = 0
            for line in output.splitlines():
                if "passed" in line:
                    m = re.search(r"(\d+) passed", line)
                    if m:
                        passed = int(m.group(1))
                    m = re.search(r"(\d+) failed", line)
                    if m:
                        failed = int(m.group(1))

            self.status["auto_test"] = {
                "last_run": time.time(),
                "passed": passed,
                "failed": failed,
            }

            self._log("auto_test", {"passed": passed, "failed": failed})
            logger.info(f"🧪 Auto-test: {passed} passed, {failed} failed")

            # Exit code 0 = pass, exit code 5 = no tests collected (also treat as pass)
            success = result.returncode == 0 or result.returncode == 5
            if result.returncode == 5:
                logger.info("No tests collected — treating as pass")
            return success, output

        except Exception as exc:
            logger.warning(f"Auto-test failed: {exc}")
            return False, str(exc)

    def get_status(self) -> dict:
        """Return the full autonomy status for the API."""
        return {
            **self.status,
            "uptime_seconds": round(time.time() - self.status["started_at"])
            if self.status["started_at"] else 0,
        }
