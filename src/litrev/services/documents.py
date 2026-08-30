from __future__ import annotations

from dataclasses import dataclass

import anydoc


@dataclass(frozen=True)
class ConvertedDocument:
    filename: str
    format: str
    markdown: str


class DocumentConversionFailure(Exception):
    def __init__(self, code: str, message: str, pages: tuple[int, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.pages = pages


def convert_document_bytes(data: bytes, filename: str) -> ConvertedDocument:
    """Convert a local document to Markdown using Anydoc's Rust engine."""
    detected_format = anydoc.format_from_bytes(data) or anydoc.format_from_path(filename)

    try:
        markdown = anydoc.to_markdown_bytes(data, detected_format)
    except anydoc.NeedsOcrError as error:
        raise DocumentConversionFailure(
            "needs_ocr",
            "This document contains scanned pages that need OCR.",
            tuple(error.pages),
        ) from error
    except anydoc.EncryptedError as error:
        raise DocumentConversionFailure("encrypted", "The document is encrypted.") from error
    except anydoc.UnsupportedError as error:
        raise DocumentConversionFailure(
            "unsupported", "Anydoc does not support this document."
        ) from error
    except anydoc.MalformedError as error:
        raise DocumentConversionFailure(
            "malformed", "The document structure could not be read."
        ) from error
    except anydoc.ResourceLimitError as error:
        raise DocumentConversionFailure(
            "resource_limit", "The document exceeded Anydoc's safety limits."
        ) from error
    except anydoc.MissingPartError as error:
        raise DocumentConversionFailure(
            "missing_part", "A required part of the document is missing."
        ) from error

    return ConvertedDocument(
        filename=filename,
        format=detected_format or "unknown",
        markdown=markdown,
    )
