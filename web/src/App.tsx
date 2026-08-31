import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  ApiError,
  applyDoiMetadataLookup,
  convertAttachment,
  createBibliographyImport,
  createDoiMetadataLookup,
  createDocumentImport,
  createSource,
  getBibliographyExport,
  getExtractedText,
  getHealth,
  getSource,
  getSources,
  removeAttachment,
  removeSource,
  updateSource,
  type ApiProblem,
  type Attachment,
  type BibliographyFormat,
  type ConversionStatus,
  type DoiMetadataField,
  type DoiMetadataLookup,
  type ExtractedText,
  type ReadingStatus,
  type Source,
  type SourceDetail,
  type SourceIdentifier,
  type SourceType,
  type SourceUpdate,
} from "./api";
import "./styles.css";

const themeStorageKey = "litrev-theme";
const serviceStartupTimeoutMs = 5_000;
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

const bibliographyFormatLabels: Record<BibliographyFormat, string> = {
  bibtex: "BibTeX",
  ris: "RIS",
  "csl-json": "CSL JSON",
};

const bibliographyExportFilenames: Record<BibliographyFormat, string> = {
  bibtex: "litrev-library.bib",
  ris: "litrev-library.ris",
  "csl-json": "litrev-library.json",
};

const doiMetadataFieldLabels: Record<DoiMetadataField, string> = {
  source_type: "Source type",
  title: "Title",
  authors: "Authors",
  publication_year: "Publication year",
  venue: "Venue",
  url: "URL",
  abstract: "Abstract",
  language: "Language",
  identifiers: "Identifiers",
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
    identifiers: source.identifiers.map((identifier) => ({ ...identifier })),
  };
}

function identifierTypeLabel(identifierType: string): string {
  return identifierType === "arxiv" ? "arXiv" : identifierType.toLocaleUpperCase();
}

function identifierDraftFrom(source: Source): string {
  return source.identifiers
    .map((identifier) => `${identifier.identifier_type}: ${identifier.value}`)
    .join("\n");
}

function doiMetadataSourceValue(source: Source, field: DoiMetadataField): string {
  if (field === "source_type") return sourceTypeLabels[source.source_type];
  if (field === "authors") return source.authors.join(", ") || "Not added";
  if (field === "identifiers") {
    return (
      source.identifiers
        .map(
          (identifier) => `${identifierTypeLabel(identifier.identifier_type)} ${identifier.value}`,
        )
        .join(", ") || "Not added"
    );
  }
  const value = source[field];
  return value === null || value === "" ? "Not added" : String(value);
}

function doiMetadataProposalValue(
  lookup: DoiMetadataLookup,
  field: DoiMetadataField,
): string {
  const value = lookup.proposal[field];
  if (field === "source_type" && typeof value === "string") {
    return sourceTypeLabels[value as SourceType];
  }
  if (field === "authors" && Array.isArray(value)) return value.join(", ");
  if (field === "identifiers" && Array.isArray(value)) {
    return value
      .map((identifier) => {
        const typedIdentifier = identifier as SourceIdentifier;
        return `${identifierTypeLabel(typedIdentifier.identifier_type)} ${typedIdentifier.value}`;
      })
      .join(", ");
  }
  return String(value);
}

function formatLookupDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function parseIdentifierDraft(value: string):
  | { identifiers: SourceIdentifier[]; error: null }
  | { identifiers: null; error: string } {
  const identifiers: SourceIdentifier[] = [];
  for (const [index, rawLine] of value.split("\n").entries()) {
    const line = rawLine.trim();
    if (!line) continue;
    const separator = line.indexOf(":");
    const identifierType = line.slice(0, separator).trim();
    const identifierValue = line.slice(separator + 1).trim();
    if (separator < 1 || !identifierType || !identifierValue) {
      return {
        identifiers: null,
        error: `Identifier line ${index + 1} must use “type: value”.`,
      };
    }
    identifiers.push({ identifier_type: identifierType, value: identifierValue });
  }
  return { identifiers, error: null };
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
  return { ...source, attachments: [attachment], metadata_provenance: [] };
}

export default function App() {
  const [sources, setSources] = useState<Source[]>([]);
  const [searchText, setSearchText] = useState("");
  const [discoverySort, setDiscoverySort] = useState<DiscoverySort>("title");
  const [sourceTypeFilter, setSourceTypeFilter] = useState<SourceType | "all">("all");
  const [readingStatusFilter, setReadingStatusFilter] = useState<ReadingStatus | "all">("all");
  const [tagFilter, setTagFilter] = useState("");
  const [collectionFilter, setCollectionFilter] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
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
  const [bibliography, setBibliography] = useState<File | null>(null);
  const [isImportingBibliography, setIsImportingBibliography] = useState(false);
  const [bibliographyFeedback, setBibliographyFeedback] = useState<Feedback | null>(null);
  const [bibliographyExportFormat, setBibliographyExportFormat] =
    useState<BibliographyFormat>("bibtex");
  const [isExportingBibliography, setIsExportingBibliography] = useState(false);
  const [bibliographyExportFeedback, setBibliographyExportFeedback] = useState<Feedback | null>(
    null,
  );
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [isLoadingSource, setIsLoadingSource] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [detailNotice, setDetailNotice] = useState<Feedback | null>(null);
  const [libraryNotice, setLibraryNotice] = useState<Feedback | null>(null);
  const [retryingAttachmentId, setRetryingAttachmentId] = useState<number | null>(null);
  const [removingAttachmentId, setRemovingAttachmentId] = useState<number | null>(null);
  const [isRemovingSource, setIsRemovingSource] = useState(false);
  const [isSavingMetadata, setIsSavingMetadata] = useState(false);
  const [extractedText, setExtractedText] = useState<ExtractedText | null>(null);
  const [loadingTextAttachmentId, setLoadingTextAttachmentId] = useState<number | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [isDarkMode, setIsDarkMode] = useState(loadDarkModePreference);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const titleInput = useRef<HTMLInputElement>(null);
  const importTitleInput = useRef<HTMLInputElement>(null);
  const documentActionButton = useRef<HTMLButtonElement>(null);
  const documentInput = useRef<HTMLInputElement>(null);
  const bibliographyInput = useRef<HTMLInputElement>(null);
  const bibliographyExportSelect = useRef<HTMLSelectElement>(null);
  const libraryHeading = useRef<HTMLHeadingElement>(null);
  const settingsHeading = useRef<HTMLHeadingElement>(null);
  const librarySearchInput = useRef<HTMLInputElement>(null);
  const filterToggleButton = useRef<HTMLButtonElement>(null);
  const sourceRequest = useRef(0);
  const textRequest = useRef(0);
  const serviceReady = serviceStatus === "ready";
  const isImporting = importStage === "saving" || importStage === "converting";
  const normalizedQuery = searchText.trim().toLocaleLowerCase();
  const activeFilterCount =
    Number(sourceTypeFilter !== "all") +
    Number(readingStatusFilter !== "all") +
    Number(tagFilter !== "") +
    Number(collectionFilter !== "");
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
  useEffect(() => {
    if (!isExportingBibliography && bibliographyExportFeedback?.kind === "error") {
      bibliographyExportSelect.current?.focus();
    }
  }, [bibliographyExportFeedback, isExportingBibliography]);
  useEffect(() => {
    if (document) importTitleInput.current?.focus();
  }, [document]);
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
    if (!selectedSource && libraryNotice) libraryHeading.current?.focus();
  }, [libraryNotice, selectedSource]);

  useEffect(() => {
    if (isSettingsOpen) settingsHeading.current?.focus();
  }, [isSettingsOpen]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const markServiceUnavailable = () => {
      window.clearTimeout(timeoutId);
      if (!active) return;
      setServiceStatus("unavailable");
      setServiceError("The local Litrev service is not available.");
    };
    const timeoutId = window.setTimeout(() => {
      controller.abort();
      markServiceUnavailable();
    }, serviceStartupTimeoutMs);

    Promise.all([getHealth(controller.signal), getSources(controller.signal)])
      .then(([, library]) => {
        window.clearTimeout(timeoutId);
        if (!active) return;
        setServiceStatus("ready");
        setServiceError(null);
        setSources(library);
      })
      .catch(() => {
        controller.abort();
        markServiceUnavailable();
      });

    return () => {
      active = false;
      window.clearTimeout(timeoutId);
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

  function clearSourceFilters() {
    setSourceTypeFilter("all");
    setReadingStatusFilter("all");
    setTagFilter("");
    setCollectionFilter("");
    filterToggleButton.current?.focus();
  }

  function resetExtractedText() {
    textRequest.current += 1;
    setLoadingTextAttachmentId(null);
    setExtractedText(null);
    setTextError(null);
  }

  function showLibrary() {
    sourceRequest.current += 1;
    setIsSettingsOpen(false);
    setSelectedSource(null);
    setIsLoadingSource(false);
    setSourceError(null);
    setDetailNotice(null);
    setLibraryNotice(null);
    resetExtractedText();
  }

  function showSettings() {
    sourceRequest.current += 1;
    setIsSettingsOpen(true);
    setSelectedSource(null);
    setIsLoadingSource(false);
    setSourceError(null);
    setDetailNotice(null);
    setLibraryNotice(null);
    resetExtractedText();
  }

  async function openSource(sourceId: number): Promise<boolean> {
    const requestId = sourceRequest.current + 1;
    sourceRequest.current = requestId;
    setIsLoadingSource(true);
    setSourceError(null);
    setDetailNotice(null);
    setLibraryNotice(null);
    setIsSettingsOpen(false);
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

  function cancelImportSelection() {
    clearImportSelection();
    documentActionButton.current?.focus();
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

  async function handleBibliographyImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isImportingBibliography) return;
    if (!bibliography) {
      setBibliographyFeedback({
        kind: "error",
        message: "Choose a BibTeX, RIS, or CSL JSON file to import.",
      });
      bibliographyInput.current?.focus();
      return;
    }
    if (!serviceReady) {
      setBibliographyFeedback({
        kind: "error",
        message:
          serviceStatus === "connecting"
            ? "The local service is still connecting. Try again when the sidebar says it is ready."
            : "The local service is unavailable. Use “Retry local service” above, then import again.",
      });
      return;
    }

    setIsImportingBibliography(true);
    setBibliographyFeedback(null);
    try {
      const result = await createBibliographyImport(bibliography);
      setSources((current) => {
        const importedIds = new Set(result.imported.map((source) => source.id));
        return [...current.filter((source) => !importedIds.has(source.id)), ...result.imported];
      });

      const importedCount = result.imported.length;
      const skippedCount = result.skipped.length;
      const importedSummary = `${importedCount} ${importedCount === 1 ? "source" : "sources"}`;
      const skippedSummary =
        `${skippedCount} duplicate ${skippedCount === 1 ? "DOI was" : "DOIs were"} skipped`;
      const duplicateNotice = skippedCount
        ? ` ${skippedSummary}; existing sources were not changed.`
        : "";
      setBibliographyFeedback({
        kind: importedCount > 0 ? "success" : "warning",
        message:
          importedCount > 0
            ? `Imported ${importedSummary} from “${bibliography.name}”.${duplicateNotice}`
            : `No new sources were imported from “${bibliography.name}”. ${skippedSummary}; existing sources were not changed.`,
      });
      setBibliography(null);
      if (bibliographyInput.current) bibliographyInput.current.value = "";
    } catch (error) {
      setBibliographyFeedback({
        kind: "error",
        message: apiFailureMessage(
          error,
          "The bibliography could not be imported. Check the local service and try again.",
        ),
      });
    } finally {
      setIsImportingBibliography(false);
    }
  }

  async function handleBibliographyExport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isExportingBibliography) return;
    if (!serviceReady) {
      setBibliographyExportFeedback({
        kind: "error",
        message:
          serviceStatus === "connecting"
            ? "The local service is still connecting. Try again when the sidebar says it is ready."
            : "The local service is unavailable. Use “Retry local service” above, then export again.",
      });
      return;
    }

    setIsExportingBibliography(true);
    setBibliographyExportFeedback(null);
    try {
      const bibliographyBlob = await getBibliographyExport(bibliographyExportFormat);
      const downloadUrl = URL.createObjectURL(bibliographyBlob);
      try {
        const download = window.document.createElement("a");
        download.href = downloadUrl;
        download.download = bibliographyExportFilenames[bibliographyExportFormat];
        download.hidden = true;
        window.document.body.append(download);
        download.click();
        download.remove();
      } finally {
        URL.revokeObjectURL(downloadUrl);
      }
      setBibliographyExportFeedback({
        kind: "success",
        message: `Downloaded the library as ${bibliographyFormatLabels[bibliographyExportFormat]}.`,
      });
    } catch (error) {
      setBibliographyExportFeedback({
        kind: "error",
        message: apiFailureMessage(
          error,
          "The library could not be exported. Check the local service and try again.",
        ),
      });
    } finally {
      setIsExportingBibliography(false);
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

  async function lookupSourceDoiMetadata(sourceId: number): Promise<DoiMetadataLookup> {
    return createDoiMetadataLookup(sourceId);
  }

  async function applySourceDoiMetadata(
    sourceId: number,
    lookupId: number,
    fields: DoiMetadataField[],
  ): Promise<SourceDetail> {
    const updated = await applyDoiMetadataLookup(sourceId, lookupId, fields);
    setSelectedSource(updated);
    setSources((current) => [
      ...current.filter((source) => source.id !== updated.id),
      updated,
    ]);
    return updated;
  }

  async function deleteSelectedSource(
    sourceId: number,
    sourceTitle: string,
    attachmentCount: number,
  ) {
    const documentSummary =
      attachmentCount === 0
        ? "It has no saved documents."
        : `This also deletes ${attachmentCount} saved ${attachmentCount === 1 ? "document" : "documents"}, including originals and extracted text.`;
    const confirmed = window.confirm(
      `Delete source “${sourceTitle}”? Its metadata and notes will be permanently removed, and it will be unlinked from tags and collections. ${documentSummary} This cannot be undone.`,
    );
    if (!confirmed) return;

    setIsRemovingSource(true);
    setDetailNotice(null);

    function showCommittedRemoval(notice: Feedback) {
      setSources((current) => current.filter((source) => source.id !== sourceId));
      sourceRequest.current += 1;
      setSelectedSource(null);
      setSourceError(null);
      resetExtractedText();
      setLibraryNotice(notice);
    }

    try {
      await removeSource(sourceId);
      showCommittedRemoval({
        kind: "success",
        message: `Deleted “${sourceTitle}” and its saved local data.`,
      });
    } catch (error) {
      const problem = problemFrom(error);
      if (problem?.code === "source_cleanup_incomplete") {
        showCommittedRemoval({
          kind: "warning",
          message:
            problem.message ??
            "The source was removed, but temporary file cleanup did not finish.",
        });
      } else {
        setDetailNotice({
          kind: "error",
          message: problem?.message ?? "The source could not be removed. Try again.",
        });
      }
    } finally {
      setIsRemovingSource(false);
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
          <button
            aria-current={isSettingsOpen ? undefined : "page"}
            className={`nav-item ${isSettingsOpen ? "" : "active"}`}
            disabled={isRemovingSource}
            onClick={showLibrary}
            type="button"
          >
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
        <div className="sidebar-footer">
          <button
            aria-current={isSettingsOpen ? "page" : undefined}
            className={`nav-item settings-nav-item ${isSettingsOpen ? "active" : ""}`}
            disabled={isRemovingSource}
            onClick={showSettings}
            type="button"
          >
            Settings
          </button>
          <div className={`service-status ${serviceStatus}`} role="status">
            <span aria-hidden="true" className="status-dot" />
            {serviceStatusLabels[serviceStatus]}
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="page-header">
          <div>
            <p className="eyebrow">
              {isSettingsOpen
                ? "Application preferences"
                : selectedSource
                  ? "Source detail"
                  : "Research workspace"}
            </p>
            <h1
              ref={isSettingsOpen ? settingsHeading : undefined}
              tabIndex={isSettingsOpen ? -1 : undefined}
            >
              {isSettingsOpen ? "Settings" : (selectedSource?.title ?? "Your library")}
            </h1>
            <p>
              {isSettingsOpen
                ? "Manage how Litrev works for you."
                : selectedSource
                  ? "Review its metadata, saved documents, and extracted text."
                  : "Collect papers and keep every idea connected to its source."}
            </p>
          </div>
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

          {isSettingsOpen ? (
            <section className="settings-panel" aria-labelledby="appearance-heading">
              <div>
                <p className="eyebrow">Appearance</p>
                <h2 id="appearance-heading">Color theme</h2>
                <p>Choose the theme used throughout your local workspace.</p>
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
            </section>
          ) : isLoadingSource ? (
            <p className="loading-message" role="status">
              Opening source…
            </p>
          ) : selectedSource ? (
            <SourceDetailScreen
              detailNotice={detailNotice}
              extractedText={extractedText}
              isRemovingSource={isRemovingSource}
              isSavingMetadata={isSavingMetadata}
              key={selectedSource.id}
              loadingTextAttachmentId={loadingTextAttachmentId}
              onApplyDoiMetadata={applySourceDoiMetadata}
              onBack={showLibrary}
              onDeleteSource={deleteSelectedSource}
              onRemove={removeFailedAttachment}
              onRetry={retryExtraction}
              onSaveMetadata={saveSourceMetadata}
              onLookupDoiMetadata={lookupSourceDoiMetadata}
              onToggleText={toggleExtractedText}
              removingAttachmentId={removingAttachmentId}
              retryingAttachmentId={retryingAttachmentId}
              source={selectedSource}
              textError={textError}
            />
          ) : (
            <>
              <section className="capture-panel" aria-labelledby="capture-heading">
                <div className="capture-heading">
                  <p className="eyebrow">Quick capture</p>
                  <h2 id="capture-heading">Add a source</h2>
                  <p>Enter a title, or start from a document on this device.</p>
                </div>
                <form
                  aria-busy={isSavingSource}
                  className="title-capture-form"
                  noValidate
                  onSubmit={handleSubmit}
                >
                  <div className="capture-bar">
                    <div className="form-field">
                      <label className="visually-hidden" htmlFor="source-type">
                        Type
                      </label>
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
                      <label className="visually-hidden" htmlFor="source-title">
                        Title
                      </label>
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
                        placeholder="Enter a book or paper title"
                        ref={titleInput}
                        value={title}
                      />
                    </div>
                    <button disabled={!serviceReady || isSavingSource} type="submit">
                      {isSavingSource ? "Adding…" : "Add source"}
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

                <div className="document-action">
                  <div>
                    <strong>Have the document?</strong>
                    <span>Import it for local storage and text extraction.</span>
                  </div>
                  <button
                    disabled={isImporting}
                    onClick={() => documentInput.current?.click()}
                    ref={documentActionButton}
                    type="button"
                  >
                    {document ? "Choose another" : "Import document"}
                  </button>
                  <label className="visually-hidden" htmlFor="document-file">
                    Choose a document
                  </label>
                  <input
                    className="visually-hidden"
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
                </div>

                {document && (
                  <form
                    aria-busy={isImporting}
                    className="document-review"
                    noValidate
                    onSubmit={handleDocumentImport}
                  >
                    <div className="document-review-heading">
                      <div>
                        <p className="inspection-label">Selected document</p>
                        <h3>{document.name}</h3>
                      </div>
                      <button disabled={isImporting} onClick={cancelImportSelection} type="button">
                        Cancel
                      </button>
                    </div>
                    <div className="file-inspection">
                      <dl>
                        <div>
                          <dt>Type</dt>
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
                )}
              </section>

              <details className="library-data-panel">
                <summary>
                  <span className="library-data-summary-copy">
                    <strong>Library data</strong>
                    <span>Import or export BibTeX, RIS, and CSL JSON</span>
                  </span>
                </summary>
                <div className="bibliography-workflows">
                  <form
                    aria-busy={isImportingBibliography}
                    aria-labelledby="bibliography-import-heading"
                    noValidate
                    onSubmit={handleBibliographyImport}
                  >
                    <h3 id="bibliography-import-heading">Import metadata</h3>
                    <div className="bibliography-fields">
                      <div className="form-field">
                        <label htmlFor="bibliography-file">Choose a bibliography</label>
                        <input
                          accept=".bib,.ris,.json"
                          aria-describedby={
                            bibliographyFeedback ? "bibliography-feedback" : undefined
                          }
                          aria-invalid={bibliographyFeedback?.kind === "error"}
                          disabled={isImportingBibliography}
                          id="bibliography-file"
                          onChange={(event) => {
                            setBibliography(event.target.files?.[0] ?? null);
                            setBibliographyFeedback(null);
                          }}
                          ref={bibliographyInput}
                          type="file"
                        />
                      </div>
                      <button disabled={isImportingBibliography} type="submit">
                        {isImportingBibliography ? "Importing…" : "Import bibliography"}
                      </button>
                    </div>
                    {bibliographyFeedback && (
                      <p
                        className={`capture-feedback ${bibliographyFeedback.kind}`}
                        id="bibliography-feedback"
                        role={bibliographyFeedback.kind === "error" ? "alert" : "status"}
                      >
                        {bibliographyFeedback.message}
                      </p>
                    )}
                  </form>
                  <form
                    aria-busy={isExportingBibliography}
                    aria-labelledby="bibliography-export-heading"
                    noValidate
                    onSubmit={handleBibliographyExport}
                  >
                    <h3 id="bibliography-export-heading">Export library</h3>
                    <div className="bibliography-fields bibliography-export-fields">
                      <div className="form-field">
                        <label htmlFor="bibliography-export-format">Export format</label>
                        <select
                          aria-describedby={
                            bibliographyExportFeedback
                              ? "bibliography-export-feedback"
                              : undefined
                          }
                          aria-invalid={bibliographyExportFeedback?.kind === "error"}
                          disabled={isExportingBibliography}
                          id="bibliography-export-format"
                          onChange={(event) => {
                            setBibliographyExportFormat(event.target.value as BibliographyFormat);
                            setBibliographyExportFeedback(null);
                          }}
                          ref={bibliographyExportSelect}
                          value={bibliographyExportFormat}
                        >
                          {Object.entries(bibliographyFormatLabels).map(([format, label]) => (
                            <option key={format} value={format}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </div>
                      <button disabled={isExportingBibliography} type="submit">
                        {isExportingBibliography ? "Exporting…" : "Export library"}
                      </button>
                    </div>
                    {bibliographyExportFeedback && (
                      <p
                        className={`capture-feedback ${bibliographyExportFeedback.kind}`}
                        id="bibliography-export-feedback"
                        role={bibliographyExportFeedback.kind === "error" ? "alert" : "status"}
                      >
                        {bibliographyExportFeedback.message}
                      </p>
                    )}
                  </form>
                </div>
              </details>

              {sourceError && (
                <p className="error-message" role="alert">
                  {sourceError}
                </p>
              )}

              {libraryNotice && (
                <p
                  className={`detail-notice ${libraryNotice.kind}`}
                  role={libraryNotice.kind === "error" ? "alert" : "status"}
                >
                  {libraryNotice.message}
                </p>
              )}

              <section className="library" aria-labelledby="library-heading">
                <div className="section-heading">
                  <h2 id="library-heading" ref={libraryHeading} tabIndex={-1}>
                    Sources
                  </h2>
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
  isRemovingSource: boolean;
  isSavingMetadata: boolean;
  loadingTextAttachmentId: number | null;
  onApplyDoiMetadata: (
    sourceId: number,
    lookupId: number,
    fields: DoiMetadataField[],
  ) => Promise<SourceDetail>;
  onBack: () => void;
  onDeleteSource: (sourceId: number, sourceTitle: string, attachmentCount: number) => Promise<void>;
  onRemove: (attachmentId: number, filename: string) => Promise<void>;
  onRetry: (attachmentId: number) => Promise<void>;
  onSaveMetadata: (sourceId: number, metadata: SourceUpdate) => Promise<boolean>;
  onLookupDoiMetadata: (sourceId: number) => Promise<DoiMetadataLookup>;
  onToggleText: (attachmentId: number) => Promise<void>;
  removingAttachmentId: number | null;
  retryingAttachmentId: number | null;
  source: SourceDetail;
  textError: string | null;
}

function SourceDetailScreen({
  detailNotice,
  extractedText,
  isRemovingSource,
  isSavingMetadata,
  loadingTextAttachmentId,
  onApplyDoiMetadata,
  onBack,
  onDeleteSource,
  onRemove,
  onRetry,
  onSaveMetadata,
  onLookupDoiMetadata,
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
  const identifierInput = useRef<HTMLTextAreaElement>(null);
  const doiLookupButton = useRef<HTMLButtonElement>(null);
  const doiReviewHeading = useRef<HTMLHeadingElement>(null);
  const doiApplyButton = useRef<HTMLButtonElement>(null);
  const [isEditingMetadata, setIsEditingMetadata] = useState(false);
  const [metadataDraft, setMetadataDraft] = useState<SourceUpdate>(() =>
    metadataFromSource(source),
  );
  const [identifierDraft, setIdentifierDraft] = useState(() => identifierDraftFrom(source));
  const [identifierError, setIdentifierError] = useState<string | null>(null);
  const [doiLookup, setDoiLookup] = useState<DoiMetadataLookup | null>(null);
  const [selectedDoiFields, setSelectedDoiFields] = useState<DoiMetadataField[]>([]);
  const [doiFeedback, setDoiFeedback] = useState<Feedback | null>(null);
  const [isLookingUpDoi, setIsLookingUpDoi] = useState(false);
  const [isApplyingDoi, setIsApplyingDoi] = useState(false);
  const wasEditingMetadata = useRef(false);
  const previousAttachmentState = useRef({
    sourceId: source.id,
    count: source.attachments.length,
  });
  const deletionDocumentSummary =
    source.attachments.length === 0
      ? "It has no saved documents."
      : `This also removes ${source.attachments.length} saved ${source.attachments.length === 1 ? "document" : "documents"}, including originals and extracted text.`;

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

  useEffect(() => {
    if (doiLookup) doiReviewHeading.current?.focus();
  }, [doiLookup]);

  useEffect(() => {
    if (!doiFeedback) return;
    if (doiFeedback.kind === "error" && doiLookup) doiApplyButton.current?.focus();
    else doiLookupButton.current?.focus();
  }, [doiFeedback, doiLookup]);

  function beginMetadataEdit() {
    setMetadataDraft(metadataFromSource(source));
    setIdentifierDraft(identifierDraftFrom(source));
    setIdentifierError(null);
    setDoiLookup(null);
    setSelectedDoiFields([]);
    setDoiFeedback(null);
    setIsEditingMetadata(true);
  }

  function cancelMetadataEdit() {
    setMetadataDraft(metadataFromSource(source));
    setIdentifierDraft(identifierDraftFrom(source));
    setIdentifierError(null);
    setIsEditingMetadata(false);
  }

  async function submitMetadata(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = metadataDraft.title.trim();
    if (!title) {
      editTitle.current?.focus();
      return;
    }
    const parsedIdentifiers = parseIdentifierDraft(identifierDraft);
    if (parsedIdentifiers.identifiers === null) {
      setIdentifierError(parsedIdentifiers.error);
      identifierInput.current?.focus();
      return;
    }
    setIdentifierError(null);
    const saved = await onSaveMetadata(source.id, {
      ...metadataDraft,
      title,
      authors: metadataDraft.authors.map((author) => author.trim()).filter(Boolean),
      tags: metadataDraft.tags.map((tag) => tag.trim()).filter(Boolean),
      collections: metadataDraft.collections
        .map((collection) => collection.trim())
        .filter(Boolean),
      identifiers: parsedIdentifiers.identifiers,
    });
    if (saved) setIsEditingMetadata(false);
  }

  async function beginDoiLookup() {
    setIsLookingUpDoi(true);
    setDoiFeedback(null);
    setDoiLookup(null);
    try {
      const lookup = await onLookupDoiMetadata(source.id);
      setDoiLookup(lookup);
      setSelectedDoiFields(
        lookup.available_fields.filter((field) => !lookup.conflicting_fields.includes(field)),
      );
    } catch (error) {
      setDoiFeedback({
        kind: "error",
        message: apiFailureMessage(
          error,
          "DOI metadata could not be retrieved. Check the local service and try again.",
        ),
      });
    } finally {
      setIsLookingUpDoi(false);
    }
  }

  function toggleDoiField(field: DoiMetadataField) {
    setSelectedDoiFields((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field],
    );
    setDoiFeedback(null);
  }

  function cancelDoiReview() {
    setDoiLookup(null);
    setSelectedDoiFields([]);
    setDoiFeedback(null);
    window.requestAnimationFrame(() => doiLookupButton.current?.focus());
  }

  async function applySelectedDoiMetadata() {
    if (!doiLookup) return;
    if (selectedDoiFields.length === 0) {
      setDoiFeedback({ kind: "error", message: "Choose at least one field to apply." });
      return;
    }
    setIsApplyingDoi(true);
    setDoiFeedback(null);
    try {
      await onApplyDoiMetadata(source.id, doiLookup.id, selectedDoiFields);
      setDoiLookup(null);
      setSelectedDoiFields([]);
      setDoiFeedback({
        kind: "success",
        message: `Applied ${selectedDoiFields.length} ${selectedDoiFields.length === 1 ? "field" : "fields"} from ${doiLookup.provider}.`,
      });
    } catch (error) {
      setDoiFeedback({
        kind: "error",
        message: apiFailureMessage(
          error,
          "DOI metadata could not be applied. Review the source and try again.",
        ),
      });
    } finally {
      setIsApplyingDoi(false);
    }
  }

  return (
    <section className="source-detail" aria-labelledby="source-detail-heading">
      <button
        className="back-button"
        disabled={isSavingMetadata || isRemovingSource}
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
            <button
              disabled={isRemovingSource}
              onClick={beginMetadataEdit}
              ref={editButton}
              type="button"
            >
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
              <div className="form-field metadata-identifiers-field">
                <label htmlFor="edit-source-identifiers">Identifiers (one per line)</label>
                <textarea
                  aria-describedby={
                    identifierError ? "edit-source-identifiers-error" : "edit-source-identifiers-hint"
                  }
                  aria-invalid={identifierError !== null}
                  disabled={isSavingMetadata}
                  id="edit-source-identifiers"
                  onChange={(event) => {
                    setIdentifierDraft(event.target.value);
                    setIdentifierError(null);
                  }}
                  placeholder="isbn: 978-1-4028-9462-6"
                  ref={identifierInput}
                  rows={3}
                  value={identifierDraft}
                />
                {identifierError ? (
                  <p className="metadata-field-error" id="edit-source-identifiers-error" role="alert">
                    {identifierError}
                  </p>
                ) : (
                  <p className="metadata-field-hint" id="edit-source-identifiers-hint">
                    Use a named type and value, such as ISBN, ISSN, PMID, PMCID, or arXiv.
                  </p>
                )}
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
            <div className="metadata-identifiers">
              <dt>Identifiers</dt>
              <dd>
                {source.identifiers.length ? (
                  <ul className="metadata-value-list">
                    {source.identifiers.map((identifier) => (
                      <li key={`${identifier.identifier_type}:${identifier.value}`}>
                        <span>{identifierTypeLabel(identifier.identifier_type)}</span> {identifier.value}
                      </li>
                    ))}
                  </ul>
                ) : (
                  "Not added"
                )}
              </dd>
            </div>
            <div className="metadata-citation-keys">
              <dt>Imported citation keys</dt>
              <dd>
                {source.citation_keys.length ? (
                  <ul className="metadata-value-list">
                    {source.citation_keys.map((citationKey) => (
                      <li key={`${citationKey.bibliography_format}:${citationKey.value}`}>
                        <span>{bibliographyFormatLabels[citationKey.bibliography_format]}</span>{" "}
                        {citationKey.value}
                      </li>
                    ))}
                  </ul>
                ) : (
                  "Not added"
                )}
              </dd>
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

      <section className="doi-metadata-section" aria-labelledby="doi-metadata-heading">
        <div className="doi-metadata-heading">
          <div>
            <h3 id="doi-metadata-heading">DOI metadata</h3>
            <p>
              Ask Crossref for metadata only when you choose. Nothing changes until you review and
              apply selected fields.
            </p>
          </div>
          <button
            disabled={
              !source.doi ||
              isEditingMetadata ||
              isLookingUpDoi ||
              isApplyingDoi ||
              isRemovingSource ||
              doiLookup !== null
            }
            onClick={beginDoiLookup}
            ref={doiLookupButton}
            type="button"
          >
            {isLookingUpDoi ? "Looking up…" : "Look up DOI metadata"}
          </button>
        </div>
        {!source.doi && <p className="doi-metadata-hint">Add and save a DOI to enable lookup.</p>}

        {doiFeedback && (
          <p
            className={`doi-metadata-feedback ${doiFeedback.kind}`}
            role={doiFeedback.kind === "error" ? "alert" : "status"}
          >
            {doiFeedback.message}
          </p>
        )}

        {doiLookup && (
          <div aria-busy={isApplyingDoi} className="doi-metadata-review">
            <div className="doi-review-intro">
              <div>
                <h4 ref={doiReviewHeading} tabIndex={-1}>
                  Review metadata from {doiLookup.provider}
                </h4>
                <p>
                  Retrieved {formatLookupDate(doiLookup.retrieved_at)} for DOI {doiLookup.retrieved_doi}.
                </p>
              </div>
              <a href={doiLookup.provider_url} rel="noreferrer" target="_blank">
                View provider record
              </a>
            </div>
            <fieldset className="doi-field-list">
              <legend>Choose fields to apply</legend>
              {doiLookup.available_fields.map((field) => {
                const conflicts = doiLookup.conflicting_fields.includes(field);
                return (
                  <label className={conflicts ? "doi-field conflict" : "doi-field"} key={field}>
                    <span className="doi-field-choice">
                      <input
                        checked={selectedDoiFields.includes(field)}
                        disabled={isApplyingDoi}
                        onChange={() => toggleDoiField(field)}
                        type="checkbox"
                      />
                      <strong>{doiMetadataFieldLabels[field]}</strong>
                      {conflicts && <span className="doi-conflict-label">Conflict</span>}
                    </span>
                    <span className="doi-field-comparison">
                      <span>
                        <small>Current</small>
                        {doiMetadataSourceValue(source, field)}
                      </span>
                      <span>
                        <small>Crossref</small>
                        {doiMetadataProposalValue(doiLookup, field)}
                      </span>
                    </span>
                    {field === "identifiers" && (
                      <span className="doi-merge-note">
                        Selected identifiers are added without removing saved identifiers.
                      </span>
                    )}
                  </label>
                );
              })}
            </fieldset>
            <div className="doi-review-actions">
              <button
                disabled={isApplyingDoi}
                onClick={applySelectedDoiMetadata}
                ref={doiApplyButton}
                type="button"
              >
                {isApplyingDoi ? "Applying…" : "Apply selected fields"}
              </button>
              <button disabled={isApplyingDoi} onClick={cancelDoiReview} type="button">
                Cancel
              </button>
            </div>
          </div>
        )}

        {source.metadata_provenance.length > 0 && (
          <div className="metadata-provenance">
            <h4>Applied metadata provenance</h4>
            <ul>
              {source.metadata_provenance.map((provenance) => (
                <li key={provenance.lookup_id}>
                  <a href={provenance.provider_url} rel="noreferrer" target="_blank">
                    {provenance.provider}
                  </a>{" "}
                  for DOI {provenance.retrieved_doi}
                  <span>
                    Retrieved {formatLookupDate(provenance.retrieved_at)} · Applied{" "}
                    {formatLookupDate(provenance.applied_at)} · Fields:{" "}
                    {provenance.applied_fields
                      .map((field) => doiMetadataFieldLabels[field].toLocaleLowerCase())
                      .join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
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
                      disabled={loadingTextAttachmentId !== null || isRemovingSource}
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
                      disabled={
                        retryingAttachmentId !== null ||
                        removingAttachmentId !== null ||
                        isRemovingSource
                      }
                      onClick={() => void onRetry(attachment.id)}
                      type="button"
                    >
                      {retryingAttachmentId === attachment.id ? "Retrying…" : "Retry extraction"}
                    </button>
                  )}
                  {attachment.can_remove && (
                    <button
                      className="danger-button"
                      disabled={
                        retryingAttachmentId !== null ||
                        removingAttachmentId !== null ||
                        isRemovingSource
                      }
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

      <section className="source-danger-zone" aria-labelledby="delete-source-heading">
        <div>
          <h3 id="delete-source-heading">Delete source</h3>
          <p>
            Permanently remove its metadata, notes, and organization links. {deletionDocumentSummary}
          </p>
        </div>
        <button
          className="danger-button"
          disabled={
            isEditingMetadata ||
            isSavingMetadata ||
            retryingAttachmentId !== null ||
            removingAttachmentId !== null ||
            loadingTextAttachmentId !== null ||
            isRemovingSource
          }
          onClick={() => void onDeleteSource(source.id, source.title, source.attachments.length)}
          type="button"
        >
          {isRemovingSource ? "Deleting source…" : "Delete source"}
        </button>
      </section>
    </section>
  );
}
