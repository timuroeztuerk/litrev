import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { Source } from "./api";

const fetchMock = vi.fn();
const confirmMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("confirm", confirmMock);

const emptySourceMetadata = {
  authors: [],
  publication_year: null,
  venue: null,
  url: null,
  abstract: null,
  language: null,
  reading_status: "unread",
  tags: [],
  collections: [],
} satisfies Partial<Source>;

const paperSource: Source = {
  ...emptySourceMetadata,
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
  can_remove: false,
  created_at: "2026-08-30T00:00:00Z",
  updated_at: "2026-08-30T00:00:00Z",
};

const convertedAttachment = {
  ...pendingAttachment,
  conversion_status: "succeeded",
  has_extracted_text: true,
};

const failedAttachment = {
  ...pendingAttachment,
  conversion_status: "unsupported",
  conversion_message: "Anydoc does not support this document.",
  can_remove: true,
};

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function makeSource(overrides: Partial<Source> & Pick<Source, "id" | "title">): Source {
  return { ...paperSource, doi: null, ...overrides };
}

async function renderLibrary(sources: Source[]) {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response(sources));
  render(<App />);
  await screen.findByText("Local service ready");
}

function sourceOrder(): string[] {
  return screen.getAllByRole("listitem").map((item) => {
    const title = item.querySelector("strong")?.textContent;
    const description = item.querySelector(".source-summary > span")?.textContent;
    return `${title} — ${description}`;
  });
}

beforeEach(() => {
  window.localStorage.clear();
  fetchMock.mockReset();
  confirmMock.mockReset();
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

test("keeps the library-empty state distinct from discovery results", async () => {
  await renderLibrary([]);

  expect(screen.getByText("0 of 0 sources")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { level: 3, name: "Start with one useful source" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "No matching sources" })).not.toBeInTheDocument();
});

test("matches trimmed search text case-insensitively across every searchable field", async () => {
  const searchableSource = makeSource({
    id: 1,
    title: "Quantum Methods",
    authors: ["Ada Lovelace"],
    venue: "Journal of Reliable Systems",
    doi: "10.1234/Discovery.Case",
  });
  const otherSource = makeSource({ id: 2, title: "Unrelated source" });
  await renderLibrary([otherSource, searchableSource]);

  const search = screen.getByLabelText("Search sources");
  for (const query of [
    "  QUANTUM  ",
    "LOVELACE",
    "reliable SYSTEMS",
    "10.1234/discovery.case",
  ]) {
    fireEvent.change(search, { target: { value: query } });
    expect(screen.getByText("1 of 2 sources")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Quantum Methods/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Unrelated source/ })).not.toBeInTheDocument();
  }
});

test("combines source type and reading status filters and clears discovery controls", async () => {
  await renderLibrary([
    makeSource({ id: 1, title: "Read paper", reading_status: "read" }),
    makeSource({ id: 2, title: "Read book", reading_status: "read", source_type: "book" }),
    makeSource({ id: 3, title: "Reading book", reading_status: "reading", source_type: "book" }),
    makeSource({ id: 4, title: "Read report", reading_status: "read", source_type: "other" }),
  ]);

  fireEvent.change(screen.getByLabelText("Source type"), { target: { value: "book" } });
  fireEvent.change(screen.getByLabelText("Reading status"), { target: { value: "read" } });

  expect(screen.getByText("1 of 4 sources")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Read book/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Reading book/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
  expect(screen.getByLabelText("Search sources")).toHaveFocus();
  expect(screen.getByLabelText("Source type")).toHaveValue("all");
  expect(screen.getByLabelText("Reading status")).toHaveValue("all");
  expect(screen.getByText("4 of 4 sources")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search sources"), {
    target: { value: "nothing matches this" },
  });
  expect(screen.getByText("0 of 4 sources")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { level: 3, name: "No matching sources" }),
  ).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(screen.getByLabelText("Search sources")).toHaveValue("");
  expect(screen.getByLabelText("Search sources")).toHaveFocus();
  expect(screen.getByLabelText("Sort by")).toHaveValue("title");
  expect(screen.getByText("4 of 4 sources")).toBeInTheDocument();
});

test("combines reusable tag and collection filters", async () => {
  await renderLibrary([
    makeSource({
      id: 1,
      title: "Methods in thesis",
      tags: ["Methods", "Local AI"],
      collections: ["Thesis"],
    }),
    makeSource({
      id: 2,
      title: "Methods elsewhere",
      tags: ["Methods"],
      collections: ["Background"],
    }),
    makeSource({
      id: 3,
      title: "Evidence in thesis",
      tags: ["Evidence"],
      collections: ["Thesis"],
    }),
  ]);

  expect(screen.getByLabelText("Tag")).toHaveValue("");
  expect(screen.getByRole("option", { name: "Evidence" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Local AI" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Methods" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "Methods" } });
  fireEvent.change(screen.getByLabelText("Collection"), { target: { value: "Thesis" } });

  expect(screen.getByText("1 of 3 sources")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Methods in thesis/ })).toBeInTheDocument();
  expect(screen.getByText("Tags: Methods, Local AI · Collections: Thesis")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Methods elsewhere/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Evidence in thesis/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
  expect(screen.getByLabelText("Tag")).toHaveValue("");
  expect(screen.getByLabelText("Collection")).toHaveValue("");
  expect(screen.getByText("3 of 3 sources")).toBeInTheDocument();
});

test("sorts by title, newest publication year, and recently added with stable ties", async () => {
  await renderLibrary([
    makeSource({
      id: 4,
      title: "Delta",
      authors: ["Undated"],
      publication_year: null,
      created_at: "2026-08-04T00:00:00Z",
    }),
    makeSource({
      id: 3,
      title: "Bravo",
      authors: ["Newest year"],
      publication_year: 2025,
      created_at: "2026-08-03T00:00:00Z",
    }),
    makeSource({
      id: 2,
      title: "Alpha",
      authors: ["Second ID"],
      publication_year: 2024,
      created_at: "2026-08-02T00:00:00Z",
    }),
    makeSource({
      id: 1,
      title: "Alpha",
      authors: ["First ID"],
      publication_year: 2024,
      created_at: "2026-08-02T00:00:00Z",
    }),
  ]);

  expect(sourceOrder()).toEqual([
    "Alpha — First ID · 2024",
    "Alpha — Second ID · 2024",
    "Bravo — Newest year · 2025",
    "Delta — Undated",
  ]);

  fireEvent.change(screen.getByLabelText("Sort by"), {
    target: { value: "publication-year" },
  });
  expect(sourceOrder()).toEqual([
    "Bravo — Newest year · 2025",
    "Alpha — First ID · 2024",
    "Alpha — Second ID · 2024",
    "Delta — Undated",
  ]);

  fireEvent.change(screen.getByLabelText("Sort by"), {
    target: { value: "recently-added" },
  });
  expect(sourceOrder()).toEqual([
    "Delta — Undated",
    "Bravo — Newest year · 2025",
    "Alpha — First ID · 2024",
    "Alpha — Second ID · 2024",
  ]);
});

test("opens the correct filtered source and preserves controls on return", async () => {
  const target = makeSource({
    id: 42,
    title: "Target source",
    source_type: "book",
    reading_status: "read",
    venue: "Discovery Symposium",
    publication_year: 2025,
  });
  await renderLibrary([makeSource({ id: 1, title: "Decoy" }), target]);

  fireEvent.change(screen.getByLabelText("Search sources"), { target: { value: "SYMPOSIUM" } });
  fireEvent.change(screen.getByLabelText("Sort by"), {
    target: { value: "publication-year" },
  });
  fireEvent.change(screen.getByLabelText("Source type"), { target: { value: "book" } });
  fireEvent.change(screen.getByLabelText("Reading status"), { target: { value: "read" } });
  fetchMock.mockResolvedValueOnce(response({ ...target, attachments: [] }));

  fireEvent.click(screen.getByRole("button", { name: /Target source/ }));

  expect(
    await screen.findByRole("heading", { level: 2, name: "Target source" }),
  ).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/sources/42",
    undefined,
  );

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  expect(screen.getByLabelText("Search sources")).toHaveValue("SYMPOSIUM");
  expect(screen.getByLabelText("Sort by")).toHaveValue("publication-year");
  expect(screen.getByLabelText("Source type")).toHaveValue("book");
  expect(screen.getByLabelText("Reading status")).toHaveValue("read");
  expect(screen.getByText("1 of 2 sources")).toBeInTheDocument();
});

test("newly added sources immediately respect active discovery controls", async () => {
  await renderLibrary([makeSource({ id: 1, title: "Existing paper" })]);
  fireEvent.change(screen.getByLabelText("Search sources"), { target: { value: "wanted" } });
  fireEvent.change(screen.getByLabelText("Source type"), { target: { value: "book" } });

  const added = makeSource({ id: 2, title: "Wanted book", source_type: "book" });
  fetchMock.mockResolvedValueOnce(response(added, 201));
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "book" } });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Wanted book" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

  expect(await screen.findByRole("button", { name: /Wanted book/ })).toBeInTheDocument();
  expect(screen.getByText("1 of 2 sources")).toBeInTheDocument();
});

test("newly imported sources immediately respect active discovery controls", async () => {
  const existingBook = makeSource({ id: 1, title: "Existing book", source_type: "book" });
  await renderLibrary([existingBook]);
  fireEvent.change(screen.getByLabelText("Source type"), { target: { value: "book" } });

  const importedSource = makeSource({ id: 8, title: "Imported paper" });
  const importedAttachment = {
    ...pendingAttachment,
    id: 18,
    source_id: importedSource.id,
  };
  fetchMock
    .mockResolvedValueOnce(
      response({ source: importedSource, attachment: importedAttachment }, 201),
    )
    .mockResolvedValueOnce(
      response({ ...importedAttachment, conversion_status: "succeeded", has_extracted_text: true }),
    );

  const file = new File(["%PDF example"], "imported-paper.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText("Source title"), { target: { value: "Imported paper" } });
  fireEvent.click(screen.getByRole("button", { name: "Save and extract" }));
  await screen.findByText("Extracted text ready");

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  expect(screen.getByLabelText("Source type")).toHaveValue("book");
  expect(screen.getByText("1 of 2 sources")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Imported paper/ })).not.toBeInTheDocument();
});

test("edited sources immediately respect active discovery controls", async () => {
  const readSource = makeSource({
    id: 9,
    title: "Read source",
    reading_status: "read",
    tags: ["Methods"],
  });
  await renderLibrary([readSource]);
  fireEvent.change(screen.getByLabelText("Reading status"), { target: { value: "read" } });
  fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "Methods" } });
  fetchMock.mockResolvedValueOnce(response({ ...readSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Read source/ }));
  await screen.findByRole("heading", { level: 3, name: "Metadata" });

  fireEvent.click(screen.getByRole("button", { name: "Edit source" }));
  fireEvent.change(screen.getByLabelText("Reading status"), { target: { value: "unread" } });
  fireEvent.change(screen.getByLabelText("Tags (one per line)"), {
    target: { value: "Evidence" },
  });
  fetchMock.mockResolvedValueOnce(
    response({ ...readSource, reading_status: "unread", tags: ["Evidence"], attachments: [] }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Source metadata saved.");

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  expect(screen.getByLabelText("Reading status")).toHaveValue("read");
  expect(screen.getByLabelText("Tag")).toHaveValue("Methods");
  expect(screen.getByText("0 of 1 sources")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { level: 3, name: "No matching sources" }),
  ).toBeInTheDocument();
});

test("quickly captures a book through the local API", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({
      ...emptySourceMetadata,
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

test("edits source metadata and updates the library summary", async () => {
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
  expect(await screen.findByRole("heading", { level: 3, name: "Metadata" })).toBeInTheDocument();
  expect(screen.getAllByText("Unread")).toHaveLength(2);

  fireEvent.click(screen.getByRole("button", { name: "Edit source" }));
  expect(screen.getByLabelText("Title")).toHaveFocus();
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "book" } });
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Revised source" } });
  fireEvent.change(screen.getByLabelText("Authors (one per line)"), {
    target: { value: "Alice Author\nResearch Collective" },
  });
  fireEvent.change(screen.getByLabelText("Year"), { target: { value: "2026" } });
  fireEvent.change(screen.getByLabelText("Venue"), { target: { value: "Evidence Press" } });
  fireEvent.change(screen.getByLabelText("DOI"), { target: { value: "10.1234/revised" } });
  fireEvent.change(screen.getByLabelText("URL"), {
    target: { value: "https://example.org/revised" },
  });
  fireEvent.change(screen.getByLabelText("Language"), { target: { value: "en" } });
  fireEvent.change(screen.getByLabelText("Reading status"), {
    target: { value: "reading" },
  });
  fireEvent.change(screen.getByLabelText("Abstract"), {
    target: { value: "A useful abstract." },
  });
  fireEvent.change(screen.getByLabelText("Tags (one per line)"), {
    target: { value: "Methods\nLocal AI" },
  });
  fireEvent.change(screen.getByLabelText("Collections (one per line)"), {
    target: { value: "Thesis\nChapter 1" },
  });

  const updatedSource = {
    ...paperSource,
    source_type: "book",
    title: "Revised source",
    authors: ["Alice Author", "Research Collective"],
    publication_year: 2026,
    venue: "Evidence Press",
    doi: "10.1234/revised",
    url: "https://example.org/revised",
    abstract: "A useful abstract.",
    language: "en",
    reading_status: "reading",
    tags: ["Local AI", "Methods"],
    collections: ["Chapter 1", "Thesis"],
    attachments: [convertedAttachment],
  };
  let resolveUpdate!: (value: unknown) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveUpdate = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(screen.getByRole("button", { name: "Saving…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Back to library/ })).toBeDisabled();
  await act(async () => {
    resolveUpdate(response(updatedSource));
  });

  expect(await screen.findByText("Source metadata saved.")).toHaveAttribute("role", "status");
  expect(screen.getByRole("heading", { level: 2, name: "Revised source" })).toBeInTheDocument();
  expect(screen.getByText("Alice Author, Research Collective")).toBeInTheDocument();
  expect(screen.getByText("Evidence Press")).toBeInTheDocument();
  expect(screen.getAllByText("Reading")).toHaveLength(2);
  expect(screen.getByText("Local AI, Methods")).toBeInTheDocument();
  expect(screen.getByText("Chapter 1, Thesis")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "https://example.org/revised" })).toHaveAttribute(
    "href",
    "https://example.org/revised",
  );
  expect(screen.getByRole("button", { name: "Edit source" })).toHaveFocus();

  const updateCall = fetchMock.mock.calls[3];
  expect(updateCall[0]).toBe("http://127.0.0.1:8765/api/sources/7");
  expect(updateCall[1]).toEqual(expect.objectContaining({ method: "PUT" }));
  expect(JSON.parse((updateCall[1] as RequestInit).body as string)).toEqual({
    source_type: "book",
    title: "Revised source",
    authors: ["Alice Author", "Research Collective"],
    publication_year: 2026,
    venue: "Evidence Press",
    doi: "10.1234/revised",
    url: "https://example.org/revised",
    abstract: "A useful abstract.",
    language: "en",
    reading_status: "reading",
    tags: ["Methods", "Local AI"],
    collections: ["Thesis", "Chapter 1"],
  });

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  const source = screen.getByRole("listitem");
  expect(within(source).getByText("Revised source")).toBeInTheDocument();
  expect(within(source).getByText("Alice Author, Research Collective · 2026")).toBeInTheDocument();
});

test("keeps metadata edits open when the API rejects them", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(response({ ...paperSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByRole("heading", { level: 3, name: "Metadata" });
  fireEvent.click(screen.getByRole("button", { name: "Edit source" }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Unsaved title" } });
  fetchMock.mockResolvedValueOnce(
    response({ detail: "A source with this DOI already exists." }, 409),
  );

  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "A source with this DOI already exists.",
  );
  expect(screen.getByLabelText("Title")).toHaveValue("Unsaved title");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();
});

test("cancels metadata edits and returns focus to the edit control", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(response({ ...paperSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByRole("heading", { level: 3, name: "Metadata" });
  fireEvent.click(screen.getByRole("button", { name: "Edit source" }));
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Discarded title" } });

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(screen.queryByDisplayValue("Discarded title")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit source" })).toHaveFocus();
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("keeps a specific conversion failure visible and retryable", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  const needsOcrAttachment = {
    ...pendingAttachment,
    conversion_status: "needs_ocr",
    conversion_message: "This document contains scanned pages that need OCR.",
    conversion_diagnostics: { pages: [2] },
    can_remove: true,
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

test("confirms and removes a failed document from its source", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [failedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  expect(await screen.findByText("Unsupported format")).toBeInTheDocument();

  confirmMock.mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole("button", { name: "Remove failed document" }));
  expect(confirmMock).toHaveBeenCalledWith(
    "Remove “paper.pdf” and its saved local files? This cannot be undone.",
  );
  expect(fetchMock).toHaveBeenCalledTimes(3);

  confirmMock.mockReturnValueOnce(true);
  fetchMock.mockResolvedValueOnce(response(undefined, 204));
  fireEvent.click(screen.getByRole("button", { name: "Remove failed document" }));

  expect(await screen.findByText("No document is attached to this source yet.")).toBeInTheDocument();
  expect(screen.getByText("Removed “paper.pdf” and its saved files.")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.getByRole("heading", { level: 3, name: "Documents" })).toHaveFocus();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/attachments/11",
    { method: "DELETE" },
  );
});

test("keeps a failed document visible when removal fails safely", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [failedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByText("Unsupported format");

  confirmMock.mockReturnValueOnce(true);
  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "attachment_removal_failed",
          message: "The failed document could not be removed; its saved files were restored.",
        },
      },
      500,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove failed document" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The failed document could not be removed; its saved files were restored.",
  );
  expect(screen.getByText("paper.pdf")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove failed document" })).toBeEnabled();
});

test("removes the document row when only temporary cleanup remains", async () => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([paperSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [failedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByText("Unsupported format");

  confirmMock.mockReturnValueOnce(true);
  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "attachment_cleanup_incomplete",
          message: "The document was removed, but temporary file cleanup did not finish.",
        },
      },
      500,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Remove failed document" }));

  expect(await screen.findByText("No document is attached to this source yet.")).toBeInTheDocument();
  expect(
    screen.getByText("The document was removed, but temporary file cleanup did not finish."),
  ).toHaveAttribute("role", "status");
  expect(screen.queryByRole("button", { name: "Remove failed document" })).not.toBeInTheDocument();
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
