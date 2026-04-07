"""
Tests for Android Emulator tool — scroll, read_screen, and core actions.

Uses mocked ADB so tests run without an actual emulator.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.tools.android_emulator import AndroidEmulatorTool


# ── Helpers ──────────────────────────────────────────────────────

def _ok(result="OK"):
    """Simulate a successful ADB response."""
    return {"success": True, "result": result}


def _fail(error="fail"):
    return {"success": False, "error": error}


SAMPLE_UI_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node text="Settings" resource-id="com.android.settings:id/title" class="android.widget.TextView" clickable="true" scrollable="false" bounds="[0,0][540,96]" />
  <node text="" resource-id="com.android.settings:id/list" class="android.widget.RecyclerView" clickable="false" scrollable="true" bounds="[0,96][1080,1920]" />
  <node text="Wi-Fi" resource-id="com.android.settings:id/title" class="android.widget.TextView" clickable="true" scrollable="false" bounds="[100,200][500,280]" />
  <node text="Bluetooth" resource-id="com.android.settings:id/title" class="android.widget.TextView" clickable="true" scrollable="false" bounds="[100,300][500,380]" />
</hierarchy>
'''


# ── Scroll Offset Calculation (pure logic, no ADB) ──────────────

class TestScrollOffsets:
    def setup_method(self):
        self.tool = AndroidEmulatorTool()

    def test_scroll_up_offsets(self):
        x1, y1, x2, y2 = self.tool._scroll_offsets(1080, 1920, "up", "medium")
        # Finger moves upward: start y > end y
        assert y1 > y2
        # Centered horizontally
        assert x1 == x2 == 540

    def test_scroll_down_offsets(self):
        x1, y1, x2, y2 = self.tool._scroll_offsets(1080, 1920, "down", "medium")
        # Finger moves downward: start y < end y
        assert y1 < y2
        assert x1 == x2 == 540

    def test_scroll_left_offsets(self):
        x1, y1, x2, y2 = self.tool._scroll_offsets(1080, 1920, "left", "medium")
        # Finger moves left: start x > end x
        assert x1 > x2
        assert y1 == y2 == 960

    def test_scroll_right_offsets(self):
        x1, y1, x2, y2 = self.tool._scroll_offsets(1080, 1920, "right", "medium")
        assert x1 < x2
        assert y1 == y2 == 960

    def test_small_distance_shorter_than_large(self):
        _, y1_s, _, y2_s = self.tool._scroll_offsets(1080, 1920, "up", "small")
        _, y1_l, _, y2_l = self.tool._scroll_offsets(1080, 1920, "up", "large")
        small_dist = abs(y1_s - y2_s)
        large_dist = abs(y1_l - y2_l)
        assert small_dist < large_dist

    def test_medium_is_default(self):
        result_medium = self.tool._scroll_offsets(1080, 1920, "up", "medium")
        result_default = self.tool._scroll_offsets(1080, 1920, "up", "bogus")
        assert result_medium == result_default


# ── Scroll Actions (mocked ADB) ─────────────────────────────────

class TestScrollActions:
    def setup_method(self):
        self.tool = AndroidEmulatorTool()

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_scroll_down_calls_swipe(self, mock_adb):
        mock_adb.return_value = _ok()
        result = await self.tool.execute(action="scroll_down")
        assert result["success"] is True
        # Should have called: wm size (to get resolution), then input swipe
        assert mock_adb.call_count == 2
        swipe_call = mock_adb.call_args_list[1]
        assert "swipe" in swipe_call.args

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_scroll_up_calls_swipe(self, mock_adb):
        mock_adb.return_value = _ok()
        result = await self.tool.execute(action="scroll_up")
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_scroll_with_custom_distance(self, mock_adb):
        mock_adb.return_value = _ok()
        result = await self.tool.execute(action="scroll_down", distance="large")
        assert result["success"] is True

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_scroll_with_custom_duration(self, mock_adb):
        mock_adb.return_value = _ok()
        result = await self.tool.execute(action="scroll_down", duration=500)
        assert result["success"] is True
        # Duration should appear in the swipe command args
        swipe_call = mock_adb.call_args_list[1]
        assert "500" in swipe_call.args

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_scroll_left_and_right(self, mock_adb):
        mock_adb.return_value = _ok()
        r1 = await self.tool.execute(action="scroll_left")
        r2 = await self.tool.execute(action="scroll_right")
        assert r1["success"] is True
        assert r2["success"] is True


# ── Read Screen (mocked ADB) ────────────────────────────────────

class TestReadScreen:
    def setup_method(self):
        self.tool = AndroidEmulatorTool()

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_read_screen_returns_elements(self, mock_adb):
        async def adb_side_effect(*args, **kwargs):
            cmd = " ".join(args)
            if "uiautomator" in cmd:
                return _ok()
            if "cat" in cmd and "ui_dump" in cmd:
                return _ok(SAMPLE_UI_XML)
            if "dumpsys activity" in cmd:
                return _ok("mResumedActivity: ActivityRecord{abc com.android.settings/.Settings}")
            if "screencap" in cmd:
                return _ok()
            # wm size for screenshot's _get_screen_size if called
            if "wm size" in cmd:
                return _ok("Physical size: 1080x1920")
            return _ok()

        mock_adb.side_effect = adb_side_effect

        # Also mock the screenshot subprocess since it uses create_subprocess_exec directly
        with patch("backend.tools.android_emulator._find_sdk_tool", return_value="adb"):
            with patch("asyncio.create_subprocess_exec") as mock_proc:
                proc_mock = AsyncMock()
                proc_mock.communicate.return_value = (b"\x89PNG fake", b"")
                proc_mock.returncode = 0
                mock_proc.return_value = proc_mock

                result = await self.tool.execute(action="read_screen")

        assert result["success"] is True
        res = result["result"]
        assert res["element_count"] > 0
        # Should find Settings, Wi-Fi, Bluetooth text
        assert "Settings" in res["visible_text"]
        assert "Wi-Fi" in res["visible_text"]
        assert "Bluetooth" in res["visible_text"]
        assert "current_app" in res
        assert len(res["elements"]) > 0

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_read_screen_identifies_scrollable(self, mock_adb):
        async def adb_side_effect(*args, **kwargs):
            cmd = " ".join(args)
            if "uiautomator" in cmd:
                return _ok()
            if "cat" in cmd:
                return _ok(SAMPLE_UI_XML)
            if "dumpsys" in cmd:
                return _ok("")
            return _ok()

        mock_adb.side_effect = adb_side_effect

        with patch("backend.tools.android_emulator._find_sdk_tool", return_value="adb"):
            with patch("asyncio.create_subprocess_exec") as mock_proc:
                proc_mock = AsyncMock()
                proc_mock.communicate.return_value = (b"\x89PNG fake", b"")
                proc_mock.returncode = 0
                mock_proc.return_value = proc_mock

                result = await self.tool.execute(action="read_screen")

        elements = result["result"]["elements"]
        scrollable = [e for e in elements if e.get("scrollable")]
        assert len(scrollable) >= 1  # The RecyclerView


# ── Screen Size Detection ────────────────────────────────────────

class TestScreenSize:
    def setup_method(self):
        self.tool = AndroidEmulatorTool()

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_parses_screen_size(self, mock_adb):
        mock_adb.return_value = _ok("Physical size: 1440x2560")
        w, h = await self.tool._get_screen_size()
        assert w == 1440
        assert h == 2560

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_fallback_on_failure(self, mock_adb):
        mock_adb.return_value = _fail("no devices")
        w, h = await self.tool._get_screen_size()
        assert w == 1080
        assert h == 1920


# ── Swipe duration parameter ────────────────────────────────────

class TestSwipeDuration:
    def setup_method(self):
        self.tool = AndroidEmulatorTool()

    @pytest.mark.asyncio
    @patch("backend.tools.android_emulator._run_adb", new_callable=AsyncMock)
    async def test_swipe_includes_duration(self, mock_adb):
        mock_adb.return_value = _ok()
        await self.tool.execute(action="swipe", x=100, y=200, x2=100, y2=800, duration=500)
        call_args = mock_adb.call_args.args
        assert "500" in call_args


# ── Synthetic tool call inference (chat_service) ─────────────────

def _mock_adb_devices_running():
    """Patch subprocess.run so _infer_tool_call thinks an emulator is online."""
    fake = MagicMock()
    fake.stdout = "List of devices attached\nemulator-5554\tdevice\n"
    return patch("subprocess.run", return_value=fake)


class TestScrollInference:
    def test_scroll_down_inferred(self):
        from backend.logic.chat_service import ChatService
        with _mock_adb_devices_running():
            result = ChatService._infer_tool_call("scroll down on the emulator")
        assert result is not None
        assert result["function"]["arguments"]["action"] == "scroll_down"

    def test_scroll_up_inferred(self):
        from backend.logic.chat_service import ChatService
        with _mock_adb_devices_running():
            result = ChatService._infer_tool_call("scroll up on the android screen")
        assert result is not None
        assert result["function"]["arguments"]["action"] == "scroll_up"

    def test_read_screen_inferred(self):
        from backend.logic.chat_service import ChatService
        with _mock_adb_devices_running():
            result = ChatService._infer_tool_call("what's on screen on the emulator")
        assert result is not None
        assert result["function"]["arguments"]["action"] == "read_screen"
