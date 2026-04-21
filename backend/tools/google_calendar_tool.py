"""
Google Calendar Tool — List calendars and upcoming events via Calendar API.

Capabilities:
  - list_calendars       — list the user's calendars
  - list_upcoming_events — list upcoming events on a calendar
  - get_events_for_day   — convenience wrapper for a single-day window
  - get_next_meeting     — return the next upcoming event, or None

Prerequisites:
  pip install google-api-python-client google-auth

Google OAuth credentials are loaded automatically via the centralized
credential store (backend.routes.google_auth.get_credentials).

Scope note:
  This tool uses the read-only Calendar scope
  (https://www.googleapis.com/auth/calendar.readonly). The central SCOPES
  list in backend/routes/google_auth.py must include this scope for the
  OAuth flow to grant access; if it does not, the user will need to
  re-authenticate after it is added.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Any, Callable

from .base import BaseTool

logger = logging.getLogger("localmind.tools.google_calendar")

# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

# Readonly is sufficient for the coworker-greeting use case (list calendars,
# read upcoming events). If a future action writes to the calendar, add
# https://www.googleapis.com/auth/calendar.events to the central SCOPES list
# in backend/routes/google_auth.py and trigger re-auth.
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"


# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------

_google_api_available: bool | None = None


def _require_google_api():
    """Raise a clear RuntimeError if google-api-python-client is not installed."""
    global _google_api_available
    if _google_api_available is True:
        return
    try:
        import googleapiclient.discovery  # noqa: F401
        _google_api_available = True
    except ImportError:
        _google_api_available = False
        raise RuntimeError(
            "google-api-python-client is not installed. "
            "Run: pip install google-api-python-client google-auth"
        )


# ---------------------------------------------------------------------------
# Credential helper
# ---------------------------------------------------------------------------


def _get_credentials():
    """Load Google OAuth credentials from the centralized credential store.

    Tries the DB-backed store first, then falls back to file-based tokens.
    Returns a google.oauth2.credentials.Credentials object or raises.
    """
    try:
        from backend.routes.google_auth import get_credentials
        creds = get_credentials()
    except Exception:
        creds = None

    if creds is None:
        raise RuntimeError(
            "No valid Google credentials found. Connect Google via Settings "
            "or run the OAuth flow at /api/google/auth"
        )
    return creds


# ---------------------------------------------------------------------------
# Service builder
# ---------------------------------------------------------------------------


def _build_calendar_service(credentials):
    """Build an authorized Google Calendar API v3 service client."""
    from googleapiclient.discovery import build
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------


def _now_utc_iso() -> str:
    """Return the current UTC time as an RFC3339/ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_iso8601(value: str | None) -> str | None:
    """Coerce a user-supplied datetime string into an ISO8601 string.

    Accepts strings already in ISO8601 form and returns them unchanged.
    Returns None if the input is None or empty.
    """
    if not value:
        return None
    # Allow a trailing 'Z' (Google accepts it); also accept plain ISO.
    return value


def _extract_event_datetime(event_time: dict | None) -> str:
    """Return an ISO8601 string from a Google Calendar event start/end dict.

    Google returns either {"dateTime": "...", "timeZone": "..."} for timed
    events, or {"date": "YYYY-MM-DD"} for all-day events.
    """
    if not event_time:
        return ""
    if "dateTime" in event_time and event_time["dateTime"]:
        return event_time["dateTime"]
    if "date" in event_time and event_time["date"]:
        # All-day event — return the date as an ISO8601 date string
        return event_time["date"]
    return ""


def _normalize_event(event: dict) -> dict:
    """Convert a Google Calendar event resource into a plain JSON-safe dict."""
    attendees_raw = event.get("attendees", []) or []
    attendees = [
        {
            "email": a.get("email", ""),
            "displayName": a.get("displayName", ""),
            "responseStatus": a.get("responseStatus", ""),
            "organizer": bool(a.get("organizer", False)),
            "self": bool(a.get("self", False)),
            "optional": bool(a.get("optional", False)),
        }
        for a in attendees_raw
    ]

    return {
        "id": event.get("id", ""),
        "summary": event.get("summary", ""),
        "description": event.get("description", ""),
        "start": _extract_event_datetime(event.get("start")),
        "end": _extract_event_datetime(event.get("end")),
        "location": event.get("location", ""),
        "attendees": attendees,
        "hangoutLink": event.get("hangoutLink", ""),
        "htmlLink": event.get("htmlLink", ""),
        "status": event.get("status", ""),
        "organizer": {
            "email": (event.get("organizer") or {}).get("email", ""),
            "displayName": (event.get("organizer") or {}).get("displayName", ""),
        },
        "allDay": "date" in (event.get("start") or {}),
    }


# ---------------------------------------------------------------------------
# Synchronous worker functions (run in executor)
# ---------------------------------------------------------------------------


def _do_list_calendars(credentials) -> dict:
    """List the user's calendars."""
    service = _build_calendar_service(credentials)

    try:
        response = service.calendarList().list().execute()
    except Exception as exc:
        return _api_error("list_calendars", "", exc)

    items = response.get("items", [])
    calendars = [
        {
            "id": c.get("id", ""),
            "summary": c.get("summary", ""),
            "description": c.get("description", ""),
            "primary": bool(c.get("primary", False)),
            "timeZone": c.get("timeZone", ""),
            "accessRole": c.get("accessRole", ""),
            "backgroundColor": c.get("backgroundColor", ""),
        }
        for c in items
    ]

    return {
        "success": True,
        "result": {
            "calendar_count": len(calendars),
            "calendars": calendars,
        },
    }


def _do_list_upcoming_events(
    credentials,
    calendar_id: str = "primary",
    max_results: int = 10,
    time_min: str | None = None,
    time_max: str | None = None,
) -> dict:
    """List upcoming events on a calendar, starting at time_min (or now)."""
    service = _build_calendar_service(credentials)

    effective_time_min = _ensure_iso8601(time_min) or _now_utc_iso()
    effective_time_max = _ensure_iso8601(time_max)

    params: dict[str, Any] = {
        "calendarId": calendar_id,
        "timeMin": effective_time_min,
        "maxResults": min(max(max_results, 1), 2500),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if effective_time_max:
        params["timeMax"] = effective_time_max

    try:
        response = service.events().list(**params).execute()
    except Exception as exc:
        return _api_error("list_upcoming_events", calendar_id, exc)

    events = [_normalize_event(e) for e in response.get("items", [])]

    return {
        "success": True,
        "result": {
            "calendar_id": calendar_id,
            "time_min": effective_time_min,
            "time_max": effective_time_max,
            "event_count": len(events),
            "events": events,
        },
    }


def _do_get_events_for_day(
    credentials,
    date_iso: str,
    calendar_id: str = "primary",
) -> dict:
    """Return events for a specific day (midnight-to-midnight, UTC).

    date_iso accepts either a YYYY-MM-DD date or a full ISO8601 datetime;
    the date portion is used to build the day window.
    """
    service = _build_calendar_service(credentials)

    # Parse the date portion
    try:
        date_part = date_iso[:10]
        parsed_date = datetime.strptime(date_part, "%Y-%m-%d").date()
    except Exception as exc:
        return {
            "success": False,
            "error": (
                f"Invalid date_iso '{date_iso}'. Expected YYYY-MM-DD or an "
                f"ISO8601 datetime: {exc}"
            ),
        }

    day_start = datetime.combine(parsed_date, dtime.min, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    params = {
        "calendarId": calendar_id,
        "timeMin": day_start.isoformat(),
        "timeMax": day_end.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
    }

    try:
        response = service.events().list(**params).execute()
    except Exception as exc:
        return _api_error("get_events_for_day", calendar_id, exc)

    events = [_normalize_event(e) for e in response.get("items", [])]

    return {
        "success": True,
        "result": {
            "calendar_id": calendar_id,
            "date": parsed_date.isoformat(),
            "time_min": day_start.isoformat(),
            "time_max": day_end.isoformat(),
            "event_count": len(events),
            "events": events,
        },
    }


def _do_get_next_meeting(
    credentials,
    calendar_id: str = "primary",
) -> dict:
    """Return the next upcoming event on the calendar, or None."""
    service = _build_calendar_service(credentials)

    params = {
        "calendarId": calendar_id,
        "timeMin": _now_utc_iso(),
        "maxResults": 1,
        "singleEvents": True,
        "orderBy": "startTime",
    }

    try:
        response = service.events().list(**params).execute()
    except Exception as exc:
        return _api_error("get_next_meeting", calendar_id, exc)

    items = response.get("items", [])
    event = _normalize_event(items[0]) if items else None

    return {
        "success": True,
        "result": {
            "calendar_id": calendar_id,
            "event": event,
        },
    }


# ---------------------------------------------------------------------------
# Public async API (module-level) — matches contract for direct callers
# ---------------------------------------------------------------------------


async def list_calendars() -> list[dict[str, Any]]:
    """Return a list of the user's calendars.

    Each item: {"id", "summary", "primary", ...}.
    Raises RuntimeError if credentials or the API client are unavailable.
    """
    _require_google_api()
    creds = _get_credentials()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: _do_list_calendars(creds))
    if not result.get("success"):
        raise RuntimeError(result.get("error", "list_calendars failed"))
    return result["result"]["calendars"]


async def list_upcoming_events(
    calendar_id: str = "primary",
    max_results: int = 10,
    time_min: str | None = None,
) -> list[dict[str, Any]]:
    """Return upcoming events starting at time_min (defaults to now, UTC).

    Each item: {"id", "summary", "start", "end", "location", "attendees", ...}.
    All datetime fields are ISO8601 strings.
    """
    _require_google_api()
    creds = _get_credentials()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _do_list_upcoming_events(
            creds,
            calendar_id=calendar_id,
            max_results=max_results,
            time_min=time_min,
        ),
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error", "list_upcoming_events failed"))
    return result["result"]["events"]


async def get_events_for_day(
    date_iso: str,
    calendar_id: str = "primary",
) -> list[dict[str, Any]]:
    """Return events scheduled on the given day (UTC, midnight-to-midnight).

    date_iso: "YYYY-MM-DD" or a full ISO8601 datetime; only the date is used.
    """
    _require_google_api()
    creds = _get_credentials()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _do_get_events_for_day(creds, date_iso, calendar_id=calendar_id),
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error", "get_events_for_day failed"))
    return result["result"]["events"]


async def get_next_meeting(
    calendar_id: str = "primary",
) -> dict[str, Any] | None:
    """Return the next upcoming event dict, or None if no events are scheduled."""
    _require_google_api()
    creds = _get_credentials()
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _do_get_next_meeting(creds, calendar_id=calendar_id),
    )
    if not result.get("success"):
        raise RuntimeError(result.get("error", "get_next_meeting failed"))
    return result["result"]["event"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_error(action: str, resource_id: str, exc: Exception) -> dict:
    """Build a structured error dict from a Google API exception."""
    error_msg = str(exc)

    status_code = None
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            status_code = exc.resp.status
            if status_code == 404:
                error_msg = (
                    f"Calendar or event not found: '{resource_id}'. "
                    "Check that the ID is correct and you have access."
                )
            elif status_code == 403:
                error_msg = (
                    f"Permission denied for '{resource_id}'. "
                    "Ensure the credentials include the Calendar scope "
                    "(https://www.googleapis.com/auth/calendar.readonly)."
                )
            elif status_code == 401:
                error_msg = (
                    "Authentication failed. The credentials may be expired "
                    "or invalid."
                )
    except ImportError:
        pass

    logger.error(
        "google_calendar %s failed for %s: %s",
        action, resource_id, error_msg,
    )

    result: dict[str, Any] = {
        "success": False,
        "error": error_msg,
        "action": action,
    }
    if resource_id:
        result["resource_id"] = resource_id
    if status_code is not None:
        result["status_code"] = status_code

    return result


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class GoogleCalendarTool(BaseTool):
    """List calendars and upcoming events in Google Calendar."""

    def __init__(self):
        self.actions: dict[str, Callable] = {
            "list_calendars": self._list_calendars,
            "list_upcoming_events": self._list_upcoming_events,
            "get_events_for_day": self._get_events_for_day,
            "get_next_meeting": self._get_next_meeting,
        }

    @property
    def name(self) -> str:
        return "google_calendar"

    @property
    def description(self) -> str:
        return (
            "List Google Calendars and read upcoming events — "
            "useful for meeting context, schedule summaries, and "
            "coworker/meeting greetings."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(self.actions.keys()),
                    "description": "The Google Calendar action to perform.",
                },
                "calendar_id": {
                    "type": "string",
                    "description": (
                        "Calendar ID (e.g. 'primary' or a calendar's email "
                        "address). Defaults to 'primary'."
                    ),
                    "default": "primary",
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Maximum number of events to return "
                        "(default 10, max 2500). For list_upcoming_events."
                    ),
                    "default": 10,
                },
                "time_min": {
                    "type": "string",
                    "description": (
                        "ISO8601 lower bound for event start times "
                        "(list_upcoming_events). Defaults to now (UTC)."
                    ),
                },
                "time_max": {
                    "type": "string",
                    "description": (
                        "ISO8601 upper bound for event start times "
                        "(list_upcoming_events). Optional."
                    ),
                },
                "date_iso": {
                    "type": "string",
                    "description": (
                        "Date for get_events_for_day in YYYY-MM-DD form "
                        "(or any ISO8601 datetime; only the date is used)."
                    ),
                },
                "credentials": {
                    "type": "object",
                    "description": (
                        "Google OAuth credentials object. Typically provided "
                        "automatically by the credential manager."
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        """Dispatch to the right action method."""
        action: str = kwargs.get("action", "")

        try:
            _require_google_api()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        handler = self.actions.get(action)
        if not handler:
            return {
                "success": False,
                "error": (
                    f"Unknown action: '{action}'. "
                    f"Available actions: {list(self.actions.keys())}"
                ),
            }

        loop = asyncio.get_event_loop()

        try:
            return await loop.run_in_executor(None, lambda: handler(kwargs))
        except Exception as exc:
            logger.exception("google_calendar %s failed", action)
            return {"success": False, "error": str(exc)}

    # -- Action handlers -----------------------------------------------------

    def _resolve_credentials(self, kwargs: dict):
        """Get credentials from kwargs or auto-load from credential store."""
        creds = kwargs.get("credentials")
        if not creds:
            creds = _get_credentials()
        return creds

    def _list_calendars(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        return _do_list_calendars(creds)

    def _list_upcoming_events(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        calendar_id = kwargs.get("calendar_id", "primary")
        max_results = kwargs.get("max_results", 10)
        time_min = kwargs.get("time_min")
        time_max = kwargs.get("time_max")
        return _do_list_upcoming_events(
            creds,
            calendar_id=calendar_id,
            max_results=max_results,
            time_min=time_min,
            time_max=time_max,
        )

    def _get_events_for_day(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        date_iso = kwargs.get("date_iso")
        if not date_iso:
            return {
                "success": False,
                "error": "date_iso is required for get_events_for_day",
            }
        calendar_id = kwargs.get("calendar_id", "primary")
        return _do_get_events_for_day(
            creds, date_iso, calendar_id=calendar_id
        )

    def _get_next_meeting(self, kwargs: dict) -> dict:
        creds = self._resolve_credentials(kwargs)
        calendar_id = kwargs.get("calendar_id", "primary")
        return _do_get_next_meeting(creds, calendar_id=calendar_id)
