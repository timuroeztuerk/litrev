import json
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest

from litrev import api
from litrev.api import create_app
from litrev.domain.documents import ConversionStatus
from litrev.domain.sources import SourceType
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import (
    CollectionRecord,
    SourceIdentifierRecord,
    SourceMetadataLookupRecord,
    SourceRecord,
    TagRecord,
)
from litrev.infrastructure.storage import LibraryPaths
from litrev.services import bibliographies, documents
from litrev.services.documents import DocumentConversionFailure
from litrev.services.doi_metadata import (
    DoiMetadata,
    DoiMetadataIdentifier,
    DoiMetadataMalformedError,
    DoiMetadataNotFoundError,
    DoiMetadataRateLimitedError,
    DoiMetadataUnavailableError,
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


def doi_metadata(**overrides: object) -> DoiMetadata:
    metadata = DoiMetadata(
        doi="10.1234/example",
        source_type=SourceType.PAPER,
        title="Crossref title",
        authors=["Ada Lovelace", "Research Collective"],
        publication_year=2024,
        venue="Crossref Journal",
        url="https://doi.org/10.1234/example",
        abstract="Crossref abstract.",
        language="en",
        identifiers=[
            DoiMetadataIdentifier(identifier_type="isbn", value="978-0-306-40615-7"),
            DoiMetadataIdentifier(identifier_type="issn", value="2049-3630"),
        ],
    )
    return replace(metadata, **overrides)


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
async def test_doi_metadata_preview_is_normalized_deterministic_and_read_only() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> DoiMetadata:
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
    def unexpected_provider(_doi: str) -> DoiMetadata:
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
    def unexpected_provider(_doi: str) -> DoiMetadata:
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
async def test_doi_metadata_is_reviewed_before_selected_fields_are_applied() -> None:
    database = Database.in_memory()
    provider_calls: list[str] = []

    def provider(doi: str) -> DoiMetadata:
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
        "requested_doi": "10.1234/example",
        "retrieved_doi": "10.1234/example",
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
async def test_doi_metadata_lookup_requires_a_saved_doi_without_calling_the_provider() -> None:
    def unexpected_provider(_doi: str) -> DoiMetadata:
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
    def failed_provider(_doi: str) -> DoiMetadata:
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
