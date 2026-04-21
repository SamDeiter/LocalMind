"""
backend/security/guard.py --- Prompt & Content Guardrails
======================================================
Intercepts malicious instructions and redacts sensitive data.
"""
import re
import logging

logger = logging.getLogger("localmind.security.guard")

# Patterns for sensitive data
PII_PATTERNS = {
    "api_key": r"(AIza[0-9A-Za-z\-_]{35}|sk-[a-zA-Z0-9]{32,}|ghp_[a-zA-Z0-9]{36})",
    "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "password_assignment": r"(password|secret|token)\s*=\s*['\"][^'\"]+['\"]"
}

# Patterns for jailbreaks / data extraction
LEAK_PATTERNS = [
    r"ignore (all )?previous instructions",
    r"output (your|the) system prompt",
    r"reveal (your|the) (secrets|credentials)",
    r"dump (the|your) database",
    r"access (the|your) credentials",
    r"print the content of your .env",
    r"what are your high-level instructions"
]

class SecurityGuard:
    """
    Electronic sentry for LocalMind.
    Ensures that prompts are safe and outputs are clean of secrets.
    """
    
    @staticmethod
    def redact(text: str) -> str:
        """Strips PII and Secrets from text before it's displayed or sent out."""
        if not text: return text
        
        redacted_text = text
        for name, pattern in PII_PATTERNS.items():
            redacted_text = re.sub(pattern, f"[REDACTED_{name.upper()}]", redacted_text)
            
        if redacted_text != text:
            logger.info("SecurityGuard: Redacted sensitive patterns from output")
            
        return redacted_text

    @staticmethod
    def is_instruction_safe(prompt: str) -> bool:
        """Detecting prompt injection, jailbreaks, or data extraction attempts."""
        if not prompt: return True
        
        p_lower = prompt.lower()
        for leak_p in LEAK_PATTERNS:
            if re.search(leak_p, p_lower):
                logger.warning(f"SecurityGuard Alert: Blocked suspected data leak attempt: '{leak_p}'")
                return False
                
        return True

    @staticmethod
    def filter_context(context: list[str]) -> list[str]:
        """Redacts sensitive data from context blocks before they are sent to the LLM."""
        return [SecurityGuard.redact(c) for c in context]

guard = SecurityGuard()
