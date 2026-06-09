
import pytest
from pathlib import Path
from backend.security.prompt_guard import PromptGuard

def test_prompt_guard_path_jailing():
    guard = PromptGuard(level="strict")
    job_dir = Path("/tmp/job_123")

    # Mock tool call with path escape
    call = {
        "name": "read_file",
        "args": {"path": "../../etc/passwd"}
    }

    # We need to make sure job_dir exists for resolve() if paths.py is used
    job_dir.mkdir(parents=True, exist_ok=True)

    result = guard.validate_tool_call(
        call=call,
        allowed_tools=["read_file"],
        job_dir=job_dir
    )

    assert result.valid is False
    assert any("escapes" in issue or "resolution failed" in issue for issue in result.issues)

def test_prompt_guard_path_valid():
    guard = PromptGuard(level="strict")
    job_dir = Path("/tmp/job_123")
    job_dir.mkdir(parents=True, exist_ok=True)

    call = {
        "name": "read_file",
        "args": {"path": "data.txt"}
    }

    result = guard.validate_tool_call(
        call=call,
        allowed_tools=["read_file"],
        job_dir=job_dir
    )

    assert result.valid is True
    assert len(result.issues) == 0
