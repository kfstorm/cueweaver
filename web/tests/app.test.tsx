import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

const BATCH_MEDIA: MediaDirectory = {
  path: "",
  entries: [
    { kind: "media", name: "Movie.mkv", path: "Movie.mkv" },
    { kind: "media", name: "Second.mkv", path: "Second.mkv" },
  ],
};

const THREE_BATCH_MEDIA: MediaDirectory = {
  path: "",
  entries: [
    { kind: "media", name: "First.mkv", path: "First.mkv" },
    { kind: "media", name: "Second.mkv", path: "Second.mkv" },
    { kind: "media", name: "Third.mkv", path: "Third.mkv" },
  ],
};

const UNIQUE_BATCH_DISCOVERIES: MediaDiscovery[] = [
  {
    path: "Movie.mkv",
    candidates: [{ kind: "external", path: "Movie.en.srt", format: "srt" }],
    unsupported_candidates: [],
  },
  {
    path: "Second.mkv",
    candidates: [{ kind: "external", path: "Second.en.srt", format: "srt" }],
    unsupported_candidates: [],
  },
];

const THREE_UNIQUE_BATCH_DISCOVERIES: MediaDiscovery[] = [
  {
    path: "First.mkv",
    candidates: [{ kind: "external", path: "First.en.srt", format: "srt" }],
    unsupported_candidates: [],
  },
  {
    path: "Second.mkv",
    candidates: [{ kind: "external", path: "Second.en.srt", format: "srt" }],
    unsupported_candidates: [],
  },
  {
    path: "Third.mkv",
    candidates: [{ kind: "external", path: "Third.en.srt", format: "srt" }],
    unsupported_candidates: [],
  },
];

const AMBIGUOUS_BATCH_DISCOVERIES: MediaDiscovery[] = [
  UNIQUE_BATCH_DISCOVERIES[0],
  {
    path: "Second.mkv",
    candidates: [
      { kind: "external", path: "Second.en.srt", format: "srt" },
      { kind: "embedded", stream_index: 3, format: "ass" },
    ],
    unsupported_candidates: [],
  },
];

const FILTERED_BATCH_DISCOVERIES: MediaDiscovery[] = [
  {
    path: "Movie.mkv",
    candidates: [
      {
        kind: "external",
        path: "Movie.en.srt",
        format: "srt",
        tags: { language: "en", title: "English" },
      },
      {
        kind: "embedded",
        stream_index: 3,
        format: "ass",
        tags: { language: "zhs", title: "Chinese" },
        dispositions: ["forced"],
      },
    ],
    unsupported_candidates: [
      { kind: "embedded", stream_index: 8, reason: "bitmap subtitle" },
    ],
  },
  {
    path: "Second.mkv",
    candidates: [
      {
        kind: "external",
        path: "Second.en.srt",
        format: "srt",
        tags: { language: "en", title: "English" },
      },
      {
        kind: "embedded",
        stream_index: 4,
        format: "ass",
        tags: { language: "zhs", title: "Chinese" },
        dispositions: ["forced"],
      },
    ],
    unsupported_candidates: [],
  },
];

const INCOMPLETE_BATCH_DISCOVERIES: MediaDiscovery[] = [
  {
    path: "Movie.mkv",
    candidates: [{ kind: "embedded", stream_index: 3 }],
    unsupported_candidates: [],
  },
  UNIQUE_BATCH_DISCOVERIES[1],
];

function jsonResponse(body: unknown, ok = true) {
  return { ok, json: async () => body };
}

async function selectBatchMedia() {
  fireEvent.click(await screen.findByLabelText("Batch mode"));
  fireEvent.click(screen.getByRole("button", { name: "Select Movie.mkv" }));
  fireEvent.click(screen.getByRole("button", { name: "Select Second.mkv" }));
}

async function selectDirectoryTermMap(id: string, optionName: string) {
  const directorySelect = await screen.findByRole("combobox", {
    name: "Directory default",
  });
  await screen.findByRole("option", { name: optionName });
  fireEvent.change(directorySelect, { target: { value: id } });
  return directorySelect;
}

type JobFixture = { status: string; [key: string]: unknown };

function jobListResponse(
  jobs: JobFixture[],
  next_cursor: string | null = null,
  completedCount = jobs.filter((job) => job.status === "Completed").length,
) {
  const activeStatuses = ["Queued", "Extracting", "Translating"];
  return jsonResponse({
    active_jobs: jobs.filter((job) => activeStatuses.includes(job.status)),
    history_jobs: jobs.filter((job) => !activeStatuses.includes(job.status)),
    next_cursor,
    matching_count: jobs.length,
    completed_count: completedCount,
  });
}

function isJobDetailRequest(input: string): boolean {
  return /^\/api\/jobs\/[^/]+$/.test(input);
}

async function expectEmbeddedSubtitlePrompt(language: string) {
  expect(
    await screen.findByText(`Embedded subtitle · Stream 3 to ${language}`),
  ).toBeInTheDocument();
}

function emptyMediaResponse() {
  return jsonResponse({ path: "", entries: [] });
}

function singleExternalMediaResponse(input: string) {
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
  return null;
}

function createDirectoryMutationFetchMock(
  termMaps: TermMapSummary[],
  handlers: {
    bind?: (
      scenario: {
        localTermMap: TermMapSummary | null;
        bindCalls: number;
        removeCalls: number;
      },
      state: () => Record<string, unknown>,
    ) => unknown;
    remove?: (
      scenario: {
        localTermMap: TermMapSummary | null;
        bindCalls: number;
        removeCalls: number;
      },
      state: () => Record<string, unknown>,
    ) => unknown;
  } = {},
  initialLocalTermMap: TermMapSummary | null = termMaps[0] ?? null,
) {
  const scenario = {
    localTermMap: initialLocalTermMap,
    bindCalls: 0,
    removeCalls: 0,
  };
  const fetchMock = vi
    .fn()
    .mockImplementation(async (input: string, init?: RequestInit) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/term-maps") {
        return jsonResponse({ term_maps: termMaps });
      }
      if (input.startsWith("/api/term-maps/directory")) {
        const state = () => ({
          directory: "",
          local: scenario.localTermMap,
          effective: scenario.localTermMap,
          source_directory: scenario.localTermMap ? "" : null,
        });
        if (init?.method === "PUT") {
          scenario.bindCalls += 1;
          return handlers.bind?.(scenario, state) ?? jsonResponse(state());
        }
        if (init?.method === "DELETE") {
          scenario.removeCalls += 1;
          return handlers.remove?.(scenario, state) ?? jsonResponse(state());
        }
        return jsonResponse(state());
      }
      return singleExternalMediaResponse(input) ?? jobListResponse([]);
    });
  return { fetchMock, scenario };
}

function statusResponse(
  providerReady = true,
  jobRecords?: {
    corrupt: { count: number; location: string };
    unsupported: { count: number; location: string };
  },
) {
  return jsonResponse({
    api: { ready: true },
    roots: { ready: true },
    translation_provider: providerReady
      ? { ready: true }
      : {
          ready: false,
          message:
            "Set PROVIDER and the matching provider environment variables, then restart CueWeaver.",
        },
    worker: { ready: true, mode: "single" },
    ...(jobRecords ? { job_records: jobRecords } : {}),
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

async function openVisibleJobDetail(fetchImplementation: typeof fetch) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  renderWithFetch("/jobs", fetchImplementation);
  fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
  await screen.findByRole("heading", { name: "Request summary" });
}

function jobsFetch(job: JobFixture) {
  return vi.fn().mockImplementation(async (input: string) => {
    if (input === "/api/status") return statusResponse();
    if (isJobDetailRequest(input)) return jsonResponse(job);
    if (input.startsWith("/api/jobs")) return jobListResponse([job]);
    return jsonResponse({ term_maps: [] });
  });
}

function jobListFetch(getJobs: () => JobFixture[]) {
  return vi.fn().mockImplementation(async (input: string) => {
    if (input === "/api/status") return statusResponse();
    if (isJobDetailRequest(input)) return jsonResponse(getJobs()[0]);
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
    if (isJobDetailRequest(input)) return jsonResponse(currentJob);
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
    if (isJobDetailRequest(input)) return jsonResponse(getJobs());
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
      term_map_mode: "follow",
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

function queuedEmbeddedJob(id: string) {
  return {
    ...embeddedJob(id, "Failed"),
    status: "Queued" as const,
    started_at: null,
    finished_at: null,
    queue_position: 1,
    error: null,
  };
}

function mockQueuedJobCreation(job: JobFixture, termMaps: TermMapSummary[] = []) {
  const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
  fetchMock.mockImplementation(async (input: string, request?: RequestInit) => {
    if (input === "/api/status") return statusResponse();
    if (input === "/api/jobs" && request?.method === "POST") {
      return jsonResponse(job);
    }
    if (input.startsWith("/api/jobs")) return jobListResponse([job]);
    return jsonResponse({ term_maps: termMaps });
  });
  return fetchMock;
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
      if (isJobDetailRequest(input)) return jsonResponse(job);
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

async function selectExternalSubtitleWithLanguage(language = "zh-Hans") {
  await selectExternalSubtitle();
  await enterCustomTargetLanguage(language);
}

async function enterCustomTargetLanguage(language: string) {
  fireEvent.change(screen.getByLabelText("Common target language"), {
    target: { value: "custom" },
  });
  fireEvent.change(await screen.findByLabelText("Target language code"), {
    target: { value: language },
  });
}

function mockBatchRequest(response: unknown) {
  const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
  const defaultImplementation = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((input, init) =>
    String(input) === "/api/jobs/batch" && init?.method === "POST"
      ? Promise.resolve(jsonResponse(response))
      : defaultImplementation(input, init),
  );
  return fetchMock;
}

async function submitBatch() {
  await enterCustomTargetLanguage("zh-Hans");
  fireEvent.click(screen.getByRole("button", { name: "Queue selected translations" }));
}

async function expectBatchResultsStatus(status: string) {
  expect(
    await screen.findByRole("heading", { name: "Batch results" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(status);
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
          output_conflict_policy: "skip",
          term_map_mode: "follow",
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
          output_conflict_policy: "skip",
          term_map_mode: termMapId === null ? "follow" : "selected",
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
        "Set PROVIDER and the matching provider environment variables",
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

  it("warns about quarantined Job records in the sidebar and Jobs page", async () => {
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") {
        return statusResponse(true, {
          corrupt: { count: 2, location: "jobs/corrupt" },
          unsupported: { count: 1, location: "jobs/unsupported" },
        });
      }
      if (input === "/api/jobs") return jsonResponse({ jobs: [] });
      return jsonResponse({ term_maps: [] });
    });

    renderWithFetch("/jobs", fetchMock);

    expect((await screen.findAllByText("Job records need attention")).length).toBe(2);
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "DD" &&
          element.textContent?.includes("2 records in jobs/corrupt") === true,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, element) =>
          element?.tagName === "DD" &&
          element.textContent?.includes("1 record in jobs/unsupported") === true,
      ),
    ).toBeInTheDocument();
  });

  it("searches Job history, exposes every status filter, and clears no-match filters", async () => {
    const job = embeddedJob("filter-job", "Failed");
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input.includes("search=missing")) return jobListResponse([]);
      if (input.startsWith("/api/jobs")) return jobListResponse([job]);
      return jsonResponse({ term_maps: [] });
    });

    renderWithFetch("/jobs", fetchMock);
    expect(await screen.findByText("Movie.mkv")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Queued status" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Extracting status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Translating status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Completed history" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Failed history" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Interrupted history" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Cancelled history" }),
    ).toBeInTheDocument();

    const statusSelect = screen.getByRole("combobox", { name: "Status" });
    for (const status of [
      "Queued",
      "Extracting",
      "Translating",
      "Completed",
      "Failed",
      "Interrupted",
      "Cancelled",
    ]) {
      fireEvent.change(statusSelect, { target: { value: status } });
      await waitFor(() =>
        expect(
          fetchMock.mock.calls.some(([input]) =>
            String(input).includes(`status=${status}`),
          ),
        ).toBe(true),
      );
    }

    fireEvent.change(screen.getByRole("searchbox", { name: "Search Jobs" }), {
      target: { value: "missing" },
    });
    expect(await screen.findByText("No matching Jobs")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("Movie.mkv")).toBeInTheDocument();
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

  it("opens a durable Job detail with local timestamps and status history", async () => {
    const job = {
      id: "job-detail-1",
      attempt: 2,
      status: "Completed" as const,
      created_at: "2026-08-13T12:00:00Z",
      started_at: "2026-08-13T12:00:01Z",
      finished_at: "2026-08-13T12:00:02Z",
      status_history: [
        {
          status: "Queued" as const,
          attempt: 2,
          started_at: "2026-08-13T12:00:00Z",
          finished_at: "2026-08-13T12:00:01Z",
        },
        {
          status: "Completed" as const,
          attempt: 2,
          started_at: "2026-08-13T12:00:01Z",
          finished_at: "2026-08-13T12:00:02Z",
        },
      ],
      queue_position: null,
      request: {
        media_path: "Shows/Movie.mkv",
        subtitle_path: "Shows/Movie.en.srt",
        target_language_code: "zh-Hans",
        term_map_mode: "selected",
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
    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));

    expect(
      await screen.findByRole("heading", { name: "Request summary" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Movie.mkv" })).toHaveFocus();
    expect(screen.getByText("Shows/Movie.mkv")).toBeInTheDocument();
    expect(screen.getByText("job-detail-1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy Job ID" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy Job ID" }));
    expect(
      await screen.findByText("Select the Job ID and copy it manually."),
    ).toBeInTheDocument();
    expect(screen.getByText("Characters")).toBeInTheDocument();
    expect(screen.getByText("Explicit Term map")).toBeInTheDocument();
    expect(screen.getByText("Shows/Movie.zh-Hans.2.srt")).toBeInTheDocument();
    expect(screen.getByText("Status history")).toBeInTheDocument();
    expect(screen.getAllByText("Attempt 2")).toHaveLength(2);
    expect(screen.queryByText(/work\/jobs/)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/job-detail-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Back to Jobs" }));
    expect(
      await screen.findByRole("heading", { name: "Select a Job" }),
    ).toBeInTheDocument();
  });

  it("keeps an open Job detail current while polling", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const queued = {
      ...queuedEmbeddedJob("job-detail-polling"),
      status_history: [
        {
          status: "Queued" as const,
          attempt: 1,
          started_at: "2026-08-13T12:00:00Z",
          finished_at: null,
        },
      ],
    };
    const translating = {
      ...queued,
      status: "Translating" as const,
      queue_position: null,
      started_at: "2026-08-13T12:00:01Z",
      status_history: [
        {
          ...queued.status_history[0],
          finished_at: "2026-08-13T12:00:01Z",
        },
        {
          status: "Translating" as const,
          attempt: 1,
          started_at: "2026-08-13T12:00:01Z",
          finished_at: null,
        },
      ],
    };
    const completed = {
      ...translating,
      status: "Completed" as const,
      finished_at: "2026-08-13T12:00:02Z",
      status_history: [
        ...translating.status_history.slice(0, 1),
        {
          ...translating.status_history[1],
          finished_at: "2026-08-13T12:00:02Z",
        },
        {
          status: "Completed" as const,
          attempt: 1,
          started_at: "2026-08-13T12:00:02Z",
          finished_at: "2026-08-13T12:00:02Z",
        },
      ],
    };
    let currentJob: JobFixture = queued;
    const fetchMock = jobsPageFetch(() => currentJob);

    try {
      await openVisibleJobDetail(fetchMock);

      currentJob = translating;
      await vi.advanceTimersByTimeAsync(2000);
      await waitFor(() =>
        expect(
          within(screen.getByRole("list", { name: "Job status history" })).getByText(
            "Translating",
          ),
        ).toBeInTheDocument(),
      );

      currentJob = completed;
      await vi.advanceTimersByTimeAsync(5000);
      await waitFor(() =>
        expect(
          within(screen.getByRole("list", { name: "Job status history" })).getByText(
            "Completed",
          ),
        ).toBeInTheDocument(),
      );
      const history = screen.getByRole("list", { name: "Job status history" });
      expect(within(history).getAllByText("Queued")).toHaveLength(1);
      expect(
        within(screen.getByRole("region", { name: "Job details" })).getAllByText(
          "Completed",
        ),
      ).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("copies a Job ID successfully when the browser clipboard is available", async () => {
    const originalClipboard = navigator.clipboard;
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    try {
      const job = embeddedJob("copy-success-job", "Failed");
      renderWithFetch("/jobs", jobsFetch(job));
      fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
      fireEvent.click(await screen.findByRole("button", { name: "Copy Job ID" }));
      expect(await screen.findByText("Copied")).toBeInTheDocument();
      expect(writeText).toHaveBeenCalledWith("copy-success-job");
    } finally {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: originalClipboard,
      });
    }
  });

  it("distinguishes all Term map policies and an absent snapshot", async () => {
    for (const [mode, policy] of [
      ["follow", "Follow directory default"],
      ["none", "Explicitly disabled"],
      ["selected", "Explicit Term map"],
    ] as const) {
      const job = {
        ...embeddedJob(`term-map-${mode}`, "Failed"),
        request: {
          ...embeddedJob(`term-map-${mode}`, "Failed").request,
          term_map_mode: mode,
          term_map: null,
        },
      };
      renderWithFetch("/jobs", jobsFetch(job));
      fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
      expect(await screen.findByText(policy)).toBeInTheDocument();
      expect(screen.getByText("Not recorded")).toBeInTheDocument();
      cleanup();
    }
  });

  it("refreshes a Job detail after an out-of-band retry", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const failed = {
      ...embeddedJob("job-detail-retry", "Failed"),
      status_history: [
        {
          status: "Queued" as const,
          attempt: 1,
          started_at: "2026-08-13T12:00:00Z",
          finished_at: "2026-08-13T12:00:01Z",
        },
        {
          status: "Translating" as const,
          attempt: 1,
          started_at: "2026-08-13T12:00:01Z",
          finished_at: "2026-08-13T12:00:02Z",
        },
        {
          status: "Failed" as const,
          attempt: 1,
          started_at: "2026-08-13T12:00:02Z",
          finished_at: "2026-08-13T12:00:03Z",
        },
      ],
    };
    const queued = {
      ...failed,
      status: "Queued" as const,
      attempt: 2,
      started_at: null,
      finished_at: null,
      queue_position: 1,
      error: null,
      status_history: [
        ...failed.status_history,
        {
          status: "Queued" as const,
          attempt: 2,
          started_at: "2026-08-13T12:00:04Z",
          finished_at: null,
        },
      ],
    };
    let currentJob: JobFixture = failed;
    const fetchMock = jobsPageFetch(() => currentJob);

    try {
      await openVisibleJobDetail(fetchMock);
      expect(
        within(screen.getByRole("region", { name: "Job details" })).getAllByText(
          "Failed",
        ),
      ).toHaveLength(2);

      currentJob = queued;
      await vi.advanceTimersByTimeAsync(2000);
      await waitFor(() =>
        expect(
          within(screen.getByRole("list", { name: "Job status history" })).getAllByText(
            "Queued",
          ),
        ).toHaveLength(2),
      );
      expect(screen.getByText("Attempt 2")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("announces a newly observed completion without browser notification permission", async () => {
    let currentJob = {
      ...embeddedJob("job-notice-1", "Interrupted"),
      status: "Translating",
      error: null,
    };
    const fetchMock = jobsPageFetch(() => currentJob);
    const { queryClient } = renderWithFetch("/jobs", fetchMock);

    await screen.findByText("Embedded subtitle · Stream 3 to zh-Hans");
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

    await screen.findByText("Embedded subtitle · Stream 3 to zh-Hans");
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
      } else {
        expect(
          screen.getByText("Running Jobs cannot be cancelled."),
        ).toBeInTheDocument();
      }
      cleanup();
    }
  });

  it("renders Job creation time as an English relative label", async () => {
    const now = vi
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-08-15T12:00:00Z").valueOf());
    try {
      const job = {
        ...embeddedJob("relative-time-job", "Failed"),
        created_at: "2026-08-14T12:00:00Z",
      };
      renderWithFetch("/jobs", jobsFetch(job));

      expect(await screen.findByText("Created yesterday")).toBeInTheDocument();
    } finally {
      now.mockRestore();
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
        if (isJobDetailRequest(input)) return jsonResponse(job);
        if (input.startsWith("/api/jobs")) return jobListResponse([job]);
        return jsonResponse({ term_maps: [] });
      });
    renderWithFetch("/jobs", fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: /Movie\.mkv/ }));
    fireEvent.click(screen.getByRole("button", { name: "Retry Job" }));
    expect(await screen.findByRole("button", { name: "Retrying..." })).toBeDisabled();
    resolveRetry(jsonResponse({ ...job, status: "Failed", error: null }));
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

    await expectEmbeddedSubtitlePrompt("zh-Hans");
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

    await expectEmbeddedSubtitlePrompt("zh");
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
    expect(confirm).toHaveBeenCalledWith(
      "Delete Job delete-j? This removes its Job history and residual Work data. Media and published output are preserved.",
    );
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
    expect(screen.getByRole("heading", { name: "Job history" })).toHaveFocus();
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
        if (input.includes("search=missing")) {
          return jobListResponse(
            [],
            null,
            currentJobs.filter((job) => job.status === "Completed").length,
          );
        }
        if (input.startsWith("/api/jobs")) return jobListResponse(currentJobs);
        return jsonResponse({ term_maps: [] });
      });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithFetch("/jobs", fetchMock);

    expect(
      await screen.findByRole("button", { name: "Clear Completed (2)" }),
    ).toBeEnabled();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search Jobs" }), {
      target: { value: "missing" },
    });
    expect(
      await screen.findByRole("heading", { name: "No matching Jobs" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Clear Completed (2)" }),
    ).toBeEnabled();
    fireEvent.click(
      screen.getAllByRole("button", { name: "Clear Completed (2)" })[0],
    );

    expect(confirm).toHaveBeenCalledWith(
      "Clear all completed Job history? This removes 2 completed Jobs and residual Work data. Media and published output are preserved.",
    );

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
    fireEvent.change(screen.getByRole("searchbox", { name: "Search Jobs" }), {
      target: { value: "" },
    });
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
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    renderTermMaps();

    const map = await screen.findByRole("button", { name: /Characters/ });
    expect(map).toHaveTextContent("2 entries");
    fireEvent.click(map);

    expect(
      await screen.findByRole("heading", { name: "Characters" }),
    ).toBeInTheDocument();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { name: "Characters" }),
    );
    expect(screen.getByText("Captain")).toBeInTheDocument();
    const termSearch = screen.getByRole("textbox", { name: "Search Source or Target" });
    expect(termSearch).toHaveAttribute("placeholder", "Type to filter");
    fireEvent.change(termSearch, {
      target: { value: "ship" },
    });
    expect(screen.getByText("Ship")).toBeInTheDocument();
    expect(screen.queryByText("Captain")).not.toBeInTheDocument();
    fireEvent.change(termSearch, {
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
    expect(screen.getByRole("heading", { name: "Term map details" })).not.toHaveFocus();
    resolveDetail(
      jsonResponse({ ...CHARACTERS_TERM_MAP, content: { Captain: "队长" } }),
    );
    expect(await screen.findByText("Captain")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Characters" })).toHaveFocus();
  });

  it("rejects a Term map whose source JSON exceeds 1 MiB", () => {
    const content = `{"source":"target"${" ".repeat(1024 * 1024)}}`;

    expect(validateTermMapContent(content).error).toBe(
      "Term map must be at most 1 MiB.",
    );
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

    expect(await screen.findByText("Term maps unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("heading", { name: "No Term maps yet" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Pending" } });
    fireEvent.change(screen.getByLabelText("JSON content"), {
      target: { value: '{"Other":"Value"}' },
    });
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
    const { queryClient } = renderTermMapsWithFetch(fetchMock);
    queryClient.setQueryDefaults(["directory-term-map"], { staleTime: 30_000 });
    const directoryPaths = ["Series", "Series/Season 1"];
    const setFreshDirectoryCaches = () => {
      for (const directory of directoryPaths) {
        queryClient.setQueryData(["directory-term-map", directory], {
          directory,
          local: initial,
          effective: initial,
          source_directory: "Series",
        });
      }
    };
    const directoryCachesAreStale = () =>
      directoryPaths.every(
        (directory) =>
          queryClient
            .getQueryCache()
            .find({ queryKey: ["directory-term-map", directory] })
            ?.isStale() === true,
      );
    setFreshDirectoryCaches();

    fireEvent.click(await screen.findByRole("button", { name: /Characters/ }));
    fireEvent.change(await screen.findByLabelText("New Term map name"), {
      target: { value: "People" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "People" })).toBeInTheDocument(),
    );
    expect(directoryCachesAreStale()).toBe(true);
    setFreshDirectoryCaches();
    fireEvent.change(screen.getByLabelText("Replacement JSON content"), {
      target: { value: '{"Ship":"舰船","Captain":"队长"}' },
    });
    expect(screen.getByRole("button", { name: "Replace content" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Replacement JSON content"), {
      target: { value: "{" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("valid JSON");
    expect(screen.getByLabelText("Replacement JSON content")).toHaveValue("{");
    fireEvent.change(screen.getByLabelText("Replacement JSON content"), {
      target: { value: '{\n  "Captain": "队长",\n  "Ship": "舰船"\n}' },
    });
    expect(screen.getByRole("button", { name: "Replace content" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Replacement JSON content"), {
      target: { value: '{"Captain":"队长"}' },
    });
    expect(screen.getByRole("button", { name: "Replace content" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Replace content" }));
    await waitFor(() => expect(screen.getByText(/1 entries/)).toBeInTheDocument());
    expect(directoryCachesAreStale()).toBe(true);
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
    expect(screen.getByRole("button", { name: "Save name" })).toBeDisabled();
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
    expect(screen.getByLabelText("Name")).toHaveAttribute(
      "placeholder",
      "Name it by media, season, language pair, and version.",
    );
    expect(screen.getByLabelText("JSON content")).toHaveAttribute(
      "placeholder",
      '{\n  "Source": "Target"\n}',
    );
    expect(screen.getByRole("status")).toHaveTextContent("file import or paste path");
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
    expect(screen.getByLabelText("JSON content")).toHaveValue("");
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
    fireEvent.change(screen.getByLabelText("JSON content"), {
      target: { value: '{"Source":"Target"}' },
    });
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

  it("auto-selects unique batch candidates and leaves ambiguous Media unselected", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      AMBIGUOUS_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();

    expect(
      await screen.findByText(
        "Multiple subtitles found. Select one candidate to continue.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Select external subtitle Metadata unavailable (Movie.en.srt)",
      }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.queryByRole("button", { name: /Select external subtitle.*Second/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps selected batch Media reachable through filtering and restores focus", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter this directory" }), {
      target: { value: "Second" },
    });

    const clearMovie = within(
      screen.getByRole("region", { name: "Subtitle selection for Movie.mkv" }),
    ).getByRole("button", { name: "Choose another Media" });
    fireEvent.click(clearMovie);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Select Second.mkv" })).toHaveFocus(),
    );
  });

  it("restores the filtered Media browser when clearing its only batch item", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );

    fireEvent.click(await screen.findByLabelText("Batch mode"));
    fireEvent.click(screen.getByRole("button", { name: "Select Movie.mkv" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter this directory" }), {
      target: { value: "Second" },
    });
    fireEvent.click(
      within(
        screen.getByRole("region", { name: "Subtitle selection for Movie.mkv" }),
      ).getByRole("button", { name: "Choose another Media" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Select Movie.mkv" })).toHaveFocus(),
    );
  });

  it("focuses the next selected Media when clearing a middle batch item", async () => {
    renderRoute(
      "/translate",
      true,
      THREE_BATCH_MEDIA,
      undefined,
      false,
      THREE_UNIQUE_BATCH_DISCOVERIES,
    );

    fireEvent.click(await screen.findByLabelText("Batch mode"));
    fireEvent.click(screen.getByRole("button", { name: "Select First.mkv" }));
    fireEvent.click(screen.getByRole("button", { name: "Select Second.mkv" }));
    fireEvent.click(screen.getByRole("button", { name: "Select Third.mkv" }));
    fireEvent.click(
      within(
        screen.getByRole("region", { name: "Subtitle selection for Second.mkv" }),
      ).getByRole("button", { name: "Choose another Media" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Select Third.mkv" })).toHaveFocus(),
    );
  });

  it("filters and manually resolves an ambiguous Embedded subtitle", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      AMBIGUOUS_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();
    await screen.findByText(
      "Multiple subtitles found. Select one candidate to continue.",
    );
    fireEvent.click(await screen.findByRole("button", { name: "Resolve candidates" }));
    fireEvent.change(
      screen.getByRole("searchbox", { name: "Search subtitle candidates" }),
      {
        target: { value: "ass" },
      },
    );

    const embedded = await screen.findByRole("button", {
      name: "Select embedded subtitle stream 3 Metadata unavailable",
    });
    expect(
      screen.queryByRole("button", { name: /Second\.en\.srt/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(embedded);
    expect(embedded).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps manual selections when the shared candidate filter changes", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      FILTERED_BATCH_DISCOVERIES,
    );
    const fetchMock = mockBatchRequest({ results: [{ id: "job-1" }, { id: "job-2" }] });

    await selectBatchMedia();
    const filter = await screen.findByRole("searchbox", {
      name: "Search subtitle candidates",
    });
    fireEvent.change(filter, { target: { value: "en" } });
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Select external subtitle en / English (Movie.en.srt)",
      }),
    );
    fireEvent.change(filter, { target: { value: "ass" } });
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Select embedded subtitle stream 4 zhs / Chinese",
      }),
    );

    await submitBatch();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/batch",
        expect.objectContaining({
          body: expect.stringContaining(
            '"items":[{"media_path":"Movie.mkv","subtitle_path":"Movie.en.srt"},{"media_path":"Second.mkv","stream_index":4,"source_format":"ass"}]',
          ),
        }),
      ),
    );
  });

  it("selects only complete unique candidates from the current filter", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      AMBIGUOUS_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();
    await screen.findByText(
      "Multiple subtitles found. Select one candidate to continue.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Select unique" }));

    expect(
      screen.getByRole("button", {
        name: "Select external subtitle Metadata unavailable (Movie.en.srt)",
      }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByText("Multiple subtitles found. Select one candidate to continue."),
    ).toBeInTheDocument();
  });

  it("recovers a failed batch Discovery row with retry", async () => {
    renderRoute("/translate", true, BATCH_MEDIA, undefined, false, [
      new Error("ffprobe failed"),
      UNIQUE_BATCH_DISCOVERIES[1],
      UNIQUE_BATCH_DISCOVERIES[0],
    ]);

    await selectBatchMedia();
    expect(
      await screen.findByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: "Select external subtitle Metadata unavailable (Movie.en.srt)",
        }),
      ).toHaveAttribute("aria-pressed", "true"),
    );
  });

  it("filters candidate language, name, format, tags, and Embedded metadata", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      FILTERED_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();
    const filter = await screen.findByRole("searchbox", {
      name: "Search subtitle candidates",
    });

    fireEvent.change(filter, { target: { value: "en" } });
    expect(
      await screen.findByRole("button", {
        name: "Select external subtitle en / English (Movie.en.srt)",
      }),
    ).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "Movie.en.srt" } });
    expect(
      await screen.findByRole("button", {
        name: "Select external subtitle en / English (Movie.en.srt)",
      }),
    ).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "ass" } });
    expect(
      await screen.findByRole("button", {
        name: "Select embedded subtitle stream 3 zhs / Chinese",
      }),
    ).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "Chinese" } });
    expect(
      await screen.findByRole("button", {
        name: "Select embedded subtitle stream 3 zhs / Chinese",
      }),
    ).toBeInTheDocument();

    fireEvent.change(filter, { target: { value: "forced" } });
    expect(
      await screen.findByRole("button", {
        name: "Select embedded subtitle stream 3 zhs / Chinese",
      }),
    ).toBeInTheDocument();
  });

  it("does not present incomplete candidates as selectable", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      INCOMPLETE_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();
    fireEvent.click(screen.getByRole("button", { name: "Select unique" }));
    await enterCustomTargetLanguage("zh-Hans");
    expect(
      await screen.findByRole("button", {
        name: "Select embedded subtitle stream 3 Metadata unavailable",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Queue selected translations" }),
    ).toBeDisabled();
  });

  it("shows empty filtered results even when unsupported candidates remain", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      FILTERED_BATCH_DISCOVERIES,
    );

    await selectBatchMedia();
    fireEvent.change(
      await screen.findByRole("searchbox", {
        name: "Search subtitle candidates",
      }),
      { target: { value: "no-match" } },
    );

    expect(
      await screen.findAllByText("No subtitle candidates match this filter."),
    ).toHaveLength(2);
  });

  it("submits an ordered mixed External and Embedded batch", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      AMBIGUOUS_BATCH_DISCOVERIES,
    );
    const fetchMock = mockBatchRequest({ results: [{ id: "job-1" }, { id: "job-2" }] });

    await selectBatchMedia();
    await screen.findByText(
      "Multiple subtitles found. Select one candidate to continue.",
    );
    fireEvent.click(await screen.findByRole("button", { name: "Resolve candidates" }));
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Select embedded subtitle stream 3 Metadata unavailable",
      }),
    );
    await submitBatch();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/batch",
        expect.objectContaining({
          body: expect.stringContaining(
            '"items":[{"media_path":"Movie.mkv","subtitle_path":"Movie.en.srt"},{"media_path":"Second.mkv","stream_index":3,"source_format":"ass"}]',
          ),
        }),
      ),
    );
  });

  it("submits selected unique Media as one ordered batch request", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );
    const fetchMock = mockBatchRequest({
      results: [
        { id: "job-1" },
        {
          error_code: "term_map_not_found",
          message: "Term map does not exist",
          id: "missing",
        },
      ],
    });

    await selectBatchMedia();
    await submitBatch();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/batch",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            items: [
              { media_path: "Movie.mkv", subtitle_path: "Movie.en.srt" },
              { media_path: "Second.mkv", subtitle_path: "Second.en.srt" },
            ],
            target_language_code: "zh-Hans",
            output_suffix: "zh-Hans",
            output_conflict_policy: "skip",
            term_map_mode: "follow",
            term_map_id: null,
            dynamic_terminology_enabled: true,
            subtitle_terminology_filter_enabled: true,
          }),
        }),
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent("1 Job queued · 1 error.");
    expect(screen.getAllByText("Movie.mkv").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Second.mkv").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Term map does not exist")).toBeInTheDocument();
  });

  it("associates mixed batch results with ordered Media and keeps later Jobs visible", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );
    mockBatchRequest({
      results: [
        {
          error_code: "term_map_not_found",
          message: "Term map does not exist",
          field: "term_map_id",
          secret: "must not be shown",
        },
        { id: "job-2" },
      ],
    });

    await selectBatchMedia();
    await submitBatch();

    await expectBatchResultsStatus("1 Job queued · 1 error.");
    const results = screen.getByLabelText("Batch submission results");
    expect(
      within(results)
        .getAllByRole("group", { name: /batch result/ })
        .map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Movie.mkv batch result", "Second.mkv batch result"]);
    expect(within(results).getAllByText("Movie.mkv").length).toBeGreaterThanOrEqual(1);
    expect(within(results).getByText("Second.mkv")).toBeInTheDocument();
    expect(within(results).getByText("Term map does not exist")).toBeInTheDocument();
    fireEvent.click(within(results).getByText("Show error details"));
    expect(within(results).getByText("term_map_not_found")).toBeInTheDocument();
    expect(within(results).getByText("term_map_id")).toBeInTheDocument();
    expect(within(results).queryByText("must not be shown")).not.toBeInTheDocument();
    expect(
      within(results).getByRole("button", { name: "View Job" }),
    ).toBeInTheDocument();
    expect(within(results).getByText("job-2", { exact: false })).toBeInTheDocument();
  });

  it("keeps the submitted Media snapshot while a batch response is pending", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );
    let resolveBatch: (response: ReturnType<typeof jsonResponse>) => void = () => {};
    const batchResponse = new Promise<ReturnType<typeof jsonResponse>>((resolve) => {
      resolveBatch = resolve;
    });
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    const defaultImplementation = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, init) =>
      String(input) === "/api/jobs/batch" && init?.method === "POST"
        ? batchResponse
        : defaultImplementation(input, init),
    );

    await selectBatchMedia();
    await submitBatch();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/batch",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Select Movie.mkv" }));
    resolveBatch(jsonResponse({ results: [{ id: "job-1" }, { id: "job-2" }] }));

    const results = await screen.findByLabelText("Batch submission results");
    expect(
      within(results)
        .getAllByRole("group", { name: /batch result/ })
        .map((row) => row.getAttribute("aria-label")),
    ).toEqual(["Movie.mkv batch result", "Second.mkv batch result"]);
  });

  it("configures shared output settings in batch mode", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );
    const fetchMock = mockBatchRequest({ results: [{ id: "job-1" }, { id: "job-2" }] });

    await selectBatchMedia();
    const suffix = await screen.findByLabelText("Subtitle suffix");
    fireEvent.change(suffix, { target: { value: "zh-Hans.forced" } });
    fireEvent.click(screen.getByLabelText("Overwrite existing output"));
    await submitBatch();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/jobs/batch",
        expect.objectContaining({
          body: expect.stringContaining('"output_suffix":"zh-Hans.forced"'),
        }),
      ),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/batch",
      expect.objectContaining({
        body: expect.stringContaining('"output_conflict_policy":"overwrite"'),
      }),
    );
  });

  it("resets single-file output settings when entering batch mode", async () => {
    renderRoute("/translate");

    await selectExternalSubtitleWithLanguage();
    fireEvent.change(screen.getByLabelText("Subtitle suffix"), {
      target: { value: "single-only" },
    });
    fireEvent.click(screen.getByLabelText("Overwrite existing output"));
    fireEvent.click(screen.getByLabelText("Batch mode"));
    fireEvent.click(await screen.findByRole("button", { name: "Select Movie.mkv" }));

    expect(screen.getByLabelText("Subtitle suffix")).toHaveValue("zh-Hans");
    expect(screen.getByLabelText(/Skip existing output/u)).toBeChecked();
    expect(screen.getByText("(No Job if output exists)")).toBeInTheDocument();
    expect(screen.queryByText(/Output filename:/u)).not.toBeInTheDocument();
    expect(screen.queryByText("single-only")).not.toBeInTheDocument();
  });

  it("queues an External subtitle with the target language", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    await enterCustomTargetLanguage("zh-Hans");

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
            output_conflict_policy: "skip",
            term_map_mode: "follow",
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

    expect(screen.queryByLabelText("Target language code")).not.toBeInTheDocument();
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

  it("starts new translations at an explicit language choice", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();

    const commonLanguage = screen.getByLabelText("Common target language");
    expect(commonLanguage).toHaveValue("");
    expect(screen.getByRole("option", { name: "Choose a language" })).toBeDisabled();
    expect(screen.queryByLabelText("Target language code")).not.toBeInTheDocument();

    fireEvent.change(commonLanguage, { target: { value: "custom" } });

    expect(screen.getByLabelText("Target language code")).toHaveValue("");
  });

  it("restores a remembered common language as its friendly choice", async () => {
    window.localStorage.setItem("cueweaver.target-language", "zh-Hans");
    renderRoute("/translate");

    await selectExternalSubtitle();

    expect(screen.getByLabelText("Common target language")).toHaveValue("zh-Hans");
    expect(screen.queryByLabelText("Target language code")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Subtitle suffix")).toHaveValue("zh-Hans");
  });

  it("restores a remembered custom language on the custom path", async () => {
    window.localStorage.setItem("cueweaver.target-language", "x-custom");
    renderRoute("/translate");

    await selectExternalSubtitle();

    expect(screen.getByLabelText("Common target language")).toHaveValue("custom");
    expect(screen.getByLabelText("Target language code")).toHaveValue("x-custom");
  });

  it("explains the Directory default and Job-level Term map scopes", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();

    expect(
      screen.getByRole("region", { name: "Directory default" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Applies to Media beneath the current directory unless a Job overrides or disables it.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Term map for this translation" }),
    ).toHaveAttribute("aria-describedby", "term-map-policy-help");
    expect(
      screen.getByText(
        "Follow the Directory default, explicitly use no Term map, or choose a specific Term map for this translation.",
      ),
    ).toBeInTheDocument();
  });

  it("announces queueing while Job creation is pending and after success", async () => {
    renderRoute("/translate");
    await selectExternalSubtitle();
    await enterCustomTargetLanguage("zh-Hans");
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

  it("shows a skipped result without opening a Job", async () => {
    renderRoute("/translate");
    await selectExternalSubtitleWithLanguage();
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    const defaultImplementation = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((input, request) =>
      input === "/api/jobs" && request?.method === "POST"
        ? Promise.resolve(
            jsonResponse({
              status: "skipped",
              media_path: "Movie.mkv",
              output_path: "Movie.zh-Hans.srt",
              reason: "Output path already exists",
            }),
          )
        : defaultImplementation(input, request),
    );

    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    expect(
      await screen.findByRole("heading", { name: "Output already exists" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/No Job was created/)).toBeInTheDocument();
    expect(screen.getAllByText("Movie.zh-Hans.srt").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByRole("button", { name: "View Job" })).not.toBeInTheDocument();
  });

  it("shows skipped and queued items in ordered batch results", async () => {
    renderRoute(
      "/translate",
      true,
      BATCH_MEDIA,
      undefined,
      false,
      UNIQUE_BATCH_DISCOVERIES,
    );
    const fetchMock = mockBatchRequest({
      results: [
        {
          status: "skipped",
          media_path: "Movie.mkv",
          output_path: "Movie.zh-Hans.srt",
          reason: "Output path already exists",
        },
        { id: "job-2" },
      ],
    });

    await selectBatchMedia();
    await submitBatch();

    await expectBatchResultsStatus("1 Job queued · 1 item skipped.");
    const results = screen.getByLabelText("Batch submission results");
    expect(
      within(results).getByText("Skipped: Output path already exists"),
    ).toBeInTheDocument();
    expect(within(results).getByText("Queued as Job job-2")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/jobs/batch",
      expect.objectContaining({
        body: expect.stringContaining('"output_conflict_policy":"skip"'),
      }),
    );
  });

  it("shows queue details and opens the created Job", async () => {
    const job = queuedEmbeddedJob("queued-detail-1");
    renderRoute("/translate");
    await selectExternalSubtitle();
    await enterCustomTargetLanguage("zh-Hans");
    mockQueuedJobCreation(job);

    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    expect(
      await screen.findByRole("heading", { name: "Translation queued" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Movie.mkv")).toHaveLength(2);
    expect(screen.getByText("zh-Hans")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View Job" }));

    expect(
      await screen.findByRole("heading", { name: "Request summary" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Movie.mkv" })).toBeInTheDocument();
  });

  it("resets the translation workflow from queue success", async () => {
    const job = queuedEmbeddedJob("queued-reset-1");
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
    fireEvent.change(await screen.findByLabelText("Term map for this translation"), {
      target: { value: "map-1" },
    });
    fireEvent.click(screen.getByLabelText("Dynamic terminology"));
    fireEvent.click(screen.getByLabelText("Subtitle terminology filtering"));
    await enterCustomTargetLanguage("zh-Hans");
    mockQueuedJobCreation(job, [CHARACTERS_TERM_MAP]);

    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));
    expect(await screen.findByText("Translation queued")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Choose another Media" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Term map for this translation")).toHaveValue("map-1");
    expect(screen.getByLabelText("Dynamic terminology")).not.toBeChecked();
    expect(
      screen.queryByRole("button", { name: "Start translation" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Translate another" }));

    expect(screen.queryByText("Translation queued")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Select Movie.mkv" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Choose another Media" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Term map for this translation")).toHaveValue(
      "__directory_default__",
    );
    expect(screen.getByLabelText("Dynamic terminology")).toBeChecked();
    expect(screen.getByLabelText("Subtitle terminology filtering")).toBeChecked();
  });

  it("queues an Embedded subtitle with its stream index and format", async () => {
    renderRoute("/translate");

    await selectEmbeddedSubtitle();
    await enterCustomTargetLanguage("zh-Hans");
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
      const mediaResponse = singleExternalMediaResponse(input);
      if (mediaResponse) return mediaResponse;
      return jobListResponse([]);
    });
    renderWithFetch("/translate", fetchMock);
    await selectExternalSubtitle();
    expect(screen.getByText("Loading Term maps")).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Term map for this translation" }),
    ).toBeDisabled();
  });

  it("recovers the Term map control after its list request fails", async () => {
    let termMapCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/term-maps") {
        termMapCalls += 1;
        return termMapCalls === 1
          ? jsonResponse({ message: "Term maps unavailable" }, false)
          : jsonResponse({ term_maps: [CHARACTERS_TERM_MAP] });
      }
      const mediaResponse = singleExternalMediaResponse(input);
      if (mediaResponse) return mediaResponse;
      return jsonResponse({
        directory: "",
        local: null,
        effective: null,
        source_directory: null,
      });
    });
    renderWithFetch("/translate", fetchMock);

    await selectExternalSubtitle();
    expect(await screen.findByText("Term maps unavailable")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("option", { name: "Characters" }),
    ).toBeInTheDocument();
    const termMapSelect = screen.getByRole("combobox", {
      name: "Term map for this translation",
    });
    expect(termMapSelect).toBeEnabled();
    expect(termMapSelect).toHaveFocus();
  });

  it("recovers the Directory default after its binding request fails", async () => {
    let directoryCalls = 0;
    const fetchMock = vi.fn().mockImplementation(async (input: string) => {
      if (input === "/api/status") return statusResponse();
      if (input === "/api/term-maps") return jsonResponse({ term_maps: [] });
      if (input.startsWith("/api/term-maps/directory")) {
        directoryCalls += 1;
        return directoryCalls === 1
          ? jsonResponse({ message: "Directory binding unavailable" }, false)
          : jsonResponse({
              directory: "",
              local: null,
              effective: null,
              source_directory: null,
            });
      }
      if (input === "/api/media/browse") {
        return jsonResponse({
          path: "",
          entries: [{ kind: "media", name: "Movie.mkv", path: "Movie.mkv" }],
        });
      }
      return jsonResponse({
        path: "Movie.mkv",
        candidates: [],
        unsupported_candidates: [],
      });
    });
    renderWithFetch("/translate", fetchMock);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Directory binding unavailable",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("No default")).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Directory default" }),
    ).toBeInTheDocument();
  });

  it("retries a failed Directory default binding and restores focus", async () => {
    const { fetchMock, scenario } = createDirectoryMutationFetchMock(
      [CHARACTERS_TERM_MAP],
      {
        bind: (current, state) =>
          current.bindCalls === 1
            ? jsonResponse({ message: "Directory binding failed" }, false)
            : jsonResponse({
                ...state(),
                local: CHARACTERS_TERM_MAP,
                effective: CHARACTERS_TERM_MAP,
                source_directory: "",
              }),
      },
      null,
    );
    renderWithFetch("/translate", fetchMock);

    const directorySelect = await screen.findByRole("combobox", {
      name: "Directory default",
    });
    await screen.findByRole("option", { name: "Characters" });
    fireEvent.change(directorySelect, { target: { value: "map-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Bind Term map" }));
    await waitFor(() => expect(scenario.bindCalls).toBe(1));
    expect(await screen.findByText("Directory binding failed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(directorySelect).toHaveFocus());
    expect(scenario.bindCalls).toBe(2);
  });

  it("clears a failed binding when removing the existing Directory default", async () => {
    const settingsTermMap: TermMapSummary = {
      id: "map-settings",
      name: "Settings",
      entry_count: 1,
      updated_at: "2026-08-13T12:00:00Z",
    };
    const { fetchMock, scenario } = createDirectoryMutationFetchMock(
      [CHARACTERS_TERM_MAP, settingsTermMap],
      {
        bind: () => jsonResponse({ message: "Directory binding failed" }, false),
        remove: (current) => {
          current.localTermMap = null;
          return jsonResponse({
            directory: "",
            local: null,
            effective: null,
            source_directory: null,
          });
        },
      },
    );
    renderWithFetch("/translate", fetchMock);

    await selectDirectoryTermMap("map-settings", "Directory: Settings");
    fireEvent.click(screen.getByRole("button", { name: "Replace local binding" }));
    expect(await screen.findByText("Directory binding failed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove local binding" }));
    await waitFor(() => expect(scenario.removeCalls).toBe(1));
    await waitFor(() => expect(screen.getByText("No default")).toBeInTheDocument());

    expect(scenario.bindCalls).toBe(1);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("clears a failed removal when replacing the Directory default", async () => {
    const settingsTermMap: TermMapSummary = {
      id: "map-settings",
      name: "Settings",
      entry_count: 1,
      updated_at: "2026-08-13T12:00:00Z",
    };
    const { fetchMock, scenario } = createDirectoryMutationFetchMock(
      [CHARACTERS_TERM_MAP, settingsTermMap],
      {
        bind: (current, state) => {
          current.localTermMap = settingsTermMap;
          return jsonResponse(state());
        },
        remove: (current, state) =>
          current.removeCalls === 1
            ? jsonResponse({ message: "Directory removal failed" }, false)
            : jsonResponse(state()),
      },
    );
    renderWithFetch("/translate", fetchMock);

    const directorySelect = await selectDirectoryTermMap(
      "map-settings",
      "Directory: Settings",
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove local binding" }));
    expect(await screen.findByText("Directory removal failed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Replace local binding" }));
    await waitFor(() => expect(scenario.bindCalls).toBe(1));
    await waitFor(() => expect(directorySelect).toHaveValue("map-settings"));

    expect(scenario.removeCalls).toBe(1);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
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
    const termMap = screen.getByLabelText("Term map for this translation");
    expect(
      screen.getByRole("option", { name: "No Term map for this Job" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Characters" })).toBeInTheDocument();
    fireEvent.change(termMap, { target: { value: "map-1" } });
    await enterCustomTargetLanguage("zh-Hans");
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await expectQueuedJobRequest("zh-Hans", "map-1", true, true);
  });

  it("submits an explicit no-map choice for only the current Job", async () => {
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
    fireEvent.change(screen.getByLabelText("Term map for this translation"), {
      target: { value: "" },
    });
    await enterCustomTargetLanguage("zh-Hans");
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));

    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/jobs",
        expect.objectContaining({
          body: expect.stringContaining('"term_map_mode":"none"'),
        }),
      ),
    );
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/jobs",
      expect.objectContaining({
        body: expect.stringContaining('"term_map_id":null'),
      }),
    );
  });

  it("resets a one-off Term map choice when changing directories", async () => {
    renderRoute(
      "/translate",
      true,
      undefined,
      undefined,
      false,
      [],
      [CHARACTERS_TERM_MAP],
    );

    await screen.findByRole("option", { name: "Characters" });
    const termMap = screen.getByLabelText("Term map for this translation");
    await waitFor(() => expect(termMap).toBeEnabled());
    fireEvent.change(termMap, {
      target: { value: "map-1" },
    });
    expect(termMap).toHaveValue("map-1");
    fireEvent.click(await screen.findByRole("button", { name: "Open Series" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Term map for this translation")).toHaveValue(
        "__directory_default__",
      ),
    );
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
    const termMap = screen.getByLabelText("Term map for this translation");
    fireEvent.change(termMap, { target: { value: "map-1" } });
    expect(termMap).toHaveValue("map-1");

    termMaps.length = 0;
    await queryClient.invalidateQueries({ queryKey: ["term-maps"] });

    await waitFor(() => expect(termMap).toHaveValue(""));
  });

  it("composes a safe output name and supports atomic overwrite", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    await enterCustomTargetLanguage("zh-Hans");
    expect(screen.getByLabelText("Subtitle suffix")).toHaveValue("zh-Hans");
    expect(
      screen.getByText(
        (_, element) => element?.textContent === "Output filename: Movie.zh-Hans.srt",
      ),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Subtitle suffix"), {
      target: { value: "zh-Hans.forced" },
    });
    expect(
      screen.getByText(
        (_, element) =>
          element?.textContent === "Output filename: Movie.zh-Hans.forced.srt",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Overwrite existing output"));
    expect(screen.queryByText("(No Job if output exists)")).not.toBeInTheDocument();
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

    await selectExternalSubtitleWithLanguage();
    fireEvent.change(screen.getByLabelText("Subtitle suffix"), {
      target: { value: "CON" },
    });

    expect(screen.getByRole("alert")).toHaveTextContent("reserved");
    expectJobSubmissionBlocked();
  });

  it("remembers a successful language without resetting the source form", async () => {
    renderRoute("/translate");

    await selectExternalSubtitle();
    await enterCustomTargetLanguage("x-custom");
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
    ).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/api/jobs",
      expect.objectContaining({
        body: JSON.stringify({
          media_path: "Movie.mkv",
          subtitle_path: "Movie.en.srt",
          target_language_code: "x-custom",
          output_suffix: "x-custom",
          output_conflict_policy: "skip",
          term_map_mode: "follow",
          term_map_id: null,
          dynamic_terminology_enabled: false,
          subtitle_terminology_filter_enabled: true,
        }),
      }),
    );
  });

  it("allows Skip submission when the Translation provider is unavailable", async () => {
    renderRoute("/translate", false);

    await selectExternalSubtitle();
    await enterCustomTargetLanguage("zh-Hans");

    expect(screen.getByRole("button", { name: "Start translation" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Start translation" }));
    await waitFor(() =>
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/jobs",
        expect.objectContaining({
          body: expect.stringContaining('"output_conflict_policy":"skip"'),
        }),
      ),
    );
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

  it("shows an episode NFO label without replacing the real filename", async () => {
    renderRoute("/translate", true, {
      path: "Shows/Season 1",
      entries: [
        {
          kind: "media",
          name: "Show - S01E02.mkv",
          path: "Shows/Season 1/Show - S01E02.mkv",
          title: "The second episode",
          season: 1,
          episode: 2,
        },
      ],
    });

    expect(
      await screen.findByRole("button", {
        name: "Select S01E02 · The second episode (Show - S01E02.mkv)",
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
