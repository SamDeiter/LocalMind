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
import os
import re
import shutil
from pathlib import Path
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


def _find_sdk_tool(tool_name: str) -> str | None:
    """Find an Android SDK tool, checking PATH first then common install locations."""
    found = shutil.which(tool_name)
    if found:
        return found

    # Common Android SDK locations
    home = Path.home()
    candidates = [
        home / "AppData" / "Local" / "Android" / "Sdk",  # Windows default
        home / "Library" / "Android" / "sdk",              # macOS default
        home / "Android" / "Sdk",                          # Linux default
        Path(os.environ.get("ANDROID_HOME", "")),          # Env var
        Path(os.environ.get("ANDROID_SDK_ROOT", "")),      # Alt env var
    ]

    subdirs = {
        "emulator": "emulator",
        "adb": "platform-tools",
        "avdmanager": "cmdline-tools/latest/bin",
    }
    subdir = subdirs.get(tool_name, "platform-tools")

    for sdk_path in candidates:
        if not sdk_path or not sdk_path.exists():
            continue
        for ext in ("", ".exe", ".bat"):
            candidate = sdk_path / subdir / f"{tool_name}{ext}"
            if candidate.exists():
                logger.info(f"Found {tool_name} at {candidate}")
                return str(candidate)

    return None


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
    adb_bin = _find_sdk_tool("adb") or "adb"
    return await _run_cmd([adb_bin] + list(args), timeout=timeout)


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
            "tap/swipe/scroll/type, take screenshots, read screen content, dump UI trees, run shell commands."
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
                        "scroll_up",
                        "scroll_down",
                        "scroll_left",
                        "scroll_right",
                        "read_screen",
                        "type_text",
                        "press_key",
                        "screenshot",
                        "ui_tree",
                        "list_packages",
                        "check_internet",
                        "shell",
                    ],
                    "description": "Emulator action to perform",
                },
                "avd_name": {
                    "type": "string",
                    "description": "AVD name (for launch)",
                },
                "cold_boot": {
                    "type": "boolean",
                    "description": "Force cold boot (no snapshot). Fixes stale network state. Default false.",
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
                "duration": {
                    "type": "integer",
                    "description": "Duration in ms for swipe/scroll (default 300). Slower = more reliable scrolling.",
                },
                "distance": {
                    "type": "string",
                    "enum": ["small", "medium", "large"],
                    "description": "Scroll distance preset (default 'medium'). small=25%, medium=50%, large=75% of screen.",
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
            "scroll_up": self._scroll_up,
            "scroll_down": self._scroll_down,
            "scroll_left": self._scroll_left,
            "scroll_right": self._scroll_right,
            "read_screen": self._read_screen,
            "type_text": self._type_text,
            "press_key": self._press_key,
            "screenshot": self._screenshot,
            "ui_tree": self._ui_tree,
            "list_packages": self._list_packages,
            "check_internet": self._check_internet,
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
        emulator_bin = _find_sdk_tool("emulator")
        if not emulator_bin:
            return {"success": False, "error": "emulator not found. Install Android SDK or set ANDROID_HOME."}
        return await _run_cmd([emulator_bin, "-list-avds"])

    async def _launch(self, kwargs: dict) -> dict:
        avd_name = kwargs.get("avd_name", "")
        if not avd_name:
            return {"success": False, "error": "avd_name is required"}

        emulator_bin = _find_sdk_tool("emulator")
        if not emulator_bin:
            return {"success": False, "error": "emulator not found. Install Android SDK or set ANDROID_HOME."}

        # Verify the AVD exists before trying to launch
        check = await _run_cmd([emulator_bin, "-list-avds"], timeout=10)
        if check.get("success"):
            avds = [a.strip() for a in check["result"].splitlines() if a.strip()]
            if avd_name not in avds:
                return {"success": False, "error": f"AVD '{avd_name}' not found. Available: {', '.join(avds)}"}

        # Redirect emulator output to a log file so pipes don't block the
        # long-running process.  DEVNULL would also work, but a log file
        # gives us something to inspect if the emulator crashes later.
        import tempfile
        self._emu_log = tempfile.NamedTemporaryFile(
            prefix="emu_", suffix=".log", delete=False, mode="w"
        )
        logger.info(f"Emulator log: {self._emu_log.name}")

        launch_args = [
            emulator_bin, "-avd", avd_name,
            "-no-audio",
            "-gpu", "swiftshader_indirect",  # Software rendering — won't fight GPU with other apps
            "-memory", "2048",               # Cap at 2 GB RAM (default can balloon to 4-8 GB)
            "-cores", "2",                   # Limit CPU cores
            "-dns-server", "8.8.8.8,8.8.4.4",
            "-no-boot-anim",                 # Skip boot animation — faster startup
        ]
        if kwargs.get("cold_boot"):
            launch_args.append("-no-snapshot-load")

        self._emulator_proc = await asyncio.create_subprocess_exec(
            *launch_args,
            stdout=self._emu_log,
            stderr=self._emu_log,
        )
        logger.info(f"Launched emulator AVD '{avd_name}' (PID: {self._emulator_proc.pid})")

        # Give the process a moment to fail fast (e.g. bad AVD name, missing image)
        await asyncio.sleep(3)
        if self._emulator_proc.returncode is not None:
            self._emu_log.close()
            try:
                err_msg = Path(self._emu_log.name).read_text(errors="replace").strip()[-500:]
            except Exception:
                err_msg = f"exit code {self._emulator_proc.returncode}"
            return {"success": False, "error": f"Emulator exited immediately: {err_msg}"}

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

        # If the path is a URL, download the APK first
        if apk_path.startswith("http://") or apk_path.startswith("https://"):
            import tempfile
            import httpx
            logger.info(f"Downloading APK from {apk_path}")
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
                    resp = await client.get(apk_path, headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    })
                    if resp.status_code != 200:
                        return {"success": False, "error": f"Download failed: HTTP {resp.status_code}"}
                    content_type = resp.headers.get("content-type", "")
                    if "html" in content_type:
                        return {"success": False, "error": "URL returned a webpage, not an APK file. Provide a direct download link ending in .apk"}
                    # Save to temp file
                    tmp = tempfile.NamedTemporaryFile(suffix=".apk", delete=False)
                    tmp.write(resp.content)
                    tmp.close()
                    apk_path = tmp.name
                    logger.info(f"APK downloaded to {apk_path} ({len(resp.content)} bytes)")
            except Exception as e:
                return {"success": False, "error": f"Download failed: {e}"}

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
        duration = kwargs.get("duration", 300)
        return await _run_adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))

    async def _get_screen_size(self) -> tuple[int, int]:
        """Get the emulator screen resolution."""
        result = await _run_adb("shell", "wm", "size")
        if result.get("success"):
            # Output like "Physical size: 1080x1920"
            match = re.search(r"(\d+)x(\d+)", result["result"])
            if match:
                return int(match.group(1)), int(match.group(2))
        return 1080, 1920  # Fallback to common resolution

    def _scroll_offsets(self, screen_w: int, screen_h: int, direction: str, distance: str) -> tuple[int, int, int, int]:
        """Calculate swipe start/end coords for a scroll direction and distance."""
        pct = {"small": 0.25, "medium": 0.50, "large": 0.75}.get(distance, 0.50)
        cx, cy = screen_w // 2, screen_h // 2
        dx = int(screen_w * pct / 2)
        dy = int(screen_h * pct / 2)

        if direction == "up":      # swipe finger upward → content scrolls down
            return cx, cy + dy, cx, cy - dy
        elif direction == "down":  # swipe finger downward → content scrolls up
            return cx, cy - dy, cx, cy + dy
        elif direction == "left":  # swipe finger left → content scrolls right
            return cx + dx, cy, cx - dx, cy
        else:                      # right: swipe finger right → content scrolls left
            return cx - dx, cy, cx + dx, cy

    async def _scroll(self, kwargs: dict, direction: str) -> dict:
        screen_w, screen_h = await self._get_screen_size()
        distance = kwargs.get("distance", "medium")
        duration = kwargs.get("duration", 300)
        x1, y1, x2, y2 = self._scroll_offsets(screen_w, screen_h, direction, distance)
        return await _run_adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))

    async def _read_screen(self, kwargs: dict) -> dict:
        """Read screen content: captures screenshot + UI tree for structured understanding."""
        # Get UI tree for text content
        await _run_adb("shell", "uiautomator", "dump", "/sdcard/ui_dump.xml")
        tree_result = await _run_adb("shell", "cat", "/sdcard/ui_dump.xml")

        elements = []
        raw_texts = []
        if tree_result.get("success"):
            xml_text = tree_result["result"]
            for match in re.finditer(
                r'text="([^"]*)"[^>]*resource-id="([^"]*)"[^>]*class="([^"]*)"[^>]*clickable="(true|false)"[^>]*scrollable="(true|false)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                xml_text,
            ):
                text, res_id, cls, clickable, scrollable, x1, y1, x2, y2 = match.groups()
                cx = (int(x1) + int(x2)) // 2
                cy = (int(y1) + int(y2)) // 2
                entry = {
                    "text": text,
                    "resource_id": res_id,
                    "class": cls.split(".")[-1],  # Short class name
                    "clickable": clickable == "true",
                    "scrollable": scrollable == "true",
                    "center": {"x": cx, "y": cy},
                    "bounds": f"[{x1},{y1}][{x2},{y2}]",
                }
                elements.append(entry)
                if text:
                    raw_texts.append(text)

        # Get current activity/package
        activity_result = await _run_adb("shell", "dumpsys", "activity", "activities", timeout=10)
        current_app = ""
        if activity_result.get("success"):
            for line in activity_result["result"].splitlines():
                if "mResumedActivity" in line or "mFocusedActivity" in line:
                    current_app = line.strip()
                    break

        # Also capture screenshot for vision
        screenshot = await self._screenshot(kwargs)

        # Build a readable summary the LLM can reason about
        summary_lines = []
        if current_app:
            summary_lines.append(f"Current app: {current_app}")
        summary_lines.append(f"Visible elements: {len(elements)}")
        if raw_texts:
            summary_lines.append(f"Text on screen: {' | '.join(raw_texts[:30])}")

        scrollable_els = [e for e in elements if e["scrollable"]]
        clickable_els = [e for e in elements if e["clickable"]]
        if scrollable_els:
            summary_lines.append(f"Scrollable areas: {len(scrollable_els)}")
        summary_lines.append(f"Clickable elements: {len(clickable_els)}")

        result = {
            "success": True,
            "result": {
                "summary": "\n".join(summary_lines),
                "current_app": current_app,
                "visible_text": raw_texts[:50],
                "elements": elements[:60],
                "element_count": len(elements),
            },
        }
        # Attach screenshot if captured
        if screenshot.get("image_base64"):
            result["image_base64"] = screenshot["image_base64"]
            result["mime_type"] = screenshot.get("mime_type", "image/png")

        return result

    async def _scroll_up(self, kwargs: dict) -> dict:
        return await self._scroll(kwargs, "up")

    async def _scroll_down(self, kwargs: dict) -> dict:
        return await self._scroll(kwargs, "down")

    async def _scroll_left(self, kwargs: dict) -> dict:
        return await self._scroll(kwargs, "left")

    async def _scroll_right(self, kwargs: dict) -> dict:
        return await self._scroll(kwargs, "right")

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
        adb_bin = _find_sdk_tool("adb") or "adb"
        proc = await asyncio.create_subprocess_exec(
            adb_bin, "exec-out", "screencap", "-p",
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

    async def _check_internet(self, kwargs: dict) -> dict:
        """Diagnose internet connectivity on the emulator."""
        checks = {}

        # 1. Check if Wi-Fi is enabled
        wifi = await _run_adb("shell", "settings", "get", "global", "wifi_on")
        checks["wifi_enabled"] = wifi.get("result", "").strip() == "1" if wifi.get("success") else "unknown"

        # 2. Check if airplane mode is off
        airplane = await _run_adb("shell", "settings", "get", "global", "airplane_mode_on")
        checks["airplane_mode"] = airplane.get("result", "").strip() == "1" if airplane.get("success") else "unknown"

        # 3. Try DNS resolution
        dns = await _run_adb("shell", "nslookup", "google.com", timeout=10)
        checks["dns_works"] = dns.get("success", False) and "Address" in dns.get("result", "")

        # 4. Try pinging Google DNS
        ping = await _run_adb("shell", "ping", "-c", "1", "-W", "3", "8.8.8.8", timeout=10)
        checks["ping_works"] = ping.get("success", False) and "1 received" in ping.get("result", "")

        # 5. Try HTTP connectivity
        http = await _run_adb("shell", "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", "http://connectivitycheck.gstatic.com/generate_204", timeout=10)
        checks["http_works"] = http.get("success", False) and http.get("result", "").strip() in ("204", "200")

        # Build diagnosis
        issues = []
        if checks.get("airplane_mode") is True:
            issues.append("Airplane mode is ON — disable it: adb shell settings put global airplane_mode_on 0")
        if checks.get("wifi_enabled") is False:
            issues.append("Wi-Fi is OFF — the emulator uses a virtual ethernet, this may be normal")
        if not checks.get("dns_works"):
            issues.append("DNS resolution failed — try restarting emulator or check host firewall")
        if not checks.get("ping_works"):
            issues.append("Cannot ping 8.8.8.8 — host network or firewall may be blocking emulator traffic")
        if not checks.get("http_works"):
            issues.append("HTTP connectivity failed — proxy or firewall issue")

        all_ok = checks.get("dns_works") and checks.get("ping_works") and checks.get("http_works")

        return {
            "success": True,
            "result": {
                "internet_working": all_ok,
                "checks": checks,
                "issues": issues if issues else ["All connectivity checks passed"],
                "fix_suggestions": [] if all_ok else [
                    "Try: adb shell svc wifi enable",
                    "Try: adb shell settings put global airplane_mode_on 0",
                    "Try relaunching emulator (cold boot clears stale network state)",
                    "Check Windows Firewall isn't blocking the emulator process",
                    "If behind a VPN, the emulator may not route through it — try disconnecting VPN",
                ],
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
