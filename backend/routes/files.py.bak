"""
routes/files.py — File Browser Router
=======================================
Handles file system operations for the built-in code editor:
- List directory contents (with security filtering)
- Read file contents (capped at 100KB for safety)
- Write file contents (with directory traversal protection)

SECURITY: All file operations are sandboxed to PROJECT_ROOT.
Directory traversal attacks (../../etc/passwd) are blocked by
normalizing paths and checking they start with PROJECT_ROOT.

Hidden files/directories (.git, __pycache__, venv, node_modules)
are filtered from directory listings to keep the UI clean.
"""

import os
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger("localmind.routes.files")

# Create router — all endpoints are file-browser-related
router = APIRouter(prefix="/api/files", tags=["files"])

# PROJECT_ROOT is the top-level LocalMind directory.
# All file operations are sandboxed within this directory.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/list")
async def list_files_api(path: str = "."):
    """List files in a directory relative to project root.
    
    Returns an array of file/directory entries with:
    - name: filename
    - path: relative path from project root (forward slashes)
    - type: 'file' or 'directory'
    - size: file size in bytes (null for directories)
    
    Filters out hidden/system directories to keep the UI clean.
    Security: prevents directory traversal via path normalization.
    """
    target = os.path.normpath(os.path.join(PROJECT_ROOT, path))

    # Security: prevent directory traversal (e.g., "../../etc/passwd")
    if not target.startswith(PROJECT_ROOT):
        return {"error": "Access denied", "files": []}
    if not os.path.isdir(target):
        return {"error": "Not a directory", "files": []}

    try:
        entries = []
        # Directories/files to hide from the file browser
        hidden = {'.git', '__pycache__', 'node_modules', 'venv', '.pytest_cache'}

        for entry in sorted(os.listdir(target)):
            # Skip hidden files (dotfiles) and system directories
            if entry.startswith('.') or entry in hidden:
                continue
            full = os.path.join(target, entry)
            rel = os.path.relpath(full, PROJECT_ROOT).replace("\\", "/")
            entries.append({
                "name": entry,
                "path": rel,
                "type": "directory" if os.path.isdir(full) else "file",
                "size": os.path.getsize(full) if os.path.isfile(full) else None,
            })
        return {
            "files": entries,
            "path": os.path.relpath(target, PROJECT_ROOT).replace("\\", "/"),
        }
    except Exception as e:
        return {"error": str(e), "files": []}


@router.get("/read")
async def read_file_api(path: str):
    """Read a file's content relative to project root.
    
    Caps file reads at 100KB to prevent loading huge files into memory.
    Returns the file content, relative path, and size in bytes.
    
    Security: prevents directory traversal via path normalization.
    """
    target = os.path.normpath(os.path.join(PROJECT_ROOT, path))

    # Security: prevent directory traversal
    if not target.startswith(PROJECT_ROOT):
        return {"error": "Access denied"}
    if not os.path.isfile(target):
        return {"error": "File not found"}

    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            # Cap at 100KB to prevent memory issues with large files
            content = f.read(100_000)
        return {
            "content": content,
            "path": os.path.relpath(target, PROJECT_ROOT).replace("\\", "/"),
            "size": os.path.getsize(target),
        }
    except Exception as e:
        return {"error": str(e)}


@router.post("/write")
async def write_file_api(request: Request):
    """Write content to a file relative to project root.
    
    Creates parent directories if they don't exist.
    Used by the code editor's save functionality.
    
    Security: prevents directory traversal via path normalization.
    Note: Files can NEVER be deleted via this API — only created/overwritten.
    """
    body = await request.json()
    path = body.get("path", "")
    content = body.get("content", "")
    target = os.path.normpath(os.path.join(PROJECT_ROOT, path))

    # Security: prevent directory traversal
    if not target.startswith(PROJECT_ROOT):
        return {"error": "Access denied"}

    try:
        # Ensure parent directories exist
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"File written: {path}")
        return {
            "success": True,
            "path": os.path.relpath(target, PROJECT_ROOT).replace("\\", "/"),
        }
    except Exception as e:
        return {"error": str(e)}
