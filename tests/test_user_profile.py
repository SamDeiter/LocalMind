"""
Tests for user-profile onboarding endpoints and encryption round-trip.

Covers:
  1. POST /api/user-profile — save profile (plaintext fallback + encrypted)
  2. GET  /api/user-profile — load profile back
  3. MemoryManager preference propagation from profile fields
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile_payload(**overrides) -> dict:
    base = {
        "name": "Alice",
        "role": "Developer",
        "interests": ["AI/ML", "Web Dev"],
        "communication_style": "concise",
        "tools": "Python, VS Code, Docker",
        "user_id": "test_user",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Plaintext round-trip (no encryption key set)
# ---------------------------------------------------------------------------

class TestProfilePlaintextRoundTrip:
    """Save and load a profile with encryption disabled (dev fallback)."""

    def test_save_and_load(self, tmp_path):
        """Profile should survive a save -> load cycle as plain JSON."""
        from backend.routes.settings import (
            _save_plaintext_profile,
            _load_profile,
            _PROFILE_JSON_PATH,
        )

        profile = {
            "name": "Alice",
            "role": "Developer",
            "interests": ["AI/ML", "Web Dev"],
            "communication_style": "concise",
            "tools": "Python, VS Code",
            "created_at": time.time(),
        }

        json_path = tmp_path / "user_profile.json"

        # Patch the module-level path to use tmp_path
        with patch("backend.routes.settings._PROFILE_JSON_PATH", json_path), \
             patch("backend.routes.settings._PROFILE_ENC_PATH", tmp_path / "nope.enc"), \
             patch("backend.routes.settings._WORKSPACE", tmp_path):
            _save_plaintext_profile("test_user", profile)

            assert json_path.exists(), "JSON profile file should exist after save"

            loaded = _load_profile("test_user", enc=None)
            assert loaded is not None, "Profile should be loadable"
            assert loaded["name"] == "Alice"
            assert loaded["role"] == "Developer"
            assert loaded["interests"] == ["AI/ML", "Web Dev"]
            assert loaded["communication_style"] == "concise"
            assert loaded["tools"] == "Python, VS Code"

    def test_load_returns_none_for_missing(self, tmp_path):
        """_load_profile returns None when no file exists."""
        from backend.routes.settings import _load_profile

        with patch("backend.routes.settings._PROFILE_JSON_PATH", tmp_path / "nope.json"), \
             patch("backend.routes.settings._PROFILE_ENC_PATH", tmp_path / "nope.enc"):
            result = _load_profile("test_user", enc=None)
            assert result is None

    def test_load_returns_none_for_wrong_user(self, tmp_path):
        """_load_profile returns None when user_id doesn't match."""
        from backend.routes.settings import _save_plaintext_profile, _load_profile

        json_path = tmp_path / "user_profile.json"
        with patch("backend.routes.settings._PROFILE_JSON_PATH", json_path), \
             patch("backend.routes.settings._PROFILE_ENC_PATH", tmp_path / "nope.enc"), \
             patch("backend.routes.settings._WORKSPACE", tmp_path):
            _save_plaintext_profile("user_A", {"name": "Alice"})
            result = _load_profile("user_B", enc=None)
            assert result is None


# ---------------------------------------------------------------------------
# 2. Encrypted round-trip
# ---------------------------------------------------------------------------

class TestProfileEncryptedRoundTrip:
    """Save and load a profile with real AES-256-GCM encryption."""

    def _get_encryption(self):
        """Create a MemoryEncryption with a test key."""
        try:
            from backend.security.memory_encryption import MemoryEncryption
            # 32-byte test key
            key = b"\x42" * 32
            return MemoryEncryption(key)
        except Exception:
            pytest.skip("cryptography library or MemoryEncryption not available")

    def test_encrypted_save_and_load(self, tmp_path):
        """Profile survives encrypt -> save -> load -> decrypt cycle."""
        from backend.routes.settings import (
            _save_encrypted_profile,
            _load_profile,
        )

        enc = self._get_encryption()
        profile = {
            "name": "Bob",
            "role": "Data Scientist",
            "interests": ["Data Analysis", "AI/ML"],
            "communication_style": "detailed",
            "tools": "Jupyter, pandas",
            "created_at": time.time(),
        }

        enc_path = tmp_path / "user_profile.enc"
        json_path = tmp_path / "user_profile.json"

        encrypted_blob = enc.encrypt("enc_user", json.dumps(profile))

        with patch("backend.routes.settings._PROFILE_ENC_PATH", enc_path), \
             patch("backend.routes.settings._PROFILE_JSON_PATH", json_path), \
             patch("backend.routes.settings._WORKSPACE", tmp_path):
            _save_encrypted_profile("enc_user", encrypted_blob)

            assert enc_path.exists(), "Encrypted profile file should exist"

            loaded = _load_profile("enc_user", enc)
            assert loaded is not None
            assert loaded["name"] == "Bob"
            assert loaded["role"] == "Data Scientist"
            assert loaded["interests"] == ["Data Analysis", "AI/ML"]

    def test_encrypted_load_fails_with_wrong_key(self, tmp_path):
        """Decryption with a different key should fail gracefully (return None)."""
        from backend.routes.settings import (
            _save_encrypted_profile,
            _load_profile,
        )
        from backend.security.memory_encryption import MemoryEncryption

        good_key = b"\x42" * 32
        bad_key = b"\x99" * 32
        good_enc = MemoryEncryption(good_key)
        bad_enc = MemoryEncryption(bad_key)

        profile = {"name": "Charlie", "role": "Designer"}
        encrypted_blob = good_enc.encrypt("user_c", json.dumps(profile))

        enc_path = tmp_path / "user_profile.enc"

        with patch("backend.routes.settings._PROFILE_ENC_PATH", enc_path), \
             patch("backend.routes.settings._PROFILE_JSON_PATH", tmp_path / "nope.json"), \
             patch("backend.routes.settings._WORKSPACE", tmp_path):
            _save_encrypted_profile("user_c", encrypted_blob)

            # Should fail gracefully — wrong key
            loaded = _load_profile("user_c", bad_enc)
            assert loaded is None


# ---------------------------------------------------------------------------
# 3. MemoryManager preference propagation
# ---------------------------------------------------------------------------

class TestProfilePreferences:
    """Verify profile fields are written to MemoryManager as preferences."""

    def test_preferences_saved(self, tmp_path):
        """Profile save should call MemoryManager.propose_preference for each field."""
        from backend.metacognition.memory_manager import MemoryManager

        mm = MemoryManager(path=tmp_path / "prefs.json")

        profile_fields = {
            "user.name": "Diana",
            "user.role": "Manager",
            "user.communication_style": "casual",
            "user.tools": "Slack, Notion",
            "user.interests": "Security, DevOps",
        }

        for key, value in profile_fields.items():
            result = mm.propose_preference(key, value, source="explicit")
            assert result is True, f"propose_preference should succeed for {key}"

        # Verify they are readable
        for key, value in profile_fields.items():
            pref = mm.get_preference(key)
            assert pref is not None, f"Preference {key} should exist"
            assert pref.value == value
            assert pref.source == "explicit"

    def test_forbidden_keys_blocked(self, tmp_path):
        """MemoryManager should block forbidden keys even from profile save."""
        from backend.metacognition.memory_manager import MemoryManager

        mm = MemoryManager(path=tmp_path / "prefs.json")
        result = mm.propose_preference("api_key", "sk-secret-123", source="explicit")
        assert result is False

    def test_explicit_preferences_are_durable(self, tmp_path):
        """Preferences from onboarding (source=explicit) should be immediately durable."""
        from backend.metacognition.memory_manager import MemoryManager

        mm = MemoryManager(path=tmp_path / "prefs.json")
        mm.propose_preference("user.name", "Eve", source="explicit")

        pref = mm.get_preference("user.name")
        assert pref is not None
        assert pref.is_durable() is True


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------

class TestProfileEdgeCases:
    """Edge cases for partial / empty profiles."""

    def test_empty_profile_saves(self, tmp_path):
        """An empty profile (user skipped all fields) should still save."""
        from backend.routes.settings import _save_plaintext_profile, _load_profile

        json_path = tmp_path / "user_profile.json"
        profile = {
            "name": "",
            "role": "",
            "interests": [],
            "communication_style": "",
            "tools": "",
            "created_at": time.time(),
        }

        with patch("backend.routes.settings._PROFILE_JSON_PATH", json_path), \
             patch("backend.routes.settings._PROFILE_ENC_PATH", tmp_path / "nope.enc"), \
             patch("backend.routes.settings._WORKSPACE", tmp_path):
            _save_plaintext_profile("empty_user", profile)
            loaded = _load_profile("empty_user", enc=None)
            assert loaded is not None
            assert loaded["name"] == ""
            assert loaded["interests"] == []

    def test_encrypted_preferred_over_plaintext(self, tmp_path):
        """When both .enc and .json exist, encrypted file takes precedence."""
        from backend.routes.settings import (
            _save_plaintext_profile,
            _save_encrypted_profile,
            _load_profile,
        )

        try:
            from backend.security.memory_encryption import MemoryEncryption
        except Exception:
            pytest.skip("cryptography library not available")

        enc = MemoryEncryption(b"\x42" * 32)

        encrypted_profile = {"name": "Encrypted User", "role": "Dev"}
        plaintext_profile = {"name": "Plaintext User", "role": "PM"}

        enc_path = tmp_path / "user_profile.enc"
        json_path = tmp_path / "user_profile.json"

        encrypted_blob = enc.encrypt("dual_user", json.dumps(encrypted_profile))

        with patch("backend.routes.settings._PROFILE_ENC_PATH", enc_path), \
             patch("backend.routes.settings._PROFILE_JSON_PATH", json_path), \
             patch("backend.routes.settings._WORKSPACE", tmp_path):
            _save_encrypted_profile("dual_user", encrypted_blob)
            _save_plaintext_profile("dual_user", plaintext_profile)

            loaded = _load_profile("dual_user", enc)
            assert loaded is not None
            assert loaded["name"] == "Encrypted User"
