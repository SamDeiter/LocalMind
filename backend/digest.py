"""
digest.py — Daily digest for autonomy engine activity
======================================================
Generates a markdown summary of what the engine did in the last 24h.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("localmind.autonomy.digest")

PROPOSALS_DIR = Path(__file__).resolve().parent.parent / "data" / "proposals"
ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "data" / "proposals" / "archive"
DIGESTS_DIR = Path(__file__).resolve().parent.parent / "data" / "digests"


def generate_digest(hours: int = 24) -> str:
    """Generate a markdown summary of autonomy activity.

    Args:
        hours: How far back to look (default 24h).

    Returns:
        Markdown-formatted digest string.
    """
    DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - (hours * 3600)

    # Gather proposals from active + archive dirs
    proposals = []
    for directory in (PROPOSALS_DIR, ARCHIVE_DIR):
        if not directory or not directory.exists():
            continue
        for f in directory.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                created = data.get("created_at", 0)
                finished = data.get("execution_finished_at", 0)
                if created > cutoff or finished > cutoff:
                    proposals.append(data)
            except (json.JSONDecodeError, OSError):
                continue

    if not proposals:
        return "# Daily Digest\n\nNo autonomy activity in the last 24 hours.\n"

    # Group by status
    completed = [p for p in proposals if p.get("status") == "completed"]
    failed = [p for p in proposals if p.get("status") == "failed"]
    skipped = [p for p in proposals if p.get("status") == "skipped"]
    pending = [p for p in proposals if p.get("status") in ("proposed", "approved")]

    lines = [
        f"# Daily Digest — {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**Period**: Last {hours} hours",
        f"**Total Activity**: {len(proposals)} proposals",
        "",
    ]

    if completed:
        lines.append(f"## ✅ Completed ({len(completed)})")
        for p in completed:
            files = ", ".join(p.get("files_edited", [])[:3])
            lines.append(f"- **{p['title']}** [{p.get('category', '?')}] — {files}")
        lines.append("")

    if failed:
        lines.append(f"## ❌ Failed ({len(failed)})")
        for p in failed:
            error = p.get("error", "Unknown")[:80]
            lines.append(f"- **{p['title']}** — {error}")
        lines.append("")

    if skipped:
        lines.append(f"## ⏭️ Skipped ({len(skipped)})")
        for p in skipped:
            lines.append(f"- {p['title']}")
        lines.append("")

    if pending:
        lines.append(f"## ⏳ Pending ({len(pending)})")
        for p in pending:
            lines.append(f"- {p['title']} [{p.get('status')}]")
        lines.append("")

    # Success rate
    total_executed = len(completed) + len(failed)
    if total_executed > 0:
        rate = len(completed) / total_executed * 100
        lines.append(f"**Success Rate**: {rate:.0f}% ({len(completed)}/{total_executed})")

    digest_text = "\n".join(lines)

    # Save to file
    filename = time.strftime("%Y-%m-%d") + ".md"
    (DIGESTS_DIR / filename).write_text(digest_text, encoding="utf-8")
    logger.info(f"Daily digest saved: {filename}")

    return digest_text


def get_latest_digest() -> str:
    """Return the most recent digest, or generate one if none exists."""
    if not DIGESTS_DIR.exists():
        return generate_digest()

    files = sorted(DIGESTS_DIR.glob("*.md"), reverse=True)
    if files:
        return files[0].read_text(encoding="utf-8")
    return generate_digest()


def get_digest_by_date(date_str: str) -> str | None:
    """Return a digest for a specific date (YYYY-MM-DD format)."""
    target = DIGESTS_DIR / f"{date_str}.md"
    if target.exists():
        return target.read_text(encoding="utf-8")
    return None
