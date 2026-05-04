"""
git_ops.py — Git operations and test runner for LocalMind Autonomy Engine
=========================================================================
Extracted from autonomy.py to keep files lean and editable.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.git")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def git_run(args: list[str]) -> str:
    """Run a git command in the project root. Returns stdout."""
    try:
        result = subprocess.run(
            f'cmd /c "git {" ".join(args)}"',
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            shell=True,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or f"git exited with code {result.returncode}"
            logger.warning(f"Git command failed: git {' '.join(args)} → {error}")
            return ""
        return result.stdout.strip()
    except Exception as exc:
        logger.warning(f"Git command error: {exc}")
        return ""


def revert_file(relative_path: str):
    """Restore a file from its .bak backup."""
    target = (PROJECT_ROOT / relative_path).resolve()
    backup = target.with_suffix(target.suffix + ".bak")

    if backup.exists():
        shutil.copy2(backup, target)
        backup.unlink()
        logger.info(f"Reverted: {relative_path}")
    else:
        logger.warning(f"No backup found for: {relative_path}")


async def run_tests(target_files: list[str] = None) -> tuple[bool, str]:
    """Run pytest and return (success, output).
    
    By default runs only fast smoke tests (server + core) to avoid
    the full 170s+ suite timing out every execution.
    
    Args:
        target_files: Optional list of changed file paths to scope tests.
    """
    import asyncio
    await asyncio.sleep(3)  # Let WatchFiles settle after file edits

    # Build a targeted test command based on what files were changed.
    # If we changed backend/foo.py, try tests/test_foo.py first.
    test_targets = []
    if target_files:
        for f in target_files:
            name = Path(f).stem
            candidate = PROJECT_ROOT / "tests" / f"test_{name}.py"
            if candidate.exists():
                test_targets.append(str(candidate))

    # Fallback: run a fast subset — server health + core logic only
    if not test_targets:
        fast_tests = PROJECT_ROOT / "tests" / "test_server.py"
        if fast_tests.exists():
            test_targets = [str(fast_tests)]
        else:
            test_targets = ["tests/"]

    try:
        test_args = " ".join(test_targets + ["-q", "--tb=short", "-x"])
        result = subprocess.run(
            f'cmd /c "python -m pytest {test_args}"',
            capture_output=True, text=True, timeout=180,
            cwd=str(PROJECT_ROOT),
            shell=True,
        )

        output = result.stdout.strip() or result.stderr.strip()
        if not output:
            output = "No test output captured."

        # Parse "83 passed in 10.11s"
        passed = failed = 0
        for line in output.splitlines():
            if "passed" in line:
                m = re.search(r"(\d+) passed", line)
                if m:
                    passed = int(m.group(1))
                m = re.search(r"(\d+) failed", line)
                if m:
                    failed = int(m.group(1))

        success = result.returncode == 0 or result.returncode == 5
        if result.returncode == 5:
            logger.info("No tests collected — treating as pass")
        
        logger.info(f"Auto-test: {passed} passed, {failed} failed")
        return success, output

    except Exception as exc:
        logger.warning(f"Auto-test failed: {exc}")
        return False, str(exc)


def count_tests() -> int:
    """Quickly count total tests without running them.
    
    Uses --collect-only for speed. Returns 0 on error.
    """
    try:
        result = subprocess.run(
            'cmd /c "python -m pytest tests/ --collect-only -q"',
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
            shell=True,
        )
        # Output ends with "X tests collected"
        for line in result.stdout.splitlines():
            m = re.search(r"(\d+)\s+test", line)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return 0


def get_merge_commit(branch_name: str) -> str | None:
    """Find the merge commit SHA for a branch merge."""
    output = git_run(["log", "--oneline", "--merges", "-20"])
    for line in output.splitlines():
        if branch_name in line or "autonomy" in line.lower():
            sha = line.split()[0] if line else None
            return sha
    return None


def revert_merge(merge_sha: str) -> bool:
    """Revert a merge commit on main."""
    result = git_run(["revert", "--no-commit", "-m", "1", merge_sha])
    if result is not None:
        git_run(["commit", "-m", f"Revert autonomy merge {merge_sha}"])
        logger.info(f"Reverted merge: {merge_sha}")
        return True
    return False


# ── Git Awareness Tools (Phase D) ────────────────────────────────────────────

def git_status() -> dict:
    """Return the current working tree status as structured data.
    
    Returns a dict with:
      - branch: current branch name
      - staged: list of staged file paths
      - unstaged: list of unstaged modified file paths
      - untracked: list of untracked file paths
      - is_clean: bool
    """
    raw = git_run(["status", "--porcelain=v1", "--branch"])
    branch = ""
    staged, unstaged, untracked = [], [], []

    for line in raw.splitlines():
        if line.startswith("## "):
            # ## main...origin/main [ahead 1]
            branch_part = line[3:].split("...")[0].split(" ")[0]
            branch = branch_part
            continue
        if len(line) < 3:
            continue
        xy = line[:2]
        path = line[3:].strip()
        # Index (staged) status
        if xy[0] in ("M", "A", "D", "R", "C"):
            staged.append(path)
        # Working tree (unstaged) status
        if xy[1] in ("M", "D"):
            unstaged.append(path)
        # Untracked
        if xy == "??":
            untracked.append(path)

    return {
        "branch": branch,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "is_clean": not staged and not unstaged and not untracked,
    }


def git_diff_staged() -> str:
    """Return the diff for all staged changes (what would be committed).
    
    Caps output at 8000 chars to avoid overwhelming LLM context.
    """
    diff = git_run(["diff", "--cached"])
    if len(diff) > 8000:
        diff = diff[:8000] + "\n... [diff truncated at 8000 chars]"
    return diff or "(no staged changes)"


def git_log_short(n: int = 20) -> list[dict]:
    """Return the last N commits as a list of dicts.
    
    Each dict has: sha (short), message, author, date_relative
    """
    raw = git_run([
        "log", f"-{n}",
        "--pretty=format:%h|||%s|||%an|||%ar"
    ])
    commits = []
    for line in raw.splitlines():
        parts = line.split("|||")
        if len(parts) == 4:
            commits.append({
                "sha": parts[0],
                "message": parts[1],
                "author": parts[2],
                "date_relative": parts[3],
            })
    return commits


def git_commit_staged(message: str) -> dict:
    """Commit all currently staged files with the given message.
    
    Returns dict with: success, sha, message, error
    IMPORTANT: Callers should present staged filelist to user before calling.
    """
    if not message or not message.strip():
        return {"success": False, "error": "Commit message cannot be empty", "sha": "", "message": ""}
    
    result_raw = git_run(["commit", "-m", f"{message}"])
    if result_raw:
        # Extract SHA from "main abc1234 message" or "[branch abc1234] message"
        sha_match = __import__("re").search(r"[\[\s]([0-9a-f]{7})[\]\s]", result_raw)
        sha = sha_match.group(1) if sha_match else ""
        return {"success": True, "sha": sha, "message": message, "error": ""}
    return {"success": False, "sha": "", "message": message, "error": "git commit returned no output — check git status"}
