export type SourceType = "paper" | "book" | "other";
export type ReadingStatus = "unread" | "reading" | "read";
export type BibliographyFormat = "bibtex" | "ris" | "csl-json";
export type MetadataField =
  | "source_type"
  | "title"
  | "authors"
  | "publication_year"
  | "venue"
  | "url"
  | "abstract"
  | "language"
  | "identifiers";

export interface SourceIdentifier {
  identifier_type: string;
  value: string;
}

export interface SourceCitationKey {
  bibliography_format: BibliographyFormat;
  value: string;
}

export interface Source {
  id: number;
  source_type: SourceType;
  title: string;
  authors: string[];
  publication_year: number | null;
  venue: string | null;
  doi: string | null;
  url: string | null;
  abstract: string | null;
  language: string | null;
  reading_status: ReadingStatus;
  tags: string[];
  collections: string[];
  identifiers: SourceIdentifier[];
  citation_keys: SourceCitationKey[];
  created_at: string;
}

export interface SourceUpdate {
  source_type: SourceType;
  title: string;
  authors: string[];
  publication_year: number | null;
  venue: string | null;
  doi: string | null;
  url: string | null;
  abstract: string | null;
  language: string | null;
  reading_status: ReadingStatus;
  tags: string[];
  collections: string[];
  identifiers: SourceIdentifier[];
}

export type ConversionStatus =
  | "pending"
  | "succeeded"
  | "empty"
  | "oversized"
  | "needs_ocr"
  | "encrypted"
  | "unsupported"
  | "malformed"
  | "resource_limit"
  | "missing_part";

export interface Attachment {
  id: number;
  source_id: number;
  original_filename: string;
  media_type: string | null;
  byte_size: number;
  detected_format: string | null;
  conversion_status: ConversionStatus;
  conversion_message: string | null;
  conversion_diagnostics: Record<string, unknown> | null;
  has_extracted_text: boolean;
  can_remove: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReaderDocument {
  attachment_id: number;
  source_id: number;
  source_title: string;
  original_filename: string;
  byte_size: number;
  attachment_availability: AttachmentAvailability;
  reader_notes: ReaderNote[];
}

export type AttachmentAvailability =
  | "available"
  | "missing_or_changed"
  | "storage_unavailable";

export interface HighlightRectangle {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Highlight {
  id: number;
  attachment_id: number;
  source_id: number;
  page_number: number;
  selected_text: string;
  rectangles: HighlightRectangle[];
  created_at: string;
}

export interface HighlightCreate {
  page_number: number;
  selected_text: string;
  rectangles: HighlightRectangle[];
}

export interface ReaderNote {
  id: number;
  source_id: number;
  source_title: string;
  attachment_id: number;
  original_filename: string;
  page_number: number;
  body: string;
  highlight: Highlight | null;
  attachment_availability: AttachmentAvailability;
  created_at: string;
}

export interface ReaderNoteCreate {
  page_number: number;
  body: string;
  highlight_id?: number;
  new_highlight?: Pick<HighlightCreate, "selected_text" | "rectangles">;
}

export interface SourceDetail extends Source {
  attachments: Attachment[];
  metadata_provenance: MetadataProvenance[];
}

export interface MetadataProvenance {
  lookup_id: number;
  provider: string;
  provider_url: string;
  identifier_type: "doi" | "isbn";
  requested_identifier: string;
  retrieved_identifier: string;
  retrieved_at: string;
  applied_fields: MetadataField[];
  applied_at: string;
}

export interface MetadataProposal {
  source_type: SourceType | null;
  title: string | null;
  authors: string[] | null;
  publication_year: number | null;
  venue: string | null;
  url: string | null;
  abstract: string | null;
  language: string | null;
  identifiers: SourceIdentifier[] | null;
}

export interface MetadataLookup {
  id: number;
  provider: string;
  provider_url: string;
  identifier_type: "doi" | "isbn";
  requested_identifier: string;
  retrieved_identifier: string;
  retrieved_at: string;
  proposal: MetadataProposal;
  available_fields: MetadataField[];
  conflicting_fields: MetadataField[];
}

export interface ExistingDoiSource {
  id: number;
  source_type: SourceType;
  title: string;
  doi: string;
}

export type DoiMetadataPreview =
  | {
      kind: "existing_source";
      normalized_doi: string;
      existing_source: ExistingDoiSource;
    }
  | {
      kind: "proposal";
      normalized_doi: string;
      provider: string;
      provider_url: string;
      retrieved_doi: string;
      retrieved_at: string;
      proposal_fingerprint: string;
      proposal: MetadataProposal;
      available_fields: MetadataField[];
    };

export interface DoiSourceCreate {
  doi: string;
  proposal_fingerprint: string;
  fields: MetadataField[];
}

export interface ExistingIsbnSource {
  id: number;
  source_type: SourceType;
  title: string;
  isbn_values: string[];
}

export type IsbnMetadataPreview =
  | {
      kind: "existing_sources";
      input_isbn: string;
      normalized_isbn: string;
      canonical_isbn13: string;
      existing_sources: ExistingIsbnSource[];
    }
  | {
      kind: "proposal";
      input_isbn: string;
      normalized_isbn: string;
      canonical_isbn13: string;
      provider: string;
      provider_url: string;
      retrieved_isbn: string;
      retrieved_at: string;
      proposal_fingerprint: string;
      proposal: MetadataProposal;
      available_fields: MetadataField[];
    };

export interface IsbnSourceCreate {
  isbn: string;
  proposal_fingerprint: string;
  fields: MetadataField[];
}

export interface ImportedDocument {
  source: Source;
  attachment: Attachment;
}

export interface SkippedBibliographySource {
  entry_id: string;
  title: string;
  doi: string;
  reason: "existing_doi" | "duplicate_doi_in_file";
}

export interface BibliographyImport {
  bibliography_format: BibliographyFormat;
  total_entries: number;
  imported: Source[];
  skipped: SkippedBibliographySource[];
}

export interface ExtractedText {
  attachment_id: number;
  markdown: string;
}

export interface Health {
  status: "ok";
  technology: Record<string, string>;
}

export interface ApiProblem {
  code?: string;
  message?: string;
  source_id?: number;
  attachment_id?: number;
  [key: string]: unknown;
}

const apiBase = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8765";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(`Litrev service returned ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, init);
  if (!response.ok) {
    throw new ApiError(response.status, await responseDetail(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path: string): Promise<Blob> {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok) {
    throw new ApiError(response.status, await responseDetail(response));
  }
  return response.blob();
}

async function responseDetail(response: Response): Promise<unknown> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return body.detail;
  } catch {
    return undefined;
  }
}

export function getHealth(signal?: AbortSignal): Promise<Health> {
  return request<Health>("/api/health", { signal });
}

export function getSources(signal?: AbortSignal): Promise<Source[]> {
  return request<Source[]>("/api/sources", { signal });
}

export function getSource(sourceId: number): Promise<SourceDetail> {
  return request<SourceDetail>(`/api/sources/${sourceId}`);
}

export function getReaderDocuments(signal?: AbortSignal): Promise<ReaderDocument[]> {
  return request<ReaderDocument[]>("/api/reader/documents", { signal });
}

export function getPdfContentUrl(attachmentId: number): string {
  return `${apiBase}/api/attachments/${attachmentId}/content`;
}

export function getHighlights(
  attachmentId: number,
  signal?: AbortSignal,
): Promise<Highlight[]> {
  return request<Highlight[]>(`/api/attachments/${attachmentId}/highlights`, { signal });
}

export function createHighlight(
  attachmentId: number,
  highlight: HighlightCreate,
): Promise<Highlight> {
  return request<Highlight>(`/api/attachments/${attachmentId}/highlights`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(highlight),
  });
}

export function deleteHighlight(highlightId: number): Promise<void> {
  return request<void>(`/api/highlights/${highlightId}`, { method: "DELETE" });
}

export function getReaderNotes(
  attachmentId: number,
  signal?: AbortSignal,
): Promise<ReaderNote[]> {
  return request<ReaderNote[]>(`/api/attachments/${attachmentId}/notes`, { signal });
}

export function createReaderNote(
  attachmentId: number,
  note: ReaderNoteCreate,
): Promise<ReaderNote> {
  return request<ReaderNote>(`/api/attachments/${attachmentId}/notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(note),
  });
}

export function updateReaderNote(noteId: number, body: string): Promise<ReaderNote> {
  return request<ReaderNote>(`/api/notes/${noteId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body }),
  });
}

export function updateSource(sourceId: number, source: SourceUpdate): Promise<SourceDetail> {
  return request<SourceDetail>(`/api/sources/${sourceId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(source),
  });
}

export function createDoiMetadataLookup(sourceId: number): Promise<MetadataLookup> {
  return request<MetadataLookup>(`/api/sources/${sourceId}/doi-metadata-lookups`, {
    method: "POST",
  });
}

export function createDoiMetadataPreview(doi: string): Promise<DoiMetadataPreview> {
  return request<DoiMetadataPreview>("/api/doi-metadata-previews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doi }),
  });
}

export function createSourceFromDoi(creation: DoiSourceCreate): Promise<SourceDetail> {
  return request<SourceDetail>("/api/sources/from-doi", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(creation),
  });
}

export function createIsbnMetadataPreview(
  isbn: string,
  lookupIfLocalMatch = false,
): Promise<IsbnMetadataPreview> {
  return request<IsbnMetadataPreview>("/api/isbn-metadata-previews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ isbn, lookup_if_local_match: lookupIfLocalMatch }),
  });
}

export function createIsbnMetadataLookup(
  sourceId: number,
  isbn: string,
): Promise<MetadataLookup> {
  return request<MetadataLookup>(`/api/sources/${sourceId}/isbn-metadata-lookups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ isbn }),
  });
}

export function createSourceFromIsbn(creation: IsbnSourceCreate): Promise<SourceDetail> {
  return request<SourceDetail>("/api/sources/from-isbn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(creation),
  });
}

export function applyDoiMetadataLookup(
  sourceId: number,
  lookupId: number,
  fields: MetadataField[],
): Promise<SourceDetail> {
  return request<SourceDetail>(
    `/api/sources/${sourceId}/doi-metadata-lookups/${lookupId}/apply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    },
  );
}

export function applyIsbnMetadataLookup(
  sourceId: number,
  lookupId: number,
  fields: MetadataField[],
): Promise<SourceDetail> {
  return request<SourceDetail>(
    `/api/sources/${sourceId}/isbn-metadata-lookups/${lookupId}/apply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    },
  );
}

export function removeSource(sourceId: number): Promise<void> {
  return request<void>(`/api/sources/${sourceId}`, { method: "DELETE" });
}

export function createSource(sourceType: Exclude<SourceType, "other">, title: string): Promise<Source> {
  return request<Source>("/api/sources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_type: sourceType, title }),
  });
}

export function createDocumentImport(
  sourceType: Exclude<SourceType, "other">,
  title: string,
  file: File,
): Promise<ImportedDocument> {
  const form = new FormData();
  form.append("source_type", sourceType);
  form.append("title", title);
  form.append("document", file);
  return request<ImportedDocument>("/api/imports", {
    method: "POST",
    body: form,
  });
}

export function createBibliographyImport(file: File): Promise<BibliographyImport> {
  const form = new FormData();
  form.append("bibliography", file);
  return request<BibliographyImport>("/api/bibliography-imports", {
    method: "POST",
    body: form,
  });
}

export function getBibliographyExport(format: BibliographyFormat): Promise<Blob> {
  return requestBlob(`/api/bibliography-exports/${format}`);
}

export function convertAttachment(attachmentId: number): Promise<Attachment> {
  return request<Attachment>(`/api/attachments/${attachmentId}/convert`, {
    method: "POST",
  });
}

export function getExtractedText(attachmentId: number): Promise<ExtractedText> {
  return request<ExtractedText>(`/api/attachments/${attachmentId}/extracted-text`);
}

export function removeAttachment(attachmentId: number): Promise<void> {
  return request<void>(`/api/attachments/${attachmentId}`, { method: "DELETE" });
}
