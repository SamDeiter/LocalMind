"""
Integration tests for LocalMind document tools.

These tests exercise the real file-format libraries (python-pptx, openpyxl,
python-docx, pymupdf) against actual generated documents.  No mocking of
document internals — the whole point is end-to-end validation.

pytest-asyncio is required (pip install pytest-asyncio).
"""

import shutil
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Tool imports — these are the classes under test
# ---------------------------------------------------------------------------
from backend.tools.pptx_tool import PptxTool
from backend.tools.excel_tool import ExcelTool
from backend.tools.word_tool import WordTool
from backend.tools.pdf_tool import PDFTool
from backend.tools.file_tools import ReadFileTool, WriteFileTool, ListFilesTool


# ═══════════════════════════════════════════════════════════════════════════
#  PPTX TOOL
# ═══════════════════════════════════════════════════════════════════════════


class TestPptxTool:
    """Integration tests for the PowerPoint tool."""

    @pytest.fixture(autouse=True)
    def _tool(self):
        self.tool = PptxTool()

    # -- read_metadata --------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_metadata_returns_slide_count(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="read_metadata", file_path=str(sample_pptx)
        )
        assert result["success"] is True
        meta = result["result"]
        assert meta["slide_count"] == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_metadata_has_dimensions(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="read_metadata", file_path=str(sample_pptx)
        )
        dims = result["result"]["dimensions"]
        assert dims["width_emu"] is not None
        assert dims["height_emu"] is not None
        assert isinstance(dims["width_inches"], float)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_metadata_layout_names(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="read_metadata", file_path=str(sample_pptx)
        )
        layouts = result["result"]["layout_names"]
        assert isinstance(layouts, list)
        assert len(layouts) > 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_metadata_file_not_found(self, workspace: Path):
        result = await self.tool.execute(
            action="read_metadata", file_path=str(workspace / "nonexistent.pptx")
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    # -- extract_content ------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_content_all_slides(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_content", file_path=str(sample_pptx)
        )
        assert result["success"] is True
        slides = result["result"]
        assert len(slides) == 3

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_content_specific_slide(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_content",
            file_path=str(sample_pptx),
            slide_indices=[0],
        )
        assert result["success"] is True
        slides = result["result"]
        assert len(slides) == 1
        assert slides[0]["slide_index"] == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_content_finds_title_text(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_content",
            file_path=str(sample_pptx),
            slide_indices=[0],
        )
        slide0 = result["result"][0]
        # The title shape should contain "Integration Test Deck"
        all_text = " ".join(
            shape.get("text", "") or ""
            for shape in slide0["shapes"]
        )
        assert "Integration Test Deck" in all_text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_content_out_of_range(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_content",
            file_path=str(sample_pptx),
            slide_indices=[99],
        )
        assert result["success"] is False
        assert "out of range" in result["error"]

    # -- extract_styles -------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_styles_has_fonts(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_styles", file_path=str(sample_pptx)
        )
        assert result["success"] is True
        style = result["result"]
        # We set Arial on slide 3 — it should appear in the font list
        font_names = [f["name"] for f in style["fonts"]]
        assert "Arial" in font_names

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_styles_has_colors(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_styles", file_path=str(sample_pptx)
        )
        style = result["result"]
        # Red (#FF0000) was set on slide 3
        color_hexes = [c["hex"] for c in style["colors"]]
        assert "#FF0000" in color_hexes

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_styles_has_layouts(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="extract_styles", file_path=str(sample_pptx)
        )
        layouts = result["result"]["layouts"]
        assert isinstance(layouts, list)
        assert len(layouts) > 0
        assert "layout_name" in layouts[0]

    # -- edit_slide -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_edit_slide_changes_text(self, sample_pptx: Path, workspace: Path):
        output = workspace / "edited.pptx"
        result = await self.tool.execute(
            action="edit_slide",
            file_path=str(sample_pptx),
            slide_index=0,
            edits=[{"shape_name": "Title 1", "new_text": "Updated Title"}],
            output_path=str(output),
        )
        assert result["success"] is True
        changes = result["result"]["changes"]
        assert len(changes) >= 1
        # Verify by re-reading the edited file
        verify = await self.tool.execute(
            action="extract_content",
            file_path=str(output),
            slide_indices=[0],
        )
        all_text = " ".join(
            shape.get("text", "") or ""
            for shape in verify["result"][0]["shapes"]
        )
        assert "Updated Title" in all_text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_edit_slide_preserves_formatting(self, sample_pptx: Path, workspace: Path):
        """Verify that editing text preserves the original font properties."""
        output = workspace / "edited_fmt.pptx"

        # First, extract the formatted text box on slide 2 (index=2)
        before = await self.tool.execute(
            action="extract_content",
            file_path=str(sample_pptx),
            slide_indices=[2],
        )
        # Find the text box with "Formatted text"
        textbox = None
        for shape in before["result"][0]["shapes"]:
            if shape.get("text") and "Formatted text" in shape["text"]:
                textbox = shape
                break
        assert textbox is not None, "Could not find 'Formatted text' shape on slide 3"
        original_style = textbox["style"]

        # Edit the text
        result = await self.tool.execute(
            action="edit_slide",
            file_path=str(sample_pptx),
            slide_index=2,
            edits=[{"shape_name": textbox["name"], "new_text": "New formatted text"}],
            output_path=str(output),
        )
        assert result["success"] is True

        # Verify formatting is preserved after edit
        after = await self.tool.execute(
            action="extract_content",
            file_path=str(output),
            slide_indices=[2],
        )
        edited_box = None
        for shape in after["result"][0]["shapes"]:
            if shape.get("text") and "New formatted text" in shape["text"]:
                edited_box = shape
                break
        assert edited_box is not None, "Edited text not found"
        # Font name and bold should survive the edit
        assert edited_box["style"].get("font") == original_style.get("font")
        assert edited_box["style"].get("bold") == original_style.get("bold")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_edit_slide_missing_params(self, sample_pptx: Path):
        result = await self.tool.execute(
            action="edit_slide",
            file_path=str(sample_pptx),
        )
        assert result["success"] is False
        assert "required" in result["error"].lower()


# ═══════════════════════════════════════════════════════════════════════════
#  EXCEL TOOL
# ═══════════════════════════════════════════════════════════════════════════


class TestExcelTool:
    """Integration tests for the Excel tool."""

    @pytest.fixture(autouse=True)
    def _tool(self):
        self.tool = ExcelTool()

    # -- read_sheet -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_sheet_default(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="read_sheet", file_path=str(sample_xlsx)
        )
        assert result["success"] is True
        data = result["result"]
        assert data["sheet_name"] == "Sales"
        assert data["headers"] == ["Product", "Q1", "Q2", "Q3", "Q4", "Total"]
        assert len(data["rows"]) == 3  # 3 data rows below header

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_sheet_by_name(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="read_sheet",
            file_path=str(sample_xlsx),
            sheet_name="Metadata",
        )
        assert result["success"] is True
        data = result["result"]
        assert data["sheet_name"] == "Metadata"
        assert data["headers"] == ["Key", "Value"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_sheet_nonexistent(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="read_sheet",
            file_path=str(sample_xlsx),
            sheet_name="DoesNotExist",
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    # -- write_cells ----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_write_cells_updates_value(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="write_cells",
            file_path=str(sample_xlsx),
            updates=[
                {"cell": "A2", "value": "Super Widget"},
                {"cell": "B2", "value": 999},
            ],
        )
        assert result["success"] is True
        changes = result["result"]["changes"]
        assert changes[0]["old_value"] == "Widget A"
        assert changes[0]["new_value"] == "Super Widget"
        assert changes[1]["old_value"] == 100
        assert changes[1]["new_value"] == 999

        # Verify by re-reading
        verify = await self.tool.execute(
            action="read_sheet", file_path=str(sample_xlsx)
        )
        row1 = verify["result"]["rows"][0]
        assert row1[0] == "Super Widget"
        assert row1[1] == 999

    @pytest.mark.asyncio(loop_scope="function")
    async def test_write_cells_invalid_cell_ref(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="write_cells",
            file_path=str(sample_xlsx),
            updates=[{"cell": "!!!bad", "value": "x"}],
        )
        assert result["success"] is False
        assert "invalid cell" in result["error"].lower()

    # -- read_range -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_range_returns_correct_data(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="read_range",
            file_path=str(sample_xlsx),
            range_notation="A1:B3",
        )
        assert result["success"] is True
        values = result["result"]["values"]
        # Row 1: headers; Row 2: first data; Row 3: second data
        assert values[0] == ["Product", "Q1"]
        assert values[1] == ["Widget A", 100]
        assert values[2] == ["Widget B", 80]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_range_invalid_notation(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="read_range",
            file_path=str(sample_xlsx),
            range_notation="BADFORMAT",
        )
        assert result["success"] is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_range_with_sheet_name(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="read_range",
            file_path=str(sample_xlsx),
            range_notation="A1:B2",
            sheet_name="Metadata",
        )
        assert result["success"] is True
        values = result["result"]["values"]
        assert values[0] == ["Key", "Value"]
        assert values[1] == ["Author", "Test Suite"]

    # -- list_sheets ----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_sheets(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="list_sheets", file_path=str(sample_xlsx)
        )
        assert result["success"] is True
        assert set(result["result"]["sheets"]) == {"Sales", "Metadata"}

    # -- get_metadata ---------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_metadata(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="get_metadata", file_path=str(sample_xlsx)
        )
        assert result["success"] is True
        assert result["result"]["sheet_count"] == 2

    # -- extract_formulas -----------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_formulas(self, sample_xlsx: Path):
        result = await self.tool.execute(
            action="extract_formulas", file_path=str(sample_xlsx)
        )
        assert result["success"] is True
        formulas = result["result"]["formulas"]
        assert len(formulas) >= 1
        assert formulas[0]["formula"] == "=SUM(B2:E2)"


# ═══════════════════════════════════════════════════════════════════════════
#  WORD TOOL
# ═══════════════════════════════════════════════════════════════════════════


class TestWordTool:
    """Integration tests for the Word document tool."""

    @pytest.fixture(autouse=True)
    def _tool(self):
        self.tool = WordTool()

    # -- read_document --------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_document_paragraphs(self, sample_docx: Path):
        result = await self.tool.execute(
            action="read_document", file_path=str(sample_docx)
        )
        assert result["success"] is True
        data = result["result"]
        assert data["paragraph_count"] >= 3
        texts = [p["text"] for p in data["paragraphs"]]
        assert any("first paragraph" in t for t in texts)
        assert any("Bold and formatted" in t for t in texts)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_document_title(self, sample_docx: Path):
        result = await self.tool.execute(
            action="read_document", file_path=str(sample_docx)
        )
        assert result["result"]["title"] == "Integration Test Document"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_document_paragraph_styles(self, sample_docx: Path):
        result = await self.tool.execute(
            action="read_document", file_path=str(sample_docx)
        )
        styles = [p["style"] for p in result["result"]["paragraphs"]]
        assert "Title" in styles  # from doc.add_heading(..., level=0)

    # -- edit_paragraph -------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_edit_paragraph_changes_text(self, sample_docx: Path):
        # Find the index of "first paragraph"
        read_result = await self.tool.execute(
            action="read_document", file_path=str(sample_docx)
        )
        target_idx = None
        for p in read_result["result"]["paragraphs"]:
            if "first paragraph" in p["text"]:
                target_idx = p["index"]
                break
        assert target_idx is not None

        result = await self.tool.execute(
            action="edit_paragraph",
            file_path=str(sample_docx),
            index=target_idx,
            new_text="Replaced paragraph text.",
        )
        assert result["success"] is True
        assert result["result"]["old_text"] != result["result"]["new_text"]
        assert result["result"]["new_text"] == "Replaced paragraph text."

        # Verify round-trip
        verify = await self.tool.execute(
            action="read_document", file_path=str(sample_docx)
        )
        texts = [p["text"] for p in verify["result"]["paragraphs"]]
        assert "Replaced paragraph text." in texts

    @pytest.mark.asyncio(loop_scope="function")
    async def test_edit_paragraph_out_of_range(self, sample_docx: Path):
        result = await self.tool.execute(
            action="edit_paragraph",
            file_path=str(sample_docx),
            index=9999,
            new_text="x",
        )
        assert result["success"] is False
        assert "out of range" in result["error"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_edit_paragraph_missing_params(self, sample_docx: Path):
        result = await self.tool.execute(
            action="edit_paragraph",
            file_path=str(sample_docx),
        )
        assert result["success"] is False

    # -- extract_styles -------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_styles_lists_styles(self, sample_docx: Path):
        result = await self.tool.execute(
            action="extract_styles", file_path=str(sample_docx)
        )
        assert result["success"] is True
        data = result["result"]
        assert data["total_styles"] > 0
        style_names = [s["name"] for s in data["styles"]]
        assert "Normal" in style_names
        assert "Title" in style_names

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_styles_in_use(self, sample_docx: Path):
        result = await self.tool.execute(
            action="extract_styles", file_path=str(sample_docx)
        )
        used = result["result"]["styles_in_use"]
        assert "Normal" in used

    # -- get_metadata ---------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_metadata_returns_author(self, sample_docx: Path):
        result = await self.tool.execute(
            action="get_metadata", file_path=str(sample_docx)
        )
        assert result["success"] is True
        assert result["result"]["author"] == "Test Suite"
        assert result["result"]["title"] == "Integration Test Document"
        assert result["result"]["word_count"] > 0

    # -- get_tables -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_tables(self, sample_docx: Path):
        result = await self.tool.execute(
            action="get_tables", file_path=str(sample_docx)
        )
        assert result["success"] is True
        assert result["result"]["table_count"] == 1
        table = result["result"]["tables"][0]
        assert table["row_count"] == 3
        assert table["col_count"] == 3
        assert table["rows"][0][0] == "R0C0"

    # -- file not found -------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_not_found(self, workspace: Path):
        result = await self.tool.execute(
            action="read_document",
            file_path=str(workspace / "nonexistent.docx"),
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ═══════════════════════════════════════════════════════════════════════════
#  PDF TOOL
# ═══════════════════════════════════════════════════════════════════════════


class TestPDFTool:
    """Integration tests for the PDF (read-only) tool."""

    @pytest.fixture(autouse=True)
    def _tool(self):
        self.tool = PDFTool()

    # -- extract_text ---------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_text_all_pages(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="extract_text", file_path=str(sample_pdf)
        )
        assert result["success"] is True
        pages = result["result"]["pages"]
        assert len(pages) == 2
        assert "Page 1" in pages[0]["text"]
        assert "Page 2" in pages[1]["text"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_text_specific_page(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="extract_text",
            file_path=str(sample_pdf),
            pages=[1],
        )
        assert result["success"] is True
        pages = result["result"]["pages"]
        assert len(pages) == 1
        assert pages[0]["page_num"] == 1
        assert "Page 2" in pages[0]["text"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_extract_text_total_chars(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="extract_text", file_path=str(sample_pdf)
        )
        assert result["result"]["total_chars"] > 0

    # -- get_metadata ---------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_metadata(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="get_metadata", file_path=str(sample_pdf)
        )
        assert result["success"] is True
        meta = result["result"]
        assert meta["page_count"] == 2
        assert meta["file_size"] > 0

    # -- page_count -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_page_count(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="page_count", file_path=str(sample_pdf)
        )
        assert result["success"] is True
        assert result["result"]["page_count"] == 2

    # -- get_page_text --------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_page_text(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="get_page_text",
            file_path=str(sample_pdf),
            page_num=0,
        )
        assert result["success"] is True
        assert "Page 1" in result["result"]["text"]
        assert result["result"]["char_count"] > 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_page_text_out_of_range(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="get_page_text",
            file_path=str(sample_pdf),
            page_num=99,
        )
        assert result["success"] is False
        assert "out of range" in result["error"].lower()

    # -- search_text ----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_search_text_finds_match(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="search_text",
            file_path=str(sample_pdf),
            query="sample content",
        )
        assert result["success"] is True
        assert result["result"]["match_count"] >= 1
        assert result["result"]["matches"][0]["page_num"] == 0

    @pytest.mark.asyncio(loop_scope="function")
    async def test_search_text_no_match(self, sample_pdf: Path):
        result = await self.tool.execute(
            action="search_text",
            file_path=str(sample_pdf),
            query="xyzzy_not_found_42",
        )
        assert result["success"] is True
        assert result["result"]["match_count"] == 0

    # -- file not found -------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_file_not_found(self, workspace: Path):
        result = await self.tool.execute(
            action="extract_text",
            file_path=str(workspace / "nonexistent.pdf"),
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()


# ═══════════════════════════════════════════════════════════════════════════
#  FILE TOOLS (binary detection, read, write, list)
# ═══════════════════════════════════════════════════════════════════════════


class TestFileTools:
    """Integration tests for the file-system tools (read, write, list)."""

    # -- read_file ------------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_text_file(self, workspace: Path, monkeypatch):
        """ReadFileTool can read a plain text file."""
        # Patch WORKSPACE so the tool targets our temp dir
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        (workspace / "hello.txt").write_text("Hello, LocalMind!", encoding="utf-8")

        tool = ReadFileTool()
        result = await tool.execute(path="hello.txt")
        assert result["success"] is True
        assert result["result"] == "Hello, LocalMind!"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_file_not_found(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        tool = ReadFileTool()
        result = await tool.execute(path="does_not_exist.txt")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_read_binary_file_returns_content(self, workspace: Path, monkeypatch, sample_pptx: Path):
        """ReadFileTool attempts to read binary files (they get replacement chars)."""
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        # sample_pptx is already in workspace/
        tool = ReadFileTool()
        result = await tool.execute(path="sample.pptx")
        # The tool reads with errors="replace", so it should succeed
        assert result["success"] is True

    # -- write_file -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_write_file_creates_new(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        tool = WriteFileTool()
        result = await tool.execute(path="output.txt", content="New file content")
        assert result["success"] is True
        assert (workspace / "output.txt").read_text() == "New file content"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_write_file_creates_subdirectory(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        tool = WriteFileTool()
        result = await tool.execute(
            path="subdir/deep/file.txt", content="Nested content"
        )
        assert result["success"] is True
        assert (workspace / "subdir" / "deep" / "file.txt").exists()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_write_file_path_traversal_blocked(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        tool = WriteFileTool()
        result = await tool.execute(
            path="../../escape.txt", content="Should fail"
        )
        assert result["success"] is False
        assert "escape" in result["error"].lower() or "sandbox" in result["error"].lower()

    # -- list_files -----------------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_files_shows_contents(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        (workspace / "a.txt").write_text("aaa")
        (workspace / "b.txt").write_text("bbb")
        sub = workspace / "subdir"
        sub.mkdir(exist_ok=True)
        (sub / "c.txt").write_text("ccc")

        tool = ListFilesTool()
        result = await tool.execute(path=".")
        assert result["success"] is True
        assert len(result["entries"]) >= 3  # a.txt, b.txt, subdir/

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_files_empty_dir(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        empty = workspace / "empty_dir"
        empty.mkdir()

        tool = ListFilesTool()
        result = await tool.execute(path="empty_dir")
        assert result["success"] is True
        assert result["entries"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_files_nonexistent_dir(self, workspace: Path, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", workspace)

        tool = ListFilesTool()
        result = await tool.execute(path="no_such_dir")
        assert result["success"] is False

    # -- binary file detection ------------------------------------------------

    @pytest.mark.asyncio(loop_scope="function")
    async def test_binary_detection_pptx(self, all_samples: dict, monkeypatch):
        """PPTX files are binary (ZIP). ReadFileTool should still return content
        (via errors='replace'), but the output will be garbled — the key point is
        it does not crash."""
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", all_samples["pptx"].parent)

        tool = ReadFileTool()
        result = await tool.execute(path="sample.pptx")
        assert result["success"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_binary_detection_xlsx(self, all_samples: dict, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", all_samples["xlsx"].parent)

        tool = ReadFileTool()
        result = await tool.execute(path="sample.xlsx")
        assert result["success"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_binary_detection_docx(self, all_samples: dict, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", all_samples["docx"].parent)

        tool = ReadFileTool()
        result = await tool.execute(path="sample.docx")
        assert result["success"] is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_binary_detection_pdf(self, all_samples: dict, monkeypatch):
        import backend.tools.file_tools as ft
        monkeypatch.setattr(ft, "WORKSPACE", all_samples["pdf"].parent)

        tool = ReadFileTool()
        result = await tool.execute(path="sample.pdf")
        assert result["success"] is True
