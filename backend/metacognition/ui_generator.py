"""
UI Generator — Generates HTML/CSS UIs using Gemini, inspired by Google Stitch.

Takes a natural language description and produces production-quality HTML/CSS.
Can generate:
  - Dashboard components (calibration, memory stats, uncertainty graphs)
  - Chat UI components (intent panels, uncertainty badges)
  - Settings panels
  - Full page layouts

Uses Gemini 2.0 Flash for speed, PII-scrubbed via gemini_client.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from backend.gemini_client import generate, is_available

logger = logging.getLogger("metacognition.ui_generator")

WORKSPACE = Path.home() / "LocalMind_Workspace"
GENERATED_UI_DIR = WORKSPACE / "generated_ui"

# ── System Prompt for UI Generation ─────────────────────────────────
UI_SYSTEM_PROMPT = """You are an expert UI designer and frontend developer.
You generate production-quality HTML/CSS from natural language descriptions.

RULES:
1. Output ONLY valid HTML with embedded <style> tags. No explanations.
2. Use modern CSS: flexbox, grid, custom properties, smooth transitions.
3. Dark theme by default (background: #0a0a0f, text: #e0e0e8).
4. Use a vibrant accent palette: cyan (#00e5ff), purple (#7c4dff), amber (#ffab00).
5. Include micro-animations: hover effects, smooth transitions (0.2s ease).
6. Make it responsive (mobile-first, flex-wrap).
7. Use clean typography: system-ui font stack, proper hierarchy.
8. Include realistic placeholder data — never use lorem ipsum.
9. No external dependencies — everything inline.
10. Glassmorphism effects where appropriate (backdrop-filter, semi-transparent bg).
11. Add data-component attributes for programmatic access.
12. Output is a COMPLETE, self-contained HTML document with <!DOCTYPE html>.

DESIGN LANGUAGE:
- Cards: rounded corners (12px), subtle border (1px rgba(255,255,255,0.08))
- Shadows: 0 4px 24px rgba(0,0,0,0.3)
- Spacing: 8px grid system
- Status colors: success=#00e676, warning=#ffab00, error=#ff5252, info=#00e5ff
- Fonts: system-ui, -apple-system, 'Segoe UI', sans-serif"""

# ── Component Templates ─────────────────────────────────────────────
COMPONENT_PROMPTS = {
    "calibration_dashboard": """Generate a calibration dashboard showing:
- A header "Meta-Cognitive Calibration" with a brain emoji
- A bar chart showing 5 confidence buckets (0-0.2, 0.2-0.4, etc.) with actual success rates
- Stats cards showing: Total predictions, Overall accuracy, Average confidence, Avg revisions
- A recent predictions table with columns: Time, Task Type, Predicted Conf, Actual Outcome
- Use {data} for placeholder data values""",

    "intent_panel": """Generate a floating intent analysis panel showing:
- Current parsed intent with goal and explicit request
- Active constraints as pill badges
- Uncertainty score as a horizontal gauge (0-100%)
- Top concern displayed prominently if uncertainty > 50%
- Assumptions list with confidence indicators (green/yellow/red dots)
- Action routed to (ANSWER/ASK/TOOL/VERIFY/ABSTAIN) as a status badge""",

    "memory_panel": """Generate a user preferences panel showing:
- Header "Long-Term Memory" with a database emoji
- Grid of preference cards, each showing: key, value, confidence bar, observation count
- Durable vs provisional indicator (checkmark or hourglass)
- "Forget" button on each card (red, small)
- Stats footer: total, durable, explicit, inferred counts""",

    "thinking_stream": """Generate a real-time thinking stream UI showing:
- A vertical timeline of thinking events
- Each event has: timestamp, type icon (🔍 parse, ⚡ route, 🧠 check, 🔄 revise)
- Event cards expand on click to show detail JSON
- Auto-scroll to bottom
- Connected by a thin vertical line (timeline effect)""",

    "uncertainty_gauge": """Generate a compact uncertainty gauge widget:
- Horizontal bar, 240px wide
- Gradient from green (0%) through yellow (50%) to red (100%)
- Current value shown as a marker/needle
- Label below showing the top concern
- Thresholds marked: "Ask" at 60%, "Abstain" at 85%""",
}


class UIGenerator:
    """
    Generates HTML/CSS UIs using Gemini, callable from the metacognition pipeline.

    Usage:
        gen = UIGenerator()
        html = await gen.generate("Create a stats dashboard showing...")
        path = await gen.generate_and_save("calibration_dashboard", data={...})
    """

    def __init__(self):
        self.output_dir = GENERATED_UI_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict = {}  # Simple in-memory cache

    async def generate(
        self,
        description: str,
        component_type: str = "",
        data: Optional[dict] = None,
        model: str = "gemini-2.0-flash",
    ) -> str:
        """
        Generate HTML/CSS from a natural language description.

        Args:
            description: What UI to generate (or use component_type for presets)
            component_type: Optional preset component key
            data: Optional data to inject into the template
            model: Gemini model to use
        """
        if not is_available():
            return self._fallback_ui(description)

        # Use preset prompt if component_type matches
        if component_type in COMPONENT_PROMPTS:
            prompt = COMPONENT_PROMPTS[component_type]
            if data:
                prompt = prompt.replace("{data}", json.dumps(data, indent=2))
        else:
            prompt = description

        # Add data context if provided
        if data and component_type not in COMPONENT_PROMPTS:
            prompt += f"\n\nUse this real data:\n{json.dumps(data, indent=2)}"

        try:
            html = await generate(
                prompt=prompt,
                model=model,
                system_instruction=UI_SYSTEM_PROMPT,
                scrub=True,
            )

            # Clean up: extract HTML if wrapped in markdown code blocks
            html = self._extract_html(html)

            return html

        except Exception as e:
            logger.error(f"UI generation failed: {e}")
            return self._fallback_ui(description)

    async def generate_and_save(
        self,
        name: str,
        description: str = "",
        component_type: str = "",
        data: Optional[dict] = None,
    ) -> Path:
        """Generate and save to a file. Returns the file path."""
        if not description and component_type:
            description = f"Generate {component_type} component"

        html = await self.generate(
            description=description,
            component_type=component_type,
            data=data,
        )

        filename = f"{name}_{int(time.time())}.html"
        path = self.output_dir / filename
        path.write_text(html, encoding="utf-8")
        logger.info(f"Generated UI saved: {path}")
        return path

    async def generate_component(
        self,
        component_type: str,
        data: Optional[dict] = None,
    ) -> str:
        """
        Generate a preset component. Returns just the inner HTML
        (no doctype/head/body) for embedding into existing pages.
        """
        html = await self.generate(
            description="",
            component_type=component_type,
            data=data,
        )

        # Extract just the body content for embedding
        body_match = re.search(r'<body[^>]*>(.*)</body>', html, re.DOTALL)
        if body_match:
            return body_match.group(1).strip()
        return html

    def list_presets(self) -> list:
        """List available preset component types."""
        return [
            {"type": k, "description": v.split("\n")[0].strip("- ")}
            for k, v in COMPONENT_PROMPTS.items()
        ]

    def list_generated(self) -> list:
        """List previously generated UI files."""
        files = []
        for f in sorted(self.output_dir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True):
            files.append({
                "name": f.stem,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
        return files[:20]  # Last 20

    # ── Helpers ───────────────────────────────────────────────────────

    def _extract_html(self, text: str) -> str:
        """Extract HTML from a response that might include markdown."""
        # Remove markdown code blocks
        if "```html" in text:
            match = re.search(r'```html\s*(.*?)```', text, re.DOTALL)
            if match:
                return match.group(1).strip()
        elif "```" in text:
            match = re.search(r'```\s*(.*?)```', text, re.DOTALL)
            if match:
                return match.group(1).strip()

        # If it starts with <!DOCTYPE or <html, it's already clean
        if text.strip().startswith(("<!DOCTYPE", "<html", "<div", "<style")):
            return text.strip()

        return text.strip()

    def _fallback_ui(self, description: str) -> str:
        """Generate a basic fallback UI when Gemini is unavailable."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LocalMind UI</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: system-ui, -apple-system, sans-serif;
    background: #0a0a0f;
    color: #e0e0e8;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 24px;
  }}
  .card {{
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 32px;
    max-width: 480px;
    text-align: center;
  }}
  .card h2 {{
    color: #00e5ff;
    margin-bottom: 16px;
    font-size: 1.25rem;
  }}
  .card p {{
    color: #a0a0b0;
    line-height: 1.6;
  }}
  .badge {{
    display: inline-block;
    background: rgba(0,229,255,0.15);
    color: #00e5ff;
    padding: 4px 12px;
    border-radius: 99px;
    font-size: 0.75rem;
    margin-top: 16px;
  }}
</style>
</head>
<body>
  <div class="card" data-component="fallback">
    <h2>🧠 UI Generation Offline</h2>
    <p>Gemini API is not available. Configure GEMINI_API_KEY to enable AI-powered UI generation.</p>
    <p style="margin-top:12px; font-size:0.9rem;">Requested: {description[:200]}</p>
    <span class="badge">Fallback Mode</span>
  </div>
</body>
</html>"""
