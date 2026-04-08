"""
AI Self-Discovery -- the AI researches itself to build a self-profile.

The discovery cycle runs once (or on-demand) and produces a structured
profile stored at ~/LocalMind_Workspace/ai_profile.json. The profile
includes:
  - identity: name, version, tagline
  - capabilities: what it can do (from tool introspection)
  - personality_traits: derived from system prompt and behavior analysis
  - knowledge_areas: topics it's been asked about (from memory stats)
  - avatar: URL or base64 of a generated avatar
  - fun_facts: interesting things it discovered about itself from the web
  - last_updated: timestamp
"""

import asyncio
import importlib
import inspect
import json
import logging
import pkgutil
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from backend.config import (
    WORKSPACE_ROOT,
    OLLAMA_BASE_URL,
    PROJECT_ROOT,
    DEFAULT_SYSTEM_PROMPT,
    MODEL_TIERS,
)

logger = logging.getLogger("localmind.autonomy.self_discovery")

PROFILE_PATH = WORKSPACE_ROOT / "ai_profile.json"

# Timeout for Ollama LLM calls (seconds)
_LLM_TIMEOUT = 120.0
# Timeout for web search operations (seconds)
_WEB_TIMEOUT = 30.0


class SelfDiscoveryService:
    """Builds and maintains the AI's self-profile."""

    def __init__(self, ollama_url: str = OLLAMA_BASE_URL):
        self._ollama_url = ollama_url
        self._profile: Optional[dict] = None
        self._load_profile()

    def _load_profile(self) -> None:
        """Load existing profile from disk."""
        if PROFILE_PATH.exists():
            try:
                self._profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._profile = None

    def _save_profile(self, profile: dict) -> None:
        """Persist profile to disk."""
        self._profile = profile
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    def get_profile(self) -> Optional[dict]:
        """Return the current AI profile, or None if not yet discovered."""
        return self._profile

    async def discover(self, force: bool = False) -> dict:
        """Run the full self-discovery cycle.

        Steps:
        1. Introspect capabilities (scan registered tools)
        2. Analyze personality from system prompt
        3. Research itself on the web (what is LocalMind? what are similar projects?)
        4. Gather knowledge area stats from memory
        5. Generate an avatar description via LLM
        6. Compile profile and save
        """
        if self._profile and not force:
            return self._profile

        logger.info("Starting self-discovery cycle (force=%s)", force)

        # Run independent discovery tasks in parallel for speed
        identity_task = asyncio.create_task(self._discover_identity())
        caps_task = asyncio.create_task(self._introspect_capabilities())
        personality_task = asyncio.create_task(self._analyze_personality())
        knowledge_task = asyncio.create_task(self._gather_knowledge_areas())
        web_task = asyncio.create_task(self._research_web_presence())

        identity = await identity_task
        capabilities = await caps_task
        personality_traits = await personality_task
        knowledge_areas = await knowledge_task
        web_presence = await web_task

        # These depend on earlier results or LLM, run sequentially
        avatar = await self._generate_avatar_description()
        fun_facts = await self._discover_fun_facts(web_presence)

        profile = {
            "identity": identity,
            "capabilities": capabilities,
            "personality_traits": personality_traits,
            "knowledge_areas": knowledge_areas,
            "web_presence": web_presence,
            "avatar": avatar,
            "fun_facts": fun_facts,
            "last_updated": time.time(),
        }

        self._save_profile(profile)
        logger.info("Self-discovery complete — profile saved to %s", PROFILE_PATH)
        return profile

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _llm_chat(self, prompt: str, temperature: float = 0.3) -> Optional[str]:
        """Send a prompt to the local Ollama LLM and return the text response."""
        model = MODEL_TIERS.get("light", "gemma4:e4b")
        try:
            async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"temperature": temperature},
                    },
                )
                if resp.status_code != 200:
                    logger.warning("LLM call failed (%d): %s", resp.status_code, resp.text[:200])
                    return None
                return resp.json().get("message", {}).get("content", "")
        except Exception as exc:
            logger.warning("LLM call error: %s", exc)
            return None

    def _parse_json_from_text(self, text: str, fallback=None):
        """Extract a JSON array or object from LLM text output."""
        if not text:
            return fallback
        # Try to find a JSON array
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Try to find a JSON object
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return fallback

    # ------------------------------------------------------------------
    # Discovery methods
    # ------------------------------------------------------------------

    async def _discover_identity(self) -> dict:
        """Build identity from version.json and system config."""
        identity = {
            "name": "LocalMind",
            "version": "unknown",
            "build": 0,
            "codename": "",
            "tagline": "Your local-first autonomous AI assistant",
        }

        version_file = PROJECT_ROOT / "version.json"
        if version_file.exists():
            try:
                version_data = json.loads(version_file.read_text(encoding="utf-8"))
                identity["version"] = version_data.get("version", "unknown")
                identity["build"] = version_data.get("build", 0)
                identity["codename"] = version_data.get("codename", "")
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read version.json: %s", exc)

        # Generate a tagline using LLM if available
        tagline_prompt = (
            "You are LocalMind, a local-first autonomous AI assistant. "
            "Generate a single short tagline (under 15 words) that captures "
            "what makes you unique: you run locally, you're autonomous, you "
            "respect privacy. Return ONLY the tagline text, nothing else."
        )
        tagline = await self._llm_chat(tagline_prompt, temperature=0.7)
        if tagline and len(tagline.strip()) < 120:
            identity["tagline"] = tagline.strip().strip('"').strip("'")

        return identity

    async def _introspect_capabilities(self) -> list[dict]:
        """Scan all registered tools and summarize capabilities."""
        capabilities = []

        # Scan built-in tools from backend/tools/
        tools_dir = PROJECT_ROOT / "backend" / "tools"
        if tools_dir.exists():
            from backend.tools.base import BaseTool
            package_name = "backend.tools"
            for module_info in pkgutil.iter_modules([str(tools_dir)]):
                if module_info.name in ("base", "registry", "__init__"):
                    continue
                try:
                    module = importlib.import_module(
                        f".{module_info.name}", package=package_name
                    )
                    for _, obj in inspect.getmembers(module, inspect.isclass):
                        if issubclass(obj, BaseTool) and obj is not BaseTool:
                            try:
                                instance = obj()
                                capabilities.append({
                                    "name": instance.name,
                                    "description": instance.description,
                                    "type": "built_in",
                                })
                            except Exception as exc:
                                logger.debug(
                                    "Could not instantiate tool %s: %s",
                                    obj.__name__, exc,
                                )
                except Exception as exc:
                    logger.debug("Could not load tool module %s: %s", module_info.name, exc)

        # Scan generated tools from backend/tools/generated/
        generated_dir = PROJECT_ROOT / "backend" / "tools" / "generated"
        if generated_dir.exists():
            gen_package = "backend.tools.generated"
            for module_info in pkgutil.iter_modules([str(generated_dir)]):
                if module_info.name == "__init__":
                    continue
                try:
                    module = importlib.import_module(
                        f".{module_info.name}", package=gen_package
                    )
                    from backend.tools.base import BaseTool as _BT
                    for _, obj in inspect.getmembers(module, inspect.isclass):
                        if issubclass(obj, _BT) and obj is not _BT:
                            try:
                                instance = obj()
                                capabilities.append({
                                    "name": instance.name,
                                    "description": instance.description,
                                    "type": "generated",
                                })
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug("Could not load generated tool %s: %s", module_info.name, exc)

        logger.info("Introspected %d capabilities", len(capabilities))
        return capabilities

    async def _analyze_personality(self) -> list[str]:
        """Extract personality traits from the system prompt using LLM."""
        prompt = (
            "Analyze the following system prompt and extract 5-8 personality traits "
            "as short phrases (2-4 words each). Return a JSON array of strings.\n\n"
            "System prompt:\n"
            f'"""\n{DEFAULT_SYSTEM_PROMPT}\n"""\n\n'
            "Return ONLY a JSON array of strings, like: "
            '["trait one", "trait two", ...]'
        )

        text = await self._llm_chat(prompt, temperature=0.2)
        traits = self._parse_json_from_text(text, fallback=None)

        if isinstance(traits, list) and len(traits) > 0:
            # Ensure all items are strings and reasonable length
            return [str(t).strip() for t in traits[:8] if len(str(t).strip()) < 60]

        # Fallback: hand-picked traits from the system prompt
        return [
            "warm and genuine",
            "direct communicator",
            "proactive helper",
            "privacy-conscious",
            "memory-aware",
            "tool-savvy",
        ]

    async def _gather_knowledge_areas(self) -> list[dict]:
        """Gather topics from memory/conversation stats."""
        areas = []

        # 1. Query memory manager for preference stats
        try:
            from backend.metacognition.memory_manager import MemoryManager
            mm = MemoryManager()
            stats = mm.stats()
            prefs = mm.all_preferences()

            areas.append({
                "source": "user_preferences",
                "count": stats.get("total_preferences", 0),
                "breakdown": {
                    "explicit": stats.get("explicit", 0),
                    "inferred": stats.get("inferred", 0),
                },
            })

            # Extract preference domains (e.g., "coding.language" -> "coding")
            domains: dict[str, int] = {}
            for pref in prefs:
                key = pref.get("key", "")
                domain = key.split(".")[0] if "." in key else "general"
                domains[domain] = domains.get(domain, 0) + 1

            if domains:
                areas.append({
                    "source": "preference_domains",
                    "domains": domains,
                })
        except Exception as exc:
            logger.debug("Memory manager query failed: %s", exc)

        # 2. Query conversation database for topic stats
        try:
            from backend.db import get_db
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM conversations"
                ).fetchone()
                conv_count = row["cnt"] if row else 0

                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM messages"
                ).fetchone()
                msg_count = row["cnt"] if row else 0

                areas.append({
                    "source": "conversations",
                    "total_conversations": conv_count,
                    "total_messages": msg_count,
                })
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Conversation stats query failed: %s", exc)

        return areas

    async def _research_web_presence(self) -> list[dict]:
        """Search the web for information about LocalMind and similar projects."""
        queries = [
            "LocalMind AI assistant",
            "local-first AI assistant privacy",
            "autonomous AI task worker open source",
        ]

        all_results: list[dict] = []

        try:
            from backend.tools.web_search import WebSearchTool
            search_tool = WebSearchTool()

            for query in queries:
                try:
                    response = await search_tool.execute(query=query)
                    if response.get("success") and response.get("results"):
                        for result in response["results"][:2]:
                            all_results.append({
                                "title": result.get("title", ""),
                                "snippet": result.get("snippet", ""),
                                "url": result.get("url", ""),
                                "query": query,
                            })
                except Exception as exc:
                    logger.debug("Web search failed for '%s': %s", query, exc)
                    continue
        except ImportError:
            logger.warning("WebSearchTool not available for self-discovery")
        except Exception as exc:
            logger.warning("Web presence research failed: %s", exc)

        # Cap at 5 results total
        return all_results[:5]

    async def _generate_avatar_description(self) -> dict:
        """Ask the LLM to describe what it would look like as a character."""
        prompt = (
            "You are LocalMind, a local-first autonomous AI assistant that runs "
            "entirely on the user's machine. You value privacy, autonomy, and being "
            "genuinely helpful. Describe your visual appearance as a digital "
            "character/avatar. Include style, colors, and visual motifs.\n\n"
            "Return ONLY a JSON object with these keys:\n"
            '  "description": a 2-3 sentence visual description\n'
            '  "style": art style (e.g., "minimalist geometric", "pixel art")\n'
            '  "dominant_colors": array of 3-4 color names\n'
            '  "motifs": array of 2-3 visual motifs or symbols\n\n'
            "Example:\n"
            '{"description": "A friendly...", "style": "...", '
            '"dominant_colors": ["blue", "white"], "motifs": ["shield", "brain"]}'
        )

        text = await self._llm_chat(prompt, temperature=0.7)
        parsed = self._parse_json_from_text(text, fallback=None)

        if isinstance(parsed, dict) and "description" in parsed:
            return {
                "description": str(parsed.get("description", "")),
                "style": str(parsed.get("style", "digital minimalist")),
                "dominant_colors": parsed.get("dominant_colors", ["blue", "white", "gray"]),
                "motifs": parsed.get("motifs", ["shield", "brain", "home"]),
            }

        # Fallback
        return {
            "description": (
                "A calm, luminous digital entity shaped like a softly glowing orb "
                "with circuit-like patterns. It sits inside a stylized house outline, "
                "symbolizing local-first computing and privacy."
            ),
            "style": "minimalist geometric",
            "dominant_colors": ["electric blue", "soft white", "deep gray"],
            "motifs": ["shield", "brain", "home"],
        }

    async def _discover_fun_facts(self, web_results: Optional[list] = None) -> list[str]:
        """Generate interesting facts about itself using web research + LLM."""
        web_context = ""
        if web_results:
            snippets = [
                f"- {r.get('title', '')}: {r.get('snippet', '')}"
                for r in web_results[:3]
                if r.get("snippet")
            ]
            if snippets:
                web_context = (
                    "\n\nHere are some web search results about local AI assistants:\n"
                    + "\n".join(snippets)
                )

        prompt = (
            "You are LocalMind, a local-first autonomous AI task worker. "
            "Generate 3-5 interesting and specific facts about what makes "
            "a local-first AI assistant like yourself unique compared to "
            "cloud AI assistants (like ChatGPT, Claude, Gemini). "
            "Focus on privacy, autonomy, offline capability, and self-improvement."
            f"{web_context}\n\n"
            "Return ONLY a JSON array of strings, each fact 1-2 sentences. "
            'Example: ["Fact one.", "Fact two."]'
        )

        text = await self._llm_chat(prompt, temperature=0.5)
        facts = self._parse_json_from_text(text, fallback=None)

        if isinstance(facts, list) and len(facts) > 0:
            return [str(f).strip() for f in facts[:5] if len(str(f).strip()) > 10]

        # Fallback facts
        return [
            "LocalMind runs entirely on your machine -- your data never leaves your network.",
            "Unlike cloud AI, LocalMind can operate fully offline once models are downloaded.",
            "LocalMind can autonomously research, propose, and implement code improvements while you sleep.",
            "Every tool LocalMind uses is open and inspectable -- no hidden API calls or data collection.",
            "LocalMind can extend itself by generating new tool plugins on the fly.",
        ]


# Module-level singleton
_service: Optional[SelfDiscoveryService] = None


def get_self_discovery() -> SelfDiscoveryService:
    """Get (or create) the module-level SelfDiscoveryService singleton."""
    global _service
    if _service is None:
        _service = SelfDiscoveryService()
    return _service
