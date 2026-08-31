from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from litrev.domain.sources import SourceType
from litrev.services.bibliographies import doi_key, normalize_imported_doi

CROSSREF_PROVIDER = "Crossref"
CROSSREF_API_ROOT = "https://api.crossref.org/works"
CROSSREF_RESPONSE_MEDIA_TYPE = "application/vnd.crossref-api-message+json"
MAX_CROSSREF_RESPONSE_BYTES = 2 * 1024 * 1024
CROSSREF_TIMEOUT_SECONDS = 10
_CROSSREF_USER_AGENT = "Litrev/0.1 (local literature-review application)"


class DoiMetadataError(Exception):
    pass


class InvalidDoiError(DoiMetadataError):
    pass


class DoiMetadataNotFoundError(DoiMetadataError):
    pass


class DoiMetadataRateLimitedError(DoiMetadataError):
    pass


class DoiMetadataUnavailableError(DoiMetadataError):
    pass


class DoiMetadataMalformedError(DoiMetadataError):
    pass


class DoiMetadataMismatchError(DoiMetadataError):
    pass


@dataclass(frozen=True)
class DoiMetadataIdentifier:
    identifier_type: str
    value: str


@dataclass(frozen=True)
class DoiMetadata:
    doi: str
    source_type: SourceType | None
    title: str | None
    authors: list[str] | None
    publication_year: int | None
    venue: str | None
    url: str | None
    abstract: str | None
    language: str | None
    identifiers: list[DoiMetadataIdentifier] | None


CrossrefFetch = Callable[[Request], bytes]


def normalize_doi_for_lookup(doi: str) -> str:
    normalized = normalize_imported_doi(doi)
    prefix, separator, suffix = normalized.partition("/")
    prefix_segments = prefix.split(".")
    if not normalized:
        raise InvalidDoiError("Enter a DOI to look up.")
    if (
        len(normalized) > 255
        or separator != "/"
        or len(prefix_segments) < 2
        or prefix_segments[0].casefold() != "10"
        or any(not segment for segment in prefix_segments)
        or not suffix
        or any(character.isspace() or ord(character) < 32 for character in normalized)
    ):
        raise InvalidDoiError(
            "Enter a DOI with a 10. prefix and a non-empty suffix separated by a slash."
        )
    return normalized


def lookup_crossref_metadata(
    doi: str,
    *,
    fetch: CrossrefFetch | None = None,
) -> DoiMetadata:
    requested_doi = normalize_doi_for_lookup(doi)
    provider_url = crossref_record_url(requested_doi)
    request = Request(
        provider_url,
        headers={
            "Accept": CROSSREF_RESPONSE_MEDIA_TYPE,
            "User-Agent": _CROSSREF_USER_AGENT,
        },
    )
    content = (fetch or _fetch_crossref)(request)
    metadata = _parse_crossref_response(content)
    if doi_key(metadata.doi) != doi_key(requested_doi):
        raise DoiMetadataMismatchError(
            "Crossref returned metadata for a different DOI; nothing was saved."
        )
    return metadata


def crossref_record_url(doi: str) -> str:
    return f"{CROSSREF_API_ROOT}/{quote(normalize_imported_doi(doi), safe='')}"


def _fetch_crossref(request: Request) -> bytes:
    try:
        with urlopen(request, timeout=CROSSREF_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_CROSSREF_RESPONSE_BYTES:
                raise DoiMetadataMalformedError("Crossref returned an unexpectedly large record.")
            content = response.read(MAX_CROSSREF_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            raise DoiMetadataNotFoundError("Crossref has no metadata for this DOI.") from error
        if error.code in {403, 429}:
            raise DoiMetadataRateLimitedError(
                "Crossref is temporarily limiting requests. Try again later."
            ) from error
        raise DoiMetadataUnavailableError(
            "Crossref could not complete the metadata lookup."
        ) from error
    except (TimeoutError, URLError, OSError, ValueError) as error:
        raise DoiMetadataUnavailableError(
            "Crossref could not be reached. Check the network connection and try again."
        ) from error

    if len(content) > MAX_CROSSREF_RESPONSE_BYTES:
        raise DoiMetadataMalformedError("Crossref returned an unexpectedly large record.")
    return content


def _parse_crossref_response(content: bytes) -> DoiMetadata:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DoiMetadataMalformedError("Crossref returned unreadable metadata.") from error
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise DoiMetadataMalformedError("Crossref returned an invalid metadata response.")
    message = payload.get("message")
    if not isinstance(message, dict):
        raise DoiMetadataMalformedError("Crossref returned an invalid metadata record.")

    doi = _required_text(message, "DOI", 255)
    title = _first_text(message, "title", 500)
    authors = _authors(message.get("author"))
    publication_year = _publication_year(message)
    venue = _first_text(message, "container-title", 500) or _optional_text(
        message, "publisher", 500
    )
    url = _web_url(message.get("URL"))
    abstract = _abstract(message.get("abstract"))
    language = _optional_text(message, "language", 35)
    identifiers = _identifiers(message)
    source_type = _source_type(message.get("type"))

    if all(
        value is None
        for value in (
            source_type,
            title,
            authors,
            publication_year,
            venue,
            url,
            abstract,
            language,
            identifiers,
        )
    ):
        raise DoiMetadataMalformedError("Crossref returned no usable metadata for this DOI.")

    return DoiMetadata(
        doi=doi,
        source_type=source_type,
        title=title,
        authors=authors,
        publication_year=publication_year,
        venue=venue,
        url=url,
        abstract=abstract,
        language=language,
        identifiers=identifiers,
    )


def _required_text(values: Mapping[str, object], field: str, maximum_length: int) -> str:
    value = _optional_text(values, field, maximum_length)
    if value is None:
        raise DoiMetadataMalformedError(f"Crossref metadata is missing {field}.")
    return value


def _optional_text(values: Mapping[str, object], field: str, maximum_length: int) -> str | None:
    value = values.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DoiMetadataMalformedError(f"Crossref returned an invalid {field} value.")
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > maximum_length:
        raise DoiMetadataMalformedError(f"Crossref returned an oversized {field} value.")
    return cleaned


def _first_text(values: Mapping[str, object], field: str, maximum_length: int) -> str | None:
    value = values.get(field)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DoiMetadataMalformedError(f"Crossref returned an invalid {field} list.")
    for item in value:
        cleaned = " ".join(item.split())
        if cleaned:
            if len(cleaned) > maximum_length:
                raise DoiMetadataMalformedError(f"Crossref returned an oversized {field} value.")
            return cleaned
    return None


def _authors(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise DoiMetadataMalformedError("Crossref returned an invalid author list.")
    if len(value) > 100:
        raise DoiMetadataMalformedError("Crossref returned too many authors.")

    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise DoiMetadataMalformedError("Crossref returned an invalid author.")
        name = _author_name(item)
        if name is not None:
            authors.append(name)
    return authors or None


def _author_name(author: Mapping[str, object]) -> str | None:
    literal = author.get("name")
    if literal is not None:
        if not isinstance(literal, str):
            raise DoiMetadataMalformedError("Crossref returned an invalid author name.")
        name = " ".join(literal.split())
    else:
        parts: list[str] = []
        for field in ("given", "family"):
            value = author.get(field)
            if value is not None and not isinstance(value, str):
                raise DoiMetadataMalformedError("Crossref returned an invalid author name.")
            if isinstance(value, str) and value.strip():
                parts.append(" ".join(value.split()))
        name = " ".join(parts)
    if not name:
        return None
    if len(name) > 500:
        raise DoiMetadataMalformedError("Crossref returned an oversized author name.")
    return name


def _publication_year(message: Mapping[str, object]) -> int | None:
    for field in ("published-print", "published-online", "published", "issued"):
        value = message.get(field)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise DoiMetadataMalformedError("Crossref returned an invalid publication date.")
        date_parts = value.get("date-parts")
        if (
            not isinstance(date_parts, list)
            or not date_parts
            or not isinstance(date_parts[0], list)
            or not date_parts[0]
            or not isinstance(date_parts[0][0], int)
        ):
            raise DoiMetadataMalformedError("Crossref returned an invalid publication date.")
        year = date_parts[0][0]
        if not 1 <= year <= 9999:
            raise DoiMetadataMalformedError("Crossref returned an invalid publication year.")
        return year
    return None


def _web_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DoiMetadataMalformedError("Crossref returned an invalid URL.")
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(cleaned) > 2048:
        raise DoiMetadataMalformedError("Crossref returned an invalid URL.")
    return cleaned


class _AbstractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _abstract(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DoiMetadataMalformedError("Crossref returned an invalid abstract.")
    parser = _AbstractParser()
    try:
        parser.feed(value)
        parser.close()
    except ValueError as error:
        raise DoiMetadataMalformedError("Crossref returned an invalid abstract.") from error
    cleaned = " ".join("".join(parser.parts).split())
    if not cleaned:
        return None
    if len(cleaned) > 100_000:
        raise DoiMetadataMalformedError("Crossref returned an oversized abstract.")
    return cleaned


def _identifiers(message: Mapping[str, object]) -> list[DoiMetadataIdentifier] | None:
    identifiers: dict[tuple[str, str], DoiMetadataIdentifier] = {}
    for field, identifier_type in (("ISBN", "isbn"), ("ISSN", "issn")):
        value = message.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise DoiMetadataMalformedError(f"Crossref returned an invalid {field} list.")
        for item in value:
            cleaned = item.strip()
            if not cleaned:
                continue
            if len(cleaned) > 500:
                raise DoiMetadataMalformedError(f"Crossref returned an oversized {field} value.")
            identifiers.setdefault(
                (identifier_type, cleaned.casefold()),
                DoiMetadataIdentifier(identifier_type=identifier_type, value=cleaned),
            )
    if len(identifiers) > 50:
        raise DoiMetadataMalformedError("Crossref returned too many identifiers.")
    return (
        sorted(
            identifiers.values(),
            key=lambda identifier: (identifier.identifier_type, identifier.value.casefold()),
        )
        or None
    )


def _source_type(value: object) -> SourceType | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DoiMetadataMalformedError("Crossref returned an invalid work type.")
    if value in {
        "journal-article",
        "proceedings-article",
        "posted-content",
        "report",
        "dissertation",
        "peer-review",
    }:
        return SourceType.PAPER
    if value in {
        "book",
        "book-chapter",
        "book-part",
        "book-section",
        "book-series",
        "edited-book",
        "monograph",
        "reference-book",
        "reference-entry",
    }:
        return SourceType.BOOK
    return SourceType.OTHER
