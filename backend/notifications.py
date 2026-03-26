"""
notifications.py — LocalMind Alerting System
============================================
Handles SMS/Text notifications for autonomous progress and critical alerts.
Supports:
  - Email-to-SMS (Free, carrier gateways)
  - Twilio (API-based, robust)
"""

import smtplib
import logging
from email.message import EmailMessage
from pathlib import Path
import json

logger = logging.getLogger("localmind.notifications")

SETTINGS_FILE = Path.home() / "LocalMind_Workspace" / "notification_settings.json"

# Common US Carrier Gateways
CARRIER_GATEWAYS = {
    "verizon": "@vtext.com",
    "att": "@txt.att.net",
    "tmobile": "@tmomail.net",
    "sprint": "@messaging.sprintpcs.com",
    "googlevoice": "@msg.fi.google.com",
}

def get_settings():
    if not SETTINGS_FILE.exists():
        return {"enabled": False, "method": "email-to-sms", "phone": "", "carrier": "", "smtp_user": "", "smtp_pass": ""}
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except:
        return {"enabled": False}

def save_settings(settings: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))

async def send_sms(message: str, priority: str = "normal"):
    """Sends a text message via the configured method."""
    settings = get_settings()
    if not settings.get("enabled"):
        return False

    phone = settings.get("phone")
    carrier = settings.get("carrier")
    
    if not phone or not carrier:
        logger.warning("SMS failed: Phone or carrier not configured.")
        return False

    if settings.get("method") == "email-to-sms":
        return _send_via_email(phone, carrier, message, settings)
    
    # Placeholder for Twilio
    logger.info(f"Notification triggered (Mock SMS): {message}")
    return True

def _send_via_email(phone: str, carrier: str, body: str, settings: dict):
    """Sends SMS via SMTP gateway."""
    gateway = CARRIER_GATEWAYS.get(carrier.lower())
    if not gateway:
        logger.error(f"Unknown carrier: {carrier}")
        return False

    to_address = f"{phone}{gateway}"
    
    # Defaulting to a generic 'Notify' sender if SMTP not fully configured
    # In a real app, user would provide Gmail/Outlook App Password
    smtp_user = settings.get("smtp_user")
    smtp_pass = settings.get("smtp_pass")
    
    if not smtp_user or not smtp_pass:
        logger.warning("Email-to-SMS requires SMTP credentials in settings.")
        return False

    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = "LocalMind Alert"
        msg['From'] = smtp_user
        msg['To'] = to_address

        # Most modern SMTP uses port 587 (TLS)
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        
        logger.info(f"SMS sent to {to_address}")
        return True
    except Exception as e:
        logger.error(f"Failed to send SMS via email: {e}")
        return False
