"""
Global Codebase Context Tool.
"""

from typing import Any
import os
import re
from pathlib import Path
from backend.config import PROJECT_ROOT
from .base import BaseTool

class ASTAnalyzerTool(BaseTool):
    @property
    def name(self) -> str:
        return "ast_analyzer"

    @property
    def description(self) -> str:
        return "Find definitions, classes, and function signatures across the entire project."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "search_term": {"type": "string", "description": "Function or class name to find"},
                "file_extension": {"type": "string", "description": "Optional file extension (e.g. .py)"}
            },
            "required": ["search_term"]
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        term = kwargs.get("search_term")
        ext = kwargs.get("file_extension", ".py").lower()
        cwd = PROJECT_ROOT
        
        matches = []
        pattern = re.compile(rf"(class|def|const|let|var)\s+{term}[\(\s:]")
        
        for root, dirs, files in os.walk(cwd):
            if ".git" in root or ".venv" in root or "node_modules" in root:
                continue
            for file in files:
                if file.endswith(ext):
                    filepath = Path(root) / file
                    try:
                        content = filepath.read_text(encoding="utf-8")
                        for i, line in enumerate(content.splitlines()):
                            if pattern.search(line):
                                matches.append(f"{filepath.relative_to(cwd)}:{i+1} -> {line.strip()}")
                    except Exception:
                        pass
                        
        if not matches:
            return {"success": False, "result": "No definitions found."}
        return {"success": True, "result": "\n".join(matches[:20])}
