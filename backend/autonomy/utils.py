
import logging
import json
import time
import subprocess
from pathlib import Path
from .config import LOG_FILE

logger = logging.getLogger("localmind.autonomy.utils")

def log_event(event: str, data: dict = None):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            **(data or {}),
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning(f"Failed to write autonomy log: {exc}")

def sample_code_snippets(real_files: list[str], count: int = 3) -> str:
    import random
    project_root = Path(__file__).parent.parent.parent
    code_exts = {".py", ".js", ".html", ".css"}
    candidates = [f for f in real_files if Path(f).suffix in code_exts]
    if not candidates:
        return ""

    weights = []
    try:
        from backend.todo_harvester import harvest_todos
        todo_files = {t["file"] for t in harvest_todos(str(project_root))}
    except Exception:
        todo_files = set()

    try:
        result = subprocess.run(
            ["git", "log", "--diff-filter=M", "-5", "--name-only", "--format="],
            capture_output=True, text=True, cwd=str(project_root), timeout=5
        )
        recent_files = {l.strip() for l in result.stdout.splitlines() if l.strip()}
    except Exception:
        recent_files = set()

    for f in candidates:
        w = 1
        if f in todo_files: w += 2
        if f in recent_files: w += 2
        weights.append(w)

    sampled = []
    pop = list(candidates)
    w_pop = list(weights)
    for _ in range(min(count, len(pop))):
        if not pop: break
        chosen = random.choices(pop, weights=w_pop, k=1)[0]
        idx = pop.index(chosen)
        sampled.append(pop.pop(idx))
        w_pop.pop(idx)

    snippets = []
    for filepath in sampled:
        try:
            full_path = project_root / filepath
            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            if len(lines) > 150: continue
            numbered = [f"{i:>4}| {line}" for i, line in enumerate(lines[:100], 1)]
            preview = "\n".join(numbered)
            if len(lines) > 100:
                preview += f"\n     ... ({len(lines) - 100} more lines)"
            snippets.append(f"### {filepath} ({len(lines)} lines)\n```\n{preview}\n```")
        except: continue
    return "\nCODE SAMPLES...\n" + "\n\n".join(snippets) + "\n" if snippets else ""
