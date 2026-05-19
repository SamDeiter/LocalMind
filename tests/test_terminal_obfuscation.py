import pytest
from unittest.mock import AsyncMock, patch
from backend.tools.terminal import TerminalTool

@pytest.mark.asyncio
async def test_terminal_ansi_obfuscation_bypass():
    tool = TerminalTool()
    # "rm" with ANSI color codes: \x1b[31mrm\x1b[0m
    command = "echo \x1b[31mrm\x1b[0m -rf /"

    with patch("backend.tools.terminal.ProposeActionTool") as MockProposer:
        mock_proposer_instance = MockProposer.return_value
        mock_proposer_instance.execute = AsyncMock(return_value={"approved": False})

        # If the bypass works, it won't call the Proposer and will try to execute
        with patch("asyncio.create_subprocess_shell") as mock_shell:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            mock_shell.return_value = mock_proc

            await tool.execute(command=command)

            # If it reached create_subprocess_shell without ProposeActionTool, it's a bypass
            assert MockProposer.call_count > 0, "Security bypass: ANSI escape codes hidden dangerous command"

@pytest.mark.asyncio
async def test_terminal_backslash_obfuscation_bypass():
    tool = TerminalTool()
    # r\m is interpreted as rm by many shells
    command = "r\m -rf /"

    with patch("backend.tools.terminal.ProposeActionTool") as MockProposer:
        mock_proposer_instance = MockProposer.return_value
        mock_proposer_instance.execute = AsyncMock(return_value={"approved": False})

        with patch("asyncio.create_subprocess_shell") as mock_shell:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            mock_shell.return_value = mock_proc

            await tool.execute(command=command)

            assert MockProposer.call_count > 0, "Security bypass: Backslash hidden dangerous command"
