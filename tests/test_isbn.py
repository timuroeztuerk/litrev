import pytest

from litrev.domain.isbn import (
    EmptyIsbnError,
    IsbnChecksumError,
    MalformedIsbnError,
    UnsupportedIsbnPrefixError,
    isbn_identity,
)


@pytest.mark.parametrize(
    ("display_value", "normalized_isbn", "canonical_isbn13"),
    [
        ("0-306-40615-2", "0306406152", "9780306406157"),
        ("0 306 40615 2", "0306406152", "9780306406157"),
        (" 0306406152 ", "0306406152", "9780306406157"),
        ("0-8044-2957-X", "080442957X", "9780804429573"),
        ("0-8044-2957-x", "080442957X", "9780804429573"),
        ("978-0-306-40615-7", "9780306406157", "9780306406157"),
        ("979-10-90636-07-1", "9791090636071", "9791090636071"),
    ],
)
def test_isbn_identity_normalizes_valid_isbn10_and_isbn13(
    display_value: str,
    normalized_isbn: str,
    canonical_isbn13: str,
) -> None:
    identity = isbn_identity(display_value)

    assert identity.display_value == display_value
    assert identity.normalized_isbn == normalized_isbn
    assert identity.canonical_isbn13 == canonical_isbn13


def test_isbn10_and_equivalent_isbn13_share_one_canonical_key() -> None:
    isbn10 = isbn_identity("0-306-40615-2")
    isbn13 = isbn_identity("978-0-306-40615-7")

    assert isbn10.normalized_isbn != isbn13.normalized_isbn
    assert isbn10.canonical_isbn13 == isbn13.canonical_isbn13


@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_empty_isbn_is_distinct_from_malformed_input(value: str) -> None:
    with pytest.raises(EmptyIsbnError, match=r"^Enter an ISBN-10 or ISBN-13\.$"):
        isbn_identity(value)


@pytest.mark.parametrize(
    "value",
    [
        "978.0.306.40615.7",
        "ISBN 978-0-306-40615-7",
        "978030640615",
        "978030640615X",
        "03064061X2",
        "９７８０３０６４０６１５７",
    ],
)
def test_malformed_isbn_reports_its_expected_shape(value: str) -> None:
    with pytest.raises(
        MalformedIsbnError,
        match=r"^Enter 10 or 13 ISBN digits separated only by spaces or hyphens; ",
    ):
        isbn_identity(value)


def test_isbn13_rejects_an_unsupported_bookland_prefix_before_checksum() -> None:
    with pytest.raises(
        UnsupportedIsbnPrefixError,
        match=r"^ISBN-13 numbers must start with 978 or 979\.$",
    ):
        isbn_identity("9770306406158")


@pytest.mark.parametrize(
    "value",
    [
        "0-306-40615-3",
        "978-0-306-40615-8",
        "979-10-90636-07-2",
    ],
)
def test_isbn10_and_isbn13_report_checksum_failures(value: str) -> None:
    with pytest.raises(IsbnChecksumError, match=r"^This ISBN has an invalid check digit\.$"):
        isbn_identity(value)
