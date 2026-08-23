import {
  ArrowLeftIcon,
  BriefcaseIcon,
  CheckCircleIcon,
  CopyIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Button } from "./components/ui/button";
import { PageHeader } from "./components/page-header";
import { Guidance } from "./components/ui/guidance";
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
  type JobStatusFilter,
  type JobStatusHistoryEntry,
  type OutputConflictPolicy,
} from "./jobs";
import { cn, formatLocalTimestamp, formatRelativeTimestamp } from "./lib/utils";
import { formatError, useI18n, type TranslationKey } from "./i18n";
import { useProductStatus } from "./status";

type ClearFeedback = {
  title: string;
  tone: "success" | "warning" | "error";
  message: string;
  role: "status" | "alert";
};

export function JobNotificationRegion({
  notifications,
  dismiss,
}: {
  notifications: JobNotification[];
  dismiss: (id: string) => void;
}) {
  const { t } = useI18n();
  return (
    <aside className="toast-region" aria-label={t("jobs.notifications")}>
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
  const { t } = useI18n();
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
      <span>
        {notification.status === "Completed"
          ? t("jobs.notificationCompleted", { media: notification.media })
          : t("jobs.notificationFailed", {
              media: notification.media,
              error: notification.errorMessage ?? t("jobs.notificationDetails"),
            })}
      </span>
      <button
        type="button"
        onClick={() => dismiss(notification.id)}
        aria-label={t("jobs.dismissNotification")}
      >
        {t("jobs.dismissNotification")}
      </button>
    </div>
  );
}

export function JobsPage() {
  const { t } = useI18n();
  const { jobId } = useParams();
  const navigate = useNavigate();
  const listTitleRef = useRef<HTMLHeadingElement>(null);
  const focusListOnReturn = useRef(false);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<JobStatusFilter>("all");
  const jobs = useJobs({ poll: false, search, status });
  const productStatus = useProductStatus();
  const clearCompleted = useClearCompletedJobs();
  const activeJobs = jobs.data?.active_jobs ?? [];
  const historyJobs = jobs.data?.history_jobs ?? [];
  const allJobs = [...activeJobs, ...historyJobs];
  const selected = allJobs.find((job) => job.id === jobId) ?? null;
  const detail = useJob(jobId ?? null, jobId !== undefined);
  const displayedJob = detail.data ?? selected;
  const completedCount = jobs.data?.completed_count ?? 0;
  const matchingCount = jobs.data?.matching_count ?? historyJobs.length;
  const completedCountKnown = jobs.data?.completed_count !== undefined;
  const canClearCompleted = completedCount > 0;
  const clearCompletedLabel = completedCountKnown
    ? `${t("jobs.clearCompleted")} (${completedCount})`
    : t("jobs.clearCompleted");
  const recordHealth = productStatus.data?.job_records;
  const recordAttention =
    (recordHealth?.corrupt.count ?? 0) + (recordHealth?.unsupported.count ?? 0) > 0;
  const [clearFeedback, setClearFeedback] = useState<ClearFeedback | null>(null);

  const clearCompletedJobs = () => {
    const prompt = t(
      completedCount === 1
        ? "jobs.clearConfirmationSingular"
        : "jobs.clearConfirmationPlural",
      { count: completedCount },
    );
    if (!window.confirm(prompt)) {
      return;
    }
      setClearFeedback(null);
      clearCompleted.mutate(undefined, {
        onSuccess: (result) => {
        const unit = result.deleted.length === 1 ? t("jobs.job") : t("jobs.jobs");
        const failedUnit = result.failed.length === 1 ? t("jobs.job") : t("jobs.jobs");
        if (result.failed.length === 0) {
          setClearFeedback({
            title: t("jobs.clearSuccessTitle"),
            tone: "success",
            role: "status",
            message: t("jobs.clearSuccess", { count: result.deleted.length, unit }),
          });
        } else if (result.deleted.length > 0) {
          setClearFeedback({
            title: t("jobs.clearPartialTitle"),
            tone: "warning",
            role: "status",
            message: t("jobs.clearPartial", {
              count: result.deleted.length,
              unit,
              failed: result.failed.length,
              failedUnit,
            }),
          });
        } else {
          setClearFeedback({
            title: t("jobs.clearFailedTitle"),
            tone: "error",
            role: "alert",
            message: t("jobs.clearFailed"),
          });
        }
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
      <PageHeader title={t("jobs.title")} detail={t("jobs.detail")} />
      {recordAttention && recordHealth && <RecordHealthNotice health={recordHealth} />}
      <div className={cn("job-layout", jobId && "has-selection")}>
        <section className="job-list-panel" aria-labelledby="job-list-title">
          <div className="section-heading job-list-heading">
            <div>
              <h2 id="job-list-title" ref={listTitleRef} tabIndex={-1}>
                {t("jobs.history")}
              </h2>
            </div>
            <div
              className="job-history-filters"
              role="search"
              aria-label={t("jobs.filterHistory")}
            >
              <label>
                {t("jobs.search")}
                <input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder={t("jobs.searchPlaceholder")}
                />
              </label>
              <label>
                {t("jobs.status")}
                <select
                  value={status}
                  onChange={(event) => {
                    const nextStatus = event.target.value;
                    if (isJobStatusFilter(nextStatus)) setStatus(nextStatus);
                  }}
                >
                  <option value="all">{t("jobs.allStatuses")}</option>
                  <option value="Queued">{t("jobs.statusOption.Queued")}</option>
                  <option value="Extracting">
                    {t("jobs.statusOption.Extracting")}
                  </option>
                  <option value="Translating">
                    {t("jobs.statusOption.Translating")}
                  </option>
                  <option value="Completed">{t("jobs.statusOption.Completed")}</option>
                  <option value="Failed">{t("jobs.statusOption.Failed")}</option>
                  <option value="Interrupted">
                    {t("jobs.statusOption.Interrupted")}
                  </option>
                  <option value="Cancelled">{t("jobs.statusOption.Cancelled")}</option>
                </select>
              </label>
            </div>
            <div className="job-list-actions">
              {jobs.data && (
                <span className="count-badge">
                  {t("jobs.matching", { count: matchingCount })}
                </span>
              )}
              <Button
                variant="outline"
                type="button"
                disabled={!canClearCompleted || clearCompleted.isPending}
                aria-describedby="clear-completed-scope"
                onClick={clearCompletedJobs}
              >
                {clearCompleted.isPending ? t("jobs.clearing") : clearCompletedLabel}
              </Button>
              <span id="clear-completed-scope" className="clear-completed-scope">
                {t("jobs.clearScope")}
              </span>
            </div>
          </div>
          {clearFeedback && (
            <Guidance
              title={clearFeedback.title}
              tone={clearFeedback.tone}
              role={clearFeedback.role}
            >
              {clearFeedback.message}
            </Guidance>
          )}
          {clearCompleted.isError && (
            <p className="form-error" role="alert">
              {formatError(clearCompleted.error, t)}
            </p>
          )}
          {clearCompleted.data && clearCompleted.data.failed.length > 0 && (
            <div
              className="form-error"
              role={clearCompleted.data.deleted.length === 0 ? "status" : "alert"}
            >
              <p>
                {clearCompleted.data.deleted.length === 0
                  ? t("jobs.noCompletedCleared")
                  : t("jobs.someCompletedFailed")}
              </p>
              <ul>
                {clearCompleted.data.failed.map((failure) => (
                  <li key={failure.id}>
                    {t("jobs.jobPrefix", { id: failure.id.slice(0, 8) })}:{" "}
                    {failure.message}
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
                {t("jobs.loading")}
              </div>
            )}
            {jobs.isError && (
              <div className="inline-state error" role="alert">
                {formatError(jobs.error, t)}
                <Button variant="outline" onClick={() => void jobs.refetch()}>
                  {t("common.tryAgain")}
                </Button>
              </div>
            )}
            {!jobs.isPending && !jobs.isError && allJobs.length === 0 && (
              <div className="empty-state">
                <span className="empty-icon">
                  <BriefcaseIcon size={22} aria-hidden="true" />
                </span>
                <h2>
                  {hasJobFilters(search, status)
                    ? t("jobs.noMatching")
                    : t("jobs.noJobs")}
                </h2>
                <p>
                  {hasJobFilters(search, status)
                    ? t("jobs.noMatchingDetail")
                    : t("jobs.noJobsDetail")}
                </p>
                {!hasJobFilters(search, status) && (
                  <Button
                    variant="outline"
                    type="button"
                    onClick={() => navigate("/translate")}
                  >
                    Start a translation
                  </Button>
                )}
                {hasJobFilters(search, status) && (
                  <Button
                    variant="outline"
                    type="button"
                    onClick={() => {
                      setSearch("");
                      setStatus("all");
                    }}
                  >
                    {t("jobs.clearFilters")}
                  </Button>
                )}
              </div>
            )}
          </div>
          {activeJobs.length > 0 && (
            <section aria-labelledby="active-jobs-title">
              <h3 id="active-jobs-title">{t("jobs.active")}</h3>
              <JobList jobs={activeJobs} selectedId={jobId} onSelect={navigate} />
            </section>
          )}
          {historyJobs.length > 0 && (
            <section aria-labelledby="history-jobs-title">
              <h3 id="history-jobs-title">{t("jobs.completedAndPast")}</h3>
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
                ? t("jobs.refreshingHistory")
                : jobs.isFetchingNextPage
                  ? t("jobs.loadingHistory")
                  : t("jobs.loadMoreHistory")}
            </Button>
          )}
        </section>
        <section className="job-detail" aria-label={t("jobs.detailsRegion")}>
          {!jobId && <SelectionPrompt />}
          {jobId && !selected && detail.isPending && (
            <DetailState>{t("jobs.loading")}</DetailState>
          )}
          {jobId && detail.isError && (
            <DetailState error>
              {detail.error.message === "Job does not exist"
                ? t("jobs.noLongerAvailable")
                : formatError(detail.error, t)}
              <Button variant="outline" onClick={() => navigate("/jobs")}>
                {t("jobs.back")}
              </Button>
            </DetailState>
          )}
          {jobId &&
            displayedJob &&
            !detail.isError &&
            (selected || !detail.isPending) && (
              <JobDetail
                key={displayedJob.id}
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
  const { t } = useI18n();
  if (!health) return null;
  const entries = [
    [t("jobs.corrupt"), health.corrupt],
    [t("jobs.unsupported"), health.unsupported],
  ] as const;
  return (
    <section className="record-health-notice" aria-labelledby="record-health-title">
      <div>
        <p className="eyebrow">{t("jobs.persistenceWarning")}</p>
        <h2 id="record-health-title">{t("runtime.recordsAttention")}</h2>
        <p>{t("jobs.recordsExcluded")}</p>
      </div>
      <dl>
        {entries.map(([label, record]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {t("jobs.recordCount", {
                count: record.count,
                unit: record.count === 1 ? t("jobs.record") : t("jobs.records"),
              })}{" "}
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
  const { t } = useI18n();
  return (
    <div className="job-list" role="list" aria-label={t("jobs.translationJobs")}>
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
  const { t } = useI18n();
  const cancelJob = useCancelJob();

  return (
    <article className={cn("job-item", selected && "selected")} role="listitem">
      <button
        type="button"
        className="job-item-select"
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
      >
        <strong title={job.request.media_path}>
          {mediaBasename(job.request.media_path)}
        </strong>
        <span className="job-source">
          {t("jobs.sourceTo", {
            source: sourceSummary(job, t),
            target: job.request.target_language_code,
          })}
        </span>
        <time dateTime={job.created_at} title={formatLocalTimestamp(job.created_at)}>
          {t("jobs.created")} {formatRelativeTimestamp(job.created_at)}
        </time>
        <span className="job-id">{t("jobs.jobId", { id: job.id.slice(0, 8) })}</span>
      </button>
      <JobStatus status={job.status} />
      {job.queue_position !== null && job.queue_position !== undefined && (
        <span className="job-queue">
          {t("jobs.queuePosition")} {job.queue_position}
        </span>
      )}
      {job.request.term_map && (
        <span className="job-queue">
          {t("jobs.termMapLabel", { name: job.request.term_map.name })}
        </span>
      )}
      {isRunningJob(job.status) && (
        <span className="job-action-note">{t("jobs.runningCannotCancel")}</span>
      )}
      {job.status === "Queued" && !selected && (
        <>
          <Button
            type="button"
            variant="outline"
            disabled={cancelJob.isPending}
            onClick={() => {
              if (confirmCancelJob(job.id, t)) cancelJob.mutate(job.id);
            }}
          >
            {cancelJob.isPending ? t("jobs.cancelling") : t("jobs.cancelJob")}
          </Button>
          {cancelJob.isError && (
            <p className="form-error" role="alert">
              {formatError(cancelJob.error, t)}
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
  const { t } = useI18n();
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
  const [copyFeedback, setCopyFeedback] = useState<"success" | "fallback" | null>(null);

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
          <ArrowLeftIcon size={16} aria-hidden="true" /> {t("jobs.back")}
        </Button>
        <div className="job-detail-title">
          <h2 id="job-detail-title" ref={titleRef} tabIndex={-1}>
            {mediaBasename(job.request.media_path)}
          </h2>
        </div>
        <div className="job-detail-context">
          <JobStatus status={job.status} />
          <p className="job-status-explanation">{jobStatusExplanation(job.status, t)}</p>
          <div className="job-id-control">
            <code title={job.id}>{job.id}</code>
            <Button
              type="button"
              variant="outline"
              aria-label={t("jobs.copyId")}
              onClick={() => {
                void copyJobId(job.id).then((copied) =>
                  setCopyFeedback(copied ? "success" : "fallback"),
                );
              }}
            >
              <CopyIcon size={16} aria-hidden="true" /> {t("jobs.copyId")}
            </Button>
            {copyFeedback === "success" && (
              <span role="status">{t("jobs.copied")}</span>
            )}
            {copyFeedback === "fallback" && (
              <span role="status">{t("jobs.copyManually")}</span>
            )}
          </div>
        </div>
      </div>

      <div className="job-detail-actions">
        {cancellable && (
          <Button
            type="button"
            variant="outline"
            disabled={cancelJob.isPending}
            onClick={() => {
              if (confirmCancelJob(job.id, t)) {
                cancelJob.mutate(job.id);
              }
            }}
          >
            {cancelJob.isPending ? t("jobs.cancelling") : t("jobs.cancelJob")}
          </Button>
        )}
        {retryable && (
          <Button
            type="button"
            variant="outline"
            disabled={retryJob.isPending}
            onClick={() => retryJob.mutate(job.id)}
          >
            {retryJob.isPending ? t("jobs.retrying") : t("jobs.retry")}
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
                  t("jobs.deleteConfirmation", {
                    action: t("jobs.delete"),
                    id: job.id.slice(0, 8),
                  }),
                )
              ) {
                deleteJob.mutate(job.id, { onSuccess: onDeleted });
              }
            }}
          >
            {deleteJob.isPending ? t("jobs.deleting") : t("jobs.delete")}
          </Button>
        )}
        {retryJob.isError && (
          <p className="form-error" role="alert">
            {formatError(retryJob.error, t)}
          </p>
        )}
        {deleteJob.isError && (
          <p className="form-error" role="alert">
            {formatError(deleteJob.error, t)}
          </p>
        )}
        {cancelJob.isError && (
          <p className="form-error" role="alert">
            {formatError(cancelJob.error, t)}
          </p>
        )}
      </div>
      {isRunningJob(job.status) && (
        <p className="job-action-note" role="status">
          {t("jobs.runningCannotCancel")}
        </p>
      )}

      {retryable && (
        <Guidance title={t("jobs.actionAvailable")} tone="info">
          {t("jobs.retryGuidance")}
        </Guidance>
      )}

      {job.error && <JobError error={job.error} />}

      <section className="job-detail-section" aria-labelledby="job-summary-title">
        <h3 id="job-summary-title">{t("jobs.requestSummary")}</h3>
        <p className="section-intro">{t("jobs.requestSummaryDetail")}</p>
        <dl className="job-summary">
          <SummaryItem label={t("jobs.media")} value={job.request.media_path} />
          <SummaryItem label={t("jobs.source")} value={sourceSummary(job, t)} />
          <SummaryItem
            label={t("translate.targetLanguage")}
            value={job.request.target_language_code}
          />
          <SummaryItem
            label={t("jobs.outputFormat")}
            value={job.request.source_format.toUpperCase()}
          />
          <SummaryItem
            label={t("jobs.termMapPolicy")}
            value={termMapPolicy(job.request.term_map_mode, t)}
          />
          <SummaryItem
            label={t("jobs.termMapSnapshot")}
            value={termMap?.name ?? t("time.notRecorded")}
          />
          <SummaryItem label={t("jobs.attempt")} value={String(job.attempt)} />
          {job.request.dynamic_terminology_enabled !== undefined && (
            <SummaryItem
              label={t("translate.dynamicTerminology")}
              value={
                job.request.dynamic_terminology_enabled
                  ? t("jobs.enabled")
                  : t("jobs.disabled")
              }
            />
          )}
          {job.request.subtitle_terminology_filter_enabled !== undefined && (
            <SummaryItem
              label={t("translate.subtitleTerminology")}
              value={
                job.request.subtitle_terminology_filter_enabled
                  ? t("jobs.enabled")
                  : t("jobs.disabled")
              }
            />
          )}
          {job.request.output_suffix !== undefined && (
            <SummaryItem
              label={t("translate.outputSuffix")}
              value={job.request.output_suffix || t("jobs.none")}
            />
          )}
          {job.request.output_conflict_policy !== undefined && (
            <SummaryItem
              label={t("translate.outputConflict")}
              value={conflictPolicyLabel(job.request.output_conflict_policy, t)}
            />
          )}
        </dl>
      </section>

      <section className="job-detail-section" aria-labelledby="job-output-title">
        <h3 id="job-output-title">
          {job.status === "Completed" ? t("jobs.savedOutput") : t("jobs.plannedOutput")}
        </h3>
        <p className="job-final-output">{job.request.output_path}</p>
      </section>

      <section className="job-detail-section" aria-labelledby="job-time-title">
        <h3 id="job-time-title">{t("jobs.timestampsLocal")}</h3>
        <dl className="job-summary">
          <SummaryItem
            label={t("jobs.created")}
            value={formatLocalTimestamp(job.created_at)}
          />
          <SummaryItem
            label={t("jobs.started")}
            value={formatLocalTimestamp(job.started_at)}
          />
          <SummaryItem
            label={t("jobs.finished")}
            value={formatLocalTimestamp(job.finished_at)}
          />
        </dl>
      </section>
      <section className="job-detail-section" aria-labelledby="job-history-title">
        <h3 id="job-history-title">{t("jobs.stateHistory")}</h3>
        {job.status_history && job.status_history.length > 0 ? (
          <StatusHistory entries={job.status_history} />
        ) : (
          <p className="job-history-unavailable">{t("jobs.statusUnavailable")}</p>
        )}
      </section>
    </div>
  );
}

function JobError({ error }: { error: NonNullable<Job["error"]> }) {
  const { t } = useI18n();
  const context = Object.entries(error).filter(([key]) =>
    (APPROVED_ERROR_CONTEXT_KEYS as readonly string[]).includes(key),
  );
  return (
    <section className="job-error" aria-labelledby="job-error-title">
      <div className="job-error-heading">
        <WarningCircleIcon size={19} aria-hidden="true" />
        <div>
          <h3 id="job-error-title">{t("jobs.actionNeeded")}</h3>
          <p>{error.message}</p>
        </div>
      </div>
      <details>
        <summary>{t("jobs.showDiagnostics")}</summary>
        <p className="field-help">{t("jobs.diagnosticsDetail")}</p>
        <dl className="job-summary">
          <SummaryItem label={t("jobs.errorCode")} value={error.code} />
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
  const { t } = useI18n();
  return (
    <ol className="job-status-history" aria-label={t("jobs.statusHistoryLabel")}>
      {entries.map((entry, index) => (
        <li key={`${entry.attempt}-${entry.status}-${entry.started_at}-${index}`}>
          <div>
            <JobStatus status={entry.status} />
            <span>{t("jobs.attemptLabel", { attempt: entry.attempt })}</span>
          </div>
          <dl>
            <div>
              <dt>{t("jobs.started")}</dt>
              <dd>{formatLocalTimestamp(entry.started_at)}</dd>
            </div>
            <div>
              <dt>{t("jobs.finished")}</dt>
              <dd>{formatLocalTimestamp(entry.finished_at)}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ol>
  );
}

function JobStatus({ status }: { status: Job["status"] }) {
  const { t } = useI18n();
  return (
    <span className={`job-status status-${status.toLowerCase()}`}>
      {t(`jobs.status.${status}`)}
    </span>
  );
}

function jobStatusExplanation(
  status: Job["status"],
  t: ReturnType<typeof useI18n>["t"],
): string {
  return t(`jobs.statusExplanation.${status}` as TranslationKey);
}

function confirmCancelJob(jobId: string, t: ReturnType<typeof useI18n>["t"]): boolean {
  return window.confirm(
    t("jobs.cancelConfirmation", {
      action: t("common.cancel"),
      id: jobId.slice(0, 8),
    }),
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
  const { t } = useI18n();
  return (
    <div className="detail-state">
      <BriefcaseIcon size={24} aria-hidden="true" />
      <h2>{t("jobs.select")}</h2>
      <p>{t("jobs.selectDetail")}</p>
    </div>
  );
}

function sourceSummary(job: Job, t: ReturnType<typeof useI18n>["t"]): string {
  return (
    job.request.subtitle_path ??
    `${t("translate.embeddedSubtitle")} · ${t("translate.stream", { index: job.request.stream_index ?? "?" })}`
  );
}

function mediaBasename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

function termMapPolicy(
  mode: Job["request"]["term_map_mode"],
  t: ReturnType<typeof useI18n>["t"],
): string {
  switch (mode) {
    case "selected":
      return t("jobs.termMap.selected");
    case "none":
      return t("jobs.termMap.none");
    case "follow":
      return t("jobs.termMap.follow");
  }
}

function conflictPolicyLabel(
  policy: OutputConflictPolicy | undefined,
  t: ReturnType<typeof useI18n>["t"],
): string {
  switch (policy) {
    case "skip":
      return t("translate.skipExisting");
    case "append-number":
      return t("translate.appendNumber");
    case "overwrite":
      return t("translate.overwrite");
    default:
      return "";
  }
}

async function copyJobId(jobId: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(jobId);
    return true;
  } catch {
    return false;
  }
}

function isRunningJob(status: Job["status"]): boolean {
  return status === "Extracting" || status === "Translating";
}

function hasJobFilters(search: string, status: JobStatusFilter): boolean {
  return search.trim().length > 0 || status !== "all";
}

function isJobStatusFilter(value: string): value is JobStatusFilter {
  return [
    "all",
    "Queued",
    "Extracting",
    "Translating",
    "Completed",
    "Failed",
    "Interrupted",
    "Cancelled",
  ].includes(value);
}
