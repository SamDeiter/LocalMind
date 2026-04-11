"""
Dual-Mode Job Planner — the engine of the LocalMind job pipeline train.

Decides how many nodes (cars) to create, in what order, and what each one
does.  Two modes:

  quick    — auto-plan by asking Gemini to decompose the job description.
  pipeline — load the node list verbatim from a saved PipelineTemplate.

Error resilience (in order of preference):
  1. Gemini produces a valid plan → use it.
  2. Plan fails validation → retry once with the error list injected.
  3. JSON cannot be parsed  → retry once with stricter format instructions.
  4. Any of the above still fail, or Gemini is unavailable → single-node
     fallback that hands the entire job description to a local agent.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from backend.config import DEPLOYMENT_MODE, MAX_NODES_PER_JOB
from backend.gemini_client import generate, is_available
from backend.jobs.models import Job, JobFile

if TYPE_CHECKING:
    from backend.tools.registry import ToolRegistry

logger = logging.getLogger("localmind.jobs.planner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Timeouts (seconds) per complexity bucket
_TIMEOUT_BY_COMPLEXITY: dict[str, int] = {
    "simple": 120,
    "medium": 300,
    "complex": 600,
}

# Word-count thresholds for _estimate_complexity
_SIMPLE_WORD_LIMIT = 12
_COMPLEX_WORD_LIMIT = 40

# Node-count caps per complexity bucket (before MAX_NODES_PER_JOB cap)
_MAX_NODES_BY_COMPLEXITY: dict[str, int] = {
    "simple": 2,
    "medium": 4,
    "complex": 8,
}

# Required keys in every node dict returned by the planner
_NODE_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"title", "instructions", "tools_allowed", "expected_output", "depends_on", "timeout_sec"}
)

# System instruction sent to Gemini for all planning calls
_SYSTEM_INSTRUCTION = (
    "You are a job planner for LocalMind, an enterprise autonomous task worker. "
    "Your only job is to output a valid JSON array — nothing else. "
    "Do not add commentary, markdown prose, or explanation outside the JSON block."
)

# ---------------------------------------------------------------------------
# Prompt examples embedded once (avoids rebuilding per call)
# ---------------------------------------------------------------------------

_EXAMPLES = """
EXAMPLES
========

Simple (1-2 nodes) — "Summarise the meeting notes":
[
  {
    "title": "Summarise meeting notes",
    "instructions": "Read the attached meeting notes file and produce a concise bullet-point summary.",
    "tools_allowed": ["read_file", "write_file"],
    "expected_output": "A markdown file containing the summary.",
    "depends_on": [],
    "timeout_sec": 120
  }
]

Medium (3-4 nodes) — "Research best Python async frameworks and write a comparison report":
[
  {
    "title": "Web research",
    "instructions": "Search the web for the top Python async frameworks released or updated in the last two years. Collect key metrics, community adoption, and use-cases.",
    "tools_allowed": ["web_search"],
    "expected_output": "Raw research notes saved to a temporary file.",
    "depends_on": [],
    "timeout_sec": 180
  },
  {
    "title": "Draft comparison report",
    "instructions": "Using the research notes, draft a structured markdown comparison report covering: overview, performance, ecosystem, and recommended use-cases.",
    "tools_allowed": ["read_file", "write_file"],
    "expected_output": "A markdown report file at LocalMind_Workspace/async_frameworks_report.md.",
    "depends_on": ["Web research"],
    "timeout_sec": 240
  },
  {
    "title": "Review and finalise",
    "instructions": "Re-read the drafted report, fix any factual gaps or formatting issues, and save the final version.",
    "tools_allowed": ["read_file", "write_file"],
    "expected_output": "Finalised markdown report.",
    "depends_on": ["Draft comparison report"],
    "timeout_sec": 120
  }
]

Complex (5+ nodes) — "Refactor the auth module: add tests, run them, commit the changes":
[
  {
    "title": "Analyse auth module",
    "instructions": "Read the current auth module source files. Identify functions, classes, and any obvious code smells.",
    "tools_allowed": ["read_file", "project_context"],
    "expected_output": "Internal analysis notes.",
    "depends_on": [],
    "timeout_sec": 180
  },
  {
    "title": "Refactor auth module",
    "instructions": "Apply the improvements identified: split large functions, improve naming, add type hints. Write changes to disk.",
    "tools_allowed": ["read_file", "write_file"],
    "expected_output": "Refactored source files saved.",
    "depends_on": ["Analyse auth module"],
    "timeout_sec": 300
  },
  {
    "title": "Write unit tests",
    "instructions": "Write pytest unit tests covering the refactored auth module. Save to tests/test_auth.py.",
    "tools_allowed": ["read_file", "write_file"],
    "expected_output": "tests/test_auth.py with comprehensive test coverage.",
    "depends_on": ["Refactor auth module"],
    "timeout_sec": 240
  },
  {
    "title": "Run tests",
    "instructions": "Run pytest on the new test file. Capture output and report pass/fail counts.",
    "tools_allowed": ["run_code", "terminal"],
    "expected_output": "Test run output confirming all tests pass.",
    "depends_on": ["Write unit tests"],
    "timeout_sec": 120
  },
  {
    "title": "Commit changes",
    "instructions": "Stage and commit all modified files with a descriptive commit message summarising the refactor.",
    "tools_allowed": ["git_commit", "git_status"],
    "expected_output": "Git commit SHA confirming the changes are saved.",
    "depends_on": ["Run tests"],
    "timeout_sec": 60
  }
]

Google Workspace (2-3 nodes) — "Research competitors and write a Google Doc report, then upload the raw data to Drive":
[
  {
    "title": "Web research",
    "instructions": "Search the web for the top five competitors. Collect key data points: pricing, features, market share.",
    "tools_allowed": ["web_search"],
    "expected_output": "Raw research notes.",
    "depends_on": [],
    "timeout_sec": 180
  },
  {
    "title": "Create report in Google Docs",
    "instructions": "Using the research notes, create a new Google Doc titled 'Competitor Analysis'. Write a structured report with sections for each competitor.",
    "tools_allowed": ["google_docs"],
    "expected_output": "A Google Doc containing the competitor analysis report.",
    "depends_on": ["Web research"],
    "timeout_sec": 240
  },
  {
    "title": "Upload raw data to Drive",
    "instructions": "Upload the raw research notes file to a 'Research' folder in Google Drive for archival.",
    "tools_allowed": ["google_drive", "write_file"],
    "expected_output": "Research notes uploaded to Google Drive.",
    "depends_on": ["Web research"],
    "timeout_sec": 120
  }
]

PowerPoint (2-3 nodes) — "Create a presentation about quarterly results":
[
  {
    "title": "Research and outline",
    "instructions": "Gather the key data points and draft a slide-by-slide outline with titles, bullet points, and speaker notes.",
    "tools_allowed": ["web_search", "read_file", "query_documents", "write_file"],
    "expected_output": "A structured outline saved to a temporary file.",
    "depends_on": [],
    "timeout_sec": 180
  },
  {
    "title": "Create PowerPoint file",
    "instructions": "Using the pptx tool, create a new .pptx presentation. Add slides following the outline: set titles, body text, and speaker notes for each slide. Save the final file.",
    "tools_allowed": ["pptx", "read_file", "write_file"],
    "expected_output": "A .pptx file saved in the workspace.",
    "depends_on": ["Research and outline"],
    "timeout_sec": 300
  },
  {
    "title": "Review and polish",
    "instructions": "Re-read the generated presentation with pptx extract_content. Fix any formatting issues, missing content, or typos using edit_slide.",
    "tools_allowed": ["pptx"],
    "expected_output": "Final polished .pptx file.",
    "depends_on": ["Create PowerPoint file"],
    "timeout_sec": 180
  }
]
"""


# ---------------------------------------------------------------------------
# JobPlanner
# ---------------------------------------------------------------------------


class JobPlanner:
    """
    Dual-mode planner that converts a Job into an ordered list of node dicts.

    Callers receive a plain list[dict] — no database writes happen here.
    The node runner is responsible for persisting nodes to job_nodes.
    """

    def __init__(self, tool_registry: "ToolRegistry | None" = None) -> None:
        self._registry = tool_registry

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def plan(
        self,
        job: Job,
        input_files: list[JobFile] | None = None,
        file_metadata: dict | None = None,
    ) -> list[dict]:
        """
        Generate the node plan for a job.

        Returns a list of node dicts, each containing:
          title         : str  — human-readable node name
          instructions  : str  — detailed execution instructions
          tools_allowed : list[str] — tool names the node may call
          expected_output: str — description of the expected artefact
          depends_on    : list[str] — titles of prerequisite nodes (DAG edges)
          timeout_sec   : int  — hard time limit for this node

        The list is always non-empty (fallback to single-node if needed).
        """
        logger.info(
            "Planning job %s (mode=%s, files=%d)",
            job.id,
            job.mode,
            len(input_files or []),
        )

        if job.mode == "pipeline":
            return await self._plan_pipeline(job)

        return await self._plan_quick(job, input_files or [], file_metadata or {})

    # ------------------------------------------------------------------
    # Quick mode
    # ------------------------------------------------------------------

    async def _plan_quick(
        self,
        job: Job,
        input_files: list[JobFile],
        file_metadata: dict,
    ) -> list[dict]:
        """Auto-plan using Gemini.  Retries once on parse or validation failure."""

        # Strict-local deployment: Gemini is not allowed — go straight to fallback.
        if DEPLOYMENT_MODE == "strict-local":
            logger.info(
                "strict-local deployment — skipping Gemini, using single-node fallback"
            )
            return self._fallback_single_node(job)

        if not is_available():
            logger.warning("Gemini unavailable — falling back to single-node plan")
            return self._fallback_single_node(job)

        complexity = self._estimate_complexity(
            job.description or job.title,
            has_files=bool(input_files),
        )
        logger.debug("Estimated complexity: %s for job %s", complexity, job.id)

        prompt = self._build_planner_prompt(job, input_files, file_metadata, complexity)

        # --- Attempt 1 ---
        try:
            raw = await generate(
                prompt=prompt,
                system_instruction=_SYSTEM_INSTRUCTION,
                scrub=True,
            )
            nodes = self._parse_plan_response(raw)
            errors = self._validate_plan(nodes)
            if not errors:
                logger.info(
                    "Plan generated: %d nodes for job %s", len(nodes), job.id
                )
                return self._cap_nodes(nodes)

            logger.warning(
                "Plan validation failed (%d errors) for job %s — retrying with errors",
                len(errors),
                job.id,
            )
            retry_prompt = self._build_retry_prompt_validation(prompt, nodes, errors)

        except _ParseError as exc:
            logger.warning(
                "Plan JSON parse failed for job %s (%s) — retrying with format hint",
                job.id,
                exc,
            )
            retry_prompt = self._build_retry_prompt_format(prompt)
            errors = []

        # --- Attempt 2 ---
        try:
            raw2 = await generate(
                prompt=retry_prompt,
                system_instruction=_SYSTEM_INSTRUCTION,
                scrub=True,
            )
            nodes2 = self._parse_plan_response(raw2)
            errors2 = self._validate_plan(nodes2)
            if not errors2:
                logger.info(
                    "Retry plan succeeded: %d nodes for job %s", len(nodes2), job.id
                )
                return self._cap_nodes(nodes2)

            logger.error(
                "Retry plan still invalid (%d errors) for job %s — using fallback",
                len(errors2),
                job.id,
            )

        except (_ParseError, Exception) as exc:
            logger.error(
                "Retry plan failed for job %s: %s — using fallback", job.id, exc
            )

        return self._fallback_single_node(job)

    # ------------------------------------------------------------------
    # Pipeline mode
    # ------------------------------------------------------------------

    async def _plan_pipeline(self, job: Job) -> list[dict]:
        """
        Load node definitions from the pipeline_templates table.

        Falls back to single-node if no template_id is set or the template
        cannot be retrieved from the database.
        """
        if not job.template_id:
            logger.warning(
                "Job %s is pipeline mode but has no template_id — using fallback",
                job.id,
            )
            return self._fallback_single_node(job)

        try:
            from backend.db import get_db
            from backend.jobs.models import PipelineTemplate

            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT * FROM pipeline_templates WHERE id = ?",
                    (job.template_id,),
                ).fetchone()
            finally:
                conn.close()

            if not row:
                logger.error(
                    "Template %s not found for job %s — using fallback",
                    job.template_id,
                    job.id,
                )
                return self._fallback_single_node(job)

            template = PipelineTemplate.from_row(row)

            if not template.nodes:
                logger.error(
                    "Template %s has no nodes for job %s — using fallback",
                    job.template_id,
                    job.id,
                )
                return self._fallback_single_node(job)

            # Normalise each template node to ensure all required keys exist
            normalised: list[dict] = []
            for n in template.nodes:
                normalised.append(self._normalise_node(n))

            errors = self._validate_plan(normalised)
            if errors:
                logger.warning(
                    "Template %s has validation errors: %s",
                    job.template_id,
                    errors,
                )
                # Still use it — template may be intentionally minimal

            logger.info(
                "Pipeline plan loaded: %d nodes from template %s for job %s",
                len(normalised),
                job.template_id,
                job.id,
            )
            return self._cap_nodes(normalised)

        except Exception as exc:
            logger.error(
                "Failed to load pipeline template for job %s: %s — using fallback",
                job.id,
                exc,
            )
            return self._fallback_single_node(job)

    # ------------------------------------------------------------------
    # Complexity estimation
    # ------------------------------------------------------------------

    def _estimate_complexity(self, description: str, has_files: bool) -> str:
        """
        Estimate task complexity from the description and file presence.

        Returns one of: 'simple' | 'medium' | 'complex'.

        Heuristics:
          - Word count below _SIMPLE_WORD_LIMIT and no files → simple
          - Word count above _COMPLEX_WORD_LIMIT or has multiple files → complex
          - Otherwise → medium

        Keywords that upgrade complexity regardless of length:
          - "refactor", "migrate", "pipeline", "integrate", "deploy",
            "test", "review", "analyse", "audit", "compare" → medium/complex
        """
        if not description:
            return "simple"

        words = description.split()
        word_count = len(words)

        # Keywords that signal multi-step work
        _complex_keywords = {
            "refactor", "migrate", "pipeline", "integrate", "deploy",
            "analyse", "analyze", "audit", "compare", "benchmark",
            "architecture", "redesign", "orchestrate",
        }
        _medium_keywords = {
            "research", "report", "test", "review", "summarize",
            "summarise", "edit", "update", "modify", "convert",
            "transform", "extract", "import", "export",
        }

        desc_lower = description.lower()
        has_complex_keyword = any(kw in desc_lower for kw in _complex_keywords)
        has_medium_keyword = any(kw in desc_lower for kw in _medium_keywords)

        if has_complex_keyword or word_count >= _COMPLEX_WORD_LIMIT or (has_files and word_count >= _SIMPLE_WORD_LIMIT):
            return "complex"

        if has_medium_keyword or has_files or word_count >= _SIMPLE_WORD_LIMIT:
            return "medium"

        return "simple"

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_planner_prompt(
        self,
        job: Job,
        input_files: list[JobFile],
        file_metadata: dict,
        complexity: str,
    ) -> str:
        """Build the planning prompt sent to Gemini."""
        max_nodes = min(
            _MAX_NODES_BY_COMPLEXITY.get(complexity, 4),
            MAX_NODES_PER_JOB,
        )
        default_timeout = _TIMEOUT_BY_COMPLEXITY.get(complexity, 300)

        tool_list = self._format_tool_list()

        file_section = ""
        if input_files:
            lines = ["The following files are attached to this job:"]
            for f in input_files:
                meta = file_metadata.get(f.id, {})
                size_kb = (f.size_bytes or 0) // 1024
                lines.append(
                    f"  - {f.filename}  (type={f.file_type}, "
                    f"mime={f.mime_type or 'unknown'}, size≈{size_kb} KB)"
                )
                if meta:
                    lines.append(f"    metadata: {json.dumps(meta)}")
            file_section = "\n".join(lines)

        prompt = f"""You are planning an autonomous job for the LocalMind enterprise task worker.

JOB DETAILS
===========
ID          : {job.id}
Title       : {job.title}
Description : {job.description or "(no description — use the title)"}
Priority    : {job.priority}
Requester   : {job.requester or "system"}

COMPLEXITY ESTIMATE: {complexity.upper()}
  - Target node count : {max_nodes} (hard cap: {MAX_NODES_PER_JOB})
  - Default timeout   : {default_timeout}s per node

{file_section}

AVAILABLE TOOLS
===============
{tool_list}

TASK
====
Decompose the job into an ordered, dependency-aware list of nodes.  Return
ONLY a JSON array — no prose, no markdown fences, no explanation.

Each element must be a JSON object with EXACTLY these keys:

  "title"          — short, unique node name (used as a dependency reference)
  "instructions"   — detailed step-by-step instructions for the executing agent
  "tools_allowed"  — list of tool names from the AVAILABLE TOOLS list above
                     (empty list [] means the node uses no external tools)
  "expected_output"— one sentence describing the artefact this node produces
  "depends_on"     — list of title strings that must complete first ([] for entry nodes)
  "timeout_sec"    — integer seconds; use {default_timeout} if unsure

RULES
=====
1. Use at most {max_nodes} nodes.
2. Every node title must be unique.
3. depends_on references must match the title of another node exactly.
4. No node may depend on itself.
5. There must be at least one node with an empty depends_on list (entry point).
6. Only reference tools from the AVAILABLE TOOLS list.
7. Produce linear chains (depends_on=[prev_title]) unless true parallelism adds value.
8. Keep instructions self-contained — the agent has no memory of other nodes.

{_EXAMPLES}

OUTPUT (JSON array only):"""

        return prompt

    def _build_retry_prompt_validation(
        self,
        original_prompt: str,
        nodes: list[dict],
        errors: list[str],
    ) -> str:
        """Retry prompt when the plan had validation errors."""
        error_block = "\n".join(f"  - {e}" for e in errors)
        bad_plan = json.dumps(nodes, indent=2)
        return (
            f"{original_prompt}\n\n"
            f"PREVIOUS ATTEMPT HAD ERRORS — fix them and output a corrected JSON array:\n"
            f"{error_block}\n\n"
            f"Broken plan:\n{bad_plan}\n\n"
            f"Corrected JSON array (no prose):"
        )

    def _build_retry_prompt_format(self, original_prompt: str) -> str:
        """Retry prompt when the previous response couldn't be parsed as JSON."""
        return (
            f"{original_prompt}\n\n"
            f"IMPORTANT: Your previous response could not be parsed as JSON.\n"
            f"Output ONLY the raw JSON array — no text before or after it, "
            f"no markdown code fences, no explanation.\n\n"
            f"JSON array:"
        )

    def _format_tool_list(self) -> str:
        """Return a formatted list of available tools from the registry."""
        if not self._registry:
            return "(tool registry not available — list all tools you know)"

        if not self._registry.tools:
            return "(no tools registered)"

        lines: list[str] = []
        for tool in self._registry.tools:
            lines.append(f"  {tool.name:<28} {tool.description}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_plan_response(self, response: str) -> list[dict]:
        """
        Parse Gemini's response into a list of node dicts.

        Uses a 4-layer cascade:
          1. Direct json.loads()
          2. Extract from ```json ... ``` or ``` ... ``` fences
          3. Bracket-balanced block extraction ([ ... ])
          4. Fuzzy repair (trailing commas, Python booleans)

        Raises _ParseError if all layers fail.
        """
        if not response or not response.strip():
            raise _ParseError("Empty response from Gemini")

        text = response.strip()

        # Layer 1 — direct parse
        data = _try_parse(text)
        if isinstance(data, list):
            return data

        # Layer 2 — markdown fence
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence_match:
            data = _try_parse(fence_match.group(1).strip())
            if isinstance(data, list):
                return data

        # Layer 3 — bracket-balanced extraction
        start = text.find("[")
        if start != -1:
            depth = 0
            in_string = False
            escape = False
            end = -1
            for i in range(start, len(text)):
                ch = text[i]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end > start:
                data = _try_parse(text[start : end + 1])
                if isinstance(data, list):
                    return data

        # Layer 4 — fuzzy repair
        candidate = text[text.find("[") :] if "[" in text else text
        repaired = re.sub(r"\bTrue\b", "true", candidate)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        data = _try_parse(repaired)
        if isinstance(data, list):
            return data

        raise _ParseError(f"Could not extract JSON array from response (preview: {text[:120]!r})")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_plan(self, nodes: list[dict]) -> list[str]:
        """
        Validate a list of node dicts.

        Checks:
          - nodes is a non-empty list
          - each node has all required keys with correct types
          - all tool names exist in the registry (if registry is available)
          - no node has an empty title
          - all titles are unique
          - no node depends on itself
          - all depends_on references exist as titles
          - at least one entry point (no dependencies)
          - no circular dependency chains

        Returns a list of error strings.  Empty list means the plan is valid.
        """
        errors: list[str] = []

        if not isinstance(nodes, list):
            return ["Plan is not a list"]
        if not nodes:
            return ["Plan is empty — at least one node is required"]

        # Collect known tools
        known_tools: set[str] = set()
        if self._registry and self._registry.tools:
            known_tools = {t.name for t in self._registry.tools}

        titles: list[str] = []
        seen_titles: set[str] = set()

        for i, node in enumerate(nodes):
            prefix = f"Node[{i}]"

            if not isinstance(node, dict):
                errors.append(f"{prefix}: not a dict")
                continue

            # Required key presence and types
            missing = _NODE_REQUIRED_KEYS - node.keys()
            if missing:
                errors.append(f"{prefix}: missing keys {sorted(missing)}")

            title = node.get("title", "")
            if not isinstance(title, str) or not title.strip():
                errors.append(f"{prefix}: 'title' must be a non-empty string")
                title = f"__unnamed_{i}__"
            else:
                title = title.strip()

            if title in seen_titles:
                errors.append(f"{prefix}: duplicate title {title!r}")
            else:
                seen_titles.add(title)
            titles.append(title)

            if not isinstance(node.get("instructions", ""), str):
                errors.append(f"{prefix} ({title}): 'instructions' must be a string")

            tools = node.get("tools_allowed", [])
            if not isinstance(tools, list):
                errors.append(f"{prefix} ({title}): 'tools_allowed' must be a list")
            elif known_tools:
                bad_tools = [t for t in tools if t not in known_tools]
                if bad_tools:
                    errors.append(
                        f"{prefix} ({title}): unknown tools {bad_tools} "
                        f"(available: {sorted(known_tools)})"
                    )

            depends_on = node.get("depends_on", [])
            if not isinstance(depends_on, list):
                errors.append(f"{prefix} ({title}): 'depends_on' must be a list")
            else:
                for dep in depends_on:
                    if dep == title:
                        errors.append(f"{prefix} ({title}): node depends on itself")

            timeout = node.get("timeout_sec")
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                errors.append(
                    f"{prefix} ({title}): 'timeout_sec' must be a positive number"
                )

        # Validate depends_on references now that we have all titles
        title_set = set(titles)
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            depends_on = node.get("depends_on", [])
            if not isinstance(depends_on, list):
                continue
            node_title = (node.get("title") or f"__unnamed_{i}__").strip()
            for dep in depends_on:
                if isinstance(dep, str) and dep not in title_set:
                    errors.append(
                        f"Node ({node_title!r}): depends_on references unknown title {dep!r}"
                    )

        # At least one entry point
        has_entry_point = any(
            isinstance(n, dict) and not n.get("depends_on")
            for n in nodes
        )
        if not has_entry_point:
            errors.append("No entry point: every node has non-empty depends_on")

        # Cycle detection via DFS
        cycle_errors = _detect_cycles(nodes)
        errors.extend(cycle_errors)

        return errors

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback_single_node(self, job: Job) -> list[dict]:
        """
        Return a single catch-all node.

        Used when Gemini is unavailable, the plan fails validation twice,
        or JSON parsing fails twice.
        """
        logger.info("Using single-node fallback for job %s", job.id)
        description = job.description or job.title or "Complete the task."
        all_tools: list[str] = []
        if self._registry:
            all_tools = [t.name for t in self._registry.tools]

        return [
            {
                "title": "Execute task",
                "instructions": (
                    f"Complete the following task using whatever tools are available:\n\n"
                    f"{description}\n\n"
                    f"Work autonomously, verify your results, and report what was done."
                ),
                "tools_allowed": all_tools,
                "expected_output": "Task completed successfully, results documented.",
                "depends_on": [],
                "timeout_sec": min(MAX_NODES_PER_JOB * 120, 1800),
            }
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cap_nodes(self, nodes: list[dict]) -> list[dict]:
        """Enforce the MAX_NODES_PER_JOB cap."""
        if len(nodes) > MAX_NODES_PER_JOB:
            logger.warning(
                "Plan has %d nodes, capping at MAX_NODES_PER_JOB=%d",
                len(nodes),
                MAX_NODES_PER_JOB,
            )
            nodes = nodes[:MAX_NODES_PER_JOB]
        return nodes

    @staticmethod
    def _normalise_node(node: dict) -> dict:
        """
        Fill in default values for optional keys missing from a template node.

        Ensures all _NODE_REQUIRED_KEYS are present so callers can always
        access them without KeyError.
        """
        return {
            "title": str(node.get("title", "Unnamed node")).strip(),
            "instructions": str(node.get("instructions", "")),
            "tools_allowed": node.get("tools_allowed") if isinstance(node.get("tools_allowed"), list) else [],
            "expected_output": str(node.get("expected_output", "")),
            "depends_on": node.get("depends_on") if isinstance(node.get("depends_on"), list) else [],
            "timeout_sec": int(node.get("timeout_sec") or 300),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _ParseError(Exception):
    """Raised when the planner cannot parse Gemini's response as a JSON list."""


def _try_parse(text: str) -> object | None:
    """Attempt json.loads; return None on any failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _detect_cycles(nodes: list[dict]) -> list[str]:
    """
    Detect circular dependencies in the node DAG.

    Uses an iterative DFS (colour-marking: white=0, grey=1, black=2).
    Returns a list of error strings for each cycle found.
    """
    # Build adjacency: title → set of depends_on titles
    adj: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = (node.get("title") or "").strip()
        depends = node.get("depends_on", [])
        adj[title] = [d for d in (depends or []) if isinstance(d, str)]

    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {t: WHITE for t in adj}
    errors: list[str] = []

    def dfs(node: str, path: list[str]) -> None:
        colour[node] = GREY
        path.append(node)
        for neighbour in adj.get(node, []):
            if neighbour not in colour:
                continue  # unknown title — already reported in _validate_plan
            if colour[neighbour] == GREY:
                cycle_repr = " → ".join(path + [neighbour])
                errors.append(f"Circular dependency detected: {cycle_repr}")
            elif colour[neighbour] == WHITE:
                dfs(neighbour, path)
        path.pop()
        colour[node] = BLACK

    for title in list(adj.keys()):
        if colour.get(title) == WHITE:
            dfs(title, [])

    return errors
