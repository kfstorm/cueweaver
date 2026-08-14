import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

export function useJobs() {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: async (): Promise<Job[]> => {
      const response = await fetch("/api/jobs");
      const body = (await response.json()) as { jobs?: Job[]; message?: string };
      if (!response.ok) throw new Error(body.message ?? "Jobs could not be loaded.");
      return body.jobs ?? [];
    },
    refetchInterval: 1000,
  });
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
