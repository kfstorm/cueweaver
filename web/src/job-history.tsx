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
  useJob,
  useJobs,
  useRetryJob,
  type Job,
  type JobNotification,
} from "./jobs";
import { cn, formatLocalTimestamp, formatUtcTimestamp } from "./lib/utils";

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
  const jobs = useJobs({ poll: false });
  const selected = jobs.data?.find((job) => job.id === jobId) ?? null;
  const detail = useJob(jobId ?? null, selected === null);
  const displayedJob = selected ?? detail.data;

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Jobs</h1>
          <p>Review durable translation history, diagnostics, and retryable work.</p>
        </div>
        <span className="worker-badge">Single worker</span>
      </header>
      <div className={cn("job-layout", jobId && "has-selection")}>
        <section className="job-list-panel" aria-labelledby="job-list-title">
          <div className="section-heading job-list-heading">
            <div>
              <p className="eyebrow">History</p>
              <h2 id="job-list-title">All Jobs</h2>
            </div>
            {jobs.data && <span className="count-badge">{jobs.data.length}</span>}
          </div>
          <div className="job-list-state">
            {jobs.isPending && (
              <div className="inline-state" role="status">
                Loading Jobs
              </div>
            )}
            {jobs.isError && (
              <div className="inline-state error" role="alert">
                {jobs.error.message}
                <Button variant="outline" onClick={() => void jobs.refetch()}>
                  Try again
                </Button>
              </div>
            )}
            {!jobs.isPending && !jobs.isError && jobs.data?.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon">
                  <BriefcaseIcon size={22} aria-hidden="true" />
                </span>
                <h2>No Jobs yet</h2>
                <p>Submitted translations will appear here with their current state.</p>
              </div>
            )}
          </div>
          {jobs.data && jobs.data.length > 0 && (
            <div className="job-list" role="list" aria-label="Translation Jobs">
              {jobs.data.map((job) => (
                <JobListItem
                  key={job.id}
                  job={job}
                  selected={job.id === jobId}
                  onSelect={() => navigate(`/jobs/${encodeURIComponent(job.id)}`)}
                />
              ))}
            </div>
          )}
        </section>
        <section className="job-detail" aria-label="Job details">
          {!jobId && <SelectionPrompt />}
          {jobId && !selected && detail.isPending && (
            <DetailState>Loading Job details</DetailState>
          )}
          {jobId && !selected && detail.isError && (
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
            (selected || (!detail.isPending && !detail.isError)) && (
              <JobDetail job={displayedJob} onBack={() => navigate("/jobs")} />
            )}
        </section>
      </div>
    </>
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
  return (
    <article className={cn("job-item", selected && "selected")} role="listitem">
      <button
        type="button"
        className="job-item-select"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
      >
        <span className="job-id">Job {job.id.slice(0, 8)}</span>
        <strong title={job.request.media_path}>{job.request.media_path}</strong>
        <span className="job-source">
          {sourceSummary(job)} to {job.request.target_language_code}
        </span>
        <span className="job-output">Output: {job.request.output_path}</span>
        <time dateTime={job.created_at}>
          Created {formatLocalTimestamp(job.created_at)}
        </time>
      </button>
      <JobStatus status={job.status} />
      {job.queue_position !== null && job.queue_position !== undefined && (
        <span className="job-queue">Queue position {job.queue_position}</span>
      )}
      {job.request.term_map && (
        <span className="job-queue">Term map: {job.request.term_map.name}</span>
      )}
    </article>
  );
}

function JobDetail({ job, onBack }: { job: Job; onBack: () => void }) {
  const retryJob = useRetryJob();
  const titleRef = useRef<HTMLHeadingElement>(null);
  const retryable = job.status === "Failed" || job.status === "Interrupted";
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
        {retryJob.isError && (
          <p className="form-error" role="alert">
            {retryJob.error.message}
          </p>
        )}
      </div>

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
        <h3 id="job-time-title">Timestamps (UTC)</h3>
        <dl className="job-summary">
          <SummaryItem label="Created" value={formatUtcTimestamp(job.created_at)} />
          <SummaryItem label="Started" value={formatUtcTimestamp(job.started_at)} />
          <SummaryItem label="Finished" value={formatUtcTimestamp(job.finished_at)} />
        </dl>
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

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd title={value}>{value}</dd>
    </div>
  );
}

function JobStatus({ status }: { status: Job["status"] }) {
  return <span className={`job-status status-${status.toLowerCase()}`}>{status}</span>;
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
  return job.request.subtitle_path ?? `Embedded stream ${job.request.stream_index}`;
}
