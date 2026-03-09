"""
Vision Tool — analyze images using Ollama's multimodal models.
Handles webcam captures and workspace images.
"""

import base64

import httpx

from .base import BaseTool

OLLAMA_BASE = "http://localhost:11434"
VISION_MODEL = "llama3.2-vision:11b"


class AnalyzeImageTool(BaseTool):
    @property
    def name(self) -> str:
        return "analyze_image"

    @property
    def description(self) -> str:
        return (
            "Analyze an image using AI vision. Can process webcam captures or images from the workspace. "
            "Describe what you see, read text, identify objects, or answer questions about the image."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "image_base64": {
                    "type": "string",
                    "description": "Base64-encoded image data",
                },
                "question": {
                    "type": "string",
                    "description": "Question or instruction about the image (e.g., 'What do you see?' or 'Read the text in this screenshot')",
                    "default": "Describe what you see in this image in detail.",
                },
            },
            "required": ["image_base64"],
        }

    async def execute(self, image_base64: str = "", question: str = "Describe what you see.", **kwargs) -> dict:
        if not image_base64:
            return {"success": False, "error": "No image provided"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                resp = await client.post(
                    f"{OLLAMA_BASE}/api/chat",
                    json={
                        "model": VISION_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": question,
                                "images": [image_base64],
                            }
                        ],
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            answer = data.get("message", {}).get("content", "No response from vision model.")
            return {"success": True, "result": answer}

        except httpx.ConnectError:
            return {"success": False, "error": "Ollama is not running. Start with: ollama serve"}
        except Exception as exc:
            return {"success": False, "error": f"Vision analysis failed: {exc}"}
