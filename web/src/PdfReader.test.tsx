import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const pdfMocks = vi.hoisted(() => ({
  destroy: vi.fn(),
  getDocument: vi.fn(),
  getPage: vi.fn(),
  getTextContent: vi.fn(),
  getViewport: vi.fn(),
  render: vi.fn(),
  renderCancel: vi.fn(),
  textLayerCancel: vi.fn(),
  textLayerConstructor: vi.fn(),
  textLayerRender: vi.fn(),
}));

const highlightMocks = vi.hoisted(() => ({
  createHighlight: vi.fn(),
  createReaderNote: vi.fn(),
  deleteHighlight: vi.fn(),
  getHighlights: vi.fn(),
  getReaderNotes: vi.fn(),
  updateReaderNote: vi.fn(),
}));

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  TextLayer: class {
    constructor({
      container,
      textContentSource,
    }: {
      container: HTMLElement;
      textContentSource: { items: { str?: string }[] };
    }) {
      pdfMocks.textLayerConstructor({ container, textContentSource });
      for (const item of textContentSource.items) {
        if (!item.str) continue;
        const span = document.createElement("span");
        span.textContent = item.str;
        container.append(span);
      }
    }

    render() {
      return pdfMocks.textLayerRender();
    }

    cancel() {
      pdfMocks.textLayerCancel();
    }
  },
  getDocument: pdfMocks.getDocument,
}));

vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "pdf.worker.mjs",
}));

vi.mock("./api", () => highlightMocks);

import { PdfReader } from "./PdfReader";

function resolvedLoadingTask() {
  const renderTask = {
    cancel: pdfMocks.renderCancel,
    promise: Promise.resolve(),
  };
  const page = {
    getTextContent: pdfMocks.getTextContent,
    getViewport: pdfMocks.getViewport.mockImplementation(({ scale }: { scale: number }) => ({
      height: 800 * scale,
      rotation: 0,
      scale,
      userUnit: 1,
      viewBox: [0, 0, 600, 800],
      width: 600 * scale,
    })),
    render: pdfMocks.render.mockReturnValue(renderTask),
  };
  const document = {
    getPage: pdfMocks.getPage.mockResolvedValue(page),
    numPages: 2,
  };
  return {
    destroy: pdfMocks.destroy,
    promise: Promise.resolve(document),
  };
}

beforeEach(() => {
  for (const mock of [...Object.values(pdfMocks), ...Object.values(highlightMocks)]) {
    mock.mockReset();
  }
  pdfMocks.getTextContent.mockResolvedValue({
    items: [{ str: "Selectable text" }],
    styles: {},
  });
  pdfMocks.textLayerRender.mockResolvedValue(undefined);
  highlightMocks.getHighlights.mockResolvedValue([]);
  highlightMocks.getReaderNotes.mockResolvedValue([]);
  highlightMocks.deleteHighlight.mockResolvedValue(undefined);
  pdfMocks.getDocument.mockReturnValue(resolvedLoadingTask());
});

afterEach(cleanup);

test("loads a PDF, renders one page at a time, and changes zoom", async () => {
  render(<PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />);

  expect(screen.getByText("Opening PDF…")).toBeInTheDocument();
  expect(await screen.findByRole("img", { name: "Page 1 of 2" })).toHaveFocus();
  expect(pdfMocks.getDocument).toHaveBeenCalledWith({ url: "http://local.test/paper.pdf" });
  await waitFor(() => expect(pdfMocks.getPage).toHaveBeenCalledWith(1));
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());

  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(pdfMocks.getPage).toHaveBeenCalledWith(2));
  expect(screen.getByLabelText("Page number")).toHaveValue(2);

  await waitFor(() => expect(screen.getByRole("button", { name: "Zoom in" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  await waitFor(() =>
    expect(pdfMocks.getViewport).toHaveBeenLastCalledWith({ scale: 1.25 }),
  );
  expect(screen.getByLabelText("Zoom level")).toHaveTextContent("125%");
  expect(pdfMocks.getTextContent).toHaveBeenCalled();
  expect(pdfMocks.textLayerConstructor).toHaveBeenCalled();
});

test("shows a PDF loading failure and retries explicitly", async () => {
  pdfMocks.getDocument.mockReturnValueOnce({
    destroy: pdfMocks.destroy,
    promise: Promise.reject(new Error("malformed PDF")),
  });
  render(<PdfReader attachmentId={7} title="Broken paper" url="http://local.test/broken.pdf" />);

  expect(
    await screen.findByText(
      "This PDF could not be opened. It may be missing, changed, damaged, or encrypted.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toHaveFocus();

  pdfMocks.getDocument.mockReturnValueOnce(resolvedLoadingTask());
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));

  expect(await screen.findByRole("img", { name: "Page 1 of 2" })).toBeInTheDocument();
  expect(pdfMocks.getDocument).toHaveBeenCalledTimes(2);
});

function selectText(
  container: HTMLElement,
  selectedText = "Selectable text",
  releaseOnPageSurface = false,
) {
  const surface = container.querySelector(".pdf-page-surface");
  const text = container.querySelector(".pdf-text-layer span")?.firstChild;
  if (!(surface instanceof HTMLElement) || !text) {
    throw new Error("The rendered text layer is required for this test.");
  }
  vi.spyOn(surface, "getBoundingClientRect").mockReturnValue(
    DOMRect.fromRect({ x: 100, y: 200, width: 600, height: 800 }),
  );
  const removeAllRanges = vi.fn();
  const getSelection = vi.spyOn(window, "getSelection").mockReturnValue({
    getRangeAt: () => ({
      endContainer: text,
      getClientRects: () => [
        DOMRect.fromRect({ x: 160, y: 280, width: 300, height: 32 }),
      ],
      startContainer: text,
    }),
    isCollapsed: false,
    rangeCount: 1,
    removeAllRanges,
    toString: () => selectedText,
  } as unknown as Selection);
  fireEvent.mouseUp(releaseOnPageSurface ? surface : (text.parentElement as HTMLElement));
  return { getSelection, removeAllRanges };
}

test("captures a text selection when the drag ends over page whitespace", async () => {
  const rendered = render(
    <PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />,
  );
  await waitFor(() => expect(pdfMocks.textLayerRender).toHaveBeenCalled());

  const selection = selectText(rendered.container, "Selectable text", true);

  expect(await screen.findByRole("button", { name: "Highlight" })).toBeEnabled();
  selection.getSelection.mockRestore();
});

test("saves normalized selection geometry and restores and deletes the overlay after reopen", async () => {
  const savedHighlight = {
    id: 31,
    attachment_id: 7,
    source_id: 4,
    page_number: 1,
    selected_text: "Selectable text",
    rectangles: [{ x: 0.1, y: 0.1, width: 0.5, height: 0.04 }],
    created_at: "2026-09-01T12:00:00Z",
  };
  highlightMocks.createHighlight.mockResolvedValue(savedHighlight);
  const firstRender = render(
    <PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />,
  );
  await screen.findByRole("img", { name: "Page 1 of 2" });
  await waitFor(() => expect(pdfMocks.textLayerRender).toHaveBeenCalled());

  const selection = selectText(firstRender.container);
  expect(highlightMocks.createHighlight).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "Highlight" }));

  await waitFor(() =>
    expect(highlightMocks.createHighlight).toHaveBeenCalledWith(7, {
      page_number: 1,
      selected_text: "Selectable text",
      rectangles: [{ x: 0.1, y: 0.1, width: 0.5, height: 0.04 }],
    }),
  );
  expect(screen.getByText("Selectable text", { selector: "q" })).toBeInTheDocument();
  expect(firstRender.container.querySelector(".pdf-highlight-rectangle")).toHaveStyle({
    height: "4%",
    left: "10%",
    top: "10%",
    width: "50%",
  });
  expect(selection.removeAllRanges).toHaveBeenCalled();
  selection.getSelection.mockRestore();
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  await waitFor(() =>
    expect(pdfMocks.getViewport).toHaveBeenLastCalledWith({ scale: 1.25 }),
  );
  expect(firstRender.container.querySelector(".pdf-highlight-rectangle")).toHaveStyle({
    height: "4%",
    left: "10%",
    top: "10%",
    width: "50%",
  });

  firstRender.unmount();
  highlightMocks.getHighlights.mockResolvedValueOnce([savedHighlight]);
  const reopened = render(
    <PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />,
  );

  const deleteButton = await screen.findByRole("button", {
    name: "Delete highlight: Selectable text",
  });
  expect(reopened.container.querySelector(".pdf-highlight-rectangle")).toHaveStyle({
    height: "4%",
    left: "10%",
    top: "10%",
    width: "50%",
  });
  fireEvent.click(deleteButton);

  await waitFor(() => expect(highlightMocks.deleteHighlight).toHaveBeenCalledWith(31));
  expect(screen.queryByText("Selectable text", { selector: "q" })).not.toBeInTheDocument();
  expect(reopened.container.querySelector(".pdf-highlight-rectangle")).toBeNull();
});

test("keeps a failed highlight save actionable without rendering a false saved overlay", async () => {
  highlightMocks.createHighlight.mockRejectedValue(new Error("database unavailable"));
  const rendered = render(
    <PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />,
  );
  await waitFor(() => expect(pdfMocks.textLayerRender).toHaveBeenCalled());

  const selection = selectText(rendered.container);
  fireEvent.click(await screen.findByRole("button", { name: "Highlight" }));

  expect(
    await screen.findByText("The highlight could not be saved. The selection was not stored."),
  ).toBeInTheDocument();
  expect(rendered.container.querySelector(".pdf-highlight-rectangle")).toBeNull();
  expect(screen.getByRole("button", { name: "Highlight" })).toBeEnabled();
  selection.getSelection.mockRestore();
});

test("creates a note and its selected-text highlight atomically", async () => {
  const savedHighlight = {
    id: 41,
    attachment_id: 7,
    source_id: 4,
    page_number: 1,
    selected_text: "Selectable text",
    rectangles: [{ x: 0.1, y: 0.1, width: 0.5, height: 0.04 }],
    created_at: "2026-09-01T12:00:00Z",
  };
  highlightMocks.createReaderNote.mockResolvedValue({
    id: 52,
    source_id: 4,
    source_title: "Test paper",
    attachment_id: 7,
    original_filename: "paper.pdf",
    page_number: 1,
    body: "This supports the central claim.",
    highlight: savedHighlight,
    attachment_availability: "available",
    created_at: "2026-09-01T12:01:00Z",
  });
  const rendered = render(
    <PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />,
  );
  await waitFor(() => expect(pdfMocks.textLayerRender).toHaveBeenCalled());

  const selection = selectText(rendered.container);
  fireEvent.click(await screen.findByRole("button", { name: "Write note on selection" }));
  const noteBody = screen.getByLabelText("Note");
  expect(screen.getByRole("button", { name: "Save note" })).toBeDisabled();
  fireEvent.change(noteBody, { target: { value: "This supports the central claim." } });
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));

  await waitFor(() =>
    expect(highlightMocks.createReaderNote).toHaveBeenCalledWith(7, {
      page_number: 1,
      body: "This supports the central claim.",
      new_highlight: {
        selected_text: "Selectable text",
        rectangles: [{ x: 0.1, y: 0.1, width: 0.5, height: 0.04 }],
      },
    }),
  );
  expect(highlightMocks.createHighlight).not.toHaveBeenCalled();
  expect(screen.getByText("This supports the central claim.")).toBeInTheDocument();
  expect(rendered.container.querySelector(".pdf-highlight-rectangle")).toBeInTheDocument();
  expect(selection.removeAllRanges).toHaveBeenCalled();
  selection.getSelection.mockRestore();
});

test("opens at a saved note page and edits the shared note record", async () => {
  const savedNote = {
    id: 52,
    source_id: 4,
    source_title: "Test paper",
    attachment_id: 7,
    original_filename: "paper.pdf",
    page_number: 2,
    body: "Initial note",
    highlight: null,
    attachment_availability: "available",
    created_at: "2026-09-01T12:01:00Z",
  };
  highlightMocks.getReaderNotes.mockResolvedValueOnce([savedNote]);
  highlightMocks.updateReaderNote.mockResolvedValueOnce({
    ...savedNote,
    body: "Revised note",
  });

  render(
    <PdfReader
      attachmentId={7}
      initialPage={2}
      title="Test paper"
      url="http://local.test/paper.pdf"
    />,
  );

  expect(await screen.findByRole("img", { name: "Page 2 of 2" })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Edit note: Initial note" }));
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Revised note" } });
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));

  await waitFor(() =>
    expect(highlightMocks.updateReaderNote).toHaveBeenCalledWith(52, "Revised note"),
  );
  expect(screen.getByText("Revised note")).toBeInTheDocument();
  expect(screen.queryByText("Initial note")).not.toBeInTheDocument();
});

test("keeps a note draft when saving fails", async () => {
  highlightMocks.createReaderNote.mockRejectedValueOnce(new Error("database unavailable"));
  render(<PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />);
  await screen.findByRole("img", { name: "Page 1 of 2" });

  fireEvent.click(screen.getByRole("button", { name: "Write page note" }));
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "Unsaved draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save note" }));

  expect(
    await screen.findByText("The note could not be saved. Your draft remains available."),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Note")).toHaveValue("Unsaved draft");
  expect(screen.getByRole("button", { name: "Save note" })).toBeEnabled();
});

test("keeps a highlight visible when deletion fails", async () => {
  highlightMocks.getHighlights.mockResolvedValueOnce([
    {
      id: 31,
      attachment_id: 7,
      source_id: 4,
      page_number: 1,
      selected_text: "Saved text",
      rectangles: [{ x: 0.1, y: 0.1, width: 0.5, height: 0.04 }],
      created_at: "2026-09-01T12:00:00Z",
    },
  ]);
  highlightMocks.deleteHighlight.mockRejectedValueOnce(new Error("database unavailable"));
  const rendered = render(
    <PdfReader attachmentId={7} title="Test paper" url="http://local.test/paper.pdf" />,
  );

  fireEvent.click(
    await screen.findByRole("button", { name: "Delete highlight: Saved text" }),
  );

  expect(
    await screen.findByText("The highlight could not be deleted and remains saved."),
  ).toBeInTheDocument();
  expect(screen.getByText("Saved text", { selector: "q" })).toBeInTheDocument();
  expect(rendered.container.querySelector(".pdf-highlight-rectangle")).toBeInTheDocument();
});

test("keeps an image-only page readable and explains that highlighting needs selectable text", async () => {
  pdfMocks.getTextContent.mockResolvedValueOnce({ items: [], styles: {} });
  render(
    <PdfReader attachmentId={7} title="Scanned paper" url="http://local.test/scanned.pdf" />,
  );

  expect(await screen.findByRole("img", { name: "Page 1 of 2" })).toBeInTheDocument();
  expect(
    await screen.findByText(
      /This page has no usable selectable text.*Litrev does not run OCR automatically/,
    ),
  ).toBeInTheDocument();
  expect(pdfMocks.textLayerConstructor).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Highlight" })).not.toBeInTheDocument();
});
