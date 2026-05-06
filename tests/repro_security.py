import unittest
from pathlib import Path
from backend.config import PROJECT_ROOT
from backend.tools.ast_analyzer import ASTAnalyzerTool

class TestHardcodedPath(unittest.TestCase):
    def test_ast_analyzer_uses_project_root(self):
        # This test verifies that ASTAnalyzerTool uses PROJECT_ROOT instead of a hardcoded Windows path
        tool = ASTAnalyzerTool()
        # We can't easily check the local variable 'cwd' inside 'execute',
        # but we can check the file content itself or verify it works on this system.

        path = Path("backend/tools/ast_analyzer.py")
        content = path.read_text()

        self.assertNotIn(r"c:\Users\Sam Deiter", content)
        self.assertIn("cwd = PROJECT_ROOT", content)

if __name__ == "__main__":
    unittest.main()
