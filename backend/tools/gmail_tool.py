"""
Gmail Tool — Read, search, send, draft, and label emails via Gmail API.

Prerequisites:
- pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
- Place OAuth credentials.json from Google Cloud Console in ~/.localmind/
- First use opens a browser for OAuth consent; token is cached in ~/.localmind/gmail_token.json

Safety:
- send and reply actions log a warning so the autonomy engine can gate them
- read-only actions are always safe
"""

import asyncio
import base64
import logging
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.gmail")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

CONFIG_DIR = Path.home() / ".localmind"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "gmail_token.json"


def _get_gmail_service():
    """Build and return an authenticated Gmail API service object."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Gmail dependencies not installed. Run: "
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"OAuth credentials not found at {CREDENTIALS_FILE}. "
                    "Download from Google Cloud Console and place there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _extract_body(payload: dict) -> str:
    """Extract plaintext body from a Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        # Recurse into nested multipart
        nested = _extract_body(part)
        if nested:
            return nested
    return ""


def _header(headers: list, name: str) -> str:
    """Get a header value by name from a Gmail message headers list."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


class GmailTool(BaseTool):
    """Read, search, send, and manage Gmail emails."""

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def description(self) -> str:
        return (
            "Access Gmail: list recent emails, read a message, search with Gmail query syntax, "
            "send emails, create drafts, reply to messages, and manage labels."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_messages",
                        "read_message",
                        "search",
                        "send",
                        "draft",
                        "reply",
                        "label",
                        "list_labels",
                        "analyze_style",
                    ],
                    "description": "The Gmail action to perform",
                },
                "message_id": {
                    "type": "string",
                    "description": "Gmail message ID (for read_message, reply, label)",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query (for search action)",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient email address (for send, draft)",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject (for send, draft)",
                },
                "body": {
                    "type": "string",
                    "description": "Email body text (for send, draft, reply)",
                },
                "label_ids": {
                    "type": "string",
                    "description": "Comma-separated label IDs to add (for label action)",
                },
                "remove_label_ids": {
                    "type": "string",
                    "description": "Comma-separated label IDs to remove (for label action)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to return (default 10, max 50)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        action = kwargs.get("action", "")
        loop = asyncio.get_event_loop()

        # Safety for send/reply/draft is handled by the propose_action tool
        # which gates dangerous actions with an approval card before this runs.

        try:
            service = await loop.run_in_executor(None, _get_gmail_service)
        except (RuntimeError, FileNotFoundError) as exc:
            return {"success": False, "error": str(exc)}

        dispatch = {
            "list_messages": self._list_messages,
            "read_message": self._read_message,
            "search": self._search,
            "send": self._send,
            "draft": self._draft,
            "reply": self._reply,
            "label": self._label,
            "list_labels": self._list_labels,
            "analyze_style": self._analyze_style,
        }

        handler = dispatch.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}

        try:
            return await loop.run_in_executor(None, lambda: handler(service, kwargs))
        except Exception as exc:
            logger.exception(f"Gmail {action} failed")
            return {"success": False, "error": str(exc)}

    # ── Actions ──────────────────────────────────────────────────────

    def _list_messages(self, service, kwargs: dict) -> dict:
        max_results = min(kwargs.get("max_results", 10), 50)
        results = (
            service.users()
            .messages()
            .list(userId="me", maxResults=max_results)
            .execute()
        )
        messages = results.get("messages", [])
        if not messages:
            return {"success": True, "result": "No messages found."}

        summaries = []
        for msg_stub in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_stub["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = msg.get("payload", {}).get("headers", [])
            summaries.append({
                "id": msg["id"],
                "subject": _header(headers, "Subject"),
                "from": _header(headers, "From"),
                "date": _header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
            })

        return {"success": True, "result": summaries}

    def _read_message(self, service, kwargs: dict) -> dict:
        msg_id = kwargs.get("message_id")
        if not msg_id:
            return {"success": False, "error": "message_id is required"}

        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        body = _extract_body(msg.get("payload", {}))

        return {
            "success": True,
            "result": {
                "id": msg["id"],
                "subject": _header(headers, "Subject"),
                "from": _header(headers, "From"),
                "to": _header(headers, "To"),
                "date": _header(headers, "Date"),
                "body": body[:5000] if body else "(no plaintext body)",
                "labels": msg.get("labelIds", []),
            },
        }

    def _search(self, service, kwargs: dict) -> dict:
        query = kwargs.get("query", "")
        if not query:
            return {"success": False, "error": "query is required for search"}

        max_results = min(kwargs.get("max_results", 10), 50)
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        messages = results.get("messages", [])
        if not messages:
            return {"success": True, "result": f"No messages matching '{query}'."}

        summaries = []
        for msg_stub in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_stub["id"], format="metadata",
                     metadataHeaders=["Subject", "From", "Date"])
                .execute()
            )
            headers = msg.get("payload", {}).get("headers", [])
            summaries.append({
                "id": msg["id"],
                "subject": _header(headers, "Subject"),
                "from": _header(headers, "From"),
                "date": _header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
            })

        return {"success": True, "result": summaries}

    def _send(self, service, kwargs: dict) -> dict:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "") or "Hello from LocalMind"
        body = kwargs.get("body", "") or "This is a message sent via LocalMind."
        if not to:
            return {"success": False, "error": "'to' address is required for send"}

        logger.warning(f"Sending email to {to} — subject: {subject}")

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return {"success": True, "result": f"Email sent (id: {sent['id']})"}

    def _draft(self, service, kwargs: dict) -> dict:
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        if not body:
            return {"success": False, "error": "body is required for draft"}

        message = MIMEText(body)
        if to:
            message["to"] = to
        message["subject"] = subject or "(no subject)"
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )
        return {"success": True, "result": f"Draft created (id: {draft['id']})"}

    def _reply(self, service, kwargs: dict) -> dict:
        msg_id = kwargs.get("message_id")
        body = kwargs.get("body", "")
        if not msg_id or not body:
            return {"success": False, "error": "message_id and body are required for reply"}

        original = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata",
                 metadataHeaders=["Subject", "From", "Message-ID"])
            .execute()
        )
        headers = original.get("payload", {}).get("headers", [])
        reply_to = _header(headers, "From")
        subject = _header(headers, "Subject")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        original_msg_id = _header(headers, "Message-ID")
        thread_id = original.get("threadId", "")

        logger.warning(f"Replying to {reply_to} — subject: {subject}")

        message = MIMEText(body)
        message["to"] = reply_to
        message["subject"] = subject
        if original_msg_id:
            message["In-Reply-To"] = original_msg_id
            message["References"] = original_msg_id
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw, "threadId": thread_id})
            .execute()
        )
        return {"success": True, "result": f"Reply sent (id: {sent['id']})"}

    def _label(self, service, kwargs: dict) -> dict:
        msg_id = kwargs.get("message_id")
        if not msg_id:
            return {"success": False, "error": "message_id is required for label"}

        add_ids = [l.strip() for l in kwargs.get("label_ids", "").split(",") if l.strip()]
        remove_ids = [l.strip() for l in kwargs.get("remove_label_ids", "").split(",") if l.strip()]

        if not add_ids and not remove_ids:
            return {"success": False, "error": "label_ids or remove_label_ids required"}

        body = {}
        if add_ids:
            body["addLabelIds"] = add_ids
        if remove_ids:
            body["removeLabelIds"] = remove_ids

        service.users().messages().modify(userId="me", id=msg_id, body=body).execute()
        return {"success": True, "result": f"Labels updated for message {msg_id}"}

    def _list_labels(self, service, kwargs: dict) -> dict:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        return {
            "success": True,
            "result": [{"id": l["id"], "name": l["name"]} for l in labels],
        }

    def _analyze_style(self, service, kwargs: dict) -> dict:
        """Read recent sent emails and produce a writing style profile.

        The LLM can use this profile to draft emails that match the user's
        natural tone, greeting style, sign-off, sentence length, etc.
        """
        max_results = min(kwargs.get("max_results", 20), 50)

        # Fetch sent emails
        results = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["SENT"], maxResults=max_results)
            .execute()
        )
        messages = results.get("messages", [])
        if not messages:
            return {"success": True, "result": "No sent emails found to analyze."}

        samples = []
        for msg_stub in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_stub["id"], format="full")
                .execute()
            )
            body = _extract_body(msg.get("payload", {}))
            if body:
                # Take first 500 chars per email to keep payload reasonable
                samples.append(body[:500])

        if not samples:
            return {"success": True, "result": "Sent emails found but no plaintext bodies extracted."}

        # Build a style profile from the samples
        all_text = "\n".join(samples)
        total_words = len(all_text.split())
        total_sentences = max(all_text.count(".") + all_text.count("!") + all_text.count("?"), 1)
        avg_sentence_len = round(total_words / total_sentences, 1)

        # Extract common greetings and sign-offs
        greetings = []
        signoffs = []
        for sample in samples:
            lines = [l.strip() for l in sample.strip().splitlines() if l.strip()]
            if lines:
                first_line = lines[0]
                if len(first_line) < 60:
                    greetings.append(first_line)
                last_lines = lines[-3:] if len(lines) >= 3 else lines
                for line in reversed(last_lines):
                    if len(line) < 60:
                        signoffs.append(line)
                        break

        # Find most common patterns
        def most_common(items: list, n: int = 3) -> list:
            counts: dict[str, int] = {}
            for item in items:
                counts[item] = counts.get(item, 0) + 1
            return sorted(counts, key=counts.get, reverse=True)[:n]

        return {
            "success": True,
            "result": {
                "emails_analyzed": len(samples),
                "avg_sentence_length_words": avg_sentence_len,
                "common_greetings": most_common(greetings),
                "common_signoffs": most_common(signoffs),
                "sample_excerpts": [s[:200] for s in samples[:5]],
                "style_notes": (
                    "Use the greetings, sign-offs, sentence length, and sample excerpts above "
                    "to match this user's natural email writing style when composing emails."
                ),
            },
        }
