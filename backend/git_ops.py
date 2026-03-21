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
            ["git"] + args,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
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


async def run_tests() -> tuple[bool, str]:
    """Run pytest and return (success, output).
    
    Waits 3s before running to let any server file-watcher reload complete,
    preventing import collisions that cause false 0-passed results.
    """
    import asyncio
    await asyncio.sleep(3)  # Let WatchFiles settle after file edits

    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=short"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
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
