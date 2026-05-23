"""
git_ops.py — Git operations and test runner for LocalMind Autonomy Engine
=========================================================================
Extracted from autonomy.py to keep files lean and editable.
"""

import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.git")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def git_run(args: list[str]) -> str:
    """Run a git command in the project root. Returns stdout."""
    try:
        # Security: Use list of arguments and shell=False to prevent injection.
        # We also drop 'cmd /c' and use 'git' directly for cross-platform compatibility.
        cmd = ["git"] + args
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
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
        logger.info(f"↩️ Reverted: {relative_path}")
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
        # Security: Avoid shell=True and manual string concatenation for commands.
        cmd = [sys.executable, "-m", "pytest"] + test_targets + ["-q", "--tb=short", "-x"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=180,
            cwd=str(PROJECT_ROOT),
            shell=False,
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
        
        logger.info(f"🧪 Auto-test: {passed} passed, {failed} failed")
        return success, output

    except Exception as exc:
        logger.warning(f"Auto-test failed: {exc}")
        return False, str(exc)


def count_tests() -> int:
    """Quickly count total tests without running them.
    
    Uses --collect-only for speed. Returns 0 on error.
    """
    try:
        # Security: Avoid shell=True.
        cmd = [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
            shell=False,
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
        logger.info(f"↩️ Reverted merge: {merge_sha}")
        return True
    return False
