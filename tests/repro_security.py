import json
import os
from pathlib import Path
import sys
import asyncio
from unittest.mock import MagicMock

# Add current directory to sys.path to find 'backend'
sys.path.append(os.getcwd())

from backend.routes import settings

async def repro():
    # Setup dummy settings
    WORKSPACE = Path.home() / "LocalMind_Workspace"
    SETTINGS_FILE = WORKSPACE / "notification_settings.json"
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    dummy_settings = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "SECRET_PASSWORD"
    }
    SETTINGS_FILE.write_text(json.dumps(dummy_settings))

    # Check what get_notification_settings returns
    # It's an async function
    masked_settings = await settings.get_notification_settings()
    print(f"Masked SMTP Pass: {masked_settings.get('smtp_pass')}")

    if masked_settings.get("smtp_pass") == "SECRET_PASSWORD":
        print("VULNERABILITY STILL PRESENT: smtp_pass is leaked in plain text")
    elif masked_settings.get("smtp_pass") == "********":
        print("FIX VERIFIED: smtp_pass is masked")
    else:
        print(f"Unexpected value for smtp_pass: {masked_settings.get('smtp_pass')}")

    # Test preservation
    incoming = {
        "enabled": True,
        "method": "email-to-sms",
        "phone": "1234567890",
        "carrier": "verizon",
        "smtp_user": "user@example.com",
        "smtp_pass": "********" # The mask
    }

    await settings.update_notification_settings(incoming)

    # Read raw file to see if secret was preserved
    raw_settings = json.loads(SETTINGS_FILE.read_text())
    if raw_settings.get("smtp_pass") == "SECRET_PASSWORD":
        print("PRESERVATION VERIFIED: secret was not overwritten by mask")
    else:
        print(f"PRESERVATION FAILED: secret was overwritten by {raw_settings.get('smtp_pass')}")

if __name__ == "__main__":
    asyncio.run(repro())
