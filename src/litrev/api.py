from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from litrev.diagnostics import run_checks
from litrev.infrastructure.database import Database, default_database_path
from litrev.infrastructure.models import SourceRecord


class SourceCreate(BaseModel):
    title: str
    doi: str | None = None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    doi: str | None
    created_at: datetime


def create_app(database: Database | None = None) -> FastAPI:
    active_database = database or Database.from_path(default_database_path())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_database.create_schema()
        yield

    application = FastAPI(
        title="Litrev local API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:1420",
            "http://localhost:1420",
            "http://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "technology": run_checks()}

    @application.get("/api/sources", response_model=list[SourceRead])
    async def list_sources() -> list[SourceRecord]:
        with active_database.session() as session:
            return list(session.scalars(select(SourceRecord).order_by(SourceRecord.title)))

    @application.post(
        "/api/sources",
        response_model=SourceRead,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_source(source: SourceCreate) -> SourceRecord:
        title = source.title.strip()
        if not title:
            raise HTTPException(status_code=422, detail="A source title is required")

        with active_database.session() as session:
            record = SourceRecord(title=title, doi=source.doi)
            session.add(record)
            session.commit()
            session.refresh(record)
            session.expunge(record)
            return record

    return application


app = create_app()
