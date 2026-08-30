from litrev.infrastructure.database import Database
from litrev.infrastructure.models import NoteRecord, SourceRecord


def test_source_and_linked_note_are_persisted() -> None:
    database = Database.in_memory()
    database.create_schema()

    with database.session() as session:
        source = SourceRecord(title="A useful paper", doi="10.1234/example")
        source.notes.append(NoteRecord(body="Important finding", locator="p. 7"))
        session.add(source)
        session.commit()

    with database.session() as session:
        saved = session.query(SourceRecord).one()
        assert saved.title == "A useful paper"
        assert saved.notes[0].locator == "p. 7"
