import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  ApiError,
  convertAttachment,
  createDocumentImport,
  createSource,
  getExtractedText,
  getHealth,
  getSource,
  getSources,
  removeAttachment,
  updateSource,
  type ApiProblem,
  type Attachment,
  type ConversionStatus,
  type ExtractedText,
  type ReadingStatus,
  type Source,
  type SourceDetail,
  type SourceType,
  type SourceUpdate,
} from "./api";
import "./styles.css";

const themeStorageKey = "litrev-theme";
type CapturableSourceType = Exclude<SourceType, "other">;
type ImportStage = "idle" | "selected" | "saving" | "converting";
type FeedbackKind = "error" | "success" | "warning";
type DiscoverySort = "title" | "publication-year" | "recently-added";

const sourceTypeLabels: Record<SourceType, string> = {
  paper: "Paper",
  book: "Book",
  other: "Source",
};

const readingStatusLabels: Record<ReadingStatus, string> = {
  unread: "Unread",
  reading: "Reading",
  read: "Read",
};

const conversionStatusLabels: Record<ConversionStatus, string> = {
  pending: "Waiting for extraction",
  succeeded: "Extracted text ready",
  empty: "Empty document",
  oversized: "Document too large",
  needs_ocr: "OCR required",
  encrypted: "Encrypted document",
  unsupported: "Unsupported format",
  malformed: "Unreadable document",
  resource_limit: "Safety limit reached",
  missing_part: "Incomplete document",
};

interface Feedback {
  kind: FeedbackKind;
  message: string;
}

type ServiceStatus = "connecting" | "ready" | "unavailable";

const serviceStatusLabels: Record<ServiceStatus, string> = {
  connecting: "Connecting locally",
  ready: "Local service ready",
  unavailable: "Local service unavailable",
};

const markdownComponents: Components = {
  img({ alt }) {
    return (
      <span className="markdown-image-placeholder" role="note">
        Image omitted from local preview{alt ? `: ${alt}` : "."}
      </span>
    );
  },
};

function loadDarkModePreference(): boolean {
  try {
    return window.localStorage.getItem(themeStorageKey) !== "light";
  } catch {
    return true;
  }
}

function titleFromFilename(filename: string): string {
  const withoutExtension = filename.replace(/\.[^./]+$/, "");
  const readable = withoutExtension.replaceAll("_", " ").replaceAll("-", " ").trim();
  return readable || filename;
}

function detectedFileType(file: File): string {
  const extension = file.name.match(/\.([^.]+)$/)?.[1];
  if (extension) return extension.toUpperCase();
  return file.type || "Unknown";
}

function formatByteSize(byteSize: number): string {
  if (byteSize < 1024) return `${byteSize} B`;
  if (byteSize < 1024 * 1024) return `${(byteSize / 1024).toFixed(1)} KB`;
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MB`;
}

function problemFrom(error: unknown): ApiProblem | null {
  if (
    !(error instanceof ApiError) ||
    typeof error.detail !== "object" ||
    error.detail === null ||
    Array.isArray(error.detail)
  ) {
    return null;
  }
  return error.detail as ApiProblem;
}

function importFailureMessage(error: unknown): string {
  const problem = problemFrom(error);
  if (problem?.code === "oversized") {
    return problem.message ?? "This document exceeds the local import limit.";
  }
  if (problem?.message) return problem.message;
  if (error instanceof ApiError && typeof error.detail === "string") return error.detail;
  return "The document could not be saved. Check the local service and try again.";
}

function apiFailureMessage(error: unknown, fallback: string): string {
  const problem = problemFrom(error);
  if (problem?.message) return problem.message;
  if (error instanceof ApiError && typeof error.detail === "string") return error.detail;
  return fallback;
}

function metadataFromSource(source: Source): SourceUpdate {
  return {
    source_type: source.source_type,
    title: source.title,
    authors: [...source.authors],
    publication_year: source.publication_year,
    venue: source.venue,
    doi: source.doi,
    url: source.url,
    abstract: source.abstract,
    language: source.language,
    reading_status: source.reading_status,
    tags: [...source.tags],
    collections: [...source.collections],
  };
}

function sourceListDescription(source: Source): string {
  const citation = [source.authors.join(", "), source.publication_year]
    .filter((value) => value !== "" && value !== null)
    .join(" · ");
  return citation || source.doi || "Metadata not added yet";
}

function compareSourceTitles(a: Source, b: Source): number {
  return a.title.localeCompare(b.title) || a.id - b.id;
}

function compareSources(a: Source, b: Source, sort: DiscoverySort): number {
  if (sort === "publication-year") {
    if (a.publication_year === null && b.publication_year !== null) return 1;
    if (a.publication_year !== null && b.publication_year === null) return -1;
    if (a.publication_year !== null && b.publication_year !== null) {
      const yearComparison = b.publication_year - a.publication_year;
      if (yearComparison !== 0) return yearComparison;
    }
  }

  if (sort === "recently-added") {
    const addedComparison = Date.parse(b.created_at) - Date.parse(a.created_at);
    if (Number.isFinite(addedComparison) && addedComparison !== 0) return addedComparison;
  }

  return compareSourceTitles(a, b);
}

function sourceMatchesQuery(source: Source, normalizedQuery: string): boolean {
  if (!normalizedQuery) return true;
  return [source.title, ...source.authors, source.venue, source.doi].some((value) =>
    value?.toLocaleLowerCase().includes(normalizedQuery),
  );
}

function detailForImport(source: Source, attachment: Attachment): SourceDetail {
  return { ...source, attachments: [attachment] };
}

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [searchText, setSearchText] = useState("");
  const [discoverySort, setDiscoverySort] = useState<DiscoverySort>("title");
  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceType | "all">("all");
  const [readingStatusFilter, setReadingStatusFilter] = useState<ReadingStatus | "all">("all");
  const [tagFilter, setTagFilter] = useState("");
  const [collectionFilter, setCollectionFilter] = useState("");
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus>("connecting");
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [serviceAttempt, setServiceAttempt] = useState(0);
  const [sourceType, setSourceType] = useState<CapturableSourceType>("paper");
  const [title, setTitle] = useState("");
  const [isSavingSource, setIsSavingSource] = useState(false);
  const [captureFeedback, setCaptureFeedback] = useState<Feedback | null>(null);
  const [document, setDocument] = useState<File | null>(null);
  const [importSourceType, setImportSourceType] = useState<CapturableSourceType>("paper");
  const [importTitle, setImportTitle] = useState("");
  const [importStage, setImportStage] = useState<ImportStage>("idle");
  const [importFeedback, setImportFeedback] = useState<Feedback | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [isLoadingSource, setIsLoadingSource] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [detailNotice, setDetailNotice] = useState<Feedback | null>(null);
  const [retryingAttachmentId, setRetryingAttachmentId] = useState<number | null>(null);
  const [removingAttachmentId, setRemovingAttachmentId] = useState<number | null>(null);
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [extractedText, setExtractedText] = useState<ExtractedText | null>(null);
  const [loadingTextAttachmentId, setLoadingTextAttachmentId] = useState<number | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(loadDarkModePreference);
  const titleInput = useRef<HTMLInputElement>(null);
  const importTitleInput = useRef<HTMLInputElement>(null);
  const documentInput = useRef<HTMLInputElement>(null);
  const librarySearchInput = useRef<HTMLInputElement>(null);
  const sourceRequest = useRef(0);
  const textRequest = useRef(0);
  const serviceReady = serviceStatus === "ready";
  const isImporting = importStage === "saving" || importStage === "converting";
  const normalizedQuery = searchText.trim().toLocaleLowerCase();
  const discoveryControlsActive =
    normalizedQuery !== "" ||
    discoverySort !== "title" ||
    sourceTypeFilter !== "all" ||
    readingStatusFilter !== "all" ||
    tagFilter !== "" ||
    collectionFilter !== "";
  const availableTags = useMemo(
    () =>
      [...new Set([...sources.flatMap((source) => source.tags), ...(tagFilter ? [tagFilter] : [])])]
        .sort((a, b) => a.localeCompare(b)),
    [sources, tagFilter],
  );
  const availableCollections = useMemo(
    () =>
      [
        ...new Set([
          ...sources.flatMap((source) => source.collections),
          ...(collectionFilter ? [collectionFilter] : []),
        ]),
      ].sort((a, b) => a.localeCompare(b)),
    [collectionFilter, sources],
  );
  const visibleSources = useMemo(
    () =>
      sources
        .filter(
          (source) =>
            sourceMatchesQuery(source, normalizedQuery) &&
            (sourceTypeFilter === "all" || source.source_type === sourceTypeFilter) &&
            (readingStatusFilter === "all" || source.reading_status === readingStatusFilter) &&
            (tagFilter === "" || source.tags.includes(tagFilter)) &&
            (collectionFilter === "" || source.collections.includes(collectionFilter)),
        )
        .sort((a, b) => compareSources(a, b, discoverySort)),
    [
      collectionFilter,
      discoverySort,
      normalizedQuery,
      readingStatusFilter,
      sourceTypeFilter,
      sources,
      tagFilter,
    ],
  );

  useEffect(() => {
    try {
      window.localStorage.setItem(themeStorageKey, isDarkMode ? "dark" : "light");
    } catch {
      // Storage can be unavailable in private or embedded browser contexts.
    }
  }, [isDarkMode]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    Promise.all([getHealth(controller.signal), getSources(controller.signal)])
      .then(([, library]) => {
        if (!active) return;
        setServiceStatus("ready");
        setServiceError(null);
        setSources(library);
      })
      .catch(() => {
        controller.abort();
        if (!active) return;
        setServiceStatus("unavailable");
        setServiceError("The local Litrev service is not available.");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [serviceAttempt]);

  function retryService() {
    setServiceStatus("connecting");
    setServiceError(null);
    setServiceAttempt((current) => current + 1);
  }

  function clearDiscoveryControls() {
    setSearchText("");
    setDiscoverySort("title");
    setSourceTypeFilter("all");
    setReadingStatusFilter("all");
    setTagFilter("");
    setCollectionFilter("");
    librarySearchInput.current?.focus();
  }

  function resetExtractedText() {
    textRequest.current += 1;
    setLoadingTextAttachmentId(null);
    setExtractedText(null);
    setTextError(null);
  }

  function showLibrary() {
    sourceRequest.current += 1;
    setSelectedSource(null);
    setIsLoadingSource(false);
    setSourceError(null);
    setDetailNotice(null);
    resetExtractedText();
  }

  async function openSource(sourceId: number): Promise<boolean> {
    const requestId = sourceRequest.current + 1;
    sourceRequest.current = requestId;
    setIsLoadingSource(true);
    setSourceError(null);
    setDetailNotice(null);
    resetExtractedText();
    try {
      const source = await getSource(sourceId);
      if (sourceRequest.current !== requestId) return false;
      setSelectedSource(source);
      return true;
    } catch {
      if (sourceRequest.current !== requestId) return false;
      setSelectedSource(null);
      setSourceError("This source could not be opened. Check the local service and try again.");
      return false;
    } finally {
      if (sourceRequest.current === requestId) setIsLoadingSource(false);
    }
  }

  function clearImportSelection() {
    setDocument(null);
    setImportTitle("");
    setImportStage("idle");
    setImportFeedback(null);
    if (documentInput.current) documentInput.current.value = "";
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!serviceReady || isSavingSource) return;

    const cleanTitle = title.trim();
    if (!cleanTitle) {
      setCaptureFeedback({ kind: "error", message: "Enter a title to add this source." });
      titleInput.current?.focus();
      return;
    }

    setIsSavingSource(true);
    setCaptureFeedback(null);
    try {
      const source = await createSource(sourceType, cleanTitle);
      setSources((current) => [...current, source]);
      setTitle("");
      setCaptureFeedback({
        kind: "success",
        message: `Added “${source.title}” as a ${sourceTypeLabels[source.source_type].toLowerCase()}.`,
      });
    } catch {
      setCaptureFeedback({
        kind: "error",
        message: "The source could not be saved. Check the local service and try again.",
      });
    } finally {
      setIsSavingSource(false);
    }
  }

  async function handleDocumentImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!serviceReady || !document || isImporting) return;

    const cleanTitle = importTitle.trim();
    if (!cleanTitle) {
      setImportFeedback({ kind: "error", message: "Confirm a title before saving this document." });
      importTitleInput.current?.focus();
      return;
    }

    setImportStage("saving");
    setImportFeedback(null);
    let imported;
    try {
      imported = await createDocumentImport(importSourceType, cleanTitle, document);
    } catch (error) {
      const problem = problemFrom(error);
      setImportStage("selected");
      if (problem?.code === "duplicate" && typeof problem.source_id === "number") {
        const opened = await openSource(problem.source_id);
        if (opened) {
          clearImportSelection();
          setDetailNotice({
            kind: "warning",
            message: "This document is already in your library; the existing source is open.",
          });
        }
        return;
      }
      setImportFeedback({ kind: "error", message: importFailureMessage(error) });
      return;
    }

    setSources((current) => [
      ...current.filter((source) => source.id !== imported.source.id),
      imported.source,
    ]);
    setImportStage("converting");
    try {
      const converted = await convertAttachment(imported.attachment.id);
      setSelectedSource(detailForImport(imported.source, converted));
      setSourceError(null);
      if (converted.conversion_status === "succeeded") {
        setDetailNotice({ kind: "success", message: "The original and extracted text are saved locally." });
      } else {
        setDetailNotice({
          kind: "warning",
          message: `The original is saved locally. ${converted.conversion_message ?? conversionStatusLabels[converted.conversion_status]}`,
        });
      }
    } catch {
      setSelectedSource(detailForImport(imported.source, imported.attachment));
      setSourceError(null);
      setDetailNotice({
        kind: "warning",
        message: "The original is saved locally, but extraction did not finish. You can retry below.",
      });
    } finally {
      clearImportSelection();
    }
  }

  async function retryExtraction(attachmentId: number) {
    setRetryingAttachmentId(attachmentId);
    setDetailNotice(null);
    resetExtractedText();
    try {
      const attachment = await convertAttachment(attachmentId);
      setSelectedSource((current) =>
        current
          ? {
              ...current,
              attachments: current.attachments.map((item) =>
                item.id === attachment.id ? attachment : item,
              ),
            }
          : current,
      );
      if (attachment.conversion_status === "succeeded") {
        setDetailNotice({ kind: "success", message: "Extracted text is now saved locally." });
      } else {
        setDetailNotice({
          kind: "warning",
          message: attachment.conversion_message ?? conversionStatusLabels[attachment.conversion_status],
        });
      }
    } catch (error) {
      const problem = problemFrom(error);
      setDetailNotice({
        kind: "error",
        message: problem?.message ?? "Extraction could not be retried. Check the local service and try again.",
      });
    } finally {
      setRetryingAttachmentId(null);
    }
  }

  async function toggleExtractedText(attachmentId: number) {
    if (extractedText?.attachment_id === attachmentId) {
      resetExtractedText();
      return;
    }

    const requestId = textRequest.current + 1;
    textRequest.current = requestId;
    setLoadingTextAttachmentId(attachmentId);
    setTextError(null);
    setExtractedText(null);
    try {
      const text = await getExtractedText(attachmentId);
      if (textRequest.current !== requestId) return;
      setExtractedText(text);
    } catch (error) {
      if (textRequest.current !== requestId) return;
      const problem = problemFrom(error);
      setTextError(problem?.message ?? "The extracted text could not be opened.");
    } finally {
      if (textRequest.current === requestId) setLoadingTextAttachmentId(null);
    }
  }

  async function removeFailedAttachment(attachmentId: number, filename: string) {
    const confirmed = window.confirm(
      `Remove “${filename}” and its saved local files? This cannot be undone.`,
    );
    if (!confirmed) return;

    setRemovingAttachmentId(attachmentId);
    setDetailNotice(null);
    try {
      await removeAttachment(attachmentId);
      setSelectedSource((current) =>
        current
          ? {
              ...current,
              attachments: current.attachments.filter((item) => item.id !== attachmentId),
            }
          : current,
      );
      setDetailNotice({ kind: "success", message: `Removed “${filename}” and its saved files.` });
    } catch (error) {
      const problem = problemFrom(error);
      if (problem?.code === "attachment_cleanup_incomplete") {
        setSelectedSource((current) =>
          current
            ? {
                ...current,
                attachments: current.attachments.filter((item) => item.id !== attachmentId),
              }
            : current,
        );
        setDetailNotice({
          kind: "warning",
          message: problem.message ?? "The document was removed, but temporary cleanup did not finish.",
        });
      } else {
        setDetailNotice({
          kind: "error",
          message: problem?.message ?? "The failed document could not be removed. Try again.",
        });
      }
    } finally {
      setRemovingAttachmentId(null);
    }
  }

  async function saveSourceMetadata(sourceId: number, metadata: SourceUpdate): Promise<boolean> {
    setIsSavingMetadata(true);
    setDetailNotice(null);
    try {
      const updated = await updateSource(sourceId, metadata);
      setSelectedSource(updated);
      setSources((current) => [
        ...current.filter((source) => source.id !== updated.id),
        updated,
      ]);
      setDetailNotice({ kind: "success", message: "Source metadata saved." });
      return true;
    } catch (error) {
      setDetailNotice({
        kind: "error",
        message: apiFailureMessage(
          error,
          "Source metadata could not be saved. Check the local service and try again.",
        ),
      });
      return false;
    } finally {
      setIsSavingMetadata(false);
    }
  }

  return (
    <div className="app-shell" data-theme={isDarkMode ? "dark" : "light"}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">L</span>
          <span>Litrev</span>
        </div>
        <nav aria-label="Workspace">
          <button className="nav-item active" onClick={showLibrary} type="button">
            <span>Library</span>
            <span className="count">{sources.length}</span>
          </button>
          <button className="nav-item" type="button" disabled>
            Workbench
          </button>
          <button className="nav-item" type="button" disabled>
            Research map
          </button>
        </nav>
        <div className={`service-status ${serviceStatus}`} role="status">
          <span aria-hidden="true" className="status-dot" />
          {serviceStatusLabels[serviceStatus]}
        </div>
      </aside>

      <div className="workspace">
        <header className="page-header">
          <div>
            <p className="eyebrow">{selectedSource ? "Source detail" : "Research workspace"}</p>
            <h1>{selectedSource?.title ?? "Your library"}</h1>
            <p>
              {selectedSource
                ? "Review its metadata, saved documents, and extracted text."
                : "Collect papers and keep every idea connected to its source."}
            </p>
          </div>
          <button
            aria-label={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
            aria-pressed={isDarkMode}
            className="theme-toggle"
            onClick={() => setIsDarkMode((current) => !current)}
            title={isDarkMode ? "Switch to light mode" : "Switch to dark mode"}
            type="button"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
            </svg>
          </button>
        </header>

        <main>
          {serviceError && (
            <div className="error-message service-error" role="alert">
              <span>{serviceError}</span>
              <button onClick={retryService} type="button">
                Retry local service
              </button>
            </div>
          )}

          {isLoadingSource ? (
            <p className="loading-message" role="status">
              Opening source…
            </p>
          ) : selectedSource ? (
            <SourceDetailScreen
              detailNotice={detailNotice}
              extractedText={extractedText}
              isSavingMetadata={isSavingMetadata}
              key={selectedSource.id}
              loadingTextAttachmentId={loadingTextAttachmentId}
              onBack={showLibrary}
              onRemove={removeFailedAttachment}
              onRetry={retryExtraction}
              onSaveMetadata={saveSourceMetadata}
              onToggleText={toggleExtractedText}
              removingAttachmentId={removingAttachmentId}
              retryingAttachmentId={retryingAttachmentId}
              source={selectedSource}
              textError={textError}
            />
          ) : (
            <>
              <section className="capture-panel" aria-labelledby="capture-heading">
                <div className="capture-copy">
                  <p className="eyebrow">Quick capture</p>
                  <h2 id="capture-heading">Add a source</h2>
                  <p>Save a book or paper by typing its title.</p>
                </div>
                <form aria-busy={isSavingSource} noValidate onSubmit={handleSubmit}>
                  <div className="capture-fields">
                    <div className="form-field">
                      <label htmlFor="source-type">Type</label>
                      <select
                        id="source-type"
                        value={sourceType}
                        onChange={(event) =>
                          setSourceType(event.target.value as CapturableSourceType)
                        }
                        disabled={isSavingSource}
                      >
                        <option value="paper">Paper</option>
                        <option value="book">Book</option>
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="source-title">Title</label>
                      <input
                        aria-describedby={captureFeedback ? "capture-feedback" : undefined}
                        aria-invalid={captureFeedback?.kind === "error"}
                        autoComplete="off"
                        disabled={isSavingSource}
                        id="source-title"
                        maxLength={500}
                        onChange={(event) => {
                          setTitle(event.target.value);
                          setCaptureFeedback(null);
                        }}
                        placeholder="e.g. The Structure of Scientific Revolutions"
                        ref={titleInput}
                        value={title}
                      />
                    </div>
                    <button disabled={!serviceReady || isSavingSource} type="submit">
                      {isSavingSource ? "Adding…" : "Add to library"}
                    </button>
                  </div>
                  {captureFeedback && (
                    <p
                      className={`capture-feedback ${captureFeedback.kind}`}
                      id="capture-feedback"
                      role={captureFeedback.kind === "error" ? "alert" : "status"}
                    >
                      {captureFeedback.message}
                    </p>
                  )}
                </form>
              </section>

              <section className="document-panel" aria-labelledby="document-heading">
                <div className="document-copy">
                  <p className="eyebrow">Local document import</p>
                  <h2 id="document-heading">Add a document</h2>
                  <p>Inspect the selected file, confirm its source, then save and extract it locally.</p>
                </div>
                <form aria-busy={isImporting} noValidate onSubmit={handleDocumentImport}>
                  <label htmlFor="document-file">Choose a document</label>
                  <input
                    id="document-file"
                    type="file"
                    accept=".pdf,.doc,.docx,.odt,.rtf,.epub,.ppt,.pptx,.xls,.xlsx,.ods,.odp,.csv"
                    disabled={isImporting}
                    ref={documentInput}
                    onChange={(event) => {
                      const selected = event.target.files?.[0] ?? null;
                      setDocument(selected);
                      setImportTitle(selected ? titleFromFilename(selected.name) : "");
                      setImportStage(selected ? "selected" : "idle");
                      setImportFeedback(null);
                    }}
                  />

                  {document && (
                    <div className="file-inspection">
                      <p className="inspection-label">Selected document</p>
                      <dl>
                        <div>
                          <dt>File</dt>
                          <dd>{document.name}</dd>
                        </div>
                        <div>
                          <dt>Detected type</dt>
                          <dd>{detectedFileType(document)}</dd>
                        </div>
                        <div>
                          <dt>Size</dt>
                          <dd>{formatByteSize(document.size)}</dd>
                        </div>
                      </dl>
                      <div className="import-confirmation">
                        <div className="form-field">
                          <label htmlFor="import-source-type">Source type</label>
                          <select
                            disabled={isImporting}
                            id="import-source-type"
                            onChange={(event) =>
                              setImportSourceType(event.target.value as CapturableSourceType)
                            }
                            value={importSourceType}
                          >
                            <option value="paper">Paper</option>
                            <option value="book">Book</option>
                          </select>
                        </div>
                        <div className="form-field">
                          <label htmlFor="import-source-title">Source title</label>
                          <input
                            aria-describedby={importFeedback ? "import-feedback" : undefined}
                            aria-invalid={importFeedback?.kind === "error"}
                            autoComplete="off"
                            disabled={isImporting}
                            id="import-source-title"
                            maxLength={500}
                            onChange={(event) => {
                              setImportTitle(event.target.value);
                              setImportFeedback(null);
                            }}
                            ref={importTitleInput}
                            value={importTitle}
                          />
                        </div>
                        <button disabled={!serviceReady || isImporting} type="submit">
                          {importStage === "saving"
                            ? "Saving original…"
                            : importStage === "converting"
                              ? "Extracting text…"
                              : "Save and extract"}
                        </button>
                      </div>
                      <ol className="import-progress" aria-label="Import progress" aria-live="polite">
                        <li className={importStage === "selected" ? "current" : "complete"}>
                          <span>1</span> Inspect and confirm
                        </li>
                        <li
                          className={
                            importStage === "saving"
                              ? "current"
                              : importStage === "converting"
                                ? "complete"
                                : ""
                          }
                        >
                          <span>2</span> Save original locally
                        </li>
                        <li className={importStage === "converting" ? "current" : ""}>
                          <span>3</span> Extract text with Anydoc
                        </li>
                      </ol>
                    </div>
                  )}
                  {importFeedback && (
                    <p
                      className={`capture-feedback ${importFeedback.kind}`}
                      id="import-feedback"
                      role={importFeedback.kind === "error" ? "alert" : "status"}
                    >
                      {importFeedback.message}
                    </p>
                  )}
                </form>
              </section>

              {sourceError && (
                <p className="error-message" role="alert">
                  {sourceError}
                </p>
              )}

              <section className="library" aria-labelledby="library-heading">
                <div className="section-heading">
                  <h2 id="library-heading">Sources</h2>
                  <span aria-live="polite">
                    {visibleSources.length} of {sources.length} sources
                  </span>
                </div>
                {sources.length > 0 && (
                  <div className="discovery-controls" role="search">
                    <div className="form-field discovery-search">
                      <label htmlFor="library-search">Search sources</label>
                      <input
                        autoComplete="off"
                        id="library-search"
                        onChange={(event) => setSearchText(event.target.value)}
                        placeholder="Title, author, venue, or DOI"
                        ref={librarySearchInput}
                        type="search"
                        value={searchText}
                      />
                    </div>
                    <div className="form-field">
                      <label htmlFor="library-sort">Sort by</label>
                      <select
                        id="library-sort"
                        onChange={(event) => setDiscoverySort(event.target.value as DiscoverySort)}
                        value={discoverySort}
                      >
                        <option value="title">Title A–Z</option>
                        <option value="publication-year">Newest publication year</option>
                        <option value="recently-added">Recently added</option>
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="library-source-type">Source type</label>
                      <select
                        id="library-source-type"
                        onChange={(event) =>
                          setSourceTypeFilter(event.target.value as SourceType | "all")
                        }
                        value={sourceTypeFilter}
                      >
                        <option value="all">All types</option>
                        <option value="paper">Paper</option>
                        <option value="book">Book</option>
                        <option value="other">Other</option>
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="library-reading-status">Reading status</label>
                      <select
                        id="library-reading-status"
                        onChange={(event) =>
                          setReadingStatusFilter(event.target.value as ReadingStatus | "all")
                        }
                        value={readingStatusFilter}
                      >
                        <option value="all">All statuses</option>
                        <option value="unread">Unread</option>
                        <option value="reading">Reading</option>
                        <option value="read">Read</option>
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="library-tag">Tag</label>
                      <select
                        disabled={availableTags.length === 0}
                        id="library-tag"
                        onChange={(event) => setTagFilter(event.target.value)}
                        value={tagFilter}
                      >
                        <option value="">All tags</option>
                        {availableTags.map((tag) => (
                          <option key={tag} value={tag}>
                            {tag}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="form-field">
                      <label htmlFor="library-collection">Collection</label>
                      <select
                        disabled={availableCollections.length === 0}
                        id="library-collection"
                        onChange={(event) => setCollectionFilter(event.target.value)}
                        value={collectionFilter}
                      >
                        <option value="">All collections</option>
                        {availableCollections.map((collection) => (
                          <option key={collection} value={collection}>
                            {collection}
                          </option>
                        ))}
                      </select>
                    </div>
                    {discoveryControlsActive && (
                      <button onClick={clearDiscoveryControls} type="button">
                        Clear all
                      </button>
                    )}
                  </div>
                )}
                {sources.length === 0 ? (
                  <div className="empty-state">
                    <span className="empty-glyph">↗</span>
                    <h3>Start with one useful source</h3>
                    <p>Add its title or import a local document.</p>
                  </div>
                ) : visibleSources.length === 0 ? (
                  <div className="empty-state">
                    <span className="empty-glyph">∅</span>
                    <h3>No matching sources</h3>
                    <p>Adjust the search or filters to see more of your library.</p>
                    <button onClick={clearDiscoveryControls} type="button">
                      Clear filters
                    </button>
                  </div>
                ) : (
                  <ul className="source-list">
                    {visibleSources.map((source) => (
                      <li key={source.id}>
                        <button onClick={() => void openSource(source.id)} type="button">
                          <span className="source-summary">
                            <strong>{source.title}</strong>
                            <span>{sourceListDescription(source)}</span>
                            {(source.tags.length > 0 || source.collections.length > 0) && (
                              <span className="source-organization-summary">
                                {[
                                  source.tags.length > 0 ? `Tags: ${source.tags.join(", ")}` : "",
                                  source.collections.length > 0
                                    ? `Collections: ${source.collections.join(", ")}`
                                    : "",
                                ]
                                  .filter(Boolean)
                                  .join(" · ")}
                              </span>
                            )}
                          </span>
                          <span className="source-kind">{sourceTypeLabels[source.source_type]}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

interface SourceDetailScreenProps {
  detailNotice: Feedback | null;
  extractedText: ExtractedText | null;
  isSavingMetadata: boolean;
  loadingTextAttachmentId: number | null;
  onBack: () => void;
  onRemove: (attachmentId: number, filename: string) => Promise<void>;
  onRetry: (attachmentId: number) => Promise<void>;
  onSaveMetadata: (sourceId: number, metadata: SourceUpdate) => Promise<boolean>;
  onToggleText: (attachmentId: number) => Promise<void>;
  removingAttachmentId: number | null;
  retryingAttachmentId: number | null;
  source: SourceDetail;
  textError: string | null;
}

function SourceDetailScreen({
  detailNotice,
  extractedText,
  isSavingMetadata,
  loadingTextAttachmentId,
  onBack,
  onRemove,
  onRetry,
  onSaveMetadata,
  onToggleText,
  removingAttachmentId,
  retryingAttachmentId,
  source,
  textError,
}: SourceDetailScreenProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  const documentsHeading = useRef<HTMLHeadingElement>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const editTitle = useRef<HTMLInputElement>(null);
  const [isEditingMetadata, setIsEditingMetadata] = useState(false);
  const [metadataDraft, setMetadataDraft] = useState<SourceUpdate>(() =>
    metadataFromSource(source),
  );
  const wasEditingMetadata = useRef(false);
  const previousAttachmentState = useRef({
    sourceId: source.id,
    count: source.attachments.length,
  });

  useEffect(() => {
    heading.current?.focus();
  }, [source.id]);

  useEffect(() => {
    if (isEditingMetadata) {
      editTitle.current?.focus();
    } else if (wasEditingMetadata.current) {
      editButton.current?.focus();
    }
    wasEditingMetadata.current = isEditingMetadata;
  }, [isEditingMetadata]);

  useEffect(() => {
    const previous = previousAttachmentState.current;
    if (previous.sourceId === source.id && source.attachments.length < previous.count) {
      documentsHeading.current?.focus();
    }
    previousAttachmentState.current = {
      sourceId: source.id,
      count: source.attachments.length,
    };
  }, [source.id, source.attachments.length]);

  function beginMetadataEdit() {
    setMetadataDraft(metadataFromSource(source));
    setIsEditingMetadata(true);
  }

  function cancelMetadataEdit() {
    setMetadataDraft(metadataFromSource(source));
    setIsEditingMetadata(false);
  }

  async function submitMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = metadataDraft.title.trim();
    if (!title) {
      editTitle.current?.focus();
      return;
    }
    const saved = await onSaveMetadata(source.id, {
      ...metadataDraft,
      title,
      authors: metadataDraft.authors.map((author) => author.trim()).filter(Boolean),
      tags: metadataDraft.tags.map((tag) => tag.trim()).filter(Boolean),
      collections: metadataDraft.collections
        .map((collection) => collection.trim())
        .filter(Boolean),
    });
    if (saved) setIsEditingMetadata(false);
  }

  return (
    <section className="source-detail" aria-labelledby="source-detail-heading">
      <button
        className="back-button"
        disabled={isSavingMetadata}
        onClick={onBack}
        type="button"
      >
        ← Back to library
      </button>
      <div className="source-detail-heading">
        <div>
          <p className="eyebrow">{sourceTypeLabels[source.source_type]}</p>
          <h2 id="source-detail-heading" ref={heading} tabIndex={-1}>
            {source.title}
          </h2>
        </div>
        <span>{readingStatusLabels[source.reading_status]}</span>
      </div>

      {detailNotice && (
        <p
          className={`detail-notice ${detailNotice.kind}`}
          role={detailNotice.kind === "error" ? "alert" : "status"}
        >
          {detailNotice.message}
        </p>
      )}

      <section className="metadata-section" aria-labelledby="metadata-heading">
        <div className="metadata-heading">
          <h3 id="metadata-heading">Metadata</h3>
          {!isEditingMetadata && (
            <button onClick={beginMetadataEdit} ref={editButton} type="button">
              Edit source
            </button>
          )}
        </div>
        {isEditingMetadata ? (
          <form aria-busy={isSavingMetadata} className="metadata-form" onSubmit={submitMetadata}>
            <div className="metadata-form-grid">
              <div className="form-field">
                <label htmlFor="edit-source-type">Type</label>
                <select
                  disabled={isSavingMetadata}
                  id="edit-source-type"
                  onChange={(event) =>
                    setMetadataDraft((current) => ({
                      ...current,
                      source_type: event.target.value as SourceType,
                    }))
                  }
                  value={metadataDraft.source_type}
                >
                  <option value="paper">Paper</option>
                  <option value="book">Book</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-title">Title</label>
                <input
                  disabled={isSavingMetadata}
                  id="edit-source-title"
                  maxLength={500}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({ ...current, title: event.target.value }))
                  }
                  ref={editTitle}
                  required
                  value={metadataDraft.title}
                />
              </div>
              <div className="form-field metadata-authors-field">
                <label htmlFor="edit-source-authors">Authors (one per line)</label>
                <textarea
                  disabled={isSavingMetadata}
                  id="edit-source-authors"
                  onChange={(event) =>
                    setMetadataDraft((current) => ({
                      ...current,
                      authors: event.target.value.split("\n"),
                    }))
                  }
                  rows={3}
                  value={metadataDraft.authors.join("\n")}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-year">Year</label>
                <input
                  disabled={isSavingMetadata}
                  id="edit-source-year"
                  max={9999}
                  min={1}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({
                      ...current,
                      publication_year: event.target.value ? Number(event.target.value) : null,
                    }))
                  }
                  type="number"
                  value={metadataDraft.publication_year ?? ""}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-venue">Venue</label>
                <input
                  disabled={isSavingMetadata}
                  id="edit-source-venue"
                  maxLength={500}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({ ...current, venue: event.target.value }))
                  }
                  value={metadataDraft.venue ?? ""}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-doi">DOI</label>
                <input
                  disabled={isSavingMetadata}
                  id="edit-source-doi"
                  maxLength={255}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({ ...current, doi: event.target.value }))
                  }
                  value={metadataDraft.doi ?? ""}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-url">URL</label>
                <input
                  disabled={isSavingMetadata}
                  id="edit-source-url"
                  maxLength={2048}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({ ...current, url: event.target.value }))
                  }
                  type="url"
                  value={metadataDraft.url ?? ""}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-language">Language</label>
                <input
                  disabled={isSavingMetadata}
                  id="edit-source-language"
                  maxLength={35}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({ ...current, language: event.target.value }))
                  }
                  placeholder="en"
                  value={metadataDraft.language ?? ""}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-reading-status">Reading status</label>
                <select
                  disabled={isSavingMetadata}
                  id="edit-source-reading-status"
                  onChange={(event) =>
                    setMetadataDraft((current) => ({
                      ...current,
                      reading_status: event.target.value as ReadingStatus,
                    }))
                  }
                  value={metadataDraft.reading_status}
                >
                  <option value="unread">Unread</option>
                  <option value="reading">Reading</option>
                  <option value="read">Read</option>
                </select>
              </div>
              <div className="form-field metadata-abstract-field">
                <label htmlFor="edit-source-abstract">Abstract</label>
                <textarea
                  disabled={isSavingMetadata}
                  id="edit-source-abstract"
                  maxLength={100000}
                  onChange={(event) =>
                    setMetadataDraft((current) => ({ ...current, abstract: event.target.value }))
                  }
                  rows={5}
                  value={metadataDraft.abstract ?? ""}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-tags">Tags (one per line)</label>
                <textarea
                  disabled={isSavingMetadata}
                  id="edit-source-tags"
                  onChange={(event) =>
                    setMetadataDraft((current) => ({
                      ...current,
                      tags: event.target.value.split("\n"),
                    }))
                  }
                  rows={3}
                  value={metadataDraft.tags.join("\n")}
                />
              </div>
              <div className="form-field">
                <label htmlFor="edit-source-collections">Collections (one per line)</label>
                <textarea
                  disabled={isSavingMetadata}
                  id="edit-source-collections"
                  onChange={(event) =>
                    setMetadataDraft((current) => ({
                      ...current,
                      collections: event.target.value.split("\n"),
                    }))
                  }
                  rows={3}
                  value={metadataDraft.collections.join("\n")}
                />
              </div>
            </div>
            <div className="metadata-form-actions">
              <button disabled={isSavingMetadata} type="submit">
                {isSavingMetadata ? "Saving…" : "Save changes"}
              </button>
              <button disabled={isSavingMetadata} onClick={cancelMetadataEdit} type="button">
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <dl className="metadata-grid">
            <div className="metadata-authors">
              <dt>Authors</dt>
              <dd>{source.authors.length ? source.authors.join(", ") : "Not added"}</dd>
            </div>
            <div>
              <dt>Year</dt>
              <dd>{source.publication_year ?? "Not added"}</dd>
            </div>
            <div>
              <dt>Venue</dt>
              <dd>{source.venue ?? "Not added"}</dd>
            </div>
            <div>
              <dt>DOI</dt>
              <dd>{source.doi ?? "Not added"}</dd>
            </div>
            <div>
              <dt>URL</dt>
              <dd>
                {source.url ? (
                  <a href={source.url} rel="noreferrer" target="_blank">
                    {source.url}
                  </a>
                ) : (
                  "Not added"
                )}
              </dd>
            </div>
            <div>
              <dt>Language</dt>
              <dd>{source.language ?? "Not added"}</dd>
            </div>
            <div>
              <dt>Reading status</dt>
              <dd>{readingStatusLabels[source.reading_status]}</dd>
            </div>
            <div className="metadata-tags">
              <dt>Tags</dt>
              <dd>{source.tags.length ? source.tags.join(", ") : "Not added"}</dd>
            </div>
            <div className="metadata-collections">
              <dt>Collections</dt>
              <dd>{source.collections.length ? source.collections.join(", ") : "Not added"}</dd>
            </div>
            <div className="metadata-abstract">
              <dt>Abstract</dt>
              <dd>{source.abstract ?? "Not added"}</dd>
            </div>
          </dl>
        )}
      </section>

      <div className="attachment-heading">
        <h3 ref={documentsHeading} tabIndex={-1}>Documents</h3>
        <span>{source.attachments.length}</span>
      </div>
      {source.attachments.length === 0 ? (
        <div className="attachment-empty">
          <p>No document is attached to this source yet.</p>
        </div>
      ) : (
        <ul className="attachment-list">
          {source.attachments.map((attachment) => {
            const succeeded = attachment.conversion_status === "succeeded";
            const textIsOpen = extractedText?.attachment_id === attachment.id;
            const textIsLoading = loadingTextAttachmentId === attachment.id;
            return (
              <li key={attachment.id}>
                <div className="attachment-summary">
                  <div>
                    <strong>{attachment.original_filename}</strong>
                    <span>
                      {(attachment.detected_format ?? "Unknown format").toUpperCase()} · {formatByteSize(attachment.byte_size)}
                    </span>
                  </div>
                  <span className={`conversion-status ${succeeded ? "success" : attachment.conversion_status}`}>
                    {conversionStatusLabels[attachment.conversion_status]}
                  </span>
                </div>
                {attachment.conversion_message && (
                  <p className="conversion-message">{attachment.conversion_message}</p>
                )}
                <div className="attachment-actions">
                  {succeeded ? (
                    <button
                      disabled={loadingTextAttachmentId !== null}
                      onClick={() => void onToggleText(attachment.id)}
                      type="button"
                    >
                      {textIsLoading
                        ? "Opening text…"
                        : textIsOpen
                          ? "Hide extracted text"
                          : "View extracted text"}
                    </button>
                  ) : (
                    <button
                      disabled={retryingAttachmentId !== null || removingAttachmentId !== null}
                      onClick={() => void onRetry(attachment.id)}
                      type="button"
                    >
                      {retryingAttachmentId === attachment.id ? "Retrying…" : "Retry extraction"}
                    </button>
                  )}
                  {attachment.can_remove && (
                    <button
                      className="danger-button"
                      disabled={retryingAttachmentId !== null || removingAttachmentId !== null}
                      onClick={() => void onRemove(attachment.id, attachment.original_filename)}
                      type="button"
                    >
                      {removingAttachmentId === attachment.id ? "Removing…" : "Remove failed document"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {textError && (
        <p className="error-message" role="alert">
          {textError}
        </p>
      )}
      {extractedText && (
        <section className="document-preview" aria-labelledby="preview-heading">
          <div className="section-heading">
            <h2 id="preview-heading">Extracted text</h2>
            <span>Saved locally</span>
          </div>
          <article>
            <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
              {extractedText.markdown}
            </ReactMarkdown>
          </article>
        </section>
      )}
    </section>
  );
}
