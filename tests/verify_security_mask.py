
import asyncio
import unittest
from unittest.mock import MagicMock, patch
from backend.routes.settings import get_notification_settings, update_notification_settings

class TestSecurityMask(unittest.IsolatedAsyncioTestCase):

    @patch("backend.notifications.get_settings")
    async def test_get_notification_settings_masks_password(self, mock_get):
        # Setup
        mock_get.return_value = {
            "enabled": True,
            "smtp_pass": "secret123",
            "smtp_user": "user@example.com"
        }

        # Execute
        result = await get_notification_settings()

        # Verify
        self.assertEqual(result["smtp_pass"], "****")
        self.assertEqual(result["smtp_user"], "user@example.com")
        # Ensure original mock wasn't mutated
        self.assertEqual(mock_get.return_value["smtp_pass"], "secret123")

    @patch("backend.notifications.save_settings")
    @patch("backend.notifications.get_settings")
    async def test_update_notification_settings_preserves_masked_password_and_masks_response(self, mock_get, mock_save):
        # Setup
        mock_get.return_value = {
            "enabled": True,
            "smtp_pass": "secret123",
            "smtp_user": "user@example.com"
        }

        incoming_settings = {
            "enabled": True,
            "smtp_pass": "****",
            "smtp_user": "new_user@example.com"
        }

        # Execute
        result = await update_notification_settings(incoming_settings)

        # Verify
        mock_save.assert_called_once()
        saved_settings = mock_save.call_args[0][0]
        self.assertEqual(saved_settings["smtp_pass"], "secret123")
        self.assertEqual(saved_settings["smtp_user"], "new_user@example.com")

        # Verify response masking
        self.assertEqual(result["settings"]["smtp_pass"], "****")

    @patch("backend.notifications.save_settings")
    @patch("backend.notifications.get_settings")
    async def test_update_notification_settings_allows_new_password_and_masks_response(self, mock_get, mock_save):
        # Setup
        mock_get.return_value = {
            "enabled": True,
            "smtp_pass": "secret123"
        }

        incoming_settings = {
            "enabled": True,
            "smtp_pass": "new_secret_456"
        }

        # Execute
        result = await update_notification_settings(incoming_settings)

        # Verify
        mock_save.assert_called_once()
        saved_settings = mock_save.call_args[0][0]
        self.assertEqual(saved_settings["smtp_pass"], "new_secret_456")

        # Verify response masking
        self.assertEqual(result["settings"]["smtp_pass"], "****")

if __name__ == "__main__":
    unittest.main()
