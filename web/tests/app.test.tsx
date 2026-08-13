import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/app";
import type { MediaDirectory, MediaDiscovery } from "../src/browse";

function renderRoute(
  path: string,
  providerReady = true,
  browseResponse: MediaDirectory = {
    path: "",
    entries: [
      { kind: "directory", name: "Series", path: "Series" },
      { kind: "media", name: "Movie.mkv", path: "Movie.mkv" },
    ],
  },
  discoveryResponse: MediaDiscovery = {
    path: "Movie.mkv",
    candidates: [
      {
        kind: "external",
        path: "Movie.en.srt",
        format: "srt",
        tags: { language: "en", title: "" },
      },
      {
        kind: "embedded",
        stream_index: 3,
        format: "ass",
        tags: { language: "zhs", title: "Chinese" },
      },
    ],
    unsupported_candidates: [
      { kind: "embedded", stream_index: 4, reason: "bitmap subtitle" },
    ],
  },
  discoveryFailure = false,
  discoveryResponses: Array<MediaDiscovery | Error | Promise<MediaDiscovery | Error>> = [],
) {
  let discoveryCall = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/api/media/browse")) {
        const path = init?.body ? JSON.parse(String(init.body)).path : "";
        return Promise.resolve({
          ok: true,
          json: async () =>
            path === "Series"
              ? { path, entries: [{ kind: "media", name: "Episode.mkv", path: "Series/Episode.mkv" }] }
              : browseResponse,
        });
      }
      if (String(input).includes("/api/media/discover")) {
        const response = discoveryResponses[discoveryCall++] ??
          (discoveryFailure ? new Error("ffprobe failed") : discoveryResponse);
        return Promise.resolve(response).then((value) => ({
          ok: !(value instanceof Error),
          json: async () => (value instanceof Error ? { message: value.message } : value),
        }));
      }
      return Promise.resolve({
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
      });
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

  it("browses one directory at a time and filters its entries", async () => {
    renderRoute("/translate");

    expect(await screen.findByRole("button", { name: "Open Series" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select Movie.mkv" })).toBeInTheDocument();

    const filter = screen.getByRole("searchbox", { name: "Filter this directory" });
    fireEvent.change(filter, { target: { value: "movie" } });

    expect(screen.getByRole("button", { name: "Select Movie.mkv" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open Series" })).not.toBeInTheDocument();
  });

  it("navigates with breadcrumbs and preserves Media selection", async () => {
    renderRoute("/translate");
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;

    await screen.findByRole("button", { name: "Open Series" });
    fireEvent.click(screen.getByRole("button", { name: "Open Series" }));
    expect(await screen.findByRole("button", { name: "Media" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/media/browse",
      expect.objectContaining({ body: JSON.stringify({ path: "Series" }) }),
    );
  });

  it("selects a Media with keyboard- and touch-sized controls", async () => {
    renderRoute("/translate");

    const media = await screen.findByRole("button", { name: "Select Movie.mkv" });
    fireEvent.click(media);

    expect(media).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Selected")).toBeInTheDocument();
  });

  it("keeps the real filename in the accessible Media name", async () => {
    renderRoute("/translate", true, {
      path: "",
      entries: [
        {
          kind: "media",
          name: "Actual filename.mkv",
          path: "Actual filename.mkv",
          title: "Displayed title",
          year: 2024,
        },
      ],
    });

    expect(
      await screen.findByRole("button", {
        name: "Select Displayed title (2024) (Actual filename.mkv)",
      }),
    ).toBeInTheDocument();
  });

  it("automatically discovers all subtitle candidates without selecting one", async () => {
    renderRoute("/translate");

    await screen.findByRole("button", { name: "Select Movie.mkv" });
    fireEvent.click(screen.getByRole("button", { name: "Select Movie.mkv" }));

    expect(
      await screen.findByRole("button", {
        name: "Select external subtitle en (Movie.en.srt)",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Select embedded subtitle zhs / Chinese" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("bitmap subtitle")).toBeInTheDocument();
    expect(screen.getByText("Not selectable")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Select external subtitle en (Movie.en.srt)" }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("group", { name: "Unsupported embedded subtitle" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("renders a retryable Discovery error", async () => {
    renderRoute("/translate", true, undefined, undefined, true);

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("ffprobe failed");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    const discoverCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([input]) => String(input).includes("/api/media/discover"),
    ).length;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(
        (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([input]) =>
          String(input).includes("/api/media/discover"),
        ).length,
      ).toBe(discoverCalls + 1),
    );
  });

  it("clears Discovery and subtitle selection when changing directory", async () => {
    renderRoute("/translate");

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));
    const subtitle = await screen.findByRole("button", {
      name: "Select external subtitle en (Movie.en.srt)",
    });
    fireEvent.click(subtitle);
    expect(subtitle).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Open Series" }));

    await screen.findByRole("button", { name: "Select Episode.mkv" });
    expect(screen.queryByRole("button", { name: "Select external subtitle en (Movie.en.srt)" })).not.toBeInTheDocument();
  });

  it("clears the selected Media explicitly", async () => {
    renderRoute("/translate");

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));
    fireEvent.click(await screen.findByRole("button", { name: "Choose another Media" }));

    expect(screen.queryByRole("button", { name: "Choose another Media" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select Movie.mkv" })).toBeInTheDocument();
  });

  it("shows an empty Discovery result", async () => {
    renderRoute("/translate", true, undefined, {
      path: "Movie.mkv",
      candidates: [],
      unsupported_candidates: [],
    });

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));

    expect(
      await screen.findByText("No subtitles were found for this Media."),
    ).toBeInTheDocument();
  });

  it("does not show cached candidates after clearing and selecting Media again", async () => {
    let resolveSecond!: (value: MediaDiscovery | Error) => void;
    const secondResponse = new Promise<MediaDiscovery | Error>((resolve) => {
      resolveSecond = resolve;
    });
    renderRoute("/translate", true, undefined, undefined, false, [
      {
        path: "Movie.mkv",
        candidates: [
          {
            kind: "external",
            path: "Movie.en.srt",
            format: "srt",
            tags: { language: "en", title: "" },
          },
        ],
        unsupported_candidates: [],
      },
      secondResponse,
    ]);

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));
    await screen.findByRole("button", {
      name: "Select external subtitle en (Movie.en.srt)",
    });
    fireEvent.click(screen.getByRole("button", { name: "Choose another Media" }));
    fireEvent.click(screen.getByRole("button", { name: "Select Movie.mkv" }));

    expect(
      await screen.findByRole("status", { name: "Loading subtitles" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Select external subtitle en (Movie.en.srt)",
      }),
    ).not.toBeInTheDocument();

    resolveSecond(new Error("ffprobe failed"));
    expect(await screen.findByRole("alert")).toHaveTextContent("ffprobe failed");
    expect(
      screen.queryByRole("button", {
        name: "Select external subtitle en (Movie.en.srt)",
      }),
    ).not.toBeInTheDocument();
  });

  it("distinguishes External subtitles with the same language", async () => {
    renderRoute("/translate", true, undefined, {
      path: "Movie.mkv",
      candidates: [
        {
          kind: "external",
          path: "Movie.en.forced.srt",
          format: "srt",
          tags: { language: "en", title: "" },
        },
        {
          kind: "external",
          path: "Movie.en.sdh.srt",
          format: "srt",
          tags: { language: "en", title: "" },
        },
      ],
      unsupported_candidates: [],
    });

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));

    expect(
      await screen.findByRole("button", {
        name: "Select external subtitle en (Movie.en.forced.srt)",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Select external subtitle en (Movie.en.sdh.srt)",
      }),
    ).toBeInTheDocument();
  });
});
