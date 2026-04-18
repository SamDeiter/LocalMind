import pytest
from unittest.mock import patch, MagicMock
from backend.routes.settings import _mask_settings, get_notification_settings, update_notification_settings, get_cloud_settings, update_cloud_settings
from backend.security.prompt_guard import _scrub_secrets

def test_mask_settings_redacts_sensitive_fields():
    settings = {
        "api_key": "secret-api-key",
        "smtp_pass": "secret-smtp-pass",
        "twilio_auth_token": "secret-twilio-token",
        "other_field": "public-info"
    }
    masked = _mask_settings(settings)
    assert masked["api_key"] == "****"
    assert masked["smtp_pass"] == "****"
    assert masked["twilio_auth_token"] == "****"
    assert masked["other_field"] == "public-info"
    # Ensure original is not mutated
    assert settings["api_key"] == "secret-api-key"

def test_mask_settings_handles_missing_fields():
    settings = {"other_field": "public-info"}
    masked = _mask_settings(settings)
    assert masked == settings

@pytest.mark.asyncio
@patch("backend.notifications.get_settings")
async def test_get_notification_settings_is_masked(mock_get):
    mock_get.return_value = {
        "smtp_pass": "supersecret",
        "phone": "123456"
    }
    result = await get_notification_settings()
    assert result["smtp_pass"] == "****"
    assert result["phone"] == "123456"

@pytest.mark.asyncio
@patch("backend.notifications.get_settings")
@patch("backend.notifications.save_settings")
async def test_update_notification_settings_preserves_secrets(mock_save, mock_get):
    mock_get.return_value = {
        "smtp_pass": "original-pass",
        "twilio_auth_token": "original-token"
    }

    # User sends masked values
    new_settings = {
        "smtp_pass": "****",
        "twilio_auth_token": "****",
        "phone": "999999"
    }

    result = await update_notification_settings(new_settings)

    # Verify save_settings was called with original values
    saved = mock_save.call_args[0][0]
    assert saved["smtp_pass"] == "original-pass"
    assert saved["twilio_auth_token"] == "original-token"
    assert saved["phone"] == "999999"

    # Verify return value is masked
    assert result["settings"]["smtp_pass"] == "****"

@pytest.mark.asyncio
@patch("backend.gemini_client.get_settings")
async def test_get_cloud_settings_is_masked(mock_get):
    mock_get.return_value = {"api_key": "ai-key-123"}
    result = await get_cloud_settings()
    assert result["api_key"] == "****"

@pytest.mark.asyncio
@patch("backend.gemini_client.get_settings")
@patch("backend.gemini_client.save_settings")
async def test_update_cloud_settings_preserves_secrets(mock_save, mock_get):
    mock_get.return_value = {"api_key": "original-api-key"}

    new_settings = {"api_key": "****"}
    await update_cloud_settings(new_settings)

    saved = mock_save.call_args[0][0]
    assert saved["api_key"] == "original-api-key"

def test_prompt_guard_scrubs_google_api_key():
    text = "Here is my key: AIzaSyA1234567890abcdefghijklmnopqrstuvwx and some prose."
    cleaned, scrubbed = _scrub_secrets(text)
    assert "AIza" not in cleaned
    assert "[REDACTED]" in cleaned
    # The name in _scrub_secrets is derived from the pattern itself for named patterns:
    # kind: str = pat.pattern[:12]
    # "AIza[a-zA-Z0-9_-]{35}"[:12] -> "AIza[a-zA-Z0"
    assert "aiza[a-za-z0" in str(scrubbed).lower()
