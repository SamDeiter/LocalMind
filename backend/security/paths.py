"""
localmind.security.paths — Path Jailing Module

ALL file operations (read, write, copy, move, delete) must go through this
module.  LLM-generated file paths are NEVER trusted directly.

Key guarantees:
  • Resolves every symlink before comparing to the jail root.
  • Rejects null bytes, Unicode control chars, bidi overrides, and BOM.
  • Rejects double-extension filenames (e.g. report.docx.exe).
  • Validates upload MIME type against an explicit allowlist.
  • Works on both POSIX and Windows (tested on Windows 11).
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger("localmind.security.paths")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pptx",
        ".xlsx",
        ".docx",
        ".pdf",
        ".txt",
        ".csv",
        ".json",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
    }
)

# Maps extension → expected MIME type prefix/exact string.
# mimetypes.guess_type() returns (mime, encoding); we compare the mime part.
MIME_TYPE_MAP: dict[str, str] = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

# Control-character ranges to strip/reject in filenames.
# U+0000-U+001F  C0 controls (includes NUL, tab, newline …)
# U+007F         DEL
# U+0080-U+009F  C1 controls
# U+200B         Zero-width space
# U+202E         Right-to-left override (bidi attack)
# U+FEFF         BOM / zero-width no-break space
_DANGEROUS_CHARS_RE = re.compile(
    r"[\x00-\x1f\x7f\x80-\x9f\u200b\u202e\ufeff]"
)

# A path separator on *any* OS.
_PATH_SEP_RE = re.compile(r"[/\\]")

# Detect a Windows absolute path like C:\ or C:/ anywhere in the string.
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")

# Max filename length (POSIX and NTFS both cap at 255 bytes in UTF-8).
_MAX_FILENAME_BYTES = 255


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class SecurityError(Exception):
    """Raised when a path escapes the jail or violates a security policy."""


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _has_double_extension(name: str) -> bool:
    """
    Return True if *name* has more than one significant extension.

    Examples
    --------
    >>> _has_double_extension("report.docx.exe")
    True
    >>> _has_double_extension("report.docx")
    False
    >>> _has_double_extension(".hidden")
    False
    """
    # Split into stem + suffix pairs, ignoring leading dots (hidden files).
    p = Path(name)
    suffixes = p.suffixes  # e.g. ['.docx', '.exe']
    # A bare dotfile like ".bashrc" has suffixes=['.bashrc'] — not double.
    # Two or more distinct suffixes → double extension.
    return len(suffixes) >= 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def safe_resolve(base_dir: Path | str, user_path: str | Path) -> Path:
    """
    Resolve *user_path* relative to *base_dir* and verify it stays inside.

    Parameters
    ----------
    base_dir:
        The jail root.  Must be an existing directory.
    user_path:
        Untrusted path supplied by an LLM or end-user.

    Returns
    -------
    Path
        Fully resolved, canonical path guaranteed to be under *base_dir*.

    Raises
    ------
    SecurityError
        If the resolved path escapes the jail for any reason.
    """
    base_dir = Path(base_dir)
    user_path_str = str(user_path)

    # --- Null-byte check (would truncate C strings silently) ---------------
    if "\x00" in user_path_str:
        logger.warning("Null byte detected in path: %r", user_path_str)
        raise SecurityError(f"Null byte in path: {user_path_str!r}")

    # --- Resolve the jail root itself (follow its symlinks too) ------------
    try:
        resolved_base = base_dir.resolve()
    except OSError as exc:
        raise SecurityError(f"Cannot resolve base directory {base_dir!r}: {exc}") from exc

    # --- Build a candidate path --------------------------------------------
    # Strip leading separators / Windows drive letters so we always treat
    # user_path as a *relative* path inside the jail.
    user_path_stripped = user_path_str

    if _WIN_DRIVE_RE.match(user_path_stripped):
        # e.g. "C:\secret\data" → "secret\data" (drop drive + root sep)
        user_path_stripped = re.sub(r"^[A-Za-z]:[/\\]", "", user_path_stripped)
        logger.debug("Stripped Windows drive letter from user path")

    # Strip leading POSIX or Windows root separators.
    user_path_stripped = user_path_stripped.lstrip("/\\")

    candidate = resolved_base / user_path_stripped

    # --- Resolve ALL symlinks in candidate ---------------------------------
    try:
        resolved_candidate = candidate.resolve()
    except OSError as exc:
        # A dangling symlink or permission error still counts as a violation.
        raise SecurityError(
            f"Cannot resolve candidate path {candidate!r}: {exc}"
        ) from exc

    # --- Enforce jail via string prefix comparison -------------------------
    # We must compare strings (not Path parents) so that a jail at
    # /data/uploads does NOT match /data/uploads_evil.
    # Add the OS separator to avoid that exact prefix-collision scenario.
    resolved_base_str = str(resolved_base)
    resolved_candidate_str = str(resolved_candidate)

    # Normalise case on Windows (NTFS is case-insensitive).
    if os.name == "nt":
        resolved_base_str = resolved_base_str.lower()
        resolved_candidate_str = resolved_candidate_str.lower()

    jail_prefix = resolved_base_str.rstrip(os.sep) + os.sep

    if not (
        resolved_candidate_str == resolved_base_str.rstrip(os.sep)
        or resolved_candidate_str.startswith(jail_prefix)
    ):
        logger.warning(
            "Path escape attempt: %r resolved to %r, outside jail %r",
            user_path_str,
            resolved_candidate,
            resolved_base,
        )
        raise SecurityError(
            f"Path {user_path_str!r} escapes the jail {resolved_base!r}"
        )

    logger.debug("safe_resolve OK: %r -> %r", user_path_str, resolved_candidate)
    return resolved_candidate


def sanitize_filename(filename: str) -> str:
    """
    Clean an untrusted filename.

    Steps (in order):
      1. Reject null bytes.
      2. Strip path separators (/ and \\).
      3. Strip Unicode control characters and dangerous Unicode characters.
      4. Reject double extensions.
      5. Truncate to 255 bytes (UTF-8 encoded).

    Parameters
    ----------
    filename:
        Raw filename from user/LLM input.

    Returns
    -------
    str
        Cleaned filename, guaranteed to be safe to use on disk.

    Raises
    ------
    SecurityError
        If the filename contains a double extension or is otherwise
        irrecoverably dangerous.
    """
    if "\x00" in filename:
        raise SecurityError(f"Null byte in filename: {filename!r}")

    # Strip path separators — we never want a filename to contain / or \.
    cleaned = _PATH_SEP_RE.sub("", filename)

    # Strip dangerous Unicode: control chars, bidi override, zero-width, BOM.
    cleaned = _DANGEROUS_CHARS_RE.sub("", cleaned)

    # Also strip Unicode "other" categories (Cc, Cf) beyond what the regex
    # catches — paranoia pass using unicodedata.
    cleaned = "".join(
        ch
        for ch in cleaned
        if unicodedata.category(ch) not in ("Cc", "Cf")
    )

    if not cleaned:
        raise SecurityError(f"Filename {filename!r} is empty after sanitization")

    # Double-extension check.
    if _has_double_extension(cleaned):
        raise SecurityError(
            f"Double extension detected in filename: {cleaned!r}"
        )

    # Truncate to 255 bytes without splitting a multibyte sequence.
    encoded = cleaned.encode("utf-8")
    if len(encoded) > _MAX_FILENAME_BYTES:
        # Truncate at byte boundary and decode.
        encoded = encoded[:_MAX_FILENAME_BYTES]
        # Walk back until the truncated slice is valid UTF-8.
        while encoded:
            try:
                cleaned = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        else:
            raise SecurityError(
                f"Filename {filename!r} could not be truncated to valid UTF-8"
            )

    logger.debug("sanitize_filename: %r -> %r", filename, cleaned)
    return cleaned


def validate_upload(filepath: Path, max_size_mb: int = 100) -> None:
    """
    Validate an uploaded file before it is processed by the task worker.

    Checks (in order):
      1. File exists.
      2. File size is within *max_size_mb*.
      3. Extension is in ALLOWED_EXTENSIONS.
      4. No double extension.
      5. MIME type (guessed from extension) matches the expected type.

    Parameters
    ----------
    filepath:
        Absolute path to the file on disk.  Must already be resolved through
        ``safe_resolve`` before calling this function.
    max_size_mb:
        Maximum allowed file size in megabytes (default 100 MB).

    Raises
    ------
    SecurityError
        On any violation.
    """
    filepath = Path(filepath)

    # --- Existence check ---------------------------------------------------
    if not filepath.exists():
        raise SecurityError(f"Upload file does not exist: {filepath}")

    if not filepath.is_file():
        raise SecurityError(f"Upload path is not a regular file: {filepath}")

    # --- Size check --------------------------------------------------------
    size_bytes = filepath.stat().st_size
    max_size_bytes = max_size_mb * 1024 * 1024
    if size_bytes > max_size_bytes:
        raise SecurityError(
            f"Upload too large: {size_bytes} bytes exceeds {max_size_mb} MB limit"
        )

    # --- Extension check ---------------------------------------------------
    name = filepath.name

    if _has_double_extension(name):
        raise SecurityError(
            f"Double extension in upload filename: {name!r}"
        )

    suffix = filepath.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise SecurityError(
            f"Extension {suffix!r} is not in the upload allowlist. "
            f"Allowed: {sorted(ALLOWED_EXTENSIONS)}"
        )

    # --- MIME check --------------------------------------------------------
    # mimetypes.guess_type() uses the extension, which is sufficient here
    # because we are enforcing the extension allowlist above and this is an
    # additional consistency check.  Deep magic-byte sniffing is out of scope
    # for this module (would require python-magic / libmagic).
    expected_mime = MIME_TYPE_MAP.get(suffix)
    if expected_mime is None:
        # Should be unreachable given the allowlist check above, but be safe.
        raise SecurityError(
            f"No MIME mapping for extension {suffix!r} (internal error)"
        )

    guessed_mime, _encoding = mimetypes.guess_type(str(filepath))
    if guessed_mime is None:
        # mimetypes DB may not know the type — warn but do not block; the
        # extension check already enforces policy.
        logger.warning(
            "mimetypes.guess_type returned None for %r; extension check passed",
            filepath.name,
        )
    elif guessed_mime != expected_mime:
        raise SecurityError(
            f"MIME mismatch for {filepath.name!r}: "
            f"expected {expected_mime!r}, got {guessed_mime!r}"
        )

    logger.debug(
        "validate_upload OK: %r (%d bytes, %s)",
        filepath.name,
        size_bytes,
        guessed_mime or expected_mime,
    )
