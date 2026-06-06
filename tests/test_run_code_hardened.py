import pytest
from backend.tools.run_code import RunCodeTool

@pytest.mark.asyncio
async def test_run_code_blocks_os_system():
    tool = RunCodeTool()
    code = "import os; os.system('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "os.system" in result["error"]

@pytest.mark.asyncio
async def test_run_code_blocks_subprocess():
    tool = RunCodeTool()
    code = "import subprocess; subprocess.run(['ls'])"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "subprocess" in result["error"]

@pytest.mark.asyncio
async def test_run_code_blocks_getattr():
    tool = RunCodeTool()
    code = "getattr(os, 'system')('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "getattr" in result["error"]

@pytest.mark.asyncio
async def test_run_code_blocks_builtins():
    tool = RunCodeTool()
    code = "print(__builtins__)"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "BLOCKED" in result["error"]
    assert "__builtins__" in result["error"]

@pytest.mark.asyncio
async def test_run_code_allows_safe_code():
    tool = RunCodeTool()
    code = "print('Hello, World!')"
    result = await tool.execute(code=code)
    assert result["success"] is True
    assert result["result"] == "Hello, World!"
