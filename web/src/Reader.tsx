import { PdfReader } from "./PdfReader";
import { getPdfContentUrl, type ReaderDocument } from "./api";

interface ReaderScreenProps {
  documents: ReaderDocument[];
  error: string | null;
  isLoading: boolean;
  onBackToDocuments: () => void;
  onOpenDocument: (document: ReaderDocument) => void;
  onRetry: () => void;
  selectedDocument: ReaderDocument | null;
}

function formatByteSize(byteSize: number): string {
  if (byteSize < 1024) return `${byteSize} B`;
  if (byteSize < 1024 * 1024) return `${(byteSize / 1024).toFixed(1)} KB`;
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`;
}

export function ReaderScreen({
  documents,
  error,
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
        <PdfReader
          key={selectedDocument.attachment_id}
          title={selectedDocument.source_title}
          url={getPdfContentUrl(selectedDocument.attachment_id)}
        />
      </section>
    );
  }

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
      )}
    </section>
  );
}
