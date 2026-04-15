import pytest
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.server import app

client = TestClient(app)

@pytest.fixture
def mock_settings(tmp_path):
    notif_file = tmp_path / "notification_settings.json"
    cloud_file = tmp_path / "cloud_settings.json"

    with patch("backend.notifications.SETTINGS_FILE", notif_file), \
         patch("backend.gemini_client.CLOUD_SETTINGS_FILE", cloud_file):
        yield notif_file, cloud_file

def test_notification_settings_masking(mock_settings):
    notif_file, _ = mock_settings
    original_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "supersecretpassword",
        "twilio_sid": "AC12345",
        "twilio_auth_token": "secret_token"
    }
    notif_file.write_text(json.dumps(original_settings))

    # Mock authentication to bypass it
    with patch("backend.security.auth.authenticate_request", return_value={"role": "admin"}):
        response = client.get("/api/settings/notifications")
        assert response.status_code == 200
        data = response.json()

        # Verify sensitive fields are masked
        assert data["smtp_pass"] == "****"
        assert data["twilio_auth_token"] == "****"
        # Non-sensitive fields should be intact
        assert data["phone"] == "1234567890"

def test_notification_settings_preservation(mock_settings):
    notif_file, _ = mock_settings
    original_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "supersecretpassword",
        "twilio_sid": "AC12345",
        "twilio_auth_token": "secret_token"
    }
    notif_file.write_text(json.dumps(original_settings))

    updated_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "0987654321",
        "carrier": "att",
        "smtp_user": "newuser@example.com",
        "smtp_pass": "****", # Masked value sent from UI
        "twilio_sid": "AC12345",
        "twilio_auth_token": "****" # Masked value sent from UI
    }

    with patch("backend.security.auth.authenticate_request", return_value={"role": "admin"}):
        response = client.post("/api/settings/notifications", json=updated_settings)
        assert response.status_code == 200

        # Verify original secrets are preserved
        saved_settings = json.loads(notif_file.read_text())
        assert saved_settings["smtp_pass"] == "supersecretpassword"
        assert saved_settings["twilio_auth_token"] == "secret_token"
        assert saved_settings["phone"] == "0987654321"

def test_cloud_settings_masking(mock_settings):
    _, cloud_file = mock_settings
    original_settings = {
        "api_key": "AIzaSy_very_secret_key"
    }
    cloud_file.write_text(json.dumps(original_settings))

    with patch("backend.security.auth.authenticate_request", return_value={"role": "admin"}):
        response = client.get("/api/settings/cloud")
        assert response.status_code == 200
        data = response.json()

        # Verify sensitive fields are masked
        assert data["api_key"] == "****"

def test_cloud_settings_preservation(mock_settings):
    _, cloud_file = mock_settings
    original_settings = {
        "api_key": "AIzaSy_very_secret_key"
    }
    cloud_file.write_text(json.dumps(original_settings))

    updated_settings = {
        "api_key": "****" # Masked value sent from UI
    }

    with patch("backend.security.auth.authenticate_request", return_value={"role": "admin"}):
        response = client.post("/api/settings/cloud", json=updated_settings)
        assert response.status_code == 200

        # Verify original secrets are preserved
        saved_settings = json.loads(cloud_file.read_text())
        assert saved_settings["api_key"] == "AIzaSy_very_secret_key"

def test_google_api_key_scrubbing():
    from backend.security.prompt_guard import PromptGuard
    guard = PromptGuard("strict")
    text = "My Google key is AIzaSyA1234567890abcdefghijklmnopqrstuvwx"
    result = guard.validate_output(text)
    assert "[REDACTED]" in result.cleaned_text
    assert "AIzaSy" not in result.cleaned_text
