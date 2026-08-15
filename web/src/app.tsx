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
import {
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type FormEvent,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";

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
import { jobRecordAttention, useProductStatus } from "./status";
import { useCreateJob, useJobs, useJobNotifications } from "./jobs";
import { JobNotificationRegion, JobsPage } from "./job-history";
import { COMMON_TARGET_LANGUAGES } from "./languages";
import {
  useCreateTermMap,
  useDeleteTermMap,
  useRenameTermMap,
  useReplaceTermMap,
  useTermMap,
  useTermMaps,
  validateTermMapContent,
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
  const jobs = useJobs();
  const jobNotifications = useJobNotifications(jobs.data);
  const ready =
    status.data?.api.ready &&
    status.data?.roots.ready &&
    status.data.translation_provider.ready;
  const recordsNeedAttention = jobRecordAttention(status.data);
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
            : status.data && !status.data.translation_provider.ready
              ? "Provider needs configuration"
              : ready
                ? "Runtime ready"
                : "Runtime unavailable"}
        </div>
        {recordsNeedAttention && (
          <div className="runtime-warning" role="status">
            Job records need attention
          </div>
        )}
      </aside>
      <main className="workspace">
        <Outlet />
      </main>
      <Navigation mobile />
      <JobNotificationRegion {...jobNotifications} />
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
    </header>
  );
}

function Translate() {
  const queryClient = useQueryClient();
  const createJob = useCreateJob();
  const navigate = useNavigate();
  const status = useProductStatus();
  const [directory, setDirectory] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedMedia, setSelectedMedia] = useState<string | null>(null);
  const [selectedSubtitle, setSelectedSubtitle] = useState<string | null>(null);
  const [targetLanguage, setTargetLanguage] = useState(
    () => window.localStorage.getItem("cueweaver.target-language") ?? "",
  );
  const [outputSuffix, setOutputSuffix] = useState(() => targetLanguage);
  const commonTargetLanguage = COMMON_TARGET_LANGUAGES.some(
    ({ code }) => code === targetLanguage,
  )
    ? targetLanguage
    : "custom";
  const customTargetLanguage = commonTargetLanguage === "custom";
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
  const updateTargetLanguage = (value: string) => {
    setTargetLanguage(value);
    if (!suffixEdited.current) setOutputSuffix(value);
  };
  const canSubmit =
    selectedMedia !== null &&
    ((selectedCandidate?.kind === "external" && selectedCandidate.path !== undefined) ||
      (selectedCandidate?.kind === "embedded" &&
        selectedCandidate.stream_index !== undefined &&
        selectedCandidate.format !== undefined)) &&
    targetLanguage.trim() !== "" &&
    outputSuffixError === null &&
    status.data?.translation_provider.ready === true &&
    !createJob.isSuccess &&
    !createJob.isPending;

  const resetTranslationWorkflow = () => {
    clearMedia(selectedMedia);
    setDynamicTerminologyEnabled(true);
    setSubtitleTerminologyFilterEnabled(true);
    setOutputSuffix(targetLanguage);
    setOutputConflictPolicy("append-number");
    suffixEdited.current = false;
    createJob.reset();
  };

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
          <label htmlFor="common-target-language">
            Common target language
            <select
              id="common-target-language"
              value={commonTargetLanguage}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "custom") {
                  updateTargetLanguage("");
                } else {
                  updateTargetLanguage(value);
                }
              }}
              disabled={selectedCandidate === undefined}
            >
              <option value="" disabled>
                Choose a language
              </option>
              <option value="custom">Custom language code</option>
              {COMMON_TARGET_LANGUAGES.map(({ code, label }) => (
                <option key={code} value={code}>
                  {label} — {code}
                </option>
              ))}
            </select>
          </label>
          {customTargetLanguage && (
            <label htmlFor="target-language-code" className="custom-language-field">
              Target language code
              <Input
                id="target-language-code"
                required
                aria-describedby="target-language-help"
                value={targetLanguage}
                onChange={(event) => updateTargetLanguage(event.target.value)}
                placeholder="zh-Hans"
                disabled={selectedCandidate === undefined}
              />
            </label>
          )}
          <span id="target-language-help" className="field-help">
            Choose a common language or select Custom language code.
          </span>
          <label className="term-map-field">
            Term map
            <select
              id="term-map-select"
              aria-label="Term map"
              value={selectedTermMapId ?? ""}
              onChange={(event) => setTermMapId(event.target.value || null)}
              disabled={termMaps.isPending || termMaps.isError}
            >
              <option value="">No Term map</option>
              {(termMaps.data?.term_maps ?? []).map((termMap) => (
                <option key={termMap.id} value={termMap.id}>
                  {termMap.name}
                </option>
              ))}
            </select>
            {termMaps.isPending && (
              <span className="field-help" role="status">
                Loading Term maps
              </span>
            )}
            {termMaps.isError && (
              <span className="form-error" role="alert">
                {termMaps.error.message}
              </span>
            )}
          </label>
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
                <output
                  aria-label="Media stem"
                  className="form-control output-name-stem"
                >
                  {`${outputParts.stem}.`}
                </output>
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
                <output
                  aria-label="Source format extension"
                  className="form-control output-name-extension"
                >
                  {`.${outputParts.format}`}
                </output>
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
      <div className={cn("submission-bar", createJob.isSuccess && "queued")}>
        {createJob.isSuccess ? (
          <QueueSuccess
            job={createJob.data}
            onViewJob={() => {
              if (createJob.data?.id)
                navigate(`/jobs/${encodeURIComponent(createJob.data.id)}`);
            }}
            onTranslateAnother={resetTranslationWorkflow}
          />
        ) : (
          <>
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
                    subtitle_terminology_filter_enabled:
                      subtitleTerminologyFilterEnabled,
                  };
                  createJob.mutate(request, {
                    onSuccess: () => {
                      window.localStorage.setItem(
                        "cueweaver.target-language",
                        targetLanguage,
                      );
                    },
                  });
                }
              }}
            >
              {createJob.isPending ? "Queueing..." : "Start translation"}
            </Button>
          </>
        )}
      </div>
      {createJob.isError && (
        <p className="form-error" role="alert">
          {createJob.error.message}
        </p>
      )}
    </>
  );
}

function QueueSuccess({
  job,
  onViewJob,
  onTranslateAnother,
}: {
  job: ReturnType<typeof useCreateJob>["data"];
  onViewJob: () => void;
  onTranslateAnother: () => void;
}) {
  const media = job?.request?.media_path ?? "Media";
  const targetLanguage =
    job?.request?.target_language_code ?? "Target language unavailable";

  return (
    <section className="queue-success" aria-labelledby="queue-success-title">
      <div className="queue-success-heading" role="status">
        <CheckCircleIcon size={22} weight="fill" aria-hidden="true" />
        <div>
          <p className="eyebrow">Queued Job</p>
          <h2 id="queue-success-title">Translation queued</h2>
          <p>The translation is ready to run in the queue.</p>
        </div>
      </div>
      <dl className="queue-success-summary">
        <div>
          <dt>Media</dt>
          <dd title={media}>{media}</dd>
        </div>
        <div>
          <dt>Target language</dt>
          <dd>{targetLanguage}</dd>
        </div>
      </dl>
      <div className="queue-success-actions">
        {job?.id && (
          <Button type="button" onClick={onViewJob}>
            View Job
          </Button>
        )}
        <Button type="button" variant="outline" onClick={onTranslateAnother}>
          Translate another
        </Button>
      </div>
    </section>
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

const SUBTITLE_DISPOSITION_LABELS: Record<string, string> = {
  default: "Default",
  forced: "Forced",
  hearing_impaired: "Hearing impaired",
  visual_impaired: "Visually impaired",
  comment: "Commentary",
  lyrics: "Lyrics",
  karaoke: "Karaoke",
  original: "Original",
  dub: "Dubbed",
  clean_effects: "Clean effects",
};

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

function subtitleDetails(candidate: SubtitleCandidate) {
  const details = [candidate.format?.toUpperCase() ?? "Unknown format"];
  if (candidate.kind === "embedded" && candidate.stream_index !== undefined) {
    details.push(`Stream ${candidate.stream_index}`);
    details.push(
      ...(candidate.dispositions ?? [])
        .map((disposition) => SUBTITLE_DISPOSITION_LABELS[disposition])
        .filter((label): label is string => label !== undefined),
    );
  } else if (subtitlePath(candidate)) {
    details.push(subtitlePath(candidate)!);
  }
  return details.join(" · ");
}

function subtitleAccessibleLabel(candidate: SubtitleCandidate) {
  const path = subtitlePath(candidate);
  const stream =
    candidate.kind === "embedded" && candidate.stream_index !== undefined
      ? `stream ${candidate.stream_index} `
      : "";
  return path
    ? `${stream}${subtitleLabel(candidate)} (${path})`
    : `${stream}${subtitleLabel(candidate)}`;
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
      className={cn("subtitle-entry", candidate.kind === "embedded" && "embedded")}
      aria-pressed={selected}
      aria-label={`Select ${candidate.kind} subtitle ${subtitleAccessibleLabel(candidate)}`}
      onClick={() => onSelect(candidateId)}
    >
      <span className="subtitle-kind">
        {candidate.kind === "external" ? "External" : "Embedded"}
      </span>
      <span className="subtitle-copy">
        <strong>{subtitleLabel(candidate)}</strong>
        <small>{subtitleDetails(candidate)}</small>
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
  const episodeLabel =
    entry.season !== undefined && entry.episode !== undefined
      ? `S${String(entry.season).padStart(2, "0")}E${String(entry.episode).padStart(2, "0")}${entry.title ? ` · ${entry.title}` : ""}`
      : null;
  const label =
    episodeLabel ??
    (entry.title
      ? `${entry.title}${entry.year ? ` (${entry.year})` : ""}`
      : entry.name);
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
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileReadGeneration = useRef(0);
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

  const contentValidation = useMemo(() => validateTermMapContent(content), [content]);
  const contentError = fileError ?? contentValidation.error;

  async function loadTermMapFile(file: File) {
    const generation = ++fileReadGeneration.current;
    if (!file.name.toLocaleLowerCase().endsWith(".json")) {
      setFileError("Choose a .json file containing a Term map.");
      setFileName(null);
      setFileLoading(false);
      return;
    }
    setFileLoading(true);
    setFileError(null);
    try {
      const fileContent = await readTextFile(file);
      if (generation !== fileReadGeneration.current) return;
      setContent(fileContent);
      setFileName(file.name);
      setFileError(null);
    } catch {
      if (generation !== fileReadGeneration.current) return;
      setFileName(null);
      setFileError("The selected JSON file could not be read.");
    } finally {
      if (generation === fileReadGeneration.current) setFileLoading(false);
    }
  }

  function readTextFile(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result === "string") resolve(reader.result);
        else reject(new Error("File content is not text"));
      };
      reader.onerror = () =>
        reject(reader.error ?? new Error("File could not be read"));
      reader.readAsText(file);
    });
  }

  function handleFileDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files[0];
    if (file) void loadTermMapFile(file);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (fileLoading || contentError !== null) return;
    create.mutate(
      { name, content },
      {
        onSuccess: () => {
          fileReadGeneration.current += 1;
          setFileLoading(false);
          setName("");
          setContent('{\n  "Source": "Target"\n}');
          setFileName(null);
          setFileError(null);
          if (fileInputRef.current) fileInputRef.current.value = "";
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
                placeholder="e.g. Character names"
              />
            </label>
            <div
              className="term-map-dropzone"
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleFileDrop}
            >
              <strong>Import JSON file</strong>
              <span>Drop a .json file here, or select one.</span>
              <Button
                type="button"
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
              >
                Select JSON file
              </Button>
              <input
                ref={fileInputRef}
                className="sr-only"
                type="file"
                accept=".json,application/json"
                aria-label="JSON file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void loadTermMapFile(file);
                }}
              />
              {fileName && <span className="field-help">Loaded {fileName}</span>}
            </div>
            <label htmlFor="term-map-content">
              Paste JSON
              <Textarea
                id="term-map-content"
                aria-label="JSON content"
                required
                value={content}
                onChange={(event) => {
                  fileReadGeneration.current += 1;
                  setContent(event.target.value);
                  setFileName(null);
                  setFileError(null);
                  setFileLoading(false);
                }}
                rows={6}
                spellCheck={false}
                aria-describedby="upload-help"
              />
            </label>
            <p id="upload-help" className="field-help">
              A non-empty object of Source-to-Target strings, up to 1 MiB.
            </p>
            {fileLoading ? (
              <p className="upload-status" role="status">
                Reading JSON file...
              </p>
            ) : contentError ? (
              <p className="form-error" role="alert">
                {contentError}
              </p>
            ) : (
              <p className="term-map-validation valid" role="status">
                Valid Term map: {contentValidation.entryCount}{" "}
                {contentValidation.entryCount === 1 ? "mapping" : "mappings"}.
              </p>
            )}
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
              disabled={create.isPending || fileLoading || contentError !== null}
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
                <Button variant="outline" onClick={() => void maps.refetch()}>
                  Try again
                </Button>
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
                <Button variant="outline" onClick={() => void selected.refetch()}>
                  Try again
                </Button>
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
        <Route path="jobs/:jobId" element={<JobsPage />} />
        <Route path="term-maps" element={<TermMapsPage />} />
        <Route path="*" element={<Navigate to="/translate" replace />} />
      </Route>
    </Routes>
  );
}
