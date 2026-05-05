"""DOCX → PDF conversion using LibreOffice headless subprocess."""
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def convert_docx_to_pdf(docx_bytes: bytes) -> bytes:
    """Convert DOCX bytes to PDF bytes using LibreOffice headless."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _convert_sync, docx_bytes)


def _convert_sync(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp_dir:
        docx_path = Path(tmp_dir) / "input.docx"
        docx_path.write_bytes(docx_bytes)

        result = __import__("subprocess").run(
            [
                settings.libreoffice_bin,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", tmp_dir,
                str(docx_path),
            ],
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")
            logger.error("LibreOffice conversion failed: %s", err)
            raise RuntimeError(f"DOCX→PDF conversion failed: {err}")

        pdf_path = Path(tmp_dir) / "input.pdf"
        if not pdf_path.exists():
            raise RuntimeError("LibreOffice did not produce a PDF output")

        return pdf_path.read_bytes()
