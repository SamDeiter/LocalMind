"""
AtomicFileWriter — crash-safe file writes for all LocalMind output artifacts.

All file writes in the system (PPTX, XLSX, DOCX, JSON, etc.) must go through
this module. Never use open(path, 'wb') directly in task workers or tool
implementations.

Write pattern:
    writer = AtomicFileWriter()
    result = writer.write_atomic(Path("output/report.pptx"), lambda tmp: prs.save(tmp))

The caller's write_fn receives a temp Path and saves to it. On success, the
temp file is atomically promoted to target_path. On any failure, the temp file
is deleted and the original file is left unchanged.
"""

import hashlib
import json
import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict
from uuid import uuid4

logger = logging.getLogger("localmind.core.atomic_io")


# ── Result type ────────────────────────────────────────────────────────────────


@dataclass
class FileWriteResult:
    """Returned by AtomicFileWriter.write_atomic on success."""

    path: Path
    sha256: str
    size_bytes: int


# ── Format validation ──────────────────────────────────────────────────────────


def _validate_pptx(path: Path) -> None:
    """Verify PPTX is a valid ZIP containing [Content_Types].xml."""
    if not zipfile.is_zipfile(path):
        raise ValueError(f"PPTX file is not a valid ZIP archive: {path}")
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
    if "[Content_Types].xml" not in names:
        raise ValueError(
            f"PPTX archive is missing [Content_Types].xml (found {len(names)} entries): {path}"
        )


def _validate_xlsx(path: Path) -> None:
    """Verify XLSX is a valid ZIP containing xl/workbook.xml."""
    if not zipfile.is_zipfile(path):
        raise ValueError(f"XLSX file is not a valid ZIP archive: {path}")
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
    if "xl/workbook.xml" not in names:
        raise ValueError(
            f"XLSX archive is missing xl/workbook.xml (found {len(names)} entries): {path}"
        )


def _validate_docx(path: Path) -> None:
    """Verify DOCX is a valid ZIP containing [Content_Types].xml and word/document.xml."""
    if not zipfile.is_zipfile(path):
        raise ValueError(f"DOCX file is not a valid ZIP archive: {path}")
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
    missing = [e for e in ("[Content_Types].xml", "word/document.xml") if e not in names]
    if missing:
        raise ValueError(
            f"DOCX archive is missing required entries {missing} (found {len(names)} entries): {path}"
        )


def _validate_json(path: Path) -> None:
    """Verify the file contains valid JSON."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            json.loads(fh.read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"File is not valid JSON: {path}") from exc


# Maps lowercase file extension (with leading dot) to a validation callable.
FORMAT_VALIDATORS: Dict[str, Callable[[Path], None]] = {
    ".pptx": _validate_pptx,
    ".xlsx": _validate_xlsx,
    ".docx": _validate_docx,
    ".json": _validate_json,
}


def validate_file(path: Path, expected_format: str | None = None) -> None:
    """
    Run format-specific validation on *path*.

    Args:
        path: Path to the file to validate.
        expected_format: Optional explicit format key (e.g. ".pptx"). When
            omitted, the format is inferred from path.suffix.

    Raises:
        ValueError: File does not exist, is empty, or fails format validation.
    """
    if not path.exists():
        raise ValueError(f"File does not exist after write: {path}")
    size = path.stat().st_size
    if size == 0:
        raise ValueError(f"File is empty (0 bytes) after write: {path}")

    ext = (expected_format or path.suffix).lower()
    validator = FORMAT_VALIDATORS.get(ext)
    if validator is not None:
        logger.debug("Running %s validator on %s", ext, path)
        validator(path)
    else:
        logger.debug("No format validator registered for extension %r — skipping format check", ext)


# ── SHA-256 utility ────────────────────────────────────────────────────────────


def compute_sha256(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── AtomicFileWriter ───────────────────────────────────────────────────────────


class AtomicFileWriter:
    """
    All file writes go through this. No direct open(path, 'wb') anywhere.

    Thread-safe: each call generates a unique temp path via uuid4, so
    concurrent writes to the same target are safe (last os.replace wins,
    which is the desired behaviour for idempotent task workers).
    """

    def write_atomic(
        self,
        target_path: Path,
        write_fn: Callable[[Path], None],
        expected_format: str | None = None,
    ) -> FileWriteResult:
        """
        Write *target_path* atomically via *write_fn*.

        Steps:
            1. Generate a temp path alongside target_path using a UUID suffix.
            2. Call write_fn(temp_path) — the writer saves to the temp location.
            3. Validate: file exists, size > 0, and format-specific check.
            4. Compute SHA-256 of the temp file.
            5. os.replace(temp_path, target_path) — atomic on POSIX,
               near-atomic on Windows (same volume required).
            6. Return FileWriteResult(path, sha256, size_bytes).

        On any failure the temp file is deleted (in a finally block) and the
        original target_path is left unchanged.

        Args:
            target_path: Final destination path for the file.
            write_fn: Callable that receives the temp Path and writes the file
                      contents to it (e.g. ``lambda tmp: presentation.save(tmp)``).
            expected_format: Optional explicit format override (e.g. ".xlsx").
                             When None, the format is inferred from target_path.suffix.

        Returns:
            FileWriteResult with path, sha256 hex digest, and size in bytes.

        Raises:
            Any exception raised by write_fn or validation. Original file is
            always left intact on failure.
        """
        suffix = ".tmp." + uuid4().hex[:8]
        # Keep the temp file next to the target so os.replace stays on the
        # same filesystem/volume (required on Windows).
        temp_path = target_path.with_suffix(suffix)

        logger.debug(
            "AtomicFileWriter: writing %s via temp %s", target_path, temp_path.name
        )

        try:
            # Ensure parent directory exists.
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Step 1-2: invoke caller's write function.
            write_fn(temp_path)

            # Step 3: validate using the *target* extension, not the temp
            # file's extension (which is always ".tmp.<hex>").
            effective_format = expected_format or target_path.suffix
            validate_file(temp_path, expected_format=effective_format)

            # Step 4: hash.
            digest = compute_sha256(temp_path)
            size_bytes = temp_path.stat().st_size

            # Step 5: atomic promotion.
            os.replace(temp_path, target_path)

            logger.info(
                "AtomicFileWriter: committed %s (%d bytes, sha256=%s...)",
                target_path,
                size_bytes,
                digest[:12],
            )

            # Step 6: return result (temp_path is gone; report target_path).
            return FileWriteResult(
                path=target_path,
                sha256=digest,
                size_bytes=size_bytes,
            )

        except Exception:
            logger.exception(
                "AtomicFileWriter: write failed for %s — cleaning up temp file", target_path
            )
            raise

        finally:
            # Always clean up the temp file if it survived (write failed or
            # os.replace raised after writing but before completing).
            if temp_path.exists():
                try:
                    temp_path.unlink()
                    logger.debug("AtomicFileWriter: removed temp file %s", temp_path)
                except OSError as cleanup_err:
                    logger.warning(
                        "AtomicFileWriter: could not remove temp file %s: %s",
                        temp_path,
                        cleanup_err,
                    )
