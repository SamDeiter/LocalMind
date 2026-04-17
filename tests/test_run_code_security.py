import pytest
import asyncio
from backend.tools.run_code import RunCodeTool

@pytest.mark.asyncio
async def test_run_code_safety_regex():
    tool = RunCodeTool()

    # Simple os.remove (blocked by regex)
    result = await tool.execute(code="import os\nos.remove('important.txt')")
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "regex" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_ast_module():
    tool = RunCodeTool()

    # Blocked module import (blocked by AST)
    result = await tool.execute(code="import subprocess\nsubprocess.run(['ls'])")
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "blocked module" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_ast_func():
    tool = RunCodeTool()

    # Blocked function call (blocked by Regex first, but AST would catch it too)
    result = await tool.execute(code="eval('print(123)')")
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    # It might be caught by regex or AST depending on the pattern.
    # Current regex catches 'eval('

@pytest.mark.asyncio
async def test_run_code_safety_obfuscation_getattr():
    tool = RunCodeTool()

    # Obfuscation: getattr(os, 'system')
    # This is caught by both 'os' being a blocked name and 'getattr' being a blocked function
    result = await tool.execute(code="import os\ngetattr(os, 'system')('ls')")
    assert result["success"] is False
    assert "BLOCKED" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_obfuscation_concat():
    tool = RunCodeTool()

    # Obfuscation: getattr(os, 'sys' + 'tem')
    # Caught by the BinOp check in AST
    result = await tool.execute(code="import os\ngetattr(os, 'sys' + 'tem')('ls')")
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "dynamic attribute access" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_aliasing():
    tool = RunCodeTool()

    # Aliasing: o = os; o.remove('file')
    # Caught by the Name check for 'os'
    result = await tool.execute(code="import os\no = os\no.remove('file')")
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "blocked name 'os'" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_safe_code():
    tool = RunCodeTool()

    # Safe code
    result = await tool.execute(code="print('Hello, World!')\nx = 1 + 2\nprint(x)")
    assert result["success"] is True
    assert "3" in result["result"]
