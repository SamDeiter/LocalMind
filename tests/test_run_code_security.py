import pytest
import asyncio
from backend.tools.run_code import RunCodeTool

@pytest.mark.asyncio
async def test_run_code_safety_regex_blocks():
    tool = RunCodeTool()

    # Direct os.system
    code = "import os; os.system('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "os.system" in result["error"]

    # Direct subprocess
    code = "import subprocess; subprocess.run(['ls'])"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "subprocess" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_ast_blocks_getattr():
    tool = RunCodeTool()

    # getattr obfuscation
    code = """
import os
func = getattr(os, "system")
func("echo Vulnerable")
"""
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "getattr" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_ast_blocks_import():
    tool = RunCodeTool()

    # __import__ obfuscation
    code = "mod = __import__('os'); mod.system('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "__import__" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_ast_blocks_attribute_access():
    tool = RunCodeTool()

    # Simple attribute access without call (regex might miss if spaced weirdly)
    code = "import os\nx = os  .  system"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "os.system" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_allows_benign_code():
    tool = RunCodeTool()

    code = "print('Hello, World!'); x = 1 + 1; print(x)"
    result = await tool.execute(code=code)
    assert result["success"] is True
    assert "Hello, World!" in result["result"]
    assert "2" in result["result"]

@pytest.mark.asyncio
async def test_run_code_safety_blocks_eval_exec():
    tool = RunCodeTool()

    for dangerous in ["eval('1+1')", "exec('print(1)')"]:
        result = await tool.execute(code=dangerous)
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
