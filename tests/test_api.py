import httpx2
import pytest

from litrev.api import create_app
from litrev.infrastructure.database import Database


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
            json={"title": "A useful paper", "doi": "10.1234/example"},
        )
        sources = await client.get("/api/sources")

    assert created.status_code == 201
    assert sources.status_code == 200
    assert sources.json()[0]["title"] == "A useful paper"


@pytest.mark.anyio
async def test_document_can_be_converted_with_anydoc() -> None:
    application = create_app(Database.in_memory())
    transport = httpx2.ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        httpx2.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/documents/convert",
            files={"document": ("papers.csv", b"paper,year\nA useful paper,2026\n", "text/csv")},
        )

    assert response.status_code == 200
    assert response.json()["format"] == "csv"
    assert "A useful paper" in response.json()["markdown"]
