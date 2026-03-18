"""
Git Tools — Check status, view diffs, read logs, and commit changes
in git repositories within the sandboxed workspace.

Safety:
- All paths validated against ~/LocalMind_Workspace sandbox
- subprocess.run with explicit args (no shell=True)
- 30-second timeout on all git commands
- git_commit does NOT push — user must push manually
"""

import subprocess
from pathlib import Path

from .base import BaseTool

# Sandbox: same workspace as file tools
WORKSPACE = Path.home() / "LocalMind_Workspace"


def _validate_repo_path(repo_path: str) -> Path:
    """Validate a repo path stays within the workspace sandbox.

    Raises ValueError if the path escapes the sandbox.
    """
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    target = (WORKSPACE / repo_path).resolve()

    if not str(target).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path escapes sandbox: {repo_path}")

    if not target.is_dir():
        raise ValueError(f"Not a directory: {repo_path}")

    # Verify it's actually a git repo
    git_dir = target / ".git"
    if not git_dir.is_dir():
        raise ValueError(f"Not a git repository: {repo_path}")

    return target


def _run_git(args: list[str], cwd: Path) -> dict:
    """Run a git command and return structured result.

    Args:
        args: Git command arguments (e.g., ['status', '--porcelain'])
        cwd: Working directory for the command

    Returns:
        dict with success, result (stdout), and optionally error (stderr)
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr.strip() or f"git exited with code {result.returncode}",
            }

        return {
            "success": True,
            "result": result.stdout.strip() or "(no output)",
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Git command timed out (30s limit)"}
    except FileNotFoundError:
        return {"success": False, "error": "git is not installed or not on PATH"}
    except Exception as exc:
        return {"success": False, "error": f"Git command failed: {exc}"}


class GitStatusTool(BaseTool):
    """Show the current branch and working tree status."""

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return (
            "Show the git status of a repository in the workspace. "
            "Returns the current branch and list of changed files. "
            "Path is relative to ~/LocalMind_Workspace."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Relative path to the git repository (e.g., 'my-project')",
                }
            },
            "required": ["repo_path"],
        }

    async def execute(self, repo_path: str = "", **kwargs) -> dict:
        try:
            repo = _validate_repo_path(repo_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        # Get current branch
        branch_result = _run_git(["branch", "--show-current"], repo)
        branch = branch_result.get("result", "unknown") if branch_result["success"] else "unknown"

        # Get status
        status_result = _run_git(["status", "--porcelain"], repo)
        if not status_result["success"]:
            return status_result

        status_text = status_result["result"]
        if status_text == "(no output)":
            status_text = "Working tree clean — no changes"

        return {
            "success": True,
            "result": f"Branch: {branch}\n\n{status_text}",
        }


class GitDiffTool(BaseTool):
    """Show the diff of uncommitted changes."""

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return (
            "Show the git diff of a repository — what has changed but not yet been committed. "
            "Can show staged or unstaged changes. Path is relative to ~/LocalMind_Workspace."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Relative path to the git repository",
                },
                "staged": {
                    "type": "boolean",
                    "description": "If true, show only staged changes (--cached). Default: false (unstaged)",
                    "default": False,
                },
            },
            "required": ["repo_path"],
        }

    async def execute(self, repo_path: str = "", staged: bool = False, **kwargs) -> dict:
        try:
            repo = _validate_repo_path(repo_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        args = ["diff"]
        if staged:
            args.append("--cached")

        result = _run_git(args, repo)
        if result["success"] and result["result"] == "(no output)":
            label = "staged" if staged else "unstaged"
            result["result"] = f"No {label} changes."

        return result


class GitLogTool(BaseTool):
    """Show recent commit history."""

    @property
    def name(self) -> str:
        return "git_log"

    @property
    def description(self) -> str:
        return (
            "Show the recent git commit history of a repository. "
            "Returns a concise one-line-per-commit log. "
            "Path is relative to ~/LocalMind_Workspace."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Relative path to the git repository",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of commits to show (default: 10, max: 50)",
                    "default": 10,
                },
            },
            "required": ["repo_path"],
        }

    async def execute(self, repo_path: str = "", count: int = 10, **kwargs) -> dict:
        try:
            repo = _validate_repo_path(repo_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        # Clamp count
        count = max(1, min(50, count))

        return _run_git(
            ["log", "--oneline", "--decorate", f"-n{count}"],
            repo,
        )


class GitCommitTool(BaseTool):
    """Stage all changes and commit with a message."""

    @property
    def name(self) -> str:
        return "git_commit"

    @property
    def description(self) -> str:
        return (
            "Stage all changes and create a git commit in a workspace repository. "
            "This does NOT push — the user must push manually. "
            "Path is relative to ~/LocalMind_Workspace."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo_path": {
                    "type": "string",
                    "description": "Relative path to the git repository",
                },
                "message": {
                    "type": "string",
                    "description": "The commit message describing what changed",
                },
            },
            "required": ["repo_path", "message"],
        }

    async def execute(self, repo_path: str = "", message: str = "", **kwargs) -> dict:
        if not message.strip():
            return {"success": False, "error": "Commit message cannot be empty"}

        try:
            repo = _validate_repo_path(repo_path)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        # Stage all changes
        stage_result = _run_git(["add", "-A"], repo)
        if not stage_result["success"]:
            return stage_result

        # Commit
        commit_result = _run_git(["commit", "-m", message], repo)
        if not commit_result["success"]:
            # "nothing to commit" is not really an error
            if "nothing to commit" in commit_result.get("error", ""):
                return {"success": True, "result": "Nothing to commit — working tree clean."}
            return commit_result

        return {
            "success": True,
            "result": f"Committed: {message}\n\n{commit_result['result']}",
        }
