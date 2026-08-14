import {
  ArrowLeftIcon,
  BriefcaseIcon,
  CheckCircleIcon,
  ListChecksIcon,
  MagnifyingGlassIcon,
  SpinnerGapIcon,
  TranslateIcon,
  UploadSimpleIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { useDeferredValue, useEffect, useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, Route, Routes } from "react-router-dom";

import { Button } from "./components/ui/button";
import { Input, Textarea } from "./components/ui/input";
import {
  useMediaDirectory,
  useMediaDiscovery,
  type MediaDirectoryEntry,
  type SubtitleCandidate,
  type UnsupportedSubtitleCandidate,
} from "./browse";
import { cn } from "./lib/utils";
import { useProductStatus } from "./status";
import { useCreateJob, useJobs, useRetryJob } from "./jobs";
import { COMMON_TARGET_LANGUAGES } from "./languages";
import {
  useCreateTermMap,
  useDeleteTermMap,
  useRenameTermMap,
  useReplaceTermMap,
  useTermMap,
  useTermMaps,
} from "./term-maps";

const routes: Array<{ label: string; path: string; icon: Icon }> = [
  { label: "Translate", path: "/translate", icon: TranslateIcon },
  { label: "Jobs", path: "/jobs", icon: BriefcaseIcon },
  { label: "Term maps", path: "/term-maps", icon: ListChecksIcon },
];

function Navigation({ mobile = false }: { mobile?: boolean }) {
  return (
    <nav
      aria-label={mobile ? "Mobile navigation" : "Primary navigation"}
      className={mobile ? "mobile-nav" : "desktop-nav"}
    >
      {routes.map(({ label, path, icon: RouteIcon }) => (
        <NavLink
          key={path}
          to={path}
          className={({ isActive }) => cn("nav-link", isActive && "active")}
        >
          <RouteIcon aria-hidden="true" size={18} weight="regular" />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

function Shell() {
  const status = useProductStatus();
  const ready = status.data?.api.ready && status.data?.roots.ready;
  return (
    <div className="product-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            CW
          </span>
          <span>CueWeaver</span>
        </div>
        <Navigation />
        <div className="runtime-summary">
          <span className={cn("status-dot", ready && "ready")} />
          {status.isPending
            ? "Checking runtime"
            : ready
              ? "Runtime ready"
              : "Runtime unavailable"}
        </div>
      </aside>
      <main className="workspace">
        <Outlet />
      </main>
      <Navigation mobile />
    </div>
  );
}

function PageHeader({ title, detail }: { title: string; detail: string }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
      <span className="worker-badge">Single worker</span>
    </header>
  );
}

function Translate() {
  const queryClient = useQueryClient();
  const createJob = useCreateJob();
  const status = useProductStatus();
  const [directory, setDirectory] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedMedia, setSelectedMedia] = useState<string | null>(null);
  const [selectedSubtitle, setSelectedSubtitle] = useState<string | null>(null);
  const [targetLanguage, setTargetLanguage] = useState(
    () => window.localStorage.getItem("cueweaver.target-language") ?? "",
  );
  const [outputSuffix, setOutputSuffix] = useState(() => targetLanguage);
  const [outputConflictPolicy, setOutputConflictPolicy] = useState<
    "append-number" | "overwrite"
  >("append-number");
  const suffixEdited = useRef(false);
  const [termMapId, setTermMapId] = useState<string | null>(null);
  const [dynamicTerminologyEnabled, setDynamicTerminologyEnabled] = useState(true);
  const [subtitleTerminologyFilterEnabled, setSubtitleTerminologyFilterEnabled] =
    useState(true);
  const termMaps = useTermMaps();
  const selectedTermMapId =
    termMapId !== null &&
    (termMaps.data === undefined ||
      termMaps.data.term_maps.some((termMap) => termMap.id === termMapId))
      ? termMapId
      : null;
  const browser = useMediaDirectory(directory);
  const discovery = useMediaDiscovery(selectedMedia);
  const clearDiscovery = (previousMedia: string | null) => {
    if (previousMedia !== null) {
      void queryClient.cancelQueries({ queryKey: ["media-discovery", previousMedia] });
      queryClient.removeQueries({ queryKey: ["media-discovery", previousMedia] });
    }
  };
  const clearMedia = (previousMedia: string | null) => {
    clearDiscovery(previousMedia);
    setSelectedMedia(null);
    setSelectedSubtitle(null);
  };
  const selectedCandidate = discovery.data?.candidates.find(
    (candidate, index) => candidateKey(candidate, index) === selectedSubtitle,
  );
  const outputFormat = selectedCandidate?.format ?? "srt";
  const outputParts = selectedMedia
    ? outputNameParts(selectedMedia, outputFormat)
    : null;
  const outputSuffixError = validateOutputSuffix(outputSuffix);
  const canSubmit =
    selectedMedia !== null &&
    ((selectedCandidate?.kind === "external" && selectedCandidate.path !== undefined) ||
      (selectedCandidate?.kind === "embedded" &&
        selectedCandidate.stream_index !== undefined &&
        selectedCandidate.format !== undefined)) &&
    targetLanguage.trim() !== "" &&
    outputSuffixError === null &&
    status.data?.translation_provider.ready === true &&
    !createJob.isPending;
  return (
    <>
      <PageHeader
        title="Translate"
        detail="Prepare a subtitle translation from your mounted media library."
      />
      <section className="workflow-panel" aria-labelledby="source-title">
        <div className="step-index">01</div>
        <div className="step-content">
          <h2 id="source-title">Choose media</h2>
          <MediaBrowser
            directory={directory}
            filter={filter}
            onDirectoryChange={(path) => {
              setDirectory(path);
              setFilter("");
              clearMedia(selectedMedia);
            }}
            onFilterChange={setFilter}
            selectedMedia={selectedMedia}
            onMediaSelect={(path) => {
              clearDiscovery(selectedMedia);
              setSelectedMedia(path);
              setSelectedSubtitle(null);
            }}
            query={browser}
          />
          {selectedMedia && (
            <SubtitleDiscovery
              mediaPath={selectedMedia}
              selected={selectedSubtitle}
              onSelect={setSelectedSubtitle}
              query={discovery}
              onClear={() => {
                clearMedia(selectedMedia);
              }}
            />
          )}
        </div>
      </section>
      <section className="workflow-panel muted" aria-labelledby="configure-title">
        <div className="step-index">02</div>
        <div className="step-content">
          <h2 id="configure-title">Configure translation</h2>
          <p>Select an External or Embedded subtitle and enter the target language.</p>
          <label htmlFor="target-language-code">
            Target language code
            <Input
              id="target-language-code"
              list="target-languages"
              required
              aria-describedby="target-language-help"
              value={targetLanguage}
              onChange={(event) => {
                const value = event.target.value;
                setTargetLanguage(value);
                if (!suffixEdited.current) setOutputSuffix(value);
              }}
              placeholder="zh-Hans"
              disabled={selectedCandidate === undefined}
            />
            <datalist id="target-languages">
              {COMMON_TARGET_LANGUAGES.map((language) => (
                <option key={language} value={language} />
              ))}
            </datalist>
          </label>
          <span id="target-language-help" className="field-help">
            Search common BCP 47 codes or enter a custom code.
          </span>
          <details className="advanced-settings">
            <summary>Advanced settings</summary>
            <div className="advanced-fields">
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={dynamicTerminologyEnabled}
                  onChange={(event) =>
                    setDynamicTerminologyEnabled(event.target.checked)
                  }
                />
                Dynamic terminology
              </label>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={subtitleTerminologyFilterEnabled}
                  onChange={(event) =>
                    setSubtitleTerminologyFilterEnabled(event.target.checked)
                  }
                />
                Subtitle terminology filtering
              </label>
              <label>
                Term map
                <select
                  value={selectedTermMapId ?? ""}
                  onChange={(event) => setTermMapId(event.target.value || null)}
                >
                  <option value="">No Term map</option>
                  {(termMaps.data?.term_maps ?? []).map((termMap) => (
                    <option key={termMap.id} value={termMap.id}>
                      {termMap.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </details>
          {selectedMedia && selectedCandidate && outputParts && (
            <div className="output-name-section">
              <span id="output-name-label" className="field-label">
                Output filename
              </span>
              <div
                className="output-name-control"
                role="group"
                aria-labelledby="output-name-label"
              >
                <Input
                  aria-label="Media stem"
                  className="output-name-stem"
                  readOnly
                  value={`${outputParts.stem}.`}
                />
                <Input
                  aria-label="Subtitle suffix"
                  aria-describedby="output-suffix-help"
                  className="output-name-suffix"
                  value={outputSuffix}
                  onChange={(event) => {
                    suffixEdited.current = true;
                    setOutputSuffix(event.target.value);
                  }}
                />
                <Input
                  aria-label="Source format extension"
                  className="output-name-extension"
                  readOnly
                  value={`.${outputParts.format}`}
                />
              </div>
              <p id="output-suffix-help" className="field-help" aria-live="polite">
                Final name: <strong>{outputParts.name(outputSuffix)}</strong>
              </p>
              {outputSuffixError && (
                <p className="form-error" role="alert">
                  {outputSuffixError}
                </p>
              )}
              <fieldset className="output-conflict-policy">
                <legend>If the final name already exists</legend>
                <label>
                  <input
                    type="radio"
                    name="output-conflict-policy"
                    value="append-number"
                    checked={outputConflictPolicy === "append-number"}
                    onChange={() => setOutputConflictPolicy("append-number")}
                  />
                  Append a number (recommended)
                </label>
                <label>
                  <input
                    type="radio"
                    name="output-conflict-policy"
                    value="overwrite"
                    checked={outputConflictPolicy === "overwrite"}
                    onChange={() => setOutputConflictPolicy("overwrite")}
                  />
                  Overwrite existing output
                </label>
              </fieldset>
            </div>
          )}
        </div>
      </section>
      <div className="submission-bar">
        <ProviderState />
        <Button
          disabled={!canSubmit}
          onClick={() => {
            if (
              selectedMedia &&
              selectedCandidate &&
              ((selectedCandidate.kind === "external" && selectedCandidate.path) ||
                (selectedCandidate.kind === "embedded" &&
                  selectedCandidate.stream_index !== undefined &&
                  selectedCandidate.format))
            ) {
              const request = {
                media_path: selectedMedia,
                ...(selectedCandidate.kind === "external"
                  ? { subtitle_path: selectedCandidate.path }
                  : {
                      stream_index: selectedCandidate.stream_index,
                      source_format: selectedCandidate.format,
                    }),
                target_language_code: targetLanguage,
                output_suffix: outputSuffix,
                output_conflict_policy: outputConflictPolicy,
                term_map_id: selectedTermMapId,
                dynamic_terminology_enabled: dynamicTerminologyEnabled,
                subtitle_terminology_filter_enabled: subtitleTerminologyFilterEnabled,
              };
              createJob.mutate(request, {
                onSuccess: () => {
                  window.localStorage.setItem(
                    "cueweaver.target-language",
                    targetLanguage,
                  );
                  suffixEdited.current = false;
                  setOutputSuffix(targetLanguage);
                  setOutputConflictPolicy("append-number");
                  clearMedia(selectedMedia);
                },
              });
            }
          }}
        >
          {createJob.isPending ? "Queueing..." : "Start translation"}
        </Button>
      </div>
      {createJob.isError && (
        <p className="form-error" role="alert">
          {createJob.error.message}
        </p>
      )}
      {createJob.isSuccess && (
        <p className="upload-status" role="status">
          Translation queued
        </p>
      )}
    </>
  );
}

function outputNameParts(mediaPath: string, format: string) {
  const mediaName = mediaPath.split("/").pop() ?? mediaPath;
  const extensionIndex = mediaName.lastIndexOf(".");
  const stem = extensionIndex > 0 ? mediaName.slice(0, extensionIndex) : mediaName;
  return {
    stem,
    format,
    name: (suffix: string) => `${stem}.${suffix || "<subtitle suffix>"}.${format}`,
  };
}

function validateOutputSuffix(value: string): string | null {
  if (!value) return "Subtitle suffix must be non-empty.";
  const reserved = new Set([
    "con",
    "prn",
    "aux",
    "nul",
    ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
    ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
  ]);
  for (const segment of value.split(".")) {
    if (!segment) return "Subtitle suffix segments cannot be empty.";
    if (/\s$/u.test(segment)) {
      return "Subtitle suffix segments cannot end in a space.";
    }
    if (reserved.has(segment.toLocaleLowerCase())) {
      return "Subtitle suffix contains a reserved filename segment.";
    }
    for (const character of segment) {
      const codePoint = character.codePointAt(0) ?? 0;
      if (
        codePoint < 32 ||
        codePoint === 127 ||
        /\p{C}/u.test(character) ||
        !/[\p{L}\p{N}\s_-]/u.test(character)
      ) {
        return "Subtitle suffix contains an unsafe character.";
      }
    }
  }
  return null;
}

function SubtitleDiscovery({
  mediaPath,
  selected,
  onSelect,
  query,
  onClear,
}: {
  mediaPath: string;
  selected: string | null;
  onSelect: (value: string) => void;
  query: ReturnType<typeof useMediaDiscovery>;
  onClear: () => void;
}) {
  return (
    <section className="subtitle-discovery" aria-labelledby="subtitle-title">
      <div className="subtitle-heading">
        <div>
          <h3 id="subtitle-title">Choose a subtitle</h3>
          <p>Sources discovered for {mediaPath.split("/").pop()}.</p>
        </div>
        <Button type="button" variant="outline" onClick={onClear}>
          Choose another Media
        </Button>
      </div>
      <div className="subtitle-results" aria-live="polite">
        {(query.isPending || query.isFetching) && (
          <div
            role="status"
            className="discovery-skeleton"
            aria-label="Loading subtitles"
          >
            <span />
            <span />
            <span />
          </div>
        )}
        {query.isError && (
          <QueryErrorMessage
            message={query.error.message}
            onRetry={() => void query.refetch()}
          />
        )}
        {!query.isFetching &&
          query.data &&
          query.data.candidates.length === 0 &&
          query.data.unsupported_candidates.length === 0 && (
            <EmptyMessage>No subtitles were found for this Media.</EmptyMessage>
          )}
        {!query.isFetching &&
          !query.isError &&
          query.data?.candidates.map((candidate, index) => {
            const key = candidateKey(candidate, index);
            return (
              <SubtitleEntry
                key={key}
                candidate={candidate}
                candidateId={key}
                selected={selected === key}
                onSelect={onSelect}
              />
            );
          })}
        {!query.isFetching &&
          !query.isError &&
          query.data?.unsupported_candidates.map((candidate, index) => (
            <UnsupportedSubtitleEntry
              key={`unsupported-${candidateKey(candidate, index)}`}
              candidate={candidate}
            />
          ))}
      </div>
    </section>
  );
}

function candidateKey(
  candidate: SubtitleCandidate | UnsupportedSubtitleCandidate,
  index: number,
) {
  return `${candidate.kind}-${candidate.path ?? candidate.stream_index ?? index}`;
}

function subtitleLabel(candidate: SubtitleCandidate) {
  const tags = candidate.tags ?? {};
  return (
    [tags.language, tags.title].filter(Boolean).join(" / ") || "Metadata unavailable"
  );
}

function subtitlePath(candidate: SubtitleCandidate) {
  if (candidate.kind !== "external" || !candidate.path) {
    return null;
  }
  return candidate.path.split("/").pop() ?? candidate.path;
}

function subtitleAccessibleLabel(candidate: SubtitleCandidate) {
  const path = subtitlePath(candidate);
  return path ? `${subtitleLabel(candidate)} (${path})` : subtitleLabel(candidate);
}

function SubtitleEntry({
  candidate,
  candidateId,
  selected,
  onSelect,
}: {
  candidate: SubtitleCandidate;
  candidateId: string;
  selected: boolean;
  onSelect: (value: string) => void;
}) {
  return (
    <Button
      type="button"
      variant="outline"
      className="subtitle-entry"
      aria-pressed={selected}
      aria-label={`Select ${candidate.kind} subtitle ${subtitleAccessibleLabel(candidate)}`}
      onClick={() => onSelect(candidateId)}
    >
      <span className="subtitle-kind">
        {candidate.kind === "external" ? "External" : "Embedded"}
      </span>
      <span className="subtitle-copy">
        <strong>{subtitleLabel(candidate)}</strong>
        <small>
          {candidate.format?.toUpperCase() ?? "Unknown format"}
          {subtitlePath(candidate) && ` · ${subtitlePath(candidate)}`}
        </small>
      </span>
      {selected && <span className="media-entry-selected">Selected</span>}
    </Button>
  );
}

function UnsupportedSubtitleEntry({
  candidate,
}: {
  candidate: UnsupportedSubtitleCandidate;
}) {
  return (
    <div
      className="subtitle-entry unsupported"
      role="group"
      aria-disabled="true"
      aria-label={`Unsupported ${candidate.kind} subtitle`}
    >
      <span className="subtitle-kind">
        {candidate.kind === "external" ? "External" : "Embedded"}
      </span>
      <span className="subtitle-copy">
        <strong>Unavailable subtitle</strong>
        <small>{candidate.reason}</small>
      </span>
      <span className="disabled-note">Not selectable</span>
    </div>
  );
}

function MediaBrowser({
  directory,
  filter,
  onDirectoryChange,
  onFilterChange,
  selectedMedia,
  onMediaSelect,
  query,
}: {
  directory: string;
  filter: string;
  onDirectoryChange: (path: string) => void;
  onFilterChange: (filter: string) => void;
  selectedMedia: string | null;
  onMediaSelect: (path: string) => void;
  query: ReturnType<typeof useMediaDirectory>;
}) {
  const entries = query.data?.entries.filter((entry) =>
    entry.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase()),
  );
  return (
    <div className="media-browser">
      <div className="breadcrumbs" role="group" aria-label="Media breadcrumbs">
        <Button
          type="button"
          variant="outline"
          className="breadcrumb-button"
          onClick={() => onDirectoryChange("")}
        >
          Media
        </Button>
        {directory
          .split("/")
          .filter(Boolean)
          .map((part, index, parts) => {
            const path = parts.slice(0, index + 1).join("/");
            return (
              <span key={path} className="breadcrumb-item">
                <span aria-hidden="true">/</span>
                <Button
                  type="button"
                  variant="outline"
                  className="breadcrumb-button"
                  onClick={() => onDirectoryChange(path)}
                >
                  {part}
                </Button>
              </span>
            );
          })}
      </div>
      <label className="media-filter">
        <span>Filter this directory</span>
        <input
          type="search"
          value={filter}
          onChange={(event) => onFilterChange(event.target.value)}
          placeholder="Type a name"
        />
      </label>
      <div className="media-results" aria-live="polite">
        {query.isPending && (
          <div role="status" className="browser-message">
            Loading Media...
          </div>
        )}
        {query.isError && (
          <QueryErrorMessage
            message={query.error.message}
            onRetry={() => void query.refetch()}
          />
        )}
        {query.data && entries?.length === 0 && (
          <EmptyMessage>
            {filter ? "No matching Media or directories." : "This directory is empty."}
          </EmptyMessage>
        )}
        {entries?.map((entry) => (
          <MediaEntry
            key={entry.path}
            entry={entry}
            onDirectoryChange={onDirectoryChange}
            selected={selectedMedia === entry.path}
            onMediaSelect={onMediaSelect}
          />
        ))}
      </div>
    </div>
  );
}

function EmptyMessage({ children }: { children: string }) {
  return <div className="browser-message">{children}</div>;
}

function QueryErrorMessage({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div role="alert" className="browser-message error">
      {message}
      <Button variant="outline" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}

function MediaEntry({
  entry,
  onDirectoryChange,
  selected,
  onMediaSelect,
}: {
  entry: MediaDirectoryEntry;
  onDirectoryChange: (path: string) => void;
  selected: boolean;
  onMediaSelect: (path: string) => void;
}) {
  const isDirectory = entry.kind === "directory";
  const label = entry.title
    ? `${entry.title}${entry.year ? ` (${entry.year})` : ""}`
    : entry.name;
  const accessibleLabel = label === entry.name ? label : `${label} (${entry.name})`;
  return (
    <Button
      type="button"
      variant="outline"
      className="media-entry"
      onClick={() =>
        isDirectory ? onDirectoryChange(entry.path) : onMediaSelect(entry.path)
      }
      aria-pressed={!isDirectory ? selected : undefined}
      aria-label={isDirectory ? `Open ${accessibleLabel}` : `Select ${accessibleLabel}`}
    >
      <span className="media-entry-kind">{isDirectory ? "Directory" : "Media"}</span>
      <span className="media-entry-copy">
        <strong title={entry.name}>{label}</strong>
        {entry.title && <small title={entry.name}>{entry.name}</small>}
      </span>
      {!isDirectory && selected && (
        <span className="media-entry-selected">Selected</span>
      )}
      {isDirectory && <span aria-hidden="true">-&gt;</span>}
    </Button>
  );
}

function ProviderState() {
  const status = useProductStatus();
  if (status.isPending) {
    return (
      <div role="status" className="provider-state">
        <SpinnerGapIcon className="spin" size={18} /> Checking provider
      </div>
    );
  }
  if (status.isError) {
    return (
      <div role="alert" className="provider-state error">
        <WarningCircleIcon size={18} /> {status.error.message}
      </div>
    );
  }
  if (!status.data.translation_provider.ready) {
    return (
      <div role="status" className="provider-state warning">
        <WarningCircleIcon size={18} />
        {status.data.translation_provider.message}
      </div>
    );
  }
  return (
    <div role="status" className="provider-state ready">
      <CheckCircleIcon size={18} weight="fill" /> Translation provider ready
    </div>
  );
}

function JobsPage() {
  const jobs = useJobs();
  const retryJob = useRetryJob();
  return (
    <>
      <PageHeader title="Jobs" detail="Track queued and completed translation work." />
      <section className="job-list" aria-label="Translation jobs">
        {jobs.isPending && (
          <div className="inline-state" role="status">
            Loading Jobs
          </div>
        )}
        {jobs.isError && (
          <div className="inline-state error" role="alert">
            {jobs.error.message}
          </div>
        )}
        {!jobs.isPending && !jobs.isError && jobs.data.length === 0 && (
          <div className="empty-state">
            <span className="empty-icon">
              <BriefcaseIcon size={22} aria-hidden="true" />
            </span>
            <h2>No jobs yet</h2>
            <p>Submitted translations will appear here with their current state.</p>
          </div>
        )}
        {(jobs.data ?? []).map((job) => (
          <article className="job-item" key={job.id}>
            <div>
              <small className="job-id">Job {job.id.slice(0, 8)}</small>
              <strong>{job.request.media_path}</strong>
              <p>
                {job.request.subtitle_path
                  ? job.request.subtitle_path
                  : `Embedded stream ${job.request.stream_index}`}{" "}
                to {job.request.target_language_code}
              </p>
              {job.request.term_map && (
                <small>Term map: {job.request.term_map.name}</small>
              )}
              {job.attempt > 1 && <small>Attempt {job.attempt}</small>}
              {job.queue_position !== null && job.queue_position !== undefined && (
                <small>Queue position {job.queue_position}</small>
              )}
            </div>
            <span className={`job-status status-${job.status.toLowerCase()}`}>
              {job.status}
            </span>
            {job.error && <p className="form-error">{job.error.message}</p>}
            {(job.status === "Failed" || job.status === "Interrupted") &&
              job.request.subtitle_path && (
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={retryJob.isPending}
                    onClick={() => retryJob.mutate(job.id)}
                  >
                    {retryJob.isPending && retryJob.variables === job.id
                      ? "Retrying..."
                      : "Retry"}
                  </Button>
                  {retryJob.isError && retryJob.variables === job.id && (
                    <p className="form-error" role="alert">
                      {retryJob.error.message}
                    </p>
                  )}
                </div>
              )}
            {job.error && (
              <details className="job-error-details">
                <summary>Show error details</summary>
                <dl>
                  <div>
                    <dt>Code</dt>
                    <dd>{job.error.code}</dd>
                  </div>
                  {Object.entries(job.error)
                    .filter(([key]) =>
                      [
                        "field",
                        "media_path",
                        "output_path",
                        "path",
                        "stream_index",
                      ].includes(key),
                    )
                    .map(([key, value]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>{String(value)}</dd>
                      </div>
                    ))}
                </dl>
              </details>
            )}
          </article>
        ))}
      </section>
    </>
  );
}

function TermMapsPage() {
  const maps = useTermMaps();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const selected = useTermMap(selectedId);
  const create = useCreateTermMap();
  const rename = useRenameTermMap();
  const replace = useReplaceTermMap();
  const remove = useDeleteTermMap();
  const [name, setName] = useState("");
  const [content, setContent] = useState('{\n  "Source": "Target"\n}');
  const [renameName, setRenameName] = useState("");
  const [replacement, setReplacement] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const selectedIdRef = useRef(selectedId);
  const resetRename = rename.reset;
  const resetReplace = replace.reset;
  const resetRemove = remove.reset;

  useEffect(() => {
    selectedIdRef.current = selectedId;
    resetRename();
    resetReplace();
    resetRemove();
  }, [resetRemove, resetRename, resetReplace, selectedId]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate(
      { name, content },
      {
        onSuccess: () => {
          setName("");
          setContent('{\n  "Source": "Target"\n}');
        },
      },
    );
  }

  const entries = selected.data
    ? Object.entries(selected.data.content).filter(([source, target]) =>
        `${source} ${target}`
          .toLocaleLowerCase()
          .includes(deferredSearch.toLocaleLowerCase()),
      )
    : [];

  function renameSelected() {
    if (!selectedId || !renameName.trim()) return;
    rename.mutate(
      { id: selectedId, name: renameName },
      {
        onSuccess: (summary) => {
          if (selectedIdRef.current === selectedId) setRenameName(summary.name);
        },
      },
    );
  }

  function deleteSelected() {
    if (!selectedId || !selected.data || confirmation !== selected.data.name) return;
    remove.mutate(
      { id: selectedId, name: confirmation },
      {
        onSuccess: () => {
          if (selectedIdRef.current === selectedId) {
            selectedIdRef.current = null;
            setSelectedId(null);
            setConfirmation("");
          }
        },
      },
    );
  }

  return (
    <>
      <PageHeader
        title="Term maps"
        detail="Keep reusable terminology precise and available across translations."
      />
      <div className="term-map-layout">
        <section className="term-map-upload" aria-labelledby="upload-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">New resource</p>
              <h2 id="upload-title">Upload a Term map</h2>
            </div>
            <UploadSimpleIcon size={20} aria-hidden="true" />
          </div>
          <form onSubmit={submit}>
            <label>
              Name
              <Input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Character names"
              />
            </label>
            <label>
              JSON content
              <Textarea
                required
                value={content}
                onChange={(event) => setContent(event.target.value)}
                rows={6}
                spellCheck={false}
                aria-describedby="upload-help"
              />
            </label>
            <p id="upload-help" className="field-help">
              A non-empty object of Source-to-Target strings, up to 1 MiB.
            </p>
            {create.isError && (
              <p className="form-error" role="alert">
                {create.error.message}
              </p>
            )}
            {create.isPending && (
              <p className="upload-status" role="status">
                Uploading Term map
              </p>
            )}
            <Button
              className="primary-action"
              type="submit"
              disabled={create.isPending}
            >
              {create.isPending ? "Uploading..." : "Upload Term map"}
            </Button>
          </form>
        </section>

        <section className="term-map-list" aria-labelledby="maps-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Library</p>
              <h2 id="maps-title">Saved Term maps</h2>
            </div>
            <span className="count-badge">{maps.data?.term_maps?.length ?? 0}</span>
          </div>
          <div className="term-map-list-state">
            {maps.isPending && (
              <div className="inline-state" role="status">
                <SpinnerGapIcon className="spin" /> Loading Term maps
              </div>
            )}
            {maps.isError && (
              <div className="inline-state error" role="alert">
                {maps.error.message}
              </div>
            )}
            {maps.data?.term_maps?.length === 0 && (
              <div className="term-map-empty">
                <ListChecksIcon size={24} aria-hidden="true" />
                <h3>No Term maps yet</h3>
                <p>Upload a JSON Term map to make consistent terminology reusable.</p>
              </div>
            )}
          </div>
          <div className="term-map-items">
            {maps.data?.term_maps?.map((map) => (
              <button
                className={`term-map-item${selectedId === map.id ? " selected" : ""}`}
                aria-label={`${map.name}, ${map.entry_count} ${map.entry_count === 1 ? "entry" : "entries"}`}
                aria-pressed={selectedId === map.id}
                key={map.id}
                type="button"
                onClick={() => {
                  selectedIdRef.current = map.id;
                  setSelectedId(map.id);
                  setRenameName(map.name);
                  setReplacement(null);
                  setConfirmation("");
                }}
              >
                <span className="term-map-item-name" title={map.name}>
                  {map.name}
                </span>
                <span>
                  {map.entry_count} {map.entry_count === 1 ? "entry" : "entries"}
                </span>
                <time dateTime={map.updated_at}>{map.updated_at}</time>
              </button>
            ))}
          </div>
        </section>
      </div>

      {selectedId && (
        <section className="term-map-detail" aria-labelledby="detail-title">
          <div className="detail-header">
            <div>
              <Button
                className="back-action"
                variant="outline"
                type="button"
                onClick={() => {
                  selectedIdRef.current = null;
                  setSelectedId(null);
                  setRenameName("");
                  setReplacement(null);
                  setConfirmation("");
                }}
              >
                <ArrowLeftIcon size={16} aria-hidden="true" /> Back to Term maps
              </Button>
              <h2 id="detail-title">{selected.data?.name ?? "Term map details"}</h2>
              {selected.data && (
                <p>
                  {selected.data.entry_count} entries · Updated{" "}
                  <time dateTime={selected.data.updated_at}>
                    {selected.data.updated_at}
                  </time>
                </p>
              )}
              {selected.data && (
                <div className="term-map-actions">
                  <Input
                    aria-label="New Term map name"
                    value={renameName}
                    placeholder={selected.data.name}
                    onChange={(event) => setRenameName(event.target.value)}
                    disabled={rename.isPending}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={renameSelected}
                    disabled={rename.isPending}
                  >
                    Save name
                  </Button>
                  {rename.isError && (
                    <p className="form-error" role="alert">
                      {rename.error.message}
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="term-map-detail-state">
            {selected.isPending && (
              <div className="inline-state" role="status">
                <SpinnerGapIcon className="spin" /> Loading details
              </div>
            )}
            {selected.isError && (
              <div className="inline-state error" role="alert">
                {selected.error.message}
              </div>
            )}
            {selected.data && (
              <>
                <label className="search-field">
                  <MagnifyingGlassIcon size={17} aria-hidden="true" />
                  <span>Search Source or Target</span>
                  <Input
                    aria-label="Search Source or Target"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search Source or Target"
                  />
                </label>
                <div className="term-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>Target</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map(([source, target]) => (
                        <tr key={source}>
                          <td>{source}</td>
                          <td>{target}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {entries.length === 0 && (
                    <p className="table-empty">No matching terms.</p>
                  )}
                </div>
                <div className="term-map-management">
                  <h3>Replace JSON content</h3>
                  <Textarea
                    aria-label="Replacement JSON content"
                    value={
                      replacement ?? JSON.stringify(selected.data.content, null, 2)
                    }
                    onChange={(event) => setReplacement(event.target.value)}
                    rows={7}
                    spellCheck={false}
                    disabled={replace.isPending}
                  />
                  {replace.isError && (
                    <p className="form-error" role="alert">
                      {replace.error.message}
                    </p>
                  )}
                  <Button
                    type="button"
                    className="primary-action"
                    onClick={() =>
                      replace.mutate(
                        {
                          id: selected.data.id,
                          content: replacement ?? JSON.stringify(selected.data.content),
                        },
                        {
                          onSuccess: () => {
                            if (selectedIdRef.current === selected.data.id) {
                              setReplacement(null);
                            }
                          },
                        },
                      )
                    }
                    disabled={replace.isPending}
                  >
                    {replace.isPending ? "Replacing..." : "Replace content"}
                  </Button>
                  <div className="term-map-delete">
                    <h3>Delete Term map</h3>
                    <p>
                      Enter &quot;{selected.data.name}&quot; to confirm permanent
                      deletion.
                    </p>
                    <Input
                      aria-label="Confirm Term map name"
                      value={confirmation}
                      onChange={(event) => setConfirmation(event.target.value)}
                      placeholder={selected.data.name}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      onClick={deleteSelected}
                      disabled={remove.isPending || confirmation !== selected.data.name}
                    >
                      {remove.isPending ? "Deleting..." : "Delete Term map"}
                    </Button>
                    {remove.isError && (
                      <p className="form-error" role="alert">
                        {remove.error.message}
                      </p>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </section>
      )}
    </>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/translate" replace />} />
        <Route path="translate" element={<Translate />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="term-maps" element={<TermMapsPage />} />
        <Route path="*" element={<Navigate to="/translate" replace />} />
      </Route>
    </Routes>
  );
}
