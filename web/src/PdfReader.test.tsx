import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const pdfMocks = vi.hoisted(() => ({
  destroy: vi.fn(),
  getDocument: vi.fn(),
  getPage: vi.fn(),
  getViewport: vi.fn(),
  render: vi.fn(),
  renderCancel: vi.fn(),
}));

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: pdfMocks.getDocument,
}));

vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "pdf.worker.mjs",
}));

import { PdfReader } from "./PdfReader";

function resolvedLoadingTask() {
  const renderTask = {
    cancel: pdfMocks.renderCancel,
    promise: Promise.resolve(),
  };
  const page = {
    getViewport: pdfMocks.getViewport.mockImplementation(({ scale }: { scale: number }) => ({
      height: 800 * scale,
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
  for (const mock of Object.values(pdfMocks)) mock.mockReset();
  pdfMocks.getDocument.mockReturnValue(resolvedLoadingTask());
});

afterEach(cleanup);

test("loads a PDF, renders one page at a time, and changes zoom", async () => {
  render(<PdfReader title="Test paper" url="http://local.test/paper.pdf" />);

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
});

test("shows a PDF loading failure and retries explicitly", async () => {
  pdfMocks.getDocument.mockReturnValueOnce({
    destroy: pdfMocks.destroy,
    promise: Promise.reject(new Error("malformed PDF")),
  });
  render(<PdfReader title="Broken paper" url="http://local.test/broken.pdf" />);

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
