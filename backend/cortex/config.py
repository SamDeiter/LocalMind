"""
Cortex Configuration — manages workspace scanning settings.
Loads/saves from data/cortex_config.json.
"""

import json
import logging
import fnmatch
from pathlib import Path
from typing import Optional

logger = logging.getLogger("localmind.cortex.config")

# Default config file location
_CONFIG_DIR = Path(__file__).parent.parent.parent / "data"
_CONFIG_FILE = _CONFIG_DIR / "cortex_config.json"

# Default project directory (the LocalMind repo itself)
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Common binary / non-text extensions to always ignore
_DEFAULT_IGNORE_PATTERNS: list[str] = [
    "__pycache__",
    "node_modules",
    ".git",
    "*.pyc",
    "venv",
    ".venv",
    ".env",
    ".env.*",
    "*.db",
    "*.sqlite3",
    "*.egg-info",
    "dist",
    "build",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "*.swp",
    "*.swo",
    "*.bak",
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.bin",
    "*.obj",
    "*.o",
    "*.a",
    "*.lib",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.bz2",
    "*.7z",
    "*.rar",
    "*.jar",
    "*.war",
    "*.ear",
    "*.class",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.bmp",
    "*.ico",
    "*.svg",
    "*.webp",
    "*.mp3",
    "*.mp4",
    "*.wav",
    "*.avi",
    "*.mov",
    "*.mkv",
    "*.pdf",
    "*.doc",
    "*.docx",
    "*.xls",
    "*.xlsx",
    "*.ppt",
    "*.pptx",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.min.js",
    "*.min.css",
    "*.map",
    "package-lock.json",
    "cortex_data",
    "rag_data",
    "memory_db",
    "chroma_data",
    "Thumbs.db",
    ".DS_Store",
    "desktop.ini",
]

_DEFAULT_FILE_EXTENSIONS: list[str] = [
    ".py",
    ".js",
    ".ts",
    ".json",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".ini",
    ".sh",
    ".bat",
    ".ps1",
]


class CortexConfig:
    """Manages Workspace Cortex configuration — what to scan, what to skip."""

    def __init__(self) -> None:
        self.watched_dirs: list[str] = [str(_PROJECT_ROOT)]
        self.ignore_patterns: list[str] = list(_DEFAULT_IGNORE_PATTERNS)
        self.scan_interval_seconds: int = 300
        self.max_file_size_kb: int = 500
        self.enabled: bool = True
        self.file_extensions: list[str] = list(_DEFAULT_FILE_EXTENSIONS)

    # ── Persistence ──────────────────────────────────────────────

    def load(self) -> "CortexConfig":
        """Load config from disk. Returns self for chaining."""
        try:
            if _CONFIG_FILE.exists():
                data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
                self.watched_dirs = data.get("watched_dirs", self.watched_dirs)
                self.ignore_patterns = data.get("ignore_patterns", self.ignore_patterns)
                self.scan_interval_seconds = data.get("scan_interval_seconds", self.scan_interval_seconds)
                self.max_file_size_kb = data.get("max_file_size_kb", self.max_file_size_kb)
                self.enabled = data.get("enabled", self.enabled)
                self.file_extensions = data.get("file_extensions", self.file_extensions)
                logger.info("Cortex config loaded from %s", _CONFIG_FILE)
            else:
                logger.info("No cortex config file found; using defaults.")
        except Exception as exc:
            logger.warning("Failed to load cortex config: %s — using defaults.", exc)
        return self

    def save(self) -> None:
        """Persist current config to disk."""
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = {
                "watched_dirs": self.watched_dirs,
                "ignore_patterns": self.ignore_patterns,
                "scan_interval_seconds": self.scan_interval_seconds,
                "max_file_size_kb": self.max_file_size_kb,
                "enabled": self.enabled,
                "file_extensions": self.file_extensions,
            }
            _CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("Cortex config saved to %s", _CONFIG_FILE)
        except Exception as exc:
            logger.error("Failed to save cortex config: %s", exc)

    # ── Directory management ─────────────────────────────────────

    def add_watch_dir(self, path: str) -> None:
        """Add a directory to the watch list (idempotent)."""
        resolved = str(Path(path).resolve())
        if resolved not in self.watched_dirs:
            self.watched_dirs.append(resolved)
            logger.info("Added watch directory: %s", resolved)

    def remove_watch_dir(self, path: str) -> None:
        """Remove a directory from the watch list."""
        resolved = str(Path(path).resolve())
        if resolved in self.watched_dirs:
            self.watched_dirs.remove(resolved)
            logger.info("Removed watch directory: %s", resolved)

    # ── Filtering ────────────────────────────────────────────────

    def should_ignore(self, filepath: str) -> bool:
        """Return True if the given filepath should be skipped based on ignore patterns."""
        path = Path(filepath)
        # Check every component of the path and the filename against patterns
        parts = list(path.parts) + [path.name]
        for pattern in self.ignore_patterns:
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return True
            # Also check against full path for glob-style patterns
            if fnmatch.fnmatch(str(path), pattern):
                return True
        return False
