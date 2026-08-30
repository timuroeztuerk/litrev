import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import App from "./App";

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock
    .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "ok", technology: {} }) })
    .mockResolvedValueOnce({ ok: true, json: async () => [] });
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
