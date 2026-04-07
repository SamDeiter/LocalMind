"""
Browser Automation Tool — Navigate, interact with, and extract data from web pages.

Uses Playwright for headless Chromium control. Complements web_search
with full page interaction (clicking, filling forms, scrolling, screenshots).

Prerequisites:
- pip install playwright
- playwright install chromium
"""

import asyncio
import base64
import logging
from typing import Any

from .base import BaseTool

logger = logging.getLogger("localmind.tools.browser")

_NAV_TIMEOUT = 30_000  # 30s page load timeout
_ACTION_TIMEOUT = 10_000  # 10s for clicks/fills


class BrowserTool(BaseTool):
    """Navigate and interact with web pages using a headless browser."""

    def __init__(self):
        self._browser = None
        self._page = None
        self._playwright = None

    @property
    def name(self) -> str:
        return "browser"

    @property
    def description(self) -> str:
        return (
            "Control a headless browser: navigate to URLs, click elements, fill forms, "
            "take screenshots, extract page text, and run JavaScript."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "navigate",
                        "click",
                        "fill",
                        "screenshot",
                        "get_text",
                        "get_html",
                        "evaluate_js",
                        "scroll",
                        "back",
                        "forward",
                        "wait",
                        "close",
                    ],
                    "description": "Browser action to perform",
                },
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (for navigate action)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the target element (for click, fill, get_text)",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for fill action)",
                },
                "js": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate (for evaluate_js)",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction (default: down)",
                },
                "amount": {
                    "type": "integer",
                    "description": "Scroll amount in pixels (default: 500)",
                },
                "seconds": {
                    "type": "number",
                    "description": "Seconds to wait (for wait action, max 10)",
                },
            },
            "required": ["action"],
        }

    async def _ensure_browser(self):
        """Launch browser if not already running."""
        if self._page and not self._page.is_closed():
            return

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._page = await self._browser.new_page()
        self._page.set_default_timeout(_ACTION_TIMEOUT)
        logger.info("Browser launched (headless Chromium)")

    async def execute(self, **kwargs) -> dict[str, Any]:
        action = kwargs.get("action", "")

        # Close doesn't need a browser
        if action == "close":
            return await self._close(kwargs)

        try:
            await self._ensure_browser()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        dispatch = {
            "navigate": self._navigate,
            "click": self._click,
            "fill": self._fill,
            "screenshot": self._screenshot,
            "get_text": self._get_text,
            "get_html": self._get_html,
            "evaluate_js": self._evaluate_js,
            "scroll": self._scroll,
            "back": self._back,
            "forward": self._forward,
            "wait": self._wait,
        }

        handler = dispatch.get(action)
        if not handler:
            return {"success": False, "error": f"Unknown action: {action}"}

        try:
            return await handler(kwargs)
        except Exception as exc:
            logger.exception(f"Browser {action} failed")
            return {"success": False, "error": str(exc)}

    # ── Actions ──────────────────────────────────────────────────────

    async def _navigate(self, kwargs: dict) -> dict:
        url = kwargs.get("url", "")
        if not url:
            return {"success": False, "error": "url is required"}
        resp = await self._page.goto(url, timeout=_NAV_TIMEOUT, wait_until="domcontentloaded")
        status = resp.status if resp else "unknown"
        title = await self._page.title()
        return {"success": True, "result": f"Navigated to {url} (status: {status}, title: {title})"}

    async def _click(self, kwargs: dict) -> dict:
        selector = kwargs.get("selector", "")
        if not selector:
            return {"success": False, "error": "selector is required"}
        await self._page.click(selector)
        return {"success": True, "result": f"Clicked '{selector}'"}

    async def _fill(self, kwargs: dict) -> dict:
        selector = kwargs.get("selector", "")
        text = kwargs.get("text", "")
        if not selector or not text:
            return {"success": False, "error": "selector and text are required"}
        await self._page.fill(selector, text)
        return {"success": True, "result": f"Filled '{selector}' with text"}

    async def _screenshot(self, kwargs: dict) -> dict:
        screenshot_bytes = await self._page.screenshot(full_page=False)
        img_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
        title = await self._page.title()
        return {
            "success": True,
            "result": f"Screenshot captured (page: {title})",
            "image_base64": img_b64,
            "mime_type": "image/png",
        }

    async def _get_text(self, kwargs: dict) -> dict:
        selector = kwargs.get("selector")
        if selector:
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element not found: {selector}"}
            text = await el.inner_text()
        else:
            text = await self._page.inner_text("body")

        # Truncate to avoid huge payloads
        if len(text) > 5000:
            text = text[:5000] + f"\n... (truncated, {len(text)} chars total)"
        return {"success": True, "result": text}

    async def _get_html(self, kwargs: dict) -> dict:
        selector = kwargs.get("selector")
        if selector:
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element not found: {selector}"}
            html = await el.inner_html()
        else:
            html = await self._page.content()

        if len(html) > 10000:
            html = html[:10000] + f"\n... (truncated, {len(html)} chars total)"
        return {"success": True, "result": html}

    async def _evaluate_js(self, kwargs: dict) -> dict:
        js = kwargs.get("js", "")
        if not js:
            return {"success": False, "error": "js is required"}
        result = await self._page.evaluate(js)
        return {"success": True, "result": str(result) if result is not None else "(undefined)"}

    async def _scroll(self, kwargs: dict) -> dict:
        direction = kwargs.get("direction", "down")
        amount = kwargs.get("amount", 500)
        delta = amount if direction == "down" else -amount
        await self._page.evaluate(f"window.scrollBy(0, {delta})")
        return {"success": True, "result": f"Scrolled {direction} by {amount}px"}

    async def _back(self, kwargs: dict) -> dict:
        await self._page.go_back(timeout=_NAV_TIMEOUT)
        title = await self._page.title()
        return {"success": True, "result": f"Went back (now: {title})"}

    async def _forward(self, kwargs: dict) -> dict:
        await self._page.go_forward(timeout=_NAV_TIMEOUT)
        title = await self._page.title()
        return {"success": True, "result": f"Went forward (now: {title})"}

    async def _wait(self, kwargs: dict) -> dict:
        seconds = min(kwargs.get("seconds", 1), 10)
        await asyncio.sleep(seconds)
        return {"success": True, "result": f"Waited {seconds}s"}

    async def _close(self, kwargs: dict) -> dict:
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        return {"success": True, "result": "Browser closed."}
