import pytest
from backend.tools.run_code import RunCodeTool

@pytest.mark.asyncio
async def test_run_code_blocked_patterns():
    tool = RunCodeTool()

    dangerous_cases = [
        "import os; os.system('ls')",
        "import subprocess; subprocess.run(['ls'])",
        "getattr(os, 'system')('ls')",
        "__builtins__.eval('1+1')",
        "import importlib; importlib.import_module('os')",
        "__import__('os').system('ls')",
        "import sys; sys.modules['os'].system('ls')",
        "import os; os.popen('ls')",
        "import os; os.spawnv(os.P_WAIT, '/bin/ls', ['ls'])",
    ]

    for code in dangerous_cases:
        result = await tool.execute(code=code)
        assert result["success"] is False, f"Failed to block: {code}"
        assert "BLOCKED" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safe_patterns():
    tool = RunCodeTool()

    safe_cases = [
        "print('hello world')",
        "x = 1 + 1; print(x)",
        "import math; print(math.sqrt(16))",
        "def hello(): return 'hi'\nprint(hello())",
    ]

    for code in safe_cases:
        result = await tool.execute(code=code)
        assert result["success"] is True
        assert "error" not in result
