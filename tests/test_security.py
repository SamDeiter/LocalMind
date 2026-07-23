import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

# Ensure backend can be imported
sys.path.append(os.getcwd())

# Mock modules that are missing in the environment but required for imports
# Only mock if the real package isn't installed (avoids corrupting starlette/httpx)
for _mock_mod in ("fastapi", "httpx"):
    if _mock_mod not in sys.modules:
        try:
            __import__(_mock_mod)
        except ImportError:
            sys.modules[_mock_mod] = MagicMock()

from backend.routes.files import PROJECT_ROOT as FILES_PROJECT_ROOT
from backend.tools.self_edit import PROJECT_ROOT as SELF_EDIT_PROJECT_ROOT, _validate_self_path
from backend.code_editor import PROJECT_ROOT as CODE_EDITOR_PROJECT_ROOT, is_protected_file

def test_files_project_root_type():
    """Verify that FILES_PROJECT_ROOT is now a Path object."""
    assert isinstance(FILES_PROJECT_ROOT, Path)

def test_files_api_traversal_bypass():
    """Verify that path traversal is blocked in files API logic."""
    # Current PROJECT_ROOT (as a Path)
    root = FILES_PROJECT_ROOT

    # A path that is outside root
    # Note: we use resolve() in the actual code, so we test that behavior.
    outside = (root / "../../etc/passwd").resolve()
    assert not outside.is_relative_to(root)

    # A path that shares a prefix but is outside (the core issue)
    fake_root = Path("/app/backend")
    fake_secret = Path("/app/backend_secret")
    assert not fake_secret.is_relative_to(fake_root)

def test_self_edit_validate_self_path_traversal():
    """Verify that self_edit tool blocks path traversal."""
    bypass_path = "../../outside.txt"
    with pytest.raises(ValueError, match="Path escapes project"):
        _validate_self_path(bypass_path)

def test_code_editor_is_protected_file_traversal():
    """Verify that code_editor blocks protected file access via traversal."""
    bypass_path = "../../outside.txt"
    assert is_protected_file(bypass_path) is True

def test_project_root_consistency():
    """Verify all PROJECT_ROOTs are Path objects and point to the same place."""
    assert isinstance(FILES_PROJECT_ROOT, Path)
    assert isinstance(SELF_EDIT_PROJECT_ROOT, Path)
    assert isinstance(CODE_EDITOR_PROJECT_ROOT, Path)

    # All PROJECT_ROOTs should point to the same project root
    assert FILES_PROJECT_ROOT.resolve() == SELF_EDIT_PROJECT_ROOT.resolve()
    assert SELF_EDIT_PROJECT_ROOT.resolve() == CODE_EDITOR_PROJECT_ROOT.resolve()

def test_prompt_guard_validate_tool_call_path_jailing(tmp_path):
    """Verify that PromptGuard's validate_tool_call correctly validates tool path arguments and blocks traversal."""
    from backend.security.prompt_guard import PromptGuard
    guard = PromptGuard(level="strict")

    # Define a clean job directory
    job_dir = tmp_path / "job_123"
    job_dir.mkdir()

    # Define a prefix-collision fake job directory (not inside job_dir)
    job_dir_evil = tmp_path / "job_123_evil"
    job_dir_evil.mkdir()

    # 1. Valid tool call with a path inside job_dir
    valid_file = job_dir / "valid.txt"
    valid_file.write_text("hello")
    call_valid = {
        "name": "read_file",
        "args": {"filepath": str(valid_file)}
    }
    result = guard.validate_tool_call(call_valid, ["read_file"], job_dir)
    assert result.valid is True, f"Valid path was incorrectly blocked: {result.issues}"

    # 2. Blocked traversal with prefix collision (e.g. ../job_123_evil/secret.txt against /path/to/job_123)
    evil_file = job_dir_evil / "secret.txt"
    evil_file.write_text("evil")
    call_evil = {
        "name": "read_file",
        "args": {"filepath": "../job_123_evil/secret.txt"}
    }
    result = guard.validate_tool_call(call_evil, ["read_file"], job_dir)
    assert result.valid is False, "Prefix collision traversal bypass was not blocked!"
    assert any("escapes job directory" in issue or "path resolution failed" in issue or "escapes the jail" in issue for issue in result.issues)

    # 3. Blocked standard traversal with relative path ../../passwd
    call_traversal = {
        "name": "read_file",
        "args": {"filepath": "../../../etc/passwd"}
    }
    result = guard.validate_tool_call(call_traversal, ["read_file"], job_dir)
    assert result.valid is False, "Relative path traversal was not blocked!"
    assert any("escapes job directory" in issue or "path resolution failed" in issue for issue in result.issues)
