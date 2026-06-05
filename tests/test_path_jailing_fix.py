
import pytest
from pathlib import Path
from backend.security.prompt_guard import PromptGuard

def test_path_jailing_traversal_blocked():
    """Verify that path traversal attempts are blocked in validate_tool_call."""
    guard = PromptGuard(level="strict")
    job_dir = Path("/app/jobs/job_123").resolve()

    # Payload trying to escape job_dir
    payload = {
        "name": "read_file",
        "args": {"path": "../../etc/passwd"}
    }

    result = guard.validate_tool_call(payload, allowed_tools=["read_file"], job_dir=job_dir)

    assert not result.valid
    assert any("escapes jail" in issue for issue in result.issues)

def test_path_jailing_prefix_bypass_blocked():
    """Verify that prefix-based jailing bypasses are blocked (segment-aware)."""
    guard = PromptGuard(level="strict")
    # Base directory
    job_dir = Path("/app/jobs/job_1").resolve()

    # "job_1_evil" starts with "job_1" but is a different directory
    payload = {
        "name": "read_file",
        "args": {"path": "../job_1_evil/secret.txt"}
    }

    result = guard.validate_tool_call(payload, allowed_tools=["read_file"], job_dir=job_dir)

    assert not result.valid
    assert any("escapes jail" in issue for issue in result.issues)

def test_path_jailing_absolute_path_stripped_stays_in_jail():
    """Verify that absolute paths are stripped and kept inside the jail."""
    guard = PromptGuard(level="strict")
    job_dir = Path("/tmp/localmind_test_job_abs").resolve()
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Create a file that WOULD be hit if /etc/passwd was stripped to etc/passwd
        # inside the jail.
        jail_etc = job_dir / "etc"
        jail_etc.mkdir()
        jail_passwd = jail_etc / "passwd"
        jail_passwd.write_text("jailed")

        payload = {
            "name": "read_file",
            "args": {"path": "/etc/passwd"}
        }

        result = guard.validate_tool_call(payload, allowed_tools=["read_file"], job_dir=job_dir)

        # Current implementation strips leading slash and resolves it relative to job_dir.
        # This is considered valid and safe since it stays in jail.
        assert result.valid
        assert not result.issues

    finally:
        import shutil
        if job_dir.exists():
            shutil.rmtree(job_dir)

def test_path_jailing_valid_path_allowed():
    """Verify that valid paths inside the jail are allowed."""
    guard = PromptGuard(level="strict")
    job_dir = Path("/tmp/localmind_test_job").resolve()
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        valid_file = job_dir / "data.txt"
        valid_file.write_text("hello")

        payload = {
            "name": "read_file",
            "args": {"path": "data.txt"}
        }

        result = guard.validate_tool_call(payload, allowed_tools=["read_file"], job_dir=job_dir)

        assert result.valid
        assert not result.issues
    finally:
        import shutil
        if job_dir.exists():
            shutil.rmtree(job_dir)
