import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/app";
import type { MediaDirectory, MediaDiscovery } from "../src/browse";
import { validateTermMapContent, type TermMapSummary } from "../src/term-maps";

const CHARACTERS_TERM_MAP: TermMapSummary = {
  id: "map-1",
  name: "Characters",
  entry_count: 1,
  updated_at: "2026-08-13T12:00:00Z",
};

function jsonResponse(body: unknown, ok = true) {
  return { ok, json: async () => body };
}

type JobFixture = { status: string; [key: string]: unknown };

function jobListResponse(jobs: JobFixture[], next_cursor: string | null = null) {
  const activeStatuses = ["Queued", "Extracting", "Translating"];
  return jsonResponse({
    active_jobs: jobs.filter((job) => activeStatuses.includes(job.status)),
    history_jobs: jobs.filter((job) => !activeStatuses.includes(job.status)),
    next_cursor,
  });
}

function emptyMediaResponse() {
  return jsonResponse({ path: "", entries: [] });
}

function statusResponse(providerReady = true) {
  return jsonResponse({
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
  });
}

function renderWithFetch(path: string, fetchImplementation: typeof fetch) {
  vi.stubGlobal("fetch", fetchImplementation);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

function jobsFetch(job: JobFixture) {
  return vi.fn().mockImplementation(async (input: string) => {
    if (input === "/api/status") return statusResponse();
    if (input.startsWith("/api/jobs")) return jobListResponse([job]);
    return jsonResponse({ term_maps: [] });
  });
}

function jobListFetch(getJobs: () => JobFixture[]) {
  return vi.fn().mockImplementation(async (input: string) => {
    if (input === "/api/status") return statusResponse();
    if (input.startsWith("/api/jobs")) return jobListResponse(getJobs());
    return jsonResponse({ term_maps: [] });
  });
}

function cancelJobFetch(jobId: string, cancelError?: string) {
  const queuedJob = {
    ...embeddedJob(jobId, "Failed"),
    status: "Queued" as const,
    started_at: null,
    finished_at: null,
    queue_position: 1,
    error: null,
  };
  let currentJob: JobFixture = queuedJob;
  return vi.fn().mockImplementation(async (input: string, init?: RequestInit) => {
    if (input === "/api/status") return statusResponse();
    if (input.endsWith("/cancel") && init?.method === "POST") {
      if (cancelError !== undefined) {
        return jsonResponse({ message: cancelError }, false);
      }
      currentJob = {
        ...currentJob,
        status: "Cancelled",
        finished_at: "2026-08-13T12:00:03Z",
        queue_position: null,
      };
      return jsonResponse(currentJob);
    }
    if (input.startsWith("/api/jobs")) return jobListResponse([currentJob]);
    return jsonResponse({ term_maps: [] });
  });
}

function jobsPageFetch(
  getJobs: () => JobFixture,
  details: Record<string, unknown> = {},
) {
  return vi.fn().mockImplementation(async (input: string) => {
    if (input === "/api/status") return statusResponse();
    if (input in details) return jsonResponse(details[input]);
    if (input.startsWith("/api/jobs")) return jobListResponse([getJobs()]);
    return jsonResponse({ term_maps: [] });
  });
}

function embeddedJob(id: string, status: "Failed" | "Interrupted", target = "zh-Hans") {
  return {
    id,
    attempt: 1,
    status,
    created_at: "2026-08-13T12:00:00Z",
    started_at: "2026-08-13T12:00:01Z",
    finished_at: "2026-08-13T12:00:02Z",
    request: {
      media_path: "Movie.mkv",
      stream_index: 3,
      target_language_code: target,
      term_map: null,
      dynamic_terminology_enabled: true,
      subtitle_terminology_filter_enabled: true,
      output_suffix: target,
      output_conflict_policy: "append-number",
      output_path: `Movie.${target}.srt`,
      source_format: "srt",
    },
    error: {
      code: status === "Failed" ? "translation_failed" : "job_interrupted",
      message: status === "Failed" ? "Translation failed" : "Job was interrupted",
    },
  };
}

function translatingEmbeddedJob(id: string) {
  return {
    ...embeddedJob(id, "Interrupted"),
    status: "Translating" as const,
    error: null,
  };
}

function completedJob(job: JobFixture) {
  return {
    ...job,
    status: "Completed" as const,
    finished_at: "2026-08-13T12:01:00Z",
  };
}

function retryFetch(job: JobFixture, firstFailure?: string) {
  let attempts = 0;
  let response: { status: string; attempt: number } | null = null;
  const fetchMock = vi
    .fn()
    .mockImplementation(async (input: string, init?: RequestInit) => {
      if (input === "/api/status") return statusResponse();
      if (input.endsWith("/retry") && init?.method === "POST") {
        attempts += 1;
        if (firstFailure && attempts === 1) {
          return jsonResponse({ message: firstFailure }, false);
        }
        response = { status: "Queued", attempt: 2 };
        return jsonResponse({ ...job, ...response, error: null });
      }
      if (input.startsWith("/api/jobs")) return jobListResponse([job]);
      return jsonResponse({ term_maps: [] });
    });
  return {
    fetchMock,
    attempts: () => attempts,
    response: () => response,
  };
}

async function selectExternalSubtitle() {
  fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));
  const subtitle = await screen.findByRole("button", {
    name: "Select external subtitle en (Movie.en.srt)",
  });
  fireEvent.click(subtitle);
  return subtitle;
}

function expectJobSubmissionBlocked() {
  expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
  expect(globalThis.fetch).not.toHaveBeenCalledWith(
    "/api/jobs",
    expect.objectContaining({ method: "POST" }),
  );
}

async function selectEmbeddedSubtitle() {
  fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));
  const subtitle = await screen.findByRole("button", {
    name: "Select embedded subtitle stream 3 zhs / Chinese",
  });
  fireEvent.click(subtitle);
  return subtitle;
}

async function expectQueuedJob(source: Record<string, unknown>) {
  await waitFor(() =>
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/jobs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          ...source,
          target_language_code: "zh-Hans",
          output_suffix: "zh-Hans",
          output_conflict_policy: "append-number",
          term_map_id: null,
          dynamic_terminology_enabled: true,
          subtitle_terminology_filter_enabled: true,
        }),
      }),
    ),
  );
}

async function expectQueuedJobRequest(
  targetLanguage: string,
  termMapId: string | null,
  dynamicTerminologyEnabled: boolean,
  subtitleTerminologyFilterEnabled: boolean,
) {
  await waitFor(() =>
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/jobs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          media_path: "Movie.mkv",
          subtitle_path: "Movie.en.srt",
          target_language_code: targetLanguage,
          output_suffix: targetLanguage,
          output_conflict_policy: "append-number",
          term_map_id: termMapId,
          dynamic_terminology_enabled: dynamicTerminologyEnabled,
          subtitle_terminology_filter_enabled: subtitleTerminologyFilterEnabled,
        }),
      }),
    ),
  );
}

function termMapFetch(postResponse: unknown = {}, postOk = true) {
  return vi.fn().mockImplementation(async (input: string, init?: RequestInit) => {
    if (input === "/api/status") return statusResponse();
    if (init?.method === "POST") {
      return jsonResponse(postResponse, postOk);
    }
    return jsonResponse({ term_maps: [] });
  });
}

async function expectTermMapPost(fetchMock: ReturnType<typeof vi.fn>, body: string) {
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/term-maps",
      expect.objectContaining({ method: "POST", body }),
    ),
  );
}

async function expectDuplicateContentRejected(fetchMock: ReturnType<typeof vi.fn>) {
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "unique regardless of case",
  );
  expect(screen.getByRole("button", { name: "Upload Term map" })).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalledWith(
    "/api/term-maps",
    expect.objectContaining({ method: "POST" }),
  );
}

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
  discoveryResponses: Array<
    MediaDiscovery | Error | Promise<MediaDiscovery | Error>
  > = [],
  termMaps: TermMapSummary[] = [],
) {
  let discoveryCall = 0;
  const fetchMock = vi
    .fn()
    .mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/api/media/browse")) {
        const path = init?.body ? JSON.parse(String(init.body)).path : "";
        return Promise.resolve({
          ok: true,
          json: async () =>
            path === "Series"
              ? {
                  path,
                  entries: [
                    { kind: "media", name: "Episode.mkv", path: "Series/Episode.mkv" },
                  ],
                }
              : browseResponse,
        });
      }
      if (String(input).includes("/api/media/discover")) {
        const response =
          discoveryResponses[discoveryCall++] ??
          (discoveryFailure ? new Error("ffprobe failed") : discoveryResponse);
        return Promise.resolve(response).then((value) => ({
          ok: !(value instanceof Error),
          json: async () =>
            value instanceof Error ? { message: value.message } : value,
        }));
      }
      if (String(input) === "/api/term-maps") {
        return Promise.resolve(jsonResponse({ term_maps: [...termMaps] }));
      }
      return Promise.resolve(statusResponse(providerReady));
    });
  return renderWithFetch(path, fetchMock);
}

function renderTermMaps() {
  const summary = {
    id: "map-1",
    name: "Characters",
    entry_count: 2,
    updated_at: "2026-08-13T12:00:00Z",
  };
  return renderWithFetch(
    "/term-maps",
    vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") {
        return statusResponse();
      }
      if (input === "/api/term-maps") {
        return jsonResponse({ term_maps: [summary] });
      }
      return jsonResponse({
        ...summary,
        content: { Captain: "队长", Ship: "舰船" },
      });
    }),
  );
}

function renderTermMapsWithFetch(fetchImplementation: typeof fetch) {
  return renderWithFetch("/term-maps", fetchImplementation);
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
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

  it("shows runtime and provider failures without enabling submission", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status")
        return jsonResponse({ message: "Runtime unavailable" }, false);
      if (input === "/api/media/browse") {
        return emptyMediaResponse();
      }
      return jsonResponse({ term_maps: [] });
    });
    renderWithFetch("/translate", fetchMock);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "CueWeaver status is unavailable.",
    );
    expect(screen.getByText("Runtime unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
  });

  it("shows Media loading, empty, and retryable error states", async () => {
    let browseCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/media/browse") {
        browseCalls += 1;
        if (browseCalls === 1)
          return jsonResponse({ message: "Media is unavailable" }, false);
        return emptyMediaResponse();
      }
      return jsonResponse({ term_maps: [] });
    });
    renderWithFetch("/translate", fetchMock);

    expect(await screen.findByRole("alert")).toHaveTextContent("Media is unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("This directory is empty.")).toBeInTheDocument();
    expect(browseCalls).toBe(2);
  });

  it("shows the Media loading state while browsing is pending", () => {
    const pending = new Promise<never>(() => undefined);
    const fetchMock = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/media/browse") return pending;
      if (input === "/api/status") return Promise.resolve(statusResponse());
      return Promise.resolve(jsonResponse({ term_maps: [] }));
    });
    renderWithFetch("/translate", fetchMock);

    expect(screen.getByText("Loading Media...")).toBeInTheDocument();
  });

  it("renders a persisted Job with its status and failure context", async () => {
    const fetchMock = jobsFetch({
      id: "job-123456789",
      status: "Failed",
      created_at: "2026-08-13T12:00:00Z",
      started_at: "2026-08-13T12:00:01Z",
      finished_at: "2026-08-13T12:00:02Z",
      request: {
        media_path: "Movie.mkv",
        subtitle_path: "Movie.en.srt",
        target_language_code: "zh-Hans",
        term_map: {
          id: "map-1",
          name: "Characters",
          content: { Captain: "队长" },
        },
        output_path: "Movie.zh-Hans.srt",
        source_format: "srt",
      },
      error: {
        code: "translation_failed",
        message: "Translation failed",
        field: "subtitle",
      },
    });
    renderWithFetch("/jobs", fetchMock);

    expect(await screen.findByText("Movie.mkv")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Movie.en.srt to zh-Hans")).toBeInTheDocument();
    expect(screen.getByText("Term map: Characters")).toBeInTheDocument();
    expect(screen.getByText("Job job-1234")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Movie\.mkv/ }));
    expect(
      await screen.findByRole("heading", { name: "Action needed" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Translation failed")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Show approved diagnostic context"));
    expect(screen.getByText("translation_failed")).toBeInTheDocument();
    expect(screen.getByText("subtitle")).toBeInTheDocument();
  });

  it("opens a durable Job detail with local list time and UTC diagnostics", async () => {
    const job = {
      id: "job-detail-1",
      attempt: 2,
      status: "Completed" as const,
      created_at: "2026-08-13T12:00:00Z",
      started_at: "2026-08-13T12:00:01Z",
      finished_at: "2026-08-13T12:00:02Z",
      queue_position: null,
      request: {
        media_path: "Shows/Movie.mkv",
        subtitle_path: "Shows/Movie.en.srt",
        target_language_code: "zh-Hans",
        term_map: {
          id: "map-1",
          name: "Characters",
          content: { Captain: "队长" },
        },
        dynamic_terminology_enabled: true,
        subtitle_terminology_filter_enabled: true,
        output_suffix: "zh-Hans",
        output_conflict_policy: "append-number" as const,
        output_path: "Shows/Movie.zh-Hans.2.srt",
        source_format: "srt",
      },
      error: null,
    };
    const fetchMock = jobsPageFetch(() => job, {
      "/api/jobs/job-detail-1": job,
    });

    renderWithFetch("/jobs", fetchMock);
    fireEvent.click(await screen.findByRole("button", { name: /Shows\/Movie\.mkv/ }));

    expect(
      await screen.findByRole("heading", { name: "Request summary" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Shows/Movie.mkv" })).toHaveFocus();
    expect(screen.getByText("Characters")).toBeInTheDocument();
    expect(screen.getByText("Shows/Movie.zh-Hans.2.srt")).toBeInTheDocument();
    expect(screen.getAllByText(/13 Aug 2026.*UTC/).length).toBe(3);
    expect(screen.queryByText(/work\/jobs/)).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/jobs/job-detail-1");

    fireEvent.click(screen.getByRole("button", { name: "Back to Jobs" }));
    expect(
      await screen.findByRole("heading", { name: "Select a Job" }),
    ).toBeInTheDocument();
  });

  it("announces a newly observed completion without browser notification permission", async () => {
    let currentJob = {
      ...embeddedJob("job-notice-1", "Interrupted"),
      status: "Translating",
      error: null,
    };
    const fetchMock = jobsPageFetch(() => currentJob);
    const { queryClient } = renderWithFetch("/jobs", fetchMock);

    await screen.findByText("Embedded stream 3 to zh-Hans");
    currentJob = {
      ...currentJob,
      status: "Completed",
      finished_at: "2026-08-13T12:01:00Z",
    };
    await queryClient.invalidateQueries({ queryKey: ["jobs"] });

    expect(
      await screen.findByText("Movie.mkv translation completed."),
    ).toBeInTheDocument();
    expect(globalThis.Notification).toBeUndefined();
  });

  it("announces and dismisses a newly observed failed Job", async () => {
    let currentJob = {
      ...embeddedJob("job-notice-failed", "Interrupted"),
      status: "Translating",
      error: null as { code: string; message: string } | null,
    };
    const fetchMock = jobsPageFetch(() => currentJob);
    const { queryClient } = renderWithFetch("/jobs", fetchMock);

    await screen.findByText("Embedded stream 3 to zh-Hans");
    currentJob = {
      ...currentJob,
      status: "Failed",
      error: { code: "translation_failed", message: "Translation failed" },
    };
    await queryClient.invalidateQueries({ queryKey: ["jobs"] });

    const notification = await screen.findByRole("alert");
    expect(notification).toHaveTextContent("Movie.mkv translation failed");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("moves a completed active Job into history during polling", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const active = translatingEmbeddedJob("polling-job-1");
    const completed = completedJob(active);
    let currentJobs: JobFixture[] = [active];
    const fetchMock = jobListFetch(() => currentJobs);
    try {
      renderWithFetch("/jobs", fetchMock);
      expect(await screen.findByText("Translating")).toBeInTheDocument();
      currentJobs = [completed];

      await vi.advanceTimersByTimeAsync(2000);

      expect(await screen.findByText("Completed")).toBeInTheDocument();
      expect(
        await screen.findByText("Movie.mkv translation completed."),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not announce terminal Jobs when loading another history page", async () => {
    const firstHistory = {
      ...embeddedJob("history-notice-page-one", "Failed"),
      status: "Completed",
      error: null,
    };
    const secondCompleted = {
      ...embeddedJob("history-notice-page-two", "Failed"),
      status: "Completed",
      error: null,
    };
    const secondFailed = embeddedJob("history-notice-page-three", "Failed");
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/jobs?limit=1") {
        return jsonResponse({ active_jobs: [], history_jobs: [], next_cursor: null });
      }
      if (input === "/api/jobs") {
        return jsonResponse({
          active_jobs: [],
          history_jobs: [firstHistory],
          next_cursor: "cursor-notice-1",
        });
      }
      if (input === "/api/jobs?limit=50&cursor=cursor-notice-1") {
        return jsonResponse({
          active_jobs: [],
          history_jobs: [secondCompleted, secondFailed],
          next_cursor: null,
        });
      }
      return jsonResponse({ term_maps: [] });
    });
    renderWithFetch("/jobs", fetchMock);

    expect(await screen.findByText("Load more history")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more history" }));
    await waitFor(() => expect(screen.getAllByText("Movie.mkv")).toHaveLength(3));
    expect(
      screen.queryByText("Movie.mkv translation completed."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Movie.mkv translation failed.")).not.toBeInTheDocument();
  });

  it("announces a terminal Job after it temporarily leaves the result", async () => {
    const active = translatingEmbeddedJob("temporary-notice-job");
    const completed = completedJob(active);
    let currentJobs: JobFixture[] = [active];
    const fetchMock = jobListFetch(() => currentJobs);
    const { queryClient } = renderWithFetch("/jobs", fetchMock);

    expect(await screen.findByText("Translating")).toBeInTheDocument();
    currentJobs = [];
    await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    expect(
      screen.queryByText("Movie.mkv translation completed."),
    ).not.toBeInTheDocument();

    currentJobs = [completed];
    await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    expect(
      await screen.findByText("Movie.mkv translation completed."),
    ).toBeInTheDocument();
  });

  it("pauses Job polling while hidden and refreshes when visible again", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input.startsWith("/api/jobs")) return jobListResponse([]);
      return jsonResponse({ term_maps: [] });
    });
    try {
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: "visible",
      });
      renderWithFetch("/jobs", fetchMock);
      await screen.findByRole("heading", { name: "No Jobs yet" });

      const jobCalls = () =>
        fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/jobs"))
          .length;
      const initialCalls = jobCalls();
      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: "hidden",
      });
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(5000);
      expect(jobCalls()).toBe(initialCalls);

      Object.defineProperty(document, "visibilityState", {
        configurable: true,
        value: "visible",
      });
      document.dispatchEvent(new Event("visibilitychange"));
      await waitFor(() => expect(jobCalls()).toBe(initialCalls + 1));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a stale-selection state when a requested Job is gone", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/jobs/missing") {
        return jsonResponse({ message: "Job does not exist" }, false);
      }
      if (input.startsWith("/api/jobs")) return jobListResponse([]);
      return jsonResponse({ term_maps: [] });
    });
    renderWithFetch("/jobs/missing", fetchMock);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This Job is no longer available.",
    );
    expect(screen.getByRole("button", { name: "Back to Jobs" })).toBeInTheDocument();
  });

  it("shows Job list loading and retryable error states", async () => {
    let listCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input.startsWith("/api/jobs")) {
        listCalls += 1;
        if (listCalls === 1)
          return jsonResponse({ message: "History is unavailable" }, false);
        return jobListResponse([]);
      }
      return jsonResponse({ term_maps: [] });
    });
    renderWithFetch("/jobs", fetchMock);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "History is unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "No Jobs yet" }),
    ).toBeInTheDocument();
    expect(listCalls).toBe(4);
  });

  it("loads another history page from the returned cursor", async () => {
    const active = {
      ...embeddedJob("active-page-job", "Interrupted"),
      status: "Translating",
      error: null,
    };
    const firstHistory = {
      ...embeddedJob("history-page-one", "Failed"),
      status: "Completed",
      error: null,
    };
    const secondHistory = {
      ...embeddedJob("history-page-two", "Failed"),
      status: "Failed",
    };
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/jobs?limit=1") {
        return jsonResponse({
          active_jobs: [active],
          history_jobs: [],
          next_cursor: null,
        });
      }
      if (input === "/api/jobs") {
        return jsonResponse({
          active_jobs: [],
          history_jobs: [firstHistory],
          next_cursor: "cursor-1",
        });
      }
      if (input === "/api/jobs?limit=50&cursor=cursor-1") {
        return jsonResponse({
          active_jobs: [],
          history_jobs: [secondHistory],
          next_cursor: null,
        });
      }
      return jsonResponse({ term_maps: [] });
    });
    renderWithFetch("/jobs", fetchMock);

    expect(await screen.findByText("Translating")).toBeInTheDocument();
    expect(screen.getByText("Load more history")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Load more history" }));
    await waitFor(() => expect(screen.getAllByText("Movie.mkv")).toHaveLength(3));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs?limit=50&cursor=cursor-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("renders queued and running Job states with queue context", async () => {
    const statuses = ["Queued", "Extracting", "Translating"] as const;
    for (const status of statuses) {
      const job = {
        ...embeddedJob(`state-${status}`, "Failed"),
        status,
        queue_position: status === "Queued" ? 2 : null,
        error: null,
      };
      renderWithFetch("/jobs", jobsFetch(job));

      expect(await screen.findByText(status)).toBeInTheDocument();
      if (status === "Queued") {
        expect(screen.getByText("Queue position 2")).toBeInTheDocument();
      }
      cleanup();
    }
  });

  it("cancels a queued Job directly from the list", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = cancelJobFetch("cancel-list-job");
    renderWithFetch("/jobs", fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Cancel Job" }));

    expect(confirm).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/jobs/cancel-list-job/cancel", {
        method: "POST",
      }),
    );
    expect((await screen.findAllByText("Cancelled")).length).toBeGreaterThan(0);
  });

  it("confirms cancellation for a queued Job in its detail view", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = cancelJobFetch("cancel-job-1");
    renderWithFetch("/jobs", fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/jobs/cancel-job-1/cancel",
      expect.objectContaining({ method: "POST" }),
    );

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel Job" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/jobs/cancel-job-1/cancel", {
        method: "POST",
      }),
    );
    expect((await screen.findAllByText("Cancelled")).length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "Cancel Job" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Job" })).toBeInTheDocument();
  });

  it("shows a list cancellation error when the Job starts running first", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = cancelJobFetch(
      "cancel-list-error",
      "Only Queued Jobs can be cancelled",
    );
    renderWithFetch("/jobs", fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Cancel Job" }));

    expect(confirm).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only Queued Jobs can be cancelled",
    );
  });

  it("does not expose cancellation for a running Job", async () => {
    const job = translatingEmbeddedJob("running-no-cancel");
    renderWithFetch("/jobs", jobsFetch(job));

    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
    expect(
      screen.queryByRole("button", { name: "Cancel Job" }),
    ).not.toBeInTheDocument();
  });

  it("shows a delete error and disables retry while a mutation is pending", async () => {
    const job = embeddedJob("mutation-state-1", "Failed");
    let resolveRetry!: (value: unknown) => void;
    const retryPending = new Promise((resolve) => {
      resolveRetry = resolve;
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi
      .fn()
      .mockImplementation(async (input: string, init?: RequestInit) => {
        if (input === "/api/status") return statusResponse();
        if (input.endsWith("/retry")) return retryPending;
        if (input.endsWith("mutation-state-1") && init?.method === "DELETE") {
          return jsonResponse({ message: "Job could not be deleted." }, false);
        }
        if (input.startsWith("/api/jobs")) return jobListResponse([job]);
        return jsonResponse({ term_maps: [] });
      });
    renderWithFetch("/jobs", fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    expect(await screen.findByRole("button", { name: "Retrying..." })).toBeDisabled();
    resolveRetry(jsonResponse({ ...job, status: "Queued", error: null }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Retry Job" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete Job" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be deleted");
    expect(confirm).toHaveBeenCalled();
  });

  it("offers retry for a failed External Job without exposing its configuration", async () => {
    const job = {
      id: "job-retry-1",
      attempt: 1,
      status: "Failed",
      created_at: "2026-08-13T12:00:00Z",
      started_at: "2026-08-13T12:00:01Z",
      finished_at: "2026-08-13T12:00:02Z",
      request: {
        media_path: "Movie.mkv",
        subtitle_path: "Movie.en.srt",
        target_language_code: "zh-Hans",
        term_map: null,
        dynamic_terminology_enabled: true,
        subtitle_terminology_filter_enabled: true,
        output_suffix: "zh-Hans",
        output_conflict_policy: "append-number",
        output_path: "Movie.zh-Hans.srt",
        source_format: "srt",
      },
      error: { code: "translation_failed", message: "Translation failed" },
    };
    const retry = retryFetch(job, "External subtitle does not exist.");
    renderWithFetch("/jobs", retry.fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry Job" }));

    await waitFor(() =>
      expect(retry.fetchMock).toHaveBeenCalledWith("/api/jobs/job-retry-1/retry", {
        method: "POST",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "External subtitle does not exist.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await waitFor(() => expect(retry.attempts()).toBe(2));
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("offers retry for a failed Embedded Job", async () => {
    const job = embeddedJob("job-embedded-retry", "Failed");
    const retry = retryFetch(job, "Embedded subtitle stream disappeared.");
    renderWithFetch("/jobs", retry.fetchMock);

    expect(await screen.findByText("Embedded stream 3 to zh-Hans")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry Job" }));
    await waitFor(() => expect(retry.attempts()).toBe(1));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Embedded subtitle stream disappeared.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    await waitFor(() => expect(retry.attempts()).toBe(2));
    expect(retry.response()).toEqual(
      expect.objectContaining({ status: "Queued", attempt: 2 }),
    );
  });

  it("offers retry for an Interrupted Embedded Job", async () => {
    const job = embeddedJob("interrupted-embedded-1", "Interrupted", "zh");
    const retry = retryFetch(job);
    renderWithFetch("/jobs", retry.fetchMock);

    expect(await screen.findByText("Embedded stream 3 to zh")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry Job" }));
    await waitFor(() => expect(retry.attempts()).toBe(1));
    expect(retry.fetchMock).toHaveBeenCalledWith(
      "/api/jobs/interrupted-embedded-1/retry",
      { method: "POST" },
    );
    expect(retry.response()).toEqual(
      expect.objectContaining({ status: "Queued", attempt: 2 }),
    );
  });

  it("renders an Interrupted Job as a terminal state", async () => {
    const fetchMock = jobsFetch({
      id: "interrupted-1",
      status: "Interrupted",
      created_at: "2026-08-13T12:00:00Z",
      started_at: "2026-08-13T12:00:01Z",
      finished_at: "2026-08-13T12:00:02Z",
      request: {
        media_path: "Movie.mkv",
        subtitle_path: "Movie.en.srt",
        target_language_code: "zh",
        output_path: "Movie.zh.srt",
        source_format: "srt",
      },
      error: {
        code: "job_interrupted",
        message: "Job was interrupted when CueWeaver stopped",
      },
    });
    renderWithFetch("/jobs", fetchMock);

    expect(await screen.findByText("Interrupted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Movie\.mkv/ }));
    expect(
      await screen.findByRole("button", { name: "Retry Job" }),
    ).toBeInTheDocument();
  });

  it("requires confirmation before deleting a terminal Job and returns to the list", async () => {
    const job = embeddedJob("delete-job-1", "Failed");
    let deleted = false;
    const fetchMock = vi
      .fn()
      .mockImplementation(async (input: string, init?: RequestInit) => {
        if (input === "/api/status") return statusResponse();
        if (input === "/api/jobs/delete-job-1" && init?.method === "DELETE") {
          deleted = true;
          return jsonResponse({ id: job.id, deleted: true });
        }
        if (input.startsWith("/api/jobs")) return jobListResponse(deleted ? [] : [job]);
        return jsonResponse({ term_maps: [] });
      });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithFetch("/jobs", fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Job" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/jobs/delete-job-1",
      expect.objectContaining({ method: "DELETE" }),
    );

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Delete Job" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/jobs/delete-job-1", {
        method: "DELETE",
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "No Jobs yet" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "All Jobs" })).toHaveFocus();
  });

  it("clears only Completed Jobs and reports partial cleanup failures", async () => {
    const first = {
      ...embeddedJob("clear-job-a", "Failed"),
      status: "Completed",
      error: null,
    };
    const second = {
      ...embeddedJob("clear-job-b", "Failed"),
      status: "Completed",
      error: null,
    };
    const retained = { ...embeddedJob("clear-job-failed", "Failed") };
    let currentJobs: JobFixture[] = [first, second, retained];
    const fetchMock = vi
      .fn()
      .mockImplementation(async (input: string, init?: RequestInit) => {
        if (input === "/api/status") return statusResponse();
        if (input === "/api/jobs/completed" && init?.method === "DELETE") {
          currentJobs = [second, retained];
          return jsonResponse({
            deleted: [first.id],
            failed: [
              {
                id: second.id,
                error_code: "job_work_cleanup_failed",
                message: "Job Work data could not be cleaned up",
                path: `jobs/${second.id}`,
              },
            ],
          });
        }
        if (input === `/api/jobs/${second.id}` && init?.method === "DELETE") {
          currentJobs = [retained];
          return jsonResponse({ id: second.id, deleted: true });
        }
        if (input.startsWith("/api/jobs")) return jobListResponse(currentJobs);
        return jsonResponse({ term_maps: [] });
      });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithFetch("/jobs", fetchMock);

    expect(
      await screen.findByRole("button", { name: "Clear Completed (2)" }),
    ).toBeEnabled();
    fireEvent.click(screen.getAllByRole("button", { name: "Clear Completed (2)" })[0]);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/jobs/completed", {
        method: "DELETE",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Some Completed Jobs could not be cleared",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Job clear-jo: Job Work data could not be cleaned up",
    );
    expect(screen.getByText("Clear Completed (1)")).toBeInTheDocument();
    expect(screen.getAllByText("Movie.mkv")).not.toHaveLength(0);

    fireEvent.click(screen.getAllByRole("button", { name: /Job clear-jo/ })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Delete Job" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(`/api/jobs/${second.id}`, {
        method: "DELETE",
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByText("Some Completed Jobs could not be cleared."),
      ).not.toBeInTheDocument(),
    );
  });

  it("lists a Term map and supports keyboard inspection and search", async () => {
    renderTermMaps();

    const map = await screen.findByRole("button", { name: /Characters/ });
    expect(map).toHaveTextContent("2 entries");
    fireEvent.click(map);

    expect(
      await screen.findByRole("heading", { name: "Characters" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Captain")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search Source or Target"), {
      target: { value: "ship" },
    });
    expect(screen.getByText("Ship")).toBeInTheDocument();
    expect(screen.queryByText("Captain")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Search Source or Target"), {
      target: { value: "missing" },
    });
    expect(screen.getByText("No matching terms.")).toBeInTheDocument();
  });

  it("shows Term map list and detail loading states", async () => {
    let resolveList!: (value: unknown) => void;
    let resolveDetail!: (value: unknown) => void;
    const listPending = new Promise((resolve) => {
      resolveList = resolve;
    });
    const detailPending = new Promise((resolve) => {
      resolveDetail = resolve;
    });
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/term-maps") return listPending;
      if (input === "/api/term-maps/map-1") return detailPending;
      return jsonResponse({ term_maps: [] });
    });
    renderTermMapsWithFetch(fetchMock);

    expect(screen.getByText("Loading Term maps")).toBeInTheDocument();
    resolveList(jsonResponse({ term_maps: [CHARACTERS_TERM_MAP] }));
    fireEvent.click(await screen.findByRole("button", { name: /Characters/ }));
    expect(screen.getByText("Loading details")).toBeInTheDocument();
    resolveDetail(
      jsonResponse({ ...CHARACTERS_TERM_MAP, content: { Captain: "队长" } }),
    );
    expect(await screen.findByText("Captain")).toBeInTheDocument();
  });

  it("retries a Term map list error and exposes mutation pending states", async () => {
    let listCalls = 0;
    let resolveUpload!: (value: unknown) => void;
    const uploadPending = new Promise((resolve) => {
      resolveUpload = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockImplementation(async (input: string, init?: RequestInit) => {
        if (input === "/api/status") return statusResponse();
        if (input === "/api/term-maps" && init?.method === "POST") return uploadPending;
        if (input === "/api/term-maps") {
          listCalls += 1;
          if (listCalls === 1)
            return jsonResponse({ message: "Term maps unavailable" }, false);
          return jsonResponse({ term_maps: [] });
        }
        return jsonResponse({ term_maps: [] });
      });
    renderTermMapsWithFetch(fetchMock);

    expect(await screen.findByRole("alert")).toHaveTextContent("Term maps unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "No Term maps yet" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Pending" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));
    expect(await screen.findByRole("button", { name: "Uploading..." })).toBeDisabled();
    resolveUpload(
      jsonResponse({
        id: "pending",
        name: "Pending",
        entry_count: 1,
        updated_at: "2026-08-13T12:00:00Z",
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upload Term map" })).toBeEnabled(),
    );
  });

  it("shows and retries a Term map detail error", async () => {
    let detailCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/term-maps")
        return jsonResponse({ term_maps: [CHARACTERS_TERM_MAP] });
      if (input === "/api/term-maps/map-1") {
        detailCalls += 1;
        if (detailCalls === 1)
          return jsonResponse({ message: "Term map detail unavailable" }, false);
        return jsonResponse({ ...CHARACTERS_TERM_MAP, content: { Captain: "队长" } });
      }
      return jsonResponse({ term_maps: [] });
    });
    renderTermMapsWithFetch(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Characters/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Term map detail unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Captain")).toBeInTheDocument();
    expect(detailCalls).toBe(2);
  });

  it("renames, replaces, and confirms deletion of a Term map", async () => {
    const initial = {
      id: "map-1",
      name: "Characters",
      entry_count: 2,
      updated_at: "2026-08-13T12:00:00Z",
    };
    let summary = initial;
    let content: Record<string, string> = { Captain: "队长", Ship: "舰船" };
    let deleted = false;
    const fetchMock = vi
      .fn()
      .mockImplementation(async (input: string, init?: RequestInit) => {
        if (input === "/api/status") return statusResponse();
        if (input === "/api/term-maps") {
          return jsonResponse({ term_maps: deleted ? [] : [summary] });
        }
        if (init?.method === "PATCH") {
          summary = { ...summary, name: "People" };
          return jsonResponse(summary);
        }
        if (init?.method === "PUT") {
          content = { Captain: "队长" };
          summary = { ...summary, entry_count: 1 };
          return jsonResponse(summary);
        }
        if (init?.method === "DELETE") {
          deleted = true;
          return jsonResponse(summary);
        }
        return jsonResponse({ ...summary, content });
      });
    renderTermMapsWithFetch(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Characters/ }));
    fireEvent.change(await screen.findByLabelText("New Term map name"), {
      target: { value: "People" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "People" })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("Replacement JSON content"), {
      target: { value: '{"Captain":"队长"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Replace content" }));
    await waitFor(() => expect(screen.getByText(/1 entries/)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Confirm Term map name"), {
      target: { value: "People" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Delete Term map" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/term-maps/map-1",
        expect.objectContaining({ method: "PATCH" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/term-maps/map-1",
        expect.objectContaining({ method: "PUT" }),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/term-maps/map-1",
        expect.objectContaining({ method: "DELETE" }),
      );
    });
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "People" })).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("heading", { name: "No Term maps yet" }),
    ).toBeInTheDocument();
  });

  it("does not leak mutation state when switching Term maps", async () => {
    const summaries = [
      {
        id: "map-a",
        name: "Alpha",
        entry_count: 1,
        updated_at: "2026-08-13T12:00:00Z",
      },
      {
        id: "map-b",
        name: "Beta",
        entry_count: 1,
        updated_at: "2026-08-13T12:00:00Z",
      },
    ];
    let resolveRename!: (response: unknown) => void;
    let renameSettled = false;
    const renamePending = new Promise((resolve) => {
      resolveRename = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockImplementation(async (input: string, init?: RequestInit) => {
        if (input === "/api/status") return statusResponse();
        if (init?.method === "PATCH") {
          return renamePending.finally(() => {
            renameSettled = true;
          });
        }
        if (input === "/api/term-maps") return jsonResponse({ term_maps: summaries });
        const summary = input.endsWith("map-a") ? summaries[0] : summaries[1];
        return jsonResponse({ ...summary, content: { Source: "Target" } });
      });
    renderTermMapsWithFetch(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Alpha/ }));
    fireEvent.change(await screen.findByLabelText("New Term map name"), {
      target: { value: "Alpha renamed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save name" })).toBeDisabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: /Beta/ }));
    expect(await screen.findByDisplayValue("Beta")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save name" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    resolveRename(jsonResponse(summaries[0]));
    await waitFor(() => expect(renameSettled).toBe(true));
    expect(screen.getByLabelText("New Term map name")).toHaveValue("Beta");
    expect(screen.getByRole("heading", { name: "Beta" })).toBeInTheDocument();
  });

  it("shows the empty state and uploads valid JSON", async () => {
    const fetchMock = termMapFetch({
      id: "new",
      name: "New terms",
      entry_count: 1,
      updated_at: "2026-08-13T12:00:00Z",
    });
    renderTermMapsWithFetch(fetchMock);

    expect(
      await screen.findByRole("heading", { name: "No Term maps yet" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New terms" } });
    fireEvent.change(screen.getByLabelText("JSON content"), {
      target: { value: '{"Captain":"队长"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    await expectTermMapPost(
      fetchMock,
      JSON.stringify({ name: "New terms", content: { Captain: "队长" } }),
    );
    expect(
      screen.getByRole("heading", { name: "No Term maps yet" }),
    ).toBeInTheDocument();
  });

  it("reports client-side JSON validation without making an upload request", async () => {
    const fetchMock = termMapFetch();
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Broken" } });
    fireEvent.change(screen.getByLabelText("JSON content"), { target: { value: "{" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("valid JSON");
    expect(fetchMock).not.toHaveBeenCalledWith(
      "/api/term-maps",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("previews a valid imported JSON file and resets the source form after upload", async () => {
    const fetchMock = termMapFetch({
      id: "new",
      name: "Imported",
      entry_count: 2,
      updated_at: "2026-08-13T12:00:00Z",
    });
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Imported" } });
    fireEvent.change(screen.getByLabelText("JSON file"), {
      target: {
        files: [
          new File(['{"Captain":"队长","Ship":"舰船"}'], "terms.json", {
            type: "application/json",
          }),
        ],
      },
    });

    expect(await screen.findByText("Loaded terms.json")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 mappings");
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    await expectTermMapPost(
      fetchMock,
      JSON.stringify({
        name: "Imported",
        content: { Captain: "队长", Ship: "舰船" },
      }),
    );
    expect(screen.getByLabelText("Name")).toHaveValue("");
    expect(screen.getByLabelText("JSON content")).toHaveValue(
      '{\n  "Source": "Target"\n}',
    );
  });

  it("uses the same validation for dropped JSON and blocks folded duplicate keys", async () => {
    const fetchMock = termMapFetch();
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.drop(screen.getByText("Import JSON file").parentElement!, {
      dataTransfer: {
        files: [new File(['{"Source":"one","source":"two"}'], "terms.json")],
      },
    });

    await expectDuplicateContentRejected(fetchMock);
  });

  it("matches server content validation for object shape and compact UTF-8 size", () => {
    expect(validateTermMapContent("[]").error).toContain("non-empty object");
    expect(validateTermMapContent('{"Source":""}').error).toContain(
      "non-empty strings",
    );
    expect(
      validateTermMapContent('{"Stra\u00dfe":"one","STRASSE":"two"}').error,
    ).toContain("unique regardless of case");
    expect(
      validateTermMapContent('{"\u017fource":"one","source":"two"}').error,
    ).toContain("unique regardless of case");
    expect(validateTermMapContent('{"\u01f0":"one","j\u030c":"two"}').error).toContain(
      "unique regardless of case",
    );
    const prototypeMapping = validateTermMapContent('{"__proto__":"Target"}').content;
    expect(Object.keys(prototypeMapping ?? {})).toEqual(["__proto__"]);
    expect(prototypeMapping?.["__proto__"]).toBe("Target");
    expect(validateTermMapContent('{\u00a0"Source":"Target"}').error).toContain(
      "valid JSON",
    );
    expect(validateTermMapContent('{"Source":"Target"}').byteLength).toBe(
      new TextEncoder().encode('{"Source":"Target"}').byteLength,
    );
  });

  it("blocks duplicate JSON keys before submission", async () => {
    const duplicateContent = '{"Source":"one","Source":"two"}';
    const fetchMock = termMapFetch();
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Duplicate" } });
    fireEvent.change(screen.getByLabelText("JSON content"), {
      target: { value: duplicateContent },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    await expectDuplicateContentRejected(fetchMock);
  });

  it("exposes a duplicate-name API error", async () => {
    const fetchMock = termMapFetch(
      { message: "A Term map with this name already exists" },
      false,
    );
    renderTermMapsWithFetch(fetchMock);

    await screen.findByRole("heading", { name: "No Term maps yet" });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Names" } });
    fireEvent.click(screen.getByRole("button", { name: "Upload Term map" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
  });

  it("browses one directory at a time and filters its entries", async () => {
    renderRoute("/translate");

    expect(
      await screen.findByRole("button", { name: "Open Series" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Select Movie.mkv" }),
    ).toBeInTheDocument();

    const filter = screen.getByRole("searchbox", { name: "Filter this directory" });
    fireEvent.change(filter, { target: { value: "movie" } });

    expect(
      screen.getByRole("button", { name: "Select Movie.mkv" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open Series" }),
    ).not.toBeInTheDocument();
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

  it("queues an External subtitle with the target language", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });

    expect(screen.getByText("Movie.zh-Hans.srt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/jobs",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            media_path: "Movie.mkv",
            subtitle_path: "Movie.en.srt",
            target_language_code: "zh-Hans",
            output_suffix: "zh-Hans",
            output_conflict_policy: "append-number",
            term_map_id: null,
            dynamic_terminology_enabled: true,
            subtitle_terminology_filter_enabled: true,
          }),
        }),
      ),
    );
  });

  it("offers friendly common languages while submitting their BCP 47 code", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    const commonLanguage = screen.getByLabelText("Common target language");
    expect(screen.getByRole("option", { name: "Choose a language" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "Arabic — ar" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Chinese (Simplified) — zh-Hans" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Portuguese (Brazil) — pt-BR" }),
    ).toBeInTheDocument();

    fireEvent.change(commonLanguage, { target: { value: "zh-Hans" } });

    expect(screen.getByLabelText("Target language code")).toHaveValue("zh-Hans");
    expect(screen.getByLabelText("Subtitle suffix")).toHaveValue("zh-Hans");
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/jobs",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining('"target_language_code":"zh-Hans"'),
        }),
      ),
    );
  });

  it("announces queueing while Job creation is pending and after success", async () => {
    renderRoute("/translate");
    await selectExternalSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });
    let resolveCreate!: (value: unknown) => void;
    const createPending = new Promise((resolve) => {
      resolveCreate = resolve;
    });
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(async (input: string, request?: RequestInit) => {
      if (input === "/api/jobs" && request?.method === "POST") return createPending;
      return jobListResponse([]);
    });

    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));
    expect(await screen.findByRole("button", { name: "Queueing..." })).toBeDisabled();
    resolveCreate(jsonResponse({ id: "queued-1", status: "Queued" }));
    expect(await screen.findByText("Translation queued")).toBeInTheDocument();
  });

  it("queues an Embedded subtitle with its stream index and format", async () => {
    renderRoute("/translate");

    await selectEmbeddedSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await expectQueuedJob({
      media_path: "Movie.mkv",
      stream_index: 3,
      source_format: "ass",
    });
  });

  it("distinguishes Embedded streams and shows their known dispositions", async () => {
    renderRoute("/translate", true, undefined, {
      path: "Movie.mkv",
      candidates: [
        {
          kind: "embedded",
          stream_index: 3,
          format: "ass",
          tags: { language: "zhs", title: "Chinese" },
          dispositions: [
            "default",
            "forced",
            "hearing_impaired",
            "visual_impaired",
            "comment",
            "lyrics",
            "karaoke",
            "original",
            "dub",
            "clean_effects",
          ],
        },
        {
          kind: "embedded",
          stream_index: 4,
          format: "ass",
          tags: { language: "zhs", title: "Chinese" },
          dispositions: [],
        },
      ],
      unsupported_candidates: [],
    });

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));

    const first = await screen.findByRole("button", {
      name: /Select embedded subtitle stream 3 zhs \/ Chinese/,
    });
    const second = screen.getByRole("button", {
      name: /Select embedded subtitle stream 4 zhs \/ Chinese/,
    });
    expect(first).toHaveTextContent(
      "ASS · Stream 3 · Default · Forced · Hearing impaired · Visually impaired · Commentary · Lyrics · Karaoke · Original · Dubbed · Clean effects",
    );
    expect(second).toHaveTextContent("ASS · Stream 4");
    expect(first).not.toHaveTextContent("Stream 4");
  });

  it("keeps the Term map control disabled while its list is loading", async () => {
    const pending = new Promise<never>(() => undefined);
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/term-maps") return pending;
      if (input === "/api/media/browse") {
        return jsonResponse({
          path: "",
          entries: [{ kind: "media", name: "Movie.mkv", path: "Movie.mkv" }],
        });
      }
      if (input === "/api/media/discover") {
        return jsonResponse({
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
        });
      }
      return jobListResponse([]);
    });
    renderWithFetch("/translate", fetchMock);
    await selectExternalSubtitle();
    fireEvent.click(screen.getByText("Advanced settings"));

    expect(screen.getByText("Loading Term maps")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Term map selector" })).toBeDisabled();
  });

  it("lists and submits a selected Term map with the default terminology flags", async () => {
    renderRoute(
      "/translate",
      true,
      undefined,
      undefined,
      false,
      [],
      [CHARACTERS_TERM_MAP],
    );

    await selectExternalSubtitle();
    fireEvent.click(screen.getByText("Advanced settings"));
    const termMap = screen.getByLabelText("Term map");
    expect(screen.getByRole("option", { name: "No Term map" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Characters" })).toBeInTheDocument();
    fireEvent.change(termMap, { target: { value: "map-1" } });
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await expectQueuedJobRequest("zh-Hans", "map-1", true, true);
  });

  it("clears a Term map selection after the refreshed list removes it", async () => {
    const termMaps = [CHARACTERS_TERM_MAP];
    const { queryClient } = renderRoute(
      "/translate",
      true,
      undefined,
      undefined,
      false,
      [],
      termMaps,
    );

    fireEvent.click(screen.getByText("Advanced settings"));
    await screen.findByRole("option", { name: "Characters" });
    const termMap = screen.getByLabelText("Term map");
    fireEvent.change(termMap, { target: { value: "map-1" } });
    expect(termMap).toHaveValue("map-1");

    termMaps.length = 0;
    await queryClient.invalidateQueries({ queryKey: ["term-maps"] });

    await waitFor(() => expect(termMap).toHaveValue(""));
  });

  it("composes a safe output name and supports atomic overwrite", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });
    expect(screen.getByLabelText("Media stem")).toHaveValue("Movie.");
    expect(screen.getByLabelText("Media stem")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Source format extension")).toHaveValue(".srt");
    expect(screen.getByLabelText("Subtitle suffix")).toHaveValue("zh-Hans");

    fireEvent.change(screen.getByLabelText("Subtitle suffix"), {
      target: { value: "zh-Hans.forced" },
    });
    expect(screen.getByText("Movie.zh-Hans.forced.srt")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Overwrite existing output"));
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/jobs",
        expect.objectContaining({
          body: expect.stringContaining('"output_suffix":"zh-Hans.forced"'),
        }),
      ),
    );
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/jobs",
      expect.objectContaining({
        body: expect.stringContaining('"output_conflict_policy":"overwrite"'),
      }),
    );
  });

  it("blocks unsafe output suffixes before submission", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });
    fireEvent.change(screen.getByLabelText("Subtitle suffix"), {
      target: { value: "CON" },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("reserved");
    expectJobSubmissionBlocked();
  });

  it("remembers a successful language and resets the source form", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "x-custom" },
    });
    fireEvent.click(screen.getByText("Advanced settings"));
    fireEvent.click(screen.getByLabelText("Dynamic terminology"));
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await waitFor(() => {
      expect(window.localStorage.getItem("cueweaver.target-language")).toBe("x-custom");
    });
    expect(screen.getByLabelText("Target language code")).toHaveValue("x-custom");
    expect(
      screen.getByRole("button", { name: "Select Movie.mkv" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Choose another Media" }),
    ).not.toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/jobs",
      expect.objectContaining({
        body: JSON.stringify({
          media_path: "Movie.mkv",
          subtitle_path: "Movie.en.srt",
          target_language_code: "x-custom",
          output_suffix: "x-custom",
          output_conflict_policy: "append-number",
          term_map_id: null,
          dynamic_terminology_enabled: false,
          subtitle_terminology_filter_enabled: true,
        }),
      }),
    );
  });

  it("disables submission when the Translation provider is unavailable", async () => {
    renderRoute("/translate", false);

    await selectExternalSubtitle();
    fireEvent.change(screen.getByLabelText("Target language code"), {
      target: { value: "zh-Hans" },
    });

    expectJobSubmissionBlocked();
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
      screen.getByRole("button", {
        name: "Select embedded subtitle stream 3 zhs / Chinese",
      }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("bitmap subtitle")).toBeInTheDocument();
    expect(screen.getByText("Not selectable")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Select external subtitle en (Movie.en.srt)",
      }),
    ).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.getByRole("group", { name: "Unsupported embedded subtitle" }),
    ).toHaveAttribute("aria-disabled", "true");
  });

  it("renders a retryable Discovery error", async () => {
    renderRoute("/translate", true, undefined, undefined, true);

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("ffprobe failed");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    const discoverCalls = (
      globalThis.fetch as ReturnType<typeof vi.fn>
    ).mock.calls.filter(([input]) =>
      String(input).includes("/api/media/discover"),
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

    const subtitle = await selectExternalSubtitle();
    expect(subtitle).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Open Series" }));

    await screen.findByRole("button", { name: "Select Episode.mkv" });
    expect(
      screen.queryByRole("button", {
        name: "Select external subtitle en (Movie.en.srt)",
      }),
    ).not.toBeInTheDocument();
  });

  it("clears the selected Media explicitly", async () => {
    renderRoute("/translate");

    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Choose another Media" }),
    );

    expect(
      screen.queryByRole("button", { name: "Choose another Media" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Select Movie.mkv" }),
    ).toBeInTheDocument();
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
