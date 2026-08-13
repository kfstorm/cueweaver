import {
  BriefcaseIcon,
  CheckCircleIcon,
  GearIcon,
  ListChecksIcon,
  SpinnerGapIcon,
  TranslateIcon,
  WarningCircleIcon,
} from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import { useState } from "react";
import { NavLink, Navigate, Outlet, Route, Routes } from "react-router-dom";

import { Button } from "./components/ui/button";
import { useMediaDirectory, type MediaDirectoryEntry } from "./browse";
import { cn } from "./lib/utils";
import { useProductStatus } from "./status";

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
  const status = useProductStatus();
  const [directory, setDirectory] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedMedia, setSelectedMedia] = useState<string | null>(null);
  const browser = useMediaDirectory(directory);
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
              setSelectedMedia(null);
            }}
            onFilterChange={setFilter}
            selectedMedia={selectedMedia}
            onMediaSelect={setSelectedMedia}
            query={browser}
          />
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
      aria-label={isDirectory ? `Open ${label}` : `Select ${label}`}
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
          element={
            <EmptyPage
              title="Term maps"
              detail="Manage reusable terminology for consistent translations."
              emptyTitle="No Term maps"
              emptyDetail="Uploaded terminology will be available for future translations."
              icon={GearIcon}
            />
          }
        />
        <Route path="*" element={<Navigate to="/translate" replace />} />
      </Route>
    </Routes>
  );
}
