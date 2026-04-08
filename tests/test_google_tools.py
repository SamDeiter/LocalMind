"""
Comprehensive pytest tests for Google Slides and Google Sheets tool implementations.

Covers:
  1. GoogleSlidesTool — all 8 actions: read_presentation, get_slide, get_slide_text,
     update_text, add_slide, delete_slide, duplicate_slide, download_as_pptx
  2. GoogleSheetsTool — all 9 actions: read_spreadsheet, read_range, write_range,
     append_data, create_sheet, delete_sheet, clear_range, get_cell_formats, batch_update
  3. Tool metadata (name, description, parameters, action lists)
  4. Error paths (missing credentials, invalid action, API errors, missing params)
  5. Helper functions (_text_content_to_string, _extract_shapes, _api_error)

All Google API calls are mocked — tests run without google-api-python-client installed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously for tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _mock_credentials():
    """Return a mock Google OAuth credentials object."""
    creds = MagicMock()
    creds.token = "fake-token"
    creds.refresh_token = "fake-refresh"
    creds.valid = True
    return creds


# ---------------------------------------------------------------------------
# Canned API responses
# ---------------------------------------------------------------------------

CANNED_PRESENTATION = {
    "presentationId": "pres-123",
    "title": "My Presentation",
    "locale": "en",
    "pageSize": {
        "width": {"magnitude": 9144000, "unit": "EMU"},
        "height": {"magnitude": 6858000, "unit": "EMU"},
    },
    "slides": [
        {
            "objectId": "slide-001",
            "slideProperties": {
                "layoutObjectId": "layout-BLANK",
                "notesPage": {
                    "pageElements": [
                        {
                            "shape": {
                                "placeholder": {"type": "BODY"},
                                "text": {
                                    "textElements": [
                                        {"textRun": {"content": "Speaker notes here"}}
                                    ]
                                },
                            }
                        }
                    ]
                },
            },
            "pageElements": [
                {
                    "objectId": "shape-title",
                    "size": {"width": {"magnitude": 100}},
                    "transform": {},
                    "shape": {
                        "shapeType": "TEXT_BOX",
                        "placeholder": {"type": "TITLE", "index": 0},
                        "text": {
                            "textElements": [
                                {"textRun": {"content": "Hello World"}}
                            ]
                        },
                    },
                },
                {
                    "objectId": "table-001",
                    "size": {},
                    "transform": {},
                    "table": {
                        "rows": 2,
                        "columns": 2,
                        "tableRows": [
                            {
                                "tableCells": [
                                    {"text": {"textElements": [{"textRun": {"content": "A1"}}]}},
                                    {"text": {"textElements": [{"textRun": {"content": "B1"}}]}},
                                ]
                            },
                            {
                                "tableCells": [
                                    {"text": {"textElements": [{"textRun": {"content": "A2"}}]}},
                                    {"text": {"textElements": [{"textRun": {"content": "B2"}}]}},
                                ]
                            },
                        ],
                    },
                },
                {
                    "objectId": "img-001",
                    "size": {},
                    "transform": {},
                    "image": {
                        "contentUrl": "https://example.com/image.png",
                        "sourceUrl": "https://example.com/source.png",
                    },
                },
            ],
        },
        {
            "objectId": "slide-002",
            "slideProperties": {"layoutObjectId": "layout-TITLE"},
            "pageElements": [],
        },
    ],
    "masters": [{"objectId": "master-001"}],
    "layouts": [
        {
            "objectId": "layout-BLANK",
            "layoutProperties": {"name": "BLANK", "displayName": "Blank"},
        }
    ],
}

CANNED_BATCH_UPDATE_RESPONSE = {
    "presentationId": "pres-123",
    "replies": [{"createSlide": {"objectId": "new-slide-999"}}],
}

CANNED_DUPLICATE_RESPONSE = {
    "presentationId": "pres-123",
    "replies": [{"duplicateObject": {"objectId": "dup-slide-888"}}],
}

CANNED_SPREADSHEET_META = {
    "properties": {"title": "Budget 2024"},
    "sheets": [
        {
            "properties": {
                "sheetId": 0,
                "title": "Sheet1",
                "index": 0,
                "gridProperties": {"rowCount": 1000, "columnCount": 26},
            }
        },
        {
            "properties": {
                "sheetId": 123,
                "title": "Summary",
                "index": 1,
                "gridProperties": {"rowCount": 500, "columnCount": 10},
            }
        },
    ],
}

CANNED_RANGE_VALUES = {
    "range": "Sheet1!A1:C3",
    "majorDimension": "ROWS",
    "values": [["Name", "Age", "City"], ["Alice", "30", "NYC"], ["Bob", "25", "LA"]],
}

CANNED_WRITE_RESPONSE = {
    "updatedRange": "Sheet1!A1:C2",
    "updatedRows": 2,
    "updatedColumns": 3,
    "updatedCells": 6,
}

CANNED_APPEND_RESPONSE = {
    "tableRange": "Sheet1!A1:C3",
    "updates": {
        "updatedRange": "Sheet1!A4:C4",
        "updatedRows": 1,
        "updatedColumns": 3,
        "updatedCells": 3,
    },
}

CANNED_CREATE_SHEET_RESPONSE = {
    "replies": [
        {
            "addSheet": {
                "properties": {
                    "sheetId": 456,
                    "title": "NewTab",
                    "index": 2,
                }
            }
        }
    ],
}

CANNED_CLEAR_RESPONSE = {"clearedRange": "Sheet1!A1:D10"}

CANNED_FORMATS_RESPONSE = {
    "sheets": [
        {
            "properties": {"title": "Sheet1"},
            "data": [
                {
                    "rowData": [
                        {
                            "values": [
                                {
                                    "effectiveFormat": {
                                        "textFormat": {
                                            "bold": True,
                                            "italic": False,
                                            "strikethrough": False,
                                            "underline": False,
                                            "fontFamily": "Arial",
                                            "fontSize": 10,
                                            "foregroundColorStyle": {
                                                "rgbColor": {"red": 0, "green": 0, "blue": 0}
                                            },
                                        },
                                        "backgroundColorStyle": {
                                            "rgbColor": {"red": 1, "green": 1, "blue": 1}
                                        },
                                        "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                                        "horizontalAlignment": "LEFT",
                                        "verticalAlignment": "BOTTOM",
                                        "wrapStrategy": "OVERFLOW_CELL",
                                    },
                                    "userEnteredFormat": {"textFormat": {"bold": True}},
                                }
                            ]
                        }
                    ]
                }
            ],
        }
    ]
}

CANNED_BATCH_UPDATE_SHEETS = {
    "spreadsheetId": "ss-abc",
    "replies": [{"addConditionalFormatRule": {}}],
}


# ---------------------------------------------------------------------------
# Mock build helpers
# ---------------------------------------------------------------------------

def _make_slides_service(get_response=None, batch_response=None):
    """Create a mock Slides API service."""
    service = MagicMock()

    # presentations().get().execute()
    pres = service.presentations.return_value
    pres.get.return_value.execute.return_value = get_response or CANNED_PRESENTATION

    # presentations().batchUpdate().execute()
    pres.batchUpdate.return_value.execute.return_value = (
        batch_response or CANNED_BATCH_UPDATE_RESPONSE
    )

    return service


def _make_drive_service(export_content=b"PPTX-BINARY-DATA"):
    """Create a mock Drive API service."""
    service = MagicMock()
    service.files.return_value.export.return_value.execute.return_value = export_content
    return service


def _make_sheets_service(
    get_response=None,
    values_get_response=None,
    values_update_response=None,
    values_append_response=None,
    values_clear_response=None,
    batch_response=None,
):
    """Create a mock Sheets API service."""
    service = MagicMock()
    ss = service.spreadsheets.return_value

    # spreadsheets().get().execute()
    ss.get.return_value.execute.return_value = get_response or CANNED_SPREADSHEET_META

    # spreadsheets().batchUpdate().execute()
    ss.batchUpdate.return_value.execute.return_value = (
        batch_response or CANNED_CREATE_SHEET_RESPONSE
    )

    # spreadsheets().values().get().execute()
    vals = ss.values.return_value
    vals.get.return_value.execute.return_value = values_get_response or CANNED_RANGE_VALUES
    vals.update.return_value.execute.return_value = values_update_response or CANNED_WRITE_RESPONSE
    vals.append.return_value.execute.return_value = values_append_response or CANNED_APPEND_RESPONSE
    vals.clear.return_value.execute.return_value = values_clear_response or CANNED_CLEAR_RESPONSE

    return service


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Google Slides Tool
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoogleSlidesToolMetadata:
    """Tool metadata: name, description, parameters, actions list."""

    def test_name(self):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        assert tool.name == "google_slides"

    def test_description(self):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        assert "Google Slides" in tool.description

    def test_parameters_schema_has_action(self):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        params = tool.parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert "presentation_id" in params["required"]

    def test_actions_list(self):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        expected = {
            "read_presentation", "get_slide", "get_slide_text", "update_text",
            "add_slide", "delete_slide", "duplicate_slide", "download_as_pptx",
        }
        assert set(tool.actions.keys()) == expected

    def test_to_ollama_tool(self):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        ollama = tool.to_ollama_tool()
        assert ollama["type"] == "function"
        assert ollama["function"]["name"] == "google_slides"


class TestSlidesReadPresentation:
    """Tests for the read_presentation action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service", return_value=_make_slides_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="read_presentation",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["title"] == "My Presentation"
        assert result["result"]["slide_count"] == 2
        assert len(result["result"]["slides"]) == 2

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_credentials(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="read_presentation",
            presentation_id="pres-123",
        ))
        assert result["success"] is False
        assert "credentials" in result["error"]

    def test_missing_presentation_id(self):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(action="read_presentation", presentation_id=""))
        assert result["success"] is False
        assert "presentation_id" in result["error"]

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.presentations.return_value.get.return_value.execute.side_effect = Exception("Network error")
        mock_build.return_value = svc

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="read_presentation",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Network error" in result["error"]


class TestSlidesGetSlide:
    """Tests for the get_slide action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service", return_value=_make_slides_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="get_slide",
            presentation_id="pres-123",
            slide_index=0,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["objectId"] == "slide-001"
        assert result["result"]["notes"] == "Speaker notes here"
        # shapes should include text_box, table, and image
        shapes = result["result"]["shapes"]
        shape_types = {s["type"] for s in shapes}
        assert "shape" in shape_types
        assert "table" in shape_types
        assert "image" in shape_types

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service", return_value=_make_slides_service())
    def test_out_of_range(self, mock_build, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="get_slide",
            presentation_id="pres-123",
            slide_index=99,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "out of range" in result["error"]

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_slide_index(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="get_slide",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "slide_index" in result["error"]


class TestSlidesGetSlideText:
    """Tests for the get_slide_text action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service", return_value=_make_slides_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="get_slide_text",
            presentation_id="pres-123",
            slide_index=0,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        entries = result["result"]["text_entries"]
        # Should find "Hello World" from shape + table cell texts
        texts = [e["text"] for e in entries]
        assert any("Hello World" in t for t in texts)
        # Table cells should have type field
        table_entries = [e for e in entries if e.get("type") == "table_cell"]
        assert len(table_entries) > 0

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service", return_value=_make_slides_service())
    def test_out_of_range(self, mock_build, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="get_slide_text",
            presentation_id="pres-123",
            slide_index=-1,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_slide_index(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="get_slide_text",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "slide_index" in result["error"]


class TestSlidesUpdateText:
    """Tests for the update_text action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_happy_path(self, mock_build, mock_req):
        svc = _make_slides_service(batch_response={"replies": [{}]})
        mock_build.return_value = svc

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="update_text",
            presentation_id="pres-123",
            shape_id="shape-title",
            new_text="Updated Title",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["new_text"] == "Updated Title"

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_shape_id(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="update_text",
            presentation_id="pres-123",
            new_text="text",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "shape_id" in result["error"]

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_new_text(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="update_text",
            presentation_id="pres-123",
            shape_id="shape-1",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "new_text" in result["error"]


class TestSlidesAddSlide:
    """Tests for the add_slide action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_happy_path(self, mock_build, mock_req):
        mock_build.return_value = _make_slides_service()

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="add_slide",
            presentation_id="pres-123",
            layout="TITLE",
            insert_at=1,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["new_slide_id"] == "new-slide-999"
        assert result["result"]["layout"] == "TITLE"

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_default_blank_layout(self, mock_build, mock_req):
        mock_build.return_value = _make_slides_service()

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="add_slide",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["layout"] == "BLANK"

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_api_error_on_add(self, mock_build, mock_req):
        svc = MagicMock()
        svc.presentations.return_value.batchUpdate.return_value.execute.side_effect = (
            Exception("Quota exceeded")
        )
        mock_build.return_value = svc

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="add_slide",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Quota exceeded" in result["error"]


class TestSlidesDeleteSlide:
    """Tests for the delete_slide action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_happy_path(self, mock_build, mock_req):
        mock_build.return_value = _make_slides_service(batch_response={"replies": []})

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="delete_slide",
            presentation_id="pres-123",
            slide_id="slide-001",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["deleted_slide_id"] == "slide-001"

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_slide_id(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="delete_slide",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "slide_id" in result["error"]


class TestSlidesDuplicateSlide:
    """Tests for the duplicate_slide action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_happy_path_without_move(self, mock_build, mock_req):
        mock_build.return_value = _make_slides_service(batch_response=CANNED_DUPLICATE_RESPONSE)

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="duplicate_slide",
            presentation_id="pres-123",
            slide_id="slide-001",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["new_slide_id"] == "dup-slide-888"
        assert result["result"]["insert_at"] is None

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_happy_path_with_move(self, mock_build, mock_req):
        svc = _make_slides_service(batch_response=CANNED_DUPLICATE_RESPONSE)
        mock_build.return_value = svc

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="duplicate_slide",
            presentation_id="pres-123",
            slide_id="slide-001",
            insert_at=0,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["new_slide_id"] == "dup-slide-888"
        assert result["result"]["insert_at"] == 0

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_slides_service")
    def test_duplicate_move_fails_partial_success(self, mock_build, mock_req):
        """When duplicate succeeds but the move batchUpdate fails, we get partial success."""
        svc = MagicMock()
        # First batchUpdate (duplicate) succeeds
        # Second batchUpdate (move) fails
        svc.presentations.return_value.batchUpdate.return_value.execute.side_effect = [
            CANNED_DUPLICATE_RESPONSE,
            Exception("Move failed"),
        ]
        mock_build.return_value = svc

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="duplicate_slide",
            presentation_id="pres-123",
            slide_id="slide-001",
            insert_at=0,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert "warning" in result["result"]

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_slide_id(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="duplicate_slide",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "slide_id" in result["error"]


class TestSlidesDownloadAsPptx:
    """Tests for the download_as_pptx action."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_drive_service")
    def test_happy_path(self, mock_build, mock_req, tmp_path):
        mock_build.return_value = _make_drive_service(b"PPTX-DATA")

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        output = str(tmp_path / "exported.pptx")
        result = _run(tool.execute(
            action="download_as_pptx",
            presentation_id="pres-123",
            output_path=output,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["size_bytes"] == 9
        assert Path(output).read_bytes() == b"PPTX-DATA"

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_missing_output_path(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="download_as_pptx",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "output_path" in result["error"]

    @patch("backend.tools.google_slides_tool._require_google_api")
    @patch("backend.tools.google_slides_tool._build_drive_service")
    def test_api_error(self, mock_build, mock_req, tmp_path):
        svc = MagicMock()
        svc.files.return_value.export.return_value.execute.side_effect = Exception("Drive error")
        mock_build.return_value = svc

        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        output = str(tmp_path / "fail.pptx")
        result = _run(tool.execute(
            action="download_as_pptx",
            presentation_id="pres-123",
            output_path=output,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False


class TestSlidesInvalidAction:
    """Test that invalid/unknown actions fail cleanly."""

    @patch("backend.tools.google_slides_tool._require_google_api")
    def test_unknown_action(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="do_magic",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Unknown action" in result["error"]
        assert "do_magic" in result["error"]


class TestSlidesGoogleApiNotInstalled:
    """Verify graceful failure when google-api-python-client is missing."""

    @patch(
        "backend.tools.google_slides_tool._require_google_api",
        side_effect=RuntimeError("google-api-python-client is not installed"),
    )
    def test_returns_error(self, mock_req):
        from backend.tools.google_slides_tool import GoogleSlidesTool
        tool = GoogleSlidesTool()
        result = _run(tool.execute(
            action="read_presentation",
            presentation_id="pres-123",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "not installed" in result["error"]


class TestSlidesHelpers:
    """Tests for module-level helper functions."""

    def test_text_content_to_string(self):
        from backend.tools.google_slides_tool import _text_content_to_string

        tc = {"textElements": [
            {"textRun": {"content": "Hello "}},
            {"textRun": {"content": "World"}},
        ]}
        assert _text_content_to_string(tc) == "Hello World"

    def test_text_content_to_string_empty(self):
        from backend.tools.google_slides_tool import _text_content_to_string
        assert _text_content_to_string({}) == ""
        assert _text_content_to_string(None) == ""

    def test_extract_shapes_unknown_type(self):
        from backend.tools.google_slides_tool import _extract_shapes

        slide = {
            "pageElements": [
                {"objectId": "unk-001", "size": {}, "transform": {}},
            ]
        }
        shapes = _extract_shapes(slide)
        assert len(shapes) == 1
        assert shapes[0]["type"] == "unknown"

    def test_api_error_generic(self):
        from backend.tools.google_slides_tool import _api_error

        result = _api_error("test_action", "pres-xyz", RuntimeError("oops"))
        assert result["success"] is False
        assert result["action"] == "test_action"
        assert result["presentationId"] == "pres-xyz"
        assert "oops" in result["error"]
        assert "status_code" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Google Sheets Tool
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoogleSheetsToolMetadata:
    """Tool metadata: name, description, parameters, action lists."""

    def test_name(self):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        assert tool.name == "google_sheets"

    def test_description(self):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        assert "Google Sheets" in tool.description

    def test_parameters_schema_required(self):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        params = tool.parameters
        assert "action" in params["properties"]
        assert "spreadsheet_id" in params["required"]
        assert "credentials" in params["required"]

    def test_action_enum(self):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        actions = tool.parameters["properties"]["action"]["enum"]
        expected = [
            "read_spreadsheet", "read_range", "write_range", "append_data",
            "create_sheet", "delete_sheet", "clear_range", "get_cell_formats",
            "batch_update",
        ]
        assert actions == expected

    def test_to_ollama_tool(self):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        ollama = tool.to_ollama_tool()
        assert ollama["function"]["name"] == "google_sheets"


class TestSheetsReadSpreadsheet:
    """Tests for the read_spreadsheet action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service", return_value=_make_sheets_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_spreadsheet",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["title"] == "Budget 2024"
        assert len(result["result"]["sheets"]) == 2
        assert result["result"]["sheets"][0]["title"] == "Sheet1"

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_credentials(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_spreadsheet",
            spreadsheet_id="ss-abc",
        ))
        assert result["success"] is False
        assert "credentials" in result["error"]

    def test_missing_spreadsheet_id(self):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_spreadsheet",
            spreadsheet_id="",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "spreadsheet_id" in result["error"]


class TestSheetsReadRange:
    """Tests for the read_range action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service", return_value=_make_sheets_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_range",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1:C3",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["values"][0] == ["Name", "Age", "City"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_range(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_range",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "range_notation" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = (
            Exception("Range not found")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_range",
            spreadsheet_id="ss-abc",
            range_notation="BadRange!Z99",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Range not found" in result["error"]


class TestSheetsWriteRange:
    """Tests for the write_range action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service", return_value=_make_sheets_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="write_range",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1:C2",
            values=[["a", "b", "c"], ["d", "e", "f"]],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["updated_cells"] == 6

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_range(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="write_range",
            spreadsheet_id="ss-abc",
            values=[["a"]],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "range_notation" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_values(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="write_range",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "values" in result["error"]


class TestSheetsAppendData:
    """Tests for the append_data action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service", return_value=_make_sheets_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="append_data",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1:C1",
            values=[["Charlie", "35", "Chicago"]],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["updated_rows"] == 1

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_range(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="append_data",
            spreadsheet_id="ss-abc",
            values=[["x"]],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "range_notation" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_values(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="append_data",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "values" in result["error"]


class TestSheetsCreateSheet:
    """Tests for the create_sheet action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service", return_value=_make_sheets_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="create_sheet",
            spreadsheet_id="ss-abc",
            sheet_name="NewTab",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["sheet_id"] == 456
        assert result["result"]["title"] == "NewTab"

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_sheet_name(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="create_sheet",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "sheet_name" in result["error"]


class TestSheetsDeleteSheet:
    """Tests for the delete_sheet action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_happy_path(self, mock_build, mock_req):
        svc = _make_sheets_service()
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="delete_sheet",
            spreadsheet_id="ss-abc",
            sheet_id=123,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["deleted_sheet_id"] == 123

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_sheet_id(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="delete_sheet",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "sheet_id" in result["error"]


class TestSheetsClearRange:
    """Tests for the clear_range action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service", return_value=_make_sheets_service())
    def test_happy_path(self, mock_build, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="clear_range",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1:D10",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["cleared_range"] == "Sheet1!A1:D10"

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_range(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="clear_range",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "range_notation" in result["error"]


class TestSheetsGetCellFormats:
    """Tests for the get_cell_formats action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_happy_path(self, mock_build, mock_req):
        svc = _make_sheets_service(get_response=CANNED_FORMATS_RESPONSE)
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="get_cell_formats",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        formats = result["result"]["cell_formats"]
        assert len(formats) == 1  # 1 row
        assert formats[0][0]["bold"] is True
        assert formats[0][0]["font_family"] == "Arial"
        assert formats[0][0]["number_format_type"] == "NUMBER"

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_range(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="get_cell_formats",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "range_notation" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_empty_response(self, mock_build, mock_req):
        svc = _make_sheets_service(get_response={"sheets": []})
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="get_cell_formats",
            spreadsheet_id="ss-abc",
            range_notation="Empty!A1",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert result["result"]["cell_formats"] == []
        assert result["result"]["sheet_title"] == ""


class TestSheetsBatchUpdate:
    """Tests for the batch_update action."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_happy_path(self, mock_build, mock_req):
        svc = _make_sheets_service(batch_response=CANNED_BATCH_UPDATE_SHEETS)
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="batch_update",
            spreadsheet_id="ss-abc",
            requests=[{"addConditionalFormatRule": {}}],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is True
        assert len(result["result"]["replies"]) == 1

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_missing_requests(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="batch_update",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "requests" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_empty_requests_list(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="batch_update",
            spreadsheet_id="ss-abc",
            requests=[],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "requests" in result["error"]


class TestSheetsInvalidAction:
    """Test that invalid/unknown actions fail cleanly."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    def test_unknown_action(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="pivot_table",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Unknown action" in result["error"]
        assert "pivot_table" in result["error"]


class TestSheetsGoogleApiNotInstalled:
    """Verify graceful failure when google-api-python-client is missing."""

    @patch(
        "backend.tools.google_sheets_tool._require_gapi",
        side_effect=RuntimeError("google-api-python-client is not installed"),
    )
    def test_returns_error(self, mock_req):
        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_spreadsheet",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "not installed" in result["error"]


class TestSheetsApiErrors:
    """Test Sheets API error handling for various actions."""

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_read_spreadsheet_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.get.return_value.execute.side_effect = (
            Exception("Permission denied")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="read_spreadsheet",
            spreadsheet_id="ss-abc",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Permission denied" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_write_range_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.values.return_value.update.return_value.execute.side_effect = (
            Exception("Quota limit")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="write_range",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1",
            values=[["x"]],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Quota limit" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_create_sheet_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.batchUpdate.return_value.execute.side_effect = (
            Exception("Duplicate sheet name")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="create_sheet",
            spreadsheet_id="ss-abc",
            sheet_name="Existing",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Duplicate sheet name" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_delete_sheet_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.batchUpdate.return_value.execute.side_effect = (
            Exception("Cannot delete only sheet")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="delete_sheet",
            spreadsheet_id="ss-abc",
            sheet_id=0,
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Cannot delete only sheet" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_clear_range_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.values.return_value.clear.return_value.execute.side_effect = (
            Exception("Invalid range")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="clear_range",
            spreadsheet_id="ss-abc",
            range_notation="Bad!Range",
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Invalid range" in result["error"]

    @patch("backend.tools.google_sheets_tool._require_gapi")
    @patch("backend.tools.google_sheets_tool._build_service")
    def test_append_data_api_error(self, mock_build, mock_req):
        svc = MagicMock()
        svc.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("Service unavailable")
        )
        mock_build.return_value = svc

        from backend.tools.google_sheets_tool import GoogleSheetsTool
        tool = GoogleSheetsTool()
        result = _run(tool.execute(
            action="append_data",
            spreadsheet_id="ss-abc",
            range_notation="Sheet1!A1",
            values=[["x"]],
            credentials=_mock_credentials(),
        ))
        assert result["success"] is False
        assert "Service unavailable" in result["error"]
