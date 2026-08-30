from __future__ import annotations

import sqlite3
from importlib.metadata import version

import networkx
import sqlalchemy


def run_checks() -> dict[str, str]:
    """Return a small, side-effect-free report of the application's foundations."""
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE documents USING fts5(content)")
    finally:
        connection.close()

    return {
        "Anydoc": version("firecrawl-anydoc"),
        "FastAPI": version("fastapi"),
        "SQLite + FTS5": sqlite3.sqlite_version,
        "SQLAlchemy": sqlalchemy.__version__,
        "NetworkX": networkx.__version__,
    }
