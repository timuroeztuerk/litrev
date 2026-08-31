import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

const paperSource = {
  id: 7,
  source_type: "paper",
  title: "Confirmed paper",
  doi: null,
  created_at: "2026-08-30T00:00:00Z",
};

const pendingAttachment = {
  id: 11,
  source_id: paperSource.id,
  original_filename: "paper.pdf",
  media_type: "application/pdf",
  byte_size: 12,
  detected_format: "pdf",
  conversion_status: "pending",
  conversion_message: null,
  conversion_diagnostics: null,
  has_extracted_text: false,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

const convertedAttachment = {
  ...pendingAttachment,
  conversion_status: "succeeded",
  has_extracted_text: true,
};

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([]));
});

afterEach(cleanup);

test("defaults to dark mode and saves a light-mode preference", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  const toggle = screen.getByRole("button", { name: "Switch to light mode" });

  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("main").closest(".app-shell")).toHaveAttribute("data-theme", "dark");

  fireEvent.click(toggle);

  expect(screen.getByRole("button", { name: "Switch to dark mode" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("main").closest(".app-shell")).toHaveAttribute("data-theme", "light");
  expect(window.localStorage.getItem("litrev-theme")).toBe("light");
});

test("uses the saved theme preference", async () => {
  window.localStorage.setItem("litrev-theme", "light");

  render(<App />);
  await screen.findByText("Local service ready");

  expect(screen.getByRole("button", { name: "Switch to dark mode" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("main").closest(".app-shell")).toHaveAttribute("data-theme", "light");
});

test("quickly captures a book through the local API", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({
      id: 1,
      source_type: "book",
      title: "The Dawn of Everything",
      doi: null,
      created_at: "2026-08-30T00:00:00Z",
    }),
  );

  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "book" } });
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "The Dawn of Everything" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

  const source = await screen.findByRole("listitem");
  expect(within(source).getByText("The Dawn of Everything")).toBeInTheDocument();
  expect(within(source).getByText("Book")).toBeInTheDocument();
  expect(screen.getByText("Added “The Dawn of Everything” as a book.")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.getByLabelText("Title")).toHaveValue("");

  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/sources",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ source_type: "book", title: "The Dawn of Everything" }),
    }),
  );
});

test("keeps the title and explains when a source cannot be saved", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "A useful paper" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The source could not be saved. Check the local service and try again.",
  );
  expect(screen.getByLabelText("Title")).toHaveValue("A useful paper");
  expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
});

test("requires a source title before making a request", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

  expect(screen.getByRole("alert")).toHaveTextContent("Enter a title to add this source.");
  expect(screen.getByLabelText("Title")).toHaveFocus();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("inspects, confirms, saves, converts, and reopens an imported document", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock
    .mockResolvedValueOnce(
      response({ source: paperSource, attachment: pendingAttachment }, 201),
    )
    .mockResolvedValueOnce(response(convertedAttachment));

  const file = new File(["%PDF example"], "paper.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });

  expect(screen.getByText("paper.pdf")).toBeInTheDocument();
  expect(screen.getByText("PDF")).toBeInTheDocument();
  expect(screen.getByLabelText("Source title")).toHaveValue("paper");
  fireEvent.change(screen.getByLabelText("Source title"), {
    target: { value: "Confirmed paper" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save and extract" }));

  expect(await screen.findByText("Extracted text ready")).toBeInTheDocument();
  expect(screen.getByText("The original and extracted text are saved locally.")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.getByRole("heading", { level: 2, name: "Confirmed paper" })).toHaveFocus();

  const importCall = fetchMock.mock.calls[2];
  expect(importCall[0]).toBe("http://127.0.0.1:8765/api/imports");
  const form = (importCall[1] as RequestInit).body as FormData;
  expect(form.get("source_type")).toBe("paper");
  expect(form.get("title")).toBe("Confirmed paper");
  expect(form.get("document")).toBe(file);
  expect(fetchMock.mock.calls[3][0]).toBe(
    "http://127.0.0.1:8765/api/attachments/11/convert",
  );

  fetchMock.mockResolvedValueOnce(
    response({ attachment_id: 11, markdown: "# Converted paper\n\nA useful finding." }),
  );
  fireEvent.click(screen.getByRole("button", { name: "View extracted text" }));
  expect(await screen.findByText("Converted paper")).toBeInTheDocument();
  expect(screen.getByText("A useful finding.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  expect(screen.queryByText("Converted paper")).not.toBeInTheDocument();
  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [convertedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));

  expect(await screen.findByText("Extracted text ready")).toBeInTheDocument();
  expect(screen.queryByText("Converted paper")).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/sources/7",
    undefined,
  );
});

test("shows the real save and extraction stages", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  let resolveImport!: (value: unknown) => void;
  let resolveConversion!: (value: unknown) => void;
  const importRequest = new Promise((resolve) => {
    resolveImport = resolve;
  });
  const conversionRequest = new Promise((resolve) => {
    resolveConversion = resolve;
  });
  fetchMock
    .mockImplementationOnce(() => importRequest)
    .mockImplementationOnce(() => conversionRequest);

  const file = new File(["paper,year"], "paper.csv", { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Save and extract" }));

  expect(screen.getByRole("button", { name: "Saving original…" })).toBeDisabled();
  expect(screen.getByText("Save original locally").closest("li")).toHaveClass("current");

  await act(async () => {
    resolveImport(response({ source: paperSource, attachment: pendingAttachment }, 201));
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Extracting text…" })).toBeDisabled(),
  );
  expect(screen.getByText("Extract text with Anydoc").closest("li")).toHaveClass("current");

  await act(async () => {
    resolveConversion(response(convertedAttachment));
  });
  expect(await screen.findByText("Extracted text ready")).toBeInTheDocument();
});

test("opens the existing source when duplicate bytes are reported", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock
    .mockResolvedValueOnce(
      response(
        {
          detail: {
            code: "duplicate",
            message: "This document is already in the library.",
            source_id: paperSource.id,
            attachment_id: pendingAttachment.id,
          },
        },
        409,
      ),
    )
    .mockResolvedValueOnce(response({ ...paperSource, attachments: [convertedAttachment] }));

  const file = new File(["same bytes"], "duplicate.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Save and extract" }));

  expect(
    await screen.findByText("This document is already in your library; the existing source is open."),
  ).toBeInTheDocument();
  expect(screen.getByText("Extracted text ready")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

test("keeps a specific conversion failure visible and retryable", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  const needsOcrAttachment = {
    ...pendingAttachment,
    conversion_status: "needs_ocr",
    conversion_message: "This document contains scanned pages that need OCR.",
    conversion_diagnostics: { pages: [2] },
  };
  fetchMock
    .mockResolvedValueOnce(response({ source: paperSource, attachment: pendingAttachment }, 201))
    .mockResolvedValueOnce(response(needsOcrAttachment));

  const file = new File(["%PDF scan"], "paper.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Save and extract" }));

  expect(await screen.findByText("OCR required")).toBeInTheDocument();
  expect(screen.getByText("This document contains scanned pages that need OCR.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry extraction" })).toBeEnabled();

  fetchMock.mockResolvedValueOnce(response(convertedAttachment));
  fireEvent.click(screen.getByRole("button", { name: "Retry extraction" }));

  expect(await screen.findByText("Extracted text is now saved locally.")).toBeInTheDocument();
  expect(screen.getByText("Extracted text ready")).toBeInTheDocument();
});

test("explains an oversized import without losing the selected file", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "oversized",
          message: "Documents are limited to 50 MB.",
          maximum_byte_size: 52_428_800,
        },
      },
      413,
    ),
  );

  const file = new File(["large"], "large.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Save and extract" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Documents are limited to 50 MB.",
  );
  expect(screen.getByText("large.pdf")).toBeInTheDocument();
  expect(screen.getByLabelText("Source title")).toHaveValue("large");
});

test("retries startup after the local service becomes available", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockRejectedValueOnce(new TypeError("service is starting"))
    .mockRejectedValueOnce(new TypeError("service is starting"))
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([]));

  render(<App />);

  await screen.findByText("Local service unavailable");
  expect(screen.getByRole("button", { name: "Add to library" })).toBeDisabled();

  const file = new File(["paper,year"], "paper.csv", { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  expect(screen.getByRole("button", { name: "Save and extract" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: "Retry local service" }));

  expect(screen.getByText("Connecting locally")).toBeInTheDocument();
  await screen.findByText("Local service ready");
  expect(screen.getByRole("button", { name: "Add to library" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Save and extract" })).toBeEnabled();
});

test("cancels the discarded startup requests used by StrictMode", async () => {
  fetchMock.mockReset();
  const waitForAbort = (_url: string, init: RequestInit) =>
    new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
  fetchMock
    .mockImplementationOnce(waitForAbort)
    .mockImplementationOnce(waitForAbort)
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([]));

  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  await screen.findByText("Local service ready");
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect((fetchMock.mock.calls[0][1] as RequestInit).signal?.aborted).toBe(true);
  expect((fetchMock.mock.calls[1][1] as RequestInit).signal?.aborted).toBe(true);
  expect(screen.queryByText("Local service unavailable")).not.toBeInTheDocument();
});

test("does not load remote images from persisted extracted text", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [convertedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByText("Extracted text ready");

  fetchMock.mockResolvedValueOnce(
    response({
      attachment_id: convertedAttachment.id,
      markdown: "![Remote figure](https://example.invalid/tracker.png)",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "View extracted text" }));

  expect(
    await screen.findByText("Image omitted from local preview: Remote figure"),
  ).toBeInTheDocument();
  expect(document.querySelector("img")).toBeNull();
  expect(document.querySelector('link[rel="preload"][as="image"]')).toBeNull();
});

test("clears an attachment preview before opening another attachment", async () => {
  const secondAttachment = {
    ...convertedAttachment,
    id: 12,
    original_filename: "supplement.pdf",
  };
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [convertedAttachment, secondAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  expect(await screen.findAllByText("Extracted text ready")).toHaveLength(2);

  fetchMock.mockResolvedValueOnce(
    response({ attachment_id: convertedAttachment.id, markdown: "# First attachment" }),
  );
  fireEvent.click(screen.getAllByRole("button", { name: "View extracted text" })[0]);
  expect(await screen.findByText("First attachment")).toBeInTheDocument();

  let resolveSecondRequest!: (value: unknown) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveSecondRequest = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "View extracted text" }));

  expect(screen.queryByText("First attachment")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Opening text…" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: "View extracted text" })).toHaveLength(1);

  await act(async () => {
    resolveSecondRequest(
      response(
        {
          detail: {
            code: "managed_file_conflict",
            message: "The extracted text is missing or has changed.",
          },
        },
        409,
      ),
    );
  });

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The extracted text is missing or has changed.",
  );
  expect(screen.queryByText("First attachment")).not.toBeInTheDocument();
});

test("ignores an extracted-text response after navigating to another source", async () => {
  const secondSource = { ...paperSource, id: 8, title: "Second paper" };
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource, secondSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [convertedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByText("Extracted text ready");

  let resolveTextRequest!: (value: unknown) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveTextRequest = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "View extracted text" }));
  expect(screen.getByRole("button", { name: "Opening text…" })).toBeDisabled();

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  fetchMock.mockResolvedValueOnce(response({ ...secondSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Second paper/ }));
  await screen.findByText("No document is attached to this source yet.");

  await act(async () => {
    resolveTextRequest(
      response({ attachment_id: convertedAttachment.id, markdown: "# First attachment" }),
    );
  });

  expect(screen.queryByText("First attachment")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Opening text…" })).not.toBeInTheDocument();
});
