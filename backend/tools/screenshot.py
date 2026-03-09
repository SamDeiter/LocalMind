"""
Screenshot Tool — capture the screen for AI analysis.
Uses 'mss' for fast, cross-platform screen capture.
"""

import base64
import io

from .base import BaseTool


class ScreenshotTool(BaseTool):
    @property
    def name(self) -> str:
        return "take_screenshot"

    @property
    def description(self) -> str:
        return (
            "Take a screenshot of the user's screen. "
            "The screenshot is sent to the vision model for analysis. "
            "Use when the user says 'look at my screen' or needs help with something visible."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "monitor": {
                    "type": "integer",
                    "description": "Which monitor to capture (0 = all monitors, 1 = primary, 2 = secondary, etc.)",
                    "default": 1,
                }
            },
        }

    async def execute(self, monitor: int = 1, **kwargs) -> dict:
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                if monitor < 0 or monitor >= len(sct.monitors):
                    monitor = 1

                screenshot = sct.grab(sct.monitors[monitor])

                # Convert to PNG bytes
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                # Resize if very large to save bandwidth
                max_dim = 1920
                if img.width > max_dim or img.height > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.LANCZOS)

                buffer = io.BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

            return {
                "success": True,
                "result": "Screenshot captured. Use analyze_image to examine it.",
                "image_base64": img_base64,
            }

        except ImportError:
            return {
                "success": False,
                "error": "Screenshot requires 'mss' and 'Pillow' packages. Install with: pip install mss Pillow",
            }
        except Exception as exc:
            return {"success": False, "error": f"Screenshot failed: {exc}"}
