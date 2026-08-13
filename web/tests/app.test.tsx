import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/app";

function renderRoute(path: string, providerReady = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        api: { ready: true },
        roots: { ready: true },
        translation_provider: providerReady
          ? { ready: true }
          : {
              ready: false,
              message:
                "Configure a provider in PySubtrans service settings, then restart CueWeaver.",
            },
        worker: { ready: true, mode: "single" },
      }),
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderTermMaps() {
  const summary = {
    id: "map-1",
    name: "Characters",
    entry_count: 2,
    updated_at: "2026-08-13T12:00:00Z",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") {
        return {
          ok: true,
          json: async () => ({
            api: { ready: true },
            roots: { ready: true },
            translation_provider: { ready: true },
            worker: { ready: true, mode: "single" },
          }),
        };
      }
      if (input === "/api/term-maps") {
        return { ok: true, json: async () => ({ term_maps: [summary] }) };
      }
      return {
        ok: true,
        json: async () => ({
          ...summary,
          content: { Captain: "队长", Ship: "舰船" },
        }),
      };
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/term-maps"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderTermMapsWithFetch(fetchImplementation: typeof fetch) {
  vi.stubGlobal("fetch", fetchImplementation);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/term-maps"]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("product shell", () => {
  it.each([
    ["/translate", "Translate"],
    ["/jobs", "Jobs"],
    ["/term-maps", "Term maps"],
  ])("renders %s through shared navigation", (path, heading) => {
    renderRoute(path);

    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.getAllByRole("navigation")).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: heading })[0]).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("presents an actionable unavailable translation state", async () => {
    renderRoute("/translate", false);

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Configure a provider in PySubtrans service settings",
      ),
    );
    expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
  });

  it("does not offer submission before the workflow exists", async () => {
    renderRoute("/translate", true);

    await waitFor(() =>
      expect(screen.getByText("Translation provider ready")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
  });

  it("lists a Term map and supports keyboard inspection and search", async () => {
    renderTermMaps();

    const map = await screen.findByRole("button", { name: /Characters/ });
    expect(map).toHaveTextContent("2 entries");
    fireEvent.click(map);

    expect(await screen.findByRole("heading", { name: "Characters" })).toBeInTheDocument();
    expect(screen.getByText("Captain")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search Source or Target"), {
      target: { value: "ship" },
    });
    expect(screen.getByText("Ship")).toBeInTheDocument();
    expect(screen.queryByText("Captain")).not.toBeInTheDocument();
  });

  it("shows the empty state and uploads valid JSON", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string, init?: RequestInit) => {
      if (input === "/api/status") {
        return { ok: true, json: async () => ({ api: { ready: true }, roots: { ready: true }, translation_provider: { ready: true }, worker: { ready: true, mode: "single" } }) };
      }
      if (input === "/api/term-maps" && init?.method === "POST") {
        return { ok: true, json: async () => ({ id: "new", name: "New terms", entry_count: 1, updated_at: "2026-08-13T12:00:00Z" }) };
      }
      return { ok: true, json: async () => ({ term_maps: [] }) };
    });
    renderTermMapsWithFetch(fetchMock);

    expect(await screen.findByRole("heading", { name: "No Term maps yet" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New terms" } });
    fireEvent.change(screen.getByLabelText("JSON content"), { target: { value: '{"Captain":"队长"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/term-maps",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "New terms", content: { Captain: "队长" } }),
      }),
    ));
    expect(screen.getByRole("heading", { name: "No Term maps yet" })).toBeInTheDocument();
  });

  it("reports client-side JSON validation without making an upload request", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") {
        return { ok: true, json: async () => ({ api: { ready: true }, roots: { ready: true }, translation_provider: { ready: true }, worker: { ready: true, mode: "single" } }) };
      }
      return { ok: true, json: async () => ({ term_maps: [] }) };
    });
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Broken" } });
    fireEvent.change(screen.getByLabelText("JSON content"), { target: { value: "{" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("valid JSON");
    expect(fetchMock).not.toHaveBeenCalledWith("/api/term-maps", expect.objectContaining({ method: "POST" }));
  });

  it("exposes a duplicate-name API error", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string, init?: RequestInit) => {
      if (input === "/api/status") {
        return { ok: true, json: async () => ({ api: { ready: true }, roots: { ready: true }, translation_provider: { ready: true }, worker: { ready: true, mode: "single" } }) };
      }
      if (init?.method === "POST") {
        return { ok: false, json: async () => ({ message: "A Term map with this name already exists" }) };
      }
      return { ok: true, json: async () => ({ term_maps: [] }) };
    });
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Names" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });
});
