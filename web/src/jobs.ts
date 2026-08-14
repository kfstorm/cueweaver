import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

export interface Job {
  id: string;
  attempt: number;
  status:
    "Queued" | "Extracting" | "Translating" | "Completed" | "Failed" | "Interrupted";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  queue_position: number | null;
  request: {
    media_path: string;
    subtitle_path?: string;
    stream_index?: number;
    target_language_code: string;
    term_map: {
      id: string;
      name: string;
      content: Record<string, string>;
    } | null;
    dynamic_terminology_enabled: boolean;
    subtitle_terminology_filter_enabled: boolean;
    output_suffix: string;
    output_conflict_policy: "append-number" | "overwrite";
    output_path: string;
    source_format: string;
  };
  error: { code: string; message: string; [key: string]: unknown } | null;
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

export function useJobs() {
  const query = useQuery({
    queryKey: ["jobs"],
    queryFn: async (): Promise<Job[]> => {
      const response = await fetch("/api/jobs");
      const body = (await response.json()) as { jobs?: Job[]; message?: string };
      if (!response.ok) throw new Error(body.message ?? "Jobs could not be loaded.");
      return body.jobs ?? [];
    },
    staleTime: 0,
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });

  const refetch = query.refetch;
  useEffect(() => {
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refetch();
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => document.removeEventListener("visibilitychange", refreshWhenVisible);
  }, [refetch]);

  return query;
}

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["job", jobId],
    enabled: jobId !== null,
    retry: false,
    queryFn: async (): Promise<Job> => {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobId!)}`);
      const body = (await response.json()) as Job & { message?: string };
      if (!response.ok)
        throw new Error(body.message ?? "Job details could not be loaded.");
      return body;
    },
    staleTime: 0,
  });
}

export function useJobNotifications(jobs: Job[] | undefined): {
  notifications: JobNotification[];
  dismiss: (id: string) => void;
} {
  const previousStatuses = useRef<Map<string, Job["status"]> | null>(null);
  const [notifications, setNotifications] = useState<JobNotification[]>([]);

  useEffect(() => {
    if (jobs === undefined) return;
    const currentStatuses = new Map(jobs.map((job) => [job.id, job.status]));
    const previous = previousStatuses.current;
    if (previous !== null) {
      const observed: JobNotification[] = [];
      for (const job of jobs) {
        if (
          previous.get(job.id) !== job.status &&
          (job.status === "Completed" || job.status === "Failed")
        ) {
          const media =
            job.request.media_path.split("/").pop() ?? job.request.media_path;
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
      }
      if (observed.length > 0) {
        // The query is the external source; this state is the in-app notification queue.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setNotifications((current) => [...current, ...observed].slice(-4));
      }
    }
    previousStatuses.current = currentStatuses;
  }, [jobs]);

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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}
