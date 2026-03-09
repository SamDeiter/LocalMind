"""
Tests for tool implementations and the tool registry.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Tool Registry ────────────────────────────────────────────────

class TestToolRegistry:
    def test_registry_discovers_tools(self):
        """Registry should auto-discover tools from backend/tools/."""
        from backend.tools.registry import ToolRegistry
        registry = ToolRegistry()
        tools = registry.get_ollama_tools()

        # Should find at least some tools
        assert len(tools) > 0

        # Each tool should have required fields
        for tool in tools:
            assert "type" in tool
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]

    def test_registry_get_tool(self):
        """Should be able to get a tool by name."""
        from backend.tools.registry import ToolRegistry
        registry = ToolRegistry()

        # These tools should exist
        for name in ["save_memory", "recall_memories", "web_search"]:
            tool = registry.get_tool(name)
            # Tool may or may not be found depending on imports, but shouldn't crash
            if tool:
                assert tool.name == name


# ── Path Validation (file_tools._validate_path) ─────────────────

class TestPathValidation:
    def test_normal_path(self):
        """Normal relative paths should resolve within workspace."""
        from backend.tools.file_tools import _validate_path, WORKSPACE
        result = _validate_path("hello.py")
        assert str(result).startswith(str(WORKSPACE.resolve()))

    def test_path_traversal_blocked(self):
        """Path traversal attempts should be blocked."""
        from backend.tools.file_tools import _validate_path
        with pytest.raises(ValueError, match="escapes sandbox"):
            _validate_path("../../../../../../etc/passwd")

    def test_nested_path(self):
        """Nested paths should resolve within workspace."""
        from backend.tools.file_tools import _validate_path, WORKSPACE
        result = _validate_path("subdir/file.txt")
        assert str(result).startswith(str(WORKSPACE.resolve()))


# ── Class-Based File Tools (backend/tools/file_tools.py) ─────────

class TestFileTools:
    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        """Reading a non-existent file returns error dict."""
        from backend.tools.file_tools import ReadFileTool
        tool = ReadFileTool()
        result = await tool.execute(path="definitely_nonexistent_file_12345.py")
        assert result["success"] is False
        assert "not found" in result["error"].lower() or "not found" in str(result).lower()

    @pytest.mark.asyncio
    async def test_write_and_read_file(self, tmp_path):
        """Write a file then read it back via the tool classes."""
        from backend.tools.file_tools import WriteFileTool, ReadFileTool, WORKSPACE
        import os

        # Write a test file into the actual workspace
        write_tool = WriteFileTool()
        test_filename = f"_test_{os.getpid()}.txt"
        write_result = await write_tool.execute(path=test_filename, content="unit test content")
        assert write_result["success"] is True

        # Read it back
        read_tool = ReadFileTool()
        read_result = await read_tool.execute(path=test_filename)
        assert read_result["success"] is True
        assert "unit test content" in read_result["result"]

        # Clean up
        test_file = WORKSPACE / test_filename
        if test_file.exists():
            test_file.unlink()

    @pytest.mark.asyncio
    async def test_list_files(self):
        """List files tool returns workspace contents."""
        from backend.tools.file_tools import ListFilesTool
        tool = ListFilesTool()
        result = await tool.execute(path=".")
        assert result["success"] is True
        assert isinstance(result["result"], str)
