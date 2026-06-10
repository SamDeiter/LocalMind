import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from backend.security.prompt_guard import PromptGuard

def test_prompt_guard_path_jailing_bypass():
    guard = PromptGuard(level="strict")
    job_dir = Path("/app/workspace")

    call = {
        "name": "read_file",
        "args": {"path": "/app/workspace_evil/secret.txt"}
    }

    with patch("backend.security.prompt_guard.safe_resolve") as mock_resolve:
        # safe_resolve returns the malicious path
        mock_resolve.return_value = Path("/app/workspace_evil/secret.txt")

        # We need to patch resolve() on Path to return self to avoid real filesystem access
        # The issue with mock.patch.object(Path, 'resolve') is that it affects all Path instances
        # and might break internal Path behavior if not careful.
        with patch("pathlib.Path.resolve", autospec=True) as mock_res:
            mock_res.side_effect = lambda self: self
            result = guard.validate_tool_call(call, ["read_file"], job_dir)

            assert result.valid is False
            assert any("escapes job directory" in issue for issue in result.issues)

def test_prompt_guard_path_jailing_valid():
    guard = PromptGuard(level="strict")
    job_dir = Path("/app/workspace")

    call = {
        "name": "read_file",
        "args": {"path": "safe.txt"}
    }

    with patch("backend.security.prompt_guard.safe_resolve") as mock_resolve:
        mock_resolve.return_value = Path("/app/workspace/safe.txt")

        with patch("pathlib.Path.resolve", autospec=True) as mock_res:
            mock_res.side_effect = lambda self: self
            result = guard.validate_tool_call(call, ["read_file"], job_dir)
            assert result.valid is True

def test_prompt_guard_path_jailing_exact():
    guard = PromptGuard(level="strict")
    job_dir = Path("/app/workspace")

    call = {
        "name": "read_file",
        "args": {"path": "."}
    }

    with patch("backend.security.prompt_guard.safe_resolve") as mock_resolve:
        mock_resolve.return_value = Path("/app/workspace")

        with patch("pathlib.Path.resolve", autospec=True) as mock_res:
            mock_res.side_effect = lambda self: self
            result = guard.validate_tool_call(call, ["read_file"], job_dir)
            assert result.valid is True
