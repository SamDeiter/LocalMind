import ast
import logging
import os
import psutil
from pathlib import Path
from backend.config import PROJECT_ROOT, PROPOSALS_DIR

logger = logging.getLogger("localmind.research.scanner")
WORKSPACE = PROPOSALS_DIR.parent

class CodebaseScanner:
    SKIP_DIRS = {"venv", "node_modules", "__pycache__", ".git", "memory_db",
                 "rag_data", "browser_recordings", ".gemini"}

    def scan_all(self) -> dict:
        return {
            "complexity": self.scan_complexity(),
            "smells": self.scan_code_smells(),
            "summary": self._build_summary(),
        }

    def scan_complexity(self) -> list[dict]:
        findings = []
        for py_file in PROJECT_ROOT.rglob("*.py"):
            rel = py_file.relative_to(PROJECT_ROOT)
            if any(part in self.SKIP_DIRS for part in rel.parts): continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        length = (node.end_lineno - node.lineno + 1) if hasattr(node, "end_lineno") and node.end_lineno else 10
                        branches = sum(1 for child in ast.walk(node) if isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler)))
                        if length > 40 or branches > 8:
                            findings.append({
                                "file": str(rel).replace("\\", "/"),
                                "function": node.name,
                                "lines": length,
                                "branches": branches,
                                "severity": "high" if length > 80 or branches > 12 else "medium",
                            })
            except Exception: continue
        findings.sort(key=lambda x: (0 if x["severity"] == "high" else 1, -x["lines"]))
        return findings[:15]

    def scan_code_smells(self) -> list[dict]:
        smells = []
        for ext in ("*.py", "*.js"):
            for f in PROJECT_ROOT.rglob(ext):
                rel = f.relative_to(PROJECT_ROOT)
                if any(part in self.SKIP_DIRS for part in rel.parts): continue
                try:
                    content = f.read_text(encoding="utf-8")
                    lines = content.splitlines()
                    rel_str = str(rel).replace("\\", "/")
                    if len(lines) > 500:
                        smells.append({"type": "large_file", "file": rel_str, "detail": f"{len(lines)} lines", "severity": "high" if len(lines) > 800 else "medium"})
                    for i, line in enumerate(lines, 1):
                        for marker in ("TODO", "FIXME", "HACK"):
                            if marker in line and not line.strip().startswith("#!"):
                                smells.append({"type": "todo_marker", "file": rel_str, "line": i, "detail": line.strip()[:100], "severity": "low"})
                except Exception: continue
        smells.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x["severity"], 3))
        return smells[:20]

    def _build_summary(self) -> dict:
        file_counts = {"py": 0, "js": 0, "html": 0, "css": 0, "other": 0}
        total_lines = 0
        for f in PROJECT_ROOT.rglob("*"):
            rel = f.relative_to(PROJECT_ROOT)
            if any(part in self.SKIP_DIRS for part in rel.parts): continue
            if f.is_file():
                ext = f.suffix.lstrip(".")
                if ext in file_counts: file_counts[ext] += 1
                else: file_counts["other"] += 1
                try: total_lines += len(f.read_text(encoding="utf-8").splitlines())
                except Exception: pass
        return {"file_counts": file_counts, "total_lines": total_lines}

    def get_findings_for_prompt(self, max_findings: int = 8) -> str:
        complexity = self.scan_complexity()
        smells = self.scan_code_smells()
        if not complexity and not smells: return ""
        lines = ["CODEBASE HEALTH SCAN RESULTS:"]
        if complexity:
            lines.append("  Complex functions (consider refactoring):")
            for finding in complexity[:4]:
                lines.append(f"    📐 {finding['file']}::{finding['function']}() - {finding['lines']} lines [{finding['severity']}]")
        if smells:
            lines.append("  Code smells:")
            for smell in [s for s in smells if s["severity"] in ("high", "medium")][:4]:
                lines.append(f"    🔍 {smell['file']}: {smell['detail']} [{smell['severity']}]")
        return "\n".join(lines) + "\n"

class PerformanceProfiler:
    def scan_performance(self) -> list[str]:
        msgs = []
        try:
            process = psutil.Process(os.getpid())
            mem_mb = process.memory_info().rss / (1024 * 1024)
            msgs.append(f"{'⚠️ High' if mem_mb > 500 else '🟢'} Memory Usage: {mem_mb:.1f} MB RAM.")
        except: pass
        return msgs

    def get_findings_for_prompt(self) -> str:
        findings = self.scan_performance()
        if not findings: return ""
        return "SYSTEM PERFORMANCE PROFILE:\n" + "\n".join(f"  {m}" for m in findings) + "\n"
