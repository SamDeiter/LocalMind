import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.server import app

client = TestClient(app)

@pytest.fixture
def mock_notifications():
    with patch("backend.routes.settings.notifications") as mock:
        mock.get_settings.return_value = {
            "enabled": True,
            "smtp_user": "user@example.com",
            "smtp_pass": "secret_smtp",
            "twilio_auth_token": "secret_twilio"
        }
        yield mock

@pytest.fixture
def mock_gemini():
    with patch("backend.routes.settings.gemini_client") as mock:
        mock.get_settings.return_value = {
            "api_key": "secret_gemini_key"
        }
        yield mock

def test_get_notification_settings_masking(mock_notifications):
    response = client.get("/api/settings/notifications")
    assert response.status_code == 200
    data = response.json()
    assert data["smtp_pass"] == "****"
    assert data["twilio_auth_token"] == "****"
    assert data["smtp_user"] == "user@example.com"

def test_get_cloud_settings_masking(mock_gemini):
    response = client.get("/api/settings/cloud")
    assert response.status_code == 200
    data = response.json()
    assert data["api_key"] == "****"

def test_update_notification_settings_preservation(mock_notifications):
    # Simulate sending back masked values
    payload = {
        "enabled": True,
        "smtp_user": "new_user@example.com",
        "smtp_pass": "****",
        "twilio_auth_token": "****"
    }
    response = client.post("/api/settings/notifications", json=payload)
    assert response.status_code == 200

    # Verify save_settings was called with original secrets restored
    mock_notifications.save_settings.assert_called_once()
    saved_args = mock_notifications.save_settings.call_args[0][0]
    assert saved_args["smtp_pass"] == "secret_smtp"
    assert saved_args["twilio_auth_token"] == "secret_twilio"
    assert saved_args["smtp_user"] == "new_user@example.com"

def test_update_cloud_settings_preservation(mock_gemini):
    # Simulate sending back masked value
    payload = {
        "api_key": "****"
    }
    response = client.post("/api/settings/cloud", json=payload)
    assert response.status_code == 200

    # Verify save_settings was called with original secret restored
    mock_gemini.save_settings.assert_called_once()
    saved_args = mock_gemini.save_settings.call_args[0][0]
    assert saved_args["api_key"] == "secret_gemini_key"

def test_update_settings_with_new_values(mock_notifications, mock_gemini):
    # Test updating with actual new values (not masks)
    notif_payload = {
        "enabled": True,
        "smtp_pass": "new_secret_smtp",
        "twilio_auth_token": "new_secret_twilio"
    }
    client.post("/api/settings/notifications", json=notif_payload)
    mock_notifications.save_settings.assert_called_with(notif_payload)

    cloud_payload = {
        "api_key": "new_gemini_key"
    }
    client.post("/api/settings/cloud", json=cloud_payload)
    mock_gemini.save_settings.assert_called_with(cloud_payload)
