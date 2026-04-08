import pytest
import json
from unittest.mock import patch, MagicMock
from backend.routes.settings import get_notification_settings, update_notification_settings, get_cloud_settings

@pytest.mark.asyncio
async def test_get_notification_settings_masking():
    mock_settings = {"smtp_pass": "secret123", "other": "data"}
    with patch("backend.notifications.get_settings", return_value=mock_settings):
        result = await get_notification_settings()
        assert result["smtp_pass"] == "****"
        assert result["other"] == "data"
        assert mock_settings["smtp_pass"] == "secret123"

@pytest.mark.asyncio
async def test_update_notification_settings_preservation():
    mock_settings = {"smtp_pass": "secret123", "other": "old"}
    new_settings = {"smtp_pass": "****", "other": "new"}

    with patch("backend.notifications.get_settings", return_value=mock_settings), \
         patch("backend.notifications.save_settings") as mock_save:
        result = await update_notification_settings(new_settings)
        mock_save.assert_called_once()
        saved_data = mock_save.call_args[0][0]
        assert saved_data["smtp_pass"] == "secret123"
        assert saved_data["other"] == "new"
        # Verify response masking
        assert result["settings"]["smtp_pass"] == "****"

@pytest.mark.asyncio
async def test_update_notification_settings_change():
    mock_settings = {"smtp_pass": "secret123", "other": "old"}
    new_settings = {"smtp_pass": "newsecret", "other": "new"}

    with patch("backend.notifications.get_settings", return_value=mock_settings), \
         patch("backend.notifications.save_settings") as mock_save:
        result = await update_notification_settings(new_settings)
        mock_save.assert_called_once()
        saved_data = mock_save.call_args[0][0]
        assert saved_data["smtp_pass"] == "newsecret"
        assert saved_data["other"] == "new"
        # Verify response masking
        assert result["settings"]["smtp_pass"] == "****"

@pytest.mark.asyncio
async def test_get_cloud_settings_masking():
    mock_settings = {"api_key": "AIzaSyTest1234567890"}
    with patch("backend.gemini_client.get_settings", return_value=mock_settings):
        result = await get_cloud_settings()
        assert result["api_key"].startswith("****")
        assert result["api_key"].endswith("7890")
        assert mock_settings["api_key"] == "AIzaSyTest1234567890"
