import { useEffect, useRef, useState } from "react";
import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const minimumScale = 0.5;
const maximumScale = 3;
const scaleStep = 0.25;

interface PdfReaderProps {
  title: string;
  url: string;
}

function boundedScale(scale: number): number {
  return Math.min(maximumScale, Math.max(minimumScale, scale));
}

export function PdfReader({ title, url }: PdfReaderProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const pageContainer = useRef<HTMLDivElement>(null);
  const retryButton = useRef<HTMLButtonElement>(null);
  const renderTask = useRef<RenderTask | null>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isRendering, setIsRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    const task = getDocument({ url });
    void task.promise
      .then((loadedDocument) => {
        if (!active) return;
        setDocument(loadedDocument);
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
  }, [loadAttempt, url]);

  useEffect(() => {
    if (!document || !canvas.current) return;
    let active = true;

    void document
      .getPage(pageNumber)
      .then((page) => {
        if (!active || !canvas.current) return;
        const viewport = page.getViewport({ scale });
        const outputScale = window.devicePixelRatio || 1;
        const target = canvas.current;
        target.width = Math.floor(viewport.width * outputScale);
        target.height = Math.floor(viewport.height * outputScale);
        target.style.width = `${Math.floor(viewport.width)}px`;
        target.style.height = `${Math.floor(viewport.height)}px`;

        const task = page.render({
          canvas: target,
          transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
          viewport,
        });
        renderTask.current = task;
        return task.promise;
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
    setPageNumber(1);
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
      <div
        aria-busy={isRendering}
        className="pdf-page-container"
        ref={pageContainer}
      >
        {isRendering && (
          <span className="pdf-rendering-status" role="status">
            Rendering page…
          </span>
        )}
        <canvas
          aria-label={`Page ${pageNumber} of ${document.numPages}`}
          className="pdf-page-canvas"
          ref={canvas}
          role="img"
          tabIndex={-1}
        />
      </div>
    </section>
  );
}
