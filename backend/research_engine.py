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
            async with httpx.AsyncClient(timeout=10.0) as client:
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
