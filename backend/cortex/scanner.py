"""
Workspace Scanner — recursively walks directories and collects file metadata.
"""

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import CortexConfig

logger = logging.getLogger("localmind.cortex.scanner")

# Map file extensions to human-readable language names
_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".cfg": "config",
    ".ini": "config",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".bat": "batch",
    ".ps1": "powershell",
    ".xml": "xml",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".r": "r",
    ".lua": "lua",
    ".pl": "perl",
    ".dockerfile": "dockerfile",
}


@dataclass
class FileInfo:
    """Metadata for a single scanned file."""
    path: str
    name: str
    extension: str
    size_bytes: int
    modified_at: float  # Unix timestamp
    content_hash: str   # MD5 hex digest
    project_name: str   # Parent directory name (top-level project)
    language: str       # Inferred from extension


@dataclass
class ChangeSummary:
    """Result of comparing two scans."""
    added: list[FileInfo] = field(default_factory=list)
    modified: list[FileInfo] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    def __repr__(self) -> str:
        return (
            f"ChangeSummary(added={len(self.added)}, "
            f"modified={len(self.modified)}, "
            f"deleted={len(self.deleted)})"
        )


def _infer_language(extension: str) -> str:
    """Infer language from file extension."""
    return _EXTENSION_LANGUAGE_MAP.get(extension.lower(), "unknown")


def _compute_hash(filepath: Path) -> str:
    """Compute MD5 hash of file content. Returns empty string on failure."""
    try:
        hasher = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as exc:
        logger.debug("Hash failed for %s: %s", filepath, exc)
        return ""


class WorkspaceScanner:
    """Recursively scans directories and collects file metadata."""

    def __init__(self, config: CortexConfig) -> None:
        self._config = config

    def scan_directory(self, dir_path: str) -> list[FileInfo]:
        """Recursively walk a single directory and return metadata for eligible files."""
        results: list[FileInfo] = []
        root = Path(dir_path)

        if not root.exists():
            logger.warning("Directory does not exist: %s", dir_path)
            return results

        if not root.is_dir():
            logger.warning("Path is not a directory: %s", dir_path)
            return results

        project_name = root.name
        max_size = self._config.max_file_size_kb * 1024
        allowed_extensions = set(self._config.file_extensions)

        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                # Prune ignored directories in-place so os.walk skips them
                dirnames[:] = [
                    d for d in dirnames
                    if not self._config.should_ignore(os.path.join(dirpath, d))
                ]

                for fname in filenames:
                    full_path = os.path.join(dirpath, fname)

                    # Check ignore patterns
                    if self._config.should_ignore(full_path):
                        continue

                    file_path = Path(full_path)
                    ext = file_path.suffix.lower()

                    # Only index configured file extensions
                    if ext not in allowed_extensions:
                        continue

                    try:
                        stat = file_path.stat()
                    except (OSError, PermissionError) as exc:
                        logger.debug("Cannot stat %s: %s", full_path, exc)
                        continue

                    # Skip files that are too large
                    if stat.st_size > max_size:
                        logger.debug("Skipping large file (%d KB): %s",
                                     stat.st_size // 1024, full_path)
                        continue

                    content_hash = _compute_hash(file_path)

                    info = FileInfo(
                        path=str(file_path.resolve()),
                        name=fname,
                        extension=ext,
                        size_bytes=stat.st_size,
                        modified_at=stat.st_mtime,
                        content_hash=content_hash,
                        project_name=project_name,
                        language=_infer_language(ext),
                    )
                    results.append(info)

        except PermissionError as exc:
            logger.warning("Permission denied while scanning %s: %s", dir_path, exc)
        except Exception as exc:
            logger.error("Unexpected error scanning %s: %s", dir_path, exc)

        logger.info("Scanned %s — found %d files", dir_path, len(results))
        return results

    def scan_all(self) -> list[FileInfo]:
        """Scan all watched directories and return combined results."""
        all_files: list[FileInfo] = []
        for dir_path in self._config.watched_dirs:
            try:
                files = self.scan_directory(dir_path)
                all_files.extend(files)
            except Exception as exc:
                logger.error("Error scanning directory %s: %s", dir_path, exc)
        logger.info("Full scan complete — %d files total across %d directories",
                     len(all_files), len(self._config.watched_dirs))
        return all_files

    def detect_changes(
        self,
        previous_scan: dict[str, str],
        current_scan: list[FileInfo],
    ) -> ChangeSummary:
        """
        Compare hashes to find new, modified, and deleted files.

        Args:
            previous_scan: dict mapping file_path -> content_hash from the last scan.
            current_scan: list of FileInfo from the current scan.

        Returns:
            ChangeSummary with added, modified, and deleted files.
        """
        summary = ChangeSummary()
        current_paths: set[str] = set()

        for fi in current_scan:
            current_paths.add(fi.path)
            old_hash = previous_scan.get(fi.path)
            if old_hash is None:
                summary.added.append(fi)
            elif old_hash != fi.content_hash:
                summary.modified.append(fi)
            # else: unchanged — skip

        # Files in previous scan but not in current scan have been deleted
        for old_path in previous_scan:
            if old_path not in current_paths:
                summary.deleted.append(old_path)

        if summary.has_changes:
            logger.info("Changes detected: %s", summary)
        else:
            logger.debug("No changes detected.")

        return summary
