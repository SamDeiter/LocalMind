"""
Gemini Client — Thin wrapper around Google's Generative AI SDK.
Handles API calls to Gemini models with PII scrubbing.

Privacy: All prompts are scrubbed of personal information before
being sent to Google's servers. The user must approve cloud usage
via the propose_action tool first.
"""

import logging
import os
import re
from typing import Optional

logger = logging.getLogger("localmind.gemini")


# ── PII Scrubber ────────────────────────────────────────────────────────
# Patterns to strip before sending data to cloud
_PII_PATTERNS = [
    # Email addresses
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), '[EMAIL_REDACTED]'),
    # Phone numbers (US formats)
    (re.compile(r'\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[PHONE_REDACTED]'),
    # SSN
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN_REDACTED]'),
    # Credit card numbers (basic)
    (re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'), '[CARD_REDACTED]'),
    # IP addresses
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[IP_REDACTED]'),
    # Windows file paths with usernames — use lambda to avoid \U escape in re.sub
    (re.compile(r'C:\\Users\\[^\\]+', re.IGNORECASE), lambda m: 'C:\\Users\\[USER_REDACTED]'),
    # Unix home dirs
    (re.compile(r'/home/[^/\s]+'), '/home/[USER_REDACTED]'),
    # API keys (generic patterns) — {35,} to match keys >= 35 chars
    (re.compile(r'\b(AIza[A-Za-z0-9_-]{35,})\b'), '[API_KEY_REDACTED]'),
    (re.compile(r'\b(sk-[a-zA-Z0-9]{20,})\b'), '[API_KEY_REDACTED]'),
]


def scrub_pii(text: str) -> str:
    """Remove personal identifiable information from text before cloud send."""
    scrubbed = text
    for pattern, replacement in _PII_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


# ── Gemini API Client ──────────────────────────────────────────────────
_client = None


def _get_api_key() -> Optional[str]:
    """Get Gemini API key from environment."""
    return os.environ.get("GEMINI_API_KEY")


def _ensure_client():
    """Lazy-init the Gemini client."""
    global _client
    if _client is not None:
        return _client

    api_key = _get_api_key()
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY not found in environment. "
            "Get a free key at https://aistudio.google.com/apikey "
            "and add it to your .env file."
        )

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        _client = genai
        logger.info("Gemini client initialized successfully")
        return _client
    except ImportError:
        raise ImportError(
            "google-generativeai package not installed. "
            "Run: pip install google-generativeai"
        )


async def generate(
    prompt: str,
    model: str = "gemini-2.0-flash",
    system_instruction: str = "",
    scrub: bool = True,
) -> str:
    """Generate a response from Gemini.
    
    Args:
        prompt: The user/system prompt to send
        model: Gemini model name (default: gemini-2.0-flash)
        system_instruction: Optional system prompt
        scrub: Whether to scrub PII before sending (default: True)
    
    Returns:
        The generated text response
    """
    client = _ensure_client()

    # Scrub PII if enabled
    clean_prompt = scrub_pii(prompt) if scrub else prompt
    clean_system = scrub_pii(system_instruction) if (scrub and system_instruction) else system_instruction

    try:
        gen_model = client.GenerativeModel(
            model_name=model,
            system_instruction=clean_system or None,
        )
        response = gen_model.generate_content(clean_prompt)
        return response.text
    except Exception as exc:
        logger.error(f"Gemini generation failed: {exc}")
        raise


def is_available() -> bool:
    """Check if Gemini is configured and available."""
    return _get_api_key() is not None
