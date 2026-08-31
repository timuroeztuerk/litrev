import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from litrev.domain.sources import SourceType
from litrev.services import doi_metadata
from litrev.services.doi_metadata import (
    CROSSREF_RESPONSE_MEDIA_TYPE,
    DoiMetadataMalformedError,
    DoiMetadataMismatchError,
    DoiMetadataNotFoundError,
    DoiMetadataRateLimitedError,
    DoiMetadataUnavailableError,
    lookup_crossref_metadata,
)


def crossref_response(**overrides: object) -> bytes:
    message: dict[str, object] = {
        "DOI": "10.1234/Example",
        "type": "journal-article",
        "title": ["Über evidence"],
        "author": [
            {"given": "Ada", "family": "Lovelace"},
            {"name": "Research Collective"},
        ],
        "published-print": {"date-parts": [[2024, 3, 1]]},
        "published-online": {"date-parts": [[2023, 12, 1]]},
        "container-title": ["Journal Ω"],
        "URL": "https://doi.org/10.1234/example",
        "abstract": "<jats:p>A <jats:italic>useful</jats:italic> abstract.</jats:p>",
        "language": "en",
        "ISBN": ["978-0-306-40615-7"],
        "ISSN": ["2049-3630", "2049-3630"],
    }
    message.update(overrides)
    return json.dumps({"status": "ok", "message": message}).encode()


def test_crossref_lookup_requests_one_encoded_doi_and_maps_canonical_metadata() -> None:
    requests: list[Request] = []

    def fetch(request: Request) -> bytes:
        requests.append(request)
        return crossref_response()

    metadata = lookup_crossref_metadata("https://doi.org/10.1234/Example", fetch=fetch)

    assert requests[0].full_url == "https://api.crossref.org/works/10.1234%2FExample"
    assert requests[0].get_header("Accept") == CROSSREF_RESPONSE_MEDIA_TYPE
    assert requests[0].get_header("User-agent", "").startswith("Litrev/")
    assert metadata.doi == "10.1234/Example"
    assert metadata.source_type is SourceType.PAPER
    assert metadata.title == "Über evidence"
    assert metadata.authors == ["Ada Lovelace", "Research Collective"]
    assert metadata.publication_year == 2024
    assert metadata.venue == "Journal Ω"
    assert metadata.url == "https://doi.org/10.1234/example"
    assert metadata.abstract == "A useful abstract."
    assert metadata.language == "en"
    assert [
        (identifier.identifier_type, identifier.value) for identifier in metadata.identifiers or []
    ] == [
        ("isbn", "978-0-306-40615-7"),
        ("issn", "2049-3630"),
    ]


def test_crossref_lookup_rejects_a_different_or_unusable_record() -> None:
    with pytest.raises(DoiMetadataMismatchError, match="different DOI"):
        lookup_crossref_metadata(
            "10.1234/requested",
            fetch=lambda _request: crossref_response(DOI="10.1234/different"),
        )

    with pytest.raises(DoiMetadataMalformedError, match="no usable metadata"):
        lookup_crossref_metadata(
            "10.1234/example",
            fetch=lambda _request: crossref_response(
                type=None,
                title=None,
                author=None,
                **{
                    "published-print": None,
                    "published-online": None,
                    "container-title": None,
                    "URL": None,
                    "abstract": None,
                    "language": None,
                    "ISBN": None,
                    "ISSN": None,
                },
            ),
        )


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (404, DoiMetadataNotFoundError),
        (429, DoiMetadataRateLimitedError),
        (503, DoiMetadataUnavailableError),
    ],
)
def test_crossref_http_failures_are_specific(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    error_type: type[Exception],
) -> None:
    def fail_request(request: Request, *, timeout: int) -> bytes:
        assert timeout == 10
        raise HTTPError(request.full_url, status_code, "failure", {}, None)

    monkeypatch.setattr(doi_metadata, "urlopen", fail_request)

    with pytest.raises(error_type):
        lookup_crossref_metadata("10.1234/example")
