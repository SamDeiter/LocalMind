import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from backend.server import app

client = TestClient(app)

@pytest.fixture
def mock_auth():
    with patch("backend.security.auth.authenticate_request") as mock_auth:
        mock_auth.return_value = {"user_id": "test_user", "role": "admin", "key_id": "test_key"}
        yield mock_auth

@pytest.fixture
def mock_rbac():
    with patch("backend.security.rbac.check_permission") as mock_rbac:
        yield mock_rbac

def test_notification_settings_masked(mock_auth, mock_rbac):
    """Verify that sensitive notification settings are masked."""
    test_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "supersecretpassword",
        "twilio_sid": "AC12345",
        "twilio_auth_token": "secret_token"
    }

    with patch("backend.notifications.get_settings", return_value=test_settings):
        response = client.get("/api/settings/notifications")
        assert response.status_code == 200
        data = response.json()

        assert data["smtp_pass"] == "****"
        assert data["twilio_auth_token"] == "****"
        assert data["twilio_sid"] == "****"
        assert data["phone"] == "1234567890"  # Non-sensitive preserved

def test_cloud_settings_masked(mock_auth, mock_rbac):
    """Verify that cloud settings (Gemini API key) are fully masked."""
    test_settings = {
        "api_key": "AIzaSy_ActualKey12345"
    }

    with patch("backend.gemini_client.get_settings", return_value=test_settings):
        response = client.get("/api/settings/cloud")
        assert response.status_code == 200
        data = response.json()

        assert data["api_key"] == "****"

def test_notification_settings_preservation(mock_auth, mock_rbac):
    """Verify that masked placeholders are preserved during update."""
    current_settings = {
        "enabled": True,
        "smtp_pass": "original_secret",
        "phone": "1234567890"
    }

    incoming_settings = {
        "enabled": True,
        "smtp_pass": "****",  # Masked placeholder
        "phone": "0987654321" # Updated non-sensitive field
    }

    with patch("backend.notifications.get_settings", return_value=current_settings), \
         patch("backend.notifications.save_settings") as mock_save:

        response = client.post("/api/settings/notifications", json=incoming_settings)
        assert response.status_code == 200

        # Verify that original_secret was restored
        saved_args = mock_save.call_args[0][0]
        assert saved_args["smtp_pass"] == "original_secret"
        assert saved_args["phone"] == "0987654321"
