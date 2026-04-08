"""Tool call detection, inference, parsing, and text stripping.

Extracted from ChatService to isolate the tool-calling heuristics
from the streaming orchestration logic.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("localmind.logic.tool_dispatcher")


class ToolDispatcher:
    def __init__(self, registry):
        self.registry = registry

    # ------------------------------------------------------------------
    # Synthetic tool call inference
    # ------------------------------------------------------------------

    @staticmethod
    def infer_tool_call(user_message: str) -> Optional[Dict]:
        """Infer a tool call from the user's message when the model fails to call tools.

        Pattern-matches common requests to the correct tool+action so the user
        doesn't have to wait for a model escalation/retry cycle.
        """
        msg = user_message.lower()

        # Android emulator patterns
        if any(kw in msg for kw in ["emulator", "android", "avd", "apk", "install app"]):
            import shutil as _shutil
            adb_bin = _shutil.which("adb") or "adb"
            try:
                import subprocess as _sp
                result = _sp.run([adb_bin, "devices"], capture_output=True, text=True, timeout=5)
                lines = [l for l in result.stdout.strip().splitlines()[1:] if l.strip() and "device" in l]
                emulator_running = len(lines) > 0
            except Exception:
                emulator_running = False

            if any(kw in msg for kw in ["list avd", "list emulator", "available avd", "available emulator"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_avds"}}}

            if not emulator_running:
                logger.info("No emulator running — launching AVD before processing request")
                return {"function": {"name": "android_emulator", "arguments": {"action": "launch", "avd_name": "Pixel_Fold_API_35"}}}

            if any(kw in msg for kw in ["install", "apk"]):
                path_match = re.search(r'["\']?([^\s"\']+\.apk)["\']?', user_message, re.IGNORECASE)
                if path_match:
                    return {"function": {"name": "android_emulator", "arguments": {"action": "install", "apk_path": path_match.group(1)}}}
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_packages"}}}
            if any(kw in msg for kw in ["scroll up", "swipe up"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_up"}}}
            if any(kw in msg for kw in ["scroll down", "swipe down"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_down"}}}
            if any(kw in msg for kw in ["scroll left", "swipe left"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_left"}}}
            if any(kw in msg for kw in ["scroll right", "swipe right"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "scroll_right"}}}
            if any(kw in msg for kw in ["read screen", "what's on screen", "what is on screen", "read the screen", "what do you see", "describe screen"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "read_screen"}}}
            if any(kw in msg for kw in ["screenshot", "screen", "show", "see", "look"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "screenshot"}}}
            if any(kw in msg for kw in ["go home", "home screen", "press home"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "press_key", "keycode": "KEYCODE_HOME"}}}
            if any(kw in msg for kw in ["go back", "press back", "back button"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "press_key", "keycode": "KEYCODE_BACK"}}}
            if any(kw in msg for kw in ["launch", "start", "boot", "open emulator"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_avds"}}}
            if any(kw in msg for kw in ["kill", "stop", "close", "shut"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "kill"}}}
            if any(kw in msg for kw in ["list", "what app", "packages"]):
                return {"function": {"name": "android_emulator", "arguments": {"action": "list_packages"}}}
            if "tap" in msg:
                return {"function": {"name": "android_emulator", "arguments": {"action": "screenshot"}}}
            return {"function": {"name": "android_emulator", "arguments": {"action": "screenshot"}}}

        # Gmail patterns
        if any(kw in msg for kw in ["email", "gmail", "inbox", "mail"]):
            if any(kw in msg for kw in ["send", "write", "compose"]):
                return {"function": {"name": "gmail", "arguments": {"action": "draft"}}}
            if any(kw in msg for kw in ["read", "open", "check"]):
                return {"function": {"name": "gmail", "arguments": {"action": "list_messages", "max_results": 5}}}
            if "search" in msg or "find" in msg:
                return {"function": {"name": "gmail", "arguments": {"action": "list_messages", "max_results": 10}}}
            return {"function": {"name": "gmail", "arguments": {"action": "list_messages", "max_results": 5}}}

        # Web search patterns
        if any(kw in msg for kw in [
            "search", "look up", "look into", "look over", "look at what",
            "google", "find out", "research", "what do people say",
            "what people say", "what are people saying", "opinions on",
            "reviews of", "browse for", "check out what",
        ]):
            query = _extract_search_query(user_message)
            return {"function": {"name": "web_search", "arguments": {"query": query}}}

        # Screenshot
        if "screenshot" in msg:
            return {"function": {"name": "take_screenshot", "arguments": {}}}

        return None

    # ------------------------------------------------------------------
    # Text-based tool call parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_tool_template(text: str) -> bool:
        """Detect when the model outputs a format template instead of a real tool call."""
        if '"name"' not in text and "'name'" not in text:
            return False
        indicators = [
            "<function-name>", "<function_name>", "<tool-name>", "<tool_name>",
            "// Arguments", "// arguments", "// JSON", "// json",
            '"<', "function-name", "tool-name",
        ]
        return any(ind in text for ind in indicators)

    @staticmethod
    def _strip_markdown_fences(text: str) -> str:
        """Remove markdown code fences (```json ... ```) wrapping tool calls."""
        return re.sub(r'```(?:json)?\s*', '', text)

    @staticmethod
    def _is_template_placeholder(obj: dict) -> bool:
        """Reject tool calls that are format templates, not real calls."""
        name = obj.get("name", "")
        if "<" in name or "function" in name.lower():
            return True
        args = obj.get("arguments", {})
        if isinstance(args, dict):
            for v in args.values():
                if isinstance(v, str) and ("//" in v or "<" in v):
                    return True
        return False

    def parse_text_tools(self, text: str) -> List[Dict[str, Any]]:
        """Parse tool calls from model text output, handling nested JSON."""
        text = self._strip_markdown_fences(text)
        calls = []
        i = 0
        while i < len(text):
            idx = text.find('"name"', i)
            if idx == -1:
                break
            start = text.rfind('{', max(0, idx - 10), idx)
            if start == -1:
                i = idx + 1
                continue
            obj = self.extract_json_object(text, start)
            if obj and "name" in obj and "arguments" in obj:
                if self._is_template_placeholder(obj):
                    i = idx + 1
                    continue
                name = obj["name"]
                if any(t.name == name for t in self.registry.tools):
                    args = obj["arguments"]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            pass
                    if isinstance(args, dict):
                        calls.append({"function": {"name": name, "arguments": args}})
            i = idx + 1
        return calls

    @staticmethod
    def extract_json_object(text: str, start: int) -> Optional[Dict]:
        """Extract a balanced JSON object starting at position ``start``."""
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        return None
        return None

    @staticmethod
    def extract_search_query(user_message: str) -> str:
        """Public wrapper around ``_extract_search_query``."""
        return _extract_search_query(user_message)

    def strip_tool_json(self, text: str) -> str:
        """Remove JSON tool call blocks (including markdown fences) from text."""
        # Strip markdown code fences wrapping tool call JSON
        result = re.sub(
            r'```(?:json)?\s*\{[^}]*"name"[^}]*"arguments".*?\}\s*```',
            '', text, flags=re.DOTALL
        )
        # Also strip bare JSON tool call objects
        i = 0
        while i < len(result):
            idx = result.find('"name"', i)
            if idx == -1:
                break
            start = result.rfind('{', max(0, idx - 10), idx)
            if start == -1:
                i = idx + 1
                continue
            obj = self.extract_json_object(result, start)
            if obj and "name" in obj and "arguments" in obj:
                depth = 0
                in_str = False
                esc = False
                for j in range(start, len(result)):
                    c = result[j]
                    if esc:
                        esc = False
                        continue
                    if c == '\\' and in_str:
                        esc = True
                        continue
                    if c == '"' and not esc:
                        in_str = not in_str
                        continue
                    if in_str:
                        continue
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            result = result[:start] + result[j + 1:]
                            break
                i = start
            else:
                i = idx + 1
        return result


# ------------------------------------------------------------------
# Search query extraction
# ------------------------------------------------------------------

# Phrases that trigger web search but should NOT be part of the query.
_SEARCH_PREFIXES = re.compile(
    r"^(?:"
    r"search\s+(?:for|about|on|the\s+web\s+for)?\s*"
    r"|look\s+(?:up|into|over|at\s+what)\s*"
    r"|google\s*"
    r"|find\s+out\s+(?:about|what)?\s*"
    r"|research\s*"
    r"|what\s+(?:do\s+)?people\s+(?:say|think)\s+(?:about)?\s*"
    r"|what\s+are\s+people\s+saying\s+(?:about)?\s*"
    r"|opinions?\s+on\s*"
    r"|reviews?\s+(?:of|for)\s*"
    r"|browse\s+for\s*"
    r"|check\s+out\s+what\s*"
    r")+",
    re.IGNORECASE,
)

# Trailing noise that often leaks in from system prompt / task wrappers.
_TRAILING_NOISE = re.compile(
    r"\s*[.,;:!?]*\s*$"
    r"|\s+(?:work\s+)?autonomously.*$"
    r"|\s+verify\s+your\s+results.*$"
    r"|\s+and\s+report\s+what\s+was\s+done.*$",
    re.IGNORECASE,
)


def _extract_search_query(raw_message: str) -> str:
    """Strip trigger prefixes and trailing noise to produce a clean search query.

    Examples
    --------
    >>> _extract_search_query("look over what people say about UE5")
    'UE5'
    >>> _extract_search_query("search for Python web frameworks")
    'Python web frameworks'
    >>> _extract_search_query("what do people say about React vs Vue")
    'React vs Vue'
    """
    query = raw_message.strip()

    # Remove trigger prefix.
    query = _SEARCH_PREFIXES.sub("", query).strip()

    # Remove trailing task-wrapper noise.
    query = _TRAILING_NOISE.sub("", query).strip()

    # If stripping removed everything, fall back to the original (minus
    # the most obvious prefix words only).
    if not query:
        query = re.sub(
            r"^(?:search|look\s+up|google|find|research)\s+",
            "",
            raw_message.strip(),
            flags=re.IGNORECASE,
        ).strip()

    return query or raw_message.strip()
