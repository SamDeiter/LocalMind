import pytest
from unittest.mock import MagicMock, patch
from backend.routes.settings import get_notification_settings, update_notification_settings

@pytest.mark.asyncio
async def test_get_notification_settings_masking():
    # Mock settings with secrets
    mock_settings = {
        "enabled": True,
        "smtp_pass": "super-secret-password",
        "twilio_auth_token": "long-twilio-token-12345",
        "phone": "1234567890"
    }

    with patch("backend.notifications.get_settings", return_value=mock_settings):
        response = await get_notification_settings()

        # Verify secrets are masked but other fields are preserved
        assert response["enabled"] is True
        assert response["phone"] == "1234567890"
        # "super-secret-password" is 21 chars. Mask = 17 * '*' + 'word'
        assert response["smtp_pass"] == "*" * 17 + "word"
        # "long-twilio-token-12345" is 23 chars. Mask = 19 * '*' + '2345'
        assert response["twilio_auth_token"] == "*" * 19 + "2345"

        # IMPORTANT: Verify original mock_settings was NOT mutated
        assert mock_settings["smtp_pass"] == "super-secret-password"
        assert mock_settings["twilio_auth_token"] == "long-twilio-token-12345"

@pytest.mark.asyncio
async def test_update_notification_settings_preservation():
    # Existing settings with secrets
    current_settings = {
        "enabled": True,
        "smtp_pass": "original-secret",
        "twilio_auth_token": "original-token"
    }

    # Incoming settings from frontend with placeholders
    incoming_settings = {
        "enabled": False,
        "smtp_pass": "****",
        "twilio_auth_token": "********token", # also starts with ****
        "phone": "9999999999"
    }

    with patch("backend.notifications.get_settings", return_value=current_settings), \
         patch("backend.notifications.save_settings") as mock_save:

        response = await update_notification_settings(incoming_settings)

        # Verify save_settings was called with the ORIGINAL secrets restored
        saved_data = mock_save.call_args[0][0]
        assert saved_data["smtp_pass"] == "original-secret"
        assert saved_data["twilio_auth_token"] == "original-token"
        assert saved_data["enabled"] is False
        assert saved_data["phone"] == "9999999999"

        # Verify response is masked
        assert response["status"] == "ok"
        # "original-secret" (15 chars) -> 11 * '*' + 'cret'
        assert response["settings"]["smtp_pass"] == "*" * 11 + "cret"
        # "original-token" (14 chars) -> 10 * '*' + 'oken'
        assert response["settings"]["twilio_auth_token"] == "*" * 10 + "oken"

        # Verify original incoming_settings was NOT mutated (it was restored, but saved_data is what matters)
        # Actually update_notification_settings(settings) DOES mutate the input 'settings' dict by design
        # to restore secrets before saving. This is generally acceptable for a POST body dict.

@pytest.mark.asyncio
async def test_update_notification_settings_new_values():
    current_settings = {
        "smtp_pass": "old-pass",
    }
    incoming_settings = {
        "smtp_pass": "new-pass-123456",
    }

    with patch("backend.notifications.get_settings", return_value=current_settings), \
         patch("backend.notifications.save_settings") as mock_save:

        await update_notification_settings(incoming_settings)

        saved_data = mock_save.call_args[0][0]
        assert saved_data["smtp_pass"] == "new-pass-123456"
