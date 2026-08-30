import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

beforeEach(() => {
  window.localStorage.clear();
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "ok", technology: {} }) })
    .mockResolvedValueOnce({ ok: true, json: async () => [] });
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

test("creates a source through the local API", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      id: 1,
      title: "A useful paper",
      doi: null,
      created_at: "2026-08-30T00:00:00Z",
    }),
  });

  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "A useful paper" } });
  fireEvent.click(screen.getByRole("button", { name: "Add to library" }));

  await waitFor(() => expect(screen.getByText("A useful paper")).toBeInTheDocument());
});

test("converts a local document with Anydoc", async () => {
  render(<App />);
  await screen.findByText("Local service ready");

  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      filename: "paper.pdf",
      format: "pdf",
      markdown: "# Converted paper\n\nA useful finding.",
    }),
  });

  const file = new File(["%PDF example"], "paper.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Choose a document"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Read document" }));

  await waitFor(() => expect(screen.getByText("Converted paper")).toBeInTheDocument());
  expect(screen.getByText("A useful finding.")).toBeInTheDocument();
});
