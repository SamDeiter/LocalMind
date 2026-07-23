"""
Log redaction filter -- strips secrets from all log output.

Installs a ``logging.Filter`` on the root logger (and all existing handlers)
that scrubs known secret patterns before any message is emitted.  Reuses the
compiled ``SECRET_PATTERNS`` from ``prompt_guard.py`` to stay in sync.

Usage::

    from backend.security.redact import install_redacting_filter
    install_redacting_filter()  # call once at startup
"""

from __future__ import annotations

import logging
import math
import re

# ---------------------------------------------------------------------------
# Import secret patterns from prompt_guard (single source of truth)
# ---------------------------------------------------------------------------
from backend.security.prompt_guard import (
    _HIGH_ENTROPY_THRESHOLD,
    _MIN_GENERIC_TOKEN_LEN,
    SECRET_PATTERNS,
)

logger = logging.getLogger("localmind.security.redact")

# ---------------------------------------------------------------------------
# Extra patterns not in prompt_guard (password fields, bearer tokens, etc.)
# ---------------------------------------------------------------------------

_EXTRA_SECRET_RAW: list[str] = [
    # Authorization headers
    r"(?i:Bearer\s+)[A-Za-z0-9\-_\.]{20,}",
    # password=..., passwd=..., secret=... in key=value contexts
    r"(?i:(?:password|passwd|secret|token|api_key|apikey)\s*[=:]\s*)\S+",
]

_EXTRA_PATTERNS: list[re.Pattern] = [
    re.compile(p) for p in _EXTRA_SECRET_RAW
]

_REDACT_PLACEHOLDER = "[REDACTED]"


# ---------------------------------------------------------------------------
# Entropy helper (duplicated inline to avoid circular import issues)
# ---------------------------------------------------------------------------

def _shannon_entropy(text: str) -> float:
    """Compute Shannon entropy (bits) of a string."""
    if not text:
        return 0.0
    freq: dict[str, int] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(text)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in freq.values()
    )


def _is_high_entropy_token(token: str) -> bool:
    """Return True if the token looks like a secret (high entropy, long enough)."""
    if len(token) < _MIN_GENERIC_TOKEN_LEN:
        return False
    return _shannon_entropy(token) >= _HIGH_ENTROPY_THRESHOLD


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def redact_string(text: str) -> str:
    """Scrub all known secret patterns from *text*.

    Applies the same named and generic patterns used by ``prompt_guard.py``
    plus additional patterns for bearer tokens and key=value pairs.

    Parameters
    ----------
    text : str
        Arbitrary text that may contain secrets.

    Returns
    -------
    str
        Text with all secret-looking substrings replaced by ``[REDACTED]``.
    """
    if not text:
        return text

    result = text

    # 1. Named patterns from prompt_guard (all except the last generic one)
    for pat in SECRET_PATTERNS[:-1]:
        result = pat.sub(_REDACT_PLACEHOLDER, result)

    # 2. Generic high-entropy token (last pattern in SECRET_PATTERNS)
    generic_pat = SECRET_PATTERNS[-1]

    def _repl_generic(m: re.Match) -> str:
        token = m.group(1)
        if _is_high_entropy_token(token):
            return _REDACT_PLACEHOLDER
        return m.group(0)

    result = generic_pat.sub(_repl_generic, result)

    # 3. Extra patterns (bearer tokens, password=value, etc.)
    for pat in _EXTRA_PATTERNS:
        result = pat.sub(_REDACT_PLACEHOLDER, result)

    return result


# ---------------------------------------------------------------------------
# Logging filter
# ---------------------------------------------------------------------------

class RedactingFilter(logging.Filter):
    """A ``logging.Filter`` that scrubs secret patterns from log records.

    Attached to the root logger and all its handlers so that no secret
    material is ever written to log files, stderr, or external sinks.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the formatted message and arguments of *record*.

        Always returns ``True`` (we never suppress a record, we only clean it).
        """
        # Redact the message template
        if isinstance(record.msg, str):
            record.msg = redact_string(record.msg)

        # Redact positional args (commonly used by logger.info("x %s", secret))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_string(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_string(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )

        return True


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

_installed: bool = False


def install_redacting_filter() -> None:
    """Add :class:`RedactingFilter` to the root logger and all existing handlers.

    Safe to call multiple times -- only installs once.

    Call this as early as possible in the application lifecycle (before any
    log statements that might include secrets).
    """
    global _installed
    if _installed:
        return

    redacting_filter = RedactingFilter(name="secret_redact")
    root = logging.getLogger()

    # Attach to the root logger itself
    root.addFilter(redacting_filter)

    # Also attach to every handler currently registered (belt + suspenders)
    for handler in root.handlers:
        handler.addFilter(redacting_filter)

    _installed = True
    logger.info("Redacting log filter installed on root logger and %d handlers", len(root.handlers))
