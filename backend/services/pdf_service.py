"""PDF text extraction using PyMuPDF (fitz)."""
import logging

from services.text_sanitizer import sanitize_text_for_db

logger = logging.getLogger(__name__)


def extract_text_from_pdf(
    pdf_bytes: bytes,
    *,
    application_id: str | None = None,
    filename: str | None = None,
) -> str:
    """Extract text from *pdf_bytes* and return a PostgreSQL-safe string.

    Image pages (applicant photos, embedded pictures) produce no text and are
    silently skipped — they never contribute binary garbage to the output.

    Args:
        pdf_bytes:      Raw PDF bytes.
        application_id: Optional — used only for structured log context.
        filename:       Optional — used only for structured log context.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF not installed. Run: pip install pymupdf") from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_texts: list[str] = []
    total_sanitized = 0

    for page_num, page in enumerate(doc, start=1):
        raw_page_text = page.get_text()
        if not raw_page_text:
            continue
        clean = sanitize_text_for_db(
            raw_page_text,
            application_id=application_id,
            source=f"pdf_page_{page_num}",
        )
        total_sanitized += len(raw_page_text) - len(clean)
        if clean:
            page_texts.append(clean)

    doc.close()

    if total_sanitized:
        logger.info(
            "pdf_extraction application_id=%s filename=%s pages=%d total_chars_sanitized=%d",
            application_id or "?",
            filename or "?",
            len(page_texts),
            total_sanitized,
        )

    return "\n".join(page_texts).strip()
