import json
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from alembic import command

from litrev import api
from litrev.api import create_app
from litrev.domain.documents import ConversionStatus
from litrev.domain.sources import SourceType
from litrev.infrastructure.database import Database, _migration_config
from litrev.infrastructure.models import (
    AttachmentRecord,
    CollectionRecord,
    HighlightRecord,
    NoteRecord,
    SourceIdentifierRecord,
    SourceMetadataLookupRecord,
    SourceRecord,
    TagRecord,
)
from litrev.infrastructure.storage import LibraryPaths
from litrev.services import bibliographies, documents
from litrev.services.documents import DocumentConversionFailure
from litrev.services.doi_metadata import (
    DoiMetadataMalformedError,
    DoiMetadataNotFoundError,
    DoiMetadataRateLimitedError,
    DoiMetadataUnavailableError,
)
from litrev.services.metadata import MetadataIdentifier, MetadataProposal, RetrievedMetadata
from litrev.services.open_library import (
    OpenLibraryMetadataAmbiguousError,
    OpenLibraryMetadataMalformedError,
    OpenLibraryMetadataMismatchError,
    OpenLibraryMetadataNotFoundError,
    OpenLibraryMetadataRateLimitedError,
    OpenLibraryMetadataUnavailableError,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def source_update_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source_type": "paper",
        "title": "Updated source",
        "authors": ["Jane Researcher"],
        "publication_year": 2025,
        "venue": "Journal of Useful Results",
        "doi": "10.1234/updated",
        "url": "https://example.org/paper",
        "abstract": "A concise abstract.",
        "language": "en",
        "reading_status": "reading",
        "tags": [],
        "collections": [],
        "identifiers": [],
    }
    payload.update(overrides)
    return payload


def doi_metadata(**overrides: object) -> RetrievedMetadata:
    retrieved_identifier = str(overrides.pop("doi", "10.1234/example"))
    proposal = MetadataProposal(
        source_type=SourceType.PAPER,
        title="Crossref title",
        authors=["Ada Lovelace", "Research Collective"],
        publication_year=2024,
        venue="Crossref Journal",
        url="https://doi.org/10.1234/example",
        abstract="Crossref abstract.",
        language="en",
        identifiers=[
            MetadataIdentifier(identifier_type="isbn", value="978-0-306-40615-7"),
            MetadataIdentifier(identifier_type="issn", value="2049-3630"),
        ],
    )
    return RetrievedMetadata(
        provider="Crossref",
        provider_url=("https://api.crossref.org/works/" + retrieved_identifier.replace("/", "%2F")),
        identifier_type="doi",
        retrieved_identifier=retrieved_identifier,
        proposal=replace(proposal, **overrides),
    )


def isbn_metadata(**overrides: object) -> RetrievedMetadata:
    proposal = MetadataProposal(
        source_type=SourceType.BOOK,
        title="Matilda",
        authors=["Roald Dahl"],
        publication_year=1988,
        venue="Puffin",
        url="https://openlibrary.org/books/OL7353617M",
        abstract="A gifted child discovers an unusual power.",
        language="eng",
        identifiers=[
            MetadataIdentifier(identifier_type="isbn", value="0140328726"),
            MetadataIdentifier(identifier_type="isbn", value="9780140328721"),
            MetadataIdentifier(identifier_type="openlibrary", value="OL7353617M"),
        ],
    )
    return RetrievedMetadata(
        provider="Open Library",
        provider_url="https://openlibrary.org/books/OL7353617M",
        identifier_type="isbn",
        retrieved_identifier="9780140328721",
        proposal=replace(proposal, **overrides),
    )


async def save_isbn_book(
    client: httpx2.AsyncClient,
    *,
    identifiers: list[dict[str, str]] | None = None,
    title: str = "User book title",
) -> dict[str, object]:
    created = await client.post(
        "/api/sources",
        json={"source_type": "book", "title": title},
    )
    source_id = created.json()["id"]
    updated = await client.put(
        f"/api/sources/{source_id}",
        json=source_update_payload(
            source_type="book",
            title=title,
            authors=["User Author"],
            publication_year=1980,
            venue="User Publisher",
            doi=None,
            url=None,
            abstract=None,
            language=None,
            reading_status="unread",
            identifiers=(
                identifiers
                if identifiers is not None
                else [{"identifier_type": "isbn", "value": "0-14-032872-6"}]
            ),
        ),
    )
    assert updated.status_code == 200
    return updated.json()


@pytest.mark.anyio
async def test_health_reports_the_local_service() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.anyio
async def test_local_service_repairs_an_interrupted_reader_note_migration(
    tmp_path: Path,
) -> None:
    database = Database.from_path(tmp_path / "interrupted-reader-note-service.sqlite3")
    configuration = _migration_config()
    with database.engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "20260901_0010")
        connection.exec_driver_sql("ALTER TABLE notes ADD COLUMN attachment_id INTEGER")
        connection.exec_driver_sql("ALTER TABLE notes ADD COLUMN page_number INTEGER")
        connection.exec_driver_sql("ALTER TABLE notes ADD COLUMN highlight_id INTEGER")

    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    with database.engine.connect() as connection:
        reader_foreign_keys = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in connection.exec_driver_sql("PRAGMA foreign_key_list(notes)").mappings()
            if row["from"] in {"attachment_id", "highlight_id"}
        }
    assert reader_foreign_keys == {
        ("attachment_id", "attachments", "id", "RESTRICT"),
        ("highlight_id", "highlights", "id", "SET NULL"),
    }


@pytest.mark.anyio
async def test_source_can_be_created_and_listed() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "A useful paper",
                "doi": "10.1234/example",
            },
        )
        sources = await client.get("/api/sources")

    assert created.status_code == 201
    assert created.json()["source_type"] == "paper"
    assert created.json()["authors"] == []
    assert created.json()["publication_year"] is None
    assert created.json()["reading_status"] == "unread"
    assert created.json()["tags"] == []
    assert created.json()["collections"] == []
    assert created.json()["identifiers"] == []
    assert created.json()["citation_keys"] == []
    assert sources.status_code == 200
    assert sources.json()[0] == created.json()


@pytest.mark.anyio
async def test_book_can_be_quickly_captured_without_a_doi() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={"source_type": "book", "title": "  The Dawn of Everything  "},
        )

    assert created.status_code == 201
    assert created.json()["source_type"] == "book"
    assert created.json()["title"] == "The Dawn of Everything"
    assert created.json()["doi"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content", "expected_format", "expected"),
    [
        (
            "sources.bib",
            b"""@article{bib-1,
              title = {A BibTeX Paper},
              author = {Doe, Jane},
              year = {2024},
              journal = {BibTeX Journal},
              doi = {https://doi.org/10.1234/bib},
              url = {https://example.org/bib},
              abstract = {BibTeX abstract.},
              language = {en},
              isbn = {978-1-4028-9462-6},
              pmid = {12345},
              arxiv = {2401.12345},
              eprint = {2401.12345},
              archivePrefix = {arXiv}
            }""",
            "bibtex",
            {
                "source_type": "paper",
                "title": "A BibTeX Paper",
                "authors": ["Jane Doe"],
                "publication_year": 2024,
                "venue": "BibTeX Journal",
                "doi": "10.1234/bib",
                "url": "https://example.org/bib",
                "abstract": "BibTeX abstract.",
                "language": "en",
                "identifiers": [
                    {"identifier_type": "arxiv", "value": "2401.12345"},
                    {"identifier_type": "isbn", "value": "978-1-4028-9462-6"},
                    {"identifier_type": "pmid", "value": "12345"},
                ],
                "citation_keys": [
                    {"bibliography_format": "bibtex", "value": "bib-1"},
                ],
            },
        ),
        (
            "sources.ris",
            b"""TY  - JOUR
ID  - ris-1
TI  - A RIS Paper
AU  - Doe, Jane
PY  - 2023
JO  - RIS Journal
DO  - 10.1234/ris
UR  - https://example.org/ris
AB  - RIS abstract.
LA  - en
SN  - 2049-3630
AN  - database-123
ER  -
""",
            "ris",
            {
                "source_type": "paper",
                "title": "A RIS Paper",
                "authors": ["Doe, Jane"],
                "publication_year": 2023,
                "venue": "RIS Journal",
                "doi": "10.1234/ris",
                "url": "https://example.org/ris",
                "abstract": "RIS abstract.",
                "language": "en",
                "identifiers": [
                    {"identifier_type": "accession", "value": "database-123"},
                    {"identifier_type": "issn", "value": "2049-3630"},
                ],
                "citation_keys": [
                    {"bibliography_format": "ris", "value": "ris-1"},
                ],
            },
        ),
        (
            "sources.json",
            json.dumps(
                [
                    {
                        "id": "csl-1",
                        "type": "book",
                        "title": "A CSL Book",
                        "author": [{"given": "Jane", "family": "Doe"}],
                        "issued": {"date-parts": [[2022]]},
                        "publisher": "CSL Press",
                        "DOI": "10.1234/csl",
                        "URL": "https://example.org/csl",
                        "abstract": "CSL abstract.",
                        "language": "en",
                        "ISBN": "978-0-306-40615-7",
                        "PMCID": "PMC2468",
                        "archive": "arXiv",
                        "archive_location": "2501.01234",
                    }
                ]
            ).encode(),
            "csl-json",
            {
                "source_type": "book",
                "title": "A CSL Book",
                "authors": ["Jane Doe"],
                "publication_year": 2022,
                "venue": "CSL Press",
                "doi": "10.1234/csl",
                "url": "https://example.org/csl",
                "abstract": "CSL abstract.",
                "language": "en",
                "identifiers": [
                    {"identifier_type": "arxiv", "value": "2501.01234"},
                    {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
                    {"identifier_type": "pmcid", "value": "PMC2468"},
                ],
                "citation_keys": [
                    {"bibliography_format": "csl-json", "value": "csl-1"},
                ],
            },
        ),
    ],
)
async def test_bibliography_formats_import_metadata_through_the_api(
    filename: str,
    content: bytes,
    expected_format: str,
    expected: dict[str, object],
) -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": (filename, content, "application/octet-stream")},
        )
        sources = await client.get("/api/sources")

    assert imported.status_code == 200
    assert imported.json()["bibliography_format"] == expected_format
    assert imported.json()["total_entries"] == 1
    assert imported.json()["skipped"] == []
    assert len(imported.json()["imported"]) == 1
    assert {key: imported.json()["imported"][0][key] for key in expected} == expected
    assert sources.json() == imported.json()["imported"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "bibliography_format",
        "import_filename",
        "import_content",
        "export_filename",
        "content_type",
    ),
    [
        (
            "bibtex",
            "seed.bib",
            b"@article{roundtrip-key, title = {Seed}}",
            "litrev-library.bib",
            "application/x-bibtex; charset=utf-8",
        ),
        (
            "ris",
            "seed.ris",
            b"TY  - JOUR\nID  - roundtrip-key\nTI  - Seed\nER  -\n",
            "litrev-library.ris",
            "application/x-research-info-systems; charset=utf-8",
        ),
        (
            "csl-json",
            "seed.json",
            b'{"id":"roundtrip-key","type":"article-journal","title":"Seed"}',
            "litrev-library.json",
            "application/vnd.citationstyles.csl+json; charset=utf-8",
        ),
    ],
)
async def test_library_export_round_trips_canonical_metadata_through_the_api(
    bibliography_format: str,
    import_filename: str,
    import_content: bytes,
    export_filename: str,
    content_type: str,
) -> None:
    source_application = create_app(Database.in_memory())
    source_transport = httpx2.ASGITransport(app=source_application)
    async with (
        source_application.router.lifespan_context(source_application),
        httpx2.AsyncClient(transport=source_transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/bibliography-imports",
            files={
                "bibliography": (
                    import_filename,
                    import_content,
                    "application/octet-stream",
                )
            },
        )
        source_id = imported.json()["imported"][0]["id"]
        updated = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                title="Über evidence α",
                authors=["Research Collective", "Ada Lovelace"],
                publication_year=2025,
                venue="Journal Ω",
                doi="10.1234/unicode",
                url="https://example.org/evidence",
                abstract="Résumé with useful evidence.",
                language="de",
                reading_status="unread",
                identifiers=[
                    {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
                    {"identifier_type": "issn", "value": "2049-3630"},
                    {"identifier_type": "pmid", "value": "12345"},
                    {"identifier_type": "pmid", "value": "12345"},
                    {"identifier_type": "pmcid", "value": "PMC2468"},
                    {"identifier_type": "arxiv", "value": "2501.01234"},
                    {"identifier_type": "custom-id", "value": "α-42"},
                ],
            ),
        )
        exported = await client.get(f"/api/bibliography-exports/{bibliography_format}")

    assert exported.status_code == 200
    assert exported.headers["content-type"] == content_type
    assert exported.headers["content-disposition"] == (f'attachment; filename="{export_filename}"')
    assert exported.content.decode("utf-8")

    destination_application = create_app(Database.in_memory())
    destination_transport = httpx2.ASGITransport(app=destination_application)
    async with (
        destination_application.router.lifespan_context(destination_application),
        httpx2.AsyncClient(transport=destination_transport, base_url="http://test") as client,
    ):
        reimported_response = await client.post(
            "/api/bibliography-imports",
            files={
                "bibliography": (
                    export_filename,
                    exported.content,
                    content_type,
                )
            },
        )

    assert reimported_response.status_code == 200
    reimported = reimported_response.json()["imported"][0]
    canonical_fields = (
        "source_type",
        "title",
        "authors",
        "publication_year",
        "venue",
        "doi",
        "url",
        "abstract",
        "language",
        "identifiers",
        "citation_keys",
    )
    assert {field: reimported[field] for field in canonical_fields} == {
        field: updated.json()[field] for field in canonical_fields
    }


@pytest.mark.anyio
async def test_library_export_reports_empty_and_unsupported_formats() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        empty = await client.get("/api/bibliography-exports/bibtex")
        unsupported = await client.get("/api/bibliography-exports/endnote-xml")

    assert empty.status_code == 404
    assert empty.json()["detail"] == {
        "code": "empty_library",
        "message": "The library has no sources to export.",
    }
    assert unsupported.status_code == 415
    assert unsupported.json()["detail"] == {
        "code": "unsupported_bibliography_export",
        "message": "Choose BibTeX, RIS, or CSL JSON for the export.",
    }


@pytest.mark.anyio
async def test_imported_identifiers_can_be_corrected_without_losing_the_record_key() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    bibliography = json.dumps(
        {
            "id": "preserved-key",
            "type": "article-journal",
            "title": "Imported source",
            "DOI": "10.1234/imported",
            "PMID": "incorrect",
        }
    ).encode()
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": ("sources.json", bibliography, "application/json")},
        )
        source = imported.json()["imported"][0]
        corrected = await client.put(
            f"/api/sources/{source['id']}",
            json=source_update_payload(
                title=source["title"],
                doi=source["doi"],
                identifiers=[
                    {"identifier_type": "pmid", "value": "12345"},
                    {"identifier_type": "pmid", "value": "12345"},
                    {"identifier_type": "pmcid", "value": "PMC678"},
                ],
            ),
        )
        reopened = await client.get(f"/api/sources/{source['id']}")

    assert corrected.status_code == 200
    assert corrected.json() == reopened.json()
    assert corrected.json()["doi"] == "10.1234/imported"
    assert corrected.json()["identifiers"] == [
        {"identifier_type": "pmcid", "value": "PMC678"},
        {"identifier_type": "pmid", "value": "12345"},
    ]
    assert corrected.json()["citation_keys"] == [
        {"bibliography_format": "csl-json", "value": "preserved-key"}
    ]


@pytest.mark.anyio
async def test_bibliography_import_skips_doi_duplicates_without_overwriting_sources() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    bibliography = json.dumps(
        [
            {
                "id": "existing",
                "type": "article-journal",
                "title": "Attempted overwrite",
                "DOI": "https://doi.org/10.1234/EXISTING",
            },
            {
                "id": "new",
                "type": "article-journal",
                "title": "New source",
                "DOI": "10.1234/new",
            },
            {
                "id": "new-duplicate",
                "type": "article-journal",
                "title": "Duplicate in file",
                "DOI": "doi:10.1234/NEW",
            },
        ]
    ).encode()
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "Existing source", "doi": "10.1234/existing"},
        )
        imported = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": ("sources.json", bibliography, "application/json")},
        )
        sources = await client.get("/api/sources")

    assert imported.status_code == 200
    assert [source["title"] for source in imported.json()["imported"]] == ["New source"]
    assert imported.json()["skipped"] == [
        {
            "entry_id": "existing",
            "title": "Attempted overwrite",
            "doi": "10.1234/EXISTING",
            "reason": "existing_doi",
        },
        {
            "entry_id": "new-duplicate",
            "title": "Duplicate in file",
            "doi": "10.1234/NEW",
            "reason": "duplicate_doi_in_file",
        },
    ]
    assert [source["title"] for source in sources.json()] == ["Existing source", "New source"]


@pytest.mark.anyio
async def test_invalid_bibliography_entry_saves_nothing() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    bibliography = json.dumps(
        [
            {"id": "valid", "type": "book", "title": "Would otherwise be saved"},
            {"id": "missing-title", "type": "article-journal"},
        ]
    ).encode()
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": ("sources.json", bibliography, "application/json")},
        )
        sources = await client.get("/api/sources")

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == {
        "code": "invalid_bibliography_entry",
        "message": 'Source "missing-title" is invalid: A source title is required',
    }
    assert sources.json() == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "content", "status_code", "code"),
    [
        ("sources.csv", b"title,author", 415, "unsupported_bibliography"),
        ("sources.bib", b"", 422, "empty_bibliography"),
        ("sources.json", b"not json", 422, "malformed_bibliography"),
    ],
)
async def test_unusable_bibliography_files_report_specific_errors(
    filename: str,
    content: bytes,
    status_code: int,
    code: str,
) -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": (filename, content, "application/octet-stream")},
        )
        sources = await client.get("/api/sources")

    assert rejected.status_code == status_code
    assert rejected.json()["detail"]["code"] == code
    assert sources.json() == []


@pytest.mark.anyio
async def test_oversized_bibliography_is_rejected_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "MAX_BIBLIOGRAPHY_BYTES", 10)
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": ("sources.json", b"[" + (b" " * 10), "application/json")},
        )
        sources = await client.get("/api/sources")

    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "oversized_bibliography"
    assert rejected.json()["detail"]["maximum_byte_size"] == 10
    assert sources.json() == []


@pytest.mark.anyio
async def test_bibliography_entry_limit_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bibliographies, "MAX_BIBLIOGRAPHY_ENTRIES", 1)
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    bibliography = json.dumps(
        [
            {"id": "first", "type": "book", "title": "First"},
            {"id": "second", "type": "book", "title": "Second"},
        ]
    ).encode()
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/bibliography-imports",
            files={"bibliography": ("sources.json", bibliography, "application/json")},
        )
        sources = await client.get("/api/sources")

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "bibliography_entry_limit"
    assert sources.json() == []


@pytest.mark.anyio
async def test_source_can_be_deleted_with_its_attachment_through_the_api(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    application = create_app(Database.from_library(paths))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Source to delete"},
            files={"document": ("paper.pdf", b"saved original", "application/pdf")},
        )
        source_id = imported.json()["source"]["id"]
        removed = await client.delete(f"/api/sources/{source_id}")
        reopened = await client.get(f"/api/sources/{source_id}")
        sources = await client.get("/api/sources")

    assert removed.status_code == 204
    assert removed.content == b""
    assert reopened.status_code == 404
    assert sources.json() == []
    assert not [path for path in paths.attachments.rglob("*") if path.is_file()]
    assert not list(paths.temporary_imports.iterdir())


@pytest.mark.anyio
async def test_manually_captured_source_can_be_deleted_without_managed_files(
    tmp_path: Path,
) -> None:
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={"source_type": "book", "title": "Manual source"},
        )
        removed = await client.delete(f"/api/sources/{created.json()['id']}")
        sources = await client.get("/api/sources")

    assert removed.status_code == 204
    assert sources.json() == []


@pytest.mark.anyio
async def test_unknown_source_cannot_be_deleted(tmp_path: Path) -> None:
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        removed = await client.delete("/api/sources/999")

    assert removed.status_code == 404
    assert removed.json()["detail"] == "Source not found"


@pytest.mark.anyio
async def test_source_cleanup_failure_reports_that_the_database_delete_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    application = create_app(Database.from_library(paths))
    transport = httpx2.ASGITransport(app=application)
    original_unlink = Path.unlink

    def fail_staged_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == "original" and path.parent.name.startswith("removal-"):
            raise OSError("simulated cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_staged_cleanup)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Source to delete"},
            files={"document": ("paper.pdf", b"saved original", "application/pdf")},
        )
        source_id = imported.json()["source"]["id"]
        removed = await client.delete(f"/api/sources/{source_id}")
        reopened = await client.get(f"/api/sources/{source_id}")

    assert removed.status_code == 500
    assert removed.json()["detail"] == {
        "code": "source_cleanup_incomplete",
        "message": "The source was removed, but temporary file cleanup did not finish.",
    }
    assert reopened.status_code == 404
    assert [path for path in paths.temporary_imports.rglob("*") if path.is_file()]


@pytest.mark.anyio
async def test_unknown_source_type_is_rejected_without_saving() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/sources",
            json={"source_type": "podcast", "title": "A useful episode"},
        )
        sources = await client.get("/api/sources")

    assert rejected.status_code == 422
    assert sources.json() == []


@pytest.mark.anyio
async def test_duplicate_doi_returns_a_conflict() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "First paper",
                "doi": " 10.1234/example ",
            },
        )
        duplicate = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "Second paper",
                "doi": "10.1234/example",
            },
        )
        sources = await client.get("/api/sources")

    assert created.status_code == 201
    assert created.json()["doi"] == "10.1234/example"
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "A source with this DOI already exists."
    assert [source["title"] for source in sources.json()] == ["First paper"]


@pytest.mark.anyio
async def test_source_metadata_can_be_updated_and_reopened() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "Initial title"},
        )
        source_id = created.json()["id"]
        updated = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                source_type="book",
                title="  Updated source  ",
                authors=["  Jane Researcher  ", "Research Collective"],
                venue="  Evidence Press  ",
                doi="  10.1234/updated  ",
                abstract="  A concise abstract.  ",
                language="  en  ",
                tags=["  Methods  ", "LOCAL   AI", "methods"],
                collections=[" Thesis ", "Chapter   One"],
                identifiers=[
                    {"identifier_type": " PMID ", "value": " 12345 "},
                    {"identifier_type": "pmid", "value": "12345"},
                    {"identifier_type": "Custom-ID", "value": "Alpha-1"},
                ],
            ),
        )
        reopened = await client.get(f"/api/sources/{source_id}")
        sources = await client.get("/api/sources")

    assert updated.status_code == 200
    assert updated.json() == reopened.json()
    assert updated.json()["source_type"] == "book"
    assert updated.json()["title"] == "Updated source"
    assert updated.json()["authors"] == ["Jane Researcher", "Research Collective"]
    assert updated.json()["publication_year"] == 2025
    assert updated.json()["venue"] == "Evidence Press"
    assert updated.json()["doi"] == "10.1234/updated"
    assert updated.json()["url"] == "https://example.org/paper"
    assert updated.json()["abstract"] == "A concise abstract."
    assert updated.json()["language"] == "en"
    assert updated.json()["reading_status"] == "reading"
    assert updated.json()["tags"] == ["LOCAL AI", "Methods"]
    assert updated.json()["collections"] == ["Chapter One", "Thesis"]
    assert updated.json()["identifiers"] == [
        {"identifier_type": "custom-id", "value": "Alpha-1"},
        {"identifier_type": "pmid", "value": "12345"},
    ]
    assert updated.json()["citation_keys"] == []
    assert updated.json()["attachments"] == []
    assert sources.json()[0] == {
        key: value
        for key, value in updated.json().items()
        if key not in {"attachments", "metadata_provenance"}
    }


@pytest.mark.anyio
async def test_source_metadata_update_reuses_unchanged_identifier_records() -> None:
    database = Database.in_memory()
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "Original"},
        )
        source_id = created.json()["id"]
        first_update = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                identifiers=[{"identifier_type": "pmid", "value": "12345"}],
            ),
        )
        second_update = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                title="Revised",
                identifiers=[{"identifier_type": "pmid", "value": "12345"}],
            ),
        )

    assert first_update.status_code == 200
    assert second_update.status_code == 200
    assert second_update.json()["title"] == "Revised"
    assert second_update.json()["identifiers"] == [{"identifier_type": "pmid", "value": "12345"}]
    with database.session() as session:
        assert session.query(SourceIdentifierRecord).count() == 1


@pytest.mark.anyio
async def test_unvalidated_imported_isbn_does_not_block_an_unrelated_source_edit() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    bibliography = json.dumps(
        {
            "id": "legacy-book",
            "type": "book",
            "title": "Original title",
            "ISBN": "legacy-isbn-value",
        }
    )
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/bibliography-imports",
            files={
                "bibliography": (
                    "legacy.json",
                    bibliography,
                    "application/json",
                )
            },
        )
        source = imported.json()["imported"][0]
        updated = await client.put(
            f"/api/sources/{source['id']}",
            json=source_update_payload(
                source_type="book",
                title="Corrected title",
                doi=None,
                identifiers=source["identifiers"],
            ),
        )

    assert imported.status_code == 200
    assert source["identifiers"] == [{"identifier_type": "isbn", "value": "legacy-isbn-value"}]
    assert updated.status_code == 200
    assert updated.json()["title"] == "Corrected title"
    assert updated.json()["identifiers"] == source["identifiers"]


@pytest.mark.anyio
async def test_doi_metadata_preview_is_normalized_deterministic_and_read_only() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        return doi_metadata(
            doi="10.1234/Example",
            title="Changed provider title" if len(provider_calls) == 3 else "Crossref title",
        )

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": " https://doi.org/10.1234/Example "},
        )
        second = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "doi:10.1234/Example"},
        )
        changed = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/Example"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    preview = first.json()
    assert preview["kind"] == "proposal"
    assert preview["normalized_doi"] == "10.1234/Example"
    assert preview["provider"] == "Crossref"
    assert preview["provider_url"] == "https://api.crossref.org/works/10.1234%2FExample"
    assert preview["retrieved_doi"] == "10.1234/Example"
    assert len(preview["proposal_fingerprint"]) == 64
    assert preview["proposal_fingerprint"] == second.json()["proposal_fingerprint"]
    assert preview["proposal_fingerprint"] != changed.json()["proposal_fingerprint"]
    assert preview["proposal"] == {
        "source_type": "paper",
        "title": "Crossref title",
        "authors": ["Ada Lovelace", "Research Collective"],
        "publication_year": 2024,
        "venue": "Crossref Journal",
        "url": "https://doi.org/10.1234/example",
        "abstract": "Crossref abstract.",
        "language": "en",
        "identifiers": [
            {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
            {"identifier_type": "issn", "value": "2049-3630"},
        ],
    }
    assert preview["available_fields"] == [
        "source_type",
        "title",
        "authors",
        "publication_year",
        "venue",
        "url",
        "abstract",
        "language",
        "identifiers",
    ]
    assert provider_calls == ["10.1234/Example", "10.1234/Example", "10.1234/Example"]
    with database.session() as session:
        assert session.query(SourceMetadataLookupRecord).count() == 0
        assert session.query(SourceRecord).count() == 0


@pytest.mark.anyio
async def test_doi_metadata_preview_returns_a_canonical_duplicate_without_provider_call() -> None:
    def unexpected_provider(_doi: str) -> RetrievedMetadata:
        raise AssertionError("The provider must not be contacted for a saved DOI")

    database = Database.in_memory()
    application = create_app(database, doi_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "book",
                "title": "Existing source",
                "doi": "10.1234/Existing",
            },
        )
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "https://doi.org/10.1234/existing"},
        )

    assert preview.status_code == 200
    assert preview.json() == {
        "kind": "existing_source",
        "normalized_doi": "10.1234/existing",
        "existing_source": {
            "id": created.json()["id"],
            "source_type": "book",
            "title": "Existing source",
            "doi": "10.1234/Existing",
        },
    }
    with database.session() as session:
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "doi",
    ["", "not-a-doi", "10./missing-prefix", "10.1234/", "10.1234/has space"],
)
async def test_doi_metadata_preview_rejects_unusable_input_before_provider_call(doi: str) -> None:
    def unexpected_provider(_doi: str) -> RetrievedMetadata:
        raise AssertionError("Invalid DOI input must be rejected before contacting the provider")

    database = Database.in_memory()
    application = create_app(database, doi_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post("/api/doi-metadata-previews", json={"doi": doi})

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_doi"
    with database.session() as session:
        assert session.query(SourceMetadataLookupRecord).count() == 0
        assert session.query(SourceRecord).count() == 0


@pytest.mark.anyio
async def test_source_is_created_from_reviewed_doi_metadata_with_selected_fields() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        return doi_metadata(
            identifiers=[
                MetadataIdentifier(identifier_type="isbn", value="978-0-306-40615-7"),
                MetadataIdentifier(identifier_type="ISBN", value="978-0-306-40615-7"),
                MetadataIdentifier(identifier_type="issn", value="2049-3630"),
            ]
        )

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "https://doi.org/10.1234/example"},
        )
        reviewed = preview.json()
        created = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": reviewed["normalized_doi"],
                "proposal_fingerprint": reviewed["proposal_fingerprint"],
                "fields": ["identifiers", "authors", "title", "source_type"],
            },
        )
        reopened = await client.get(f"/api/sources/{created.json()['id']}")

    assert preview.status_code == 200
    assert created.status_code == 201
    source = created.json()
    assert reopened.json() == source
    assert source["source_type"] == "paper"
    assert source["title"] == "Crossref title"
    assert source["authors"] == ["Ada Lovelace", "Research Collective"]
    assert source["doi"] == "10.1234/example"
    assert source["publication_year"] is None
    assert source["venue"] is None
    assert source["url"] is None
    assert source["abstract"] is None
    assert source["language"] is None
    assert source["identifiers"] == [
        {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
        {"identifier_type": "issn", "value": "2049-3630"},
    ]
    assert source["metadata_provenance"][0] == {
        "lookup_id": source["metadata_provenance"][0]["lookup_id"],
        "provider": "Crossref",
        "provider_url": "https://api.crossref.org/works/10.1234%2Fexample",
        "identifier_type": "doi",
        "requested_identifier": "10.1234/example",
        "retrieved_identifier": "10.1234/example",
        "retrieved_at": source["metadata_provenance"][0]["retrieved_at"],
        "applied_fields": ["source_type", "title", "authors", "identifiers"],
        "applied_at": source["metadata_provenance"][0]["applied_at"],
    }
    assert provider_calls == ["10.1234/example", "10.1234/example"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 1
        lookup = session.query(SourceMetadataLookupRecord).one()
        assert lookup.source_id == source["id"]
        assert lookup.reviewed_metadata == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("bibliography_format", "filename"),
    [
        ("bibtex", "library.bib"),
        ("ris", "library.ris"),
        ("csl-json", "library.json"),
    ],
)
async def test_doi_first_capture_round_trips_through_bibliography_boundaries(
    bibliography_format: str,
    filename: str,
) -> None:
    source_application = create_app(
        Database.in_memory(),
        doi_metadata_provider=lambda _doi: doi_metadata(),
    )
    source_transport = httpx2.ASGITransport(app=source_application)
    async with (
        source_application.router.lifespan_context(source_application),
        httpx2.AsyncClient(transport=source_transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "https://doi.org/10.1234/example"},
        )
        reviewed = preview.json()
        created = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": reviewed["normalized_doi"],
                "proposal_fingerprint": reviewed["proposal_fingerprint"],
                "fields": reviewed["available_fields"],
            },
        )
        reopened = await client.get(f"/api/sources/{created.json()['id']}")
        exported = await client.get(f"/api/bibliography-exports/{bibliography_format}")

    assert preview.status_code == 200
    assert created.status_code == 201
    assert reopened.status_code == 200
    assert reopened.json() == created.json()
    provenance = reopened.json()["metadata_provenance"]
    assert len(provenance) == 1
    assert provenance[0]["provider"] == "Crossref"
    assert provenance[0]["identifier_type"] == "doi"
    assert provenance[0]["requested_identifier"] == "10.1234/example"
    assert provenance[0]["retrieved_identifier"] == "10.1234/example"
    assert provenance[0]["applied_fields"] == reviewed["available_fields"]
    assert exported.status_code == 200

    destination_application = create_app(Database.in_memory())
    destination_transport = httpx2.ASGITransport(app=destination_application)
    async with (
        destination_application.router.lifespan_context(destination_application),
        httpx2.AsyncClient(transport=destination_transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/bibliography-imports",
            files={
                "bibliography": (
                    filename,
                    exported.content,
                    "application/octet-stream",
                )
            },
        )

    assert imported.status_code == 200
    imported_source = imported.json()["imported"][0]
    canonical_fields = (
        "source_type",
        "title",
        "authors",
        "publication_year",
        "venue",
        "doi",
        "url",
        "abstract",
        "language",
        "identifiers",
    )
    assert {field: imported_source[field] for field in canonical_fields} == {
        field: reopened.json()[field] for field in canonical_fields
    }


@pytest.mark.anyio
async def test_doi_source_creation_uses_other_when_provider_type_is_not_selected() -> None:
    database = Database.in_memory()
    application = create_app(database, doi_metadata_provider=lambda _doi: doi_metadata())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/example"},
        )
        created = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": " https://doi.org/10.1234/example ",
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title"],
            },
        )

    assert created.status_code == 201
    assert created.json()["source_type"] == "other"
    assert created.json()["doi"] == "10.1234/example"
    assert created.json()["authors"] == []
    assert created.json()["metadata_provenance"][0]["applied_fields"] == ["title"]


@pytest.mark.anyio
async def test_doi_source_creation_requires_the_reviewed_provider_title() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        return doi_metadata(title=None)

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/example"},
        )
        rejected = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": preview.json()["normalized_doi"],
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title"],
            },
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "doi_metadata_missing_title"
    assert provider_calls == ["10.1234/example", "10.1234/example"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_doi_source_creation_requires_title_selection_before_networking() -> None:
    def unexpected_provider(_doi: str) -> RetrievedMetadata:
        raise AssertionError("The provider must not be contacted without the required title field")

    database = Database.in_memory()
    application = create_app(database, doi_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": "10.1234/example",
                "proposal_fingerprint": "untrusted",
                "fields": ["authors"],
            },
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "doi_metadata_title_required"
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_doi_source_creation_returns_a_new_review_when_provider_data_changed() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        return doi_metadata(title="Original title" if len(provider_calls) == 1 else "Changed title")

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/example"},
        )
        rejected = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": preview.json()["normalized_doi"],
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title", "authors"],
            },
        )

    assert rejected.status_code == 409
    detail = rejected.json()["detail"]
    assert detail["code"] == "doi_metadata_changed"
    assert detail["preview"]["kind"] == "proposal"
    assert detail["preview"]["proposal"]["title"] == "Changed title"
    assert detail["preview"]["proposal_fingerprint"] != preview.json()["proposal_fingerprint"]
    assert provider_calls == ["10.1234/example", "10.1234/example"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_doi_source_creation_provider_failure_saves_nothing() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        if len(provider_calls) == 2:
            raise DoiMetadataUnavailableError("Crossref is temporarily unavailable.")
        return doi_metadata()

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/example"},
        )
        rejected = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": preview.json()["normalized_doi"],
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title"],
            },
        )

    assert rejected.status_code == 503
    assert rejected.json()["detail"] == {
        "code": "doi_metadata_unavailable",
        "message": "Crossref is temporarily unavailable.",
    }
    assert provider_calls == ["10.1234/example", "10.1234/example"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_doi_source_creation_returns_the_source_added_during_provider_refresh(
    tmp_path: Path,
) -> None:
    database = Database.from_path(tmp_path / "litrev.sqlite3")
    existing_source_ids: list[int] = []
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        if len(provider_calls) == 2:
            with database.session() as session:
                source = SourceRecord(
                    source_type=SourceType.BOOK.value,
                    title="Created during refresh",
                    doi="10.1234/EXAMPLE",
                )
                session.add(source)
                session.commit()
                existing_source_ids.append(source.id)
        return doi_metadata()

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/example"},
        )
        rejected = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": preview.json()["normalized_doi"],
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title"],
            },
        )

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "doi_already_exists",
        "message": "A source with this DOI already exists.",
        "existing_source": {
            "id": existing_source_ids[0],
            "source_type": "book",
            "title": "Created during refresh",
            "doi": "10.1234/EXAMPLE",
        },
    }
    assert provider_calls == ["10.1234/example", "10.1234/example"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 1
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_doi_source_creation_rolls_back_source_and_provenance_on_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.in_memory()
    application = create_app(database, doi_metadata_provider=lambda _doi: doi_metadata())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/example"},
        )
        original_session_factory = database.session

        def failing_session_factory():
            session = original_session_factory()

            def fail_commit() -> None:
                raise RuntimeError("simulated database failure")

            session.commit = fail_commit
            return session

        monkeypatch.setattr(database, "session", failing_session_factory)
        rejected = await client.post(
            "/api/sources/from-doi",
            json={
                "doi": preview.json()["normalized_doi"],
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["source_type", "title", "identifiers"],
            },
        )
        monkeypatch.setattr(database, "session", original_session_factory)

    assert rejected.status_code == 500
    assert rejected.json()["detail"] == {
        "code": "doi_source_creation_failed",
        "message": "The source could not be saved; no source or provenance was added.",
    }
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("isbn", "code"),
    [
        ("", "empty_isbn"),
        ("978-0-14-032872!", "malformed_isbn"),
        ("9770140328721", "unsupported_isbn_prefix"),
        ("9780140328722", "isbn_checksum"),
    ],
)
async def test_isbn_preview_rejects_invalid_input_before_provider_call(
    isbn: str,
    code: str,
) -> None:
    def unexpected_provider(_isbn: str) -> RetrievedMetadata:
        raise AssertionError("Invalid ISBN input must be rejected before networking")

    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post("/api/isbn-metadata-previews", json={"isbn": isbn})

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == code
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_isbn_preview_returns_all_canonical_local_matches_without_networking() -> None:
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata()

    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post(
            "/api/sources",
            json={"source_type": "book", "title": "Saved ISBN-10 edition"},
        )
        await client.put(
            f"/api/sources/{first.json()['id']}",
            json=source_update_payload(
                source_type="book",
                title="Saved ISBN-10 edition",
                authors=[],
                publication_year=None,
                venue=None,
                doi=None,
                url=None,
                abstract=None,
                language=None,
                reading_status="unread",
                identifiers=[{"identifier_type": "isbn", "value": "0-14-032872-6"}],
            ),
        )
        second = await client.post(
            "/api/sources",
            json={"source_type": "book", "title": "Saved ISBN-13 edition"},
        )
        await client.put(
            f"/api/sources/{second.json()['id']}",
            json=source_update_payload(
                source_type="book",
                title="Saved ISBN-13 edition",
                authors=[],
                publication_year=None,
                venue=None,
                doi=None,
                url=None,
                abstract=None,
                language=None,
                reading_status="unread",
                identifiers=[{"identifier_type": "isbn", "value": "9780140328721"}],
            ),
        )

        local = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "978-0-14-032872-1"},
        )
        catalog = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "978-0-14-032872-1", "lookup_if_local_match": True},
        )

    assert local.status_code == 200
    assert local.json() == {
        "kind": "existing_sources",
        "input_isbn": "978-0-14-032872-1",
        "normalized_isbn": "9780140328721",
        "canonical_isbn13": "9780140328721",
        "existing_sources": [
            {
                "id": first.json()["id"],
                "source_type": "book",
                "title": "Saved ISBN-10 edition",
                "isbn_values": ["0-14-032872-6"],
            },
            {
                "id": second.json()["id"],
                "source_type": "book",
                "title": "Saved ISBN-13 edition",
                "isbn_values": ["9780140328721"],
            },
        ],
    }
    assert catalog.status_code == 200
    assert catalog.json()["kind"] == "proposal"
    assert provider_calls == ["9780140328721"]


@pytest.mark.anyio
async def test_isbn_preview_is_canonical_deterministic_and_read_only() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata()

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        isbn10 = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": " 0-14-032872-6 "},
        )
        isbn13 = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "9780140328721"},
        )

    assert isbn10.status_code == 200
    preview = isbn10.json()
    assert preview["kind"] == "proposal"
    assert preview["input_isbn"] == " 0-14-032872-6 "
    assert preview["normalized_isbn"] == "0140328726"
    assert preview["canonical_isbn13"] == "9780140328721"
    assert preview["retrieved_isbn"] == "9780140328721"
    assert preview["provider"] == "Open Library"
    assert preview["proposal"]["title"] == "Matilda"
    assert preview["proposal"]["source_type"] == "book"
    assert preview["proposal_fingerprint"] == isbn13.json()["proposal_fingerprint"]
    assert provider_calls == ["9780140328721", "9780140328721"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_book_is_created_from_reviewed_isbn_metadata_with_selected_fields() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata()

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "0-14-032872-6"},
        )
        reviewed = preview.json()
        created = await client.post(
            "/api/sources/from-isbn",
            json={
                "isbn": "0-14-032872-6",
                "proposal_fingerprint": reviewed["proposal_fingerprint"],
                "fields": ["identifiers", "authors", "title", "publication_year"],
            },
        )
        reopened = await client.get(f"/api/sources/{created.json()['id']}")

    assert created.status_code == 201
    source = created.json()
    assert reopened.json() == source
    assert source["source_type"] == "book"
    assert source["title"] == "Matilda"
    assert source["authors"] == ["Roald Dahl"]
    assert source["publication_year"] == 1988
    assert source["venue"] is None
    assert source["doi"] is None
    assert source["identifiers"] == [
        {"identifier_type": "isbn", "value": "0-14-032872-6"},
        {"identifier_type": "openlibrary", "value": "OL7353617M"},
    ]
    provenance = source["metadata_provenance"][0]
    assert provenance == {
        "lookup_id": provenance["lookup_id"],
        "provider": "Open Library",
        "provider_url": "https://openlibrary.org/books/OL7353617M",
        "identifier_type": "isbn",
        "requested_identifier": "9780140328721",
        "retrieved_identifier": "9780140328721",
        "retrieved_at": provenance["retrieved_at"],
        "applied_fields": ["title", "authors", "publication_year", "identifiers"],
        "applied_at": provenance["applied_at"],
    }
    assert provider_calls == ["9780140328721", "9780140328721"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("bibliography_format", "filename"),
    [
        ("bibtex", "library.bib"),
        ("ris", "library.ris"),
        ("csl-json", "library.json"),
    ],
)
async def test_isbn_first_capture_round_trips_through_bibliography_boundaries(
    bibliography_format: str,
    filename: str,
) -> None:
    source_application = create_app(
        Database.in_memory(),
        isbn_metadata_provider=lambda _isbn: isbn_metadata(),
    )
    source_transport = httpx2.ASGITransport(app=source_application)
    async with (
        source_application.router.lifespan_context(source_application),
        httpx2.AsyncClient(transport=source_transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "0-14-032872-6"},
        )
        reviewed = preview.json()
        created = await client.post(
            "/api/sources/from-isbn",
            json={
                "isbn": "0-14-032872-6",
                "proposal_fingerprint": reviewed["proposal_fingerprint"],
                "fields": reviewed["available_fields"],
            },
        )
        reopened = await client.get(f"/api/sources/{created.json()['id']}")
        exported = await client.get(f"/api/bibliography-exports/{bibliography_format}")

    assert preview.status_code == 200
    assert created.status_code == 201
    assert reopened.json() == created.json()
    assert reopened.json()["metadata_provenance"][0]["identifier_type"] == "isbn"
    assert exported.status_code == 200

    destination_application = create_app(Database.in_memory())
    destination_transport = httpx2.ASGITransport(app=destination_application)
    async with (
        destination_application.router.lifespan_context(destination_application),
        httpx2.AsyncClient(transport=destination_transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/bibliography-imports",
            files={
                "bibliography": (
                    filename,
                    exported.content,
                    "application/octet-stream",
                )
            },
        )

    assert imported.status_code == 200
    imported_source = imported.json()["imported"][0]
    canonical_fields = (
        "source_type",
        "title",
        "authors",
        "publication_year",
        "venue",
        "doi",
        "url",
        "abstract",
        "language",
        "identifiers",
    )
    assert {field: imported_source[field] for field in canonical_fields} == {
        field: reopened.json()[field] for field in canonical_fields
    }


@pytest.mark.anyio
async def test_isbn_source_creation_returns_a_new_review_when_catalog_data_changed() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata(title="Matilda" if len(provider_calls) == 1 else "Matilda revised")

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "9780140328721"},
        )
        rejected = await client.post(
            "/api/sources/from-isbn",
            json={
                "isbn": "9780140328721",
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title"],
            },
        )

    assert rejected.status_code == 409
    detail = rejected.json()["detail"]
    assert detail["code"] == "isbn_metadata_changed"
    assert detail["preview"]["proposal"]["title"] == "Matilda revised"
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_isbn_source_creation_requires_a_usable_catalog_title() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata(title=None)

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "9780140328721"},
        )
        rejected = await client.post(
            "/api/sources/from-isbn",
            json={
                "isbn": "9780140328721",
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title"],
            },
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "isbn_metadata_missing_title"
    assert provider_calls == ["9780140328721", "9780140328721"]
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "status_code", "code"),
    [
        (OpenLibraryMetadataNotFoundError("No catalog match."), 404, "isbn_metadata_not_found"),
        (OpenLibraryMetadataAmbiguousError("Multiple editions."), 409, "isbn_metadata_ambiguous"),
        (
            OpenLibraryMetadataRateLimitedError("Catalog rate limited."),
            429,
            "isbn_metadata_rate_limited",
        ),
        (
            OpenLibraryMetadataUnavailableError("Catalog unavailable."),
            503,
            "isbn_metadata_unavailable",
        ),
        (
            OpenLibraryMetadataMalformedError("Malformed catalog data."),
            502,
            "invalid_isbn_metadata",
        ),
        (
            OpenLibraryMetadataMismatchError("Wrong edition."),
            502,
            "invalid_isbn_metadata",
        ),
    ],
)
async def test_isbn_provider_failures_are_actionable_and_save_nothing(
    provider_error: Exception,
    status_code: int,
    code: str,
) -> None:
    def failed_provider(_isbn: str) -> RetrievedMetadata:
        raise provider_error

    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=failed_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "9780140328721"},
        )

    assert rejected.status_code == status_code
    assert rejected.json()["detail"] == {"code": code, "message": str(provider_error)}
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_isbn_creation_requires_title_selection_before_networking() -> None:
    def unexpected_provider(_isbn: str) -> RetrievedMetadata:
        raise AssertionError("The provider must not be contacted without a selected title")

    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/sources/from-isbn",
            json={
                "isbn": "9780140328721",
                "proposal_fingerprint": "untrusted",
                "fields": ["authors"],
            },
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "isbn_metadata_title_required"


@pytest.mark.anyio
async def test_isbn_source_creation_rolls_back_source_and_provenance_on_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=lambda _isbn: isbn_metadata())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        preview = await client.post(
            "/api/isbn-metadata-previews",
            json={"isbn": "9780140328721"},
        )
        original_session_factory = database.session

        def failing_session_factory():
            session = original_session_factory()

            def fail_commit() -> None:
                raise RuntimeError("simulated database failure")

            session.commit = fail_commit
            return session

        monkeypatch.setattr(database, "session", failing_session_factory)
        rejected = await client.post(
            "/api/sources/from-isbn",
            json={
                "isbn": "9780140328721",
                "proposal_fingerprint": preview.json()["proposal_fingerprint"],
                "fields": ["title", "identifiers"],
            },
        )
        monkeypatch.setattr(database, "session", original_session_factory)

    assert rejected.status_code == 500
    assert rejected.json()["detail"]["code"] == "isbn_source_creation_failed"
    with database.session() as session:
        assert session.query(SourceRecord).count() == 0
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_saved_book_isbn_lookup_requires_an_explicit_saved_isbn_and_stores_a_review() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata()

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(
            client,
            identifiers=[
                {"identifier_type": "isbn", "value": "0-14-032872-6"},
                {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
            ],
        )
        assert provider_calls == []
        lookup = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "978-0-14-032872-1"},
        )
        unchanged = await client.get(f"/api/sources/{source['id']}")

    assert lookup.status_code == 200
    assert provider_calls == ["9780140328721"]
    review = lookup.json()
    assert review["provider"] == "Open Library"
    assert review["identifier_type"] == "isbn"
    assert review["requested_identifier"] == "9780140328721"
    assert review["retrieved_identifier"] == "9780140328721"
    assert review["conflicting_fields"] == [
        "title",
        "authors",
        "publication_year",
        "venue",
    ]
    assert unchanged.json()["title"] == "User book title"
    assert unchanged.json()["metadata_provenance"] == []
    with database.session() as session:
        stored = session.query(SourceMetadataLookupRecord).one()
        assert stored.applied_at is None
        assert stored.reviewed_metadata["title"] == "User book title"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("saved_identifiers", "requested_isbn", "code", "status_code"),
    [
        (
            [{"identifier_type": "isbn", "value": "legacy-invalid-isbn"}],
            "9780140328721",
            "missing_isbn",
            422,
        ),
        (
            [{"identifier_type": "isbn", "value": "9780306406157"}],
            "9780140328721",
            "isbn_not_saved",
            409,
        ),
    ],
)
async def test_saved_isbn_lookup_rejects_unsaved_identity_without_networking(
    saved_identifiers: list[dict[str, str]],
    requested_isbn: str,
    code: str,
    status_code: int,
) -> None:
    def unexpected_provider(_isbn: str) -> RetrievedMetadata:
        raise AssertionError("A missing or different saved ISBN must not contact Open Library")

    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(client, identifiers=saved_identifiers)
        rejected = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": requested_isbn},
        )

    assert rejected.status_code == status_code
    assert rejected.json()["detail"]["code"] == code
    with database.session() as session:
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_selected_saved_isbn_metadata_is_revalidated_and_applied() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata()

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(client)
        lookup = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "0-14-032872-6"},
        )
        applied = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title", "authors", "identifiers"]},
        )
        reopened = await client.get(f"/api/sources/{source['id']}")

    assert applied.status_code == 200
    assert provider_calls == ["9780140328721", "9780140328721"]
    assert applied.json()["title"] == "Matilda"
    assert applied.json()["authors"] == ["Roald Dahl"]
    assert applied.json()["publication_year"] == 1980
    assert applied.json()["identifiers"] == [
        {"identifier_type": "isbn", "value": "0-14-032872-6"},
        {"identifier_type": "openlibrary", "value": "OL7353617M"},
    ]
    provenance = applied.json()["metadata_provenance"][0]
    assert provenance["identifier_type"] == "isbn"
    assert provenance["requested_identifier"] == "9780140328721"
    assert provenance["applied_fields"] == ["title", "authors", "identifiers"]
    assert reopened.json() == applied.json()


@pytest.mark.anyio
async def test_saved_isbn_apply_returns_fresh_review_when_catalog_data_changed() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata(
            title="Original catalog title" if len(provider_calls) == 1 else "Updated catalog title"
        )

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(client)
        original = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "9780140328721"},
        )
        changed = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{original.json()['id']}/apply",
            json={"fields": ["title"]},
        )
        changed_review = changed.json()["detail"]["lookup"]
        unchanged = await client.get(f"/api/sources/{source['id']}")
        applied = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{changed_review['id']}/apply",
            json={"fields": ["title"]},
        )

    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "isbn_metadata_changed"
    assert changed_review["proposal"]["title"] == "Updated catalog title"
    assert changed_review["conflicting_fields"] == [
        "title",
        "authors",
        "publication_year",
        "venue",
    ]
    assert unchanged.json()["title"] == "User book title"
    assert unchanged.json()["metadata_provenance"] == []
    assert applied.status_code == 200
    assert applied.json()["title"] == "Updated catalog title"
    assert provider_calls == ["9780140328721", "9780140328721", "9780140328721"]
    with database.session() as session:
        lookups = session.query(SourceMetadataLookupRecord).order_by(SourceMetadataLookupRecord.id)
        assert lookups.count() == 2
        assert lookups[0].applied_at is None
        assert lookups[1].applied_fields == ["title"]


@pytest.mark.anyio
async def test_saved_isbn_provider_failure_leaves_source_and_review_unchanged() -> None:
    database = Database.in_memory()
    provider_available = False

    def provider(_isbn: str) -> RetrievedMetadata:
        if not provider_available:
            raise OpenLibraryMetadataUnavailableError("Open Library could not be reached.")
        return isbn_metadata()

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(client)
        lookup_failed = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "9780140328721"},
        )
        with database.session() as session:
            assert session.query(SourceMetadataLookupRecord).count() == 0

        provider_available = True
        lookup = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "9780140328721"},
        )
        provider_available = False
        apply_failed = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title"]},
        )
        reopened = await client.get(f"/api/sources/{source['id']}")

    assert lookup_failed.status_code == 503
    assert lookup_failed.json()["detail"]["code"] == "isbn_metadata_unavailable"
    assert apply_failed.status_code == 503
    assert apply_failed.json()["detail"]["code"] == "isbn_metadata_unavailable"
    assert reopened.json()["title"] == "User book title"
    assert reopened.json()["metadata_provenance"] == []
    with database.session() as session:
        stored = session.query(SourceMetadataLookupRecord).one()
        assert stored.applied_at is None
        assert stored.applied_fields is None


@pytest.mark.anyio
async def test_saved_isbn_apply_rejects_source_metadata_or_isbn_changed_after_review() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(isbn: str) -> RetrievedMetadata:
        provider_calls.append(isbn)
        return isbn_metadata()

    application = create_app(database, isbn_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(client)
        title_lookup = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "9780140328721"},
        )
        edited = await client.put(
            f"/api/sources/{source['id']}",
            json=source_update_payload(
                source_type="book",
                title="User changed title",
                authors=["User Author"],
                publication_year=1980,
                venue="User Publisher",
                doi=None,
                url=None,
                abstract=None,
                language=None,
                reading_status="unread",
                identifiers=[{"identifier_type": "isbn", "value": "0-14-032872-6"}],
            ),
        )
        metadata_changed = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{title_lookup.json()['id']}/apply",
            json={"fields": ["title"]},
        )
        isbn_lookup = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "9780140328721"},
        )
        await client.put(
            f"/api/sources/{source['id']}",
            json=source_update_payload(
                source_type="book",
                title="User changed title",
                authors=["User Author"],
                publication_year=1980,
                venue="User Publisher",
                doi=None,
                url=None,
                abstract=None,
                language=None,
                reading_status="unread",
                identifiers=[{"identifier_type": "isbn", "value": "9780306406157"}],
            ),
        )
        isbn_changed = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{isbn_lookup.json()['id']}/apply",
            json={"fields": ["authors"]},
        )

    assert edited.status_code == 200
    assert metadata_changed.status_code == 409
    assert metadata_changed.json()["detail"] == {
        "code": "source_metadata_changed",
        "message": (
            "The source changed after this review. Look up the ISBN again before applying metadata."
        ),
        "fields": ["title"],
    }
    assert isbn_changed.status_code == 409
    assert isbn_changed.json()["detail"]["code"] == "source_isbn_changed"
    assert provider_calls == [
        "9780140328721",
        "9780140328721",
        "9780140328721",
    ]


@pytest.mark.anyio
async def test_saved_isbn_apply_rolls_back_source_and_provenance_on_commit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.in_memory()
    application = create_app(database, isbn_metadata_provider=lambda _isbn: isbn_metadata())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        source = await save_isbn_book(client)
        lookup = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups",
            json={"isbn": "9780140328721"},
        )
        original_session_factory = database.session

        def failing_session_factory():
            session = original_session_factory()

            def fail_commit() -> None:
                raise RuntimeError("simulated database failure")

            session.commit = fail_commit
            return session

        monkeypatch.setattr(database, "session", failing_session_factory)
        rejected = await client.post(
            f"/api/sources/{source['id']}/isbn-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title", "authors"]},
        )
        monkeypatch.setattr(database, "session", original_session_factory)
        reopened = await client.get(f"/api/sources/{source['id']}")

    assert rejected.status_code == 500
    assert rejected.json()["detail"]["code"] == "isbn_metadata_apply_failed"
    assert reopened.json()["title"] == "User book title"
    assert reopened.json()["authors"] == ["User Author"]
    assert reopened.json()["metadata_provenance"] == []
    with database.session() as session:
        stored = session.query(SourceMetadataLookupRecord).one()
        assert stored.applied_at is None
        assert stored.applied_fields is None


@pytest.mark.anyio
async def test_doi_metadata_is_reviewed_before_selected_fields_are_applied() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> RetrievedMetadata:
        provider_calls.append(doi)
        return doi_metadata()

    application = create_app(database, doi_metadata_provider=provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "Initial source",
                "doi": "10.1234/example",
            },
        )
        source_id = created.json()["id"]
        saved = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                title="User title",
                authors=["User Author"],
                publication_year=2020,
                venue="User Journal",
                doi="10.1234/example",
                url="https://example.org/user-copy",
                abstract="User abstract.",
                language="fr",
                identifiers=[{"identifier_type": "pmid", "value": "12345"}],
            ),
        )
        assert provider_calls == []

        lookup = await client.post(f"/api/sources/{source_id}/doi-metadata-lookups")
        unchanged = await client.get(f"/api/sources/{source_id}")
        applied = await client.post(
            f"/api/sources/{source_id}/doi-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title", "authors", "identifiers"]},
        )
        reapplied = await client.post(
            f"/api/sources/{source_id}/doi-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title"]},
        )
        reopened = await client.get(f"/api/sources/{source_id}")

    assert lookup.status_code == 200
    assert provider_calls == ["10.1234/example"]
    assert lookup.json()["provider"] == "Crossref"
    assert lookup.json()["provider_url"] == ("https://api.crossref.org/works/10.1234%2Fexample")
    assert lookup.json()["proposal"] == {
        "source_type": "paper",
        "title": "Crossref title",
        "authors": ["Ada Lovelace", "Research Collective"],
        "publication_year": 2024,
        "venue": "Crossref Journal",
        "url": "https://doi.org/10.1234/example",
        "abstract": "Crossref abstract.",
        "language": "en",
        "identifiers": [
            {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
            {"identifier_type": "issn", "value": "2049-3630"},
        ],
    }
    assert lookup.json()["available_fields"] == [
        "source_type",
        "title",
        "authors",
        "publication_year",
        "venue",
        "url",
        "abstract",
        "language",
        "identifiers",
    ]
    assert lookup.json()["conflicting_fields"] == [
        "title",
        "authors",
        "publication_year",
        "venue",
        "url",
        "abstract",
        "language",
    ]
    assert unchanged.json() == saved.json()
    assert unchanged.json()["metadata_provenance"] == []

    assert applied.status_code == 200
    assert applied.json()["title"] == "Crossref title"
    assert applied.json()["authors"] == ["Ada Lovelace", "Research Collective"]
    assert applied.json()["publication_year"] == 2020
    assert applied.json()["venue"] == "User Journal"
    assert applied.json()["abstract"] == "User abstract."
    assert applied.json()["identifiers"] == [
        {"identifier_type": "isbn", "value": "978-0-306-40615-7"},
        {"identifier_type": "issn", "value": "2049-3630"},
        {"identifier_type": "pmid", "value": "12345"},
    ]
    assert applied.json()["metadata_provenance"][0] == {
        "lookup_id": lookup.json()["id"],
        "provider": "Crossref",
        "provider_url": "https://api.crossref.org/works/10.1234%2Fexample",
        "identifier_type": "doi",
        "requested_identifier": "10.1234/example",
        "retrieved_identifier": "10.1234/example",
        "retrieved_at": lookup.json()["retrieved_at"],
        "applied_fields": ["title", "authors", "identifiers"],
        "applied_at": applied.json()["metadata_provenance"][0]["applied_at"],
    }
    assert reopened.json() == applied.json()
    assert reapplied.status_code == 409
    assert reapplied.json()["detail"]["code"] == "doi_metadata_already_applied"
    with database.session() as session:
        lookup_record = session.query(SourceMetadataLookupRecord).one()
        assert lookup_record.reviewed_metadata["title"] == "User title"
        assert lookup_record.proposed_metadata["title"] == "Crossref title"
        assert lookup_record.applied_fields == ["title", "authors", "identifiers"]


@pytest.mark.anyio
async def test_doi_metadata_apply_rejects_fields_changed_after_review() -> None:
    application = create_app(
        Database.in_memory(),
        doi_metadata_provider=lambda _doi: doi_metadata(),
    )
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "Original title",
                "doi": "10.1234/example",
            },
        )
        source_id = created.json()["id"]
        lookup = await client.post(f"/api/sources/{source_id}/doi-metadata-lookups")
        edited = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                title="New user edit",
                doi="10.1234/example",
            ),
        )
        rejected = await client.post(
            f"/api/sources/{source_id}/doi-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title"]},
        )
        reopened = await client.get(f"/api/sources/{source_id}")

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "source_metadata_changed",
        "message": (
            "The source changed after this review. Look up the DOI again before applying metadata."
        ),
        "fields": ["title"],
    }
    assert reopened.json() == edited.json()
    assert reopened.json()["title"] == "New user edit"
    assert reopened.json()["metadata_provenance"] == []


@pytest.mark.anyio
async def test_doi_metadata_apply_rejects_a_review_for_the_sources_previous_doi() -> None:
    database = Database.in_memory()
    application = create_app(
        database,
        doi_metadata_provider=lambda _doi: doi_metadata(doi="10.1234/first"),
    )
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "Original title",
                "doi": "10.1234/first",
            },
        )
        source_id = created.json()["id"]
        lookup = await client.post(f"/api/sources/{source_id}/doi-metadata-lookups")
        edited = await client.put(
            f"/api/sources/{source_id}",
            json=source_update_payload(
                title="Original title",
                authors=[],
                publication_year=None,
                venue=None,
                doi="10.1234/second",
                url=None,
                abstract=None,
                language=None,
                reading_status="unread",
            ),
        )
        rejected = await client.post(
            f"/api/sources/{source_id}/doi-metadata-lookups/{lookup.json()['id']}/apply",
            json={"fields": ["title"]},
        )
        reopened = await client.get(f"/api/sources/{source_id}")

    assert lookup.status_code == 200
    assert edited.status_code == 200
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "source_doi_changed",
        "message": (
            "The source DOI changed after this review. Look up the DOI again before applying "
            "metadata."
        ),
    }
    assert reopened.json() == edited.json()
    assert reopened.json()["doi"] == "10.1234/second"
    assert reopened.json()["metadata_provenance"] == []
    with database.session() as session:
        lookup_record = session.query(SourceMetadataLookupRecord).one()
        assert lookup_record.applied_fields is None
        assert lookup_record.applied_at is None


@pytest.mark.anyio
async def test_doi_metadata_lookup_requires_a_saved_doi_without_calling_the_provider() -> None:
    def unexpected_provider(_doi: str) -> RetrievedMetadata:
        raise AssertionError("The provider must not be contacted without a DOI")

    application = create_app(Database.in_memory(), doi_metadata_provider=unexpected_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "No DOI"},
        )
        rejected = await client.post(f"/api/sources/{created.json()['id']}/doi-metadata-lookups")

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == {
        "code": "missing_doi",
        "message": "Add a DOI to this source before looking up metadata.",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_error", "status_code", "code"),
    [
        (DoiMetadataNotFoundError("No record."), 404, "doi_metadata_not_found"),
        (
            DoiMetadataRateLimitedError("Try later."),
            429,
            "doi_metadata_rate_limited",
        ),
        (
            DoiMetadataUnavailableError("Crossref did not respond before the timeout."),
            503,
            "doi_metadata_unavailable",
        ),
        (
            DoiMetadataUnavailableError("Offline."),
            503,
            "doi_metadata_unavailable",
        ),
        (DoiMetadataMalformedError("Bad record."), 502, "invalid_doi_metadata"),
    ],
)
async def test_doi_metadata_provider_failures_are_actionable_and_save_nothing(
    provider_error: Exception,
    status_code: int,
    code: str,
) -> None:
    def failed_provider(_doi: str) -> RetrievedMetadata:
        raise provider_error

    database = Database.in_memory()
    application = create_app(database, doi_metadata_provider=failed_provider)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={
                "source_type": "paper",
                "title": "Lookup source",
                "doi": "10.1234/example",
            },
        )
        rejected = await client.post(f"/api/sources/{created.json()['id']}/doi-metadata-lookups")
        preview_rejected = await client.post(
            "/api/doi-metadata-previews",
            json={"doi": "10.1234/preview"},
        )

    assert rejected.status_code == status_code
    assert rejected.json()["detail"]["code"] == code
    assert rejected.json()["detail"]["message"] == str(provider_error)
    assert preview_rejected.status_code == status_code
    assert preview_rejected.json()["detail"] == rejected.json()["detail"]
    with database.session() as session:
        assert session.query(SourceMetadataLookupRecord).count() == 0


@pytest.mark.anyio
async def test_duplicate_doi_update_is_rejected_without_partial_changes() -> None:
    database = Database.in_memory()
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "First", "doi": "10.1234/existing"},
        )
        second = await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "Second"},
        )
        rejected = await client.put(
            f"/api/sources/{second.json()['id']}",
            json=source_update_payload(
                title="Changed",
                doi="10.1234/existing",
                tags=["Should roll back"],
                collections=["Also rolled back"],
                identifiers=[{"identifier_type": "pmid", "value": "12345"}],
            ),
        )
        reopened = await client.get(f"/api/sources/{second.json()['id']}")

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "A source with this DOI already exists."
    assert reopened.json()["title"] == "Second"
    assert reopened.json()["authors"] == []
    assert reopened.json()["doi"] is None
    assert reopened.json()["tags"] == []
    assert reopened.json()["collections"] == []
    assert reopened.json()["identifiers"] == []
    with database.session() as session:
        assert session.query(TagRecord).count() == 0
        assert session.query(CollectionRecord).count() == 0
        assert session.query(SourceIdentifierRecord).count() == 0


@pytest.mark.anyio
async def test_source_organization_names_are_reused_and_assignments_can_be_replaced() -> None:
    database = Database.in_memory()
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "First source"},
        )
        second = await client.post(
            "/api/sources",
            json={"source_type": "book", "title": "Second source"},
        )
        organized = await client.put(
            f"/api/sources/{first.json()['id']}",
            json=source_update_payload(
                title="First source",
                tags=["  Methods ", "LOCAL   AI", "methods"],
                collections=[" Thesis ", "Chapter   One"],
            ),
        )
        reused = await client.put(
            f"/api/sources/{second.json()['id']}",
            json=source_update_payload(
                source_type="book",
                title="Second source",
                doi=None,
                tags=["methods"],
            ),
        )
        replaced = await client.put(
            f"/api/sources/{first.json()['id']}",
            json=source_update_payload(
                title="First source",
                tags=["Evidence"],
                collections=[],
            ),
        )
        sources = await client.get("/api/sources")

    assert organized.status_code == 200
    assert organized.json()["tags"] == ["LOCAL AI", "Methods"]
    assert organized.json()["collections"] == ["Chapter One", "Thesis"]
    assert reused.status_code == 200
    assert reused.json()["tags"] == ["Methods"]
    assert replaced.json()["tags"] == ["Evidence"]
    assert replaced.json()["collections"] == []
    assert sources.json()[0]["tags"] == ["Evidence"]
    assert sources.json()[1]["tags"] == ["Methods"]
    with database.session() as session:
        assert session.query(TagRecord).count() == 3
        assert session.query(CollectionRecord).count() == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"authors": [" "]}, "Author names cannot be empty"),
        ({"publication_year": 0}, "Publication years must be between 1 and 9999"),
        ({"url": "javascript:alert(1)"}, "URLs must use http or https"),
        ({"tags": [" "]}, "Tag names cannot be empty"),
        ({"collections": ["x" * 101]}, "Collection names are limited to 100 characters"),
        ({"tags": [f"tag-{index}" for index in range(51)]}, "Sources are limited to 50 tags"),
        (
            {"identifiers": [{"identifier_type": "not a type", "value": "123"}]},
            "Identifier types may contain letters, numbers, periods, hyphens, and underscores",
        ),
        (
            {"identifiers": [{"identifier_type": "pmid", "value": " "}]},
            "Identifier values cannot be empty",
        ),
        (
            {"identifiers": [{"identifier_type": "doi", "value": "10.1234/example"}]},
            "Use the DOI field for DOI identifiers",
        ),
    ],
)
async def test_invalid_source_metadata_is_rejected(
    overrides: dict[str, object],
    message: str,
) -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        created = await client.post(
            "/api/sources",
            json={"source_type": "paper", "title": "Original"},
        )
        rejected = await client.put(
            f"/api/sources/{created.json()['id']}",
            json=source_update_payload(**overrides),
        )

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == message


@pytest.mark.anyio
async def test_updating_an_unknown_source_returns_not_found() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.put("/api/sources/999", json=source_update_payload())

    assert response.status_code == 404
    assert response.json()["detail"] == "Source not found"


@pytest.mark.anyio
async def test_import_can_be_converted_and_reopened(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "  A useful paper  "},
            files={"document": ("papers.csv", b"paper,year\nA useful paper,2026\n", "text/csv")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        source_id = imported.json()["source"]["id"]
        converted = await client.post(f"/api/attachments/{attachment_id}/convert")
        extracted = await client.get(f"/api/attachments/{attachment_id}/extracted-text")

    assert imported.status_code == 201
    assert imported.json()["source"]["title"] == "A useful paper"
    assert imported.json()["attachment"]["conversion_status"] == "pending"
    assert imported.json()["attachment"]["detected_format"] == "csv"
    assert imported.json()["attachment"]["can_remove"] is False
    assert converted.status_code == 200
    assert converted.json()["conversion_status"] == "succeeded"
    assert converted.json()["has_extracted_text"] is True
    assert converted.json()["can_remove"] is False
    assert "A useful paper" in extracted.json()["markdown"]

    database.engine.dispose()
    reopened_application = create_app(Database.from_library(paths))
    reopened_transport = httpx2.ASGITransport(app=reopened_application)
    async with (
        reopened_application.router.lifespan_context(reopened_application),
        httpx2.AsyncClient(
            transport=reopened_transport,
            base_url="http://test",
        ) as reopened_client,
    ):
        reopened = await reopened_client.get(f"/api/sources/{source_id}")

    assert reopened.status_code == 200
    assert reopened.json()["title"] == "A useful paper"
    assert reopened.json()["attachments"][0]["conversion_status"] == "succeeded"
    assert reopened.json()["attachments"][0]["has_extracted_text"] is True


@pytest.mark.anyio
async def test_reader_lists_and_streams_managed_pdfs(tmp_path: Path) -> None:
    pdf = b"%PDF-1.4\nreader fixture\n%%EOF\n"
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported_pdf = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Readable paper"},
            files={"document": ("reader.pdf", pdf, "application/pdf")},
        )
        imported_csv = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Tabular source"},
            files={"document": ("data.csv", b"title,year\nExample,2026\n", "text/csv")},
        )
        documents = await client.get("/api/reader/documents")
        attachment_id = imported_pdf.json()["attachment"]["id"]
        content = await client.get(f"/api/attachments/{attachment_id}/content")
        partial = await client.get(
            f"/api/attachments/{attachment_id}/content",
            headers={"Range": "bytes=0-3"},
        )
        rejected_csv = await client.get(
            f"/api/attachments/{imported_csv.json()['attachment']['id']}/content"
        )

    assert imported_pdf.status_code == 201
    assert imported_csv.status_code == 201
    assert documents.status_code == 200
    assert documents.json() == [
        {
            "attachment_id": attachment_id,
            "source_id": imported_pdf.json()["source"]["id"],
            "source_title": "Readable paper",
            "original_filename": "reader.pdf",
            "byte_size": len(pdf),
            "attachment_availability": "available",
            "reader_notes": [],
        }
    ]
    assert content.status_code == 200
    assert content.content == pdf
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["accept-ranges"] == "bytes"
    assert content.headers["cache-control"] == "no-store"
    assert content.headers["content-disposition"].startswith("inline;")
    assert partial.status_code == 206
    assert partial.content == b"%PDF"
    assert partial.headers["content-range"] == f"bytes 0-3/{len(pdf)}"
    assert rejected_csv.status_code == 415
    assert rejected_csv.json()["detail"] == {
        "code": "not_pdf",
        "message": "Only PDF attachments can be opened in Reader.",
    }


@pytest.mark.anyio
async def test_reader_preserves_a_pdf_record_when_its_managed_file_changed(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Changed paper"},
            files={
                "document": (
                    "changed.pdf",
                    b"%PDF-1.4\noriginal\n%%EOF\n",
                    "application/pdf",
                )
            },
        )
        attachment_id = imported.json()["attachment"]["id"]
        with database.session() as session:
            attachment = session.get(AttachmentRecord, attachment_id)
            assert attachment is not None
            managed_file = paths.root / attachment.managed_path
        managed_file.write_bytes(b"%PDF-1.4\nchanged\n%%EOF\n")

        rejected = await client.get(f"/api/attachments/{attachment_id}/content")
        reopened = await client.get(f"/api/sources/{imported.json()['source']['id']}")

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "managed_file_conflict",
        "message": "The saved PDF is missing or has changed.",
    }
    assert reopened.json()["attachments"][0]["id"] == attachment_id


@pytest.mark.anyio
async def test_reader_highlight_is_persisted_reopened_and_explicitly_deleted(
    tmp_path: Path,
) -> None:
    pdf = b"%PDF-1.4\nselectable reader fixture\n%%EOF\n"
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Highlighted paper"},
            files={"document": ("highlighted.pdf", pdf, "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        created = await client.post(
            f"/api/attachments/{attachment_id}/highlights",
            json={
                "page_number": 2,
                "selected_text": "Exact selected text",
                "rectangles": [
                    {"x": 0.125, "y": 0.25, "width": 0.5, "height": 0.04},
                    {"x": 0.125, "y": 0.3, "width": 0.2, "height": 0.04},
                ],
            },
        )
        content_after_creation = await client.get(f"/api/attachments/{attachment_id}/content")

    assert created.status_code == 201
    highlight = created.json()
    assert highlight["attachment_id"] == attachment_id
    assert highlight["source_id"] == imported.json()["source"]["id"]
    assert highlight["page_number"] == 2
    assert highlight["selected_text"] == "Exact selected text"
    assert highlight["rectangles"] == [
        {"x": 0.125, "y": 0.25, "width": 0.5, "height": 0.04},
        {"x": 0.125, "y": 0.3, "width": 0.2, "height": 0.04},
    ]
    assert content_after_creation.content == pdf

    database.engine.dispose()
    reopened_database = Database.from_library(paths)
    reopened_application = create_app(reopened_database)
    reopened_transport = httpx2.ASGITransport(app=reopened_application)
    async with (
        reopened_application.router.lifespan_context(reopened_application),
        httpx2.AsyncClient(
            transport=reopened_transport,
            base_url="http://test",
        ) as reopened_client,
    ):
        reopened = await reopened_client.get(f"/api/attachments/{attachment_id}/highlights")
        deleted = await reopened_client.delete(f"/api/highlights/{highlight['id']}")
        after_deletion = await reopened_client.get(f"/api/attachments/{attachment_id}/highlights")

    assert reopened.status_code == 200
    assert reopened.json() == [highlight]
    assert deleted.status_code == 204
    assert after_deletion.json() == []


@pytest.mark.anyio
async def test_reader_highlight_rejects_invalid_geometry_and_wrong_attachments(
    tmp_path: Path,
) -> None:
    database = Database.from_library(LibraryPaths.from_root(tmp_path / "library"))
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    valid_rectangle = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}
    invalid_payloads = [
        {"page_number": 0, "selected_text": "Text", "rectangles": [valid_rectangle]},
        {"page_number": 1, "selected_text": "   ", "rectangles": [valid_rectangle]},
        {"page_number": 1, "selected_text": "Text", "rectangles": []},
        {
            "page_number": 1,
            "selected_text": "Text",
            "rectangles": [{"x": 0.8, "y": 0.2, "width": 0.3, "height": 0.04}],
        },
        {
            "page_number": 1,
            "selected_text": "Text",
            "rectangles": [{"x": "NaN", "y": 0.2, "width": 0.3, "height": 0.04}],
        },
        {
            "page_number": 1,
            "selected_text": "x" * 10_001,
            "rectangles": [valid_rectangle],
        },
    ]
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported_pdf = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "PDF"},
            files={"document": ("reader.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        imported_csv = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "CSV"},
            files={"document": ("reader.csv", b"title\nExample\n", "text/csv")},
        )
        attachment_id = imported_pdf.json()["attachment"]["id"]
        rejected = [
            await client.post(
                f"/api/attachments/{attachment_id}/highlights",
                json=payload,
            )
            for payload in invalid_payloads
        ]
        wrong_type = await client.post(
            f"/api/attachments/{imported_csv.json()['attachment']['id']}/highlights",
            json={
                "page_number": 1,
                "selected_text": "Text",
                "rectangles": [valid_rectangle],
            },
        )
        missing = await client.get("/api/attachments/999/highlights")

    assert all(response.status_code == 422 for response in rejected)
    assert wrong_type.status_code == 415
    assert wrong_type.json()["detail"]["code"] == "not_pdf"
    assert missing.status_code == 404
    with database.session() as session:
        assert session.query(HighlightRecord).count() == 0


@pytest.mark.anyio
async def test_reader_highlight_commit_failure_leaves_no_false_saved_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_library(LibraryPaths.from_root(tmp_path / "library"))
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "PDF"},
            files={"document": ("reader.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        original_session_factory = database.session

        def failing_session_factory():
            session = original_session_factory()

            def fail_commit() -> None:
                raise RuntimeError("simulated database failure")

            session.commit = fail_commit
            return session

        monkeypatch.setattr(database, "session", failing_session_factory)
        rejected = await client.post(
            f"/api/attachments/{attachment_id}/highlights",
            json={
                "page_number": 1,
                "selected_text": "Unsaved text",
                "rectangles": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
            },
        )
        monkeypatch.setattr(database, "session", original_session_factory)

    assert rejected.status_code == 500
    assert rejected.json()["detail"] == {
        "code": "highlight_creation_failed",
        "message": "The highlight could not be saved; no highlight was added.",
    }
    with database.session() as session:
        assert session.query(HighlightRecord).count() == 0


@pytest.mark.anyio
async def test_reader_highlight_delete_failure_preserves_the_saved_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_library(LibraryPaths.from_root(tmp_path / "library"))
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "PDF"},
            files={"document": ("reader.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        created = await client.post(
            f"/api/attachments/{attachment_id}/highlights",
            json={
                "page_number": 1,
                "selected_text": "Saved text",
                "rectangles": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
            },
        )
        original_session_factory = database.session

        def failing_session_factory():
            session = original_session_factory()

            def fail_commit() -> None:
                raise RuntimeError("simulated database failure")

            session.commit = fail_commit
            return session

        monkeypatch.setattr(database, "session", failing_session_factory)
        rejected = await client.delete(f"/api/highlights/{created.json()['id']}")
        monkeypatch.setattr(database, "session", original_session_factory)

    assert rejected.status_code == 500
    assert rejected.json()["detail"] == {
        "code": "highlight_deletion_failed",
        "message": "The highlight could not be deleted and remains saved.",
    }
    with database.session() as session:
        assert session.query(HighlightRecord).one().selected_text == "Saved text"


@pytest.mark.anyio
async def test_saved_highlights_block_attachment_removal_but_follow_confirmed_source_deletion(
    tmp_path: Path,
) -> None:
    database = Database.from_library(LibraryPaths.from_root(tmp_path / "library"))
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Recoverable annotations"},
            files={"document": ("reader.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
        )
        source_id = imported.json()["source"]["id"]
        attachment_id = imported.json()["attachment"]["id"]
        with database.session() as session:
            attachment = session.get(AttachmentRecord, attachment_id)
            assert attachment is not None
            attachment.conversion_status = ConversionStatus.NEEDS_OCR.value
            session.commit()

        created = await client.post(
            f"/api/attachments/{attachment_id}/highlights",
            json={
                "page_number": 1,
                "selected_text": "Saved passage",
                "rectangles": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
            },
        )
        reopened = await client.get(f"/api/sources/{source_id}")
        blocked = await client.delete(f"/api/attachments/{attachment_id}")
        deleted_source = await client.delete(f"/api/sources/{source_id}")

    assert created.status_code == 201
    assert reopened.json()["attachments"][0]["can_remove"] is False
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        "code": "attachment_has_highlights",
        "message": "Remove the saved highlights before removing this attachment.",
    }
    assert deleted_source.status_code == 204
    with database.session() as session:
        assert session.query(HighlightRecord).count() == 0


@pytest.mark.anyio
async def test_reader_notes_create_edit_anchor_and_reopen_through_structured_locators(
    tmp_path: Path,
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Reader notes"},
            files={"document": ("notes.pdf", b"%PDF-1.4\nnotes\n%%EOF\n", "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        page_note = await client.post(
            f"/api/attachments/{attachment_id}/notes",
            json={"page_number": 2, "body": "  Manual page note\n"},
        )
        saved_highlight = await client.post(
            f"/api/attachments/{attachment_id}/highlights",
            json={
                "page_number": 3,
                "selected_text": "Existing quote",
                "rectangles": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
            },
        )
        anchored_note = await client.post(
            f"/api/attachments/{attachment_id}/notes",
            json={
                "page_number": 3,
                "body": "Note on an existing highlight",
                "highlight_id": saved_highlight.json()["id"],
            },
        )
        atomic_note = await client.post(
            f"/api/attachments/{attachment_id}/notes",
            json={
                "page_number": 4,
                "body": "Note and highlight together",
                "new_highlight": {
                    "selected_text": "New exact quote",
                    "rectangles": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.05}],
                },
            },
        )
        edited = await client.put(
            f"/api/notes/{page_note.json()['id']}",
            json={"body": "Edited manual page note"},
        )
        deleted_highlight = await client.delete(f"/api/highlights/{saved_highlight.json()['id']}")
        listed = await client.get(f"/api/attachments/{attachment_id}/notes")
        documents = await client.get("/api/reader/documents")

    assert page_note.status_code == 201
    assert page_note.json()["body"] == "  Manual page note\n"
    assert page_note.json()["page_number"] == 2
    assert page_note.json()["highlight"] is None
    assert anchored_note.status_code == 201
    assert anchored_note.json()["highlight"]["selected_text"] == "Existing quote"
    assert atomic_note.status_code == 201
    assert atomic_note.json()["highlight"]["selected_text"] == "New exact quote"
    assert edited.status_code == 200
    assert edited.json()["body"] == "Edited manual page note"
    assert edited.json()["page_number"] == 2
    assert deleted_highlight.status_code == 204
    assert [note["page_number"] for note in listed.json()] == [2, 3, 4]
    assert listed.json()[1]["body"] == "Note on an existing highlight"
    assert listed.json()[1]["highlight"] is None
    assert listed.json()[2]["highlight"]["selected_text"] == "New exact quote"
    assert documents.json()[0]["reader_notes"] == listed.json()

    database.engine.dispose()
    reopened_database = Database.from_library(paths)
    reopened_application = create_app(reopened_database)
    reopened_transport = httpx2.ASGITransport(app=reopened_application)
    async with (
        reopened_application.router.lifespan_context(reopened_application),
        httpx2.AsyncClient(
            transport=reopened_transport,
            base_url="http://test",
        ) as reopened_client,
    ):
        reopened = await reopened_client.get("/api/reader/documents")

    assert [note["page_number"] for note in reopened.json()[0]["reader_notes"]] == [2, 3, 4]
    with reopened_database.session() as session:
        notes = list(session.query(NoteRecord).order_by(NoteRecord.page_number))
        assert notes[0].locator is None
        assert notes[0].attachment_id == attachment_id
        assert notes[1].highlight_id is None
        assert notes[2].highlight_id is not None


@pytest.mark.anyio
async def test_reader_note_relationship_validation_and_atomic_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database.from_library(LibraryPaths.from_root(tmp_path / "library"))
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "First PDF"},
            files={"document": ("first.pdf", b"%PDF-1.4\nfirst\n%%EOF\n", "application/pdf")},
        )
        second = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Second PDF"},
            files={"document": ("second.pdf", b"%PDF-1.4\nsecond\n%%EOF\n", "application/pdf")},
        )
        csv = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "CSV"},
            files={"document": ("notes.csv", b"title\nExample\n", "text/csv")},
        )
        first_attachment_id = first.json()["attachment"]["id"]
        second_attachment_id = second.json()["attachment"]["id"]
        highlight = await client.post(
            f"/api/attachments/{first_attachment_id}/highlights",
            json={
                "page_number": 2,
                "selected_text": "First quote",
                "rectangles": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
            },
        )
        wrong_attachment = await client.post(
            f"/api/attachments/{second_attachment_id}/notes",
            json={
                "page_number": 2,
                "body": "Wrong attachment",
                "highlight_id": highlight.json()["id"],
            },
        )
        wrong_page = await client.post(
            f"/api/attachments/{first_attachment_id}/notes",
            json={
                "page_number": 3,
                "body": "Wrong page",
                "highlight_id": highlight.json()["id"],
            },
        )
        empty = await client.post(
            f"/api/attachments/{first_attachment_id}/notes",
            json={"page_number": 1, "body": "  "},
        )
        wrong_type = await client.post(
            f"/api/attachments/{csv.json()['attachment']['id']}/notes",
            json={"page_number": 1, "body": "Not a PDF note"},
        )
        valid_note = await client.post(
            f"/api/attachments/{first_attachment_id}/notes",
            json={"page_number": 1, "body": "Keep the original body"},
        )

        original_session_factory = database.session

        def failing_session_factory():
            session = original_session_factory()

            def fail_commit() -> None:
                raise RuntimeError("simulated database failure")

            session.commit = fail_commit
            return session

        monkeypatch.setattr(database, "session", failing_session_factory)
        failed_update = await client.put(
            f"/api/notes/{valid_note.json()['id']}",
            json={"body": "Must not replace the original"},
        )
        failed_atomic = await client.post(
            f"/api/attachments/{first_attachment_id}/notes",
            json={
                "page_number": 4,
                "body": "Must roll back",
                "new_highlight": {
                    "selected_text": "Unsaved quote",
                    "rectangles": [{"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.05}],
                },
            },
        )
        monkeypatch.setattr(database, "session", original_session_factory)

    assert wrong_attachment.status_code == 409
    assert wrong_page.status_code == 409
    assert empty.status_code == 422
    assert wrong_type.status_code == 415
    assert valid_note.status_code == 201
    assert failed_update.status_code == 500
    assert failed_atomic.status_code == 500
    assert failed_atomic.json()["detail"] == {
        "code": "reader_note_write_failed",
        "message": "The Reader note and any new highlight could not be saved.",
    }
    with database.session() as session:
        notes = session.query(NoteRecord).all()
        assert [note.body for note in notes] == ["Keep the original body"]
        assert session.query(HighlightRecord).count() == 1


@pytest.mark.anyio
async def test_reader_notes_block_attachment_removal_and_survive_an_unavailable_pdf(
    tmp_path: Path,
) -> None:
    pdf = b"%PDF-1.4\nrecoverable note\n%%EOF\n"
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Unavailable PDF"},
            files={"document": ("unavailable.pdf", pdf, "application/pdf")},
        )
        source_id = imported.json()["source"]["id"]
        attachment_id = imported.json()["attachment"]["id"]
        created = await client.post(
            f"/api/attachments/{attachment_id}/notes",
            json={"page_number": 5, "body": "Preserve this locator"},
        )
        with database.session() as session:
            attachment = session.get(AttachmentRecord, attachment_id)
            assert attachment is not None
            attachment.conversion_status = ConversionStatus.NEEDS_OCR.value
            managed_file = paths.root / attachment.managed_path
            session.commit()

        source = await client.get(f"/api/sources/{source_id}")
        blocked = await client.delete(f"/api/attachments/{attachment_id}")
        managed_file.write_bytes(b"%PDF-1.4\nchanged\n%%EOF\n")
        unavailable_documents = await client.get("/api/reader/documents")
        managed_file.write_bytes(pdf)
        deleted_source = await client.delete(f"/api/sources/{source_id}")

    assert created.status_code == 201
    assert source.json()["attachments"][0]["can_remove"] is False
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == {
        "code": "attachment_has_reader_notes",
        "message": (
            "This attachment has saved Reader notes and must be kept. Delete the source through "
            "its confirmed workflow only if you intend to remove those notes."
        ),
    }
    document = unavailable_documents.json()[0]
    assert document["attachment_availability"] == "missing_or_changed"
    assert document["reader_notes"][0]["body"] == "Preserve this locator"
    assert document["reader_notes"][0]["page_number"] == 5
    assert deleted_source.status_code == 204
    with database.session() as session:
        assert session.query(NoteRecord).count() == 0


@pytest.mark.anyio
async def test_reader_refuses_a_symlinked_managed_directory(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    database = Database.from_library(paths)
    application = create_app(database)
    transport = httpx2.ASGITransport(app=application)
    pdf = b"%PDF-1.4\noutside file\n%%EOF\n"
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Unsafe paper"},
            files={"document": ("unsafe.pdf", pdf, "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        with database.session() as session:
            attachment = session.get(AttachmentRecord, attachment_id)
            assert attachment is not None
            managed_file = paths.root / attachment.managed_path

        managed_file.unlink()
        managed_file.parent.rmdir()
        outside_directory = tmp_path / "outside"
        outside_directory.mkdir()
        (outside_directory / managed_file.name).write_bytes(pdf)
        managed_file.parent.symlink_to(outside_directory, target_is_directory=True)

        rejected = await client.get(f"/api/attachments/{attachment_id}/content")

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "managed_file_conflict",
        "message": "The saved PDF is missing or has changed.",
    }


@pytest.mark.anyio
async def test_reader_returns_not_found_for_an_unknown_attachment(tmp_path: Path) -> None:
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/api/attachments/999/content")

    assert response.status_code == 404
    assert response.json()["detail"] == "Attachment not found"


@pytest.mark.anyio
async def test_duplicate_import_points_to_the_existing_source(tmp_path: Path) -> None:
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    content = b"paper,year\nA useful paper,2026\n"
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "First paper"},
            files={"document": ("first.csv", content, "text/csv")},
        )
        duplicate = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Duplicate paper"},
            files={"document": ("renamed.csv", content, "text/csv")},
        )
        sources = await client.get("/api/sources")

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {
        "code": "duplicate",
        "message": "This document is already in the library.",
        "source_id": first.json()["source"]["id"],
        "attachment_id": first.json()["attachment"]["id"],
    }
    assert [source["title"] for source in sources.json()] == ["First paper"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure_status",
    [
        ConversionStatus.NEEDS_OCR,
        ConversionStatus.ENCRYPTED,
        ConversionStatus.UNSUPPORTED,
        ConversionStatus.MALFORMED,
    ],
)
async def test_conversion_failure_is_returned_on_the_saved_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_status: ConversionStatus,
) -> None:
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)

    def fail_conversion(_data: bytes, _filename: str) -> documents.ConvertedDocument:
        raise DocumentConversionFailure(
            failure_status,
            "Conversion failed safely.",
            diagnostics={"detail": "test diagnostic"},
        )

    monkeypatch.setattr(documents, "convert_document_bytes", fail_conversion)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "A difficult paper"},
            files={"document": ("paper.pdf", b"not really a PDF", "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        converted = await client.post(f"/api/attachments/{attachment_id}/convert")
        reopened = await client.get(f"/api/sources/{imported.json()['source']['id']}")

    assert imported.status_code == 201
    assert converted.status_code == 200
    assert converted.json()["conversion_status"] == failure_status.value
    assert converted.json()["conversion_message"] == "Conversion failed safely."
    assert converted.json()["conversion_diagnostics"] == {"detail": "test diagnostic"}
    assert converted.json()["has_extracted_text"] is False
    assert converted.json()["can_remove"] is True
    assert reopened.json()["attachments"][0] == converted.json()


@pytest.mark.anyio
async def test_failed_attachment_can_be_removed_with_its_managed_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    application = create_app(Database.from_library(paths))
    transport = httpx2.ASGITransport(app=application)

    def fail_conversion(_data: bytes, _filename: str) -> documents.ConvertedDocument:
        raise DocumentConversionFailure(
            ConversionStatus.UNSUPPORTED,
            "Conversion failed safely.",
        )

    monkeypatch.setattr(documents, "convert_document_bytes", fail_conversion)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "A difficult paper"},
            files={"document": ("paper.pdf", b"not really a PDF", "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        source_id = imported.json()["source"]["id"]
        converted = await client.post(f"/api/attachments/{attachment_id}/convert")
        removed = await client.delete(f"/api/attachments/{attachment_id}")
        reopened = await client.get(f"/api/sources/{source_id}")

    assert converted.json()["can_remove"] is True
    assert removed.status_code == 204
    assert removed.content == b""
    assert reopened.json()["attachments"] == []
    assert not [path for path in paths.attachments.rglob("*") if path.is_file()]
    assert not list(paths.temporary_imports.iterdir())


@pytest.mark.anyio
async def test_pending_attachment_cannot_be_removed(tmp_path: Path) -> None:
    paths = LibraryPaths.from_root(tmp_path / "library")
    application = create_app(Database.from_library(paths))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "A pending paper"},
            files={"document": ("paper.pdf", b"pending document", "application/pdf")},
        )
        attachment_id = imported.json()["attachment"]["id"]
        rejected = await client.delete(f"/api/attachments/{attachment_id}")
        reopened = await client.get(f"/api/sources/{imported.json()['source']['id']}")

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == {
        "code": "attachment_not_removable",
        "message": "Only a document with a failed extraction can be removed.",
    }
    assert reopened.json()["attachments"][0]["id"] == attachment_id
    assert [path for path in paths.attachments.rglob("*") if path.is_file()]


@pytest.mark.anyio
async def test_empty_import_remains_visible_with_a_specific_status(tmp_path: Path) -> None:
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        imported = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Empty paper"},
            files={"document": ("empty.pdf", b"", "application/pdf")},
        )
        converted = await client.post(
            f"/api/attachments/{imported.json()['attachment']['id']}/convert"
        )

    assert imported.status_code == 201
    assert converted.json()["conversion_status"] == "empty"
    assert converted.json()["conversion_message"] == "The document is empty."


@pytest.mark.anyio
async def test_oversized_import_is_rejected_before_a_source_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "MAX_DOCUMENT_BYTES", 4)
    application = create_app(Database.from_library(LibraryPaths.from_root(tmp_path / "library")))
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        rejected = await client.post(
            "/api/imports",
            data={"source_type": "paper", "title": "Oversized paper"},
            files={"document": ("large.pdf", b"12345", "application/pdf")},
        )
        sources = await client.get("/api/sources")

    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "oversized"
    assert rejected.json()["detail"]["maximum_byte_size"] == 4
    assert sources.json() == []
