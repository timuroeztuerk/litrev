from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_path
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, engine: Engine, path: Path | None = None) -> None:
        self.engine = engine
        self.path = path

    @classmethod
    def from_path(cls, path: Path) -> Database:
        return cls(create_engine(f"sqlite:///{path}"), path)

    @classmethod
    def in_memory(cls) -> Database:
        return cls(create_engine("sqlite:///:memory:"))

    def create_schema(self) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

        # Import models so SQLAlchemy has registered their tables.
        from litrev.infrastructure import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return Session(self.engine)


def default_database_path() -> Path:
    return user_data_path("litrev", "Litrev") / "litrev.sqlite3"
