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
  type MutableRefObject,
  type ReactNode,
  type RefObject,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  NavLink,
  Navigate,
  Outlet,
  Link,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";

import { Button } from "./components/ui/button";
import { PageHeader } from "./components/page-header";
import { Guidance, QuickStart } from "./components/ui/guidance";
import { Input, Select, Textarea } from "./components/ui/input";
import { LocalizedErrorMessage } from "./components/ui/localized-error-message";
import {
  useMediaDirectory,
  useMediaDiscovery,
  useMediaDiscoveries,
  type MediaDirectoryEntry,
  type SubtitleCandidate,
  type UnsupportedSubtitleCandidate,
} from "./browse";
import { cn, formatLocalTimestamp, formatRelativeTimestamp } from "./lib/utils";
import { useProductStatus } from "./status";
import {
  useCreateBatchJobs,
  useCreateJob,
  useJobs,
  useJobNotifications,
  type BatchJobError,
  type BatchJobSkipped,
  type BatchJobResult,
  type Job,
  APPROVED_ERROR_CONTEXT_KEYS,
  isSkippedJobResult,
  type OutputConflictPolicy,
  type SkippedJobResult,
  type TermMapMode,
} from "./jobs";
import { JobNotificationRegion, JobsPage, SummaryItem } from "./job-history";
import { ThemeProvider } from "./theme-provider";
import { ThemeToggle } from "./theme-toggle";
import {
  formatError,
  getErrorDetail,
  I18nProvider,
  useI18n,
  type TranslationKey,
} from "./i18n";
import { COMMON_TARGET_LANGUAGES, localizedLanguageLabel } from "./languages";
import {
  useCreateTermMap,
  useDeleteTermMap,
  useBindDirectoryTermMap,
  useDirectoryTermMap,
  useRenameTermMap,
  useRemoveDirectoryTermMap,
  useReplaceTermMap,
  useTermMap,
  useTermMaps,
  validateTermMapContent,
} from "./term-maps";

const routes: Array<{
  labelKey: "navigation.translate" | "navigation.jobs" | "navigation.termMaps";
  path: string;
  icon: Icon;
}> = [
  { labelKey: "navigation.translate", path: "/translate", icon: TranslateIcon },
  { labelKey: "navigation.jobs", path: "/jobs", icon: BriefcaseIcon },
  { labelKey: "navigation.termMaps", path: "/term-maps", icon: ListChecksIcon },
];
const DIRECTORY_TERM_MAP_VALUE = "__directory_default__";
const TARGET_LANGUAGE_STORAGE_KEY = "cueweaver.target-language";

function readTargetLanguage(): string {
  try {
    return window.localStorage.getItem(TARGET_LANGUAGE_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

function storeTargetLanguage(value: string): void {
  try {
    window.localStorage.setItem(TARGET_LANGUAGE_STORAGE_KEY, value);
  } catch {
    // The translation workflow does not depend on persistence.
  }
}

function Navigation({ mobile = false }: { mobile?: boolean }) {
  const { t } = useI18n();
  return (
    <nav
      aria-label={t(mobile ? "navigation.mobile" : "navigation.primary")}
      className={mobile ? "mobile-nav" : "desktop-nav"}
    >
      {routes.map(({ labelKey, path, icon: RouteIcon }) => (
        <NavLink
          key={path}
          to={path}
          className={({ isActive }) => cn("nav-link", isActive && "active")}
        >
          <RouteIcon aria-hidden="true" size={18} weight="regular" />
          <span>{t(labelKey)}</span>
        </NavLink>
      ))}
      {mobile && <LanguageSelector />}
    </nav>
  );
}

function LanguageSelector() {
  const { locale, setLocale, t, localeOptions } = useI18n();
  return (
    <label className="language-selector">
      <span className="language-selector-label">{t("language.label")}</span>
      <Select
        value={locale}
        aria-label={t("language.change")}
        onChange={(event) => setLocale(event.target.value as typeof locale)}
      >
        {localeOptions.map((option) => (
          <option key={option.code} value={option.code}>
            {option.label}
          </option>
        ))}
      </Select>
    </label>
  );
}

function Shell() {
  const { t } = useI18n();
  const status = useProductStatus();
  const jobs = useJobs();
  const jobNotifications = useJobNotifications(jobs.data);
  const ready =
    status.data?.api.ready &&
    status.data?.roots.ready &&
    status.data.translation_provider.ready;
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
            ? t("runtime.checking")
            : status.data && !status.data.translation_provider.ready
              ? t("runtime.provider")
              : ready
                ? t("runtime.ready")
                : t("runtime.unavailable")}
        </div>
        <ThemeToggle className="sidebar-theme-toggle" />
        <LanguageSelector />
      </aside>
      <main className="workspace">
        <Outlet />
      </main>
      <Navigation mobile />
      <JobNotificationRegion {...jobNotifications} />
    </div>
  );
}

function TranslationRuntimeNotice() {
  const { t } = useI18n();
  const status = useProductStatus();
  if (status.isPending) return null;
  if (status.isError) {
    return (
      <Guidance
        title={t("runtime.unreachableTitle")}
        tone="error"
        action={
          <Button type="button" variant="outline" onClick={() => void status.refetch()}>
            {t("runtime.tryAgain")}
          </Button>
        }
      >
        {t("runtime.unreachableDetail")}
      </Guidance>
    );
  }
  if (!status.data.api.ready || !status.data.roots.ready) {
    return (
      <Guidance title={t("runtime.attentionTitle")} tone="warning">
        {t("runtime.attentionDetail")}
      </Guidance>
    );
  }
  if (!status.data.translation_provider.ready) {
    return (
      <Guidance title={t("runtime.providerNotConfiguredTitle")} tone="warning">
        {t("runtime.providerNotConfiguredDetail")}
      </Guidance>
    );
  }
  return null;
}

type TranslationStepState = {
  batchMode: boolean;
  selectedMedia: string | null;
  batchMediaCount: number;
  batchReadyCount: number;
  selectedCandidate: SubtitleCandidate | undefined;
  targetLanguage: string;
  outputSuffixError: string | null;
  providerReady: boolean;
  providerPending: boolean;
  runtimeReady: boolean;
  runtimeError: boolean;
};

function getNextTranslationStep({
  batchMode,
  selectedMedia,
  batchMediaCount,
  batchReadyCount,
  selectedCandidate,
  targetLanguage,
  outputSuffixError,
  providerReady,
  providerPending,
  runtimeReady,
  runtimeError,
  t,
}: TranslationStepState & { t: ReturnType<typeof useI18n>["t"] }): string {
  if (providerPending) return t("translate.nextChecking");
  if (runtimeError) return t("translate.nextRuntimeError");
  if (!runtimeReady) return t("translate.nextConfigureRoots");
  if (!batchMode && selectedMedia === null) return t("translate.nextChooseMedia");
  if (batchMode && batchMediaCount === 0) {
    return t("translate.nextChooseMediaBatch");
  }
  if (batchMode && batchReadyCount < batchMediaCount) {
    const remaining = batchMediaCount - batchReadyCount;
    return t("translate.nextChooseSubtitleForMedia", { count: remaining });
  }
  if (!batchMode && selectedCandidate === undefined) {
    return t("translate.nextChooseSubtitle");
  }
  if (!targetLanguage.trim()) return t("translate.nextChooseLanguage");
  if (outputSuffixError) return outputSuffixError;
  if (!providerReady) {
    return t("translate.nextProviderUnavailable");
  }
  const count = batchMode ? batchMediaCount : 1;
  return t("translate.nextReady", {
    count,
    unit: t("jobs.job", { count }),
  });
}
const isBatchError = (result: BatchJobResult): result is BatchJobError =>
  "error_code" in result;
const isBatchSkipped = (result: BatchJobResult): result is BatchJobSkipped =>
  "status" in result && result.status === "skipped";

function OutputConflictPolicy({
  value,
  onChange,
}: {
  value: OutputConflictPolicy;
  onChange: (value: OutputConflictPolicy) => void;
}) {
  const { t } = useI18n();
  return (
    <fieldset className="output-conflict-policy">
      <legend>{t("translate.outputConflict")}</legend>
      <label>
        <input
          type="radio"
          name="output-conflict-policy"
          value="skip"
          checked={value === "skip"}
          onChange={() => onChange("skip")}
        />
        <span>
          {t("translate.skipExisting")}
          {value === "skip" && (
            <span className="output-conflict-policy-hint">
              {" "}
              {t("translate.noJobIfOutputExists")}
            </span>
          )}
        </span>
      </label>
      <label>
        <input
          type="radio"
          name="output-conflict-policy"
          value="append-number"
          checked={value === "append-number"}
          onChange={() => onChange("append-number")}
        />
        {t("translate.appendNumberRecommended")}
      </label>
      <label>
        <input
          type="radio"
          name="output-conflict-policy"
          value="overwrite"
          checked={value === "overwrite"}
          onChange={() => onChange("overwrite")}
        />
        {t("translate.overwrite")}
      </label>
    </fieldset>
  );
}

function OutputSuffixError({ error }: { error: string | null }) {
  return error ? (
    <p className="form-error" role="alert">
      {error}
    </p>
  ) : null;
}

function Translate() {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const createJob = useCreateJob();
  const createBatchJobs = useCreateBatchJobs();
  const navigate = useNavigate();
  const status = useProductStatus();
  const [directory, setDirectory] = useState("");
  const [filter, setFilter] = useState("");
  const [selectedMedia, setSelectedMedia] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedBatchMedia, setSelectedBatchMedia] = useState<Set<string>>(
    () => new Set(),
  );
  const [batchSubtitleSelections, setBatchSubtitleSelections] = useState<
    Map<string, string>
  >(() => new Map());
  const [expandedBatchMedia, setExpandedBatchMedia] = useState<Set<string>>(
    () => new Set(),
  );
  const [mediaBrowserExpanded, setMediaBrowserExpanded] = useState(true);
  const [batchSubtitleFilter, setBatchSubtitleFilter] = useState("");
  const [selectedSubtitle, setSelectedSubtitle] = useState<string | null>(null);
  const [targetLanguage, setTargetLanguage] = useState(readTargetLanguage);
  const [targetLanguageChoice, setTargetLanguageChoice] = useState(() => {
    const remembered = readTargetLanguage();
    if (remembered === "") return "";
    return COMMON_TARGET_LANGUAGES.some(({ code }) => code === remembered)
      ? remembered
      : "custom";
  });
  const [outputSuffix, setOutputSuffix] = useState(() => targetLanguage);
  const customTargetLanguage = targetLanguageChoice === "custom";
  const [outputConflictPolicy, setOutputConflictPolicy] =
    useState<OutputConflictPolicy>("skip");
  const suffixEdited = useRef(false);
  const [termMapMode, setTermMapMode] = useState<TermMapMode>("follow");
  const [termMapId, setTermMapId] = useState<string | null>(null);
  const [directoryTermMapSelection, setDirectoryTermMapSelection] = useState<
    string | null
  >(null);
  const directorySelectRef = useRef<HTMLSelectElement>(null);
  const [dynamicTerminologyEnabled, setDynamicTerminologyEnabled] = useState(true);
  const [subtitleTerminologyFilterEnabled, setSubtitleTerminologyFilterEnabled] =
    useState(true);
  const termMapSelectRef = useRef<HTMLSelectElement>(null);
  const focusTermMapAfterRetry = useRef(false);
  const mediaButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const termMaps = useTermMaps();
  const directoryTermMap = useDirectoryTermMap(directory);
  const bindDirectoryTermMap = useBindDirectoryTermMap();
  const removeDirectoryTermMap = useRemoveDirectoryTermMap();
  useEffect(() => {
    if (focusTermMapAfterRetry.current && termMaps.isSuccess) {
      focusTermMapAfterRetry.current = false;
      termMapSelectRef.current?.focus();
    }
  }, [termMaps.isSuccess]);
  const selectedDirectoryTermMapId =
    directoryTermMapSelection ?? directoryTermMap.data?.local?.id ?? "";
  const selectedTermMapId =
    termMapMode === "selected" &&
    termMapId !== null &&
    (termMaps.data === undefined ||
      termMaps.data.term_maps.some((termMap) => termMap.id === termMapId))
      ? termMapId
      : null;
  const submissionTermMapMode: TermMapMode =
    termMapMode === "selected" && selectedTermMapId === null ? "none" : termMapMode;
  const browser = useMediaDirectory(directory);
  const discovery = useMediaDiscovery(selectedMedia);
  const batchPaths = [...selectedBatchMedia];
  const batchDiscoveries = useMediaDiscoveries(batchPaths);
  const focusBatchMedia = (path: string, fallbackPath?: string) => {
    queueMicrotask(() => {
      (
        mediaButtonRefs.current.get(path) ??
        (fallbackPath ? mediaButtonRefs.current.get(fallbackPath) : undefined)
      )?.focus();
    });
  };
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
    setMediaBrowserExpanded(true);
  };
  const selectedCandidate = discovery.data?.candidates.find(
    (candidate, index) => candidateKey(candidate, index) === selectedSubtitle,
  );
  const batchItems = batchPaths.flatMap((path, index) => {
    const result = batchDiscoveries[index]?.data;
    const allCandidates = result?.candidates ?? [];
    const candidates = filteredCandidates(allCandidates, batchSubtitleFilter);
    const manuallySelectedKey = batchSubtitleSelections.get(path);
    const selectedKey =
      manuallySelectedKey ??
      (candidates.length === 1 && isCompleteCandidate(candidates[0])
        ? candidateKey(candidates[0], 0)
        : undefined);
    const selectionCandidates = manuallySelectedKey ? allCandidates : candidates;
    const selected = selectionCandidates.find(
      (candidate, candidateIndex) =>
        candidateKey(candidate, candidateIndex) === selectedKey,
    );
    if (!selected || !isCompleteCandidate(selected)) return [];
    return [
      {
        media_path: path,
        ...(selected.kind === "external"
          ? { subtitle_path: selected.path }
          : { stream_index: selected.stream_index, source_format: selected.format }),
      },
    ];
  });
  const selectUniqueBatchCandidates = () => {
    setBatchSubtitleSelections(() => {
      const next = new Map<string, string>();
      batchPaths.forEach((path, index) => {
        const candidates = filteredCandidates(
          batchDiscoveries[index]?.data?.candidates ?? [],
          batchSubtitleFilter,
        );
        if (candidates.length === 1 && isCompleteCandidate(candidates[0])) {
          next.set(path, candidateKey(candidates[0], 0));
        }
      });
      return next;
    });
  };
  const outputFormat = selectedCandidate?.format ?? "srt";
  const outputParts = selectedMedia
    ? outputNameParts(selectedMedia, outputFormat)
    : null;
  const outputSuffixError = validateOutputSuffix(outputSuffix, t);
  const providerReady =
    !status.isError && status.data?.translation_provider.ready === true;
  const runtimeReady =
    !status.isError &&
    status.data?.api.ready === true &&
    status.data?.roots.ready === true;
  const updateTargetLanguage = (value: string) => {
    setTargetLanguage(value);
    if (!suffixEdited.current) setOutputSuffix(value);
  };
  const canSubmit =
    (batchMode
      ? batchPaths.length > 0 && batchItems.length === batchPaths.length
      : selectedMedia !== null) &&
    (batchMode ||
      (selectedCandidate?.kind === "external" &&
        selectedCandidate.path !== undefined) ||
      (selectedCandidate?.kind === "embedded" &&
        selectedCandidate.stream_index !== undefined &&
        selectedCandidate.format !== undefined)) &&
    targetLanguage.trim() !== "" &&
    outputSuffixError === null &&
    runtimeReady &&
    (outputConflictPolicy === "skip" || providerReady) &&
    !createJob.isSuccess &&
    !createBatchJobs.isSuccess &&
    !createJob.isPending &&
    !createBatchJobs.isPending;
  const queuedJob =
    createJob.data && !isSkippedJobResult(createJob.data) ? createJob.data : undefined;
  const nextTranslationStep = getNextTranslationStep({
    batchMode,
    selectedMedia,
    batchMediaCount: batchPaths.length,
    batchReadyCount: batchItems.length,
    selectedCandidate,
    targetLanguage,
    outputSuffixError,
    providerReady: outputConflictPolicy === "skip" || providerReady,
    providerPending: status.isPending,
    runtimeReady,
    runtimeError: status.isError,
    t,
  });

  const resetTranslationWorkflow = () => {
    clearMedia(selectedMedia);
    setSelectedBatchMedia(new Set());
    setBatchSubtitleSelections(new Map());
    setExpandedBatchMedia(new Set());
    setMediaBrowserExpanded(true);
    setBatchSubtitleFilter("");
    setBatchMode(false);
    setTermMapMode("follow");
    setTermMapId(null);
    setDynamicTerminologyEnabled(true);
    setSubtitleTerminologyFilterEnabled(true);
    setOutputSuffix(targetLanguage);
    setOutputConflictPolicy("skip");
    suffixEdited.current = false;
    createJob.reset();
    createBatchJobs.reset();
  };

  return (
    <>
      <PageHeader title={t("translate.title")} detail={t("translate.detail")} />
      <QuickStart
        title={t("translate.quickStartTitle")}
        steps={[
          t("translate.quickStartStepOne"),
          t("translate.quickStartStepTwo"),
          t("translate.quickStartStepThree"),
        ]}
      />
      <p className="page-note">{t("translate.backgroundNote")}</p>
      <TranslationRuntimeNotice />
      <section className="workflow-panel" aria-labelledby="source-title">
        <div className="step-index">01</div>
        <div className="step-content">
          <h2 id="source-title">{t("translate.chooseMedia")}</h2>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={batchMode}
              onChange={(event) => {
                const nextBatchMode = event.target.checked;
                setBatchMode(nextBatchMode);
                setSelectedMedia(null);
                setSelectedSubtitle(null);
                setSelectedBatchMedia(new Set());
                setBatchSubtitleSelections(new Map());
                setExpandedBatchMedia(new Set());
                setMediaBrowserExpanded(true);
                setBatchSubtitleFilter("");
                if (nextBatchMode) {
                  setOutputSuffix(targetLanguage);
                  setOutputConflictPolicy("skip");
                  suffixEdited.current = false;
                }
              }}
            />
            {t("translate.batchMode")}
          </label>
          <p className="field-help">{t("translate.batchDetail")}</p>
          <div className="media-discovery-layout">
            <MediaBrowser
              directory={directory}
              filter={filter}
              onDirectoryChange={(path) => {
                setDirectory(path);
                setTermMapMode("follow");
                setTermMapId(null);
                setDirectoryTermMapSelection(null);
                setFilter("");
                clearMedia(selectedMedia);
                setSelectedBatchMedia(new Set());
                setBatchSubtitleSelections(new Map());
                setExpandedBatchMedia(new Set());
              }}
              onFilterChange={setFilter}
              selectedMedia={selectedMedia}
              selectedMediaPaths={selectedBatchMedia}
              batchMode={batchMode}
              collapseUnselected={!mediaBrowserExpanded}
              onMediaSelect={(path) => {
                if (batchMode) {
                  setSelectedBatchMedia((current) => {
                    const next = new Set(current);
                    if (next.has(path)) next.delete(path);
                    else next.add(path);
                    return next;
                  });
                  setMediaBrowserExpanded(false);
                } else {
                  clearDiscovery(selectedMedia);
                  setSelectedMedia(path);
                  setSelectedSubtitle(null);
                  setMediaBrowserExpanded(false);
                }
              }}
              mediaButtonRefs={mediaButtonRefs}
              query={browser}
            />
            <div className="discovery-stack">
              {batchMode && (
                <div className="batch-subtitle-controls">
                  {batchPaths.length > 0 && !mediaBrowserExpanded && (
                    <Button
                      type="button"
                      variant="outline"
                      className="mobile-browser-restore"
                      onClick={() => setMediaBrowserExpanded(true)}
                    >
                      {t("translate.selectAnotherMedia")}
                    </Button>
                  )}
                  <label htmlFor="batch-subtitle-filter">
                    {t("translate.searchSubtitles")}
                    <Input
                      id="batch-subtitle-filter"
                      type="search"
                      value={batchSubtitleFilter}
                      onChange={(event) => setBatchSubtitleFilter(event.target.value)}
                      placeholder={t("translate.searchMedia")}
                    />
                  </label>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={batchPaths.length === 0}
                    onClick={selectUniqueBatchCandidates}
                  >
                    {t("translate.selectUnique")}
                  </Button>
                  <span className="field-help">{t("translate.autoSelectHelp")}</span>
                </div>
              )}
              {batchMode &&
                batchPaths.map((path, index) => (
                  <SubtitleDiscovery
                    key={path}
                    mediaPath={path}
                    selected={
                      batchSubtitleSelections.get(path) ??
                      (() => {
                        const candidates = filteredCandidates(
                          batchDiscoveries[index]?.data?.candidates ?? [],
                          batchSubtitleFilter,
                        );
                        return candidates.length === 1 &&
                          isCompleteCandidate(candidates[0])
                          ? candidateKey(candidates[0], 0)
                          : null;
                      })()
                    }
                    candidateFilter={batchSubtitleFilter}
                    batchMode
                    expanded={expandedBatchMedia.has(path)}
                    onToggleExpanded={() =>
                      setExpandedBatchMedia((current) => {
                        const next = new Set(current);
                        if (next.has(path)) next.delete(path);
                        else next.add(path);
                        return next;
                      })
                    }
                    onSelect={(value) =>
                      setBatchSubtitleSelections((current) => {
                        const next = new Map(current);
                        next.set(path, value);
                        return next;
                      })
                    }
                    query={batchDiscoveries[index]}
                    onClear={() => {
                      if (batchPaths.length === 1) {
                        setFilter("");
                        setMediaBrowserExpanded(true);
                      }
                      setSelectedBatchMedia((current) => {
                        const next = new Set(current);
                        next.delete(path);
                        return next;
                      });
                      setBatchSubtitleSelections((current) => {
                        const next = new Map(current);
                        next.delete(path);
                        return next;
                      });
                      setExpandedBatchMedia((current) => {
                        const next = new Set(current);
                        next.delete(path);
                        return next;
                      });
                      const clearedIndex = batchPaths.indexOf(path);
                      const focusPath =
                        batchPaths.length > 1
                          ? (batchPaths[clearedIndex + 1] ??
                            batchPaths[clearedIndex - 1])
                          : path;
                      focusBatchMedia(focusPath);
                    }}
                  />
                ))}
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
          </div>
          <DirectoryTermMapPanel
            directory={directory}
            termMaps={termMaps.data?.term_maps ?? []}
            query={directoryTermMap}
            selectRef={directorySelectRef}
            selectedId={selectedDirectoryTermMapId}
            onSelectedIdChange={(value) => {
              bindDirectoryTermMap.reset();
              removeDirectoryTermMap.reset();
              setDirectoryTermMapSelection(value);
            }}
            onBind={() => {
              if (selectedDirectoryTermMapId) {
                bindDirectoryTermMap.reset();
                removeDirectoryTermMap.reset();
                bindDirectoryTermMap.mutate(
                  {
                    path: directory,
                    termMapId: selectedDirectoryTermMapId,
                  },
                  {
                    onSuccess: () => directorySelectRef.current?.focus(),
                  },
                );
              }
            }}
            onRemove={() => {
              bindDirectoryTermMap.reset();
              removeDirectoryTermMap.reset();
              removeDirectoryTermMap.mutate(directory, {
                onSuccess: () => directorySelectRef.current?.focus(),
              });
            }}
            onRetry={() => {
              if (bindDirectoryTermMap.error && selectedDirectoryTermMapId) {
                bindDirectoryTermMap.mutate(
                  {
                    path: directory,
                    termMapId: selectedDirectoryTermMapId,
                  },
                  {
                    onSuccess: () => directorySelectRef.current?.focus(),
                  },
                );
              } else if (removeDirectoryTermMap.error) {
                removeDirectoryTermMap.mutate(directory, {
                  onSuccess: () => directorySelectRef.current?.focus(),
                });
              }
            }}
            isBinding={bindDirectoryTermMap.isPending}
            isRemoving={removeDirectoryTermMap.isPending}
            error={
              bindDirectoryTermMap.error ? (
                <LocalizedErrorMessage error={bindDirectoryTermMap.error} />
              ) : removeDirectoryTermMap.error ? (
                <LocalizedErrorMessage error={removeDirectoryTermMap.error} />
              ) : null
            }
          />
        </div>
      </section>
      <section
        className={cn(
          "workflow-panel",
          !batchMode && selectedCandidate === undefined && "muted",
        )}
        aria-labelledby="configure-title"
      >
        <div className="step-index">02</div>
        <div className="step-content">
          <h2 id="configure-title">{t("translate.configure")}</h2>
          <p>{t("translate.configureDetail")}</p>
          <label htmlFor="common-target-language">
            {t("translate.commonTargetLanguage")}
            <Select
              id="common-target-language"
              value={targetLanguageChoice}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "custom") {
                  setTargetLanguageChoice("custom");
                  updateTargetLanguage("");
                } else {
                  setTargetLanguageChoice(value);
                  updateTargetLanguage(value);
                }
              }}
              disabled={
                batchMode ? batchItems.length === 0 : selectedCandidate === undefined
              }
            >
              <option value="" disabled>
                {t("translate.chooseLanguage")}
              </option>
              <option value="custom">{t("translate.customLanguage")}</option>
              {COMMON_TARGET_LANGUAGES.map(({ code, label }) => (
                <option key={code} value={code}>
                  {localizedLanguageLabel(code, label, locale)} — {code}
                </option>
              ))}
            </Select>
          </label>
          {customTargetLanguage && (
            <label htmlFor="target-language-code" className="custom-language-field">
              {t("translate.targetLanguageCode")}
              <Input
                id="target-language-code"
                required
                aria-describedby="target-language-help"
                value={targetLanguage}
                onChange={(event) => updateTargetLanguage(event.target.value)}
                placeholder={t("translate.targetLanguagePlaceholder")}
                disabled={
                  batchMode ? batchItems.length === 0 : selectedCandidate === undefined
                }
              />
            </label>
          )}
          <span id="target-language-help" className="field-help">
            {t("translate.targetLanguageHelp")}
          </span>
          <div className="term-map-field">
            <label htmlFor="term-map-select">{t("translate.termMap")}</label>
            <Select
              id="term-map-select"
              ref={termMapSelectRef}
              aria-label={t("translate.termMap")}
              aria-describedby="term-map-policy-help"
              value={
                termMapMode === "follow"
                  ? DIRECTORY_TERM_MAP_VALUE
                  : termMapMode === "selected"
                    ? (selectedTermMapId ?? "")
                    : ""
              }
              onChange={(event) => {
                const value = event.target.value;
                if (value === DIRECTORY_TERM_MAP_VALUE) {
                  setTermMapMode("follow");
                  setTermMapId(null);
                } else if (value === "") {
                  setTermMapMode("none");
                  setTermMapId(null);
                } else {
                  setTermMapMode("selected");
                  setTermMapId(value);
                }
              }}
              disabled={termMaps.isPending || termMaps.isError}
            >
              <option value={DIRECTORY_TERM_MAP_VALUE}>
                {directoryTermMap.data?.effective
                  ? `${t("translate.directoryDefault")} (${directoryTermMap.data.effective.name})`
                  : `${t("translate.directoryDefault")} (${t("jobs.none")})`}
              </option>
              <option value="">{t("translate.noTermMapJob")}</option>
              {(termMaps.data?.term_maps ?? []).map((termMap) => (
                <option key={termMap.id} value={termMap.id}>
                  {termMap.name}
                </option>
              ))}
            </Select>
            <span id="term-map-policy-help" className="field-help">
              {t("translate.termMapPolicyHelp")}
            </span>
            <span className="field-help">{t("translate.termMapHelp")}</span>
            {termMaps.data?.term_maps.length === 0 && (
              <span className="field-help">
                {t("translate.noTermMapsHelp")}{" "}
                <Link to="/term-maps">{t("termMaps.createFirst")}</Link>.
              </span>
            )}
            {termMaps.isPending && (
              <span className="field-help" role="status">
                {t("translate.loadingTermMaps")}
              </span>
            )}
            {termMaps.isError && (
              <div className="field-recovery">
                <div className="form-error" role="alert">
                  <LocalizedErrorMessage error={termMaps.error} />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    focusTermMapAfterRetry.current = true;
                    void termMaps.refetch();
                  }}
                >
                  {t("common.tryAgain")}
                </Button>
              </div>
            )}
          </div>
          <details className="advanced-settings">
            <summary>{t("translate.advanced")}</summary>
            <p className="field-help">{t("translate.advancedHelp")}</p>
            <div className="advanced-fields">
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={dynamicTerminologyEnabled}
                  onChange={(event) =>
                    setDynamicTerminologyEnabled(event.target.checked)
                  }
                />
                {t("translate.dynamicTerminology")}
              </label>
              <span className="field-help">
                {t("translate.dynamicTerminologyHelp")}
              </span>
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={subtitleTerminologyFilterEnabled}
                  onChange={(event) =>
                    setSubtitleTerminologyFilterEnabled(event.target.checked)
                  }
                />
                {t("translate.subtitleTerminology")}
              </label>
              <span className="field-help">
                {t("translate.subtitleTerminologyHelp")}
              </span>
            </div>
          </details>
          {batchMode
            ? batchPaths.length > 0 && (
                <div className="output-name-section">
                  <span className="field-label">
                    {t("translate.sharedOutputSettings")}
                  </span>
                  <label htmlFor="batch-output-suffix" className="field-label">
                    {t("translate.outputSuffix")}
                  </label>
                  <Input
                    id="batch-output-suffix"
                    aria-describedby="batch-output-suffix-help"
                    value={outputSuffix}
                    onChange={(event) => {
                      suffixEdited.current = true;
                      setOutputSuffix(event.target.value);
                    }}
                  />
                  <p
                    id="batch-output-suffix-help"
                    className="field-help"
                    aria-live="polite"
                  >
                    {t("translate.appliedEvery")} {t("translate.appliedEveryDetail")}
                  </p>
                  <OutputSuffixError error={outputSuffixError} />
                  <OutputConflictPolicy
                    value={outputConflictPolicy}
                    onChange={setOutputConflictPolicy}
                  />
                </div>
              )
            : selectedMedia &&
              selectedCandidate &&
              outputParts && (
                <div className="output-name-section">
                  <label htmlFor="output-suffix" className="field-label">
                    {t("translate.outputSuffix")}
                  </label>
                  <Input
                    id="output-suffix"
                    aria-describedby="output-suffix-help"
                    value={outputSuffix}
                    onChange={(event) => {
                      suffixEdited.current = true;
                      setOutputSuffix(event.target.value);
                    }}
                  />
                  <p id="output-suffix-help" className="field-help" aria-live="polite">
                    {t("translate.outputFilename")}{" "}
                    <strong>{outputParts.name(outputSuffix)}</strong>
                  </p>
                  <p className="field-help">{t("translate.suffixTargetHelp")}</p>
                  <OutputSuffixError error={outputSuffixError} />
                  <OutputConflictPolicy
                    value={outputConflictPolicy}
                    onChange={setOutputConflictPolicy}
                  />
                </div>
              )}
        </div>
      </section>
      <div
        className={cn(
          "submission-bar",
          (createJob.isSuccess || createBatchJobs.isSuccess) && "queued",
        )}
      >
        {createJob.isSuccess ? (
          isSkippedJobResult(createJob.data) ? (
            <SkipSuccess
              result={createJob.data}
              onTranslateAnother={resetTranslationWorkflow}
            />
          ) : (
            <QueueSuccess
              job={queuedJob}
              onViewJob={() => {
                if (queuedJob?.id)
                  navigate(`/jobs/${encodeURIComponent(queuedJob.id)}`);
              }}
              onTranslateAnother={resetTranslationWorkflow}
            />
          )
        ) : createBatchJobs.isSuccess ? (
          <BatchQueueResults
            mediaPaths={
              createBatchJobs.variables?.items.map((item) => item.media_path) ?? []
            }
            results={createBatchJobs.data}
            onViewJob={(jobId) => navigate(`/jobs/${encodeURIComponent(jobId)}`)}
            onTranslateAnother={resetTranslationWorkflow}
          />
        ) : (
          <>
            <p className="next-action">{nextTranslationStep}</p>
            <ProviderState />
            <Button
              disabled={!canSubmit}
              onClick={() => {
                if (batchMode) {
                  createBatchJobs.mutate(
                    {
                      items: batchItems,
                      target_language_code: targetLanguage,
                      output_suffix: outputSuffix,
                      output_conflict_policy: outputConflictPolicy,
                      term_map_mode: submissionTermMapMode,
                      term_map_id: selectedTermMapId,
                      dynamic_terminology_enabled: dynamicTerminologyEnabled,
                      subtitle_terminology_filter_enabled:
                        subtitleTerminologyFilterEnabled,
                    },
                    {
                      onSuccess: () => storeTargetLanguage(targetLanguage),
                    },
                  );
                } else if (
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
                    term_map_mode: submissionTermMapMode,
                    term_map_id: selectedTermMapId,
                    dynamic_terminology_enabled: dynamicTerminologyEnabled,
                    subtitle_terminology_filter_enabled:
                      subtitleTerminologyFilterEnabled,
                  };
                  createJob.mutate(request, {
                    onSuccess: () => storeTargetLanguage(targetLanguage),
                  });
                }
              }}
            >
              {createJob.isPending || createBatchJobs.isPending
                ? t("translate.queueing")
                : batchMode
                  ? t("translate.queueSelected")
                  : t("translate.start")}
            </Button>
          </>
        )}
      </div>
      {createJob.isError && (
        <div className="form-error" role="alert">
          <LocalizedErrorMessage error={createJob.error} />
        </div>
      )}
      {createBatchJobs.isError && (
        <div className="form-error" role="alert">
          <LocalizedErrorMessage error={createBatchJobs.error} />
        </div>
      )}
    </>
  );
}

function DirectoryTermMapPanel({
  directory,
  termMaps,
  query,
  selectRef,
  selectedId,
  onSelectedIdChange,
  onBind,
  onRemove,
  onRetry,
  isBinding,
  isRemoving,
  error,
}: {
  directory: string;
  termMaps: Array<{ id: string; name: string }>;
  query: ReturnType<typeof useDirectoryTermMap>;
  selectRef: RefObject<HTMLSelectElement | null>;
  selectedId: string;
  onSelectedIdChange: (value: string) => void;
  onBind: () => void;
  onRemove: () => void;
  onRetry: () => void;
  isBinding: boolean;
  isRemoving: boolean;
  error: ReactNode;
}) {
  const { t } = useI18n();
  const local = query.data?.local;
  const effective = query.data?.effective;
  const focusAfterRetry = useRef(false);
  useEffect(() => {
    if (focusAfterRetry.current && query.isSuccess) {
      focusAfterRetry.current = false;
      selectRef.current?.focus();
    }
  }, [query.isSuccess, selectRef]);
  return (
    <section className="directory-term-map" aria-labelledby="directory-term-map-title">
      <div className="directory-term-map-heading">
        <div>
          <h3 id="directory-term-map-title">{t("translate.directoryDefault")}</h3>
          <p className="field-help">
            {directory
              ? t("translate.currentDirectory", { name: directory })
              : t("translate.currentDirectoryRoot")}
          </p>
          <p id="directory-default-help" className="field-help">
            {t("translate.directoryDefaultHelp")}
          </p>
          <p className="field-help">{t("translate.directoryDefaultScopeHelp")}</p>
        </div>
        {query.isPending && <span role="status">{t("common.loading")}</span>}
      </div>
      {query.isError ? (
        <div className="field-recovery">
          <div className="form-error" role="alert">
            <LocalizedErrorMessage error={query.error} />
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              focusAfterRetry.current = true;
              void query.refetch();
            }}
          >
            {t("common.tryAgain")}
          </Button>
        </div>
      ) : (
        <>
          <dl className="directory-term-map-state">
            <div>
              <dt>{t("translate.localBinding")}</dt>
              <dd>{local?.name ?? t("jobs.none")}</dd>
            </div>
            <div>
              <dt>{t("translate.effectiveTermMap")}</dt>
              <dd>
                {effective?.name ?? t("translate.noDefault")}
                {effective && !local && query.data?.source_directory !== null && (
                  <span className="field-help">
                    {t("translate.inheritedFrom", {
                      name: query.data?.source_directory || t("translate.mediaRoot"),
                    })}
                  </span>
                )}
              </dd>
            </div>
          </dl>
          <div className="directory-term-map-controls">
            <Select
              ref={selectRef}
              aria-label={t("translate.directoryDefault")}
              aria-describedby="directory-default-help"
              value={selectedId}
              onChange={(event) => onSelectedIdChange(event.target.value)}
              disabled={
                query.isPending || termMaps.length === 0 || isBinding || isRemoving
              }
            >
              <option value="">{t("translate.chooseTermMap")}</option>
              {termMaps.map((termMap) => (
                <option key={termMap.id} value={termMap.id}>
                  {t("translate.directoryOption", { name: termMap.name })}
                </option>
              ))}
            </Select>
            <Button
              type="button"
              variant="outline"
              disabled={!selectedId || isBinding || isRemoving}
              onClick={onBind}
            >
              {isBinding
                ? t("translate.binding")
                : local
                  ? t("translate.replaceLocalBinding")
                  : t("translate.bindTermMap")}
            </Button>
            {local && (
              <Button
                type="button"
                variant="outline"
                disabled={isBinding || isRemoving}
                onClick={onRemove}
              >
                {isRemoving
                  ? t("translate.removing")
                  : t("translate.removeLocalBinding")}
              </Button>
            )}
          </div>
          {error && (
            <div className="field-recovery">
              <div className="form-error" role="alert">
                {error}
              </div>
              <Button type="button" variant="outline" onClick={onRetry}>
                {t("common.tryAgain")}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function QueueSuccess({
  job,
  onViewJob,
  onTranslateAnother,
}: {
  job: Job | undefined;
  onViewJob: () => void;
  onTranslateAnother: () => void;
}) {
  const { t } = useI18n();
  const media = job?.request?.media_path ?? t("translate.mediaSummary");
  const targetLanguage =
    job?.request?.target_language_code ?? t("translate.targetLanguageUnavailable");

  return (
    <section className="queue-success" aria-labelledby="queue-success-title">
      <div className="queue-success-heading" role="status">
        <CheckCircleIcon size={22} weight="fill" aria-hidden="true" />
        <div>
          <p className="eyebrow">{t("translate.queuedEyebrow")}</p>
          <h2 id="queue-success-title">{t("translate.queued")}</h2>
          <p>{t("translate.queuedDetail")}</p>
        </div>
      </div>
      <dl className="queue-success-summary">
        <div>
          <dt>{t("translate.mediaSummary")}</dt>
          <dd title={media}>{media}</dd>
        </div>
        <div>
          <dt>{t("translate.targetLanguage")}</dt>
          <dd>{targetLanguage}</dd>
        </div>
      </dl>
      <div className="queue-success-actions">
        {job?.id && (
          <Button type="button" onClick={onViewJob}>
            {t("translate.viewJob")}
          </Button>
        )}
        <Button type="button" variant="outline" onClick={onTranslateAnother}>
          {t("translate.translateAnother")}
        </Button>
      </div>
    </section>
  );
}

function SkipSuccess({
  result,
  onTranslateAnother,
}: {
  result: SkippedJobResult;
  onTranslateAnother: () => void;
}) {
  const { t } = useI18n();
  return (
    <section className="queue-success" aria-labelledby="skip-success-title">
      <div className="queue-success-heading" role="status">
        <CheckCircleIcon size={22} weight="fill" aria-hidden="true" />
        <div>
          <p className="eyebrow">{t("translate.skipped")}</p>
          <h2 id="skip-success-title">{t("translate.outputExists")}</h2>
          <p>{t("translate.noJobCreated")}</p>
          <details>
            <summary>{t("translate.showErrorDetails")}</summary>
            <p className="field-help">{result.reason}</p>
          </details>
        </div>
      </div>
      <dl className="queue-success-summary">
        <div>
          <dt>{t("translate.mediaSummary")}</dt>
          <dd title={result.media_path}>{result.media_path}</dd>
        </div>
        <div>
          <dt>{t("jobs.output")}</dt>
          <dd title={result.output_path}>{result.output_path}</dd>
        </div>
      </dl>
      <div className="queue-success-actions">
        <Button type="button" variant="outline" onClick={onTranslateAnother}>
          {t("translate.translateAnother")}
        </Button>
      </div>
    </section>
  );
}

function BatchQueueResults({
  mediaPaths,
  results,
  onViewJob,
  onTranslateAnother,
}: {
  mediaPaths: string[];
  results: BatchJobResult[];
  onViewJob: (jobId: string) => void;
  onTranslateAnother: () => void;
}) {
  const { t } = useI18n();
  const queuedCount = results.filter(
    (result) => !isBatchError(result) && !isBatchSkipped(result),
  ).length;
  const skippedCount = results.filter(isBatchSkipped).length;
  const failedCount = results.filter(isBatchError).length;

  return (
    <section
      className="queue-success batch-queue-results"
      aria-labelledby="batch-results-title"
    >
      <div className="queue-success-heading" role="status">
        <CheckCircleIcon size={22} weight="fill" aria-hidden="true" />
        <div>
          <p className="eyebrow">{t("translate.batchResults")}</p>
          <h2 id="batch-results-title">{t("translate.batchResults")}</h2>
          <p>
            {t("translate.batchSummary", {
              queued: t("translate.queuedCount", {
                count: queuedCount,
                unit: t("translate.job", { count: queuedCount }),
              }),
              skipped:
                skippedCount > 0
                  ? ` · ${t("translate.skippedCount", {
                      count: skippedCount,
                      unit: t("translate.batchItem", { count: skippedCount }),
                    })}`
                  : "",
              errors:
                failedCount > 0
                  ? ` · ${t("translate.errorCount", {
                      count: failedCount,
                      unit: t("translate.batchError", { count: failedCount }),
                    })}`
                  : "",
            })}
          </p>
        </div>
      </div>
      <div
        className="batch-result-list"
        aria-label={t("translate.batchSubmissionResults")}
      >
        {mediaPaths.map((mediaPath, index) => {
          const result = results[index];
          return (
            <BatchResultRow
              key={`${mediaPath}-${index}`}
              mediaPath={mediaPath}
              result={result}
              onViewJob={onViewJob}
            />
          );
        })}
      </div>
      <div className="queue-success-actions">
        <Button type="button" variant="outline" onClick={onTranslateAnother}>
          {t("translate.translateAnother")}
        </Button>
      </div>
    </section>
  );
}

function BatchResultRow({
  mediaPath,
  result,
  onViewJob,
}: {
  mediaPath: string;
  result: BatchJobResult | undefined;
  onViewJob: (jobId: string) => void;
}) {
  const { t } = useI18n();
  const media = mediaPath.split("/").pop() ?? mediaPath;
  if (result === undefined) {
    return (
      <div
        className="batch-result-row"
        role="group"
        aria-label={`${media} ${t("translate.batchResult")}`}
      >
        {media}
      </div>
    );
  }
  if (!isBatchError(result)) {
    if (isBatchSkipped(result)) {
      return (
        <div
          className="batch-result-row batch-result-skipped"
          role="group"
          aria-label={`${media} ${t("translate.batchResult")}`}
        >
          <div>
            <strong>{media}</strong>
            <span>{t("translate.skipped")}</span>
            <details>
              <summary>{t("translate.showErrorDetails")}</summary>
              <p className="field-help">
                {t("translate.skippedReason", { reason: result.reason })}
              </p>
              <small>
                {t("translate.existingOutput", { path: result.output_path })}
              </small>
            </details>
          </div>
        </div>
      );
    }
    return (
      <div
        className="batch-result-row batch-result-success"
        role="group"
        aria-label={`${media} ${t("translate.batchResult")}`}
      >
        <div>
          <strong>{media}</strong>
          <span>{t("translate.queuedAsJob", { id: result.id })}</span>
        </div>
        <Button type="button" variant="outline" onClick={() => onViewJob(result.id)}>
          {t("translate.viewJob")}
        </Button>
      </div>
    );
  }
  const context = Object.entries(result).filter(([key]) =>
    (APPROVED_ERROR_CONTEXT_KEYS as readonly string[]).includes(key),
  );
  return (
    <div
      className="batch-result-row batch-result-error"
      role="group"
      aria-label={`${media} ${t("translate.batchResult")}`}
    >
      <div>
        <strong>{media}</strong>
        <span>{t("translate.noJobCreated")}</span>
        <details>
          <summary>{t("translate.showErrorDetails")}</summary>
          <p className="field-help">{result.message}</p>
          <dl className="job-summary">
            <SummaryItem label={t("translate.errorCode")} value={result.error_code} />
            {context.map(([key, value]) => (
              <SummaryItem key={key} label={key} value={String(value)} />
            ))}
          </dl>
        </details>
      </div>
    </div>
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

function sameTermMapContent(
  left: Record<string, string>,
  right: Record<string, string>,
): boolean {
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every((key, index) => key === rightKeys[index] && left[key] === right[key])
  );
}

function validateOutputSuffix(
  value: string,
  t: ReturnType<typeof useI18n>["t"],
): string | null {
  if (!value) return t("translate.suffixRequired");
  const reserved = new Set([
    "con",
    "prn",
    "aux",
    "nul",
    ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`),
    ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`),
  ]);
  for (const segment of value.split(".")) {
    if (!segment) return t("translate.suffixSegmentRequired");
    if (/\s$/u.test(segment)) {
      return t("translate.suffixTrailingSpace");
    }
    if (reserved.has(segment.toLocaleLowerCase())) {
      return t("translate.suffixReserved");
    }
    for (const character of segment) {
      const codePoint = character.codePointAt(0) ?? 0;
      if (
        codePoint < 32 ||
        codePoint === 127 ||
        /\p{C}/u.test(character) ||
        !/[\p{L}\p{N}\s_-]/u.test(character)
      ) {
        return t("translate.suffixUnsafe");
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
  candidateFilter = "",
  batchMode = false,
  expanded = true,
  onToggleExpanded,
}: {
  mediaPath: string;
  selected: string | null;
  onSelect: (value: string) => void;
  query: ReturnType<typeof useMediaDiscovery>;
  onClear: () => void;
  candidateFilter?: string;
  batchMode?: boolean;
  expanded?: boolean;
  onToggleExpanded?: () => void;
}) {
  const { t } = useI18n();
  const candidates = filteredCandidates(query.data?.candidates ?? [], candidateFilter);
  const visibleCandidates =
    batchMode && candidates.length > 1 && !expanded ? [] : candidates;
  const mediaName = mediaPath.split("/").pop() ?? mediaPath;
  return (
    <section
      className="subtitle-discovery"
      aria-label={t("translate.subtitleSelectionFor", { name: mediaName })}
    >
      <div className="subtitle-heading">
        <div>
          <h3>{t("translate.chooseSubtitle")}</h3>
          <p>{t("translate.sourceDiscovered", { name: mediaName })}</p>
        </div>
        <Button type="button" variant="outline" onClick={onClear}>
          {t("translate.chooseAnotherMedia")}
        </Button>
      </div>
      <div className="subtitle-results" aria-live="polite">
        {(query.isPending || query.isFetching) && (
          <div
            role="status"
            className="discovery-skeleton"
            aria-label={t("translate.loadingSubtitles")}
          >
            <span />
            <span />
            <span />
          </div>
        )}
        {query.isError && (
          <QueryErrorState error={query.error} onRetry={() => void query.refetch()} />
        )}
        {!query.isFetching && query.data && batchMode && candidates.length > 1 && (
          <>
            <EmptyMessage>
              <span>{t("translate.multipleSubtitles")}</span>
              <span className="field-help">{t("translate.multipleSubtitlesHelp")}</span>
            </EmptyMessage>
            {onToggleExpanded && (
              <Button type="button" variant="outline" onClick={onToggleExpanded}>
                {t("translate.resolveCandidates")}
              </Button>
            )}
          </>
        )}
        {!query.isFetching &&
          query.data &&
          candidates.length === 0 &&
          (candidateFilter || query.data.unsupported_candidates.length === 0) && (
            <EmptyMessage>
              {candidateFilter ? (
                t("translate.noCandidateMatch")
              ) : (
                <>
                  <span>{t("translate.noSubtitles")}</span>
                  <span className="field-help">{t("translate.noSubtitlesHelp")}</span>
                </>
              )}
            </EmptyMessage>
          )}
        {!query.isFetching &&
          !query.isError &&
          visibleCandidates.map((candidate, index) => {
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

function filteredCandidates(candidates: SubtitleCandidate[], filter: string) {
  const normalized = filter.trim().toLocaleLowerCase();
  if (!normalized) return candidates;
  return candidates.filter((candidate) => {
    const tags = Object.values(candidate.tags ?? {});
    const metadata = [
      candidate.kind,
      candidate.path,
      candidate.format,
      candidate.stream_index,
      ...(candidate.dispositions ?? []),
      ...tags,
    ];
    return metadata.some((value) =>
      String(value ?? "")
        .toLocaleLowerCase()
        .includes(normalized),
    );
  });
}

function isCompleteCandidate(
  candidate: SubtitleCandidate | undefined,
): candidate is SubtitleCandidate {
  return (
    candidate !== undefined &&
    ((candidate.kind === "external" && candidate.path !== undefined) ||
      (candidate.kind === "embedded" &&
        candidate.stream_index !== undefined &&
        candidate.format !== undefined))
  );
}

const SUBTITLE_DISPOSITION_LABELS: Record<string, TranslationKey> = {
  default: "translate.disposition.default",
  forced: "translate.disposition.forced",
  hearing_impaired: "translate.disposition.hearingImpaired",
  visual_impaired: "translate.disposition.visualImpaired",
  comment: "translate.disposition.commentary",
  lyrics: "translate.disposition.lyrics",
  karaoke: "translate.disposition.karaoke",
  original: "translate.disposition.original",
  dub: "translate.disposition.dubbed",
  clean_effects: "translate.disposition.cleanEffects",
};

function subtitleLabel(
  candidate: SubtitleCandidate,
  t: ReturnType<typeof useI18n>["t"],
) {
  const tags = candidate.tags ?? {};
  return (
    [tags.language, tags.title].filter(Boolean).join(" / ") ||
    t("translate.metadataUnavailable")
  );
}

function subtitleKindLabel(
  kind: "external" | "embedded",
  t: ReturnType<typeof useI18n>["t"],
): string {
  return kind === "external"
    ? t("translate.externalSubtitle")
    : t("translate.embeddedSubtitle");
}

function subtitlePath(candidate: SubtitleCandidate) {
  if (candidate.kind !== "external" || !candidate.path) {
    return null;
  }
  return candidate.path.split("/").pop() ?? candidate.path;
}

function subtitleDetails(
  candidate: SubtitleCandidate,
  t: ReturnType<typeof useI18n>["t"],
) {
  const details = [candidate.format?.toUpperCase() ?? t("translate.unknownFormat")];
  if (candidate.kind === "embedded" && candidate.stream_index !== undefined) {
    details.push(t("translate.stream", { index: candidate.stream_index }));
    details.push(
      ...(candidate.dispositions ?? [])
        .map((disposition) => {
          const key = SUBTITLE_DISPOSITION_LABELS[disposition];
          return key ? t(key) : undefined;
        })
        .filter((label): label is string => label !== undefined),
    );
  } else if (subtitlePath(candidate)) {
    details.push(subtitlePath(candidate)!);
  }
  return details.join(" · ");
}

function subtitleAccessibleLabel(
  candidate: SubtitleCandidate,
  t: ReturnType<typeof useI18n>["t"],
) {
  const path = subtitlePath(candidate);
  const stream =
    candidate.kind === "embedded" && candidate.stream_index !== undefined
      ? `${t("translate.streamAccessible", { index: candidate.stream_index })} `
      : "";
  return path
    ? `${stream}${subtitleLabel(candidate, t)} (${path})`
    : `${stream}${subtitleLabel(candidate, t)}`;
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
  const { t } = useI18n();
  const selectable = isCompleteCandidate(candidate);
  return (
    <Button
      type="button"
      variant="outline"
      className={cn("subtitle-entry", candidate.kind === "embedded" && "embedded")}
      aria-pressed={selectable && selected}
      disabled={!selectable}
      aria-label={t("translate.selectSubtitle", {
        kind:
          candidate.kind === "external"
            ? t("translate.externalKind")
            : t("translate.embeddedKind"),
        details: subtitleAccessibleLabel(candidate, t),
      })}
      onClick={() => {
        if (selectable) onSelect(candidateId);
      }}
    >
      <span className="subtitle-kind">{subtitleKindLabel(candidate.kind, t)}</span>
      <span className="subtitle-copy">
        <strong>{subtitleLabel(candidate, t)}</strong>
        <small>{subtitleDetails(candidate, t)}</small>
      </span>
      {selectable && selected && (
        <span className="media-entry-selected">{t("translate.selected")}</span>
      )}
      {!selectable && (
        <span className="disabled-note">{t("translate.incompleteCandidate")}</span>
      )}
    </Button>
  );
}

function UnsupportedSubtitleEntry({
  candidate,
}: {
  candidate: UnsupportedSubtitleCandidate;
}) {
  const { t } = useI18n();
  return (
    <div
      className="subtitle-entry unsupported"
      role="group"
      aria-disabled="true"
      aria-label={t("translate.unsupportedLabel", {
        kind:
          candidate.kind === "external"
            ? t("translate.externalKind")
            : t("translate.embeddedKind"),
      })}
    >
      <span className="subtitle-kind">{subtitleKindLabel(candidate.kind, t)}</span>
      <span className="subtitle-copy">
        <strong>{t("translate.unavailableSubtitle")}</strong>
        <small>
          <span>{candidate.reason}</span>. {t("translate.unsupportedSubtitleHelp")}
        </small>
      </span>
      <span className="disabled-note">{t("translate.notSelectable")}</span>
    </div>
  );
}

function MediaBrowser({
  directory,
  filter,
  onDirectoryChange,
  onFilterChange,
  selectedMedia,
  selectedMediaPaths,
  batchMode,
  collapseUnselected,
  onMediaSelect,
  mediaButtonRefs,
  query,
}: {
  directory: string;
  filter: string;
  onDirectoryChange: (path: string) => void;
  onFilterChange: (filter: string) => void;
  selectedMedia: string | null;
  selectedMediaPaths: Set<string>;
  batchMode: boolean;
  collapseUnselected: boolean;
  onMediaSelect: (path: string) => void;
  mediaButtonRefs: MutableRefObject<Map<string, HTMLButtonElement>>;
  query: ReturnType<typeof useMediaDirectory>;
}) {
  const { t } = useI18n();
  const selectionActive = batchMode
    ? selectedMediaPaths.size > 0
    : selectedMedia !== null;
  const entries = query.data?.entries.filter(
    (entry) =>
      entry.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase()) ||
      (entry.kind === "media" &&
        (batchMode
          ? selectedMediaPaths.has(entry.path)
          : selectedMedia === entry.path)),
  );
  return (
    <div
      className="media-browser"
      role="region"
      aria-label={t("translate.mediaBrowser")}
    >
      <div
        className="breadcrumbs"
        role="group"
        aria-label={t("translate.mediaBreadcrumbs")}
      >
        <Button
          type="button"
          variant="outline"
          className="breadcrumb-button"
          onClick={() => onDirectoryChange("")}
        >
          {t("translate.mediaLabel")}
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
        <span>{t("translate.filterDirectory")}</span>
        <Input
          type="search"
          value={filter}
          onChange={(event) => onFilterChange(event.target.value)}
          placeholder={t("translate.typeName")}
        />
      </label>
      <div className="media-results" aria-live="polite">
        {query.isPending && (
          <div role="status" className="browser-message">
            {t("translate.loadingMedia")}
          </div>
        )}
        {query.isError && (
          <QueryErrorState error={query.error} onRetry={() => void query.refetch()} />
        )}
        {query.data && entries?.length === 0 && (
          <div className="browser-message browser-message-stack">
            {filter ? (
              <>
                <span>{t("translate.noMatchingMedia")}</span>
                <span className="field-help">{t("translate.noMatchingMediaHelp")}</span>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => onFilterChange("")}
                >
                  {t("translate.clearFilter")}
                </Button>
              </>
            ) : (
              <>
                <span>{t("translate.emptyDirectory")}</span>
                <span className="field-help">{t("translate.emptyDirectoryHelp")}</span>
              </>
            )}
          </div>
        )}
        {entries?.map((entry) => (
          <MediaEntry
            key={entry.path}
            entry={entry}
            onDirectoryChange={onDirectoryChange}
            buttonRef={
              entry.kind === "media"
                ? (button) => {
                    if (button) mediaButtonRefs.current.set(entry.path, button);
                    else mediaButtonRefs.current.delete(entry.path);
                  }
                : undefined
            }
            collapsed={
              collapseUnselected &&
              selectionActive &&
              entry.kind === "media" &&
              (batchMode
                ? !selectedMediaPaths.has(entry.path)
                : selectedMedia !== entry.path)
            }
            selected={
              batchMode
                ? selectedMediaPaths.has(entry.path)
                : selectedMedia === entry.path
            }
            onMediaSelect={onMediaSelect}
          />
        ))}
      </div>
    </div>
  );
}

function EmptyMessage({ children }: { children: ReactNode }) {
  return <div className="browser-message">{children}</div>;
}

function QueryErrorMessage({
  message,
  error,
  onRetry,
}: {
  message: string;
  error: unknown;
  onRetry: () => void;
}) {
  const { t } = useI18n();
  return (
    <div role="alert" className="browser-message error">
      {message}
      {getErrorDetail(error) && (
        <details>
          <summary>{t("translate.showErrorDetails")}</summary>
          <p className="field-help">{getErrorDetail(error)}</p>
        </details>
      )}
      <Button variant="outline" onClick={onRetry}>
        {t("common.tryAgain")}
      </Button>
    </div>
  );
}

function QueryErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <QueryErrorMessage
      message={formatError(error, t)}
      error={error}
      onRetry={onRetry}
    />
  );
}

function MediaEntry({
  entry,
  onDirectoryChange,
  selected,
  onMediaSelect,
  buttonRef,
  collapsed,
}: {
  entry: MediaDirectoryEntry;
  onDirectoryChange: (path: string) => void;
  selected: boolean;
  onMediaSelect: (path: string) => void;
  buttonRef?: (button: HTMLButtonElement | null) => void;
  collapsed: boolean;
}) {
  const { t } = useI18n();
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
      className={cn("media-entry", collapsed && "collapsed")}
      ref={buttonRef}
      data-media-path={entry.path}
      onClick={() =>
        isDirectory ? onDirectoryChange(entry.path) : onMediaSelect(entry.path)
      }
      aria-pressed={!isDirectory ? selected : undefined}
      aria-label={
        isDirectory
          ? t("translate.openDirectory", { name: accessibleLabel })
          : t("translate.selectMedia", { name: accessibleLabel })
      }
    >
      <span className="media-entry-kind">
        {isDirectory ? t("translate.directoryLabel") : t("translate.mediaLabel")}
      </span>
      <span className="media-entry-copy">
        <strong title={entry.name}>{label}</strong>
        {entry.title && <small title={entry.name}>{entry.name}</small>}
      </span>
      {!isDirectory && selected && (
        <span className="media-entry-selected">{t("translate.selected")}</span>
      )}
      {isDirectory && <span aria-hidden="true">-&gt;</span>}
    </Button>
  );
}

function ProviderState() {
  const { t } = useI18n();
  const status = useProductStatus();
  if (status.isPending) {
    return (
      <div role="status" className="provider-state">
        <SpinnerGapIcon className="spin" size={18} /> {t("runtime.checking")}
      </div>
    );
  }
  if (status.isError) {
    return (
      <div role="alert" className="provider-state error">
        <WarningCircleIcon size={18} /> <LocalizedErrorMessage error={status.error} />
      </div>
    );
  }
  if (!status.data.translation_provider.ready) {
    return (
      <div role="status" className="provider-state warning">
        <WarningCircleIcon size={18} />
        <div>
          {t("runtime.providerNotConfiguredTitle")}
          <details>
            <summary>{t("translate.showErrorDetails")}</summary>
            <p className="field-help">{status.data.translation_provider.message}</p>
          </details>
        </div>
      </div>
    );
  }
  return (
    <div role="status" className="provider-state ready">
      <CheckCircleIcon size={18} weight="fill" /> {t("translate.providerReady")}
    </div>
  );
}

function TermMapsPage() {
  const { t } = useI18n();
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
  const nameInputRef = useRef<HTMLInputElement>(null);
  const [content, setContent] = useState("");
  const [contentTouched, setContentTouched] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileReadGeneration = useRef(0);
  const [renameName, setRenameName] = useState("");
  const [loadedName, setLoadedName] = useState("");
  const [replacement, setReplacement] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const selectedIdRef = useRef(selectedId);
  const resetRename = rename.reset;
  const resetReplace = replace.reset;
  const resetRemove = remove.reset;
  const detailRef = useRef<HTMLElement>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    selectedIdRef.current = selectedId;
    resetRename();
    resetReplace();
    resetRemove();
  }, [resetRemove, resetRename, resetReplace, selectedId]);

  useEffect(() => {
    if (!selectedId || selected.data?.id !== selectedId) return;
    detailRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    detailHeadingRef.current?.focus({ preventScroll: true });
  }, [selected.data?.id, selectedId]);

  const contentValidation = useMemo(
    () => validateTermMapContent(content, t),
    [content, t],
  );
  const contentError = fileError ?? (contentTouched ? contentValidation.error : null);
  const replacementText =
    replacement ??
    (selected.data ? JSON.stringify(selected.data.content, null, 2) : "");
  const replacementValidation = useMemo(
    () => validateTermMapContent(replacementText, t),
    [replacementText, t],
  );
  const replacementDirty =
    replacement !== null &&
    replacementValidation.content !== null &&
    selected.data !== undefined &&
    !sameTermMapContent(replacementValidation.content, selected.data.content);

  async function loadTermMapFile(file: File) {
    const generation = ++fileReadGeneration.current;
    if (!file.name.toLocaleLowerCase().endsWith(".json")) {
      setFileError(t("termMaps.invalidFile"));
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
      setContentTouched(true);
      setFileName(file.name);
      setFileError(null);
    } catch {
      if (generation !== fileReadGeneration.current) return;
      setFileName(null);
      setFileError(t("termMaps.readError"));
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
    setContentTouched(true);
    if (fileLoading || fileError !== null || contentValidation.error !== null) return;
    create.mutate(
      { name, content },
      {
        onSuccess: () => {
          setSuccessMessage(t("termMaps.savedSuccess"));
          fileReadGeneration.current += 1;
          setFileLoading(false);
          setName("");
          setContent("");
          setContentTouched(false);
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
          setSuccessMessage(t("termMaps.nameSaved"));
          if (selectedIdRef.current === selectedId) setRenameName(summary.name);
          if (selectedIdRef.current === selectedId) setLoadedName(summary.name);
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
          setSuccessMessage(t("termMaps.deletedSuccess"));
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
      <PageHeader title={t("termMaps.title")} detail={t("termMaps.detail")} />
      <Guidance title={t("termMaps.guidanceTitle")}>{t("termMaps.guidance")}</Guidance>
      <section className="concept-help" aria-label={t("termMaps.createHelpLabel")}>
        <strong>{t("termMaps.createHelpTitle")}</strong>
        <ol>
          <li>{t("termMaps.createStepOne")}</li>
          <li>{t("termMaps.createStepTwo")}</li>
          <li>{t("termMaps.createStepThree")}</li>
        </ol>
        <pre>{`{
  "New York": "Nueva York",
  "The Captain": "La capitana"
}`}</pre>
        <p>{t("termMaps.createHelpDetail")}</p>
      </section>
      {successMessage && (
        <Guidance title={t("common.saved")} tone="success" role="status">
          {successMessage}
        </Guidance>
      )}
      <div className="term-map-layout">
        <section className="term-map-upload" aria-labelledby="upload-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("termMaps.newResource")}</p>
              <h2 id="upload-title">{t("termMaps.upload")}</h2>
            </div>
            <UploadSimpleIcon size={20} aria-hidden="true" />
          </div>
          <form onSubmit={submit}>
            <label>
              {t("termMaps.name")}
              <Input
                ref={nameInputRef}
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("termMaps.namePlaceholder")}
              />
            </label>
            <span className="field-help">
              A name that helps you recognize where to use it.
            </span>
            <div
              className="term-map-dropzone"
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleFileDrop}
            >
              <strong>{t("termMaps.importJson")}</strong>
              <span>{t("termMaps.jsonHelp")}</span>
              <Button
                type="button"
                variant="outline"
                onClick={() => fileInputRef.current?.click()}
              >
                {t("termMaps.selectJson")}
              </Button>
              <input
                ref={fileInputRef}
                className="sr-only"
                type="file"
                accept=".json,application/json"
                aria-label={t("termMaps.jsonFile")}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void loadTermMapFile(file);
                }}
              />
              {fileName && (
                <span className="field-help">
                  {t("termMaps.loaded", { name: fileName })}
                </span>
              )}
            </div>
            <label htmlFor="term-map-content">
              {t("termMaps.pasteJson")}
              <Textarea
                id="term-map-content"
                aria-label={t("termMaps.jsonContent")}
                required
                value={content}
                onChange={(event) => {
                  fileReadGeneration.current += 1;
                  setContent(event.target.value);
                  setContentTouched(true);
                  setFileName(null);
                  setFileError(null);
                  setFileLoading(false);
                }}
                rows={6}
                spellCheck={false}
                placeholder={'{\n  "Source": "Target"\n}'}
                aria-describedby="upload-help"
              />
            </label>
            <p id="upload-help" className="field-help">
              {t("termMaps.pasteHelp")}
            </p>
            {fileLoading ? (
              <p className="upload-status" role="status">
                {t("termMaps.readingJson")}
              </p>
            ) : contentError ? (
              <p className="form-error" role="alert">
                {contentError}
              </p>
            ) : !content.trim() ? (
              <p className="field-help" role="status">
                {t("termMaps.previewHelp")}
              </p>
            ) : (
              <p className="term-map-validation valid" role="status">
                {t("termMaps.valid", {
                  count: contentValidation.entryCount,
                  unit: t("termMaps.mapping", { count: contentValidation.entryCount }),
                })}
              </p>
            )}
            {create.isError && (
              <div className="form-error" role="alert">
                <LocalizedErrorMessage error={create.error} />
              </div>
            )}
            {create.isPending && (
              <p className="upload-status" role="status">
                {t("termMaps.uploading")}
              </p>
            )}
            <Button
              className="primary-action"
              type="submit"
              disabled={create.isPending || fileLoading || contentError !== null}
            >
              {create.isPending ? t("termMaps.uploadingButton") : t("termMaps.upload")}
            </Button>
          </form>
        </section>

        <section className="term-map-list" aria-labelledby="maps-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">{t("termMaps.library")}</p>
              <h2 id="maps-title">{t("termMaps.saved")}</h2>
            </div>
            <span className="count-badge">{maps.data?.term_maps?.length ?? 0}</span>
          </div>
          <div
            className={cn(
              "term-map-list-state",
              (maps.isPending || maps.isError || maps.data?.term_maps?.length === 0) &&
                "has-state",
            )}
          >
            {maps.isPending && (
              <div className="inline-state" role="status">
                <SpinnerGapIcon className="spin" /> {t("translate.loadingTermMaps")}
              </div>
            )}
            {maps.isError && (
              <div className="inline-state error" role="alert">
                <LocalizedErrorMessage error={maps.error} />
                <Button variant="outline" onClick={() => void maps.refetch()}>
                  {t("termMaps.retry")}
                </Button>
              </div>
            )}
            {maps.data?.term_maps?.length === 0 && (
              <div className="term-map-empty">
                <ListChecksIcon size={24} aria-hidden="true" />
                <h3>{t("termMaps.noMaps")}</h3>
                <p>{t("termMaps.emptyDetail")}</p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => nameInputRef.current?.focus()}
                >
                  {t("termMaps.createFirst")}
                </Button>
              </div>
            )}
          </div>
          <div className="term-map-items">
            {maps.data?.term_maps?.map((map) => (
              <button
                className={`term-map-item${selectedId === map.id ? " selected" : ""}`}
                aria-label={`${map.name}, ${t("termMaps.entry", { count: map.entry_count })}`}
                aria-pressed={selectedId === map.id}
                key={map.id}
                type="button"
                onClick={() => {
                  selectedIdRef.current = map.id;
                  setSelectedId(map.id);
                  setSuccessMessage(null);
                  setRenameName(map.name);
                  setLoadedName(map.name);
                  setReplacement(null);
                  setConfirmation("");
                }}
              >
                <span className="term-map-item-name" title={map.name}>
                  {map.name}
                </span>
                <span>
                  {map.entry_count} {t("termMaps.entry", { count: map.entry_count })}
                </span>
                <time
                  dateTime={map.updated_at}
                  title={formatLocalTimestamp(map.updated_at)}
                >
                  {formatRelativeTimestamp(map.updated_at)}
                </time>
              </button>
            ))}
          </div>
        </section>
      </div>

      {selectedId && (
        <section
          ref={detailRef}
          className="term-map-detail"
          aria-labelledby="detail-title"
        >
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
                  setLoadedName("");
                  setReplacement(null);
                  setConfirmation("");
                }}
              >
                <ArrowLeftIcon size={16} aria-hidden="true" /> {t("termMaps.back")}
              </Button>
              <h2 ref={detailHeadingRef} id="detail-title" tabIndex={-1}>
                {selected.data?.name ?? t("termMaps.details")}
              </h2>
              {selected.data && (
                <p>
                  {selected.data.entry_count}{" "}
                  {t("termMaps.entry", { count: selected.data.entry_count })} ·{" "}
                  {t("termMaps.updated")}{" "}
                  <time dateTime={selected.data.updated_at}>
                    {formatLocalTimestamp(selected.data.updated_at)}
                  </time>
                </p>
              )}
              {selected.data && (
                <div className="term-map-actions">
                  <Input
                    aria-label={t("termMaps.newName")}
                    value={renameName}
                    placeholder={selected.data.name}
                    onChange={(event) => setRenameName(event.target.value)}
                    disabled={rename.isPending}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={renameSelected}
                    disabled={
                      rename.isPending ||
                      !renameName.trim() ||
                      renameName === loadedName
                    }
                  >
                    {t("termMaps.saveName")}
                  </Button>
                  {rename.isError && (
                    <div className="form-error" role="alert">
                      <LocalizedErrorMessage error={rename.error} />
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="term-map-detail-state">
            {selected.isPending && (
              <div className="inline-state" role="status">
                <SpinnerGapIcon className="spin" /> {t("termMaps.loadingDetails")}
              </div>
            )}
            {selected.isError && (
              <div className="inline-state error" role="alert">
                <LocalizedErrorMessage error={selected.error} />
                <Button variant="outline" onClick={() => void selected.refetch()}>
                  {t("common.tryAgain")}
                </Button>
              </div>
            )}
            {selected.data && (
              <>
                <label className="search-field">
                  <MagnifyingGlassIcon size={17} aria-hidden="true" />
                  <span>{t("termMaps.search")}</span>
                  <Input
                    aria-label={t("termMaps.search")}
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder={t("termMaps.filter")}
                  />
                </label>
                <div className="term-table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("termMaps.source")}</th>
                        <th>{t("termMaps.target")}</th>
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
                    <p className="table-empty">{t("termMaps.noMatchingTerms")}</p>
                  )}
                </div>
                <div className="term-map-management">
                  <h3>{t("termMaps.replaceJson")}</h3>
                  <p className="field-help">{t("termMaps.replaceHelp")}</p>
                  <Textarea
                    aria-label={t("termMaps.replacementJson")}
                    value={replacementText}
                    onChange={(event) => setReplacement(event.target.value)}
                    rows={7}
                    spellCheck={false}
                    disabled={replace.isPending}
                  />
                  {replacement !== null && replacementValidation.error && (
                    <p className="form-error" role="alert">
                      {replacementValidation.error}
                    </p>
                  )}
                  {replace.isError && (
                    <div className="form-error" role="alert">
                      <LocalizedErrorMessage error={replace.error} />
                    </div>
                  )}
                  <Button
                    type="button"
                    className="primary-action"
                    onClick={() =>
                      replace.mutate(
                        {
                          id: selected.data.id,
                          content: replacementText,
                        },
                        {
                          onSuccess: () => {
                            setSuccessMessage(
                              `Term map replaced with ${replacementValidation.entryCount} mappings.`,
                            );
                            if (selectedIdRef.current === selected.data.id) {
                              setReplacement(null);
                            }
                          },
                        },
                      )
                    }
                    disabled={replace.isPending || !replacementDirty}
                  >
                    {replace.isPending
                      ? t("termMaps.replacing")
                      : t("termMaps.replaceContent")}
                  </Button>
                  <div className="term-map-delete">
                    <h3>{t("termMaps.deleteTitle")}</h3>
                    <p>{t("termMaps.deleteHelp", { name: selected.data.name })}</p>
                    <Input
                      aria-label={t("termMaps.confirmName")}
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
                      {remove.isPending
                        ? t("termMaps.deleting")
                        : t("termMaps.deleteMap")}
                    </Button>
                    {remove.isError && (
                      <div className="form-error" role="alert">
                        <LocalizedErrorMessage error={remove.error} />
                      </div>
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
    <ThemeProvider>
      <I18nProvider>
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
      </I18nProvider>
    </ThemeProvider>
  );
}
