import json
import logging
import re
import time
from pathlib import Path
from backend.config import DB_PATH, PROPOSALS_DIR, PROJECT_ROOT

logger = logging.getLogger("localmind.research.analyzer")

WORKSPACE = PROPOSALS_DIR.parent
LESSONS_FILE = WORKSPACE / "lessons_learned.json"
STATS_FILE = WORKSPACE / "execution_stats.json"

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
        self.lessons = []
        self._load()

    def _load(self):
        if LESSONS_FILE.exists():
            try:
                self.lessons = json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.lessons = []

    def _save(self):
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        LESSONS_FILE.write_text(json.dumps(self.lessons, indent=2), encoding="utf-8")

    def analyze_failure(self, proposal: dict, error_output: str) -> dict:
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
        if not self._is_duplicate_lesson(entry):
            self.lessons.append(entry)
            if len(self.lessons) > 50:
                self.lessons = self.lessons[-50:]
            self._save()
            logger.info(f"📝 Lesson learned: [{category}] {lesson}")
        return entry

    def _generate_lesson(self, category: str, error: str, proposal: dict) -> str:
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
        for existing in self.lessons[-10:]:
            if (existing["category"] == new["category"] and
                    set(existing.get("files_affected", [])) == set(new.get("files_affected", []))):
                return True
        return False

    def get_lessons_for_prompt(self, max_lessons: int = 8) -> str:
        if not self.lessons: return ""
        recent = self.lessons[-max_lessons:]
        lines = ["LESSONS FROM PAST FAILURES (avoid repeating these mistakes):"]
        for lesson in recent:
            lines.append(f"  ⚠️ [{lesson['category']}] {lesson['lesson']}")
        return "\n".join(lines) + "\n"

class SuccessTracker:
    """Tracks proposal outcomes and computes confidence by category."""
    def __init__(self):
        self.stats = {"by_category": {}, "total": {"success": 0, "failed": 0}}
        self._load()

    def _load(self):
        if STATS_FILE.exists():
            try:
                self.stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

    def _save(self):
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(self.stats, indent=2), encoding="utf-8")

    def record_outcome(self, proposal: dict, success: bool):
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
        cat_stats = self.stats["by_category"].get(category, {})
        total = cat_stats.get("success", 0) + cat_stats.get("failed", 0)
        return cat_stats.get("success", 0) / total if total > 0 else 0.5

    def get_confidence_report(self) -> dict:
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
        report = self.get_confidence_report()
        if not report: return ""
        total = self.stats["total"]
        overall = total["success"] / max(1, total["success"] + total["failed"])
        lines = [f"EXECUTION TRACK RECORD (overall success rate: {overall:.0%}):"]
        for cat, data in sorted(report.items(), key=lambda x: x[1]["success_rate"], reverse=True):
            emoji = "🟢" if data["confidence"] == "high" else "🟡" if data["confidence"] == "medium" else "🔴"
            lines.append(f"  {emoji} {cat}: {data['success_rate']:.0%} success ({data['total_attempts']} attempts)")
        lines.append("STRATEGY: Focus on categories with high success rates. Avoid categories with <30% success.")
        return "\n".join(lines) + "\n"
