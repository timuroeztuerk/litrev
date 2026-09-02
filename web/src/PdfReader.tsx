import { useEffect, useRef, useState } from "react";
import {
  GlobalWorkerOptions,
  TextLayer,
  getDocument,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import {
  createHighlight,
  createReaderNote,
  deleteHighlight,
  getHighlights,
  getReaderNotes,
  updateReaderNote,
  type Highlight,
  type HighlightRectangle,
  type ReaderNote,
} from "./api";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const minimumScale = 0.5;
const maximumScale = 3;
const scaleStep = 0.25;
const maximumSelectedTextLength = 10_000;
const maximumRectangles = 100;

interface PdfReaderProps {
  attachmentId: number;
  initialPage?: number;
  title: string;
  url: string;
}

interface PendingSelection {
  selectedText: string;
  rectangles: HighlightRectangle[];
}

type HighlightLoadState = "loading" | "ready" | "error";
type NoteLoadState = "loading" | "ready" | "error";
type TextAvailability = "loading" | "available" | "unavailable";

type NoteAnchor =
  | { kind: "highlight"; highlight: Highlight }
  | { kind: "new-highlight"; selection: PendingSelection }
  | null;

function boundedScale(scale: number): number {
  return Math.min(maximumScale, Math.max(minimumScale, scale));
}

function roundCoordinate(value: number): number {
  return Number(value.toFixed(6));
}

function normalizedRectangle(
  rectangle: DOMRect,
  pageRectangle: DOMRect,
): HighlightRectangle | null {
  if (pageRectangle.width <= 0 || pageRectangle.height <= 0) return null;
  const left = Math.max(pageRectangle.left, rectangle.left);
  const top = Math.max(pageRectangle.top, rectangle.top);
  const right = Math.min(pageRectangle.right, rectangle.right);
  const bottom = Math.min(pageRectangle.bottom, rectangle.bottom);
  if (right <= left || bottom <= top) return null;

  const x = roundCoordinate((left - pageRectangle.left) / pageRectangle.width);
  const y = roundCoordinate((top - pageRectangle.top) / pageRectangle.height);
  const width = roundCoordinate(
    Math.min((right - left) / pageRectangle.width, 1 - x),
  );
  const height = roundCoordinate(
    Math.min((bottom - top) / pageRectangle.height, 1 - y),
  );
  if (width <= 0 || height <= 0) return null;
  return { x, y, width, height };
}

function highlightSort(left: Highlight, right: Highlight): number {
  return left.page_number - right.page_number || left.id - right.id;
}

function noteSort(left: ReaderNote, right: ReaderNote): number {
  return left.page_number - right.page_number || left.id - right.id;
}

function clearBrowserSelection() {
  window.getSelection()?.removeAllRanges();
}

export function PdfReader({ attachmentId, initialPage = 1, title, url }: PdfReaderProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const pageContainer = useRef<HTMLDivElement>(null);
  const pageSurface = useRef<HTMLDivElement>(null);
  const textLayerElement = useRef<HTMLDivElement>(null);
  const retryButton = useRef<HTMLButtonElement>(null);
  const renderTask = useRef<RenderTask | null>(null);
  const textLayerTask = useRef<TextLayer | null>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(Math.max(1, initialPage));
  const [scale, setScale] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isRendering, setIsRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [highlightLoadState, setHighlightLoadState] =
    useState<HighlightLoadState>("loading");
  const [highlightLoadAttempt, setHighlightLoadAttempt] = useState(0);
  const [highlightError, setHighlightError] = useState<string | null>(null);
  const [pendingSelection, setPendingSelection] = useState<PendingSelection | null>(null);
  const [isSavingHighlight, setIsSavingHighlight] = useState(false);
  const [deletingHighlightId, setDeletingHighlightId] = useState<number | null>(null);
  const [textAvailability, setTextAvailability] =
    useState<TextAvailability>("loading");
  const [notes, setNotes] = useState<ReaderNote[]>([]);
  const [noteLoadState, setNoteLoadState] = useState<NoteLoadState>("loading");
  const [noteLoadAttempt, setNoteLoadAttempt] = useState(0);
  const [noteError, setNoteError] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState("");
  const [notePageNumber, setNotePageNumber] = useState(Math.max(1, initialPage));
  const [noteAnchor, setNoteAnchor] = useState<NoteAnchor>(null);
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null);
  const [isNoteEditorOpen, setIsNoteEditorOpen] = useState(false);
  const [isSavingNote, setIsSavingNote] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void getReaderNotes(attachmentId, controller.signal)
      .then((savedNotes) => {
        if (!active) return;
        setNotes(savedNotes);
        setNoteLoadState("ready");
      })
      .catch((caught: unknown) => {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setNoteLoadState("error");
        setNoteError("Saved notes could not be loaded. Reading remains available.");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [attachmentId, noteLoadAttempt]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    void getHighlights(attachmentId, controller.signal)
      .then((savedHighlights) => {
        if (!active) return;
        setHighlights(savedHighlights.slice().sort(highlightSort));
        setHighlightLoadState("ready");
      })
      .catch((caught: unknown) => {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setHighlightLoadState("error");
        setHighlightError("Saved highlights could not be loaded. Reading remains available.");
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [attachmentId, highlightLoadAttempt]);

  useEffect(() => {
    let active = true;
    const task = getDocument({ url });
    void task.promise
      .then((loadedDocument) => {
        if (!active) return;
        setDocument(loadedDocument);
        setPageNumber(Math.min(loadedDocument.numPages, Math.max(1, initialPage)));
        setIsLoading(false);
        setIsRendering(true);
      })
      .catch(() => {
        if (!active) return;
        setIsLoading(false);
        setError("This PDF could not be opened. It may be missing, changed, damaged, or encrypted.");
      });

    return () => {
      active = false;
      void task.destroy();
    };
  }, [initialPage, loadAttempt, url]);

  useEffect(() => {
    if (
      !document ||
      !canvas.current ||
      !pageSurface.current ||
      !textLayerElement.current
    ) {
      return;
    }
    let active = true;
    const targetCanvas = canvas.current;
    const targetSurface = pageSurface.current;
    const targetTextLayer = textLayerElement.current;
    targetTextLayer.replaceChildren();
    clearBrowserSelection();
    setPendingSelection(null);
    setHighlightError(null);
    setTextAvailability("loading");

    void document
      .getPage(pageNumber)
      .then(async (page) => {
        if (!active) return;
        const viewport = page.getViewport({ scale });
        const outputScale = window.devicePixelRatio || 1;
        targetSurface.style.width = `${viewport.width}px`;
        targetSurface.style.height = `${viewport.height}px`;
        targetSurface.style.setProperty("--scale-factor", String(viewport.scale));
        targetSurface.style.setProperty("--user-unit", String(viewport.userUnit));
        targetCanvas.width = Math.floor(viewport.width * outputScale);
        targetCanvas.height = Math.floor(viewport.height * outputScale);
        targetCanvas.style.width = `${viewport.width}px`;
        targetCanvas.style.height = `${viewport.height}px`;

        const canvasTask = page.render({
          canvas: targetCanvas,
          transform:
            outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
          viewport,
        });
        renderTask.current = canvasTask;

        const renderText = async () => {
          try {
            const textContent = await page.getTextContent();
            if (!active) return;
            const hasUsableText = textContent.items.some(
              (item) => "str" in item && item.str.trim().length > 0,
            );
            if (!hasUsableText) {
              setTextAvailability("unavailable");
              return;
            }
            const layer = new TextLayer({
              container: targetTextLayer,
              textContentSource: textContent,
              viewport,
            });
            textLayerTask.current = layer;
            await layer.render();
            if (!active) return;
            targetTextLayer.style.width = `${
              (viewport.viewBox[2] - viewport.viewBox[0]) * viewport.scale * viewport.userUnit
            }px`;
            targetTextLayer.style.height = `${
              (viewport.viewBox[3] - viewport.viewBox[1]) * viewport.scale * viewport.userUnit
            }px`;
            setTextAvailability("available");
          } catch {
            if (active) setTextAvailability("unavailable");
          }
        };

        await Promise.all([canvasTask.promise, renderText()]);
      })
      .then(() => {
        if (active) setIsRendering(false);
      })
      .catch(() => {
        if (!active) return;
        setIsRendering(false);
        setError(`Page ${pageNumber} could not be rendered.`);
      });

    return () => {
      active = false;
      renderTask.current?.cancel();
      renderTask.current = null;
      textLayerTask.current?.cancel();
      textLayerTask.current = null;
    };
  }, [document, pageNumber, scale]);

  useEffect(() => {
    if (error) {
      retryButton.current?.focus();
    } else if (document && !isLoading) {
      canvas.current?.focus();
    }
  }, [document, error, isLoading]);

  async function fitToWidth() {
    if (!document || !pageContainer.current) return;
    try {
      const page = await document.getPage(pageNumber);
      const unscaled = page.getViewport({ scale: 1 });
      const availableWidth = Math.max(1, pageContainer.current.clientWidth - 48);
      setIsRendering(true);
      setScale(boundedScale(availableWidth / unscaled.width));
    } catch {
      setError(`Page ${pageNumber} could not be measured.`);
    }
  }

  function retry() {
    setDocument(null);
    setPageNumber(Math.max(1, initialPage));
    setScale(1);
    setIsLoading(true);
    setIsRendering(false);
    setError(null);
    setLoadAttempt((attempt) => attempt + 1);
  }

  function goToPage(page: number) {
    setIsRendering(true);
    setPageNumber(Math.min(document?.numPages ?? page, Math.max(1, page)));
  }

  function changeScale(nextScale: number) {
    setIsRendering(true);
    setScale(boundedScale(nextScale));
  }

  function captureSelection() {
    const selection = window.getSelection();
    const surface = pageSurface.current;
    const textLayer = textLayerElement.current;
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1 || !surface || !textLayer) {
      setPendingSelection(null);
      return;
    }

    const range = selection.getRangeAt(0);
    if (!textLayer.contains(range.startContainer) || !textLayer.contains(range.endContainer)) {
      setPendingSelection(null);
      return;
    }
    const selectedText = selection.toString();
    if (!selectedText.trim()) {
      setPendingSelection(null);
      return;
    }
    if (selectedText.length > maximumSelectedTextLength) {
      setPendingSelection(null);
      setHighlightError("A highlight can contain at most 10,000 selected characters.");
      return;
    }

    const surfaceRectangle = surface.getBoundingClientRect();
    const rectangles = Array.from(range.getClientRects())
      .map((rectangle) => normalizedRectangle(rectangle, surfaceRectangle))
      .filter((rectangle): rectangle is HighlightRectangle => rectangle !== null);
    if (rectangles.length > maximumRectangles) {
      setPendingSelection(null);
      setHighlightError("A highlight can span at most 100 text rectangles.");
      return;
    }
    if (rectangles.length === 0) {
      setPendingSelection(null);
      return;
    }
    setHighlightError(null);
    setPendingSelection({ selectedText, rectangles });
  }

  async function saveHighlight() {
    if (!pendingSelection || highlightLoadState !== "ready") return;
    setIsSavingHighlight(true);
    setHighlightError(null);
    try {
      const saved = await createHighlight(attachmentId, {
        page_number: pageNumber,
        selected_text: pendingSelection.selectedText,
        rectangles: pendingSelection.rectangles,
      });
      setHighlights((current) => [...current, saved].sort(highlightSort));
      setPendingSelection(null);
      clearBrowserSelection();
      textLayerElement.current?.focus();
    } catch {
      setHighlightError("The highlight could not be saved. The selection was not stored.");
    } finally {
      setIsSavingHighlight(false);
    }
  }

  async function removeHighlight(highlightId: number) {
    setDeletingHighlightId(highlightId);
    setHighlightError(null);
    try {
      await deleteHighlight(highlightId);
      setHighlights((current) => current.filter((highlight) => highlight.id !== highlightId));
      setNotes((current) =>
        current.map((note) =>
          note.highlight?.id === highlightId ? { ...note, highlight: null } : note,
        ),
      );
      textLayerElement.current?.focus();
    } catch {
      setHighlightError("The highlight could not be deleted and remains saved.");
    } finally {
      setDeletingHighlightId(null);
    }
  }

  function retryHighlights() {
    setHighlightLoadState("loading");
    setHighlightError(null);
    setHighlightLoadAttempt((attempt) => attempt + 1);
  }

  function startNote(anchor: NoteAnchor = null) {
    setEditingNoteId(null);
    setNoteDraft("");
    setNotePageNumber(pageNumber);
    setNoteAnchor(anchor);
    setNoteError(null);
    setIsNoteEditorOpen(true);
  }

  function editNote(note: ReaderNote) {
    setEditingNoteId(note.id);
    setNoteDraft(note.body);
    setNotePageNumber(note.page_number);
    setNoteAnchor(note.highlight ? { kind: "highlight", highlight: note.highlight } : null);
    setNoteError(null);
    setIsNoteEditorOpen(true);
  }

  function closeNoteEditor() {
    setEditingNoteId(null);
    setNoteDraft("");
    setNoteAnchor(null);
    setIsNoteEditorOpen(false);
  }

  async function saveNote() {
    if (!noteDraft.trim()) return;
    setIsSavingNote(true);
    setNoteError(null);
    try {
      let saved: ReaderNote;
      if (editingNoteId !== null) {
        saved = await updateReaderNote(editingNoteId, noteDraft);
        setNotes((current) =>
          current.map((note) => (note.id === saved.id ? saved : note)).sort(noteSort),
        );
      } else {
        saved = await createReaderNote(attachmentId, {
          page_number: notePageNumber,
          body: noteDraft,
          ...(noteAnchor?.kind === "highlight"
            ? { highlight_id: noteAnchor.highlight.id }
            : {}),
          ...(noteAnchor?.kind === "new-highlight"
            ? {
                new_highlight: {
                  selected_text: noteAnchor.selection.selectedText,
                  rectangles: noteAnchor.selection.rectangles,
                },
              }
            : {}),
        });
        setNotes((current) => [...current, saved].sort(noteSort));
        if (saved.highlight) {
          const savedHighlight = saved.highlight;
          setHighlights((current) =>
            current.some((highlight) => highlight.id === savedHighlight.id)
              ? current
              : [...current, savedHighlight].sort(highlightSort),
          );
        }
        if (noteAnchor?.kind === "new-highlight") {
          setPendingSelection(null);
          clearBrowserSelection();
        }
      }
      closeNoteEditor();
    } catch {
      setNoteError(
        editingNoteId === null
          ? "The note could not be saved. Your draft remains available."
          : "The note could not be updated. Your changes remain available.",
      );
    } finally {
      setIsSavingNote(false);
    }
  }

  function retryNotes() {
    setNoteLoadState("loading");
    setNoteError(null);
    setNoteLoadAttempt((attempt) => attempt + 1);
  }

  if (isLoading) {
    return (
      <p className="pdf-reader-state" role="status">
        Opening PDF…
      </p>
    );
  }

  if (!document || error) {
    return (
      <div className="pdf-reader-state error-message" role="alert">
        <span>{error ?? "This PDF could not be opened."}</span>
        <button onClick={retry} ref={retryButton} type="button">
          Try again
        </button>
      </div>
    );
  }

  const zoomPercent = Math.round(scale * 100);
  const pageHighlights = highlights.filter(
    (highlight) => highlight.page_number === pageNumber,
  );

  return (
    <section className="pdf-reader" aria-label={`${title} PDF reader`}>
      <div className="pdf-reader-toolbar" aria-label="PDF controls">
        <div className="pdf-page-controls">
          <button
            disabled={pageNumber === 1 || isRendering}
            onClick={() => goToPage(pageNumber - 1)}
            type="button"
          >
            Previous
          </button>
          <label>
            <span>Page</span>
            <input
              aria-label="Page number"
              max={document.numPages}
              min={1}
              onChange={(event) => {
                const requestedPage = event.currentTarget.valueAsNumber;
                if (Number.isInteger(requestedPage)) {
                  goToPage(requestedPage);
                }
              }}
              type="number"
              value={pageNumber}
            />
            <span>of {document.numPages}</span>
          </label>
          <button
            disabled={pageNumber === document.numPages || isRendering}
            onClick={() => goToPage(pageNumber + 1)}
            type="button"
          >
            Next
          </button>
        </div>
        <div className="pdf-zoom-controls">
          <button
            aria-label="Zoom out"
            disabled={scale <= minimumScale || isRendering}
            onClick={() => changeScale(scale - scaleStep)}
            type="button"
          >
            −
          </button>
          <output aria-label="Zoom level">{zoomPercent}%</output>
          <button
            aria-label="Zoom in"
            disabled={scale >= maximumScale || isRendering}
            onClick={() => changeScale(scale + scaleStep)}
            type="button"
          >
            +
          </button>
          <button disabled={isRendering} onClick={() => void fitToWidth()} type="button">
            Fit width
          </button>
        </div>
      </div>
      <div className="pdf-reader-content">
        <div className="pdf-reader-main">
          <div className="pdf-highlight-actions">
            {highlightLoadState === "loading" && (
              <span role="status">Loading saved highlights…</span>
            )}
            {highlightLoadState === "error" && (
              <button onClick={retryHighlights} type="button">
                Retry saved highlights
              </button>
            )}
            {pendingSelection && (
              <>
                <button
                  disabled={isSavingHighlight || highlightLoadState !== "ready"}
                  onClick={() => void saveHighlight()}
                  type="button"
                >
                  {isSavingHighlight ? "Saving highlight…" : "Highlight"}
                </button>
                <button
                  onClick={() =>
                    startNote({ kind: "new-highlight", selection: pendingSelection })
                  }
                  type="button"
                >
                  Write note on selection
                </button>
              </>
            )}
            {textAvailability === "unavailable" && !isRendering && (
              <span role="status">
                This page has no usable selectable text. Highlighting is unavailable; Litrev does
                not run OCR automatically.
              </span>
            )}
          </div>
          {highlightError && (
            <p className="pdf-highlight-error error-message" role="alert">
              {highlightError}
            </p>
          )}
          <div aria-busy={isRendering} className="pdf-page-container" ref={pageContainer}>
            {isRendering && (
              <span className="pdf-rendering-status" role="status">
                Rendering page…
              </span>
            )}
            <div
              className="pdf-page-surface"
              onKeyUp={captureSelection}
              onMouseUp={captureSelection}
              ref={pageSurface}
            >
              <canvas
                aria-label={`Page ${pageNumber} of ${document.numPages}`}
                className="pdf-page-canvas"
                ref={canvas}
                role="img"
                tabIndex={-1}
              />
              <div aria-hidden="true" className="pdf-highlight-layer">
                {pageHighlights.flatMap((highlight) =>
                  highlight.rectangles.map((rectangle, rectangleIndex) => (
                    <span
                      className="pdf-highlight-rectangle"
                      key={`${highlight.id}-${rectangleIndex}`}
                      style={{
                        height: `${rectangle.height * 100}%`,
                        left: `${rectangle.x * 100}%`,
                        top: `${rectangle.y * 100}%`,
                        width: `${rectangle.width * 100}%`,
                      }}
                    />
                  )),
                )}
              </div>
              <div
                aria-label={`Selectable text for page ${pageNumber}`}
                className="pdf-text-layer textLayer"
                ref={textLayerElement}
                role="document"
                tabIndex={textAvailability === "available" ? 0 : -1}
              />
            </div>
          </div>
          {pageHighlights.length > 0 && (
            <section className="pdf-page-highlights" aria-label={`Highlights on page ${pageNumber}`}>
              <h3>Highlights on this page</h3>
              <ul>
                {pageHighlights.map((highlight) => (
                  <li key={highlight.id}>
                    <q>{highlight.selected_text}</q>
                    <div>
                      <button
                        aria-label={`Write note on highlight: ${highlight.selected_text}`}
                        onClick={() => startNote({ kind: "highlight", highlight })}
                        type="button"
                      >
                        Write note
                      </button>
                      <button
                        aria-label={`Delete highlight: ${highlight.selected_text}`}
                        disabled={deletingHighlightId === highlight.id}
                        onClick={() => void removeHighlight(highlight.id)}
                        type="button"
                      >
                        {deletingHighlightId === highlight.id ? "Deleting…" : "Delete"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
        <aside className="pdf-note-panel" aria-labelledby="pdf-note-panel-heading">
          <div className="pdf-note-panel-heading">
            <div>
              <h3 id="pdf-note-panel-heading">Notes</h3>
              <span>{notes.length}</span>
            </div>
            <button onClick={() => startNote()} type="button">
              Write page note
            </button>
          </div>
          {noteLoadState === "loading" && <p role="status">Loading saved notes…</p>}
          {noteLoadState === "error" && (
            <button onClick={retryNotes} type="button">
              Retry saved notes
            </button>
          )}
          {isNoteEditorOpen && (
            <form
              className="pdf-note-editor"
              onSubmit={(event) => {
                event.preventDefault();
                void saveNote();
              }}
            >
              <h4>{editingNoteId === null ? "New note" : "Edit note"}</h4>
              <small>Page {notePageNumber}</small>
              {noteAnchor && (
                <q>
                  {noteAnchor.kind === "highlight"
                    ? noteAnchor.highlight.selected_text
                    : noteAnchor.selection.selectedText}
                </q>
              )}
              <label htmlFor="reader-note-body">Note</label>
              <textarea
                autoFocus
                id="reader-note-body"
                maxLength={100_000}
                onChange={(event) => setNoteDraft(event.currentTarget.value)}
                rows={7}
                value={noteDraft}
              />
              <div>
                <button disabled={!noteDraft.trim() || isSavingNote} type="submit">
                  {isSavingNote ? "Saving note…" : "Save note"}
                </button>
                <button disabled={isSavingNote} onClick={closeNoteEditor} type="button">
                  Cancel
                </button>
              </div>
            </form>
          )}
          {noteError && (
            <p className="pdf-note-error error-message" role="alert">
              {noteError}
            </p>
          )}
          {noteLoadState === "ready" && notes.length === 0 && !isNoteEditorOpen && (
            <p className="pdf-notes-empty">No notes for this PDF yet.</p>
          )}
          {notes.length > 0 && (
            <ul className="pdf-note-list">
              {notes.map((note) => (
                <li key={note.id}>
                  <p>{note.body}</p>
                  {note.highlight && <q>{note.highlight.selected_text}</q>}
                  <div>
                    <button
                      aria-label={`Open note on page ${note.page_number}`}
                      onClick={() => goToPage(note.page_number)}
                      type="button"
                    >
                      Page {note.page_number}
                    </button>
                    <button
                      aria-label={`Edit note: ${note.body}`}
                      onClick={() => editNote(note)}
                      type="button"
                    >
                      Edit
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </section>
  );
}
