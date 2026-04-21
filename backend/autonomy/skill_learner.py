"""
Skill Learner --- AI browses the web to learn new skills and improve tools.

The learning cycle:
1. Gap analysis: examine existing tools, find what's missing or weak
2. Web research: search for best practices, libraries, techniques
3. Synthesis: ask LLM to generate improved tool code based on findings
4. Validation: run through ToolGenerator's safety checks
5. Journal: record what was learned, whether it was applied

Learning journal stored at ~/LocalMind_Workspace/learning_journal.json
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from backend.config import WORKSPACE_ROOT, OLLAMA_BASE_URL, MODEL_TIERS, PROJECT_ROOT

logger = logging.getLogger("localmind.autonomy.skill_learner")

JOURNAL_PATH = WORKSPACE_ROOT / "learning_journal.json"


class LearningEntry:
    """A single learning journal entry."""

    def __init__(
        self,
        topic: str,
        source: str,
        summary: str,
        applied: bool = False,
        tool_name: str = "",
        timestamp: float = None,
    ):
        self.topic = topic
        self.source = source  # "web", "academic", "self_analysis"
        self.summary = summary
        self.applied = applied
        self.tool_name = tool_name
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "source": self.source,
            "summary": self.summary,
            "applied": self.applied,
            "tool_name": self.tool_name,
            "timestamp": self.timestamp,
        }


class SkillLearner:
    """Discovers, researches, and integrates new skills from the web."""

    def __init__(self, ollama_url: str = OLLAMA_BASE_URL):
        self._ollama_url = ollama_url
        self._journal: list[dict] = []
        self._load_journal()

    def _load_journal(self) -> None:
        if JOURNAL_PATH.exists():
            try:
                self._journal = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._journal = []

    def _save_journal(self) -> None:
        try:
            JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            JOURNAL_PATH.write_text(
                json.dumps(self._journal, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Failed to save learning journal: %s", exc)

    def get_journal(self, limit: int = 50) -> list[dict]:
        """Return recent journal entries."""
        return self._journal[-limit:]

    def get_stats(self) -> dict:
        """Return learning statistics."""
        total = len(self._journal)
        applied = sum(1 for e in self._journal if e.get("applied"))
        topics = list({e.get("topic", "") for e in self._journal})
        tools_created = [
            e.get("tool_name") for e in self._journal if e.get("tool_name")
        ]
        last_learned = self._journal[-1]["timestamp"] if self._journal else 0
        return {
            "total_entries": total,
            "applied_count": applied,
            "success_rate": round(applied / total, 2) if total else 0.0,
            "unique_topics": len(topics),
            "topics": topics[-10:],
            "tools_created": tools_created,
            "last_learned": last_learned,
        }

    # ------------------------------------------------------------------
    # Core learning cycle
    # ------------------------------------------------------------------

    async def learn(self, topic: Optional[str] = None) -> dict:
        """Run a learning cycle.

        If topic is provided, learn about that specific topic.
        If not, auto-detect gaps and learn about the most promising one.

        Returns: {"topic": str, "findings": list, "applied": bool, "tool_name": str|None}
        """
        if not topic:
            topic = await self._identify_learning_gap()

        if not topic:
            logger.info("No learning gap identified, skipping cycle")
            return {
                "topic": "",
                "findings": [],
                "summary": "",
                "applied": False,
                "tool_name": None,
            }

        logger.info("Starting learning cycle for topic: %s", topic)

        # Research
        findings = await self._research_topic(topic)
        if not findings:
            logger.info("No findings for topic '%s'", topic)
            entry = LearningEntry(
                topic=topic,
                source="web",
                summary=f"Searched for '{topic}' but found no usable results.",
                applied=False,
            )
            self._journal.append(entry.to_dict())
            self._save_journal()
            return {
                "topic": topic,
                "findings": [],
                "summary": "",
                "applied": False,
                "tool_name": None,
            }

        # Synthesize into potential tool improvement
        synthesis = await self._synthesize_findings(topic, findings)

        # Try to generate/improve a tool
        applied = False
        tool_name = None
        if synthesis.get("code"):
            result = await self._try_apply_learning(synthesis)
            applied = result.get("applied", False)
            tool_name = result.get("tool_name")

        # Journal entry
        entry = LearningEntry(
            topic=topic,
            source="web",
            summary=synthesis.get("summary", ""),
            applied=applied,
            tool_name=tool_name or "",
        )
        self._journal.append(entry.to_dict())
        self._save_journal()

        return {
            "topic": topic,
            "findings": findings,
            "summary": synthesis.get("summary", ""),
            "applied": applied,
            "tool_name": tool_name,
        }

    # ------------------------------------------------------------------
    # Gap analysis
    # ------------------------------------------------------------------

    async def _identify_learning_gap(self) -> str:
        """Analyze existing tools and find what's missing or could be improved."""
        # Collect existing tool names by scanning backend/tools/*.py
        tools_dir = PROJECT_ROOT / "backend" / "tools"
        existing_tools: list[str] = []
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "base.py":
                continue
            existing_tools.append(py_file.stem)

        # Also check generated tools
        generated_dir = tools_dir / "generated"
        if generated_dir.exists():
            for py_file in sorted(generated_dir.glob("*.py")):
                if not py_file.name.startswith("_"):
                    existing_tools.append(f"generated/{py_file.stem}")

        # Gather past topics to avoid repeats
        past_topics = [e.get("topic", "") for e in self._journal[-20:]]

        model = MODEL_TIERS.get("medium", "qwen2.5-coder:14b")
        prompt = (
            "You are an AI tool-building strategist. Given these existing tools:\n"
            f"{json.dumps(existing_tools, indent=2)}\n\n"
            f"And these topics already studied: {json.dumps(past_topics)}\n\n"
            "What NEW skill or tool would be most valuable to add? "
            "Consider: data processing, file format conversion, API integrations, "
            "text analysis, automation, testing utilities, data validation, "
            "image processing, CSV/Excel manipulation, HTTP client helpers.\n\n"
            "Return ONLY a JSON object: {\"topic\": \"...\", \"reasoning\": \"...\"}\n"
            "The topic should be a concise skill name like 'csv_data_analysis' or "
            "'json_schema_validation'. Do NOT repeat a past topic."
        )

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"num_ctx": 4096, "temperature": 0.7},
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "LLM gap analysis failed (HTTP %d)", resp.status_code
                    )
                    return "python_data_validation"

                text = resp.json().get("message", {}).get("content", "")
                # Parse JSON from response
                match = re.search(r"\{[^}]+\}", text, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    topic = data.get("topic", "").strip()
                    if topic:
                        logger.info(
                            "Identified learning gap: %s (reason: %s)",
                            topic,
                            data.get("reasoning", ""),
                        )
                        return topic

        except Exception as exc:
            logger.error("Gap analysis failed: %s", exc)

        return "python_data_validation"

    # ------------------------------------------------------------------
    # Web research
    # ------------------------------------------------------------------

    async def _research_topic(self, topic: str) -> list[dict]:
        """Search the web for information about the topic."""
        from backend.tools.web_search import WebSearchTool

        search_tool = WebSearchTool()
        all_results: list[dict] = []
        seen_urls: set[str] = set()

        queries = [
            f"python {topic} best practices implementation",
            f"python {topic} library tutorial",
        ]

        for query in queries:
            try:
                result = await search_tool.execute(query=query)
                if result.get("success") and result.get("results"):
                    for item in result["results"]:
                        url = item.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append(
                                {
                                    "title": item.get("title", ""),
                                    "snippet": item.get("snippet", ""),
                                    "url": url,
                                }
                            )
            except Exception as exc:
                logger.warning("Web search failed for '%s': %s", query, exc)

        logger.info(
            "Researched '%s': found %d unique results", topic, len(all_results)
        )
        return all_results

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def _synthesize_findings(self, topic: str, findings: list[dict]) -> dict:
        """Ask LLM to synthesize web findings into actionable knowledge."""
        # Format findings as context
        findings_text = "\n\n".join(
            f"### {f['title']}\n{f['snippet']}\nURL: {f['url']}" for f in findings[:8]
        )

        model = MODEL_TIERS.get("medium", "qwen2.5-coder:14b")
        prompt = (
            f"Based on these web findings about '{topic}':\n\n"
            f"{findings_text}\n\n"
            "Do TWO things:\n"
            "1. Write a concise summary (2-4 sentences) of the key techniques and best practices.\n"
            "2. If applicable, generate a complete Python tool class that implements this capability.\n\n"
            "The tool MUST:\n"
            "- Subclass BaseTool from backend.tools.base\n"
            "- Have @property methods: name, description, parameters\n"
            "- Have an async execute(**kwargs) method returning {\"success\": True/False, \"result\": ...}\n"
            "- Import from backend.config import WORKSPACE_ROOT if doing file I/O\n"
            "- NOT import subprocess, shutil, ctypes, socket, multiprocessing, signal, pty, "
            "resource, fcntl, termios, mmap, or webbrowser\n"
            "- NOT use os.system, os.popen, eval(, exec(, __import__(\n\n"
            "Respond in this exact format:\n"
            "SUMMARY: <your summary here>\n\n"
            "TOOL_NAME: <snake_case_name or NONE>\n\n"
            "```python\n<complete tool code or NONE>\n```"
        )

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"num_ctx": 8192, "temperature": 0.3},
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "LLM synthesis failed (HTTP %d)", resp.status_code
                    )
                    return {"summary": "", "code": None, "tool_name": None}

                text = resp.json().get("message", {}).get("content", "")

                # Parse summary
                summary = ""
                summary_match = re.search(
                    r"SUMMARY:\s*(.+?)(?=\n\nTOOL_NAME:|\n```|\Z)",
                    text,
                    re.DOTALL,
                )
                if summary_match:
                    summary = summary_match.group(1).strip()

                # Parse tool name
                tool_name = None
                name_match = re.search(r"TOOL_NAME:\s*(\S+)", text)
                if name_match:
                    val = name_match.group(1).strip()
                    if val.upper() != "NONE":
                        tool_name = val

                # Parse code block
                code = None
                code_match = re.search(
                    r"```python\s*\n(.+?)\n```", text, re.DOTALL
                )
                if code_match:
                    code_text = code_match.group(1).strip()
                    if code_text.upper() != "NONE" and "class " in code_text:
                        code = code_text

                return {
                    "summary": summary,
                    "code": code,
                    "tool_name": tool_name,
                }

        except Exception as exc:
            logger.error("Synthesis failed: %s", exc)
            return {"summary": "", "code": None, "tool_name": None}

    # ------------------------------------------------------------------
    # Apply learning
    # ------------------------------------------------------------------

    async def _try_apply_learning(self, synthesis: dict) -> dict:
        """Try to apply learned knowledge by creating/updating a tool."""
        from backend.tools.tool_generator import ToolGenerator
        from backend.security.integrity import minter

        code = synthesis.get("code", "")
        tool_name = synthesis.get("tool_name", "")

        if not code:
            return {"applied": False, "tool_name": None}

        generator = ToolGenerator()

        # Step 1: Validate
        validation = generator.validate_code(code)
        if not validation.get("valid"):
            logger.warning(
                "Generated code failed validation: %s",
                validation.get("errors", []),
            )
            return {"applied": False, "tool_name": None}

        # Derive tool_name from validation if not provided
        if not tool_name:
            tool_name = validation.get("tool_name", "learned_tool")

        # Step 2: Sign the code (Mint it)
        token = minter.sign_tool(tool_name, code)
        
        # Step 3: Generate (validates again internally, writes file + DB row)
        result = generator.generate_tool(
            tool_name=tool_name,
            code=code,
            description=synthesis.get("summary", "Auto-learned tool"),
            requested_by="skill_learner",
        )

        if result.get("ok"):
            # Save the signature token sidecar
            try:
                sig_path = (PROJECT_ROOT / "backend" / "tools" / "generated" / f"{tool_name}.sig")
                sig_path.write_text(token, encoding="utf-8")
                logger.info("Applied learning: created and MINTED tool '%s'", result["tool_name"])
            except Exception as e:
                logger.error("Failed to save tool signature for '%s': %s", tool_name, e)
                
            return {"applied": True, "tool_name": result["tool_name"]}

        logger.warning(
            "Failed to apply learning for '%s': %s",
            tool_name,
            result.get("error", "unknown"),
        )
        return {"applied": False, "tool_name": None}

    # ------------------------------------------------------------------
    # Suggest topics
    # ------------------------------------------------------------------

    async def suggest_topics(self) -> list[dict]:
        """Return a list of suggested learning topics based on gap analysis."""
        # Collect existing tool names
        tools_dir = PROJECT_ROOT / "backend" / "tools"
        existing_tools: list[str] = []
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "base.py":
                continue
            existing_tools.append(py_file.stem)

        past_topics = [e.get("topic", "") for e in self._journal]

        model = MODEL_TIERS.get("medium", "qwen2.5-coder:14b")
        prompt = (
            "You are an AI tool-building strategist. Given these existing tools:\n"
            f"{json.dumps(existing_tools, indent=2)}\n\n"
            f"Topics already studied: {json.dumps(past_topics[-15:])}\n\n"
            "Suggest exactly 5 NEW topics that would expand the AI's capabilities. "
            "Each topic should be a concrete, learnable skill. Do NOT repeat past topics.\n\n"
            "Return a JSON array:\n"
            '[{"topic": "...", "reasoning": "...", "difficulty": "easy|medium|hard"}]\n'
            "Output ONLY the JSON array, nothing else."
        )

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        "options": {"num_ctx": 4096, "temperature": 0.7},
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "LLM topic suggestion failed (HTTP %d)", resp.status_code
                    )
                    return self._fallback_suggestions()

                text = resp.json().get("message", {}).get("content", "")
                match = re.search(r"\[.*\]", text, re.DOTALL)
                if match:
                    suggestions = json.loads(match.group())
                    # Validate structure
                    valid = []
                    for s in suggestions:
                        if isinstance(s, dict) and "topic" in s:
                            valid.append(
                                {
                                    "topic": s.get("topic", ""),
                                    "reasoning": s.get("reasoning", ""),
                                    "difficulty": s.get("difficulty", "medium"),
                                }
                            )
                    return valid[:5]

        except Exception as exc:
            logger.error("Topic suggestion failed: %s", exc)

        return self._fallback_suggestions()

    @staticmethod
    def _fallback_suggestions() -> list[dict]:
        """Return hardcoded suggestions when LLM is unavailable."""
        return [
            {
                "topic": "csv_data_analysis",
                "reasoning": "CSV is a universal data format; analysis tools are broadly useful.",
                "difficulty": "easy",
            },
            {
                "topic": "json_schema_validation",
                "reasoning": "Validate API payloads and config files automatically.",
                "difficulty": "easy",
            },
            {
                "topic": "markdown_to_html_conversion",
                "reasoning": "Useful for generating reports and documentation.",
                "difficulty": "medium",
            },
            {
                "topic": "regex_pattern_builder",
                "reasoning": "Help users construct and test complex regex patterns.",
                "difficulty": "medium",
            },
            {
                "topic": "http_api_testing",
                "reasoning": "Test REST APIs with automated request/response validation.",
                "difficulty": "hard",
            },
        ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_learner: Optional[SkillLearner] = None


def get_skill_learner() -> SkillLearner:
    global _learner
    if _learner is None:
        _learner = SkillLearner()
    return _learner
