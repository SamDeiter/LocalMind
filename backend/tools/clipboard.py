"""
Clipboard Tool — read clipboard contents for AI analysis.
Text-only, read-only. Uses pyperclip.
"""

from .base import BaseTool


class ClipboardReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "clipboard_read"

    @property
    def description(self) -> str:
        return (
            "Read the current text from the user's clipboard. "
            "Useful when the user says 'analyze what I copied' or 'fix this code I just copied'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs) -> dict:
        try:
            import pyperclip

            content = pyperclip.paste()
            if not content or not content.strip():
                return {"success": True, "result": "Clipboard is empty."}

            # Truncate very large clipboard contents
            if len(content) > 50_000:
                content = content[:50_000] + "\n\n... [truncated — clipboard content is very large]"

            return {
                "success": True,
                "result": content,
                "length": len(content),
            }

        except ImportError:
            return {
                "success": False,
                "error": "Clipboard requires 'pyperclip' package. Install with: pip install pyperclip",
            }
        except Exception as exc:
            return {"success": False, "error": f"Clipboard read failed: {exc}"}
