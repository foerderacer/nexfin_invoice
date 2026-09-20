"""Render PDF pages to PNG data URLs for the vision path."""

from __future__ import annotations

import base64
import io
from pathlib import Path

__all__ = ["MAX_VISION_PAGES", "render_page_images"]

# Long scanned invoices are capped to keep request size (and cost) bounded.
MAX_VISION_PAGES = 5

_RENDER_SCALE = 2.0  # ~144 dpi


def render_page_images(pdf_path: str | Path, max_pages: int = MAX_VISION_PAGES) -> list[str]:
    """Render up to ``max_pages`` pages as ``data:image/png;base64,...`` URLs."""
    import pypdfium2 as pdfium

    images: list[str] = []
    with pdfium.PdfDocument(pdf_path) as doc:
        for page in doc:
            if len(images) >= max_pages:
                break
            bitmap = page.render(scale=_RENDER_SCALE)
            pil_image = bitmap.to_pil()
            buffer = io.BytesIO()
            pil_image.save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            images.append("data:image/png;base64," + encoded)
    return images
