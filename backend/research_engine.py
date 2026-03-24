import asyncio
"""
research_engine.py — Self-Improvement Research Pipeline
========================================================
Gives the autonomy engine data-driven intelligence for proposals.

Modules:
  - FailureAnalyzer:  Classifies errors, stores lessons learned
  - SuccessTracker:   Tracks outcomes by category, computes confidence
  - CodebaseScanner:  AST complexity, code smells, evidence-based findings
"""

import ast
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
import httpx
import psutil


logger = logging.getLogger("localmind.research")

WORKSPACE = Path.home() / "LocalMind_Workspace"
LESSONS_FILE = WORKSPACE / "lessons_learned.json"
STATS_FILE = WORKSPACE / "execution_stats.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Failure Analyzer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Error classification patterns
_ERROR_PATTERNS = [
    ("import_missing",   [r"No module named", r"ImportError", r"ModuleNotFoundError"]),
    ("syntax_error",     [r"SyntaxError", r"IndentationError", r"unexpected indent"]),
    ("name_error",       [r"NameError", r"is not defined"]),
    ("type_error",       [r"TypeError", r"argument", r"expected \d+ argument"]),
    ("attribute_error",  [r"AttributeError", r"has no attribute"]),
    ("test_assertion",   [r"AssertionError", r"AssertionError", r"assert .* ==", r"FAILED tests/"]),
    ("timeout",          [r"TimeoutError", r"timed out", r"deadline exceeded"]),
    ("file_not_found",   [r"FileNotFoundError", r"No such file"]),
    ("permission_error", [r"PermissionError", r"Access is denied"]),
    ("value_error",      [r"ValueError", r"invalid literal"]),
]


class FailureAnalyzer:
    """Classifies proposal failures and stores lessons learned."""

    def __init__(self):
        self.lessons: list[dict] = []
        self._load()

    def _load(self):
        """Load lessons from disk."""
        if LESSONS_FILE.exists():
            try:
                self.lessons = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.lessons = []

    def _save(self):
        """Persist lessons to disk."""
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        LESSONS_FILE.write_text(json.dumps(self.lessons, indent=2), encoding="utf-8")

    def analyze_failure(self, proposal: dict, error_output: str) -> dict:
        """Classify a failure and extract a lesson.

        Returns:
            dict with keys: category, pattern, lesson, proposal_title
        """
        category = "unknown"
        matched_pattern = ""

        for cat, patterns in _ERROR_PATTERNS:
            for pat in patterns:
                if re.search(pat, error_output, re.IGNORECASE):
                    category = cat
                    matched_pattern = pat
                    break
            if category != "unknown":
                break

        # Generate a human-readable lesson from the error
        lesson = self._generate_lesson(category, error_output, proposal)

        entry = {
            "timestamp": time.time(),
            "proposal_title": proposal.get("title", "?"),
            "category": category,
            "pattern": matched_pattern,
            "lesson": lesson,
            "files_affected": proposal.get("files_affected", []),
            "error_snippet": error_output[:300],
        }

        # Avoid duplicate lessons (same category + same files)
        if not self._is_duplicate_lesson(entry):
            self.lessons.append(entry)
            # Keep last 50 lessons
            if len(self.lessons) > 50:
                self.lessons = self.lessons[-50:]
            self._save()
            logger.info(f"📝 Lesson learned: [{category}] {lesson}")

        return entry

    def _generate_lesson(self, category: str, error: str, proposal: dict) -> str:
        """Generate a concise, actionable lesson from the error category."""
        title = proposal.get("title", "unknown")
        files = ", ".join(proposal.get("files_affected", []))

        lessons_map = {
            "import_missing": f"When editing {files}: add import statements before using new modules",
            "syntax_error": f"Code generated for '{title}' had syntax errors — validate code structure before writing",
            "name_error": f"Referenced undefined variables in {files} — check all variable names exist in scope",
            "type_error": f"Type mismatch in {files} — verify function signatures and argument types",
            "attribute_error": f"Accessed non-existent attribute in {files} — check object APIs before calling methods",
            "test_assertion": f"Tests failed after '{title}' — the change broke existing assertions, need more conservative edits",
            "timeout": f"Operation timed out during '{title}' — avoid long-running operations in edits",
            "file_not_found": f"Referenced non-existent file in '{title}' — only edit files that actually exist",
            "permission_error": f"Permission denied on files in '{title}' — avoid editing locked/system files",
            "value_error": f"Invalid value in {files} — validate data formats before processing",
        }
        return lessons_map.get(category, f"Unknown failure during '{title}' on {files}")

    def _is_duplicate_lesson(self, new: dict) -> bool:
        """Check if we already have a similar lesson."""
        for existing in self.lessons[-10:]:
            if (existing["category"] == new["category"] and
                    set(existing.get("files_affected", [])) == set(new.get("files_affected", []))):
                return True
        return False

    def get_lessons_for_prompt(self, max_lessons: int = 8) -> str:
        """Format recent lessons for injection into the reflection prompt."""
        if not self.lessons:
            return ""

        recent = self.lessons[-max_lessons:]
        lines = ["LESSONS FROM PAST FAILURES (avoid repeating these mistakes):"]
        for lesson in recent:
            lines.append(f"  ⚠️ [{lesson['category']}] {lesson['lesson']}")

        return "\n".join(lines) + "\n"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Success Tracker
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SuccessTracker:
    """Tracks proposal outcomes and computes confidence by category."""

    def __init__(self):
        self.stats: dict = {"by_category": {}, "total": {"success": 0, "failed": 0}}
        self._load()

    def _load(self):
        """Load stats from disk."""
        if STATS_FILE.exists():
            try:
                self.stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save(self):
        """Persist stats to disk."""
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(self.stats, indent=2), encoding="utf-8")

    def record_outcome(self, proposal: dict, success: bool):
        """Record whether a proposal succeeded or failed."""
        category = proposal.get("category", "unknown")
        if category not in self.stats["by_category"]:
            self.stats["by_category"][category] = {"success": 0, "failed": 0}

        key = "success" if success else "failed"
        self.stats["by_category"][category][key] += 1
        self.stats["total"][key] += 1
        self._save()

        rate = self.get_success_rate(category)
        logger.info(f"📊 {category}: {'✅' if success else '❌'} (rate: {rate:.0%})")

    def get_success_rate(self, category: str) -> float:
        """Get success rate for a category (0.0-1.0)."""
        cat_stats = self.stats["by_category"].get(category, {})
        total = cat_stats.get("success", 0) + cat_stats.get("failed", 0)
        if total == 0:
            return 0.5  # No data → neutral confidence
        return cat_stats.get("success", 0) / total

    def get_confidence_report(self) -> dict:
        """Return confidence scores for all categories."""
        report = {}
        for cat, data in self.stats["by_category"].items():
            total = data["success"] + data["failed"]
            rate = data["success"] / total if total > 0 else 0.5
            report[cat] = {
                "success_rate": round(rate, 2),
                "total_attempts": total,
                "confidence": "high" if rate >= 0.7 else "medium" if rate >= 0.4 else "low",
            }
        return report

    def get_stats_for_prompt(self) -> str:
        """Format stats for injection into reflection prompt."""
        report = self.get_confidence_report()
        if not report:
            return ""

        total = self.stats["total"]
        overall = total["success"] / max(1, total["success"] + total["failed"])

        lines = [f"EXECUTION TRACK RECORD (overall success rate: {overall:.0%}):"]
        for cat, data in sorted(report.items(), key=lambda x: x[1]["success_rate"], reverse=True):
            emoji = "🟢" if data["confidence"] == "high" else "🟡" if data["confidence"] == "medium" else "🔴"
            lines.append(f"  {emoji} {cat}: {data['success_rate']:.0%} success ({data['total_attempts']} attempts)")

        lines.append("STRATEGY: Focus on categories with high success rates. Avoid categories with <30% success.")
        return "\n".join(lines) + "\n"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Codebase Scanner
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CodebaseScanner:
    """Analyzes codebase for complexity, code smells, and actionable findings."""

    SKIP_DIRS = {"venv", "node_modules", "__pycache__", ".git", "memory_db",
                 "rag_data", "browser_recordings", ".gemini"}

    def scan_all(self) -> dict:
        """Run all scans and return a combined report."""
        return {
            "complexity": self.scan_complexity(),
            "smells": self.scan_code_smells(),
            "summary": self._build_summary(),
        }

    def scan_complexity(self) -> list[dict]:
        """Analyze Python files for function complexity using AST."""
        findings = []

        for py_file in PROJECT_ROOT.rglob("*.py"):
            rel = py_file.relative_to(PROJECT_ROOT)
            if any(part in self.SKIP_DIRS for part in rel.parts):
                continue

            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Count lines
                        if hasattr(node, "end_lineno") and node.end_lineno:
                            length = node.end_lineno - node.lineno + 1
                        else:
                            length = len(ast.dump(node).split("\n"))

                        # Count branches (if/elif/for/while/try/except)
                        branches = sum(1 for child in ast.walk(node)
                                       if isinstance(child, (ast.If, ast.For, ast.While,
                                                             ast.Try, ast.ExceptHandler)))

                        # Flag functions that are too long or complex
                        if length > 40 or branches > 8:
                            findings.append({
                                "file": str(rel).replace("\\", "/"),
                                "function": node.name,
                                "lines": length,
                                "branches": branches,
                                "severity": "high" if length > 80 or branches > 12 else "medium",
                            })
            except (SyntaxError, UnicodeDecodeError):
                continue

        # Sort by severity (high first), then by lines
        findings.sort(key=lambda x: (0 if x["severity"] == "high" else 1, -x["lines"]))
        return findings[:15]  # Top 15 most complex functions

    def scan_code_smells(self) -> list[dict]:
        """Find code smells: large files, TODOs, unused imports, duplicates."""
        smells = []

        for ext in ("*.py", "*.js"):
            for f in PROJECT_ROOT.rglob(ext):
                rel = f.relative_to(PROJECT_ROOT)
                if any(part in self.SKIP_DIRS for part in rel.parts):
                    continue

                try:
                    content = f.read_text(encoding="utf-8")
                    lines = content.splitlines()
                    rel_str = str(rel).replace("\\", "/")

                    # Large files
                    if len(lines) > 500:
                        smells.append({
                            "type": "large_file",
                            "file": rel_str,
                            "detail": f"{len(lines)} lines — consider splitting into modules",
                            "severity": "high" if len(lines) > 800 else "medium",
                        })

                    # TODO/FIXME/HACK comments
                    for i, line in enumerate(lines, 1):
                        for marker in ("TODO", "FIXME", "HACK", "XXX"):
                            if marker in line and not line.strip().startswith("#!"):
                                smells.append({
                                    "type": "todo_marker",
                                    "file": rel_str,
                                    "line": i,
                                    "detail": line.strip()[:100],
                                    "severity": "low",
                                })

                    # Unused imports (Python only, simple heuristic)
                    if ext == "*.py" or f.suffix == ".py":
                        self._check_unused_imports(content, lines, rel_str, smells)

                except (UnicodeDecodeError, PermissionError):
                    continue

        # Prioritize and limit
        smells.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3))
        return smells[:20]

    def _check_unused_imports(self, content: str, lines: list[str],
                              rel_str: str, smells: list):
        """Simple heuristic for unused imports in Python files."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    # Check if name is used anywhere else in the file
                    uses = content.count(name)
                    if uses <= 1:  # Only the import line itself
                        smells.append({
                            "type": "unused_import",
                            "file": rel_str,
                            "line": node.lineno,
                            "detail": f"import {alias.name} — appears unused",
                            "severity": "low",
                        })
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name == "*":
                        continue
                    uses = content.count(name)
                    if uses <= 1:
                        smells.append({
                            "type": "unused_import",
                            "file": rel_str,
                            "line": node.lineno,
                            "detail": f"from {node.module} import {alias.name} — appears unused",
                            "severity": "low",
                        })

    def _build_summary(self) -> dict:
        """Build a summary of the codebase."""
        file_counts = {"py": 0, "js": 0, "html": 0, "css": 0, "other": 0}
        total_lines = 0

        for f in PROJECT_ROOT.rglob("*"):
            rel = f.relative_to(PROJECT_ROOT)
            if any(part in self.SKIP_DIRS for part in rel.parts):
                continue
            if f.is_file():
                ext = f.suffix.lstrip(".")
                if ext in file_counts:
                    file_counts[ext] += 1
                else:
                    file_counts["other"] += 1
                try:
                    total_lines += len(f.read_text(encoding="utf-8").splitlines())
                except (UnicodeDecodeError, PermissionError):
                    pass

        return {"file_counts": file_counts, "total_lines": total_lines}

    def get_findings_for_prompt(self, max_findings: int = 8) -> str:
        """Format top findings for the reflection prompt."""
        complexity = self.scan_complexity()
        smells = self.scan_code_smells()

        if not complexity and not smells:
            return ""

        lines = ["CODEBASE HEALTH SCAN RESULTS:"]

        # Top complex functions
        if complexity:
            lines.append("  Complex functions (consider refactoring):")
            for finding in complexity[:4]:
                lines.append(
                    f"    📐 {finding['file']}::{finding['function']}() "
                    f"— {finding['lines']} lines, {finding['branches']} branches [{finding['severity']}]"
                )

        # Top code smells
        high_smells = [s for s in smells if s["severity"] in ("high", "medium")]
        if high_smells:
            lines.append("  Code smells:")
            for smell in high_smells[:4]:
                lines.append(f"    🔍 {smell['file']}: {smell['detail']} [{smell['severity']}]")

        lines.append("  USE THESE FINDINGS to generate specific, evidence-based proposals.")
        return "\n".join(lines) + "\n"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Performance Profiler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PerformanceProfiler:
    """Monitors memory usage and parses logs for slow operations."""
    
    def __init__(self):
        self.findings = []
        
    def scan_performance(self) -> list[str]:
        """Gather memory info and find slow logs."""
        msgs = []
        
        # 1. Memory usage of current process
        try:
            process = psutil.Process(os.getpid())
            mem_mb = process.memory_info().rss / (1024 * 1024)
            if mem_mb > 500:
                msgs.append(f"⚠️ High Memory Usage: Current process is using {mem_mb:.1f} MB RAM. Consider caching strategies or garbage collection.")
            else:
                msgs.append(f"🟢 Memory Usage: Healthy ({mem_mb:.1f} MB).")
        except Exception as e:
            logger.warning(f"Memory profiling failed: {e}")
            
        # 2. Database size (if relevant)
        db_path = WORKSPACE / "db" / "localmind.db"
        if db_path.exists():
            db_mb = db_path.stat().st_size / (1024 * 1024)
            if db_mb > 100:
                msgs.append(f"⚠️ Large Database: SQLite DB has grown to {db_mb:.1f} MB. Consider adding indexing or vacuuming.")
                
        # 3. Log parsing for slow operations (naive heuristic)
        log_file = WORKSPACE / "logs" / "server.log"
        if log_file.exists():
            try:
                # Read last 1000 lines efficiently
                with open(log_file, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    chunk_size = min(size, 64 * 1024)
                    f.seek(size - chunk_size)
                    tail = f.read().decode("utf-8", errors="ignore").splitlines()
                
                slow_count = sum(1 for line in tail[-1000:] if "took" in line and any(str(i)+"ms" in line for i in range(500, 9999)))
                if slow_count > 5:
                    msgs.append(f"⚠️ Slow Operations: Found {slow_count} requests taking >500ms in recent logs. Focus strictly on performance optimization.")
            except Exception:
                pass
                
        self.findings = msgs
        return msgs

    def get_findings_for_prompt(self) -> str:
        """Format performance findings for reflection prompt."""
        self.scan_performance()
        if not self.findings:
            return ""
            
        lines = ["SYSTEM PERFORMANCE PROFILE:"]
        for msg in self.findings:
            lines.append(f"  {msg}")
        return "\n".join(lines) + "\n"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. External Researcher
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ExternalResearcher:
    """Uses Wikipedia as a zero-dependency external knowledge base for architecture best practices."""
    
    def __init__(self):
        self.cache = {}
        
    async def get_best_practices(self, topic: str) -> str:
        """Search Wikipedia for software engineering concepts."""
        if topic in self.cache:
            return self.cache[topic]
            
        # Map our internal categories to Wikipedia search terms
        search_terms = {
            "performance": "Software performance caching optimization",
            "security": "Application security OWASP",
            "ux": "User experience interface design",
            "code_quality": "Software design pattern clean code",
            "feature": "Software feature toggle architecture",
            "bugfix": "Software bug tracking testing"
        }
        
        query = search_terms.get(topic, f"software engineering {topic}")
        
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "utf8": "",
            "format": "json",
            "srlimit": 2
        }
        
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": "LocalMind/1.0 (https://github.com/SamDeiter/LocalMind)"}
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("query", {}).get("search", [])
                    if results:
                        # Clean HTML tags from Wikipedia snippets
                        cleanr = re.compile('<.*?>')
                        snippets = []
                        for res in results:
                            raw = res.get("snippet", "")
                            clean_text = re.sub(cleanr, '', raw)
                            snippets.append(f"- {res.get('title')}: {clean_text}")
                            
                        result = "\n".join(snippets)
                        self.cache[topic] = result
                        return result
        except Exception as e:
            logger.warning(f"External research failed: {e}")
            
        return ""
        
    async def get_findings_for_prompt(self, focus_category: str) -> str:
        """Get best practices for the current focus category."""
        insights = await self.get_best_practices(focus_category)
        if not insights:
            return ""
            
        lines = [f"EXTERNAL BEST PRACTICES FOR '{focus_category.upper()}':"]
        lines.append(insights)
        lines.append("  (Apply these industry standards to your proposed changes.)")
        return "\n".join(lines) + "\n"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. Academic Research Scraper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ACADEMIC_CACHE_FILE = WORKSPACE / "academic_cache.json"
ACADEMIC_CACHE_TTL = 6 * 3600  # 6 hours


class AcademicResearcher:
    """Searches academic sources (arxiv, Google Scholar, ResearchGate, Sci-Hub)
    for papers with techniques the autonomy engine can implement.
    
    Pipeline:
      1. Map focus category → academic search terms
      2. Query all 4 sources in parallel
      3. Deduplicate by title similarity
      4. Format top results as implementable-technique summaries
      5. Feed into reflection prompt → AI generates concrete proposals
    """

    # Map LocalMind categories to academic search queries
    CATEGORY_QUERIES = {
        "performance": [
            "software performance optimization caching",
            "latency reduction web application",
            "memory efficient data structures",
        ],
        "security": [
            "application security vulnerability detection",
            "automated code security analysis",
            "injection prevention web application",
        ],
        "feature": [
            "AI code assistant autonomous agent",
            "large language model tool use",
            "self-improving AI system",
        ],
        "code_quality": [
            "automated code refactoring techniques",
            "static analysis software quality",
            "technical debt detection automated",
        ],
        "ux": [
            "user interface design AI assistant",
            "human computer interaction code editor",
            "developer experience tooling",
        ],
        "bugfix": [
            "automated bug detection repair",
            "fault localization software",
            "program repair technique",
        ],
    }

    def __init__(self):
        self.cache: dict = {}
        self._load_cache()

    def _load_cache(self):
        """Load cached results from disk."""
        if ACADEMIC_CACHE_FILE.exists():
            try:
                data = json.loads(ACADEMIC_CACHE_FILE.read_text(encoding="utf-8"))
                # Prune expired entries
                now = time.time()
                self.cache = {
                    k: v for k, v in data.items()
                    if now - v.get("timestamp", 0) < ACADEMIC_CACHE_TTL
                }
            except Exception:
                self.cache = {}

    def _save_cache(self):
        """Persist cache to disk."""
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        try:
            ACADEMIC_CACHE_FILE.write_text(
                json.dumps(self.cache, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Failed to save academic cache: {e}")

    def _get_queries(self, category: str) -> list[str]:
        """Get search queries for a category."""
        return self.CATEGORY_QUERIES.get(
            category,
            [f"software engineering {category}", f"AI {category} automation"],
        )

    # ── Source 1: arxiv (free REST API) ─────────────────────

    async def search_arxiv(self, query: str, max_results: int = 5) -> list[dict]:
        """Search arxiv.org via their free Atom API.
        
        Returns list of {title, authors, abstract, url, published}.
        """
        import feedparser

        url = "https://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                headers={"User-Agent": "LocalMind/1.0 (academic-research)"},
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    return []

                feed = feedparser.parse(resp.text)
                results = []
                for entry in feed.entries[:max_results]:
                    results.append({
                        "title": entry.get("title", "").replace("\n", " ").strip(),
                        "authors": ", ".join(
                            a.get("name", "") for a in entry.get("authors", [])
                        )[:100],
                        "abstract": entry.get("summary", "")[:400].strip(),
                        "url": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "source": "arxiv",
                    })
                return results
        except Exception as e:
            logger.warning(f"arxiv search failed: {e}")
            return []

    # ── Source 2: Google Scholar (HTML scrape with caution) ──

    async def search_scholar(self, query: str, max_results: int = 3) -> list[dict]:
        """Search Google Scholar via HTML scraping.
        
        Rate-limited and cached aggressively to avoid blocks.
        Returns list of {title, snippet, url, source}.
        """
        url = "https://scholar.google.com/scholar"
        params = {"q": query, "hl": "en", "num": max_results}

        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning(f"Scholar returned {resp.status_code}")
                    return []

                html = resp.text
                results = []

                # Parse result blocks (gs_ri class contains each result)
                title_pattern = re.compile(
                    r'<h3[^>]*class="gs_rt"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                    re.DOTALL,
                )
                snippet_pattern = re.compile(
                    r'<div[^>]*class="gs_rs"[^>]*>(.*?)</div>', re.DOTALL
                )

                titles = title_pattern.findall(html)
                snippets = snippet_pattern.findall(html)

                for i, (href, raw_title) in enumerate(titles[:max_results]):
                    clean_title = re.sub(r"<[^>]+>", "", raw_title).strip()
                    snippet = ""
                    if i < len(snippets):
                        snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()[:300]

                    if clean_title:
                        results.append({
                            "title": clean_title,
                            "abstract": snippet,
                            "url": href,
                            "source": "google_scholar",
                        })

                return results

        except Exception as e:
            logger.warning(f"Scholar search failed: {e}")
            return []

    # ── Source 3: ResearchGate (public search) ──────────────

    async def search_researchgate(self, query: str, max_results: int = 3) -> list[dict]:
        """Search ResearchGate for public research papers.
        
        Returns list of {title, abstract, url, source}.
        """
        url = "https://www.researchgate.net/search/publication"
        params = {"q": query}

        try:
            async with httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                },
                follow_redirects=True,
            ) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    return []

                html = resp.text
                results = []

                # Parse publication titles and links
                pub_pattern = re.compile(
                    r'<a[^>]*class="[^"]*nova-[^"]*"[^>]*href="(/publication/[^"]*)"[^>]*>(.*?)</a>',
                    re.DOTALL,
                )

                matches = pub_pattern.findall(html)
                for href, raw_title in matches[:max_results]:
                    clean_title = re.sub(r"<[^>]+>", "", raw_title).strip()
                    if clean_title and len(clean_title) > 10:
                        results.append({
                            "title": clean_title,
                            "abstract": "",
                            "url": f"https://www.researchgate.net{href}",
                            "source": "researchgate",
                        })

                return results

        except Exception as e:
            logger.warning(f"ResearchGate search failed: {e}")
            return []

    # ── Source 4: Sci-Hub (DOI → URL constructor only) ──────

    def construct_scihub_url(self, doi: str) -> str:
        """Construct a Sci-Hub URL for a given DOI.
        
        NOTE: This only constructs the URL for reference.
        It does NOT auto-download any content.
        """
        if doi:
            return f"https://sci-hub.pub/{doi}"
        return ""

    # ── Aggregator: search all sources ──────────────────────

    async def search_all(self, category: str, max_per_source: int = 3) -> list[dict]:
        """Search all academic sources for a category.
        
        Uses caching to avoid repeated API calls.
        Returns a deduplicated list of papers.
        """
        cache_key = f"{category}_{int(time.time() // ACADEMIC_CACHE_TTL)}"
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if time.time() - cached.get("timestamp", 0) < ACADEMIC_CACHE_TTL:
                logger.info(f"Academic cache hit for '{category}'")
                return cached.get("results", [])

        queries = self._get_queries(category)
        all_results = []

        for query in queries[:2]:  # Max 2 queries per cycle to be respectful
            # Run sources with staggered delays to avoid rate limits
            try:
                arxiv_results = await self.search_arxiv(query, max_per_source)
                all_results.extend(arxiv_results)
            except Exception as e:
                logger.warning(f"arxiv batch failed: {e}")

            await asyncio.sleep(1)  # Polite delay between sources

            try:
                scholar_results = await self.search_scholar(query, max_per_source)
                all_results.extend(scholar_results)
            except Exception as e:
                logger.warning(f"Scholar batch failed: {e}")

            await asyncio.sleep(1)

            try:
                rg_results = await self.search_researchgate(query, max_per_source)
                all_results.extend(rg_results)
            except Exception as e:
                logger.warning(f"ResearchGate batch failed: {e}")

        # Deduplicate by title similarity (case-insensitive substring match)
        deduped = []
        seen_titles = set()
        for paper in all_results:
            title_key = paper["title"].lower()[:60]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                deduped.append(paper)

        # Cache results
        self.cache[cache_key] = {
            "timestamp": time.time(),
            "results": deduped[:10],  # Keep top 10
        }
        self._save_cache()

        logger.info(
            f"Academic research: found {len(deduped)} papers for '{category}' "
            f"(arxiv: {sum(1 for r in all_results if r.get('source') == 'arxiv')}, "
            f"scholar: {sum(1 for r in all_results if r.get('source') == 'google_scholar')}, "
            f"researchgate: {sum(1 for r in all_results if r.get('source') == 'researchgate')})"
        )

        return deduped[:10]

    # ── Prompt Formatter ────────────────────────────────────

    async def get_findings_for_prompt(self, category: str, max_papers: int = 5) -> str:
        """Format academic findings as an implementable-techniques prompt block.
        
        This is the key output: it tells the AI to extract concrete techniques
        from these papers and generate proposals to implement them.
        """
        papers = await self.search_all(category, max_per_source=3)

        if not papers:
            return ""

        lines = [
            f"ACADEMIC RESEARCH — IMPLEMENTABLE TECHNIQUES FOR '{category.upper()}':",
            "The following papers describe techniques you can IMPLEMENT in this codebase.",
            "Read each one and extract a SPECIFIC, ACTIONABLE technique to propose.\n",
        ]

        for i, paper in enumerate(papers[:max_papers], 1):
            title = paper.get("title", "Unknown")
            abstract = paper.get("abstract", "No abstract available")[:250]
            source = paper.get("source", "unknown")
            url = paper.get("url", "")

            lines.append(f"  📄 Paper {i} [{source}]: {title}")
            if abstract:
                lines.append(f"     Summary: {abstract}")
            if url:
                lines.append(f"     Link: {url}")

            # Add Sci-Hub link if we can extract a DOI
            doi_match = re.search(r"(10\.\d{4,}/[^\s]+)", url)
            if doi_match:
                scihub_url = self.construct_scihub_url(doi_match.group(1))
                lines.append(f"     Full text: {scihub_url}")
            lines.append("")

        lines.append(
            "INSTRUCTIONS: Pick ONE technique from the papers above and propose a "
            "CONCRETE implementation for THIS codebase. Your proposal must include "
            "specific files to modify and describe the exact code changes needed. "
            "Reference the paper title in your proposal description."
        )

        return "\n".join(lines) + "\n"

