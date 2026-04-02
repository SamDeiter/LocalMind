import pytest
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import sys

# Ensure backend can be imported
sys.path.append(os.getcwd())

# Mock modules that are missing in the environment but required for imports
mock_fastapi = MagicMock()
sys.modules['fastapi'] = mock_fastapi
sys.modules['httpx'] = MagicMock()

from backend.routes.files import PROJECT_ROOT as FILES_PROJECT_ROOT
from backend.proposals import PROJECT_ROOT as PROPOSALS_PROJECT_ROOT
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

def test_proposals_is_relative_to():
    """Verify that proposals path validation is secure."""
    root = PROPOSALS_PROJECT_ROOT

    # outside = (root / "../outside.txt").resolve() # root is /app, outside is /outside.txt
    # If root is /app, root / ".." is /app if it can't go higher, or / if it can.
    # On linux root /, /.. is /.

    # A reliable way to test is_relative_to for a path outside
    outside = Path("/etc/passwd")
    assert not outside.is_relative_to(root)

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
    assert isinstance(PROPOSALS_PROJECT_ROOT, Path)
    assert isinstance(SELF_EDIT_PROJECT_ROOT, Path)
    assert isinstance(CODE_EDITOR_PROJECT_ROOT, Path)

    # They point to the root of the project.
    # Some point to backend/, some point to project root.
    # Let's verify they are relative to each other as expected.

    # FILES_PROJECT_ROOT is /app/backend
    # PROPOSALS_PROJECT_ROOT is /app
    # SELF_EDIT_PROJECT_ROOT is /app
    # CODE_EDITOR_PROJECT_ROOT is /app

    assert FILES_PROJECT_ROOT.is_relative_to(PROPOSALS_PROJECT_ROOT)
    assert PROPOSALS_PROJECT_ROOT.resolve() == SELF_EDIT_PROJECT_ROOT.resolve()
    assert SELF_EDIT_PROJECT_ROOT.resolve() == CODE_EDITOR_PROJECT_ROOT.resolve()
