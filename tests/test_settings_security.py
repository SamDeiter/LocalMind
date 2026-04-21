import pytest
import json
from unittest.mock import MagicMock, patch
from backend.routes import settings
from backend import notifications, gemini_client

@pytest.mark.asyncio
async def test_notification_settings_masking():
    """Verify that sensitive notification settings are masked in GET responses."""
    dummy_notif = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "SECRET_PASSWORD_123",
        "twilio_auth_token": "TWILIO_SECRET_456"
    }
    with patch("backend.notifications.get_settings", return_value=dummy_notif):
        leaked_notif = await settings.get_notification_settings()

        assert leaked_notif["smtp_pass"] == "********"
        assert leaked_notif["twilio_auth_token"] == "********"
        assert leaked_notif["phone"] == "1234567890"

@pytest.mark.asyncio
async def test_notification_settings_preservation():
    """Verify that masked secrets are preserved during POST updates."""
    current_notif = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "REAL_SECRET",
        "twilio_auth_token": "REAL_TWILIO"
    }

    incoming_notif = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "9999999999",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "********",
        "twilio_auth_token": "********"
    }

    with patch("backend.notifications.get_settings", return_value=current_notif), \
         patch("backend.notifications.save_settings") as mock_save:

        await settings.update_notification_settings(incoming_notif)

        # Verify what was saved
        saved_call_args = mock_save.call_args[0][0]
        assert saved_call_args["smtp_pass"] == "REAL_SECRET"
        assert saved_call_args["twilio_auth_token"] == "REAL_TWILIO"
        assert saved_call_args["phone"] == "9999999999"

@pytest.mark.asyncio
async def test_cloud_settings_masking():
    """Verify that Gemini API key is masked in GET responses."""
    dummy_cloud = {"api_key": "AIza-TEST-KEY"}
    with patch("backend.gemini_client.get_settings", return_value=dummy_cloud):
        leaked_cloud = await settings.get_cloud_settings()
        assert leaked_cloud["api_key"] == "********"

@pytest.mark.asyncio
async def test_cloud_settings_preservation():
    """Verify that masked Gemini API key is preserved during POST updates."""
    current_cloud = {"api_key": "REAL_AIZA_KEY"}
    incoming_cloud = {"api_key": "********"}

    with patch("backend.gemini_client.get_settings", return_value=current_cloud), \
         patch("backend.gemini_client.save_settings") as mock_save:

        await settings.update_cloud_settings(incoming_cloud)

        saved_call_args = mock_save.call_args[0][0]
        assert saved_call_args["api_key"] == "REAL_AIZA_KEY"
