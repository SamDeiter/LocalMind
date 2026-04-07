"""
Android Emulator Tool — Launch, control, and interact with Android emulators via ADB.

Prerequisites:
- Android SDK command-line tools on PATH (emulator, adb)
- At least one AVD created via avdmanager
- Hardware acceleration enabled (HAXM/WHPX on Windows, KVM on Linux)

Safety:
- Dangerous shell commands are blocklisted
- Emulator is launched headless by default (-no-window)
- Screenshots are returned as base64 for vision analysis
"""

import asyncio
import base64
import logging
import re
import shutil
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.android")

# Commands that should never be run through adb shell
_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r"\bfactory\s*reset\b", re.IGNORECASE),
    re.compile(r"\bwipe\s+data\b", re.IGNORECASE),
    re.compile(r"\bformat\b.*\b/dev/\b"),
    re.compile(r"\bdd\s+if="),
]

_ADB_TIMEOUT = 30
_EMULATOR_BOOT_TIMEOUT = 120


async def _run_cmd(args: list[str], timeout: int = _ADB_TIMEOUT) -> dict:
    """Run a command asynchronously and return structured output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return {"success": False, "error": stderr_str or f"Exit code {proc.returncode}"}
        return {"success": True, "result": stdout_str or "(no output)"}

    except asyncio.TimeoutError:
        proc.kill()
        return {"success": False, "error": f"Command timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"success": False, "error": f"'{args[0]}' not found on PATH. Install Android SDK command-line tools."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


async def _run_adb(*args: str, timeout: int = _ADB_TIMEOUT) -> dict:
    """Convenience wrapper for adb commands."""
    return await _run_cmd(["adb"] + list(args), timeout=timeout)


class AndroidEmulatorTool(BaseTool):
    """Launch, control, and interact with Android emulators via ADB."""

    def __init__(self):
        self._emulator_proc: asyncio.subprocess.Process | None = None

    @property
    def name(self) -> str:
        return "android_emulator"

    @property
    def description(self) -> str:
        return (
            "Control Android emulators: list/launch/kill AVDs, install APKs, launch apps, "
            "tap/swipe/type, take screenshots, dump UI trees, run shell commands."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_avds",
                        "launch",
                        "kill",
                        "install",
                        "launch_app",
                        "tap",
                        "swipe",
                        "type_text",
                        "press_key",
                        "screenshot",
                        "ui_tree",
                        "list_packages",
                        "shell",
                    ],
                    "description": "Emulator action to perform",
                },
                "avd_name": {
                    "type": "string",
                    "description": "AVD name (for launch)",
                },
                "apk_path": {
                    "type": "string",
                    "description": "Path to APK file (for install)",
                },
                "package": {
                    "type": "string",
                    "description": "Package name, e.g. com.example.app/.MainActivity (for launch_app)",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate (for tap, swipe start)",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate (for tap, swipe start)",
                },
                "x2": {
                    "type": "integer",
                    "description": "End X coordinate (for swipe)",
                },
                "y2": {
                    "type": "integer",
                    "description": "End Y coordinate (for swipe)",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for type_text)",
                },
                "keycode": {
                    "type": "string",
                    "description": "Android keycode, e.g. KEYCODE_BACK, KEYCODE_HOME (for press_key)",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command (for shell action)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs) -> dict[str, Any]:
        action = kwargs.get("action", "")

        dispatch = {
            "list_avds": self._list_avds,
            "launch": self._launch,
            "kill": self._kill,
            "install": self._install,
            "launch_app": self._launch_app,
            "tap": self._tap,
            "swipe": self._swipe,
            "type_text": self._type_text,
            "press_key": self._press_key,
            "screenshot": self._screenshot,
            "ui_tree": self._ui_tree,
            "list_packages": self._list_packages,
            "shell": self._shell,
        }

        handler = dispatch.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}

        try:
            return await handler(kwargs)
        except Exception as exc:
            logger.exception(f"Android emulator {action} failed")
            return {"success": False, "error": str(exc)}

    # ── Actions ──────────────────────────────────────────────────────

    async def _list_avds(self, kwargs: dict) -> dict:
        emulator_bin = shutil.which("emulator")
        if not emulator_bin:
            return {"success": False, "error": "emulator not found on PATH. Install Android SDK."}
        return await _run_cmd([emulator_bin, "-list-avds"])

    async def _launch(self, kwargs: dict) -> dict:
        avd_name = kwargs.get("avd_name", "")
        if not avd_name:
            return {"success": False, "error": "avd_name is required"}

        emulator_bin = shutil.which("emulator")
        if not emulator_bin:
            return {"success": False, "error": "emulator not found on PATH"}

        # Launch in background (don't await — it runs indefinitely)
        self._emulator_proc = await asyncio.create_subprocess_exec(
            emulator_bin, "-avd", avd_name, "-no-window", "-no-audio",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(f"Launched emulator AVD '{avd_name}' (PID: {self._emulator_proc.pid})")

        # Wait for device to come online
        for _ in range(30):
            result = await _run_adb("shell", "getprop", "sys.boot_completed", timeout=5)
            if result.get("success") and result.get("result", "").strip() == "1":
                return {"success": True, "result": f"Emulator '{avd_name}' is booted and ready."}
            await asyncio.sleep(4)

        return {"success": True, "result": f"Emulator '{avd_name}' launched (PID: {self._emulator_proc.pid}). Still booting — retry actions shortly."}

    async def _kill(self, kwargs: dict) -> dict:
        result = await _run_adb("emu", "kill")
        if self._emulator_proc:
            try:
                self._emulator_proc.kill()
            except ProcessLookupError:
                pass
            self._emulator_proc = None
        return result if result["success"] else {"success": True, "result": "Emulator stopped."}

    async def _install(self, kwargs: dict) -> dict:
        apk_path = kwargs.get("apk_path", "")
        if not apk_path:
            return {"success": False, "error": "apk_path is required"}
        return await _run_adb("install", "-r", apk_path, timeout=120)

    async def _launch_app(self, kwargs: dict) -> dict:
        package = kwargs.get("package", "")
        if not package:
            return {"success": False, "error": "package is required (e.g. com.example.app/.MainActivity)"}
        return await _run_adb("shell", "am", "start", "-n", package)

    async def _tap(self, kwargs: dict) -> dict:
        x, y = kwargs.get("x"), kwargs.get("y")
        if x is None or y is None:
            return {"success": False, "error": "x and y coordinates are required"}
        return await _run_adb("shell", "input", "tap", str(x), str(y))

    async def _swipe(self, kwargs: dict) -> dict:
        x1, y1 = kwargs.get("x"), kwargs.get("y")
        x2, y2 = kwargs.get("x2"), kwargs.get("y2")
        if None in (x1, y1, x2, y2):
            return {"success": False, "error": "x, y, x2, y2 are all required for swipe"}
        return await _run_adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2))

    async def _type_text(self, kwargs: dict) -> dict:
        text = kwargs.get("text", "")
        if not text:
            return {"success": False, "error": "text is required"}
        # ADB input text doesn't handle spaces well — replace with %s
        escaped = text.replace(" ", "%s")
        return await _run_adb("shell", "input", "text", escaped)

    async def _press_key(self, kwargs: dict) -> dict:
        keycode = kwargs.get("keycode", "")
        if not keycode:
            return {"success": False, "error": "keycode is required (e.g. KEYCODE_BACK)"}
        return await _run_adb("shell", "input", "keyevent", keycode)

    async def _screenshot(self, kwargs: dict) -> dict:
        """Capture the emulator screen and return as base64 PNG."""
        proc = await asyncio.create_subprocess_exec(
            "adb", "exec-out", "screencap", "-p",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        except asyncio.TimeoutError:
            proc.kill()
            return {"success": False, "error": "Screenshot timed out"}

        if proc.returncode != 0:
            return {"success": False, "error": stderr.decode("utf-8", errors="replace")}

        if not stdout:
            return {"success": False, "error": "Empty screenshot data"}

        img_b64 = base64.b64encode(stdout).decode("ascii")
        return {
            "success": True,
            "result": "Screenshot captured.",
            "image_base64": img_b64,
            "mime_type": "image/png",
        }

    async def _ui_tree(self, kwargs: dict) -> dict:
        """Dump the UI accessibility tree for structured navigation."""
        # Dump to device, then pull content
        await _run_adb("shell", "uiautomator", "dump", "/sdcard/ui_dump.xml")
        result = await _run_adb("shell", "cat", "/sdcard/ui_dump.xml")
        if not result.get("success"):
            return result

        # Parse out clickable elements with bounds for easy navigation
        xml_text = result["result"]
        elements = []
        for match in re.finditer(
            r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*clickable="(true|false)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            xml_text,
        ):
            text, res_id, clickable, x1, y1, x2, y2 = match.groups()
            if text or res_id:
                cx = (int(x1) + int(x2)) // 2
                cy = (int(y1) + int(y2)) // 2
                elements.append({
                    "text": text,
                    "resource_id": res_id,
                    "clickable": clickable == "true",
                    "center": {"x": cx, "y": cy},
                    "bounds": f"[{x1},{y1}][{x2},{y2}]",
                })

        return {
            "success": True,
            "result": {
                "element_count": len(elements),
                "elements": elements[:50],  # Cap to avoid huge payloads
                "raw_xml_length": len(xml_text),
            },
        }

    async def _list_packages(self, kwargs: dict) -> dict:
        result = await _run_adb("shell", "pm", "list", "packages", "-3")
        if not result.get("success"):
            return result
        packages = [line.replace("package:", "") for line in result["result"].splitlines()]
        return {"success": True, "result": packages}

    async def _shell(self, kwargs: dict) -> dict:
        command = kwargs.get("command", "")
        if not command:
            return {"success": False, "error": "command is required"}

        # Safety check
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(command):
                return {"success": False, "error": f"Blocked dangerous command: {command}"}

        args = command.split()
        return await _run_adb("shell", *args)
