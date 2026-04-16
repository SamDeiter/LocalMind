import pytest
import asyncio
from backend.tools.run_code import RunCodeTool

@pytest.mark.asyncio
async def test_run_code_safety_aliasing():
    tool = RunCodeTool()
    # Name aliasing
    code = "e = exec; e('print(1)')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "alias" in result["error"]

    # Attribute aliasing
    code = "import os\ns = os.system\ns('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "alias" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_import_from():
    tool = RunCodeTool()
    code = "from os import system\nsystem('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "import from" in result["error"]

@pytest.mark.asyncio
async def test_run_code_safety_getattr_module():
    tool = RunCodeTool()
    code = "import os\ngetattr(os, 'system')('ls')"
    result = await tool.execute(code=code)
    assert result["success"] is False
    assert "getattr" in result["error"]

@pytest.mark.asyncio
async def test_run_code_normal_execution():
    tool = RunCodeTool()
    code = "print('Hello ' + 'World')"
    result = await tool.execute(code=code)
    assert result["success"] is True
    assert "Hello World" in result["result"]

@pytest.mark.asyncio
async def test_run_code_getattr_safe():
    tool = RunCodeTool()
    # Legit use of common names on non-dangerous objects
    # 'run' is in DANGEROUS_ATTRS['subprocess']
    code = "class MyObj:\n    def run(self):\n        print('running')\no = MyObj()\no.run()"
    result = await tool.execute(code=code)
    assert result["success"] is True
    assert "running" in result["result"]
