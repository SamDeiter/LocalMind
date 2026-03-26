"""
code_editor.py — AI-powered code editor for LocalMind Autonomy Engine
=====================================================================
Handles search-and-replace editing with 4-layer matching:
  Layer 1: Exact match
  Layer 2: Line-ending normalization (CRLF → LF)
  Layer 3: Trailing whitespace strip
  Layer 4: Fuzzy matching via difflib (80% threshold)

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

# Proposals containing these keywords are too abstract for a 7B model to implement
SCOPE_TOO_BROAD = [
    "ssl", "tls", "oauth", "encryption", "authentication flow",
    "database migration", "microservices", "docker", "kubernetes",
    "ci/cd", "pipeline", "infrastructure", "deployment",
    "multi-language", "internationalization", "i18n",
    "redesign", "rewrite", "overhaul", "architecture",
]


def is_scope_achievable(proposal: dict) -> bool:
    """Check if a proposal is small enough for a 7B model to implement.

    Filters out proposals that are too abstract or require infrastructure
    changes that a small search-and-replace edit can't accomplish.
    """
    title = proposal.get("title", "").lower()
    desc = proposal.get("description", "").lower()
    combined = title + " " + desc

    for keyword in SCOPE_TOO_BROAD:
        if keyword in combined:
            logger.info(f"Scope too broad for 7B model: '{proposal.get('title')}' (matched: '{keyword}')")
            return False

    # Reject if effort is "large" — 7B model can't handle large changes
    if proposal.get("effort", "").lower() == "large":
        logger.info(f"Effort too large for auto-edit: '{proposal.get('title')}'")
        return False

    return True


def _find_callers(relative_path: str) -> str:
    """Find files that import/reference the target file.

    Returns a compact caller context string showing which files
    depend on this module, so the editing model knows not to
    break function signatures.
    """
    target_name = Path(relative_path).stem  # e.g. 'documents' from 'backend/routes/documents.py'
    callers = []
    skip_dirs = {"venv", "__pycache__", ".git", "node_modules", "memory_db", ".bak"}

    for ext in ("*.py", "*.js"):
        for p in PROJECT_ROOT.rglob(ext):
            rel = p.relative_to(PROJECT_ROOT)
            if any(skip in rel.parts for skip in skip_dirs):
                continue
            if str(rel).replace("\\", "/") == relative_path:
                continue  # Skip self

            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                # Look for import lines referencing this module
                relevant_lines = []
                for i, line in enumerate(content.split("\n"), 1):
                    line_stripped = line.strip()
                    if target_name in line_stripped and (
                        line_stripped.startswith("import ") or
                        line_stripped.startswith("from ") or
                        "require(" in line_stripped or
                        f"{target_name}." in line_stripped
                    ):
                        relevant_lines.append(f"  L{i}: {line_stripped[:100]}")

                if relevant_lines:
                    callers.append(f"  {str(rel).replace(chr(92), '/')}:\n" + "\n".join(relevant_lines[:3]))
            except (OSError, UnicodeDecodeError):
                continue

    if not callers:
        return ""

    header = f"FILES THAT IMPORT/USE '{target_name}':\n"
    # Cap context to avoid bloating the prompt
    context = "\n".join(callers[:5])
    return header + context + "\n"


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
            return [], 0

        files_set = set(files_list)

        # ── Fast-path: use files_affected from the proposal if they resolve ──
        proposal_files = proposal.get("files_affected", [])
        if proposal_files and isinstance(proposal_files, list):
            validated_fast = []
            for pf in proposal_files:
                pf = pf.strip().strip("/").replace("\\", "/")
                if pf in files_set and not is_protected_file(pf):
                    validated_fast.append(pf)
                else:
                    # Fuzzy: try matching by basename
                    basename = pf.rsplit("/", 1)[-1]
                    matches = [f for f in files_list if f.endswith("/" + basename) or f == basename]
                    if len(matches) == 1 and not is_protected_file(matches[0]):
                        validated_fast.append(matches[0])
                        logger.info(f"Fast-path fuzzy matched '{pf}' → '{matches[0]}'")

            if validated_fast:
                logger.info(f"Fast-path targeting: {validated_fast} (from proposal.files_affected)")
                if emit_activity:
                    emit_activity("info", f"Using proposal files: {', '.join(validated_fast[:3])}")
                return validated_fast[:2], len(validated_fast[:2]) * 50

            logger.info(f"Fast-path failed: proposal files {proposal_files} didn't match real files. Falling back to LLM.")

        numbered = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(files_list[:80]))

        async with httpx.AsyncClient(timeout=60.0) as client:
            prompt = (
                f"You are a code targeting assistant. Pick which file(s) to edit.\n\n"
                f"PROPOSAL: {proposal['title']}\n"
                f"CATEGORY: {proposal['category']}\n"
                f"DETAILS: {proposal['description']}\n\n"
                f"AVAILABLE FILES (pick ONLY from this list):\n{numbered}\n\n"
                f"RULES:\n"
                f"1. Pick 1-2 files that are MOST relevant to this proposal.\n"
                f"2. Output a JSON array of file paths, e.g. [\"backend/agent.py\"]\n"
                f"3. ONLY pick files from the list above. DO NOT invent files.\n"
                f"4. Do NOT pick server.py, autonomy.py, or run.py — they are protected.\n"
                f"5. Output ONLY the JSON array, nothing else.\n"
            )

            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": editing_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 200, "num_ctx": 4096},
                },
            )

            if resp.status_code != 200:
                logger.warning(f"Ollama returned {resp.status_code} for targeting")
                return [], 0

            text = resp.json().get("message", {}).get("content", "").strip()
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
                # Fallback: sweep the raw text for any matching filenames
                for word in text.replace('"', ' ').replace("'", " ").replace(",", " ").split():
                    word = word.strip("[].,-")
                    if not word: 
                        continue
                    if word in files_set or any(f.endswith("/" + word) or f == word for f in files_list):
                        if word not in candidates:
                            candidates.append(word)
                            
                if not candidates:
                    logger.warning(f"Could not parse file targeting response: {text[:200]}")
                    return [], 0

            validated = []
            for candidate in candidates:
                candidate = candidate.strip().strip("/")

                # Pre-filter: skip blocked files before validation
                if candidate.split("/")[-1] in BLOCKED_NAMES:
                    logger.info(f"Skipping blocked file from targeting: {candidate}")
                    if emit_activity:
                        emit_activity("info", f"Skipped protected file: {candidate}")
                    continue

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

            # Return list of mapped files, plus an estimation parameter for total size/tokens
            return validated[:2], len(validated[:2]) * 50

    except Exception as exc:
        logger.warning(f"Failed to identify target files: {exc}")

async def edit_single_file(
    relative_path: str,
    proposal: dict,
    ollama_url: str,
    editing_model: str,
    log_fn=None,
    emit_activity=None,
) -> tuple[bool, int]:
    """Read a file, ask AI for a search-and-replace diff, apply it.

    Uses a targeted diff approach instead of whole-file rewrite,
    which is far more reliable for small (7B) models.

    Returns True if the edit was successfully applied.
    """
    if is_protected_file(relative_path):
        return False, 0

    target = (PROJECT_ROOT / relative_path).resolve()

    if not target.exists():
        logger.warning(f"File not found: {relative_path}")
        return False, 0

    try:
        original_content = target.read_text(encoding="utf-8", errors="replace")

        # ── Smart Windowing ──
        # Instead of just the first 200 lines, we provide a larger window 
        # centered around where we think the change might happen (or just more lines).
        lines = original_content.split("\n")
        preview_limit = 500  # Increased from 200
        numbered_lines = []
        for i, line in enumerate(lines[:preview_limit], 1):
            numbered_lines.append(f"{i:>4}| {line}")
        file_preview = "\n".join(numbered_lines)
        if len(lines) > preview_limit:
            file_preview += f"\n     ... ({len(lines) - preview_limit} more lines)"

        async with httpx.AsyncClient(timeout=180.0) as client:
            prompt = (
                f"You are a code editor. Make ONE small, precise change.\n\n"
                f"TASK: {proposal['title']}\n"
                f"DETAILS: {proposal['description']}\n"
            )
            if proposal.get("context"):
                prompt += f"\nRESEARCH CONTEXT & ANALYSIS:\n{proposal['context']}\n"
            
            prompt += (
                f"\nFILE: {relative_path} (line numbers shown for reference only)\n"
                f"```\n{file_preview}\n```\n\n"
                f"Output a JSON object with these keys:\n"
                f'  "search": "EXACT consecutive lines from the file (WITHOUT line numbers)"\n'
                f'  "replace": "your improved version of those same lines"\n'
                f'  "explanation": "one sentence about what changed"\n\n'
                f"RULES:\n"
                f"1. Copy the search lines EXACTLY from the file — every space and character must match.\n"
                f"2. Do NOT include line numbers (like '  42|') in your search/replace text.\n"
                f"3. Keep edits to 3-15 lines. Targeted yet complete.\n"
                f"4. Only output the JSON object. No markdown fences.\n"
                f"5. The search text MUST appear verbatim in the file or the edit will FAIL.\n"
                f'6. If you cannot find a useful change, output: {{"search": "", "replace": "", "explanation": "no change needed"}}\n'
                f"7. Do NOT change function signatures unless necessary. If you add a module usage, check if imports exist.\n"
            )

            # Guardrail: Inject caller context
            caller_context = _find_callers(relative_path)
            if caller_context:
                prompt += f"\n{caller_context}\n"

            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": editing_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 2000, "num_ctx": 8192},
                },
            )

            if resp.status_code != 200:
                logger.warning(f"Ollama returned {resp.status_code} for edit")
                if emit_activity:
                    emit_activity("error", f"Edit failed: Ollama HTTP {resp.status_code} for {relative_path}")
                return False, 0

            raw_response = resp.json().get("message", {}).get("content", "").strip()

        # Parse the search/replace JSON
        diff = _parse_diff_response(raw_response, relative_path, emit_activity)
        if diff is None:
            return False, 0

        search_text = diff.get("search", "")
        replace_text = diff.get("replace", "")
        explanation = diff.get("explanation", "")

        # Strip any line number prefixes the model may have included
        search_text = _strip_line_numbers(search_text)
        replace_text = _strip_line_numbers(replace_text)

        if not search_text or not replace_text or search_text == replace_text:
            logger.info(f"AI returned no-op for {relative_path}: {explanation}")
            if emit_activity:
                emit_activity("info", f"Skipped: No changes needed for {relative_path}")
            return False, 0

        # ── Multi-layer search matching ──
        new_content = _apply_search_replace(original_content, search_text, replace_text, relative_path)

        if new_content is None:
            logger.warning(
                f"Search text not found in {relative_path}. "
                f"Search (first 150 chars): {repr(search_text[:150])}"
            )
            if emit_activity:
                emit_activity("error", f"Edit failed: search text not found in {relative_path}")
            return False, 0

        # ── AST Validation (Python) ──
        if relative_path.endswith(".py"):
            import ast
            try:
                tree = ast.parse(new_content)
                # Quick scan for obvious NameErrors (very simplified)
                # We could do more complex analysis here later.
                compile(new_content, relative_path, "exec")
            except SyntaxError as syn_err:
                logger.warning(f"AI produced invalid Python for {relative_path}: {syn_err}")
                if emit_activity:
                    emit_activity("error", f"Edit rejected: syntax error in {relative_path} line {syn_err.lineno}")
                return False, 0
            except Exception as e:
                logger.warning(f"Validation error for {relative_path}: {e}")

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
                "proposal_id": proposal.get("id", "none"),
            })
        if emit_activity:
            emit_activity("edited", f"Applied: {explanation}", file=relative_path)

        return True, 500

    except Exception as exc:
        logger.error(f"Failed to edit {relative_path}: {exc}")
        return False, 0


def _strip_line_numbers(text: str) -> str:
    """Remove line number prefixes like '  42| ' that the model may include."""
    lines = text.split("\n")
    stripped = []
    for line in lines:
        # Match patterns like "  42| code" or "42| code"
        m = re.match(r'^\s*\d+\|\s?', line)
        if m:
            stripped.append(line[m.end():])
        else:
            stripped.append(line)
    return "\n".join(stripped)


def _parse_diff_response(raw_response: str, relative_path: str, emit_activity=None) -> dict | None:
    """Parse the AI's JSON response, with fallback regex extraction."""
    try:
        json_text = raw_response
        if "```" in json_text:
            json_text = json_text.split("```")[1]
            if json_text.startswith("json"):
                json_text = json_text[4:]
            json_text = json_text.strip()

        return json.loads(json_text)
    except (json.JSONDecodeError, IndexError):
        match = re.search(r'\{[^{}]*"search"[^{}]*\}', raw_response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning(f"Could not parse edit response for {relative_path}")
        if emit_activity:
            emit_activity("error", f"Edit failed: could not parse AI response for {relative_path}")
        return None


def _apply_search_replace(original_content: str, search_text: str, replace_text: str, relative_path: str) -> str | None:
    """Apply search-and-replace with 4-layer matching. Returns new content or None."""

    # Layer 1: Exact match
    if search_text in original_content:
        return original_content.replace(search_text, replace_text, 1)

    # Layer 2: Normalize line endings
    norm_search = search_text.replace("\r\n", "\n").replace("\r", "\n")
    norm_content = original_content.replace("\r\n", "\n")
    if norm_search in norm_content:
        new_content = norm_content.replace(norm_search, replace_text.replace("\r\n", "\n"), 1)
        if "\r\n" in original_content:
            new_content = new_content.replace("\n", "\r\n")
        logger.info(f"Matched via line-ending normalization for {relative_path}")
        return new_content

    # Layer 3: Strip trailing whitespace per line
    stripped_search = "\n".join(l.rstrip() for l in norm_search.split("\n"))
    stripped_content = "\n".join(l.rstrip() for l in norm_content.split("\n"))
    if stripped_search in stripped_content:
        idx = stripped_content.index(stripped_search)
        orig_lines = norm_content.split("\n")
        stripped_lines = stripped_search.split("\n")
        start_line = stripped_content[:idx].count("\n")
        n_lines = len(stripped_lines)
        replace_lines = replace_text.replace("\r\n", "\n").split("\n")
        new_lines = orig_lines[:start_line] + replace_lines + orig_lines[start_line + n_lines:]
        sep = "\r\n" if "\r\n" in original_content else "\n"
        logger.info(f"Matched via whitespace-stripped lines for {relative_path}")
        return sep.join(new_lines)

    # Layer 4: Fuzzy line-by-line matching (lowered to 80%)
    try:
        search_lines = norm_search.strip().split("\n")
        file_lines = norm_content.split("\n")

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

        if best_ratio >= 0.80 and best_start >= 0:
            replace_lines = replace_text.replace("\r\n", "\n").split("\n")
            new_file_lines = file_lines[:best_start] + replace_lines + file_lines[best_start + window:]
            sep = "\r\n" if "\r\n" in original_content else "\n"
            logger.info(f"Matched via fuzzy matching ({best_ratio:.0%}) for {relative_path}")
            return sep.join(new_file_lines)
    except Exception as fuzzy_exc:
        logger.warning(f"Fuzzy match failed: {fuzzy_exc}")

    return None
