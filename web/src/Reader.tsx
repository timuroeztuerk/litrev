import { PdfReader } from "./PdfReader";
import { getPdfContentUrl, type ReaderDocument, type ReaderNote } from "./api";

interface ReaderScreenProps {
  documents: ReaderDocument[];
  error: string | null;
  initialPage: number;
  isLoading: boolean;
  onBackToDocuments: () => void;
  onOpenDocument: (document: ReaderDocument, pageNumber?: number) => void;
  onRetry: () => void;
  selectedDocument: ReaderDocument | null;
}

function formatByteSize(byteSize: number): string {
  if (byteSize < 1024) return `${byteSize} B`;
  if (byteSize < 1024 * 1024) return `${(byteSize / 1024).toFixed(1)} KB`;
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`;
}

function noteSort(left: ReaderNote, right: ReaderNote): number {
  return Date.parse(right.created_at) - Date.parse(left.created_at) || right.id - left.id;
}

function attachmentProblem(document: ReaderDocument): string {
  if (document.attachment_availability === "storage_unavailable") {
    return "The local library storage is unavailable. The saved notes and page locators are preserved.";
  }
  return "This PDF is missing or has changed. The saved notes and page locators are preserved.";
}

export function ReaderScreen({
  documents,
  error,
  initialPage,
  isLoading,
  onBackToDocuments,
  onOpenDocument,
  onRetry,
  selectedDocument,
}: ReaderScreenProps) {
  if (selectedDocument) {
    return (
      <section className="reader-workspace" aria-labelledby="reader-document-heading">
        <button className="back-button" onClick={onBackToDocuments} type="button">
          ← All PDFs
        </button>
        <div className="reader-document-heading">
          <div>
            <p className="eyebrow">{selectedDocument.original_filename}</p>
            <h2 id="reader-document-heading">{selectedDocument.source_title}</h2>
          </div>
          <span>{formatByteSize(selectedDocument.byte_size)}</span>
        </div>
        {selectedDocument.attachment_availability === "available" ? (
          <PdfReader
            attachmentId={selectedDocument.attachment_id}
            initialPage={initialPage}
            key={selectedDocument.attachment_id}
            title={selectedDocument.source_title}
            url={getPdfContentUrl(selectedDocument.attachment_id)}
          />
        ) : (
          <section className="reader-unresolved" aria-label="Unavailable PDF notes">
            <p className="error-message" role="alert">
              {attachmentProblem(selectedDocument)}
            </p>
            <h3>Saved notes</h3>
            <NoteList notes={selectedDocument.reader_notes} />
          </section>
        )}
      </section>
    );
  }

  const notes = documents.flatMap((document) =>
    document.reader_notes.map((note) => ({ document, note })),
  );
  notes.sort((left, right) => noteSort(left.note, right.note));

  return (
    <section className="reader-library" aria-labelledby="reader-library-heading">
      <div className="section-heading">
        <h2 id="reader-library-heading">Saved PDFs</h2>
        <span>{documents.length}</span>
      </div>
      {isLoading ? (
        <p className="loading-message" role="status">
          Finding local PDFs…
        </p>
      ) : error ? (
        <div className="reader-list-error error-message" role="alert">
          <span>{error}</span>
          <button onClick={onRetry} type="button">
            Try again
          </button>
        </div>
      ) : documents.length === 0 ? (
        <div className="reader-empty">
          <h3>No PDFs in the Reader yet</h3>
          <p>Import a PDF from Library, then open it here or from its source.</p>
        </div>
      ) : (
        <>
          <ul className="reader-document-list">
            {documents.map((document) => (
              <li key={document.attachment_id}>
                <button onClick={() => onOpenDocument(document)} type="button">
                  <span>
                    <strong>{document.source_title}</strong>
                    <small>{document.original_filename}</small>
                  </span>
                  <span>{formatByteSize(document.byte_size)}</span>
                </button>
              </li>
            ))}
          </ul>
          {notes.length > 0 && (
            <section className="reader-saved-notes" aria-labelledby="reader-saved-notes-heading">
              <div className="section-heading">
                <h2 id="reader-saved-notes-heading">Saved notes</h2>
                <span>{notes.length}</span>
              </div>
              <ul>
                {notes.map(({ document, note }) => (
                  <li key={note.id}>
                    <button
                      onClick={() => onOpenDocument(document, note.page_number)}
                      type="button"
                    >
                      <span>{note.body}</span>
                      <small>
                        {note.source_title} · page {note.page_number}
                        {note.attachment_availability === "available" ? "" : " · PDF unavailable"}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </section>
  );
}

function NoteList({ notes }: { notes: ReaderNote[] }) {
  if (notes.length === 0) {
    return <p className="reader-notes-empty">No saved notes for this PDF.</p>;
  }
  return (
    <ul className="reader-note-list">
      {notes.map((note) => (
        <li key={note.id}>
          <p>{note.body}</p>
          <small>Page {note.page_number}</small>
          {note.highlight && <q>{note.highlight.selected_text}</q>}
        </li>
      ))}
    </ul>
  );
}
