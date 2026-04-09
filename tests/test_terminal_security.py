from unittest.mock import AsyncMock, patch

import pytest

from backend.tools.terminal import TerminalTool


@pytest.mark.asyncio
async def test_terminal_tool_dangerous_command():
    tool = TerminalTool()

    # Mock ProposeActionTool
    with patch("backend.tools.terminal.ProposeActionTool") as MockProposer:
        mock_proposer_instance = MockProposer.return_value
        mock_proposer_instance.execute = AsyncMock(return_value={"approved": False})

        # Test direct dangerous command
        result = await tool.execute(command="rm -rf /")
        assert result["success"] is False
        assert "User denied execution" in result["error"]

        # Test dangerous command after operator
        result = await tool.execute(command="ls ; rm -rf /")
        assert result["success"] is False
        assert "User denied execution" in result["error"]

        # Test dangerous command with different operator
        result = await tool.execute(command="ls && rm -rf /")
        assert result["success"] is False
        assert "User denied execution" in result["error"]

        # Test case insensitivity
        result = await tool.execute(command="RM -RF /")
        assert result["success"] is False
        assert "User denied execution" in result["error"]

@pytest.mark.asyncio
async def test_terminal_tool_safe_command():
    tool = TerminalTool()

    # Mock asyncio.create_subprocess_shell
    with patch("asyncio.create_subprocess_shell") as mock_shell:
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"output", b"")
        mock_proc.returncode = 0
        mock_shell.return_value = mock_proc

        result = await tool.execute(command="ls -la")
        assert result["success"] is True
        assert result["stdout"] == "output"

@pytest.mark.asyncio
async def test_terminal_tool_bypass_attempt():
    tool = TerminalTool()

    # Mock ProposeActionTool to return approved=True if it's called (it shouldn't be for non-dangerous words)
    with patch("backend.tools.terminal.ProposeActionTool") as MockProposer:
        mock_proposer_instance = MockProposer.return_value
        mock_proposer_instance.execute = AsyncMock(return_value={"approved": True})

        # Mock asyncio.create_subprocess_shell for the actual execution
        with patch("asyncio.create_subprocess_shell") as mock_shell:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"fake output", b"")
            mock_proc.returncode = 0
            mock_shell.return_value = mock_proc

            # 'army' contains 'rm' but is not 'rm'
            result = await tool.execute(command="echo army")
            assert result["success"] is True
            # Proposer should NOT have been called
            assert MockProposer.call_count == 0
