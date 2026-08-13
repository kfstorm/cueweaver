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
import { useDeferredValue, useState, type FormEvent } from "react";
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
import { useCreateTermMap, useTermMap, useTermMaps } from "./term-maps";

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
  const status = useProductStatus();
  const [directory, setDirectory] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedMedia, setSelectedMedia] = useState<string | null>(null);
  const [selectedSubtitle, setSelectedSubtitle] = useState<string | null>(null);
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
          <p>Select media first to choose a source, language, and Term map.</p>
        </div>
      </section>
      <div className="submission-bar">
        <ProviderState />
        <Button disabled>
          Start translation
        </Button>
      </div>
    </>
  );
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
          <div role="status" className="discovery-skeleton" aria-label="Loading subtitles">
            <span /><span /><span />
          </div>
        )}
        {query.isError && (
          <div role="alert" className="browser-message error">
            {query.error.message}
            <Button variant="outline" onClick={() => void query.refetch()}>
              Try again
            </Button>
          </div>
        )}
        {!query.isFetching && query.data &&
          query.data.candidates.length === 0 &&
          query.data.unsupported_candidates.length === 0 && (
            <div className="browser-message">No subtitles were found for this Media.</div>
          )}
        {!query.isFetching && !query.isError && query.data?.candidates.map((candidate, index) => {
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
        {!query.isFetching && !query.isError && query.data?.unsupported_candidates.map((candidate, index) => (
          <UnsupportedSubtitleEntry
            key={`unsupported-${candidateKey(candidate, index)}`}
            candidate={candidate}
          />
        ))}
      </div>
    </section>
  );
}

function candidateKey(candidate: SubtitleCandidate | UnsupportedSubtitleCandidate, index: number) {
  return `${candidate.kind}-${candidate.path ?? candidate.stream_index ?? index}`;
}

function subtitleLabel(candidate: SubtitleCandidate) {
  const tags = candidate.tags ?? {};
  return [tags.language, tags.title].filter(Boolean).join(" / ") || "Metadata unavailable";
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
      <span className="subtitle-kind">{candidate.kind === "external" ? "External" : "Embedded"}</span>
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
      <span className="subtitle-kind">{candidate.kind === "external" ? "External" : "Embedded"}</span>
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
        {directory.split("/").filter(Boolean).map((part, index, parts) => {
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
        {query.isPending && <div role="status" className="browser-message">Loading Media...</div>}
        {query.isError && (
          <div role="alert" className="browser-message error">
            {query.error.message}
            <Button variant="outline" onClick={() => void query.refetch()}>
              Try again
            </Button>
          </div>
        )}
        {query.data && entries?.length === 0 && (
          <div className="browser-message">
            {filter ? "No matching Media or directories." : "This directory is empty."}
          </div>
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
        isDirectory
          ? onDirectoryChange(entry.path)
          : onMediaSelect(entry.path)
      }
      aria-pressed={!isDirectory ? selected : undefined}
      aria-label={isDirectory ? `Open ${accessibleLabel}` : `Select ${accessibleLabel}`}
    >
      <span className="media-entry-kind">{isDirectory ? "Directory" : "Media"}</span>
      <span className="media-entry-copy">
        <strong title={entry.name}>{label}</strong>
        {entry.title && <small title={entry.name}>{entry.name}</small>}
      </span>
      {!isDirectory && selected && <span className="media-entry-selected">Selected</span>}
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

function EmptyPage({
  title,
  detail,
  emptyTitle,
  emptyDetail,
  icon: EmptyIcon,
}: {
  title: string;
  detail: string;
  emptyTitle: string;
  emptyDetail: string;
  icon: Icon;
}) {
  return (
    <>
      <PageHeader title={title} detail={detail} />
      <section className="empty-state">
        <span className="empty-icon">
          <EmptyIcon size={22} aria-hidden="true" />
        </span>
        <h2>{emptyTitle}</h2>
        <p>{emptyDetail}</p>
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
  const [name, setName] = useState("");
  const [content, setContent] = useState('{\n  "Source": "Target"\n}');

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate({ name, content }, {
      onSuccess: () => {
        setName("");
        setContent('{\n  "Source": "Target"\n}');
      },
    });
  }

  const entries = selected.data
    ? Object.entries(selected.data.content).filter(([source, target]) =>
        `${source} ${target}`.toLocaleLowerCase().includes(deferredSearch.toLocaleLowerCase()),
      )
    : [];

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
            {create.isError && <p className="form-error" role="alert">{create.error.message}</p>}
            {create.isPending && <p className="upload-status" role="status">Uploading Term map</p>}
            <Button className="primary-action" type="submit" disabled={create.isPending}>
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
            {maps.isPending && <div className="inline-state" role="status"><SpinnerGapIcon className="spin" /> Loading Term maps</div>}
            {maps.isError && <div className="inline-state error" role="alert">{maps.error.message}</div>}
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
                onClick={() => setSelectedId(map.id)}
              >
                <span className="term-map-item-name" title={map.name}>{map.name}</span>
                <span>{map.entry_count} {map.entry_count === 1 ? "entry" : "entries"}</span>
                <time dateTime={map.updated_at}>{new Date(map.updated_at).toLocaleString()}</time>
              </button>
            ))}
          </div>
        </section>
      </div>

      {selectedId && (
        <section className="term-map-detail" aria-labelledby="detail-title">
          <div className="detail-header">
            <div>
              <Button className="back-action" variant="outline" type="button" onClick={() => setSelectedId(null)}>
                <ArrowLeftIcon size={16} aria-hidden="true" /> Back to Term maps
              </Button>
              <h2 id="detail-title">{selected.data?.name ?? "Term map details"}</h2>
              {selected.data && <p>{selected.data.entry_count} entries, read-only</p>}
            </div>
          </div>
          <div className="term-map-detail-state">
            {selected.isPending && <div className="inline-state" role="status"><SpinnerGapIcon className="spin" /> Loading details</div>}
            {selected.isError && <div className="inline-state error" role="alert">{selected.error.message}</div>}
            {selected.data && (
              <>
                <label className="search-field">
                  <MagnifyingGlassIcon size={17} aria-hidden="true" />
                  <span>Search Source or Target</span>
                  <Input aria-label="Search Source or Target" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search Source or Target" />
                </label>
                <div className="term-table-wrap">
                  <table>
                    <thead><tr><th>Source</th><th>Target</th></tr></thead>
                    <tbody>
                      {entries.map(([source, target]) => <tr key={source}><td>{source}</td><td>{target}</td></tr>)}
                    </tbody>
                  </table>
                  {entries.length === 0 && <p className="table-empty">No matching terms.</p>}
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
        <Route
          path="jobs"
          element={
            <EmptyPage
              title="Jobs"
              detail="Track queued and completed translation work."
              emptyTitle="No jobs yet"
              emptyDetail="Submitted translations will appear here with their current state."
              icon={BriefcaseIcon}
            />
          }
        />
        <Route
          path="term-maps"
          element={<TermMapsPage />}
        />
        <Route path="*" element={<Navigate to="/translate" replace />} />
      </Route>
    </Routes>
  );
}
