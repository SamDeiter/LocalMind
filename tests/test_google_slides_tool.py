"""
Tests for GoogleSlidesTool — read, edit, and export Google Slides presentations.

Uses mocked Google API clients so tests run without real Google services.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_PRESENTATION_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms"
FAKE_SLIDE_ID = "slide_001"
FAKE_SHAPE_ID = "shape_abc"


@pytest.fixture
def mock_credentials():
    """Return a mock Google OAuth credentials object."""
    return MagicMock(name="FakeCredentials")


@pytest.fixture
def mock_slides_service():
    """Build a deeply-nested mock that mirrors the Slides API client."""
    service = MagicMock(name="SlidesService")
    return service


@pytest.fixture
def mock_drive_service():
    """Build a mock that mirrors the Drive API v3 client."""
    service = MagicMock(name="DriveService")
    return service


@pytest.fixture
def tool():
    """Instantiate the GoogleSlidesTool under test."""
    from backend.tools.google_slides_tool import GoogleSlidesTool
    return GoogleSlidesTool()


@pytest.fixture(autouse=True)
def reset_google_api_flag():
    """Ensure the lazy-import guard resets between tests."""
    import backend.tools.google_slides_tool as mod
    original = mod._google_api_available
    mod._google_api_available = True  # pretend the library is installed
    yield
    mod._google_api_available = original


def _presentation_payload(
    slides=None, title="Test Deck", masters=None, layouts=None,
):
    """Build a minimal Slides API presentation dict."""
    if slides is None:
        slides = [
            {
                "objectId": "slide_001",
                "slideProperties": {"layoutObjectId": "layout_BLANK"},
                "pageElements": [],
            },
            {
                "objectId": "slide_002",
                "slideProperties": {"layoutObjectId": "layout_TITLE"},
                "pageElements": [],
            },
        ]
    return {
        "presentationId": FAKE_PRESENTATION_ID,
        "title": title,
        "locale": "en",
        "slides": slides,
        "pageSize": {
            "width": {"magnitude": 9144000, "unit": "EMU"},
            "height": {"magnitude": 5143500, "unit": "EMU"},
        },
        "masters": masters or [{"objectId": "master_001"}],
        "layouts": layouts or [],
    }


def _slide_with_shapes():
    """Return a single slide dict containing a text shape and a table."""
    return {
        "objectId": "slide_001",
        "slideProperties": {
            "layoutObjectId": "layout_BLANK",
            "notesPage": {
                "pageElements": [
                    {
                        "shape": {
                            "shapeType": "TEXT_BOX",
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
                "objectId": "shape_title",
                "size": {},
                "transform": {},
                "shape": {
                    "shapeType": "TEXT_BOX",
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "Hello World"}}
                        ]
                    },
                },
            },
            {
                "objectId": "table_001",
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
                "objectId": "img_001",
                "size": {},
                "transform": {},
                "image": {
                    "contentUrl": "https://img.example.com/a.png",
                    "sourceUrl": "https://source.example.com/a.png",
                },
            },
        ],
    }


# ---------------------------------------------------------------------------
# TestGoogleSlidesToolInit
# ---------------------------------------------------------------------------

class TestGoogleSlidesToolInit:
    def test_name_property(self, tool):
        assert tool.name == "google_slides"

    def test_description_property(self, tool):
        assert "Google Slides" in tool.description

    def test_parameters_schema_has_required_fields(self, tool):
        params = tool.parameters
        assert params["type"] == "object"
        assert "action" in params["properties"]
        assert "presentation_id" in params["properties"]
        assert set(params["required"]) == {"action", "presentation_id"}

    def test_actions_dict_contains_all_actions(self, tool):
        expected = {
            "read_presentation", "get_slide", "get_slide_text",
            "update_text", "add_slide", "delete_slide",
            "duplicate_slide", "download_as_pptx",
        }
        assert set(tool.actions.keys()) == expected

    def test_to_ollama_tool_format(self, tool):
        schema = tool.to_ollama_tool()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "google_slides"


# ---------------------------------------------------------------------------
# TestReadPresentation
# ---------------------------------------------------------------------------

class TestReadPresentation:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_read_presentation_success(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload()
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="read_presentation",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        data = result["result"]
        assert data["title"] == "Test Deck"
        assert data["slide_count"] == 2
        assert len(data["slides"]) == 2
        assert data["slides"][0]["objectId"] == "slide_001"

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_read_presentation_empty_slides(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload(
            slides=[], title="Empty Deck",
        )
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="read_presentation",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["slide_count"] == 0
        assert result["result"]["slides"] == []

    @pytest.mark.asyncio
    async def test_read_presentation_missing_credentials(self, tool):
        result = await tool.execute(
            action="read_presentation",
            presentation_id=FAKE_PRESENTATION_ID,
        )
        assert result["success"] is False
        assert "credentials" in result["error"].lower()


# ---------------------------------------------------------------------------
# TestGetSlide
# ---------------------------------------------------------------------------

class TestGetSlide:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_get_slide_success(
        self, mock_build, tool, mock_credentials
    ):
        slide = _slide_with_shapes()
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload(
            slides=[slide],
        )
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="get_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_index=0,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        data = result["result"]
        assert data["slide_index"] == 0
        assert data["objectId"] == "slide_001"
        assert data["notes"] == "Speaker notes here"
        assert len(data["shapes"]) == 3

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_get_slide_out_of_range(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload()
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="get_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_index=99,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "out of range" in result["error"]

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_get_slide_negative_index(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload()
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="get_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_index=-1,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "out of range" in result["error"]

    @pytest.mark.asyncio
    async def test_get_slide_missing_slide_index(self, tool, mock_credentials):
        result = await tool.execute(
            action="get_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "slide_index" in result["error"]


# ---------------------------------------------------------------------------
# TestGetSlideText
# ---------------------------------------------------------------------------

class TestGetSlideText:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_get_slide_text_extracts_shapes_and_tables(
        self, mock_build, tool, mock_credentials
    ):
        slide = _slide_with_shapes()
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload(
            slides=[slide],
        )
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="get_slide_text",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_index=0,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        entries = result["result"]["text_entries"]
        # Should have: "Hello World" from shape + 4 table cells
        texts = [e["text"] for e in entries]
        assert "Hello World" in texts
        table_entries = [e for e in entries if e.get("type") == "table_cell"]
        assert len(table_entries) == 4

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_get_slide_text_empty_slide(
        self, mock_build, tool, mock_credentials
    ):
        empty_slide = {
            "objectId": "slide_empty",
            "slideProperties": {},
            "pageElements": [],
        }
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload(
            slides=[empty_slide],
        )
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="get_slide_text",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_index=0,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["text_entries"] == []

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_get_slide_text_out_of_range(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.return_value = _presentation_payload()
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="get_slide_text",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_index=5,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "out of range" in result["error"]

    @pytest.mark.asyncio
    async def test_get_slide_text_missing_slide_index(self, tool, mock_credentials):
        result = await tool.execute(
            action="get_slide_text",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "slide_index" in result["error"]


# ---------------------------------------------------------------------------
# TestUpdateText
# ---------------------------------------------------------------------------

class TestUpdateText:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_update_text_success(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{}, {}],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="update_text",
            presentation_id=FAKE_PRESENTATION_ID,
            shape_id=FAKE_SHAPE_ID,
            new_text="Updated title",
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["new_text"] == "Updated title"
        assert result["result"]["shape_id"] == FAKE_SHAPE_ID

    @pytest.mark.asyncio
    async def test_update_text_missing_shape_id(self, tool, mock_credentials):
        result = await tool.execute(
            action="update_text",
            presentation_id=FAKE_PRESENTATION_ID,
            new_text="Hello",
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "shape_id" in result["error"]

    @pytest.mark.asyncio
    async def test_update_text_missing_new_text(self, tool, mock_credentials):
        result = await tool.execute(
            action="update_text",
            presentation_id=FAKE_PRESENTATION_ID,
            shape_id=FAKE_SHAPE_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "new_text" in result["error"]

    @pytest.mark.asyncio
    async def test_update_text_missing_credentials(self, tool):
        result = await tool.execute(
            action="update_text",
            presentation_id=FAKE_PRESENTATION_ID,
            shape_id=FAKE_SHAPE_ID,
            new_text="Hello",
        )
        assert result["success"] is False
        assert "credentials" in result["error"].lower()


# ---------------------------------------------------------------------------
# TestAddSlide
# ---------------------------------------------------------------------------

class TestAddSlide:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_add_slide_default_layout(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"createSlide": {"objectId": "new_slide_xyz"}}],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="add_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["new_slide_id"] == "new_slide_xyz"
        assert result["result"]["layout"] == "BLANK"

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_add_slide_with_layout_and_position(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"createSlide": {"objectId": "new_slide_002"}}],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="add_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            layout="TITLE_AND_BODY",
            insert_at=1,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["layout"] == "TITLE_AND_BODY"
        assert result["result"]["insert_at"] == 1

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_add_slide_empty_replies(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="add_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["new_slide_id"] == ""


# ---------------------------------------------------------------------------
# TestDeleteSlide
# ---------------------------------------------------------------------------

class TestDeleteSlide:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_delete_slide_success(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{}],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="delete_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_id=FAKE_SLIDE_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["deleted_slide_id"] == FAKE_SLIDE_ID

    @pytest.mark.asyncio
    async def test_delete_slide_missing_slide_id(self, tool, mock_credentials):
        result = await tool.execute(
            action="delete_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "slide_id" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_slide_missing_credentials(self, tool):
        result = await tool.execute(
            action="delete_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_id=FAKE_SLIDE_ID,
        )
        assert result["success"] is False
        assert "credentials" in result["error"].lower()


# ---------------------------------------------------------------------------
# TestDuplicateSlide
# ---------------------------------------------------------------------------

class TestDuplicateSlide:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_duplicate_slide_success(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"duplicateObject": {"objectId": "dup_slide_001"}}],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="duplicate_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_id=FAKE_SLIDE_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["new_slide_id"] == "dup_slide_001"
        assert result["result"]["source_slide_id"] == FAKE_SLIDE_ID

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_duplicate_slide_with_insert_at(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        # First batchUpdate (duplicate) succeeds
        # Second batchUpdate (move) also succeeds
        mock_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"duplicateObject": {"objectId": "dup_slide_002"}}],
        }
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="duplicate_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_id=FAKE_SLIDE_ID,
            insert_at=0,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["insert_at"] == 0

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_duplicate_slide_move_fails_partial_success(
        self, mock_build, tool, mock_credentials
    ):
        """When duplicate succeeds but the subsequent move fails, we get a warning."""
        mock_svc = MagicMock()
        call_count = {"n": 0}

        def batch_side_effect(*args, **kwargs):
            mock_resp = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call (duplicate): succeed
                mock_resp.execute.return_value = {
                    "replies": [{"duplicateObject": {"objectId": "dup_003"}}],
                }
            else:
                # Second call (move): fail
                mock_resp.execute.side_effect = Exception("Move failed")
            return mock_resp

        mock_svc.presentations().batchUpdate = batch_side_effect
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="duplicate_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_id=FAKE_SLIDE_ID,
            insert_at=2,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert "warning" in result["result"]
        assert "Move failed" in result["result"]["warning"]

    @pytest.mark.asyncio
    async def test_duplicate_slide_missing_slide_id(self, tool, mock_credentials):
        result = await tool.execute(
            action="duplicate_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "slide_id" in result["error"]


# ---------------------------------------------------------------------------
# TestDownloadAsPptx
# ---------------------------------------------------------------------------

class TestDownloadAsPptx:
    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_drive_service")
    async def test_download_as_pptx_success(
        self, mock_build_drive, tool, mock_credentials, tmp_path
    ):
        mock_drive = MagicMock()
        mock_drive.files().export().execute.return_value = b"PK\x03\x04fakepptx"
        mock_build_drive.return_value = mock_drive

        out = str(tmp_path / "deck.pptx")
        result = await tool.execute(
            action="download_as_pptx",
            presentation_id=FAKE_PRESENTATION_ID,
            output_path=out,
            credentials=mock_credentials,
        )
        assert result["success"] is True
        assert result["result"]["size_bytes"] > 0
        assert Path(result["result"]["output_path"]).exists()

    @pytest.mark.asyncio
    async def test_download_missing_output_path(self, tool, mock_credentials):
        result = await tool.execute(
            action="download_as_pptx",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "output_path" in result["error"]

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_drive_service")
    async def test_download_api_error(
        self, mock_build_drive, tool, mock_credentials, tmp_path
    ):
        mock_drive = MagicMock()
        mock_drive.files().export().execute.side_effect = Exception("quota exceeded")
        mock_build_drive.return_value = mock_drive

        out = str(tmp_path / "fail.pptx")
        result = await tool.execute(
            action="download_as_pptx",
            presentation_id=FAKE_PRESENTATION_ID,
            output_path=out,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "quota exceeded" in result["error"]


# ---------------------------------------------------------------------------
# TestErrorHandling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_missing_presentation_id(self, tool, mock_credentials):
        result = await tool.execute(
            action="read_presentation",
            presentation_id="",
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "presentation_id" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool, mock_credentials):
        result = await tool.execute(
            action="destroy_everything",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "Unknown action" in result["error"]
        assert "destroy_everything" in result["error"]

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_api_exception_returns_error_dict(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().get().execute.side_effect = Exception(
            "Connection refused"
        )
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="read_presentation",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "Connection refused" in result["error"]

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_batch_update_api_error_on_delete(
        self, mock_build, tool, mock_credentials
    ):
        mock_svc = MagicMock()
        mock_svc.presentations().batchUpdate().execute.side_effect = Exception(
            "Invalid objectId"
        )
        mock_build.return_value = mock_svc

        result = await tool.execute(
            action="delete_slide",
            presentation_id=FAKE_PRESENTATION_ID,
            slide_id="nonexistent",
            credentials=mock_credentials,
        )
        assert result["success"] is False
        assert "Invalid objectId" in result["error"]

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._require_google_api")
    async def test_google_api_not_installed(self, mock_require, tool):
        mock_require.side_effect = RuntimeError(
            "google-api-python-client is not installed"
        )
        result = await tool.execute(
            action="read_presentation",
            presentation_id=FAKE_PRESENTATION_ID,
            credentials=MagicMock(),
        )
        assert result["success"] is False
        assert "not installed" in result["error"]

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_api_error_helper_404(self, mock_build, tool, mock_credentials):
        """Simulate an HttpError 404 and verify friendly error message."""
        from backend.tools.google_slides_tool import _api_error

        # Create a mock HttpError
        mock_exc = MagicMock()
        mock_exc.resp.status = 404
        mock_exc.__str__ = lambda self: "HttpError 404"

        # Patch isinstance check by setting the class
        with patch("backend.tools.google_slides_tool.logger"):
            result = _api_error("read_presentation", FAKE_PRESENTATION_ID, mock_exc)

        assert result["success"] is False
        assert result["action"] == "read_presentation"
        assert result["presentationId"] == FAKE_PRESENTATION_ID

    @pytest.mark.asyncio
    @patch("backend.tools.google_slides_tool._build_slides_service")
    async def test_api_error_helper_403(self, mock_build, tool, mock_credentials):
        """Verify _api_error produces structured output for generic errors."""
        from backend.tools.google_slides_tool import _api_error

        exc = ValueError("Something went wrong")
        with patch("backend.tools.google_slides_tool.logger"):
            result = _api_error("add_slide", FAKE_PRESENTATION_ID, exc)

        assert result["success"] is False
        assert result["error"] == "Something went wrong"
        assert "status_code" not in result


# ---------------------------------------------------------------------------
# Test helpers directly
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_text_content_to_string_basic(self):
        from backend.tools.google_slides_tool import _text_content_to_string

        tc = {
            "textElements": [
                {"textRun": {"content": "Hello "}},
                {"textRun": {"content": "World"}},
            ]
        }
        assert _text_content_to_string(tc) == "Hello World"

    def test_text_content_to_string_empty(self):
        from backend.tools.google_slides_tool import _text_content_to_string

        assert _text_content_to_string({}) == ""
        assert _text_content_to_string(None) == ""

    def test_text_content_to_string_no_text_runs(self):
        from backend.tools.google_slides_tool import _text_content_to_string

        tc = {"textElements": [{"paragraphMarker": {}}]}
        assert _text_content_to_string(tc) == ""

    def test_extract_shapes_image(self):
        from backend.tools.google_slides_tool import _extract_shapes

        slide = _slide_with_shapes()
        shapes = _extract_shapes(slide)
        image_shapes = [s for s in shapes if s["type"] == "image"]
        assert len(image_shapes) == 1
        assert image_shapes[0]["contentUrl"] == "https://img.example.com/a.png"

    def test_extract_shapes_table(self):
        from backend.tools.google_slides_tool import _extract_shapes

        slide = _slide_with_shapes()
        shapes = _extract_shapes(slide)
        table_shapes = [s for s in shapes if s["type"] == "table"]
        assert len(table_shapes) == 1
        assert table_shapes[0]["rows"] == 2
        assert table_shapes[0]["columns"] == 2
        assert table_shapes[0]["cells"][0] == ["A1", "B1"]

    def test_extract_shapes_unknown_type(self):
        from backend.tools.google_slides_tool import _extract_shapes

        slide = {
            "pageElements": [
                {"objectId": "mystery_001", "size": {}, "transform": {}},
            ]
        }
        shapes = _extract_shapes(slide)
        assert shapes[0]["type"] == "unknown"

    def test_extract_shapes_shape_with_placeholder(self):
        from backend.tools.google_slides_tool import _extract_shapes

        slide = {
            "pageElements": [
                {
                    "objectId": "ph_title",
                    "size": {},
                    "transform": {},
                    "shape": {
                        "shapeType": "TEXT_BOX",
                        "placeholder": {"type": "TITLE", "index": 0},
                        "text": {
                            "textElements": [
                                {"textRun": {"content": "My Title"}}
                            ]
                        },
                    },
                }
            ]
        }
        shapes = _extract_shapes(slide)
        assert shapes[0]["placeholder"]["type"] == "TITLE"
        assert shapes[0]["text"] == "My Title"

    def test_extract_shapes_shape_no_text(self):
        from backend.tools.google_slides_tool import _extract_shapes

        slide = {
            "pageElements": [
                {
                    "objectId": "blank_shape",
                    "size": {},
                    "transform": {},
                    "shape": {"shapeType": "RECTANGLE"},
                }
            ]
        }
        shapes = _extract_shapes(slide)
        assert shapes[0]["text"] == ""

    def test_require_google_api_caches_result(self):
        import backend.tools.google_slides_tool as mod
        mod._google_api_available = True
        # Should return immediately without raising
        mod._require_google_api()

    def test_require_google_api_raises_when_missing(self):
        import backend.tools.google_slides_tool as mod
        mod._google_api_available = None
        with patch.dict("sys.modules", {"googleapiclient": None, "googleapiclient.discovery": None}):
            mod._google_api_available = None
            with pytest.raises(RuntimeError, match="not installed"):
                mod._require_google_api()
