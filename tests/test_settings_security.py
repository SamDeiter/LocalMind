import pytest
import json
from unittest.mock import patch, MagicMock
from backend.routes.settings import get_notification_settings, get_cloud_settings, update_notification_settings, update_cloud_settings

@pytest.mark.asyncio
async def test_notification_settings_masking():
    """Verify that sensitive notification settings are masked in GET and preserved in POST."""
    mock_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "smtp_user": "user@example.com",
        "smtp_pass": "secret_password",
        "twilio_sid": "AC123",
        "twilio_auth_token": "secret_token"
    }

    with patch("backend.notifications.get_settings", return_value=mock_settings.copy()):
        # CURRENT BEHAVIOR (Expected to FAIL after fix, currently might pass if unmasked)
        settings = await get_notification_settings()
        # If the fix is NOT applied, these will be plaintext
        # We want them to be masked
        assert settings["smtp_pass"].startswith("****")
        assert settings["twilio_auth_token"].startswith("****")
        assert settings["smtp_pass"] != "secret_password"
        assert settings["twilio_auth_token"] != "secret_token"

@pytest.mark.asyncio
async def test_notification_settings_preservation():
    """Verify that masked values are preserved correctly on update."""
    original_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "smtp_user": "user@example.com",
        "smtp_pass": "secret_password",
        "twilio_sid": "AC123",
        "twilio_auth_token": "secret_token"
    }

    masked_update = {
        "enabled": True,
        "method": "email-to-sms",
        "smtp_user": "user@example.com",
        "smtp_pass": "****word", # Masked
        "twilio_sid": "AC123",
        "twilio_auth_token": "********" # Masked
    }

    with patch("backend.notifications.get_settings", return_value=original_settings.copy()), \
         patch("backend.notifications.save_settings") as mock_save:

        await update_notification_settings(masked_update)

        mock_save.assert_called_once()
        saved_data = mock_save.call_args[0][0]
        # Should have restored original secrets
        assert saved_data["smtp_pass"] == "secret_password"
        assert saved_data["twilio_auth_token"] == "secret_token"

@pytest.mark.asyncio
async def test_cloud_settings_masking_and_preservation():
    """Verify cloud settings masking and preservation."""
    mock_settings = {"api_key": "sk-1234567890"}

    with patch("backend.gemini_client.get_settings", return_value=mock_settings.copy()), \
         patch("backend.gemini_client.save_settings") as mock_save:

        # Test masking
        settings = await get_cloud_settings()
        assert settings["api_key"].startswith("****")
        assert settings["api_key"] != "sk-1234567890"

        # Test preservation
        update_data = {"api_key": "********"}
        await update_cloud_settings(update_data)

        mock_save.assert_called_once()
        saved_data = mock_save.call_args[0][0]
        assert saved_data["api_key"] == "sk-1234567890"
