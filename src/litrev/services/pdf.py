from __future__ import annotations

from pathlib import Path

import pymupdf


def extract_text(path: Path) -> str:
    """Extract searchable text from a PDF using PyMuPDF."""
    with pymupdf.open(path) as document:
        return "\n".join(page.get_text() for page in document)
