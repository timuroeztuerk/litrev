from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from litrev.diagnostics import run_checks
from litrev.infrastructure.database import Database
from litrev.infrastructure.models import SourceRecord
from litrev.infrastructure.storage import LibraryPaths
from litrev.services.documents import DocumentConversionFailure, convert_document_bytes

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class SourceCreate(BaseModel):
    title: str
    doi: str | None = None


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    doi: str | None
    created_at: datetime


class DocumentRead(BaseModel):
    filename: str
    format: str
    markdown: str


def create_app(database: Database | None = None) -> FastAPI:
    active_database = database or Database.from_library(LibraryPaths.default())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_database.migrate()
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
        doi = (source.doi or "").strip() or None
        if not title:
            raise HTTPException(status_code=422, detail="A source title is required")

        with active_database.session() as session:
            record = SourceRecord(title=title, doi=doi)
            session.add(record)
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A source with this DOI already exists.",
                ) from error
            session.refresh(record)
            session.expunge(record)
            return record

    @application.post("/api/documents/convert", response_model=DocumentRead)
    async def convert_document(document: Annotated[UploadFile, File()]) -> object:
        content = await document.read(MAX_DOCUMENT_BYTES + 1)
        if not content:
            raise HTTPException(status_code=422, detail="The document is empty")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail="Documents are limited to 50 MB")

        try:
            return convert_document_bytes(content, document.filename or "document")
        except DocumentConversionFailure as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": error.code,
                    "message": str(error),
                    "pages": error.pages,
                },
            ) from error

    return application


app = create_app()
