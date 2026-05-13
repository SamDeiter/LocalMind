import pytest
from pathlib import Path
from backend.security.rbac import _is_allowed
from backend.security.prompt_guard import PromptGuard

def test_rbac_prefix_bypass():
    # operator is allowed GET on /api/
    assert _is_allowed("operator", "GET", "/api/jobs") is True
    assert _is_allowed("operator", "GET", "/api/chat") is True

    # operator is allowed POST on /api/chat
    assert _is_allowed("operator", "POST", "/api/chat") is True
    assert _is_allowed("operator", "POST", "/api/chat/send") is True

    # SECURITY BYPASS TEST: /api/chat_admin should NOT be allowed if only /api/chat is allowed
    # (assuming /api/chat_admin is not in the allowed list)
    assert _is_allowed("operator", "POST", "/api/chat_admin") is False
    assert _is_allowed("operator", "POST", "/api/chat-service") is False

def test_prompt_guard_path_jailing_bypass(tmp_path):
    guard = PromptGuard("strict")
    job_dir = tmp_path / "job_1"
    job_dir.mkdir()

    evil_job_dir = tmp_path / "job_10"
    evil_job_dir.mkdir()
    evil_file = evil_job_dir / "secret.txt"
    evil_file.write_text("evil")

    # tool call with path escaping via prefix bypass
    call = {
        "name": "read_file",
        "args": {"path": str(evil_file)}
    }

    # The check should catch that /tmp/job_10 is not in /tmp/job_1
    result = guard.validate_tool_call(call, ["read_file"], job_dir)
    assert result.valid is False
    assert any("escapes job directory" in issue for issue in result.issues)

def test_prompt_guard_relative_to_logic(tmp_path):
    # Test the logic I added to validate_tool_call
    # Using relative_to ensures that /tmp/job_10 is not considered inside /tmp/job_1
    # even if it starts with the same string prefix.

    job_dir = Path("/tmp/job_1")
    evil_path = Path("/tmp/job_10/secret.txt")

    # Prefix check would pass
    assert str(evil_path).startswith(str(job_dir)) is True

    # relative_to check should fail
    with pytest.raises(ValueError):
        evil_path.relative_to(job_dir)
