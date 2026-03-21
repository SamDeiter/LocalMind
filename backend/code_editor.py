"""
code_editor.py — AI-powered code editor for LocalMind Autonomy Engine
=====================================================================
Handles search-and-replace editing with 4-layer matching:
  Layer 1: Exact match
  Layer 2: Line-ending normalization (CRLF → LF)
  Layer 3: Trailing whitespace strip
  Layer 4: Fuzzy matching via difflib (85% threshold)

Extracted from autonomy.py to keep files lean and editable.
"""

import difflib
import json
import logging
import re
import shutil
from pathlib import Path

import httpx

logger = logging.getLogger("localmind.autonomy.editor")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Security boundaries
BLOCKED_DIRS = {"venv", ".git", "node_modules", "__pycache__", "memory_db"}
BLOCKED_NAMES = {".env", ".env.local", ".env.production", "autonomy.py", "server.py", "run.py"}
BLOCKED_EXTS = {".key", ".pem", ".secret", ".p12", ".pfx"}


def is_protected_file(relative_path: str) -> bool:
    """Check if a file is protected from editing."""
    target = (PROJECT_ROOT / relative_path).resolve()

    if not str(target).startswith(str(PROJECT_ROOT)):
        logger.warning(f"Path escapes project: {relative_path}")
        return True

    if target.name in BLOCKED_NAMES:
        logger.warning(f"Blocked file: {target.name}")
        return True

    if target.suffix.lower() in BLOCKED_EXTS:
        logger.warning(f"Blocked extension: {target.suffix}")
        return True

    for part in target.relative_to(PROJECT_ROOT).parts:
        if part in BLOCKED_DIRS:
            logger.warning(f"Blocked directory: {part}")
            return True

    return False


async def identify_target_files(
    proposal: dict,
    ollama_url: str,
    editing_model: str,
    emit_activity=None,
) -> list[str]:
    """Ask the AI which file(s) to edit for a proposal.

    Validates AI suggestions against the actual project file list
    to prevent hallucinated paths from reaching the edit stage.
    """
    try:
        files_list = []
        skip_dirs = {"venv", "__pycache__", ".git", "node_modules", "memory_db", ".bak"}
        for ext in ("*.py", "*.js", "*.html", "*.css"):
            for p in PROJECT_ROOT.rglob(ext):
                rel = p.relative_to(PROJECT_ROOT)
                if any(skip in rel.parts for skip in skip_dirs):
                    continue
                files_list.append(str(rel).replace("\\", "/"))

        if not files_list:
            logger.warning("No project files found for targeting")
            return []

        numbered = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(files_list[:80]))
        files_set = set(files_list)

        async with httpx.AsyncClient(timeout=60.0) as client:
            prompt = (
                f"You are a code targeting assistant. Pick which file(s) to edit.\n\n"
                f"PROPOSAL: {proposal['title']}\n"
                f"CATEGORY: {proposal['category']}\n"
                f"DETAILS: {proposal['description']}\n\n"
                f"AVAILABLE FILES (pick ONLY from this list):\n{numbered}\n\n"
                f"RULES:\n"
                f"1. Pick 1-3 files that are MOST relevant to this proposal.\n"
                f"2. Output a JSON array of file paths, e.g. [\"backend/agent.py\"]\n"
                f"3. ONLY pick files from the list above. DO NOT invent files.\n"
                f"4. Output ONLY the JSON array, nothing else.\n"
            )

            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": editing_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 200, "num_ctx": 4096},
                },
            )

            if resp.status_code != 200:
                logger.warning(f"Ollama returned {resp.status_code} for targeting")
                return []

            text = resp.json().get("response", "").strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()

            candidates = []
            try:
                result = json.loads(text)
                if isinstance(result, list):
                    candidates = [f for f in result if isinstance(f, str) and f.strip()]
            except json.JSONDecodeError:
                match = re.search(r'\[([^\]]+)\]', text)
                if match:
                    try:
                        result = json.loads(f"[{match.group(1)}]")
                        if isinstance(result, list):
                            candidates = [f for f in result if isinstance(f, str) and f.strip()]
                    except json.JSONDecodeError:
                        pass

            if not candidates:
                logger.warning(f"Could not parse file targeting response: {text[:200]}")
                return []

            validated = []
            for candidate in candidates:
                candidate = candidate.strip().strip("/")
                if candidate in files_set:
                    validated.append(candidate)
                else:
                    basename = candidate.rsplit("/", 1)[-1]
                    matches = [f for f in files_list if f.endswith("/" + basename) or f == basename]
                    if len(matches) == 1:
                        validated.append(matches[0])
                        logger.info(f"Fuzzy-matched '{candidate}' → '{matches[0]}'")
                    else:
                        logger.warning(f"AI suggested non-existent file: '{candidate}'")
                        if emit_activity:
                            emit_activity("info", f"Ignored hallucinated path: {candidate}")

            return validated[:3]

    except Exception as exc:
        logger.warning(f"Failed to identify target files: {exc}")

    return []


async def edit_single_file(
    relative_path: str,
    proposal: dict,
    ollama_url: str,
    editing_model: str,
    log_fn=None,
    emit_activity=None,
) -> bool:
    """Read a file, ask AI for a search-and-replace diff, apply it.

    Uses a targeted diff approach instead of whole-file rewrite,
    which is far more reliable for small (7B) models.

    Returns True if the edit was successfully applied.
    """
    if is_protected_file(relative_path):
        return False

    target = (PROJECT_ROOT / relative_path).resolve()

    if not target.exists():
        logger.warning(f"File not found: {relative_path}")
        return False

    try:
        original_content = target.read_text(encoding="utf-8", errors="replace")

        file_preview = original_content[:6000]
        if len(original_content) > 6000:
            file_preview += f"\n\n# ... ({len(original_content) - 6000} more chars truncated)"

        async with httpx.AsyncClient(timeout=180.0) as client:
            prompt = (
                f"You are a precise code editor. Make a SMALL, TARGETED fix.\n\n"
                f"TASK: {proposal['title']}\n"
                f"DETAILS: {proposal['description']}\n\n"
                f"FILE: {relative_path}\n"
                f"```\n{file_preview}\n```\n\n"
                f"Output a JSON object with exactly these keys:\n"
                f'  "search": "EXACT consecutive lines copied from the file above"\n'
                f'  "replace": "the replacement lines with your improvement"\n'
                f'  "explanation": "one sentence explaining the change"\n\n'
                f"CRITICAL RULES:\n"
                f"1. COPY-PASTE the search lines EXACTLY from the file — every space, quote, and character must match.\n"
                f"2. Keep the change to 3-10 lines maximum. Do NOT rewrite large blocks.\n"
                f"3. Output ONLY the JSON object, no markdown fences, no extra text.\n"
                f"4. The search text MUST appear verbatim in the file above. If it does not, the edit will FAIL.\n"
                f'5. If you cannot make a useful change, output: {{"search": "", "replace": "", "explanation": "no change needed"}}\n'
            )

            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": editing_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 2000, "num_ctx": 8192},
                },
            )

            if resp.status_code != 200:
                logger.warning(f"Ollama returned {resp.status_code} for edit")
                if emit_activity:
                    emit_activity("error", f"Edit failed: Ollama HTTP {resp.status_code} for {relative_path}")
                return False

            raw_response = resp.json().get("response", "").strip()

        # Parse the search/replace JSON
        try:
            json_text = raw_response
            if "```" in json_text:
                json_text = json_text.split("```")[1]
                if json_text.startswith("json"):
                    json_text = json_text[4:]
                json_text = json_text.strip()

            diff = json.loads(json_text)
        except (json.JSONDecodeError, IndexError):
            match = re.search(r'\{[^{}]*"search"[^{}]*\}', raw_response, re.DOTALL)
            if match:
                try:
                    diff = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse edit response for {relative_path}")
                    if emit_activity:
                        emit_activity("error", f"Edit failed: could not parse AI response for {relative_path}")
                    return False
            else:
                logger.warning(f"No JSON found in edit response for {relative_path}")
                if emit_activity:
                    emit_activity("error", f"Edit failed: no valid JSON in AI response for {relative_path}")
                return False

        search_text = diff.get("search", "")
        replace_text = diff.get("replace", "")
        explanation = diff.get("explanation", "")

        if not search_text or not replace_text or search_text == replace_text:
            logger.info(f"AI returned no-op for {relative_path}: {explanation}")
            if emit_activity:
                emit_activity("info", f"Skipped: No changes needed for {relative_path}")
            return False

        # ── Multi-layer search matching ──
        matched = False
        new_content = ""

        # Layer 1: Exact match
        if search_text in original_content:
            new_content = original_content.replace(search_text, replace_text, 1)
            matched = True

        # Layer 2: Normalize line endings
        if not matched:
            norm_search = search_text.replace("\r\n", "\n").replace("\r", "\n")
            norm_content = original_content.replace("\r\n", "\n")
            if norm_search in norm_content:
                new_content = original_content.replace("\r\n", "\n")
                new_content = new_content.replace(norm_search, replace_text.replace("\r\n", "\n"), 1)
                if "\r\n" in original_content:
                    new_content = new_content.replace("\n", "\r\n")
                matched = True
                logger.info(f"Matched via line-ending normalization for {relative_path}")

        # Layer 3: Strip trailing whitespace per line
        if not matched:
            stripped_search = "\n".join(l.rstrip() for l in search_text.replace("\r\n", "\n").split("\n"))
            stripped_content = "\n".join(l.rstrip() for l in original_content.replace("\r\n", "\n").split("\n"))
            if stripped_search in stripped_content:
                idx = stripped_content.index(stripped_search)
                orig_lines = original_content.replace("\r\n", "\n").split("\n")
                stripped_lines = stripped_search.split("\n")
                start_line = stripped_content[:idx].count("\n")
                n_lines = len(stripped_lines)
                replace_lines = replace_text.replace("\r\n", "\n").split("\n")
                new_lines = orig_lines[:start_line] + replace_lines + orig_lines[start_line + n_lines:]
                sep = "\r\n" if "\r\n" in original_content else "\n"
                new_content = sep.join(new_lines)
                matched = True
                logger.info(f"Matched via whitespace-stripped lines for {relative_path}")

        # Layer 4: Fuzzy line-by-line matching
        if not matched:
            try:
                search_lines = search_text.replace("\r\n", "\n").strip().split("\n")
                file_lines = original_content.replace("\r\n", "\n").split("\n")

                best_ratio = 0
                best_start = -1
                window = len(search_lines)

                for i in range(len(file_lines) - window + 1):
                    candidate = file_lines[i:i + window]
                    ratio = difflib.SequenceMatcher(
                        None,
                        "\n".join(search_lines),
                        "\n".join(candidate)
                    ).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_start = i

                if best_ratio >= 0.85 and best_start >= 0:
                    replace_lines = replace_text.replace("\r\n", "\n").split("\n")
                    new_file_lines = file_lines[:best_start] + replace_lines + file_lines[best_start + window:]
                    sep = "\r\n" if "\r\n" in original_content else "\n"
                    new_content = sep.join(new_file_lines)
                    matched = True
                    logger.info(f"Matched via fuzzy matching ({best_ratio:.0%}) for {relative_path}")
            except Exception as fuzzy_exc:
                logger.warning(f"Fuzzy match failed: {fuzzy_exc}")

        if not matched:
            logger.warning(
                f"Search text not found in {relative_path}. "
                f"Search (first 150 chars): {repr(search_text[:150])}"
            )
            if emit_activity:
                emit_activity("error", f"Edit failed: search text not found in {relative_path}")
            return False

        # Syntax validation for Python files
        if relative_path.endswith(".py"):
            try:
                compile(new_content, relative_path, "exec")
            except SyntaxError as syn_err:
                logger.warning(f"AI produced invalid Python for {relative_path}: {syn_err}")
                if emit_activity:
                    emit_activity("error", f"Edit rejected: syntax error in {relative_path} line {syn_err.lineno}")
                return False

        # Create backup and write
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)
        target.write_text(new_content, encoding="utf-8")

        logger.info(f"📝 Self-edit applied to {relative_path}: {explanation}")
        if log_fn:
            log_fn("self_edit_applied", {
                "file": relative_path,
                "original_size": len(original_content),
                "new_size": len(new_content),
                "change_size": abs(len(replace_text) - len(search_text)),
                "explanation": explanation,
                "proposal_id": proposal["id"],
            })
        if emit_activity:
            emit_activity("edited", f"Applied: {explanation}", file=relative_path)

        return True

    except Exception as exc:
        logger.error(f"Failed to edit {relative_path}: {exc}")
        return False
