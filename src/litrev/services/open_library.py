from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from litrev.domain.isbn import IsbnValidationError, isbn_identity
from litrev.domain.sources import SourceType
from litrev.services.metadata import MetadataIdentifier, MetadataProposal, RetrievedMetadata

OPEN_LIBRARY_PROVIDER = "Open Library"
OPEN_LIBRARY_API_ROOT = "https://openlibrary.org/api/volumes/brief/isbn"
OPEN_LIBRARY_TIMEOUT_SECONDS = 10
MAX_OPEN_LIBRARY_RESPONSE_BYTES = 2 * 1024 * 1024
_OPEN_LIBRARY_USER_AGENT = "Litrev/0.1 (https://github.com/timuroeztuerk/litrev)"
_BOOK_KEY = re.compile(r"^/books/OL[0-9]+M$")
_YEAR = re.compile(r"(?<![0-9])([1-9][0-9]{3})(?![0-9])")


class OpenLibraryMetadataError(Exception):
    pass


class OpenLibraryMetadataNotFoundError(OpenLibraryMetadataError):
    pass


class OpenLibraryMetadataAmbiguousError(OpenLibraryMetadataError):
    pass


class OpenLibraryMetadataRateLimitedError(OpenLibraryMetadataError):
    pass


class OpenLibraryMetadataUnavailableError(OpenLibraryMetadataError):
    pass


class OpenLibraryMetadataMalformedError(OpenLibraryMetadataError):
    pass


class OpenLibraryMetadataMismatchError(OpenLibraryMetadataError):
    pass


OpenLibraryFetch = Callable[[Request], bytes]


def lookup_open_library_metadata(
    isbn: str,
    *,
    fetch: OpenLibraryFetch | None = None,
) -> RetrievedMetadata:
    requested = isbn_identity(isbn)
    request = Request(
        f"{OPEN_LIBRARY_API_ROOT}/{quote(requested.canonical_isbn13, safe='')}.json",
        headers={
            "Accept": "application/json",
            "User-Agent": _OPEN_LIBRARY_USER_AGENT,
        },
    )
    content = (fetch or _fetch_open_library)(request)
    return _parse_open_library_response(content, requested.canonical_isbn13)


def _fetch_open_library(request: Request) -> bytes:
    try:
        with urlopen(request, timeout=OPEN_LIBRARY_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_OPEN_LIBRARY_RESPONSE_BYTES:
                raise OpenLibraryMetadataMalformedError(
                    "Open Library returned an unexpectedly large catalog record."
                )
            content = response.read(MAX_OPEN_LIBRARY_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            raise OpenLibraryMetadataNotFoundError(
                "Open Library has no catalog match for this ISBN."
            ) from error
        if error.code in {403, 429}:
            raise OpenLibraryMetadataRateLimitedError(
                "Open Library is temporarily limiting requests. Try again later."
            ) from error
        raise OpenLibraryMetadataUnavailableError(
            "Open Library could not complete the catalog lookup."
        ) from error
    except (TimeoutError, URLError, OSError, ValueError) as error:
        raise OpenLibraryMetadataUnavailableError(
            "Open Library could not be reached. Check the network connection and try again."
        ) from error

    if len(content) > MAX_OPEN_LIBRARY_RESPONSE_BYTES:
        raise OpenLibraryMetadataMalformedError(
            "Open Library returned an unexpectedly large catalog record."
        )
    return content


def _parse_open_library_response(content: bytes, requested_isbn13: str) -> RetrievedMetadata:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenLibraryMetadataMalformedError(
            "Open Library returned unreadable catalog metadata."
        ) from error
    if not isinstance(payload, dict):
        raise OpenLibraryMetadataMalformedError(
            "Open Library returned an invalid catalog response."
        )
    records = payload.get("records")
    if not isinstance(records, dict):
        raise OpenLibraryMetadataMalformedError(
            "Open Library returned an invalid catalog response."
        )
    if not records:
        raise OpenLibraryMetadataNotFoundError("Open Library has no catalog match for this ISBN.")

    exact_records: list[tuple[str, Mapping[str, object]]] = []
    for key, record in records.items():
        if (
            not isinstance(key, str)
            or _BOOK_KEY.fullmatch(key) is None
            or not isinstance(record, dict)
        ):
            raise OpenLibraryMetadataMalformedError(
                "Open Library returned an invalid edition record."
            )
        record_isbns = _text_list(record.get("isbns"), field="ISBNs", maximum_items=50)
        if _contains_canonical_isbn(record_isbns, requested_isbn13):
            exact_records.append((key, record))

    if not exact_records:
        raise OpenLibraryMetadataMismatchError(
            "Open Library returned catalog records for a different ISBN."
        )
    if len(exact_records) > 1:
        raise OpenLibraryMetadataAmbiguousError(
            "Open Library returned multiple editions with this exact ISBN."
        )

    key, record = exact_records[0]
    data = record.get("data")
    if not isinstance(data, dict):
        raise OpenLibraryMetadataMalformedError("Open Library returned an invalid edition record.")
    record_url = f"https://openlibrary.org{key}"
    return RetrievedMetadata(
        provider=OPEN_LIBRARY_PROVIDER,
        provider_url=record_url,
        identifier_type="isbn",
        retrieved_identifier=requested_isbn13,
        proposal=MetadataProposal(
            source_type=SourceType.BOOK,
            title=_optional_text(data.get("title"), field="title", maximum_length=500),
            authors=_named_values(data.get("authors"), field="authors", maximum_items=100),
            publication_year=_publication_year(data, record),
            venue=_first_named_value(data.get("publishers"), field="publishers"),
            url=record_url,
            abstract=_description(record, data),
            language=_language(record, data),
            identifiers=_identifiers(record, data),
        ),
    )


def _contains_canonical_isbn(values: list[str], requested_isbn13: str) -> bool:
    for value in values:
        try:
            if isbn_identity(value).canonical_isbn13 == requested_isbn13:
                return True
        except IsbnValidationError:
            continue
    return False


def _optional_text(value: object, *, field: str, maximum_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OpenLibraryMetadataMalformedError(f"Open Library returned an invalid {field} value.")
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > maximum_length:
        raise OpenLibraryMetadataMalformedError(
            f"Open Library returned an oversized {field} value."
        )
    return cleaned


def _text_list(value: object, *, field: str, maximum_items: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OpenLibraryMetadataMalformedError(f"Open Library returned an invalid {field} list.")
    if len(value) > maximum_items:
        raise OpenLibraryMetadataMalformedError(f"Open Library returned too many {field}.")
    return [item.strip() for item in value if item.strip()]


def _named_values(value: object, *, field: str, maximum_items: int) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > maximum_items:
        raise OpenLibraryMetadataMalformedError(f"Open Library returned an invalid {field} list.")
    names: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise OpenLibraryMetadataMalformedError(
                f"Open Library returned an invalid {field} entry."
            )
        name = _optional_text(item.get("name"), field=field, maximum_length=500)
        if name is not None:
            names.append(name)
    return names or None


def _first_named_value(value: object, *, field: str) -> str | None:
    values = _named_values(value, field=field, maximum_items=50)
    return values[0] if values else None


def _publication_year(data: Mapping[str, object], record: Mapping[str, object]) -> int | None:
    publish_date = data.get("publish_date")
    if publish_date is None:
        publish_dates = _text_list(
            record.get("publishDates"), field="publication dates", maximum_items=50
        )
        publish_date = publish_dates[0] if publish_dates else None
    if publish_date is None:
        return None
    if not isinstance(publish_date, str):
        raise OpenLibraryMetadataMalformedError(
            "Open Library returned an invalid publication date."
        )
    match = _YEAR.search(publish_date)
    return int(match.group(1)) if match is not None else None


def _details(record: Mapping[str, object]) -> Mapping[str, object] | None:
    wrapper = record.get("details")
    if wrapper is None:
        return None
    if not isinstance(wrapper, dict):
        raise OpenLibraryMetadataMalformedError("Open Library returned invalid edition details.")
    details = wrapper.get("details")
    if details is None:
        return None
    if not isinstance(details, dict):
        raise OpenLibraryMetadataMalformedError("Open Library returned invalid edition details.")
    return details


def _description(record: Mapping[str, object], data: Mapping[str, object]) -> str | None:
    value = data.get("description")
    details = _details(record)
    if value is None and details is not None:
        value = details.get("description")
    if isinstance(value, dict):
        value = value.get("value")
    return _optional_text(value, field="description", maximum_length=100_000)


def _language(record: Mapping[str, object], data: Mapping[str, object]) -> str | None:
    value = data.get("languages")
    details = _details(record)
    if value is None and details is not None:
        value = details.get("languages")
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 50:
        raise OpenLibraryMetadataMalformedError("Open Library returned an invalid language list.")
    for item in value:
        if not isinstance(item, dict):
            raise OpenLibraryMetadataMalformedError(
                "Open Library returned an invalid language entry."
            )
        name = item.get("name")
        key = item.get("key")
        if isinstance(name, str) and name.strip():
            return _optional_text(name, field="language", maximum_length=35)
        if isinstance(key, str) and key.startswith("/languages/"):
            return _optional_text(
                key.removeprefix("/languages/"),
                field="language",
                maximum_length=35,
            )
        if name is not None or key is not None:
            raise OpenLibraryMetadataMalformedError(
                "Open Library returned an invalid language entry."
            )
    return None


def _identifiers(
    record: Mapping[str, object],
    data: Mapping[str, object],
) -> list[MetadataIdentifier] | None:
    identifiers: dict[tuple[str, str], MetadataIdentifier] = {}
    _add_identifiers(identifiers, "isbn", record.get("isbns"), field="ISBNs")
    _add_identifiers(identifiers, "lccn", record.get("lccns"), field="LCCNs")
    _add_identifiers(identifiers, "oclc", record.get("oclcs"), field="OCLCs")
    _add_identifiers(identifiers, "openlibrary", record.get("olids"), field="OLIDs")

    data_identifiers = data.get("identifiers")
    if data_identifiers is not None:
        if not isinstance(data_identifiers, dict):
            raise OpenLibraryMetadataMalformedError("Open Library returned invalid identifiers.")
        for provider_type, identifier_type in (
            ("isbn_10", "isbn"),
            ("isbn_13", "isbn"),
            ("lccn", "lccn"),
            ("oclc", "oclc"),
            ("goodreads", "goodreads"),
            ("librarything", "librarything"),
            ("openlibrary", "openlibrary"),
        ):
            if provider_type in data_identifiers:
                _add_identifiers(
                    identifiers,
                    identifier_type,
                    data_identifiers[provider_type],
                    field=provider_type,
                )
    if len(identifiers) > 50:
        raise OpenLibraryMetadataMalformedError("Open Library returned too many identifiers.")
    return (
        sorted(
            identifiers.values(),
            key=lambda identifier: (identifier.identifier_type, identifier.value.casefold()),
        )
        or None
    )


def _add_identifiers(
    identifiers: dict[tuple[str, str], MetadataIdentifier],
    identifier_type: str,
    value: object,
    *,
    field: str,
) -> None:
    for item in _text_list(value, field=field, maximum_items=50):
        if len(item) > 500:
            raise OpenLibraryMetadataMalformedError(
                f"Open Library returned an oversized {field} value."
            )
        identifiers.setdefault(
            (identifier_type, item.casefold()),
            MetadataIdentifier(identifier_type=identifier_type, value=item),
        )
