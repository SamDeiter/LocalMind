"""
Tests for the Gemini client — PII scrubber and availability check.
"""
import os
from unittest.mock import patch

import pytest

from backend.gemini_client import scrub_pii, is_available


class TestPIIScrubber:
    """Verify that personal data is redacted before cloud sends."""

    def test_email_redacted(self):
        text = "Send it to sam.deiter@gmail.com please"
        assert "[EMAIL_REDACTED]" in scrub_pii(text)
        assert "sam.deiter@gmail.com" not in scrub_pii(text)

    def test_phone_redacted(self):
        assert "[PHONE_REDACTED]" in scrub_pii("Call me at 555-123-4567")
        assert "[PHONE_REDACTED]" in scrub_pii("Call me at (555) 123-4567")
        assert "[PHONE_REDACTED]" in scrub_pii("Call me at +1-555-123-4567")

    def test_ssn_redacted(self):
        assert "[SSN_REDACTED]" in scrub_pii("My SSN is 123-45-6789")

    def test_credit_card_redacted(self):
        assert "[CARD_REDACTED]" in scrub_pii("Card: 4111-1111-1111-1111")
        assert "[CARD_REDACTED]" in scrub_pii("Card: 4111 1111 1111 1111")

    def test_ip_address_redacted(self):
        assert "[IP_REDACTED]" in scrub_pii("Server at 192.168.1.100")

    def test_windows_path_redacted(self):
        result = scrub_pii(r"File at C:\Users\SamDeiter\Documents\secret.txt")
        assert "[USER_REDACTED]" in result
        assert "SamDeiter" not in result

    def test_unix_path_redacted(self):
        result = scrub_pii("File at /home/samdeiter/docs/secret.txt")
        assert "[USER_REDACTED]" in result
        assert "samdeiter" not in result

    def test_api_key_redacted(self):
        # Google API key pattern
        assert "[API_KEY_REDACTED]" in scrub_pii(
            "Key: AIzaSyA1234567890abcdefghijklmnopqrstuvwx"
        )

    def test_openai_key_redacted(self):
        assert "[API_KEY_REDACTED]" in scrub_pii(
            "Key: sk-abcdefghijklmnopqrstuvwxyz"
        )

    def test_clean_text_unchanged(self):
        clean = "Write a Python function that sorts a list"
        assert scrub_pii(clean) == clean

    def test_multiple_pii_all_redacted(self):
        text = "Email sam@test.com, call 555-123-4567, SSN 123-45-6789"
        result = scrub_pii(text)
        assert "[EMAIL_REDACTED]" in result
        assert "[PHONE_REDACTED]" in result
        assert "[SSN_REDACTED]" in result


class TestIsAvailable:
    """Verify Gemini availability detection."""

    def test_available_with_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key-123"}):
            assert is_available() is True

    def test_unavailable_without_key(self):
        env = os.environ.copy()
        env.pop("GEMINI_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            assert is_available() is False
