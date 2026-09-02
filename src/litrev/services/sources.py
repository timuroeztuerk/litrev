from __future__ import annotations

from sqlalchemy import delete, select

from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, NoteRecord, SourceRecord
from litrev.infrastructure.storage import (
    ManagedExtractionStore,
    stage_managed_attachment_removals,
)


class SourceNotFoundError(Exception):
    pass


class SourceRemovalDatabaseError(Exception):
    pass


def remove_source(database: Database, source_id: int) -> None:
    paths = database.library_paths
    if paths is None:
        raise ValueError("Source removal requires a library-backed database")

    with database.session() as session:
        if session.get(SourceRecord, source_id) is None:
            raise SourceNotFoundError(f"Source {source_id} does not exist.")

        attachments = tuple(
            session.scalars(
                select(AttachmentRecord)
                .where(AttachmentRecord.source_id == source_id)
                .order_by(AttachmentRecord.id)
            )
        )
        extraction_store = ManagedExtractionStore(paths)
        staged = stage_managed_attachment_removals(
            paths,
            attachments=(
                (
                    attachment.checksum,
                    attachment.managed_path,
                    attachment.extracted_path
                    or extraction_store.relative_path_for(attachment.checksum),
                )
                for attachment in attachments
            ),
        )

        try:
            session.execute(delete(NoteRecord).where(NoteRecord.source_id == source_id))
            session.execute(delete(AttachmentRecord).where(AttachmentRecord.source_id == source_id))
            session.execute(delete(SourceRecord).where(SourceRecord.id == source_id))
            session.commit()
        except Exception as error:
            rollback_error: Exception | None = None
            try:
                session.rollback()
            except Exception as caught:
                rollback_error = caught
            staged.restore()
            raise SourceRemovalDatabaseError(
                "The source record could not be removed; its managed artifacts were restored."
            ) from (rollback_error or error)

        staged.discard()
