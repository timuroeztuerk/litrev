import json

import pytest

from litrev.domain.sources import SourceType
from litrev.services.bibliographies import (
    BibliographyCitationKey,
    BibliographyExportSource,
    BibliographyFormat,
    BibliographyIdentifier,
    EmptyBibliographyError,
    EmptyBibliographyExportError,
    MalformedBibliographyError,
    UnsupportedBibliographyFormatError,
    doi_key,
    parse_bibliography,
    serialize_bibliography,
)


def test_bibtex_is_parsed_into_source_metadata() -> None:
    parsed = parse_bibliography(
        rb"""
        @article{nested-title,
          title = {A {Nested} Title},
          author = {Doe, Jane and {Research Collective}},
          year = {2024},
          journal = {Journal of Useful Results},
          doi = {https://doi.org/10.1234/Example},
          url = {https://example.org/paper},
          abstract = {A concise abstract.},
          language = {en},
          isbn = {978-1-4028-9462-6},
          pmid = {12345},
          eprint = {2401.12345},
          archivePrefix = {arXiv}
        }
        """,
        "library.bib",
    )

    assert parsed.bibliography_format == "bibtex"
    assert len(parsed.sources) == 1
    assert parsed.sources[0].entry_id == "nested-title"
    assert parsed.sources[0].citation_key == "nested-title"
    assert parsed.sources[0].source_type is SourceType.PAPER
    assert parsed.sources[0].title == "A Nested Title"
    assert parsed.sources[0].authors == ["Jane Doe", "Research Collective"]
    assert parsed.sources[0].publication_year == 2024
    assert parsed.sources[0].venue == "Journal of Useful Results"
    assert parsed.sources[0].doi == "https://doi.org/10.1234/Example"
    assert parsed.sources[0].url == "https://example.org/paper"
    assert parsed.sources[0].abstract == "A concise abstract."
    assert parsed.sources[0].language == "en"
    assert [
        (identifier.identifier_type, identifier.value)
        for identifier in parsed.sources[0].identifiers
    ] == [
        ("isbn", "978-1-4028-9462-6"),
        ("pmid", "12345"),
        ("arxiv", "2401.12345"),
    ]


def test_ris_is_parsed_into_source_metadata() -> None:
    parsed = parse_bibliography(
        b"""TY  - JOUR
ID  - ris-1
TI  - A RIS Paper
AU  - Doe, Jane
AU  - Research Collective
PY  - 2023/05/01
JO  - Evidence Journal
DO  - 10.1234/ris
UR  - https://example.org/ris
AB  - Imported from RIS.
LA  - en
SN  - 2049-3630
AN  - database-123
ER  -
""",
        "library.ris",
    )

    assert parsed.bibliography_format == "ris"
    assert len(parsed.sources) == 1
    assert parsed.sources[0].entry_id == "ris-1"
    assert parsed.sources[0].citation_key == "ris-1"
    assert parsed.sources[0].source_type is SourceType.PAPER
    assert parsed.sources[0].title == "A RIS Paper"
    assert parsed.sources[0].authors == ["Doe, Jane", "Research Collective"]
    assert parsed.sources[0].publication_year == 2023
    assert parsed.sources[0].venue == "Evidence Journal"
    assert parsed.sources[0].doi == "10.1234/ris"
    assert parsed.sources[0].url == "https://example.org/ris"
    assert parsed.sources[0].abstract == "Imported from RIS."
    assert parsed.sources[0].language == "en"
    assert [
        (identifier.identifier_type, identifier.value)
        for identifier in parsed.sources[0].identifiers
    ] == [
        ("issn", "2049-3630"),
        ("accession", "database-123"),
    ]


def test_csl_json_is_parsed_into_source_metadata() -> None:
    content = json.dumps(
        {
            "id": 42,
            "type": "book",
            "title": "A CSL Book",
            "author": [
                {"given": "Jane", "family": "Doe"},
                {"literal": "Research Collective"},
            ],
            "issued": {"date-parts": [[2022, 4, 3]]},
            "publisher": "Evidence Press",
            "DOI": "10.1234/csl",
            "URL": "https://example.org/csl",
            "abstract": "Imported from CSL JSON.",
            "language": "en",
            "ISBN": "978-0-306-40615-7",
            "ISSN": "2049-3630",
            "PMID": "13579",
            "PMCID": "PMC2468",
            "archive": "arXiv",
            "archive_location": "2501.01234",
        }
    ).encode()

    parsed = parse_bibliography(content, "library.json")

    assert parsed.bibliography_format == "csl-json"
    assert len(parsed.sources) == 1
    assert parsed.sources[0].entry_id == "42"
    assert parsed.sources[0].citation_key == "42"
    assert parsed.sources[0].source_type is SourceType.BOOK
    assert parsed.sources[0].title == "A CSL Book"
    assert parsed.sources[0].authors == ["Jane Doe", "Research Collective"]
    assert parsed.sources[0].publication_year == 2022
    assert parsed.sources[0].venue == "Evidence Press"
    assert parsed.sources[0].doi == "10.1234/csl"
    assert parsed.sources[0].url == "https://example.org/csl"
    assert parsed.sources[0].abstract == "Imported from CSL JSON."
    assert parsed.sources[0].language == "en"
    assert [
        (identifier.identifier_type, identifier.value)
        for identifier in parsed.sources[0].identifiers
    ] == [
        ("isbn", "978-0-306-40615-7"),
        ("issn", "2049-3630"),
        ("pmid", "13579"),
        ("pmcid", "PMC2468"),
        ("arxiv", "2501.01234"),
    ]


def test_doi_keys_ignore_common_resolver_prefixes_and_case() -> None:
    assert doi_key("https://doi.org/10.1234/Example") == doi_key("doi:10.1234/example")


@pytest.mark.parametrize(
    ("bibliography_format", "filename", "citation_key"),
    [
        (BibliographyFormat.BIBTEX, "library.bib", "unicode-bib"),
        (BibliographyFormat.RIS, "library.ris", "unicode-ris"),
        (BibliographyFormat.CSL_JSON, "library.json", "unicode-csl"),
    ],
)
def test_export_round_trip_preserves_canonical_metadata_and_identifiers(
    bibliography_format: BibliographyFormat,
    filename: str,
    citation_key: str,
) -> None:
    source = BibliographyExportSource(
        source_id=7,
        source_type=SourceType.PAPER,
        title="Über evidence {α}",
        authors=["Research {Collective}", "Ada Lovelace"],
        publication_year=2025,
        venue="Journal Ω",
        doi="10.1234/Unicode",
        url="https://example.org/über",
        abstract="Résumé with useful evidence.",
        language="de",
        identifiers=[
            BibliographyIdentifier(identifier_type="issn", value="2049-3630"),
            BibliographyIdentifier(identifier_type="issn", value="2754-1234"),
            BibliographyIdentifier(identifier_type="pmid", value="12345"),
            BibliographyIdentifier(identifier_type="pmcid", value="PMC2468"),
            BibliographyIdentifier(identifier_type="arxiv", value="2501.01234"),
            BibliographyIdentifier(identifier_type="custom-id", value="α-42"),
        ],
        citation_keys=[
            BibliographyCitationKey(
                bibliography_format=bibliography_format,
                value=citation_key,
            )
        ],
    )

    exported = serialize_bibliography([source], bibliography_format)
    imported = parse_bibliography(exported.encode("utf-8"), filename).sources[0]

    assert imported.citation_key == citation_key
    assert imported.source_type is source.source_type
    assert imported.title == source.title
    assert imported.authors == source.authors
    assert imported.publication_year == source.publication_year
    assert imported.venue == source.venue
    assert imported.doi == source.doi
    assert imported.url == source.url
    assert imported.abstract == source.abstract
    assert imported.language == source.language
    assert sorted(
        (identifier.identifier_type, identifier.value) for identifier in imported.identifiers
    ) == sorted((identifier.identifier_type, identifier.value) for identifier in source.identifiers)


@pytest.mark.parametrize("bibliography_format", list(BibliographyFormat))
def test_export_is_deterministic_and_generates_unique_keys_for_conflicts(
    bibliography_format: BibliographyFormat,
) -> None:
    sources = [
        _export_source(
            source_id=3,
            source_type=SourceType.PAPER,
            title="Zulu paper",
            bibliography_format=bibliography_format,
            citation_key="shared-key",
        ),
        _export_source(
            source_id=1,
            source_type=SourceType.BOOK,
            title="Alpha book",
            bibliography_format=bibliography_format,
            citation_key="shared-key",
        ),
        _export_source(
            source_id=2,
            source_type=SourceType.OTHER,
            title="Beta source",
            bibliography_format=bibliography_format,
            citation_key=" unsafe " if bibliography_format is BibliographyFormat.BIBTEX else None,
        ),
    ]

    exported = serialize_bibliography(sources, bibliography_format)
    reversed_export = serialize_bibliography(list(reversed(sources)), bibliography_format)
    suffix = {
        BibliographyFormat.BIBTEX: ".bib",
        BibliographyFormat.RIS: ".ris",
        BibliographyFormat.CSL_JSON: ".json",
    }[bibliography_format]
    imported = parse_bibliography(exported.encode("utf-8"), f"library{suffix}").sources

    assert exported == reversed_export
    assert [source.title for source in imported] == ["Alpha book", "Beta source", "Zulu paper"]
    assert [source.source_type for source in imported] == [
        SourceType.BOOK,
        SourceType.OTHER,
        SourceType.PAPER,
    ]
    assert imported[0].citation_key == "shared-key"
    assert len({source.citation_key for source in imported}) == 3
    assert all(source.citation_key for source in imported)
    assert all(source.authors == [] for source in imported)
    assert all(source.publication_year is None for source in imported)
    assert all(source.venue is None for source in imported)
    assert all(source.doi is None for source in imported)
    assert all(source.url is None for source in imported)
    assert all(source.abstract is None for source in imported)
    assert all(source.language is None for source in imported)
    assert all(source.identifiers == [] for source in imported)


def test_empty_library_cannot_be_serialized() -> None:
    with pytest.raises(EmptyBibliographyExportError, match="no sources"):
        serialize_bibliography([], BibliographyFormat.BIBTEX)


def _export_source(
    *,
    source_id: int,
    source_type: SourceType,
    title: str,
    bibliography_format: BibliographyFormat,
    citation_key: str | None,
) -> BibliographyExportSource:
    return BibliographyExportSource(
        source_id=source_id,
        source_type=source_type,
        title=title,
        authors=[],
        publication_year=None,
        venue=None,
        doi=None,
        url=None,
        abstract=None,
        language=None,
        identifiers=[],
        citation_keys=[
            BibliographyCitationKey(
                bibliography_format=bibliography_format,
                value=citation_key,
            )
        ]
        if citation_key is not None
        else [],
    )


@pytest.mark.parametrize(
    ("content", "filename", "error_type"),
    [
        (b"", "empty.bib", EmptyBibliographyError),
        (b"@article{broken", "broken.bib", MalformedBibliographyError),
        (b"TY  - JOUR\nTI  - Missing terminator\n", "broken.ris", MalformedBibliographyError),
        (b"not json", "broken.json", MalformedBibliographyError),
        (b"[]", "empty.json", EmptyBibliographyError),
        (b"title,author", "library.csv", UnsupportedBibliographyFormatError),
        (b"\xff", "library.ris", MalformedBibliographyError),
    ],
)
def test_invalid_bibliographies_are_rejected(
    content: bytes,
    filename: str,
    error_type: type[ValueError],
) -> None:
    with pytest.raises(error_type):
        parse_bibliography(content, filename)
