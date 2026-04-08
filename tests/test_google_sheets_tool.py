"""
Tests for the Google Sheets tool — covers every action, validation path,
and error branch WITHOUT hitting any external Google API.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.tools.google_sheets_tool import GoogleSheetsTool

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_SPREADSHEET_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"


@pytest.fixture
def mock_credentials():
    """Return a dummy credentials object (stands in for google.oauth2.credentials.Credentials)."""
    creds = MagicMock()
    creds.token = "fake-token"
    creds.valid = True
    return creds


@pytest.fixture
def tool():
    """Instantiate the GoogleSheetsTool."""
    return GoogleSheetsTool()


@pytest.fixture
def mock_service():
    """Build a deeply-nested mock that mirrors the Sheets v4 service resource."""
    svc = MagicMock()
    # Convenience: make every chain return an executable mock
    svc.spreadsheets.return_value.get.return_value.execute.return_value = {}
    svc.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {}
    svc.spreadsheets.return_value.values.return_value.update.return_value.execute.return_value = {}
    svc.spreadsheets.return_value.values.return_value.append.return_value.execute.return_value = {}
    svc.spreadsheets.return_value.values.return_value.clear.return_value.execute.return_value = {}
    svc.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {}
    return svc


def _patch_build(mock_service):
    """Return a patcher that replaces googleapiclient.discovery.build with our mock."""
    return patch(
        "googleapiclient.discovery.build",
        return_value=mock_service,
    )


def _patch_gapi_available():
    """Ensure _require_gapi succeeds without the real library."""
    return patch("backend.tools.google_sheets_tool._gapi_available", True)


# ---------------------------------------------------------------------------
# Init & metadata
# ---------------------------------------------------------------------------


class TestGoogleSheetsToolInit:
    def test_name(self, tool):
        assert tool.name == "google_sheets"

    def test_description_non_empty(self, tool):
        assert len(tool.description) > 10

    def test_parameters_has_required_fields(self, tool):
        params = tool.parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert "spreadsheet_id" in params["properties"]
        assert "credentials" in params["properties"]

    def test_required_list(self, tool):
        # credentials is optional (self-auth pattern loads OAuth internally)
        assert set(tool.parameters["required"]) == {"action", "spreadsheet_id"}

    def test_action_enum_lists_all_actions(self, tool):
        expected = {
            "read_spreadsheet", "read_range", "write_range", "append_data",
            "create_sheet", "delete_sheet", "clear_range", "get_cell_formats",
            "batch_update",
        }
        actual = set(tool.parameters["properties"]["action"]["enum"])
        assert actual == expected

    def test_to_ollama_tool(self, tool):
        ot = tool.to_ollama_tool()
        assert ot["type"] == "function"
        assert ot["function"]["name"] == "google_sheets"


# ---------------------------------------------------------------------------
# read_spreadsheet
# ---------------------------------------------------------------------------


class TestReadSpreadsheet:
    @pytest.mark.asyncio
    async def test_returns_title_and_sheets(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "properties": {"title": "Budget 2026"},
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
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="read_spreadsheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )

        assert res["success"] is True
        result = res["result"]
        assert result["title"] == "Budget 2026"
        assert len(result["sheets"]) == 2
        assert result["sheets"][0]["title"] == "Sheet1"
        assert result["sheets"][1]["row_count"] == 500

    @pytest.mark.asyncio
    async def test_empty_sheets_list(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "properties": {"title": "Empty"},
            "sheets": [],
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="read_spreadsheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["sheets"] == []


# ---------------------------------------------------------------------------
# read_range
# ---------------------------------------------------------------------------


class TestReadRange:
    @pytest.mark.asyncio
    async def test_returns_values(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "range": "Sheet1!A1:B2",
            "majorDimension": "ROWS",
            "values": [["Name", "Age"], ["Alice", "30"]],
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="read_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:B2",
                credentials=mock_credentials,
            )

        assert res["success"] is True
        assert res["result"]["values"] == [["Name", "Age"], ["Alice", "30"]]
        assert res["result"]["major_dimension"] == "ROWS"

    @pytest.mark.asyncio
    async def test_empty_range_returns_empty_values(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
            "range": "Sheet1!Z1:Z1",
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="read_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!Z1:Z1",
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["values"] == []

    @pytest.mark.asyncio
    async def test_missing_range_notation(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="read_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "range_notation" in res["error"]


# ---------------------------------------------------------------------------
# write_range
# ---------------------------------------------------------------------------


class TestWriteRange:
    @pytest.mark.asyncio
    async def test_write_success(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.update.return_value.execute.return_value = {
            "updatedRange": "Sheet1!A1:B2",
            "updatedRows": 2,
            "updatedColumns": 2,
            "updatedCells": 4,
        }
        values = [["X", "Y"], ["1", "2"]]
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="write_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:B2",
                values=values,
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["updated_cells"] == 4

    @pytest.mark.asyncio
    async def test_missing_range_notation(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="write_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                values=[["a"]],
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "range_notation" in res["error"]

    @pytest.mark.asyncio
    async def test_missing_values(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="write_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "values" in res["error"]


# ---------------------------------------------------------------------------
# append_data
# ---------------------------------------------------------------------------


class TestAppendData:
    @pytest.mark.asyncio
    async def test_append_success(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.append.return_value.execute.return_value = {
            "tableRange": "Sheet1!A1:B5",
            "updates": {
                "updatedRange": "Sheet1!A6:B7",
                "updatedRows": 2,
                "updatedColumns": 2,
                "updatedCells": 4,
            },
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="append_data",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:B1",
                values=[["new1", "new2"], ["new3", "new4"]],
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["updated_rows"] == 2
        assert res["result"]["table_range"] == "Sheet1!A1:B5"

    @pytest.mark.asyncio
    async def test_missing_range(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="append_data",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                values=[["a"]],
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "range_notation" in res["error"]

    @pytest.mark.asyncio
    async def test_missing_values(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="append_data",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "values" in res["error"]


# ---------------------------------------------------------------------------
# create_sheet
# ---------------------------------------------------------------------------


class TestCreateSheet:
    @pytest.mark.asyncio
    async def test_create_sheet_success(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
            "replies": [
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": 999,
                            "title": "NewTab",
                            "index": 2,
                        }
                    }
                }
            ]
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="create_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                sheet_name="NewTab",
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["sheet_id"] == 999
        assert res["result"]["title"] == "NewTab"

    @pytest.mark.asyncio
    async def test_missing_sheet_name(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="create_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "sheet_name" in res["error"]

    @pytest.mark.asyncio
    async def test_empty_replies_fallback(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
            "replies": []
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="create_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                sheet_name="FallbackTab",
                credentials=mock_credentials,
            )
        assert res["success"] is True
        # title falls back to sheet_name when replies are empty
        assert res["result"]["title"] == "FallbackTab"
        assert res["result"]["sheet_id"] is None


# ---------------------------------------------------------------------------
# delete_sheet
# ---------------------------------------------------------------------------


class TestDeleteSheet:
    @pytest.mark.asyncio
    async def test_delete_sheet_success(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {}
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="delete_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                sheet_id=42,
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["deleted_sheet_id"] == 42

    @pytest.mark.asyncio
    async def test_missing_sheet_id(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="delete_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "sheet_id" in res["error"]


# ---------------------------------------------------------------------------
# clear_range
# ---------------------------------------------------------------------------


class TestClearRange:
    @pytest.mark.asyncio
    async def test_clear_range_success(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.clear.return_value.execute.return_value = {
            "clearedRange": "Sheet1!A1:C5",
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="clear_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:C5",
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["cleared_range"] == "Sheet1!A1:C5"

    @pytest.mark.asyncio
    async def test_missing_range(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="clear_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "range_notation" in res["error"]


# ---------------------------------------------------------------------------
# get_cell_formats
# ---------------------------------------------------------------------------


class TestGetCellFormats:
    @pytest.mark.asyncio
    async def test_returns_formats(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.get.return_value.execute.return_value = {
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
                                                    "fontSize": 12,
                                                    "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                                                },
                                                "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                                                "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"},
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
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="get_cell_formats",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1",
                credentials=mock_credentials,
            )
        assert res["success"] is True
        fmts = res["result"]["cell_formats"]
        assert len(fmts) == 1  # one row
        assert fmts[0][0]["bold"] is True
        assert fmts[0][0]["font_family"] == "Arial"
        assert fmts[0][0]["number_format_type"] == "NUMBER"
        assert fmts[0][0]["horizontal_alignment"] == "LEFT"
        assert fmts[0][0]["user_entered_format"] == {"textFormat": {"bold": True}}

    @pytest.mark.asyncio
    async def test_empty_sheets_response(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.get.return_value.execute.return_value = {
            "sheets": [],
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="get_cell_formats",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1",
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["cell_formats"] == []
        assert res["result"]["sheet_title"] == ""

    @pytest.mark.asyncio
    async def test_missing_range(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="get_cell_formats",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "range_notation" in res["error"]


# ---------------------------------------------------------------------------
# batch_update
# ---------------------------------------------------------------------------


class TestBatchUpdate:
    @pytest.mark.asyncio
    async def test_batch_update_success(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
            "spreadsheetId": FAKE_SPREADSHEET_ID,
            "replies": [{"addSheet": {"properties": {"sheetId": 7}}}],
        }
        reqs = [{"addSheet": {"properties": {"title": "AutoTab"}}}]
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="batch_update",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                requests=reqs,
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["spreadsheet_id"] == FAKE_SPREADSHEET_ID
        assert len(res["result"]["replies"]) == 1

    @pytest.mark.asyncio
    async def test_missing_requests(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="batch_update",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "requests" in res["error"]

    @pytest.mark.asyncio
    async def test_empty_requests_list(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="batch_update",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                requests=[],
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "non-empty" in res["error"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_missing_spreadsheet_id(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="read_spreadsheet",
                spreadsheet_id="",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "spreadsheet_id" in res["error"]

    @pytest.mark.asyncio
    async def test_missing_credentials(self, tool):
        with _patch_gapi_available():
            res = await tool.execute(
                action="read_spreadsheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
            )
        assert res["success"] is False
        assert "credentials" in res["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool, mock_credentials):
        with _patch_gapi_available():
            res = await tool.execute(
                action="explode_spreadsheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "Unknown action" in res["error"]
        assert "explode_spreadsheet" in res["error"]

    @pytest.mark.asyncio
    async def test_gapi_not_installed(self, tool, mock_credentials):
        with patch("backend.tools.google_sheets_tool._gapi_available", False):
            # _require_gapi will try to import and fail; force that path
            with patch(
                "backend.tools.google_sheets_tool._require_gapi",
                side_effect=RuntimeError("google-api-python-client is not installed"),
            ):
                res = await tool.execute(
                    action="read_spreadsheet",
                    spreadsheet_id=FAKE_SPREADSHEET_ID,
                    credentials=mock_credentials,
                )
        assert res["success"] is False
        assert "not installed" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_propagated_in_read_range(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = (
            Exception("403 Forbidden")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="read_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:A1",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "403 Forbidden" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_write_range(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.update.return_value.execute.side_effect = (
            Exception("500 Internal Server Error")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="write_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1",
                values=[["oops"]],
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "500" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_append_data(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("quota exceeded")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="append_data",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1",
                values=[["data"]],
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "quota" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_create_sheet(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.side_effect = (
            Exception("duplicate sheet name")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="create_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                sheet_name="Sheet1",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "duplicate" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_delete_sheet(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.side_effect = (
            Exception("sheet not found")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="delete_sheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                sheet_id=9999,
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "sheet not found" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_clear_range(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.values.return_value.clear.return_value.execute.side_effect = (
            Exception("permission denied")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="clear_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:Z100",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "permission denied" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_get_cell_formats(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.get.return_value.execute.side_effect = (
            Exception("invalid range")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="get_cell_formats",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="BadRange!!!",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "invalid range" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_batch_update(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.batchUpdate.return_value.execute.side_effect = (
            Exception("invalid request body")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="batch_update",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                requests=[{"bogus": {}}],
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "invalid request" in res["error"]

    @pytest.mark.asyncio
    async def test_api_error_in_read_spreadsheet(self, tool, mock_credentials, mock_service):
        mock_service.spreadsheets.return_value.get.return_value.execute.side_effect = (
            Exception("not found")
        )
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="read_spreadsheet",
                spreadsheet_id="nonexistent-id",
                credentials=mock_credentials,
            )
        assert res["success"] is False
        assert "not found" in res["error"]

    @pytest.mark.asyncio
    async def test_large_data_write(self, tool, mock_credentials, mock_service):
        """Writing a large 2D array (1000 rows x 20 cols) should succeed."""
        big_values = [["cell"] * 20 for _ in range(1000)]
        mock_service.spreadsheets.return_value.values.return_value.update.return_value.execute.return_value = {
            "updatedRange": "Sheet1!A1:T1000",
            "updatedRows": 1000,
            "updatedColumns": 20,
            "updatedCells": 20000,
        }
        with _patch_gapi_available(), _patch_build(mock_service):
            res = await tool.execute(
                action="write_range",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                range_notation="Sheet1!A1:T1000",
                values=big_values,
                credentials=mock_credentials,
            )
        assert res["success"] is True
        assert res["result"]["updated_cells"] == 20000

    @pytest.mark.asyncio
    async def test_credentials_none_explicit(self, tool):
        """Passing credentials=None should be caught before any API call."""
        with _patch_gapi_available():
            res = await tool.execute(
                action="read_spreadsheet",
                spreadsheet_id=FAKE_SPREADSHEET_ID,
                credentials=None,
            )
        assert res["success"] is False
        assert "credentials" in res["error"]
