import anydoc
import pytest

from litrev.domain.documents import ConversionStatus
from litrev.services.documents import DocumentConversionFailure, convert_document_bytes


def test_csv_document_is_converted_to_markdown() -> None:
    converted = convert_document_bytes(b"paper,year\nA useful paper,2026\n", "papers.csv")

    assert converted.format == "csv"
    assert "A useful paper" in converted.markdown


def test_unsupported_document_has_a_stable_application_error() -> None:
    with pytest.raises(DocumentConversionFailure, match="does not support") as caught:
        convert_document_bytes(b"not a supported document", "notes.unknown")

    assert caught.value.code == "unsupported"


@pytest.mark.parametrize(
    ("error_type", "attributes", "expected_status", "expected_diagnostics"),
    [
        (
            anydoc.NeedsOcrError,
            {"pages": [2, 4], "page_count": 5},
            ConversionStatus.NEEDS_OCR,
            {"pages": [2, 4], "page_count": 5},
        ),
        (
            anydoc.EncryptedError,
            {},
            ConversionStatus.ENCRYPTED,
            {},
        ),
        (
            anydoc.MalformedError,
            {"part": "word/document.xml"},
            ConversionStatus.MALFORMED,
            {"part": "word/document.xml"},
        ),
        (
            anydoc.ResourceLimitError,
            {"limit": "decompressed bytes"},
            ConversionStatus.RESOURCE_LIMIT,
            {"limit": "decompressed bytes"},
        ),
        (
            anydoc.MissingPartError,
            {"part": "content.xml"},
            ConversionStatus.MISSING_PART,
            {"part": "content.xml"},
        ),
    ],
)
def test_anydoc_diagnostics_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    attributes: dict[str, object],
    expected_status: ConversionStatus,
    expected_diagnostics: dict[str, object],
) -> None:
    error = error_type("conversion failed")
    for name, value in attributes.items():
        setattr(error, name, value)

    def fail_conversion(_data: bytes, _format: str | None) -> str:
        raise error

    monkeypatch.setattr(anydoc, "to_markdown_bytes", fail_conversion)

    with pytest.raises(DocumentConversionFailure) as caught:
        convert_document_bytes(b"not really a PDF", "paper.pdf")

    assert caught.value.code == expected_status.value
    assert caught.value.diagnostics == expected_diagnostics
