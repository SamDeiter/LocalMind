"""
ocr.py - Local OCR via Tesseract (Phase F2)
===========================================
Wraps pytesseract + Pillow for fully-local image -> text. Used by Report Forge
to ingest scanned school documents (eval reports, handwritten intake sheets, etc.).

# requires: pytesseract, Pillow  (install via `pip install pytesseract pillow`; also needs Tesseract binary)

Contract:
    extract_text_from_image(file_path) -> {"text": str, "confidence": float}

Notes:
  - pytesseract needs the Tesseract OCR binary installed separately
    (brew install tesseract / apt install tesseract-ocr / choco install tesseract).
  - Confidence is the mean of per-word confidence reported by Tesseract's TSV output,
    normalized to 0.0–1.0. Negative/missing word scores are filtered.
  - Raises ImportError with an install hint if the deps are missing.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("localmind.report_forge.ocr")

_INSTALL_HINT = (
    "pytesseract / Pillow not installed. Install with:\n"
    "    pip install pytesseract pillow\n"
    "and install the Tesseract OCR binary (e.g. `brew install tesseract`, "
    "`apt install tesseract-ocr`, or `choco install tesseract`)."
)


def _extract_sync(file_path: str) -> dict:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc

    with Image.open(file_path) as img:
        # Plain text pass — what we return to the caller.
        text = (pytesseract.image_to_string(img) or "").strip()

        # Confidence pass — parse TSV output.
        confidence = 0.0
        try:
            tsv = pytesseract.image_to_data(
                img, output_type=pytesseract.Output.DICT
            )
            raw_confs = tsv.get("conf") or []
            # Tesseract returns per-word confidence as str or int; -1 means "no word".
            nums: list[float] = []
            for c in raw_confs:
                try:
                    f = float(c)
                except (TypeError, ValueError):
                    continue
                if f >= 0:
                    nums.append(f)
            if nums:
                confidence = round((sum(nums) / len(nums)) / 100.0, 4)
        except Exception:  # pragma: no cover — confidence is best-effort
            logger.warning("OCR confidence extraction failed", exc_info=True)

    return {"text": text, "confidence": confidence}


async def extract_text_from_image(file_path: str) -> dict:
    """OCR a local image file via Tesseract.

    Args:
        file_path: absolute path to an image (png/jpg/tiff/pdf-page-image).

    Returns:
        {"text": str, "confidence": float}  — confidence in 0.0-1.0.

    Raises:
        ImportError:       if pytesseract/Pillow (or the Tesseract binary) is missing.
        FileNotFoundError: if the file is missing.
    """
    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(f"Image file not found: {file_path}")

    result = await asyncio.to_thread(_extract_sync, file_path)
    logger.info(
        "OCR'd %s: %d chars, confidence=%.2f",
        file_path, len(result["text"]), result["confidence"],
    )
    return result
