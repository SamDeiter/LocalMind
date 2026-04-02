import pytest
import json
from unittest.mock import patch
from backend.notifications import get_settings, save_settings

@pytest.fixture
def mock_settings_file(tmp_path):
    mock_path = tmp_path / "test_settings.json"
    with patch("backend.notifications.SETTINGS_FILE", mock_path):
        yield mock_path

def test_get_settings_no_file(mock_settings_file):
    """Test get_settings when the file doesn't exist."""
    # Ensure file doesn't exist (mock_settings_file is a fresh tmp_path)
    settings = get_settings()
    # Based on backend/notifications.py implementation:
    # if not SETTINGS_FILE.exists(): return default dict with all keys
    assert settings["enabled"] is False
    assert settings["method"] == "email-to-sms"
    assert settings["phone"] == ""

def test_get_settings_valid_json(mock_settings_file):
    """Test get_settings when the file contains valid JSON."""
    test_data = {"enabled": True, "method": "twilio", "phone": "1234567890"}
    mock_settings_file.write_text(json.dumps(test_data))

    settings = get_settings()
    assert settings == test_data

def test_get_settings_invalid_json(mock_settings_file):
    """Test get_settings when the file contains invalid JSON."""
    mock_settings_file.write_text("{invalid json}")

    settings = get_settings()
    assert settings == {"enabled": False}

def test_save_settings_success(mock_settings_file):
    """Test save_settings correctly writes to the file."""
    test_data = {"enabled": True, "method": "email-to-sms", "phone": "0987654321"}
    save_settings(test_data)

    assert mock_settings_file.exists()
    saved_content = json.loads(mock_settings_file.read_text())
    assert saved_content == test_data
