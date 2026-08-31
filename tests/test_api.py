from pathlib import Path

import httpx2
import pytest

from litrev import api
from litrev.api import create_app
from litrev.domain.documents import ConversionStatus
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import CollectionRecord, TagRecord
from litrev.infrastructure.storage import LibraryPaths
from litrev.services import documents
from litrev.services.documents import DocumentConversionFailure


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
    }
    payload.update(overrides)
    return payload


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
    assert updated.json()["attachments"] == []
    assert sources.json()[0] == {
        key: value for key, value in updated.json().items() if key != "attachments"
    }


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
    with database.session() as session:
        assert session.query(TagRecord).count() == 0
        assert session.query(CollectionRecord).count() == 0


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
