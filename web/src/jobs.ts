import {
  useInfiniteQuery,
  useIsFetching,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

const HISTORY_QUERY_KEY = ["jobs", "history"] as const;
const HISTORY_REFRESH_QUERY_KEY = ["jobs", "history-refresh"] as const;

function updateJobAfterMutation(queryClient: QueryClient, job: Job, jobId: string) {
  queryClient.setQueryData(["job", jobId], job);
  void queryClient.invalidateQueries({ queryKey: ["jobs"] });
}

function uniqueJobs(jobs: Job[]): Job[] {
  const seen = new Set<string>();
  return jobs.filter((job) => {
    if (seen.has(job.id)) return false;
    seen.add(job.id);
    return true;
  });
}

function reconcileJobLists(activeJobs: Job[], historyJobs: Job[]): JobListData {
  const uniqueActive = uniqueJobs(activeJobs);
  const uniqueHistory = uniqueJobs(historyJobs);
  const activeById = new Map(uniqueActive.map((job) => [job.id, job]));
  const historyById = new Map(uniqueHistory.map((job) => [job.id, job]));
  return {
    active_jobs: uniqueActive.filter((job) => {
      const historyJob = historyById.get(job.id);
      return historyJob === undefined || job.attempt > historyJob.attempt;
    }),
    history_jobs: uniqueHistory.filter((job) => {
      const activeJob = activeById.get(job.id);
      return activeJob === undefined || activeJob.attempt <= job.attempt;
    }),
    next_cursor: null,
  };
}

export interface Job {
  id: string;
  attempt: number;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  status_history?: JobStatusHistoryEntry[];
  queue_position: number | null;
  request: {
    media_path: string;
    subtitle_path?: string;
    stream_index?: number;
    target_language_code: string;
    term_map_mode: TermMapMode;
    term_map: {
      id: string;
      name: string;
    } | null;
    output_path: string;
    source_format: string;
    dynamic_terminology_enabled?: boolean;
    subtitle_terminology_filter_enabled?: boolean;
    output_suffix?: string;
    output_conflict_policy?: OutputConflictPolicy;
  };
  error: { code: string; message: string; [key: string]: unknown } | null;
}

export type JobStatus =
  | "Queued"
  | "Extracting"
  | "Translating"
  | "Completed"
  | "Failed"
  | "Interrupted"
  | "Cancelled";

export type JobStatusFilter = "all" | JobStatus;

export type OutputConflictPolicy = "append-number" | "overwrite" | "skip";

export interface SkippedJobResult {
  status: "skipped";
  media_path: string;
  output_path: string;
  reason: string;
}

export type JobCreationResult = Job | SkippedJobResult;

export interface JobStatusHistoryEntry {
  status: Job["status"];
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobListPage {
  active_jobs: Job[];
  history_jobs: Job[];
  next_cursor: string | null;
  matching_count?: number;
  completed_count?: number;
}

export type TermMapMode = "follow" | "selected" | "none";

export type JobListData = JobListPage;

export interface BatchJobError {
  error_code: string;
  message: string;
  [key: string]: unknown;
}

export type BatchJobSuccess = Pick<Job, "id">;
export type BatchJobSkipped = SkippedJobResult;
export type BatchJobResult = BatchJobSuccess | BatchJobSkipped | BatchJobError;

function isBatchJobResult(value: unknown): value is BatchJobResult {
  if (typeof value !== "object" || value === null) return false;
  const result = value as Record<string, unknown>;
  if (typeof result.error_code === "string") {
    return typeof result.message === "string";
  }
  if (isSkippedJobResult(result)) return true;
  return typeof result.id === "string";
}

export function isSkippedJobResult(value: unknown): value is SkippedJobResult {
  if (typeof value !== "object" || value === null) return false;
  const result = value as Record<string, unknown>;
  return (
    result.status === "skipped" &&
    typeof result.media_path === "string" &&
    typeof result.output_path === "string" &&
    typeof result.reason === "string"
  );
}

export const APPROVED_ERROR_CONTEXT_KEYS = [
  "field",
  "media_path",
  "output_path",
  "path",
  "stream_index",
] as const;

export interface JobNotification {
  id: string;
  jobId: string;
  status: "Completed" | "Failed";
  message: string;
}

export interface JobCleanupFailure {
  id: string;
  error_code: string;
  message: string;
  [key: string]: unknown;
}

export interface ClearCompletedJobsResult {
  deleted: string[];
  failed: JobCleanupFailure[];
}

export function useJobs({
  poll = true,
  search = "",
  status = "all",
}: { poll?: boolean; search?: string; status?: JobStatusFilter } = {}) {
  const condition = `search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}`;
  const conditionSuffix = search || status !== "all" ? `?${condition}` : "";
  const activeQuery = useQuery({
    queryKey: ["jobs", "active", condition],
    queryFn: ({ signal }) =>
      fetchJobsPage(
        `/api/jobs?limit=1${conditionSuffix ? `&${condition}` : ""}`,
        signal,
      ),
    staleTime: 0,
    refetchInterval: poll ? 2000 : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
  const historyQuery = useInfiniteQuery({
    queryKey: [...HISTORY_QUERY_KEY, search, status],
    enabled: activeQuery.isSuccess,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      fetchJobsPage(
        pageParam
          ? `/api/jobs?limit=50&cursor=${encodeURIComponent(pageParam)}${
              conditionSuffix ? `&${condition}` : ""
            }`
          : `/api/jobs${conditionSuffix}`,
        signal,
      ),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const queryClient = useQueryClient();
  const previousActiveStatuses = useRef<Map<string, Job["status"]> | null>(null);
  const historyRefreshCount = useIsFetching({ queryKey: HISTORY_REFRESH_QUERY_KEY });

  useEffect(() => {
    if (!poll) return;
    const activeJobs = activeQuery.data?.active_jobs ?? [];
    const currentStatuses = new Map(activeJobs.map((job) => [job.id, job.status]));
    const previousStatuses = previousActiveStatuses.current;
    previousActiveStatuses.current = currentStatuses;
    if (previousStatuses === null) return;
    const completedJobLeftActive = [...previousStatuses.keys()].some(
      (jobId) => !currentStatuses.has(jobId),
    );
    if (!completedJobLeftActive) return;

    const refreshVersionKey = ["jobs", "history-refresh-version", search, status];
    const refreshVersion =
      (queryClient.getQueryData<number>(refreshVersionKey) ?? 0) + 1;
    queryClient.setQueryData(refreshVersionKey, refreshVersion);
    void queryClient
      .cancelQueries({ queryKey: HISTORY_QUERY_KEY })
      .then(() => queryClient.cancelQueries({ queryKey: HISTORY_REFRESH_QUERY_KEY }))
      .then(() =>
        queryClient.fetchQuery({
          queryKey: [...HISTORY_REFRESH_QUERY_KEY, search, status, refreshVersion],
          queryFn: ({ signal }) => fetchJobsPage(`/api/jobs${conditionSuffix}`, signal),
        }),
      )
      .then((page) => {
        if (queryClient.getQueryData<number>(refreshVersionKey) !== refreshVersion) {
          return;
        }
        queryClient.setQueryData<InfiniteData<JobListPage, string | null>>(
          [...HISTORY_QUERY_KEY, search, status],
          (current) =>
            current
              ? { ...current, pages: [page], pageParams: [null] }
              : { pages: [page], pageParams: [null] },
        );
      })
      .catch(() => {
        if (queryClient.getQueryData<number>(refreshVersionKey) === refreshVersion) {
          previousActiveStatuses.current = previousStatuses;
        }
      });
  }, [activeQuery.data, conditionSuffix, poll, queryClient, search, status]);

  const refetch = async () => {
    const activeResult = await activeQuery.refetch();
    if (activeResult.isSuccess) await historyQuery.refetch();
    return activeResult;
  };
  const refetchActive = activeQuery.refetch;
  useEffect(() => {
    if (!poll) return;
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refetchActive();
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => document.removeEventListener("visibilitychange", refreshWhenVisible);
  }, [poll, refetchActive]);

  const pages = historyQuery.data?.pages;
  const data: JobListData | undefined = pages
    ? {
        ...reconcileJobLists(
          activeQuery.data?.active_jobs ?? [],
          pages.flatMap((page) => page.history_jobs),
        ),
        next_cursor: pages.at(-1)?.next_cursor ?? null,
        matching_count: pages[0]?.matching_count,
        completed_count: pages[0]?.completed_count,
      }
    : undefined;

  return {
    ...historyQuery,
    data,
    isHistoryRefreshing: historyRefreshCount > 0,
    error: activeQuery.error ?? historyQuery.error,
    isError: activeQuery.isError || historyQuery.isError,
    isFetching: activeQuery.isFetching || historyQuery.isFetching,
    isPending: activeQuery.isPending || historyQuery.isPending,
    refetch,
  };
}

async function fetchJobsPage(path: string, signal?: AbortSignal): Promise<JobListPage> {
  const response = await fetch(path, signal === undefined ? undefined : { signal });
  const body = (await response.json()) as Partial<JobListPage> & {
    message?: string;
  };
  if (!response.ok) throw new Error(body.message ?? "Jobs could not be loaded.");
  if (
    !Array.isArray(body.active_jobs) ||
    !Array.isArray(body.history_jobs) ||
    !(
      body.next_cursor === null ||
      (typeof body.next_cursor === "string" && body.next_cursor.length > 0)
    )
  ) {
    throw new Error("Jobs response has an invalid shape.");
  }
  return {
    active_jobs: body.active_jobs,
    history_jobs: body.history_jobs,
    next_cursor: body.next_cursor,
    matching_count:
      typeof body.matching_count === "number" && body.matching_count >= 0
        ? body.matching_count
        : undefined,
    completed_count:
      typeof body.completed_count === "number" && body.completed_count >= 0
        ? body.completed_count
        : undefined,
  };
}

export function useJob(jobId: string | null, enabled = jobId !== null) {
  return useQuery({
    queryKey: ["job", jobId],
    enabled: enabled && jobId !== null,
    retry: false,
    queryFn: async ({ signal }): Promise<Job> => {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId!)}`, {
        signal,
      });
      const body = (await response.json()) as Job & { message?: string };
      if (!response.ok)
        throw new Error(body.message ?? "Job details could not be loaded.");
      return body;
    },
    staleTime: 0,
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
}

export function useJobNotifications(data: JobListData | undefined): {
  notifications: JobNotification[];
  dismiss: (id: string) => void;
} {
  const lastKnownStatuses = useRef(new Map<string, Job["status"]>());
  const [notifications, setNotifications] = useState<JobNotification[]>([]);

  useEffect(() => {
    if (data === undefined) return;
    const jobs = [...data.active_jobs, ...data.history_jobs];
    const observed: JobNotification[] = [];
    for (const job of jobs) {
      const previousStatus = lastKnownStatuses.current.get(job.id);
      if (
        previousStatus !== undefined &&
        previousStatus !== job.status &&
        (job.status === "Completed" || job.status === "Failed")
      ) {
        const media = job.request.media_path.split("/").pop() ?? job.request.media_path;
        observed.push({
          id: `${job.id}-${job.status}-${job.finished_at ?? Date.now()}`,
          jobId: job.id,
          status: job.status,
          message:
            job.status === "Completed"
              ? `${media} translation completed.`
              : `${media} translation failed: ${job.error?.message ?? "Check Job details."}`,
        });
      }
      lastKnownStatuses.current.set(job.id, job.status);
    }
    if (observed.length > 0) {
      // The query is the external source; this state is the in-app notification queue.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNotifications((current) => [...current, ...observed].slice(-4));
    }
  }, [data]);

  const dismiss = useCallback(
    (id: string) =>
      setNotifications((current) =>
        current.filter((notification) => notification.id !== id),
      ),
    [],
  );

  return { notifications, dismiss };
}

export function useCreateJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: {
      media_path: string;
      subtitle_path?: string;
      target_language_code: string;
      term_map_mode: TermMapMode;
      term_map_id: string | null;
      dynamic_terminology_enabled: boolean;
      subtitle_terminology_filter_enabled: boolean;
      output_suffix: string;
      output_conflict_policy: OutputConflictPolicy;
      stream_index?: number;
      source_format?: string;
    }): Promise<JobCreationResult> => {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
      const body = (await response.json()) as Job & { message?: string };
      if (!response.ok)
        throw new Error(body.message ?? "Translation could not be queued.");
      return body;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCreateBatchJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: {
      items: Array<{
        media_path: string;
        subtitle_path?: string;
        stream_index?: number;
        source_format?: string;
      }>;
      target_language_code: string;
      term_map_mode: TermMapMode;
      term_map_id: string | null;
      dynamic_terminology_enabled: boolean;
      subtitle_terminology_filter_enabled: boolean;
      output_suffix: string;
      output_conflict_policy: OutputConflictPolicy;
    }): Promise<BatchJobResult[]> => {
      const response = await fetch("/api/jobs/batch", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
      const body = (await response.json()) as {
        results?: BatchJobResult[];
        message?: string;
      };
      if (!response.ok)
        throw new Error(body.message ?? "Translations could not be queued.");
      if (
        !Array.isArray(body.results) ||
        body.results.length !== request.items.length ||
        !body.results.every(isBatchJobResult)
      )
        throw new Error("Batch response has an invalid shape.");
      return body.results;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string): Promise<Job> => {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
        method: "POST",
      });
      const body = (await response.json()) as Job & { message?: string };
      if (!response.ok) throw new Error(body.message ?? "Job could not be retried.");
      return body;
    },
    onSuccess: (job, jobId) => updateJobAfterMutation(queryClient, job, jobId),
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string): Promise<Job> => {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
        method: "POST",
      });
      const body = (await response.json()) as Job & { message?: string };
      if (!response.ok) throw new Error(body.message ?? "Job could not be cancelled.");
      return body;
    },
    onSuccess: (job, jobId) => updateJobAfterMutation(queryClient, job, jobId),
  });
}

export function useDeleteJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string): Promise<{ id: string; deleted: boolean }> => {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
        method: "DELETE",
      });
      const body = (await response.json()) as {
        id: string;
        deleted: boolean;
        message?: string;
      };
      if (!response.ok) throw new Error(body.message ?? "Job could not be deleted.");
      return body;
    },
    onSuccess: (_result, jobId) => {
      queryClient.removeQueries({ queryKey: ["job", jobId] });
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useClearCompletedJobs() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<ClearCompletedJobsResult> => {
      const response = await fetch("/api/jobs/completed", { method: "DELETE" });
      const body = (await response.json()) as ClearCompletedJobsResult & {
        message?: string;
      };
      if (!response.ok)
        throw new Error(body.message ?? "Completed Jobs could not be cleared.");
      return body;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
