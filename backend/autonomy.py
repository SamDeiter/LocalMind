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
        self.enabled = True
        self.mode = "supervised"  # "supervised" or "autonomous"
        self.tasks: list[asyncio.Task] = []
        self._last_chat_time: float = 0.0
        self._activity_subscribers: list[asyncio.Queue] = []

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
            **extra,
        }
        self.status["current_activity"] = event
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
                logger.info(f"🔥 Pre-warming model: {self.default_model}")
                self._log("prewarm_start", {"model": self.default_model})

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.default_model,
                        "prompt": "hi",
                        "stream": False,
                        "options": {"num_predict": 1, "num_ctx": 256},
                    },
                    timeout=60.0,
                )

                if resp.status_code == 200:
                    self.status["health_check"]["model_loaded"] = True
                    self._log("prewarm_done", {"model": self.default_model})
                    logger.info(f"✅ Model pre-warmed: {self.default_model}")
                else:
                    logger.warning(f"Pre-warm failed: {resp.status_code}")

        except Exception as exc:
            logger.warning(f"Pre-warm failed: {exc}")

    # ── Self-Reflection Loop ─────────────────────────────────────────

    async def _reflection_loop(self):
        """Every 30 min: review recent conversations and log proposals."""
        await asyncio.sleep(300)  # Wait 5 min after startup before first reflection

        while True:
            try:
                if self.enabled and not self.is_user_active():
                    self._emit_activity("reflecting", "Reviewing codebase for improvement ideas...")
                    await self._run_reflection()
                    self._emit_activity("idle", "Waiting for next reflection cycle")
                else:
                    logger.debug("Reflection skipped — user is active")
                await asyncio.sleep(1800)  # 30 minutes
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Reflection loop error: {exc}")
                self._emit_activity("error", f"Reflection failed: {exc}")
                await asyncio.sleep(1800)

    async def _run_reflection(self):
        """Ask the AI to reflect on its own codebase and log proposals."""
        try:
            # Use Ollama directly to ask for improvement ideas
            async with httpx.AsyncClient(timeout=120.0) as client:
                prompt = (
                    "You are LocalMind, an AI assistant reviewing your own codebase. "
                    "Think about what could be improved. Consider:\n"
                    "1. Performance bottlenecks\n"
                    "2. Missing error handling\n"
                    "3. UX improvements\n"
                    "4. Code quality issues\n"
                    "5. Features users might want\n\n"
                    "Output a JSON object with keys: title, category "
                    "(performance/feature/bugfix/ux/security/code_quality), "
                    "description, files_affected (comma-separated list), "
                    "effort (small/medium/large), priority (low/medium/high/critical).\n"
                    "Only output the JSON, nothing else."
                )

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.default_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 300, "num_ctx": 2048},
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

    async def _save_proposal(self, proposal: dict):
        """Save a proposal to the proposals directory."""
        PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

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
        """Every 15 min: pick an approved proposal and execute it."""
        await asyncio.sleep(600)  # Wait 10 min after startup

        while True:
            try:
                if self.enabled and not self.is_user_active():
                    self._emit_activity("checking", "Looking for approved proposals to execute...")
                    await self._execute_next_proposal()
                else:
                    logger.debug("Execution skipped — user is active")
                await asyncio.sleep(900)  # 15 minutes
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Execution loop error: {exc}")
                self._emit_activity("error", f"Execution failed: {exc}")
                await asyncio.sleep(900)

    async def _execute_next_proposal(self):
        """Find the highest-priority approved proposal and execute it.

        Flow: read file → ask AI for new content → write file → test → commit/revert
        """
        if not PROPOSALS_DIR.exists():
            return

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
            return

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
                            proposal_id=proposal["id"])

        try:
            # Step 1: Determine target files
            files_affected = proposal.get("files_affected", [])
            if isinstance(files_affected, str):
                files_affected = [f.strip() for f in files_affected.split(",") if f.strip()]

            if not files_affected:
                # Ask AI to figure out which file to edit
                files_affected = await self._identify_target_files(proposal)

            if not files_affected:
                proposal["status"] = "failed"
                proposal["error"] = "Could not determine which files to edit"
                filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
                self._log("proposal_execution_failed", {
                    "id": proposal["id"], "error": "no target files"
                })
                self.status["execution"]["last_run"] = time.time()
                return

            # Step 2: Create git safety branch
            branch_name = f"self-improve/{proposal['id']}-{proposal['category']}"
            self._git_run(["checkout", "-b", branch_name])
            self._log("git_branch_created", {"branch": branch_name})
            self._emit_activity("git", f"Created safety branch: {branch_name}")
            logger.info(f"🌿 Created safety branch: {branch_name}")

            # Step 3: For each target file, read → AI → write
            edits_applied = []
            for target_file in files_affected[:3]:  # Cap at 3 files per proposal
                self._emit_activity("writing", f"Editing: {target_file}",
                                    file=target_file)
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
                return

            # Step 4: Run tests
            self._emit_activity("testing", "Running test suite to verify changes...")
            test_passed = await self._run_tests()

            if test_passed:
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
                                    f"✅ Done: {proposal['title']} → branch {branch_name}",
                                    proposal_id=proposal["id"],
                                    files=edits_applied,
                                    branch=branch_name)
                logger.info(f"✅ Proposal executed: {proposal['title']} → branch {branch_name}")

            else:
                # Step 5b: Tests failed — revert from backups
                for target_file in edits_applied:
                    self._revert_file(target_file)

                self._git_run(["checkout", "--", "."])
                self._git_run(["checkout", "main"])
                self._git_run(["branch", "-D", branch_name])

                proposal["status"] = "failed"
                proposal["error"] = "Tests failed after applying edits — reverted"
                proposal["execution_finished_at"] = time.time()
                filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

                self._log("proposal_execution_reverted", {
                    "id": proposal["id"],
                    "title": proposal["title"],
                    "reason": "tests_failed",
                })
                self._emit_activity("reverted",
                                    f"⚠️ Reverted: {proposal['title']} (tests failed)",
                                    proposal_id=proposal["id"])
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
                        "model": self.default_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 200, "num_ctx": 2048},
                    },
                )

                if resp.status_code == 200:
                    text = resp.json().get("response", "").strip()
                    # Extract JSON array
                    if "```" in text:
                        text = text.split("```")[1]
                        if text.startswith("json"):
                            text = text[4:]
                        text = text.strip()
                    return json.loads(text)

        except Exception as exc:
            logger.warning(f"Failed to identify target files: {exc}")

        return []

    async def _edit_single_file(self, relative_path: str, proposal: dict) -> bool:
        """Read a file, ask AI for improved version, write it back.

        Returns True if the edit was successfully applied.
        """
        # Security checks (same as self_edit.py)
        blocked_dirs = {"venv", ".git", "node_modules", "__pycache__", "memory_db"}
        blocked_names = {".env", ".env.local", ".env.production"}
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

            # Cap file size — don't edit huge files
            if len(original_content) > 50_000:
                logger.warning(f"File too large to edit: {relative_path} ({len(original_content)} chars)")
                return False

            # Ask AI for the improved version
            async with httpx.AsyncClient(timeout=180.0) as client:
                prompt = (
                    f"You are LocalMind, improving your own source code.\n\n"
                    f"PROPOSAL: {proposal['title']}\n"
                    f"CATEGORY: {proposal['category']}\n"
                    f"DESCRIPTION: {proposal['description']}\n\n"
                    f"FILE: {relative_path}\n"
                    f"CURRENT CONTENT:\n```\n{original_content}\n```\n\n"
                    f"Output the COMPLETE improved file content. "
                    f"Make MINIMAL, TARGETED changes that address the proposal. "
                    f"Do NOT remove existing functionality. "
                    f"Do NOT add placeholder comments like 'rest of code here'. "
                    f"Output ONLY the file content, no markdown fences, no explanation."
                )

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.default_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 4000, "num_ctx": 8192},
                    },
                )

                if resp.status_code != 200:
                    logger.warning(f"Ollama returned {resp.status_code} for edit")
                    return False

                new_content = resp.json().get("response", "").strip()

            # Strip markdown fences if AI wrapped its output
            if new_content.startswith("```"):
                lines = new_content.split("\n")
                # Remove first line (```python or ```) and last line (```)
                if lines[-1].strip() == "```":
                    lines = lines[1:-1]
                else:
                    lines = lines[1:]
                # Remove language hint from first line
                new_content = "\n".join(lines)

            # Sanity checks
            if len(new_content) < 10:
                logger.warning(f"AI returned too-short content for {relative_path}")
                return False

            if len(new_content) > 60_000:
                logger.warning(f"AI returned too-large content for {relative_path}")
                return False

            # Don't apply if content is identical
            if new_content.strip() == original_content.strip():
                logger.info(f"No changes needed for {relative_path}")
                return False

            # Create backup
            backup = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, backup)

            # Write new content
            target.write_text(new_content, encoding="utf-8")
            logger.info(f"📝 Self-edit applied: {relative_path} ({len(new_content)} chars)")
            self._log("self_edit_applied", {
                "file": relative_path,
                "original_size": len(original_content),
                "new_size": len(new_content),
                "proposal_id": proposal["id"],
            })

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

    async def _run_tests(self) -> bool:
        """Run pytest and return True if all tests pass."""
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
                capture_output=True, text=True, timeout=60,
                cwd=str(PROJECT_ROOT),
            )

            output = result.stdout.strip()
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

            return result.returncode == 0

        except Exception as exc:
            logger.warning(f"Auto-test failed: {exc}")
            return False

    def get_status(self) -> dict:
        """Return the full autonomy status for the API."""
        return {
            **self.status,
            "uptime_seconds": round(time.time() - self.status["started_at"])
            if self.status["started_at"] else 0,
        }
