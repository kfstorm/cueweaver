import {
  ArrowLeftIcon,
  BriefcaseIcon,
  CheckCircleIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { useEffect, useRef, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "./components/ui/button";
import {
  APPROVED_ERROR_CONTEXT_KEYS,
  useClearCompletedJobs,
  useCancelJob,
  useDeleteJob,
  useJob,
  useJobs,
  useRetryJob,
  type Job,
  type JobNotification,
  type JobStatusHistoryEntry,
} from "./jobs";
import { cn, formatLocalTimestamp, formatRelativeTimestamp } from "./lib/utils";
import { useProductStatus } from "./status";

const RUNNING_JOB_MESSAGE = "Running Jobs cannot be cancelled.";

export function JobNotificationRegion({
  notifications,
  dismiss,
}: {
  notifications: JobNotification[];
  dismiss: (id: string) => void;
}) {
  return (
    <aside className="toast-region" aria-label="Job notifications">
      {notifications.map((notification) => (
        <JobToast key={notification.id} notification={notification} dismiss={dismiss} />
      ))}
    </aside>
  );
}

function JobToast({
  notification,
  dismiss,
}: {
  notification: JobNotification;
  dismiss: (id: string) => void;
}) {
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(notification.id), 7000);
    return () => window.clearTimeout(timer);
  }, [dismiss, notification.id]);

  return (
    <div
      className={cn("toast", notification.status === "Failed" && "toast-error")}
      role={notification.status === "Failed" ? "alert" : "status"}
    >
      {notification.status === "Completed" ? (
        <CheckCircleIcon size={18} weight="fill" aria-hidden="true" />
      ) : (
        <WarningCircleIcon size={18} aria-hidden="true" />
      )}
      <span>{notification.message}</span>
      <button
        type="button"
        onClick={() => dismiss(notification.id)}
        aria-label="Dismiss notification"
      >
        Dismiss
      </button>
    </div>
  );
}

export function JobsPage() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const listTitleRef = useRef<HTMLHeadingElement>(null);
  const focusListOnReturn = useRef(false);
  const jobs = useJobs({ poll: false });
  const status = useProductStatus();
  const clearCompleted = useClearCompletedJobs();
  const activeJobs = jobs.data?.active_jobs ?? [];
  const historyJobs = jobs.data?.history_jobs ?? [];
  const allJobs = [...activeJobs, ...historyJobs];
  const selected = allJobs.find((job) => job.id === jobId) ?? null;
  const detail = useJob(jobId ?? null, jobId !== undefined);
  const displayedJob = detail.data ?? selected;
  const completedCount = historyJobs.filter((job) => job.status === "Completed").length;
  const completedCountKnown = !jobs.hasNextPage;
  const canClearCompleted =
    completedCount > 0 || (jobs.hasNextPage && historyJobs.length > 0);
  const clearCompletedLabel = completedCountKnown
    ? `Clear Completed (${completedCount})`
    : "Clear Completed";
  const recordHealth = status.data?.job_records;
  const recordAttention =
    (recordHealth?.corrupt.count ?? 0) + (recordHealth?.unsupported.count ?? 0) > 0;

  const clearCompletedJobs = () => {
    const prompt = completedCountKnown
      ? `Clear ${completedCount} completed Job${completedCount === 1 ? "" : "s"}? This removes their history and residual Work data.`
      : "Clear all completed Jobs? This removes their history and residual Work data.";
    if (!window.confirm(prompt)) {
      return;
    }
    clearCompleted.mutate(undefined, {
      onSuccess: (result) => {
        if (jobId && result.deleted.includes(jobId)) navigateToJobList();
      },
    });
  };

  const navigateToJobList = () => {
    focusListOnReturn.current = true;
    navigate("/jobs");
  };

  const returnToJobList = () => {
    clearCompleted.reset();
    navigateToJobList();
  };

  useEffect(() => {
    if (!jobId && focusListOnReturn.current) {
      focusListOnReturn.current = false;
      listTitleRef.current?.focus();
    }
  }, [jobId]);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Jobs</h1>
          <p>Review durable translation history, diagnostics, and retryable work.</p>
        </div>
      </header>
      {recordAttention && recordHealth && <RecordHealthNotice health={recordHealth} />}
      <div className={cn("job-layout", jobId && "has-selection")}>
        <section className="job-list-panel" aria-labelledby="job-list-title">
          <div className="section-heading job-list-heading">
            <div>
              <p className="eyebrow">History</p>
              <h2 id="job-list-title" ref={listTitleRef} tabIndex={-1}>
                All Jobs
              </h2>
            </div>
            <div className="job-list-actions">
              {jobs.data && (
                <span className="count-badge">{allJobs.length} loaded</span>
              )}
              <Button
                variant="outline"
                type="button"
                disabled={!canClearCompleted || clearCompleted.isPending}
                onClick={clearCompletedJobs}
              >
                {clearCompleted.isPending ? "Clearing..." : clearCompletedLabel}
              </Button>
            </div>
          </div>
          {clearCompleted.isError && (
            <p className="form-error" role="alert">
              {clearCompleted.error.message}
            </p>
          )}
          {clearCompleted.data && clearCompleted.data.failed.length > 0 && (
            <div className="form-error" role="alert">
              <p>Some Completed Jobs could not be cleared.</p>
              <ul>
                {clearCompleted.data.failed.map((failure) => (
                  <li key={failure.id}>
                    Job {failure.id.slice(0, 8)}: {failure.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div
            className={cn(
              "job-list-state",
              (jobs.isPending || jobs.isError || allJobs.length === 0) && "has-state",
            )}
          >
            {jobs.isPending && (
              <div className="inline-state" role="status">
                Loading Jobs
              </div>
            )}
            {jobs.isError && (
              <div className="inline-state error" role="alert">
                {jobs.error?.message ?? "Jobs could not be loaded."}
                <Button variant="outline" onClick={() => void jobs.refetch()}>
                  Try again
                </Button>
              </div>
            )}
            {!jobs.isPending && !jobs.isError && allJobs.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon">
                  <BriefcaseIcon size={22} aria-hidden="true" />
                </span>
                <h2>No Jobs yet</h2>
                <p>Submitted translations will appear here with their current state.</p>
              </div>
            )}
          </div>
          {activeJobs.length > 0 && (
            <section aria-labelledby="active-jobs-title">
              <h3 id="active-jobs-title">Active Jobs</h3>
              <JobList jobs={activeJobs} selectedId={jobId} onSelect={navigate} />
            </section>
          )}
          {historyJobs.length > 0 && (
            <section aria-labelledby="history-jobs-title">
              <h3 id="history-jobs-title">History</h3>
              <JobList jobs={historyJobs} selectedId={jobId} onSelect={navigate} />
            </section>
          )}
          {jobs.hasNextPage && (
            <Button
              variant="outline"
              type="button"
              disabled={jobs.isFetchingNextPage || jobs.isHistoryRefreshing}
              onClick={() => void jobs.fetchNextPage()}
            >
              {jobs.isHistoryRefreshing
                ? "Refreshing history..."
                : jobs.isFetchingNextPage
                  ? "Loading history..."
                  : "Load more history"}
            </Button>
          )}
        </section>
        <section className="job-detail" aria-label="Job details">
          {!jobId && <SelectionPrompt />}
          {jobId && !selected && detail.isPending && (
            <DetailState>Loading Job details</DetailState>
          )}
          {jobId && detail.isError && (
            <DetailState error>
              {detail.error.message === "Job does not exist"
                ? "This Job is no longer available."
                : detail.error.message}
              <Button variant="outline" onClick={() => navigate("/jobs")}>
                Back to Jobs
              </Button>
            </DetailState>
          )}
          {jobId &&
            displayedJob &&
            !detail.isError &&
            (selected || !detail.isPending) && (
              <JobDetail
                job={displayedJob}
                onBack={() => navigate("/jobs")}
                onDeleted={returnToJobList}
              />
            )}
        </section>
      </div>
    </>
  );
}

function RecordHealthNotice({
  health,
}: {
  health: NonNullable<ReturnType<typeof useProductStatus>["data"]>["job_records"];
}) {
  if (!health) return null;
  const entries = [
    ["Corrupt", health.corrupt],
    ["Unsupported", health.unsupported],
  ] as const;
  return (
    <section className="record-health-notice" aria-labelledby="record-health-title">
      <div>
        <p className="eyebrow">Persistence warning</p>
        <h2 id="record-health-title">Job records need attention</h2>
        <p>These records were kept out of active history and need operator review.</p>
      </div>
      <dl>
        {entries.map(([label, record]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {record.count} {record.count === 1 ? "record" : "records"} in{" "}
              <code>{record.location}</code>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function JobList({
  jobs,
  selectedId,
  onSelect,
}: {
  jobs: Job[];
  selectedId: string | undefined;
  onSelect: (path: string) => void;
}) {
  return (
    <div className="job-list" role="list" aria-label="Translation Jobs">
      {jobs.map((job) => (
        <JobListItem
          key={job.id}
          job={job}
          selected={job.id === selectedId}
          onSelect={() => onSelect(`/jobs/${encodeURIComponent(job.id)}`)}
        />
      ))}
    </div>
  );
}

function JobListItem({
  job,
  selected,
  onSelect,
}: {
  job: Job;
  selected: boolean;
  onSelect: () => void;
}) {
  const cancelJob = useCancelJob();

  return (
    <article className={cn("job-item", selected && "selected")} role="listitem">
      <button
        type="button"
        className="job-item-select"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
      >
        <strong title={job.request.media_path}>{job.request.media_path}</strong>
        <span className="job-source">
          {sourceSummary(job)} to {job.request.target_language_code}
        </span>
        <time dateTime={job.created_at} title={formatLocalTimestamp(job.created_at)}>
          Created {formatRelativeTimestamp(job.created_at)}
        </time>
        <span className="job-id">Job {job.id.slice(0, 8)}</span>
      </button>
      <JobStatus status={job.status} />
      {job.queue_position !== null && job.queue_position !== undefined && (
        <span className="job-queue">Queue position {job.queue_position}</span>
      )}
      {job.request.term_map && (
        <span className="job-queue">Term map: {job.request.term_map.name}</span>
      )}
      {isRunningJob(job.status) && (
        <span className="job-action-note">{RUNNING_JOB_MESSAGE}</span>
      )}
      {job.status === "Queued" && !selected && (
        <>
          <Button
            type="button"
            variant="outline"
            disabled={cancelJob.isPending}
            onClick={() => {
              if (confirmCancelJob(job.id)) cancelJob.mutate(job.id);
            }}
          >
            {cancelJob.isPending ? "Cancelling..." : "Cancel Job"}
          </Button>
          {cancelJob.isError && (
            <p className="form-error" role="alert">
              {cancelJob.error.message}
            </p>
          )}
        </>
      )}
    </article>
  );
}

function JobDetail({
  job,
  onBack,
  onDeleted,
}: {
  job: Job;
  onBack: () => void;
  onDeleted: () => void;
}) {
  const retryJob = useRetryJob();
  const cancelJob = useCancelJob();
  const deleteJob = useDeleteJob();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const retryable = job.status === "Failed" || job.status === "Interrupted";
  const cancellable = job.status === "Queued";
  const deletable =
    job.status === "Completed" ||
    job.status === "Failed" ||
    job.status === "Interrupted" ||
    job.status === "Cancelled";
  const termMap = job.request.term_map;

  useEffect(() => {
    titleRef.current?.focus();
  }, [job.id]);

  return (
    <div className="job-detail-content">
      <div className="job-detail-header">
        <Button
          className="job-back-action"
          variant="outline"
          type="button"
          onClick={onBack}
        >
          <ArrowLeftIcon size={16} aria-hidden="true" /> Back to Jobs
        </Button>
        <div className="job-detail-title">
          <p className="eyebrow">Job {job.id}</p>
          <h2 id="job-detail-title" ref={titleRef} tabIndex={-1}>
            {job.request.media_path}
          </h2>
          <p>
            {sourceSummary(job)} to {job.request.target_language_code}
          </p>
        </div>
        <JobStatus status={job.status} />
      </div>

      <div className="job-detail-actions">
        {cancellable && (
          <Button
            type="button"
            variant="outline"
            disabled={cancelJob.isPending}
            onClick={() => {
              if (confirmCancelJob(job.id)) {
                cancelJob.mutate(job.id);
              }
            }}
          >
            {cancelJob.isPending ? "Cancelling..." : "Cancel Job"}
          </Button>
        )}
        {retryable && (
          <Button
            type="button"
            variant="outline"
            disabled={retryJob.isPending}
            onClick={() => retryJob.mutate(job.id)}
          >
            {retryJob.isPending ? "Retrying..." : "Retry Job"}
          </Button>
        )}
        {deletable && (
          <Button
            type="button"
            variant="outline"
            disabled={deleteJob.isPending}
            onClick={() => {
              if (
                window.confirm(
                  `Delete Job ${job.id.slice(0, 8)}? This removes its history and residual Work data but preserves Media and published output.`,
                )
              ) {
                deleteJob.mutate(job.id, { onSuccess: onDeleted });
              }
            }}
          >
            {deleteJob.isPending ? "Deleting..." : "Delete Job"}
          </Button>
        )}
        {retryJob.isError && (
          <p className="form-error" role="alert">
            {retryJob.error.message}
          </p>
        )}
        {deleteJob.isError && (
          <p className="form-error" role="alert">
            {deleteJob.error.message}
          </p>
        )}
        {cancelJob.isError && (
          <p className="form-error" role="alert">
            {cancelJob.error.message}
          </p>
        )}
      </div>
      {isRunningJob(job.status) && (
        <p className="job-action-note" role="status">
          {RUNNING_JOB_MESSAGE}
        </p>
      )}

      {job.error && <JobError error={job.error} />}

      <section className="job-detail-section" aria-labelledby="job-summary-title">
        <h3 id="job-summary-title">Request summary</h3>
        <dl className="job-summary">
          <SummaryItem label="Media" value={job.request.media_path} />
          <SummaryItem label="Source" value={sourceSummary(job)} />
          <SummaryItem
            label="Target language"
            value={job.request.target_language_code}
          />
          <SummaryItem
            label="Output format"
            value={job.request.source_format.toUpperCase()}
          />
          <SummaryItem label="Term map snapshot" value={termMap?.name ?? "None"} />
          <SummaryItem label="Attempt" value={String(job.attempt)} />
        </dl>
      </section>

      <section className="job-detail-section" aria-labelledby="job-output-title">
        <h3 id="job-output-title">Final output</h3>
        <p className="job-final-output">{job.request.output_path}</p>
      </section>

      <section className="job-detail-section" aria-labelledby="job-time-title">
        <h3 id="job-time-title">Timestamps (local time)</h3>
        <dl className="job-summary">
          <SummaryItem label="Created" value={formatLocalTimestamp(job.created_at)} />
          <SummaryItem label="Started" value={formatLocalTimestamp(job.started_at)} />
          <SummaryItem label="Finished" value={formatLocalTimestamp(job.finished_at)} />
        </dl>
      </section>
      <section className="job-detail-section" aria-labelledby="job-history-title">
        <h3 id="job-history-title">Status history</h3>
        {job.status_history && job.status_history.length > 0 ? (
          <StatusHistory entries={job.status_history} />
        ) : (
          <p className="job-history-unavailable">
            Status history unavailable for this Job.
          </p>
        )}
      </section>
    </div>
  );
}

function JobError({ error }: { error: NonNullable<Job["error"]> }) {
  const context = Object.entries(error).filter(([key]) =>
    (APPROVED_ERROR_CONTEXT_KEYS as readonly string[]).includes(key),
  );
  return (
    <section className="job-error" aria-labelledby="job-error-title">
      <div className="job-error-heading">
        <WarningCircleIcon size={19} aria-hidden="true" />
        <div>
          <h3 id="job-error-title">Action needed</h3>
          <p>{error.message}</p>
        </div>
      </div>
      <details>
        <summary>Show approved diagnostic context</summary>
        <dl className="job-summary">
          <SummaryItem label="Error code" value={error.code} />
          {context.map(([key, value]) => (
            <SummaryItem key={key} label={key} value={String(value)} />
          ))}
        </dl>
      </details>
    </section>
  );
}

export function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd title={value}>{value}</dd>
    </div>
  );
}

function StatusHistory({ entries }: { entries: JobStatusHistoryEntry[] }) {
  return (
    <ol className="job-status-history" aria-label="Job status history">
      {entries.map((entry, index) => (
        <li key={`${entry.attempt}-${entry.status}-${entry.started_at}-${index}`}>
          <div>
            <JobStatus status={entry.status} />
            <span>Attempt {entry.attempt}</span>
          </div>
          <dl>
            <div>
              <dt>Started</dt>
              <dd>{formatLocalTimestamp(entry.started_at)}</dd>
            </div>
            <div>
              <dt>Finished</dt>
              <dd>{formatLocalTimestamp(entry.finished_at)}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ol>
  );
}

function JobStatus({ status }: { status: Job["status"] }) {
  return <span className={`job-status status-${status.toLowerCase()}`}>{status}</span>;
}

function confirmCancelJob(jobId: string): boolean {
  return window.confirm(
    `Cancel Job ${jobId.slice(0, 8)}? It will remain in Job history and will not be translated.`,
  );
}

function DetailState({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div
      className={cn("detail-state", error && "error")}
      role={error ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

function SelectionPrompt() {
  return (
    <div className="detail-state">
      <BriefcaseIcon size={24} aria-hidden="true" />
      <h2>Select a Job</h2>
      <p>Choose a Job from history to inspect its request, output, and diagnostics.</p>
    </div>
  );
}

function sourceSummary(job: Job): string {
  return (
    job.request.subtitle_path ??
    `Embedded subtitle · Stream ${job.request.stream_index}`
  );
}

function isRunningJob(status: Job["status"]): boolean {
  return status === "Extracting" || status === "Translating";
}
