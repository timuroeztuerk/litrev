from litrev.diagnostics import run_checks


def test_selected_technology_is_available() -> None:
    checks = run_checks()

    assert set(checks) == {
        "FastAPI",
        "SQLite + FTS5",
        "SQLAlchemy",
        "PyMuPDF",
        "NetworkX",
    }
