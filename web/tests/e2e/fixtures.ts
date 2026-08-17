export type E2EJobStatus = "Queued" | "Completed" | "Failed" | "Interrupted";

export function jobRecord(id: string, status: E2EJobStatus = "Completed") {
  return {
    id,
    attempt: 1,
    status,
    created_at: "2026-08-13T12:00:00Z",
    started_at: "2026-08-13T12:00:01Z",
    finished_at: status === "Queued" ? null : "2026-08-13T12:00:02Z",
    queue_position: status === "Queued" ? 1 : null,
    request: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "zh-Hans",
      term_map_mode: "follow",
      term_map: null,
      dynamic_terminology_enabled: true,
      subtitle_terminology_filter_enabled: true,
      output_suffix: "zh-Hans",
      output_conflict_policy: "append-number",
      output_path: "Example.zh-Hans.srt",
      source_format: "srt",
    },
    error:
      status === "Failed"
        ? { code: "translation_failed", message: "Translation failed" }
        : status === "Interrupted"
          ? { code: "job_interrupted", message: "Job was interrupted" }
          : null,
  };
}
