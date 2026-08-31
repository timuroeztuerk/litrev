from pathlib import Path

import httpx2
import pytest

from litrev import api
from litrev.api import create_app
from litrev.domain.documents import ConversionStatus
from litrev.infrastructure.database import Database
from litrev.infrastructure.storage import LibraryPaths
from litrev.services import documents
from litrev.services.documents import DocumentConversionFailure


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


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
    assert converted.status_code == 200
    assert converted.json()["conversion_status"] == "succeeded"
    assert converted.json()["has_extracted_text"] is True
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
    assert reopened.json()["attachments"][0] == converted.json()


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
