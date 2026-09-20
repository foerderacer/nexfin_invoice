"""PDF text-layer extraction via pdfplumber."""

from __future__ import annotations

from pathlib import Path

__all__ = ["MIN_TEXT_CHARS", "extract_text"]

# Below this size a "text layer" is usually just a scanner watermark; treat
# the PDF as scanned and hand it to the vision path instead.
MIN_TEXT_CHARS = 100


def extract_text(pdf_path: str | Path) -> str | None:
    """Return the PDF's full text layer, or ``None`` when it is unusable.

    ``None`` signals "no usable text layer" (image-only scan or stray
    watermark text) and makes the pipeline fall back to the vision path.
    """
    import pdfplumber

    try:
        with pdfplumber.open(pdf_path) as pdf:
            parts = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        raise ValueError(f"could not read text layer of {pdf_path}: {exc}") from exc
    text = "\n\n".join(parts).strip()
    if len(text) < MIN_TEXT_CHARS:
        return None
    return text
