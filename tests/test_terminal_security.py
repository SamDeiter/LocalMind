
import asyncio
import pytest
import re
from backend.tools.terminal import TerminalTool, DANGEROUS_COMMANDS

@pytest.mark.asyncio
async def test_terminal_security_bypass_prevention():
    # Test the regex pattern directly
    pattern = r"(?:^|[;&|]|\n)\s*\b(" + "|".join(re.escape(cmd) for cmd in DANGEROUS_COMMANDS) + r")\b"

    # Valid bypass attempts that should NOW be caught
    assert bool(re.search(pattern, "  rm -rf /"))
    assert bool(re.search(pattern, "ls; rm -rf /"))
    assert bool(re.search(pattern, "ls && rm -rf /"))
    assert bool(re.search(pattern, "ls || rm -rf /"))
    assert bool(re.search(pattern, "echo hello\nrm -rf /"))
    assert bool(re.search(pattern, "pip install requests"))

    # Cases that should NOT be caught (legitimate commands containing substrings)
    assert not bool(re.search(pattern, "echo rm"))
    assert not bool(re.search(pattern, "format_disk")) # 'format' is dangerous but 'format_disk' is a different word
    assert not bool(re.search(pattern, "ls -l"))

@pytest.mark.asyncio
async def test_terminal_cwd_is_project_root():
    from backend.config import PROJECT_ROOT
    tool = TerminalTool()
    # Run 'pwd' on Linux or 'cd' on Windows to check current directory
    # Since we are in a Linux-like environment in the sandbox:
    result = await tool.execute(command="pwd")
    if result["success"]:
        assert result["stdout"].strip() == str(PROJECT_ROOT)
