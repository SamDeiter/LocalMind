"""
autonomy.py — LocalMind Autonomy Engine
=========================================
Background scheduler that makes LocalMind work independently.

Runs periodic tasks in the background without blocking chat:
  1. Health Check    (60s)   — Keep Ollama alive, pre-warm model
  2. Self-Reflect    (30m)   — Review recent conversations, log proposals
  3. Execute Proposals (15m) — Pick approved proposals and self-edit
  4. Auto-Test       (after edits) — Run pytest, log results

All proposal execution requires prior user approval.
Logs everything to ~/LocalMind_Workspace/autonomy_log.jsonl.

ARCHITECTURE:
  AutonomyEngine is created in server.py lifespan startup.
  It spawns asyncio tasks that run forever in the background.
  Toggle on/off via POST /api/autonomy/toggle.
"""

import asyncio
import json
import logging
import time
import subprocess
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("localmind.autonomy")

LOG_FILE = Path.home() / "LocalMind_Workspace" / "autonomy_log.jsonl"
PROPOSALS_DIR = Path.home() / "LocalMind_Workspace" / "proposals"


class AutonomyEngine:
    """Background scheduler for autonomous LocalMind operations."""

    def __init__(self, ollama_url: str = "http://localhost:11434",
                 default_model: str = "qwen2.5-coder:7b"):
        self.ollama_url = ollama_url
        self.default_model = default_model
        self.enabled = True
        self.tasks: list[asyncio.Task] = []

        # Status tracking
        self.status = {
            "enabled": True,
            "started_at": None,
            "health_check": {"last_run": None, "ollama_ok": False, "model_loaded": False},
            "reflection": {"last_run": None, "proposals_logged": 0},
            "execution": {"last_run": None, "proposals_executed": 0, "last_result": None},
            "auto_test": {"last_run": None, "passed": 0, "failed": 0},
        }

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
        # Initial warm-up on startup — wait a moment for server to be ready
        await asyncio.sleep(5)
        await self._prewarm_model()

        while True:
            try:
                if self.enabled:
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

                # Pre-warm if no model is loaded (keeps response time fast)
                if ollama_ok and not models_loaded:
                    await self._prewarm_model()

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
        await asyncio.sleep(120)  # Wait 2 min after startup before first reflection

        while True:
            try:
                if self.enabled:
                    await self._run_reflection()
                await asyncio.sleep(1800)  # 30 minutes
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Reflection loop error: {exc}")
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
                    "description, effort (small/medium/large), priority (low/medium/high/critical).\n"
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
                        logger.info(f"💡 Auto-reflection logged: {proposal.get('title', '?')}")

                    except (json.JSONDecodeError, IndexError):
                        self._log("reflection_parse_failed", {"response": response_text[:200]})

                self.status["reflection"]["last_run"] = time.time()

        except Exception as exc:
            logger.warning(f"Reflection failed: {exc}")
            self.status["reflection"]["last_run"] = time.time()

    async def _save_proposal(self, proposal: dict):
        """Save a proposal to the proposals directory."""
        import uuid
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

        filepath = PROPOSALS_DIR / f"{full_proposal['id']}_{full_proposal['category']}.json"
        filepath.write_text(json.dumps(full_proposal, indent=2), encoding="utf-8")

    # ── Proposal Execution Loop ──────────────────────────────────────

    async def _execution_loop(self):
        """Every 15 min: pick an approved proposal and execute it."""
        await asyncio.sleep(300)  # Wait 5 min after startup

        while True:
            try:
                if self.enabled:
                    await self._execute_next_proposal()
                await asyncio.sleep(900)  # 15 minutes
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Execution loop error: {exc}")
                await asyncio.sleep(900)

    async def _execute_next_proposal(self):
        """Find the highest-priority approved proposal and execute it."""
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

        try:
            # Ask the AI to implement the proposal
            async with httpx.AsyncClient(timeout=180.0) as client:
                prompt = (
                    f"You are LocalMind executing an approved improvement proposal.\n\n"
                    f"PROPOSAL: {proposal['title']}\n"
                    f"CATEGORY: {proposal['category']}\n"
                    f"DESCRIPTION: {proposal['description']}\n"
                    f"FILES: {proposal.get('files_affected', 'not specified')}\n\n"
                    f"Output the EXACT code changes needed as a unified diff. "
                    f"Be precise and minimal. Only change what's necessary."
                )

                resp = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": self.default_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": 1000, "num_ctx": 4096},
                    },
                )

                if resp.status_code == 200:
                    result = resp.json().get("response", "")

                    # Log the result but DON'T auto-apply code changes
                    # The user must review diffs before they're applied
                    proposal["status"] = "executed"
                    proposal["execution_result"] = result[:2000]
                    proposal["execution_finished_at"] = time.time()
                    filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

                    self.status["execution"]["proposals_executed"] += 1
                    self.status["execution"]["last_result"] = proposal["title"]
                    self._log("proposal_execution_done", {
                        "id": proposal["id"],
                        "title": proposal["title"],
                    })
                    logger.info(f"✅ Proposal executed: {proposal['title']}")

                    # Run tests after execution
                    await self._run_tests()

        except Exception as exc:
            proposal["status"] = "failed"
            proposal["error"] = str(exc)
            filepath.write_text(json.dumps(proposal, indent=2), encoding="utf-8")
            self._log("proposal_execution_failed", {
                "id": proposal["id"],
                "error": str(exc),
            })
            logger.error(f"❌ Proposal execution failed: {exc}")

        self.status["execution"]["last_run"] = time.time()

    # ── Auto-Test Runner ─────────────────────────────────────────────

    async def _run_tests(self):
        """Run pytest and log results."""
        try:
            project_root = Path(__file__).parent.parent
            result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-q", "--tb=no"],
                capture_output=True, text=True, timeout=60,
                cwd=str(project_root),
            )

            output = result.stdout.strip()
            # Parse "83 passed in 10.11s"
            passed = failed = 0
            for line in output.splitlines():
                if "passed" in line:
                    import re
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

        except Exception as exc:
            logger.warning(f"Auto-test failed: {exc}")

    def get_status(self) -> dict:
        """Return the full autonomy status for the API."""
        return {
            **self.status,
            "uptime_seconds": round(time.time() - self.status["started_at"])
            if self.status["started_at"] else 0,
        }
