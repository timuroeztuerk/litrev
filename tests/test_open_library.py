import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from litrev.domain.isbn import IsbnChecksumError
from litrev.domain.sources import SourceType
from litrev.services import open_library
from litrev.services.open_library import (
    MAX_OPEN_LIBRARY_RESPONSE_BYTES,
    OpenLibraryMetadataAmbiguousError,
    OpenLibraryMetadataMalformedError,
    OpenLibraryMetadataMismatchError,
    OpenLibraryMetadataNotFoundError,
    OpenLibraryMetadataRateLimitedError,
    OpenLibraryMetadataUnavailableError,
    lookup_open_library_metadata,
)


def open_library_response(
    *,
    key: str = "/books/OL7353617M",
    isbns: list[str] | None = None,
    data_overrides: dict[str, object] | None = None,
    record_overrides: dict[str, object] | None = None,
) -> bytes:
    data: dict[str, object] = {
        "title": "Fantastic Mr. Fox",
        "authors": [{"name": "Roald Dahl"}],
        "publish_date": "October 1, 1988",
        "publishers": [{"name": "Puffin"}],
        "identifiers": {
            "isbn_10": ["0140328726"],
            "isbn_13": ["9780140328721"],
            "goodreads": ["1507552"],
        },
    }
    if data_overrides:
        data.update(data_overrides)
    record: dict[str, object] = {
        "isbns": isbns or ["0140328726", "9780140328721"],
        "lccns": ["88012345"],
        "oclcs": ["123456"],
        "olids": ["OL7353617M"],
        "publishDates": ["October 1, 1988"],
        "data": data,
        "details": {
            "details": {
                "description": {"value": "A fox protects his family."},
                "languages": [{"key": "/languages/eng"}],
            }
        },
    }
    if record_overrides:
        record.update(record_overrides)
    return json.dumps({"records": {key: record}, "items": []}).encode()


def test_open_library_lookup_requests_canonical_isbn_and_maps_edition_metadata() -> None:
    requests: list[Request] = []

    def fetch(request: Request) -> bytes:
        requests.append(request)
        return open_library_response()

    metadata = lookup_open_library_metadata("0-14-032872-6", fetch=fetch)

    assert requests[0].full_url == (
        "https://openlibrary.org/api/volumes/brief/isbn/9780140328721.json"
    )
    assert requests[0].get_header("Accept") == "application/json"
    assert requests[0].get_header("User-agent", "").startswith("Litrev/0.1")
    assert metadata.provider == "Open Library"
    assert metadata.provider_url == "https://openlibrary.org/books/OL7353617M"
    assert metadata.identifier_type == "isbn"
    assert metadata.retrieved_identifier == "9780140328721"
    assert metadata.proposal.source_type is SourceType.BOOK
    assert metadata.proposal.title == "Fantastic Mr. Fox"
    assert metadata.proposal.authors == ["Roald Dahl"]
    assert metadata.proposal.publication_year == 1988
    assert metadata.proposal.venue == "Puffin"
    assert metadata.proposal.url == "https://openlibrary.org/books/OL7353617M"
    assert metadata.proposal.abstract == "A fox protects his family."
    assert metadata.proposal.language == "eng"
    assert [
        (identifier.identifier_type, identifier.value)
        for identifier in metadata.proposal.identifiers or []
    ] == [
        ("goodreads", "1507552"),
        ("isbn", "0140328726"),
        ("isbn", "9780140328721"),
        ("lccn", "88012345"),
        ("oclc", "123456"),
        ("openlibrary", "OL7353617M"),
    ]


def test_open_library_lookup_validates_isbn_before_networking() -> None:
    def unexpected_fetch(_request: Request) -> bytes:
        raise AssertionError("An invalid ISBN must not contact Open Library")

    with pytest.raises(IsbnChecksumError):
        lookup_open_library_metadata("978-0-306-40615-8", fetch=unexpected_fetch)


def test_open_library_lookup_distinguishes_no_match_mismatch_and_ambiguity() -> None:
    with pytest.raises(OpenLibraryMetadataNotFoundError, match="no catalog match"):
        lookup_open_library_metadata(
            "9780140328721",
            fetch=lambda _request: b'{"records": {}, "items": []}',
        )

    with pytest.raises(OpenLibraryMetadataMismatchError, match="different ISBN"):
        lookup_open_library_metadata(
            "9780140328721",
            fetch=lambda _request: open_library_response(isbns=["9780306406157"]),
        )

    first = json.loads(open_library_response())
    first_record = first["records"]["/books/OL7353617M"]
    with pytest.raises(OpenLibraryMetadataAmbiguousError, match="multiple editions"):
        lookup_open_library_metadata(
            "9780140328721",
            fetch=lambda _request: json.dumps(
                {
                    "records": {
                        "/books/OL7353617M": first_record,
                        "/books/OL9999999M": first_record,
                    }
                }
            ).encode(),
        )


def test_open_library_lookup_keeps_a_missing_title_reviewable() -> None:
    metadata = lookup_open_library_metadata(
        "9780140328721",
        fetch=lambda _request: open_library_response(data_overrides={"title": None}),
    )

    assert metadata.proposal.title is None
    assert metadata.proposal.authors == ["Roald Dahl"]


def test_open_library_lookup_accepts_omitted_optional_edition_fields() -> None:
    metadata = lookup_open_library_metadata(
        "9780140328721",
        fetch=lambda _request: open_library_response(
            data_overrides={
                "authors": None,
                "publish_date": None,
                "publishers": None,
                "identifiers": None,
            },
            record_overrides={
                "lccns": None,
                "oclcs": None,
                "olids": None,
                "publishDates": None,
                "details": None,
            },
        ),
    )

    assert metadata.proposal.title == "Fantastic Mr. Fox"
    assert metadata.proposal.authors is None
    assert metadata.proposal.publication_year is None
    assert metadata.proposal.venue is None
    assert metadata.proposal.abstract is None
    assert metadata.proposal.language is None
    assert [
        (identifier.identifier_type, identifier.value)
        for identifier in metadata.proposal.identifiers or []
    ] == [("isbn", "0140328726"), ("isbn", "9780140328721")]


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b"[]",
        b'{"records": []}',
        b'{"records": {"bad-key": {}}}',
    ],
)
def test_open_library_lookup_rejects_malformed_responses(content: bytes) -> None:
    with pytest.raises(OpenLibraryMetadataMalformedError):
        lookup_open_library_metadata("9780140328721", fetch=lambda _request: content)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (404, OpenLibraryMetadataNotFoundError),
        (429, OpenLibraryMetadataRateLimitedError),
        (503, OpenLibraryMetadataUnavailableError),
    ],
)
def test_open_library_http_failures_are_specific(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    error_type: type[Exception],
) -> None:
    def fail_request(request: Request, *, timeout: int) -> bytes:
        assert timeout == 10
        raise HTTPError(request.full_url, status_code, "failure", {}, None)

    monkeypatch.setattr(open_library, "urlopen", fail_request)

    with pytest.raises(error_type):
        lookup_open_library_metadata("9780140328721")


def test_open_library_timeout_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(_request: Request, *, timeout: int) -> bytes:
        assert timeout == 10
        raise TimeoutError("timed out")

    monkeypatch.setattr(open_library, "urlopen", time_out)

    with pytest.raises(OpenLibraryMetadataUnavailableError, match="could not be reached"):
        lookup_open_library_metadata("9780140328721")


def test_open_library_oversized_response_is_rejected_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedResponse:
        headers = {"Content-Length": str(MAX_OPEN_LIBRARY_RESPONSE_BYTES + 1)}

        def __enter__(self) -> OversizedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            raise AssertionError("An oversized response must not be read")

    monkeypatch.setattr(open_library, "urlopen", lambda *_args, **_kwargs: OversizedResponse())

    with pytest.raises(OpenLibraryMetadataMalformedError, match="unexpectedly large"):
        lookup_open_library_metadata("9780140328721")
