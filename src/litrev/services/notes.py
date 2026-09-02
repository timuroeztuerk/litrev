from __future__ import annotations

import math
from dataclasses import dataclass

from litrev.infrastructure.database import Database
from litrev.infrastructure.models import AttachmentRecord, HighlightRecord, NoteRecord

MAX_NOTE_BODY_LENGTH = 100_000
MAX_HIGHLIGHT_TEXT_LENGTH = 10_000
MAX_HIGHLIGHT_RECTANGLES = 100


@dataclass(frozen=True)
class NewHighlightDraft:
    selected_text: str
    rectangles: tuple[dict[str, float], ...]


class ReaderNoteAttachmentNotFoundError(Exception):
    pass


class ReaderNoteNotFoundError(Exception):
    pass


class ReaderNoteNotPdfError(Exception):
    pass


class ReaderNoteValidationError(Exception):
    pass


class ReaderNoteRelationshipError(Exception):
    pass


class ReaderNoteDatabaseError(Exception):
    pass


def create_reader_note(
    database: Database,
    *,
    attachment_id: int,
    page_number: int,
    body: str,
    highlight_id: int | None = None,
    new_highlight: NewHighlightDraft | None = None,
) -> int:
    clean_body = _validated_body(body)
    _validate_page_number(page_number)
    if highlight_id is not None and new_highlight is not None:
        raise ReaderNoteValidationError(
            "Choose either a saved highlight or the current selection, not both."
        )

    with database.session() as session:
        attachment = session.get(AttachmentRecord, attachment_id)
        if attachment is None:
            raise ReaderNoteAttachmentNotFoundError(f"Attachment {attachment_id} does not exist.")
        if attachment.detected_format != "pdf":
            raise ReaderNoteNotPdfError("Reader notes require a PDF attachment.")

        highlight: HighlightRecord | None = None
        if highlight_id is not None:
            highlight = session.get(HighlightRecord, highlight_id)
            if highlight is None:
                raise ReaderNoteRelationshipError("The selected highlight no longer exists.")
            if highlight.attachment_id != attachment_id or highlight.page_number != page_number:
                raise ReaderNoteRelationshipError(
                    "The selected highlight does not belong to this attachment and page."
                )
        elif new_highlight is not None:
            selected_text, rectangles = _validated_new_highlight(new_highlight)
            highlight = HighlightRecord(
                attachment=attachment,
                page_number=page_number,
                selected_text=selected_text,
                rectangles=rectangles,
            )

        note = NoteRecord(
            source_id=attachment.source_id,
            attachment=attachment,
            page_number=page_number,
            body=clean_body,
            highlight=highlight,
        )
        session.add(note)
        try:
            session.commit()
        except Exception as error:
            session.rollback()
            raise ReaderNoteDatabaseError(
                "The Reader note and any new highlight could not be saved."
            ) from error
        return note.id


def update_reader_note(database: Database, *, note_id: int, body: str) -> int:
    clean_body = _validated_body(body)
    with database.session() as session:
        note = session.get(NoteRecord, note_id)
        if note is None:
            raise ReaderNoteNotFoundError(f"Note {note_id} does not exist.")
        if note.attachment_id is None or note.page_number is None:
            raise ReaderNoteRelationshipError(
                "This note does not have a structured Reader locator."
            )
        note.body = clean_body
        try:
            session.commit()
        except Exception as error:
            session.rollback()
            raise ReaderNoteDatabaseError("The Reader note could not be updated.") from error
        return note.id


def _validated_body(body: str) -> str:
    if not body.strip():
        raise ReaderNoteValidationError("Reader notes cannot be empty.")
    if len(body) > MAX_NOTE_BODY_LENGTH:
        raise ReaderNoteValidationError(
            f"Reader notes are limited to {MAX_NOTE_BODY_LENGTH:,} characters."
        )
    return body


def _validate_page_number(page_number: int) -> None:
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ReaderNoteValidationError("Reader note pages must be one-based positive integers.")


def _validated_new_highlight(
    draft: NewHighlightDraft,
) -> tuple[str, list[dict[str, float]]]:
    if not draft.selected_text.strip():
        raise ReaderNoteValidationError("Selected highlight text cannot be blank.")
    if len(draft.selected_text) > MAX_HIGHLIGHT_TEXT_LENGTH:
        raise ReaderNoteValidationError(
            f"Selected highlight text is limited to {MAX_HIGHLIGHT_TEXT_LENGTH:,} characters."
        )
    if not 1 <= len(draft.rectangles) <= MAX_HIGHLIGHT_RECTANGLES:
        raise ReaderNoteValidationError(
            f"Highlights require between 1 and {MAX_HIGHLIGHT_RECTANGLES} rectangles."
        )

    clean_rectangles: list[dict[str, float]] = []
    expected_keys = {"x", "y", "width", "height"}
    for rectangle in draft.rectangles:
        if set(rectangle) != expected_keys:
            raise ReaderNoteValidationError("Highlight rectangles have an invalid shape.")
        values = tuple(rectangle[name] for name in ("x", "y", "width", "height"))
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in values
        ):
            raise ReaderNoteValidationError("Highlight rectangle coordinates must be finite.")
        x, y, width, height = (float(value) for value in values)
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
            raise ReaderNoteValidationError("Highlight rectangles must stay within the page.")
        clean_rectangles.append({"x": x, "y": y, "width": width, "height": height})
    return draft.selected_text, clean_rectangles
