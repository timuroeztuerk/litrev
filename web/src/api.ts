export type SourceType = "paper" | "book" | "other";
export type ReadingStatus = "unread" | "reading" | "read";

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

export interface SourceDetail extends Source {
  attachments: Attachment[];
}

export interface ImportedDocument {
  source: Source;
  attachment: Attachment;
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
    let detail: unknown;
    try {
      const body = (await response.json()) as { detail?: unknown };
      detail = body.detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
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

export function updateSource(sourceId: number, source: SourceUpdate): Promise<SourceDetail> {
  return request<SourceDetail>(`/api/sources/${sourceId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(source),
  });
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
