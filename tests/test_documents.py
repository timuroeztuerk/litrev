import pytest

from litrev.services.documents import DocumentConversionFailure, convert_document_bytes


def test_csv_document_is_converted_to_markdown() -> None:
    converted = convert_document_bytes(b"paper,year\nA useful paper,2026\n", "papers.csv")

    assert converted.format == "csv"
    assert "A useful paper" in converted.markdown


def test_unsupported_document_has_a_stable_application_error() -> None:
    with pytest.raises(DocumentConversionFailure, match="does not support") as caught:
        convert_document_bytes(b"not a supported document", "notes.unknown")

    assert caught.value.code == "unsupported"
