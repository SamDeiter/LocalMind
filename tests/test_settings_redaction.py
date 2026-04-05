import pytest
from unittest.mock import patch, MagicMock
from backend.routes.settings import get_notification_settings, update_notification_settings, get_cloud_settings, update_cloud_settings

@pytest.mark.asyncio
async def test_notification_settings_redaction():
    """Test that sensitive notification settings are redacted."""
    mock_settings = {
        "enabled": True,
        "twilio_auth_token": "secret_token_123456789",
        "smtp_pass": "my_secure_password",
        "phone": "+1234567890"
    }

    with patch("backend.notifications.get_settings", return_value=mock_settings):
        redacted = await get_notification_settings()
        assert redacted["enabled"] is True
        assert redacted["phone"] == "+1234567890"
        assert redacted["twilio_auth_token"].startswith("****")
        assert redacted["twilio_auth_token"].endswith("6789")
        assert redacted["smtp_pass"].startswith("****")
        assert redacted["smtp_pass"].endswith("word")

@pytest.mark.asyncio
async def test_notification_settings_masked_update():
    """Test that masked notification settings do not overwrite existing secrets."""
    current_settings = {
        "enabled": True,
        "twilio_auth_token": "original_token",
        "smtp_pass": "original_pass"
    }

    incoming_settings = {
        "enabled": False,
        "twilio_auth_token": "********ken", # Masked
        "smtp_pass": "new_pass" # Changed
    }

    with patch("backend.notifications.get_settings", return_value=current_settings):
        with patch("backend.notifications.save_settings") as mock_save:
            await update_notification_settings(incoming_settings)

            # Should have preserved original token but updated pass
            saved_data = mock_save.call_args[0][0]
            assert saved_data["enabled"] is False
            assert saved_data["twilio_auth_token"] == "original_token"
            assert saved_data["smtp_pass"] == "new_pass"

@pytest.mark.asyncio
async def test_cloud_settings_redaction():
    """Test that Gemini API key is redacted."""
    mock_settings = {"api_key": "AIzaSy_test_key_12345"}

    with patch("backend.gemini_client.get_settings", return_value=mock_settings):
        redacted = await get_cloud_settings()
        assert redacted["api_key"].startswith("****")
        assert redacted["api_key"].endswith("2345")

@pytest.mark.asyncio
async def test_cloud_settings_masked_update():
    """Test that masked cloud settings do not overwrite existing API key."""
    current_settings = {"api_key": "original_api_key"}
    incoming_settings = {"api_key": "********key"}

    with patch("backend.gemini_client.get_settings", return_value=current_settings):
        with patch("backend.gemini_client.save_settings") as mock_save:
            await update_cloud_settings(incoming_settings)

            saved_data = mock_save.call_args[0][0]
            assert saved_data["api_key"] == "original_api_key"
