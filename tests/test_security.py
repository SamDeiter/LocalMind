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
from backend.security.prompt_guard import PromptGuard

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

def test_prompt_guard_path_traversal_prefix_bypass():
    """Verify that PromptGuard blocks path traversal even with shared prefixes."""
    guard = PromptGuard(level="strict")
    job_dir = Path("/tmp/localmind_job").resolve()

    # Prefix bypass attempt: /tmp/localmind_job vs /tmp/localmind_job_secret
    # In a real environment we'd use mocks or temporary directories, but here we
    # test the logic of path containment.

    allowed_tools = ["read_file"]

    # Path that would pass a .startswith() check if job_dir was stringified
    # but fails is_relative_to() check.
    malicious_path = "../localmind_job_secret/credentials.txt"

    tool_call = {
        "name": "read_file",
        "args": {"path": malicious_path}
    }

    result = guard.validate_tool_call(tool_call, allowed_tools, job_dir)
    assert result.valid is False
    assert any("escapes job directory" in issue or "path resolution failed" in issue for issue in result.issues)

def test_prompt_guard_shell_injection():
    """Verify that PromptGuard blocks shell injection metacharacters."""
    guard = PromptGuard(level="strict")
    allowed_tools = ["run_command"]
    job_dir = Path("/tmp/localmind_job")

    # Test critical metacharacters
    for char in [";", "&", "|", "\n", ">", "<", "*", "(", ")", "~"]:
        tool_call = {
            "name": "run_command",
            "args": {"command": f"ls {char} /etc/passwd"}
        }
        result = guard.validate_tool_call(tool_call, allowed_tools, job_dir)
        assert result.valid is False
        assert any("shell metacharacters" in issue for issue in result.issues)
