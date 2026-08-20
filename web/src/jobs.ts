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

export interface Job {
  id: string;
  attempt: number;
  status:
    | "Queued"
    | "Extracting"
    | "Translating"
    | "Completed"
    | "Failed"
    | "Interrupted"
    | "Cancelled";
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
    output_conflict_policy?: "append-number" | "overwrite";
  };
  error: { code: string; message: string; [key: string]: unknown } | null;
}

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
}

export type TermMapMode = "follow" | "selected" | "none";

export type JobListData = JobListPage;

export interface BatchJobError {
  error_code: string;
  message: string;
  [key: string]: unknown;
}

export type BatchJobResult = Job | BatchJobError;

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

export function useJobs({ poll = true }: { poll?: boolean } = {}) {
  const activeQuery = useQuery({
    queryKey: ["jobs", "active"],
    queryFn: ({ signal }) => fetchJobsPage("/api/jobs?limit=1", signal),
    staleTime: 0,
    refetchInterval: poll ? 2000 : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });
  const historyQuery = useInfiniteQuery({
    queryKey: HISTORY_QUERY_KEY,
    enabled: activeQuery.isSuccess,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      fetchJobsPage(
        pageParam
          ? `/api/jobs?limit=50&cursor=${encodeURIComponent(pageParam)}`
          : "/api/jobs",
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

    const refreshVersionKey = ["jobs", "history-refresh-version"];
    const refreshVersion =
      (queryClient.getQueryData<number>(refreshVersionKey) ?? 0) + 1;
    queryClient.setQueryData(refreshVersionKey, refreshVersion);
    void queryClient
      .cancelQueries({ queryKey: HISTORY_QUERY_KEY })
      .then(() => queryClient.cancelQueries({ queryKey: HISTORY_REFRESH_QUERY_KEY }))
      .then(() =>
        queryClient.fetchQuery({
          queryKey: [...HISTORY_REFRESH_QUERY_KEY, refreshVersion],
          queryFn: ({ signal }) => fetchJobsPage("/api/jobs", signal),
        }),
      )
      .then((page) => {
        if (queryClient.getQueryData<number>(refreshVersionKey) !== refreshVersion) {
          return;
        }
        queryClient.setQueryData<InfiniteData<JobListPage, string | null>>(
          HISTORY_QUERY_KEY,
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
  }, [activeQuery.data, poll, queryClient]);

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
        active_jobs: activeQuery.data?.active_jobs ?? [],
        history_jobs: pages.flatMap((page) => page.history_jobs),
        next_cursor: pages.at(-1)?.next_cursor ?? null,
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
      output_conflict_policy: "append-number" | "overwrite";
      stream_index?: number;
      source_format?: string;
    }): Promise<Job> => {
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
      output_conflict_policy: "append-number" | "overwrite";
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
      if (!Array.isArray(body.results))
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
