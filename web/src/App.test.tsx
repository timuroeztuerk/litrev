import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import type { Source, SourceDetail } from "./api";

const fetchMock = vi.fn();
const confirmMock = vi.fn();
const createObjectURLMock = vi.fn(() => "blob:litrev-library");
const revokeObjectURLMock = vi.fn();
const anchorClickMock = vi
  .spyOn(HTMLAnchorElement.prototype, "click")
  .mockImplementation(() => undefined);
vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("confirm", confirmMock);
Object.defineProperty(URL, "createObjectURL", {
  configurable: true,
  value: createObjectURLMock,
});
Object.defineProperty(URL, "revokeObjectURL", {
  configurable: true,
  value: revokeObjectURLMock,
});

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
  identifiers: [],
  citation_keys: [],
} satisfies Partial<Source>;

const paperSource: Source & Pick<SourceDetail, "metadata_provenance"> = {
  ...emptySourceMetadata,
  id: 7,
  source_type: "paper",
  title: "Confirmed paper",
  doi: null,
  created_at: "2026-08-30T00:00:00Z",
  metadata_provenance: [],
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
    blob: async () => (body instanceof Blob ? body : new Blob([JSON.stringify(body)])),
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

function openLibraryData() {
  const summary = screen.getByText("Library data").closest("summary");
  if (!summary) throw new Error("Library data summary was not rendered.");
  fireEvent.click(summary);
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
  createObjectURLMock.mockClear();
  revokeObjectURLMock.mockClear();
  anchorClickMock.mockClear();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([]));
});

afterEach(cleanup);

test("defaults to dark mode and saves a light-mode preference", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  const settingsButton = screen.getByRole("button", { name: "Settings" });
  expect(settingsButton.closest(".sidebar-footer")).not.toBeNull();
  fireEvent.click(settingsButton);

  expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toHaveFocus();
  expect(settingsButton).toHaveAttribute("aria-current", "page");
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
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));

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
  fireEvent.click(screen.getByRole("button", { name: "Add source" }));

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

test("keeps secondary capture workflows compact until the user opens them", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  expect(screen.getByPlaceholderText("Enter a book or paper title")).toBeInTheDocument();
  expect(screen.queryByLabelText("Source title")).not.toBeInTheDocument();
  expect(screen.getByText("Library data").closest("details")).not.toHaveAttribute("open");

  const file = new File(["%PDF example"], "compact-paper.pdf", {
    type: "application/pdf",
  });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });

  expect(screen.getByRole("heading", { level: 3, name: "compact-paper.pdf" })).toBeInTheDocument();
  expect(screen.getByLabelText("Source title")).toHaveValue("compact paper");
  expect(screen.getByLabelText("Source title")).toHaveFocus();
  expect(screen.getByRole("button", { name: "Choose another" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByLabelText("Source title")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Import document" })).toHaveFocus();

  openLibraryData();
  expect(screen.getByText("Library data").closest("details")).toHaveAttribute("open");
  expect(screen.getByRole("button", { name: "Import bibliography" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Export library" })).toBeInTheDocument();
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
  fireEvent.click(screen.getByRole("button", { name: "Add source" }));

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
  fireEvent.click(screen.getByRole("button", { name: "Add source" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The source could not be saved. Check the local service and try again.",
  );
  expect(screen.getByLabelText("Title")).toHaveValue("A useful paper");
  expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
});

test("requires a source title before making a request", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fireEvent.click(screen.getByRole("button", { name: "Add source" }));

  expect(screen.getByRole("alert")).toHaveTextContent("Enter a title to add this source.");
  expect(screen.getByLabelText("Title")).toHaveFocus();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("imports a bibliography, reports DOI skips, and adds sources to the library", async () => {
  render(<App />);
  await screen.findByText("Local service ready");
  openLibraryData();

  const first = makeSource({ id: 21, title: "Imported paper", doi: "10.1234/paper" });
  const second = makeSource({
    id: 22,
    title: "Imported book",
    source_type: "book",
    doi: "10.1234/book",
  });
  let finishImport: ((value: ReturnType<typeof response>) => void) | undefined;
  fetchMock.mockReturnValueOnce(
    new Promise((resolve) => {
      finishImport = resolve;
    }),
  );

  const file = new File(["@article{example}"], "references.bib", {
    type: "application/x-bibtex",
  });
  fireEvent.change(screen.getByLabelText("Choose a bibliography"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Import bibliography" }));

  expect(screen.getByRole("button", { name: "Importing…" })).toBeDisabled();
  await act(async () => {
    finishImport?.(
      response({
        bibliography_format: "bibtex",
        total_entries: 3,
        imported: [first, second],
        skipped: [
          {
            entry_id: "duplicate",
            title: "Already saved",
            doi: "10.1234/existing",
            reason: "existing_doi",
          },
        ],
      }),
    );
  });

  expect(
    await screen.findByText(
      "Imported 2 sources from “references.bib”. 1 duplicate DOI was skipped; existing sources were not changed.",
    ),
  ).toHaveAttribute("role", "status");
  expect(screen.getByRole("button", { name: /Imported paper/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Imported book/ })).toBeInTheDocument();
  expect(screen.getByText("2 of 2 sources")).toBeInTheDocument();

  const [, request] = fetchMock.mock.calls.at(-1) ?? [];
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/bibliography-imports",
    expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
  );
  expect((request?.body as FormData).get("bibliography")).toBe(file);
  expect(screen.getByLabelText("Choose a bibliography")).toHaveValue("");
});

test("requires a bibliography selection before making a request", async () => {
  render(<App />);
  await screen.findByText("Local service ready");
  openLibraryData();

  fireEvent.click(screen.getByRole("button", { name: "Import bibliography" }));

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Choose a BibTeX, RIS, or CSL JSON file to import.",
  );
  expect(screen.getByLabelText("Choose a bibliography")).toHaveFocus();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("keeps a bibliography selected when the service rejects it", async () => {
  render(<App />);
  await screen.findByText("Local service ready");
  openLibraryData();

  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "malformed_bibliography",
          message: "The BibTeX file could not be parsed.",
        },
      },
      422,
    ),
  );
  const file = new File(["@article{broken"], "broken.bib", {
    type: "application/x-bibtex",
  });
  const input = screen.getByLabelText("Choose a bibliography") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Import bibliography" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The BibTeX file could not be parsed.",
  );
  expect(input.files?.[0]).toBe(file);
  expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
});

test("exports the selected format with loading and browser download feedback", async () => {
  render(<App />);
  await screen.findByText("Local service ready");
  openLibraryData();

  const bibliographyBlob = new Blob(['[{"title":"Evidence"}]'], {
    type: "application/vnd.citationstyles.csl+json",
  });
  let finishExport: ((value: ReturnType<typeof response>) => void) | undefined;
  fetchMock.mockReturnValueOnce(
    new Promise((resolve) => {
      finishExport = resolve;
    }),
  );
  let clickedDownload: { download: string; href: string } | null = null;
  anchorClickMock.mockImplementationOnce(function captureDownload(this: HTMLAnchorElement) {
    clickedDownload = { download: this.download, href: this.href };
  });

  fireEvent.change(screen.getByLabelText("Export format"), {
    target: { value: "csl-json" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Export library" }));

  expect(screen.getByRole("button", { name: "Exporting…" })).toBeDisabled();
  expect(screen.getByLabelText("Export format")).toBeDisabled();
  await act(async () => {
    finishExport?.(response(bibliographyBlob));
  });

  expect(createObjectURLMock).toHaveBeenCalledWith(bibliographyBlob);
  expect(createObjectURLMock).toHaveReturnedWith("blob:litrev-library");
  expect(revokeObjectURLMock).toHaveBeenCalledWith("blob:litrev-library");
  expect(anchorClickMock).toHaveBeenCalledOnce();
  expect(await screen.findByText("Downloaded the library as CSL JSON.")).toHaveAttribute(
    "role",
    "status",
  );
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/bibliography-exports/csl-json",
  );
  expect(clickedDownload).toEqual({
    download: "litrev-library.json",
    href: "blob:litrev-library",
  });
  expect(screen.getByRole("button", { name: "Export library" })).toBeEnabled();
  expect(screen.getByLabelText("Export format")).toBeEnabled();
});

test("reports an empty library and returns focus to the export format", async () => {
  render(<App />);
  await screen.findByText("Local service ready");
  openLibraryData();

  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "empty_library",
          message: "The library has no sources to export.",
        },
      },
      404,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Export library" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The library has no sources to export.",
  );
  await waitFor(() => expect(screen.getByLabelText("Export format")).toHaveFocus());
  expect(screen.getByLabelText("Export format")).toHaveAttribute("aria-invalid", "true");
  expect(createObjectURLMock).not.toHaveBeenCalled();
});

test("reports a service failure when the library cannot be exported", async () => {
  render(<App />);
  await screen.findByText("Local service ready");
  openLibraryData();

  fetchMock.mockResolvedValueOnce(response({}, 500));
  fireEvent.click(screen.getByRole("button", { name: "Export library" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The library could not be exported. Check the local service and try again.",
  );
  await waitFor(() => expect(screen.getByLabelText("Export format")).toHaveFocus());
  expect(screen.getByRole("button", { name: "Export library" })).toBeEnabled();
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
  const identifiedSource: Source = {
    ...paperSource,
    identifiers: [{ identifier_type: "isbn", value: "978-1-4028-9462-6" }],
    citation_keys: [{ bibliography_format: "bibtex", value: "original-key" }],
  };
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce(response({ status: "ok", technology: {} }))
    .mockResolvedValueOnce(response([identifiedSource]));
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce(
    response({ ...identifiedSource, attachments: [convertedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  expect(await screen.findByRole("heading", { level: 3, name: "Metadata" })).toBeInTheDocument();
  expect(screen.getAllByText("Unread")).toHaveLength(2);
  expect(screen.getByText(/978-1-4028-9462-6/)).toBeInTheDocument();
  expect(screen.getByText(/original-key/)).toBeInTheDocument();

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
  fireEvent.change(screen.getByLabelText("Identifiers (one per line)"), {
    target: { value: "pmid: 12345\narXiv: 2501.01234" },
  });
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
    ...identifiedSource,
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
    identifiers: [
      { identifier_type: "arxiv", value: "2501.01234" },
      { identifier_type: "pmid", value: "12345" },
    ],
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
  expect(screen.getByText(/2501.01234/)).toBeInTheDocument();
  expect(screen.getByText(/12345/)).toBeInTheDocument();
  expect(screen.getByText(/original-key/)).toBeInTheDocument();
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
    identifiers: [
      { identifier_type: "pmid", value: "12345" },
      { identifier_type: "arXiv", value: "2501.01234" },
    ],
  });

  fireEvent.click(screen.getByRole("button", { name: /Back to library/ }));
  const source = screen.getByRole("listitem");
  expect(within(source).getByText("Revised source")).toBeInTheDocument();
  expect(within(source).getByText("Alice Author, Research Collective · 2026")).toBeInTheDocument();
});

test("keeps malformed identifier edits local and focuses the identifier field", async () => {
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
  const identifiers = screen.getByLabelText("Identifiers (one per line)");
  fireEvent.change(identifiers, { target: { value: "pmid" } });

  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Identifier line 1 must use “type: value”.",
  );
  expect(identifiers).toHaveFocus();
  expect(fetchMock).toHaveBeenCalledTimes(3);
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

test("looks up DOI metadata only on request and applies reviewed fields", async () => {
  const doiSource = {
    ...paperSource,
    title: "User title",
    authors: ["User Author"],
    doi: "10.1234/example",
    identifiers: [{ identifier_type: "pmid", value: "12345" }],
  };
  await renderLibrary([doiSource]);
  fetchMock.mockResolvedValueOnce(response({ ...doiSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /User title/ }));
  await screen.findByRole("heading", { level: 3, name: "DOI metadata" });

  expect(fetchMock).toHaveBeenCalledTimes(3);
  let resolveLookup!: (value: unknown) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveLookup = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Look up DOI metadata" }));
  expect(screen.getByRole("button", { name: "Looking up…" })).toBeDisabled();

  await act(async () => {
    resolveLookup(
      response({
        id: 31,
        provider: "Crossref",
        provider_url: "https://api.crossref.org/works/10.1234%2Fexample",
        requested_doi: "10.1234/example",
        retrieved_doi: "10.1234/example",
        retrieved_at: "2026-08-31T12:00:00Z",
        proposal: {
          source_type: null,
          title: "Crossref title",
          authors: ["Provider Author"],
          publication_year: null,
          venue: null,
          url: null,
          abstract: null,
          language: null,
          identifiers: [{ identifier_type: "issn", value: "1234-5678" }],
        },
        available_fields: ["title", "authors", "identifiers"],
        conflicting_fields: ["title", "authors"],
      }),
    );
  });

  const reviewHeading = await screen.findByRole("heading", {
    level: 4,
    name: "Review metadata from Crossref",
  });
  expect(reviewHeading).toHaveFocus();
  const titleField = screen.getByRole("checkbox", { name: /Title/ });
  const authorField = screen.getByRole("checkbox", { name: /Authors/ });
  const identifierField = screen.getByRole("checkbox", { name: /Identifiers/ });
  expect(titleField).not.toBeChecked();
  expect(authorField).not.toBeChecked();
  expect(identifierField).toBeChecked();
  expect(screen.getAllByText("Conflict")).toHaveLength(2);
  fireEvent.click(titleField);

  const updatedSource = {
    ...doiSource,
    title: "Crossref title",
    identifiers: [
      { identifier_type: "issn", value: "1234-5678" },
      { identifier_type: "pmid", value: "12345" },
    ],
    attachments: [],
    metadata_provenance: [
      {
        lookup_id: 31,
        provider: "Crossref",
        provider_url: "https://api.crossref.org/works/10.1234%2Fexample",
        requested_doi: "10.1234/example",
        retrieved_doi: "10.1234/example",
        retrieved_at: "2026-08-31T12:00:00Z",
        applied_fields: ["title", "identifiers"],
        applied_at: "2026-08-31T12:02:00Z",
      },
    ],
  };
  fetchMock.mockResolvedValueOnce(response(updatedSource));
  fireEvent.click(screen.getByRole("button", { name: "Apply selected fields" }));

  expect(await screen.findByText("Applied 2 fields from Crossref.")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.getByRole("heading", { level: 2, name: "Crossref title" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Applied metadata provenance" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Crossref" })).toHaveAttribute(
    "href",
    "https://api.crossref.org/works/10.1234%2Fexample",
  );
  expect(screen.getByRole("button", { name: "Look up DOI metadata" })).toHaveFocus();

  expect(fetchMock.mock.calls[3]).toEqual([
    "http://127.0.0.1:8765/api/sources/7/doi-metadata-lookups",
    { method: "POST" },
  ]);
  const applyCall = fetchMock.mock.calls[4];
  expect(applyCall[0]).toBe(
    "http://127.0.0.1:8765/api/sources/7/doi-metadata-lookups/31/apply",
  );
  expect(applyCall[1]).toEqual(expect.objectContaining({ method: "POST" }));
  expect(JSON.parse((applyCall[1] as RequestInit).body as string)).toEqual({
    fields: ["identifiers", "title"],
  });
});

test("keeps DOI lookup unavailable until the source has a saved DOI", async () => {
  await renderLibrary([paperSource]);
  fetchMock.mockResolvedValueOnce(response({ ...paperSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByRole("heading", { level: 3, name: "DOI metadata" });

  expect(screen.getByRole("button", { name: "Look up DOI metadata" })).toBeDisabled();
  expect(screen.getByText("Add and save a DOI to enable lookup.")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test("keeps a DOI provider error actionable and returns focus to lookup", async () => {
  const doiSource = { ...paperSource, doi: "10.1234/missing" };
  await renderLibrary([doiSource]);
  fetchMock.mockResolvedValueOnce(response({ ...doiSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByRole("heading", { level: 3, name: "DOI metadata" });
  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "doi_metadata_not_found",
          message: "Crossref has no metadata for this DOI.",
        },
      },
      404,
    ),
  );

  fireEvent.click(screen.getByRole("button", { name: "Look up DOI metadata" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Crossref has no metadata for this DOI.",
  );
  expect(screen.getByRole("button", { name: "Look up DOI metadata" })).toHaveFocus();
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

test("confirms source deletion and removes it from the library", async () => {
  const retainedSource = makeSource({ id: 8, title: "Retained paper" });
  await renderLibrary([paperSource, retainedSource]);

  fetchMock.mockResolvedValueOnce(
    response({ ...paperSource, attachments: [convertedAttachment] }),
  );
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  expect(await screen.findByRole("button", { name: "Delete source" })).toBeEnabled();

  confirmMock.mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole("button", { name: "Delete source" }));
  expect(confirmMock).toHaveBeenCalledWith(
    "Delete source “Confirmed paper”? Its metadata and notes will be permanently removed, and it will be unlinked from tags and collections. This also deletes 1 saved document, including originals and extracted text. This cannot be undone.",
  );
  expect(fetchMock).toHaveBeenCalledTimes(3);

  confirmMock.mockReturnValueOnce(true);
  let resolveDelete!: (value: unknown) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveDelete = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete source" }));

  expect(screen.getByRole("button", { name: "Deleting source…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Back to library/ })).toBeDisabled();
  await act(async () => {
    resolveDelete(response(undefined, 204));
  });

  expect(await screen.findByText("Deleted “Confirmed paper” and its saved local data.")).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.getByRole("heading", { level: 2, name: "Sources" })).toHaveFocus();
  expect(screen.queryByRole("button", { name: /Confirmed paper/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Retained paper/ })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8765/api/sources/7",
    { method: "DELETE" },
  );
});

test("keeps a source open when guarded deletion fails", async () => {
  await renderLibrary([paperSource]);
  fetchMock.mockResolvedValueOnce(response({ ...paperSource, attachments: [] }));
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByRole("button", { name: "Delete source" });
  expect(
    screen.getByRole("heading", { level: 3, name: "Delete source" }).closest("section"),
  ).toHaveTextContent("It has no saved documents.");

  confirmMock.mockReturnValueOnce(true);
  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "source_removal_failed",
          message: "The source could not be removed; its saved files were restored.",
        },
      },
      500,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete source" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The source could not be removed; its saved files were restored.",
  );
  expect(screen.getByRole("heading", { level: 2, name: "Confirmed paper" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete source" })).toBeEnabled();
});

test("removes a source from the UI when only temporary cleanup remains", async () => {
  await renderLibrary([paperSource]);
  fetchMock.mockResolvedValueOnce(response({ ...paperSource, attachments: [convertedAttachment] }));
  fireEvent.click(screen.getByRole("button", { name: /Confirmed paper/ }));
  await screen.findByRole("button", { name: "Delete source" });

  confirmMock.mockReturnValueOnce(true);
  fetchMock.mockResolvedValueOnce(
    response(
      {
        detail: {
          code: "source_cleanup_incomplete",
          message: "The source was removed, but temporary file cleanup did not finish.",
        },
      },
      500,
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Delete source" }));

  expect(
    await screen.findByText("The source was removed, but temporary file cleanup did not finish."),
  ).toHaveAttribute("role", "status");
  expect(screen.getByRole("heading", { level: 2, name: "Sources" })).toHaveFocus();
  expect(screen.getByRole("heading", { name: "Start with one useful source" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Delete source" })).not.toBeInTheDocument();
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
  expect(screen.getByRole("button", { name: "Add source" })).toBeDisabled();

  const file = new File(["paper,year"], "paper.csv", { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  expect(screen.getByRole("button", { name: "Save and extract" })).toBeDisabled();

  const bibliography = new File(["@article{example}"], "sources.bib", {
    type: "application/x-bibtex",
  });
  openLibraryData();
  fireEvent.change(screen.getByLabelText("Choose a bibliography"), {
    target: { files: [bibliography] },
  });
  const bibliographyButton = screen.getByRole("button", { name: "Import bibliography" });
  expect(bibliographyButton).toBeEnabled();
  fireEvent.click(bibliographyButton);
  expect(
    screen.getByText(
      "The local service is unavailable. Use “Retry local service” above, then import again.",
    ),
  ).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);

  fireEvent.click(screen.getByRole("button", { name: "Retry local service" }));

  expect(screen.getByText("Connecting locally")).toBeInTheDocument();
  await screen.findByText("Local service ready");
  expect(screen.getByRole("button", { name: "Add source" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Save and extract" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Import bibliography" })).toBeEnabled();
});

test("leaves the connecting state when startup requests hang", () => {
  vi.useFakeTimers();
  fetchMock.mockReset();
  fetchMock.mockImplementation(() => new Promise(() => undefined));

  render(<App />);
  expect(screen.getByText("Connecting locally")).toBeInTheDocument();

  act(() => vi.advanceTimersByTime(5_000));

  expect(screen.getByText("Local service unavailable")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry local service" })).toBeEnabled();
  vi.useRealTimers();
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
