import pytest
from unittest.mock import patch
from backend.routes import settings
from backend import notifications

@pytest.fixture
def mock_settings_file(tmp_path):
    mock_path = tmp_path / "notification_settings.json"
    with patch("backend.notifications.SETTINGS_FILE", mock_path):
        yield mock_path

@pytest.mark.asyncio
async def test_get_notification_settings_masks_password(mock_settings_file):
    secret = "my_secret_password_123"
    notifications.save_settings({
        "enabled": True,
        "smtp_pass": secret
    })

    resp = await settings.get_notification_settings()
    assert resp["smtp_pass"].startswith("****")
    assert resp["smtp_pass"] != secret
    assert resp["smtp_pass"].endswith("_123")

@pytest.mark.asyncio
async def test_update_notification_settings_preserves_masked_password(mock_settings_file):
    secret = "my_secret_password_123"
    notifications.save_settings({
        "enabled": True,
        "smtp_pass": secret
    })

    # Get the masked password
    resp_get = await settings.get_notification_settings()
    masked_pw = resp_get["smtp_pass"]

    # Update other fields but pass back masked password
    update_data = {
        "enabled": False,
        "smtp_pass": masked_pw
    }
    resp_post = await settings.update_notification_settings(update_data)

    # Verify original secret is still in the real settings
    saved = notifications.get_settings()
    assert saved["smtp_pass"] == secret
    assert saved["enabled"] is False

    # NEW: Verify that the POST response itself was masked
    assert "settings" in resp_post
    assert resp_post["settings"]["smtp_pass"].startswith("****")
    assert resp_post["settings"]["smtp_pass"] != secret

@pytest.mark.asyncio
async def test_update_notification_settings_handles_none_password(mock_settings_file):
    notifications.save_settings({
        "enabled": True,
        "smtp_pass": "some_pass"
    })

    update_data = {
        "enabled": False,
        "smtp_pass": None
    }
    # This should not crash
    resp = await settings.update_notification_settings(update_data)
    assert resp["status"] == "ok"

    saved = notifications.get_settings()
    assert saved["smtp_pass"] is None
    assert saved["enabled"] is False

@pytest.mark.asyncio
async def test_update_notification_settings_allows_new_password(mock_settings_file):
    notifications.save_settings({
        "enabled": True,
        "smtp_pass": "old_pass"
    })

    new_secret = "brand_new_password"
    update_data = {
        "enabled": True,
        "smtp_pass": new_secret
    }
    await settings.update_notification_settings(update_data)

    saved = notifications.get_settings()
    assert saved["smtp_pass"] == new_secret
