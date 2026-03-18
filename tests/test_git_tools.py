"""
Tests for git tools and project context tool.

Uses temporary directories with real git repos to test tool behavior.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helper: create a temp git repo ──────────────────────────────────

@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repo inside a fake workspace sandbox."""
    # Point the workspace to our temp dir
    import backend.tools.git_tools as gt
    monkeypatch.setattr(gt, "WORKSPACE", tmp_path)

    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True)

    # Create an initial commit so the repo isn't empty
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo), capture_output=True)

    return repo


@pytest.fixture
def temp_project_dir(tmp_path, monkeypatch):
    """Create a temporary project directory for context testing."""
    import backend.tools.project_context as pc
    monkeypatch.setattr(pc, "WORKSPACE", tmp_path)

    project = tmp_path / "my-project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("print('hello')\n")
    (project / "src" / "utils.py").write_text("def helper(): pass\n")
    (project / "tests").mkdir()
    (project / "tests" / "test_app.py").write_text("def test_it(): assert True\n")
    (project / "README.md").write_text("# My Project\n")
    (project / "requirements.txt").write_text("flask\n")

    # Noise dirs that should be skipped
    (project / ".git").mkdir()
    (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "express").mkdir()
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00")

    return project


# ── Git Status ──────────────────────────────────────────────────────

class TestGitStatus:
    @pytest.mark.asyncio
    async def test_git_status_clean(self, temp_git_repo):
        from backend.tools.git_tools import GitStatusTool
        tool = GitStatusTool()
        result = await tool.execute(repo_path="test-repo")
        assert result["success"] is True
        assert "clean" in result["result"].lower() or "no changes" in result["result"].lower()

    @pytest.mark.asyncio
    async def test_git_status_dirty(self, temp_git_repo):
        # Create an untracked file
        (temp_git_repo / "new_file.txt").write_text("hello\n")
        from backend.tools.git_tools import GitStatusTool
        tool = GitStatusTool()
        result = await tool.execute(repo_path="test-repo")
        assert result["success"] is True
        assert "new_file.txt" in result["result"]


# ── Git Log ─────────────────────────────────────────────────────────

class TestGitLog:
    @pytest.mark.asyncio
    async def test_git_log_shows_commits(self, temp_git_repo):
        from backend.tools.git_tools import GitLogTool
        tool = GitLogTool()
        result = await tool.execute(repo_path="test-repo")
        assert result["success"] is True
        assert "initial commit" in result["result"]

    @pytest.mark.asyncio
    async def test_git_log_respects_count(self, temp_git_repo):
        # Add a second commit
        (temp_git_repo / "file2.txt").write_text("content\n")
        subprocess.run(["git", "add", "-A"], cwd=str(temp_git_repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "second commit"], cwd=str(temp_git_repo), capture_output=True)

        from backend.tools.git_tools import GitLogTool
        tool = GitLogTool()
        result = await tool.execute(repo_path="test-repo", count=1)
        assert result["success"] is True
        assert "second commit" in result["result"]
        # Only 1 commit should show
        lines = [l for l in result["result"].strip().split("\n") if l.strip()]
        assert len(lines) == 1


# ── Git Diff ────────────────────────────────────────────────────────

class TestGitDiff:
    @pytest.mark.asyncio
    async def test_git_diff_shows_changes(self, temp_git_repo):
        # Modify a tracked file
        (temp_git_repo / "README.md").write_text("# Updated\nNew content\n")
        from backend.tools.git_tools import GitDiffTool
        tool = GitDiffTool()
        result = await tool.execute(repo_path="test-repo")
        assert result["success"] is True
        assert "Updated" in result["result"] or "New content" in result["result"]

    @pytest.mark.asyncio
    async def test_git_diff_no_changes(self, temp_git_repo):
        from backend.tools.git_tools import GitDiffTool
        tool = GitDiffTool()
        result = await tool.execute(repo_path="test-repo")
        assert result["success"] is True
        assert "no" in result["result"].lower()


# ── Git Commit ──────────────────────────────────────────────────────

class TestGitCommit:
    @pytest.mark.asyncio
    async def test_git_commit_creates_commit(self, temp_git_repo):
        # Create a new file and commit it via the tool
        (temp_git_repo / "new_feature.py").write_text("print('feature')\n")
        from backend.tools.git_tools import GitCommitTool
        tool = GitCommitTool()
        result = await tool.execute(repo_path="test-repo", message="add new feature")
        assert result["success"] is True
        assert "add new feature" in result["result"]

        # Verify it shows in git log
        log = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=str(temp_git_repo), capture_output=True, text=True
        )
        assert "add new feature" in log.stdout

    @pytest.mark.asyncio
    async def test_git_commit_empty_message_rejected(self, temp_git_repo):
        from backend.tools.git_tools import GitCommitTool
        tool = GitCommitTool()
        result = await tool.execute(repo_path="test-repo", message="")
        assert result["success"] is False
        assert "empty" in result["error"].lower()


# ── Path Security ───────────────────────────────────────────────────

class TestGitPathSecurity:
    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, temp_git_repo):
        from backend.tools.git_tools import GitStatusTool
        tool = GitStatusTool()
        result = await tool.execute(repo_path="../../etc")
        assert result["success"] is False
        assert "escapes" in result["error"].lower() or "not a" in result["error"].lower()


# ── Project Context ─────────────────────────────────────────────────

class TestProjectContext:
    @pytest.mark.asyncio
    async def test_project_context_tree(self, temp_project_dir):
        from backend.tools.project_context import ProjectContextTool
        tool = ProjectContextTool()
        result = await tool.execute(path="my-project")
        assert result["success"] is True
        # Should contain real files
        assert "app.py" in result["result"]
        assert "README.md" in result["result"]
        assert "requirements.txt" in result["result"]

    @pytest.mark.asyncio
    async def test_project_context_skips_ignored(self, temp_project_dir):
        from backend.tools.project_context import ProjectContextTool
        tool = ProjectContextTool()
        result = await tool.execute(path="my-project")
        assert result["success"] is True
        # Noise directories should NOT appear
        assert "node_modules" not in result["result"]
        assert "__pycache__" not in result["result"]
        # .git directory should NOT appear (it's in SKIP_DIRS)
        assert ".git/" not in result["result"]

    @pytest.mark.asyncio
    async def test_project_context_path_traversal(self, temp_project_dir):
        from backend.tools.project_context import ProjectContextTool
        tool = ProjectContextTool()
        result = await tool.execute(path="../../etc")
        assert result["success"] is False
