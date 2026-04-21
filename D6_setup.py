import os
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent
    workspace_dir = root / "backend" / "integrations" / "google_workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    auth_py_path = workspace_dir / "auth.py"
    init_py_path = workspace_dir / "__init__.py"

    auth_content = """\"\"\"
auth.py - Google Workspace OAuth 2.0 flow and credential management (Phase D6)
==============================================================================
Handles secure, persistent local OAuth token storage using google-auth-oauthlib.
\"\"\"
import os
import pickle
import logging
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger("localmind.integrations.google_workspace.auth")

# Defined scopes covering Drive, Docs, Sheets, Slides, and Gmail
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/gmail.modify"
]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.pickle")

def get_credentials() -> Credentials:
    \"\"\"Gets valid user credentials from storage or initiates OAuth flow.\"\"\"
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Google credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(f"OAuth Client Secret file '{CREDENTIALS_FILE}' not found. Please set up a Google Cloud Project.")
            logger.info("Initiating new Google OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return creds
"""
    
    init_content = """\"\"\"
Google Workspace Integration Module
\"\"\"
from .auth import get_credentials

__all__ = ["get_credentials"]
"""

    auth_py_path.write_text(auth_content, encoding="utf-8")
    init_py_path.write_text(init_content, encoding="utf-8")
    print("Successfully created backend/integrations/google_workspace directory and files.")

if __name__ == "__main__":
    main()
