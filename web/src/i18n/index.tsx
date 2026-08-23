/* eslint-disable react-refresh/only-export-components -- This module intentionally exposes the i18n API beside its provider. */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import zhCN from "./locales/zh-CN.json";
import zhTW from "./locales/zh-TW.json";
import ja from "./locales/ja.json";
import ko from "./locales/ko.json";
import es from "./locales/es.json";
import fr from "./locales/fr.json";
import de from "./locales/de.json";
import ptBR from "./locales/pt-BR.json";

export const UI_LOCALE_STORAGE_KEY = "cueweaver.ui-locale";

export const SUPPORTED_LOCALES = [
  "en",
  "zh-CN",
  "zh-TW",
  "ja",
  "ko",
  "es",
  "fr",
  "de",
  "pt-BR",
] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];

export const LOCALE_OPTIONS: ReadonlyArray<{ code: Locale; label: string }> = [
  { code: "en", label: "English" },
  { code: "zh-CN", label: "简体中文" },
  { code: "zh-TW", label: "繁體中文" },
  { code: "ja", label: "日本語" },
  { code: "ko", label: "한국어" },
  { code: "es", label: "Español" },
  { code: "fr", label: "Français" },
  { code: "de", label: "Deutsch" },
  { code: "pt-BR", label: "Português (Brasil)" },
];

const ENGLISH = {
  "language.label": "Language",
  "language.change": "Change interface language",
  "navigation.primary": "Primary navigation",
  "navigation.mobile": "Mobile navigation",
  "navigation.translate": "Translate",
  "navigation.jobs": "Jobs",
  "navigation.termMaps": "Term maps",
  "theme.darkMode": "Dark mode",
  "theme.on": "On",
  "theme.off": "Off",
  "runtime.checking": "Checking runtime",
  "runtime.provider": "Provider needs configuration",
  "runtime.ready": "Runtime ready",
  "runtime.unavailable": "Runtime unavailable",
  "runtime.recordsAttention": "Job records need attention",
  "runtime.unreachableTitle": "CueWeaver is not reachable",
  "runtime.tryAgain": "Try again",
  "runtime.unreachableDetail":
    "The app could not check whether translation is available. Try again before starting a Job.",
  "runtime.attentionTitle": "CueWeaver needs attention",
  "runtime.attentionDetail":
    "The Media or Work directory is unavailable. Check the configured mounts and permissions before starting a translation. You can still manage saved Term maps and review existing Jobs.",
  "runtime.providerNotConfiguredTitle": "Translation is not configured",
  "runtime.providerNotConfiguredDetail":
    "Set PROVIDER and the matching provider environment variables, then restart CueWeaver. You can still browse Media and manage Term maps.",
  "jobs.notifications": "Job notifications",
  "jobs.dismissNotification": "Dismiss notification",
  "jobs.notificationCompleted": "{media} translation completed.",
  "jobs.notificationFailed": "{media} translation failed: {error}",
  "jobs.notificationDetails": "Check Job details.",
  "common.loading": "Loading...",
  "common.tryAgain": "Try again",
  "common.cancel": "Cancel",
  "common.delete": "Delete",
  "common.save": "Save",
  "common.saved": "Saved",
  "common.close": "Close",
  "common.back": "Back",
  "common.remove": "Remove",
  "translate.title": "Translate",
  "translate.detail":
    "Choose a Media item and subtitle, then create a background translation Job.",
  "translate.quickStartTitle": "How it works",
  "translate.quickStartStepOne": "Choose a Media item and one subtitle source.",
  "translate.quickStartStepTwo":
    "Choose the language you want the subtitle translated into.",
  "translate.quickStartStepThree": "Start the Job and follow it in Jobs.",
  "translate.backgroundNote":
    "CueWeaver runs translations in the background. When a Job completes, the translated subtitle is saved beside the Media item.",
  "translate.batchDetail":
    "Translate several Media items with the same language and output settings.",
  "translate.autoSelectHelp":
    "Automatically select Media with exactly one complete subtitle.",
  "translate.advancedHelp":
    "Optional terminology controls. The defaults work for most translations.",
  "translate.dynamicTerminologyHelp":
    "Let the translator identify useful terms while it works.",
  "translate.subtitleTerminologyHelp":
    "Focus terminology handling on text found in the chosen subtitle.",
  "translate.appliedEveryDetail": "The suffix becomes part of each output filename.",
  "translate.suffixTargetHelp": "The suffix does not change the target language.",
  "translate.termMapHelp":
    "A Term map is a reusable set of source and target terms that should stay consistent. You can follow the Directory default, choose one, or continue without one.",
  "translate.directoryDefaultScopeHelp":
    "This Term map is offered automatically for Media in this directory and its child directories. A translation can still choose another Term map or use none.",
  "translate.noTermMapsHelp":
    "No saved Term maps. You can continue without one or create a Term map first.",
  "translate.multipleSubtitlesHelp":
    "More than one subtitle is available. Choose the source you want to translate.",
  "translate.noSubtitlesHelp":
    "No usable subtitles were found. Add an External subtitle such as Movie.en.srt beside the Media item, or choose Media with an Embedded text subtitle.",
  "translate.unsupportedSubtitleHelp":
    "This is not a supported text subtitle. Choose another source.",
  "translate.noMatchingMediaHelp": "No names match this filter.",
  "translate.clearFilter": "Clear filter",
  "translate.emptyDirectoryHelp":
    "This directory contains no supported Media items or subdirectories. CueWeaver reads Media from the configured Media root.",
  "translate.nextChecking": "Checking whether CueWeaver is ready.",
  "translate.nextRuntimeError": "CueWeaver status could not be checked. Try again.",
  "translate.nextConfigureRoots": "Check the Media and Work directory configuration.",
  "translate.nextChooseMedia": "Next: choose a Media item.",
  "translate.nextChooseMediaBatch": "Next: choose one or more Media items.",
  "translate.nextChooseSubtitleForMedia":
    "Next: choose a subtitle for {count} selected Media.",
  "translate.nextChooseSubtitle": "Next: choose one subtitle source.",
  "translate.nextChooseLanguage": "Next: choose a target language.",
  "translate.nextProviderUnavailable":
    "Translation is unavailable until the provider is configured and CueWeaver is restarted.",
  "translate.nextReady": "Ready. Starting will create {count} background {unit}.",
  "translate.chooseMedia": "Choose media",
  "translate.chooseMediaDetail": "Select a Media and discover its subtitles.",
  "translate.batchMode": "Batch mode",
  "translate.selectUnique": "Select unique",
  "translate.searchMedia": "Language, name, path, format, or tags",
  "translate.loadingMedia": "Loading Media...",
  "translate.noMedia": "No Media was found in this directory.",
  "translate.noSubtitles": "No subtitles were found for this Media.",
  "translate.searchSubtitles": "Search subtitle candidates",
  "translate.chooseSubtitle": "Choose a subtitle",
  "translate.sourceDiscovered": "Sources discovered for {name}.",
  "translate.chooseAnotherMedia": "Choose another Media",
  "translate.selectAnotherMedia": "Select another Media",
  "translate.loadingSubtitles": "Loading subtitles",
  "translate.multipleSubtitles":
    "Multiple subtitles found. Select one candidate to continue.",
  "translate.resolveCandidates": "Resolve candidates",
  "translate.noCandidateMatch": "No subtitle candidates match this filter.",
  "translate.configure": "Configure translation",
  "translate.configureDetail":
    "Select a subtitle source and choose the language you want to translate into.",
  "translate.targetLanguage": "Target language",
  "translate.commonTargetLanguage": "Common target language",
  "translate.chooseLanguage": "Choose a language",
  "translate.customLanguage": "Custom language code",
  "translate.targetLanguageCode": "Target language code",
  "translate.targetLanguagePlaceholder": "zh-Hans",
  "translate.targetLanguageHelp":
    "Choose a common language or enter a BCP 47 code such as zh-Hans, pt-BR, or ja.",
  "translate.termMap": "Term map for this translation",
  "translate.noTermMap": "No Term map",
  "translate.directoryDefault": "Directory default",
  "translate.specificTermMap": "Specific Term map",
  "translate.advanced": "Advanced settings",
  "translate.dynamicTerminology": "Dynamic terminology",
  "translate.subtitleTerminology": "Subtitle terminology filtering",
  "translate.output": "Output",
  "translate.outputSuffix": "Subtitle suffix",
  "translate.outputConflict": "If the output filename already exists",
  "translate.skipExisting": "Skip existing output",
  "translate.noJobIfOutputExists": "(No Job if output exists)",
  "translate.appendNumber": "Append a number",
  "translate.appendNumberRecommended": "Append a number (recommended)",
  "translate.overwrite": "Overwrite existing output",
  "translate.queueing": "Queueing...",
  "translate.start": "Start translation",
  "translate.queueSelected": "Queue selected translations",
  "translate.selectMedia": "Select {name}",
  "translate.selectSubtitle": "Select {kind} subtitle {details}",
  "translate.streamAccessible": "stream {index}",
  "translate.selected": "Selected",
  "translate.externalSubtitle": "External subtitle",
  "translate.embeddedSubtitle": "Embedded subtitle",
  "translate.externalKind": "external",
  "translate.embeddedKind": "embedded",
  "translate.stream": "Stream {index}",
  "translate.incompleteCandidate": "Incomplete candidate",
  "translate.unsupported": "Unsupported subtitle candidate",
  "translate.unsupportedLabel": "Unsupported {kind} subtitle",
  "translate.unavailableSubtitle": "Unavailable subtitle",
  "translate.notSelectable": "Not selectable",
  "translate.metadataUnavailable": "Metadata unavailable",
  "translate.providerReady": "Translation provider ready",
  "translate.providerUnavailable":
    "Translation is unavailable until the provider is configured.",
  "translate.queued": "Translation queued",
  "translate.queuedEyebrow": "Queued Job",
  "translate.queuedDetail": "The translation is ready to run in the queue.",
  "translate.skipped": "Translation skipped",
  "translate.translateAnother": "Translate another",
  "translate.mediaSummary": "Media",
  "translate.targetLanguageUnavailable": "Target language unavailable",
  "translate.mediaRoot": "Media root",
  "translate.subtitleSelectionFor": "Subtitle selection for {name}",
  "translate.unknownFormat": "Unknown format",
  "translate.disposition.default": "Default",
  "translate.disposition.forced": "Forced",
  "translate.disposition.hearingImpaired": "Hearing impaired",
  "translate.disposition.visualImpaired": "Visually impaired",
  "translate.disposition.commentary": "Commentary",
  "translate.disposition.lyrics": "Lyrics",
  "translate.disposition.karaoke": "Karaoke",
  "translate.disposition.original": "Original",
  "translate.disposition.dubbed": "Dubbed",
  "translate.disposition.cleanEffects": "Clean effects",
  "translate.job": "Job",
  "translate.jobs": "Jobs",
  "translate.viewJob": "View Job",
  "translate.outputExists": "Output already exists",
  "translate.noJobCreated": "No Job was created.",
  "translate.batchQueuedSummary": "{queued} queued as Job(s){skipped} skipped{errors}.",
  "translate.queuedCount": "{count} {unit} queued",
  "translate.skippedCount": "{count} {unit} skipped",
  "translate.errorCount": "{count} {unit}",
  "translate.batchSummary": "{queued}{skipped}{errors}.",
  "translate.batchItem": "item",
  "translate.batchItems": "items",
  "translate.batchError": "error",
  "translate.batchErrors": "errors",
  "translate.queuedAsJob": "Queued as Job {id}",
  "translate.skippedReason": "Skipped: {reason}",
  "translate.existingOutput": "Existing output: {path}",
  "translate.showErrorDetails": "Show error details",
  "translate.errorCode": "Error code",
  "translate.batchResults": "Batch results",
  "translate.batchSubmissionResults": "Batch submission results",
  "translate.batchResult": "batch result",
  "translate.sharedOutputSettings": "Shared output settings",
  "translate.appliedEvery": "Applied to every queued translation.",
  "translate.outputFilename": "Output filename:",
  "translate.loadingTermMaps": "Loading Term maps",
  "translate.noTermMapJob": "No Term map for this Job",
  "translate.termMapPolicyHelp":
    "Follow the Directory default, explicitly use no Term map, or choose a specific Term map for this translation.",
  "translate.directoryOption": "Directory: {name}",
  "translate.mediaBrowser": "Media browser",
  "translate.mediaBreadcrumbs": "Media breadcrumbs",
  "translate.currentDirectory": "Current directory: {name}",
  "translate.currentDirectoryRoot": "Current directory: Media root",
  "translate.directoryDefaultHelp":
    "Applies to Media beneath the current directory unless a Job overrides or disables it.",
  "translate.localBinding": "Local binding",
  "translate.effectiveTermMap": "Effective Term map",
  "translate.noDefault": "No default",
  "translate.inheritedFrom": "Inherited from {name}",
  "translate.chooseTermMap": "Choose a Term map",
  "translate.binding": "Binding...",
  "translate.replaceLocalBinding": "Replace local binding",
  "translate.bindTermMap": "Bind Term map",
  "translate.removing": "Removing...",
  "translate.removeLocalBinding": "Remove local binding",
  "translate.filterDirectory": "Filter this directory",
  "translate.typeName": "Type a name",
  "translate.directory": "Directory",
  "translate.media": "Media",
  "translate.noMatchingMedia": "No matching Media or directories.",
  "translate.openDirectory": "Open {name}",
  "translate.directoryLabel": "Directory",
  "translate.mediaLabel": "Media",
  "translate.emptyDirectory": "This directory is empty.",
  "termMaps.guidanceTitle": "What is a Term map?",
  "termMaps.guidance":
    "A Term map pairs text from the source subtitle with the translation you prefer. Use one for character names, places, brands, or phrases that must stay consistent. You do not need one to translate subtitles.",
  "termMaps.createHelpLabel": "How to create a Term map",
  "termMaps.createHelpTitle": "How to create one",
  "termMaps.createStepOne": "List important terms from the source language.",
  "termMaps.createStepTwo": "Pair each term with the translation you prefer.",
  "termMaps.createStepThree": "Paste the JSON or import a .json file below.",
  "termMaps.createHelpDetail":
    "The left side is source subtitle text. The right side is the preferred translation.",
  "termMaps.createFirst": "Create your first Term map",
  "termMaps.savedSuccess": "Term map saved. It is now available on Translate.",
  "termMaps.nameSaved": "Term map name saved.",
  "termMaps.deletedSuccess":
    "Term map deleted. Directory defaults using it were cleared.",
  "jobs.title": "Jobs",
  "jobs.detail": "Review durable translation history, diagnostics, and retryable work.",
  "jobs.history": "Job history",
  "jobs.filterHistory": "Filter Job history",
  "jobs.search": "Search Jobs",
  "jobs.searchPlaceholder": "Media, language, or Job ID",
  "jobs.status": "Status",
  "jobs.allStatuses": "All statuses",
  "jobs.matching": "{count} matching",
  "jobs.clearCompleted": "Clear completed history",
  "jobs.clearConfirmation":
    "Clear all completed Job history? This removes {count} completed Job{plural} and residual Work data. Media and published output are preserved.",
  "jobs.clearConfirmationSingular":
    "Clear all completed Job history? This removes {count} completed Job and residual Work data. Media and published output are preserved.",
  "jobs.clearConfirmationPlural":
    "Clear all completed Job history? This removes {count} completed Jobs and residual Work data. Media and published output are preserved.",
  "jobs.clearing": "Clearing...",
  "jobs.clearScope":
    "Applies to all completed Jobs, regardless of the current filters.",
  "jobs.noJobs": "No Jobs yet",
  "jobs.noJobsDetail":
    "Submitted translations will appear here with their current state.",
  "jobs.startTranslation": "Start a translation",
  "jobs.loading": "Loading Jobs",
  "jobs.loadingDetails": "Loading Job details",
  "jobs.requestSummary": "Request summary",
  "jobs.created": "Created",
  "jobs.started": "Started",
  "jobs.finished": "Finished",
  "jobs.queuePosition": "Queue position",
  "jobs.output": "Output",
  "jobs.stateHistory": "Status history",
  "jobs.actionNeeded": "Action needed",
  "jobs.error": "Error",
  "jobs.retry": "Retry Job",
  "jobs.delete": "Delete Job",
  "jobs.back": "Back to Jobs",
  "jobs.someCompletedFailed": "Some Completed Jobs could not be cleared.",
  "jobs.noCompletedCleared": "No Completed Jobs could be cleared.",
  "jobs.clearSuccessTitle": "History cleared",
  "jobs.clearPartialTitle": "History partially cleared",
  "jobs.clearFailedTitle": "History could not be cleared",
  "jobs.clearSuccess":
    "Cleared {count} completed {unit}. Media and published subtitles were not deleted.",
  "jobs.clearPartial":
    "Cleared {count} completed {unit}. {failed} completed {failedUnit} could not be cleared. See the details below.",
  "jobs.clearFailed": "No completed Jobs were cleared. See the details below.",
  "jobs.actionAvailable": "Action available",
  "jobs.retryGuidance":
    "Review the error below and fix the provider, Media, or subtitle source problem, then choose Retry Job. Retry reuses this Job's saved request, including its Term map. To change the Term map or other translation settings, start a new translation.",
  "jobs.requestSummaryDetail":
    "These are the settings saved when this Job was created.",
  "jobs.savedOutput": "Saved output",
  "jobs.plannedOutput": "Planned output",
  "jobs.diagnosticsDetail": "Technical details are provided for troubleshooting.",
  "jobs.job": "Job",
  "jobs.jobs": "Jobs",
  "jobs.jobPrefix": "Job {id}",
  "jobs.persistenceWarning": "Persistence warning",
  "jobs.recordsExcluded":
    "These records were kept out of active history and need operator review.",
  "jobs.recordCount": "{count} {unit} in",
  "jobs.record": "record",
  "jobs.records": "records",
  "jobs.corrupt": "Corrupt",
  "jobs.unsupported": "Unsupported",
  "jobs.noLongerAvailable": "This Job is no longer available.",
  "jobs.sourceTo": "{source} to {target}",
  "jobs.jobId": "Job {id}",
  "jobs.termMapLabel": "Term map: {name}",
  "jobs.deleteConfirmation":
    "{action} {id}? This removes its Job history and residual Work data. Media and published output are preserved.",
  "jobs.attemptLabel": "Attempt {attempt}",
  "jobs.cancelConfirmation":
    "{action} Job {id}? It will remain in Job history and will not be translated.",
  "jobs.runningCannotCancel": "Running Jobs cannot be cancelled.",
  "jobs.status.Queued": "Queued",
  "jobs.status.Extracting": "Extracting",
  "jobs.status.Translating": "Translating",
  "jobs.status.Completed": "Completed",
  "jobs.status.Failed": "Failed",
  "jobs.status.Interrupted": "Interrupted",
  "jobs.status.Cancelled": "Cancelled",
  "jobs.statusExplanation.Queued":
    "Waiting for the worker. Only one Job runs at a time.",
  "jobs.statusExplanation.Extracting":
    "Preparing an Embedded subtitle for translation.",
  "jobs.statusExplanation.Translating":
    "The translation provider is translating the subtitle.",
  "jobs.statusExplanation.Completed": "The translated subtitle was saved successfully.",
  "jobs.statusExplanation.Failed":
    "Translation stopped because of an error. Fix the cause, then retry.",
  "jobs.statusExplanation.Interrupted":
    "CueWeaver stopped before the Job finished. It can be retried.",
  "jobs.statusExplanation.Cancelled": "The Job was cancelled before translation.",
  "jobs.statusOption.Queued": "Queued status",
  "jobs.statusOption.Extracting": "Extracting status",
  "jobs.statusOption.Translating": "Translating status",
  "jobs.statusOption.Completed": "Completed history",
  "jobs.statusOption.Failed": "Failed history",
  "jobs.statusOption.Interrupted": "Interrupted history",
  "jobs.statusOption.Cancelled": "Cancelled history",
  "jobs.detailsRegion": "Job details",
  "jobs.statusHistoryLabel": "Job status history",
  "jobs.translationJobs": "Translation Jobs",
  "jobs.noMatching": "No matching Jobs",
  "jobs.noMatchingDetail": "Try a different search or clear the filters.",
  "jobs.clearFilters": "Clear filters",
  "jobs.active": "Active Jobs",
  "jobs.completedAndPast": "Completed and past Jobs",
  "jobs.refreshingHistory": "Refreshing history...",
  "jobs.loadingHistory": "Loading history...",
  "jobs.loadMoreHistory": "Load more history",
  "jobs.cancelJob": "Cancel Job",
  "jobs.cancelling": "Cancelling...",
  "jobs.retrying": "Retrying...",
  "jobs.deleting": "Deleting...",
  "jobs.copyId": "Copy Job ID",
  "jobs.copied": "Copied",
  "jobs.copyManually": "Select the Job ID and copy it manually.",
  "jobs.media": "Media",
  "jobs.source": "Source",
  "jobs.outputFormat": "Output format",
  "jobs.termMapPolicy": "Term map policy",
  "jobs.termMapSnapshot": "Term map snapshot",
  "jobs.attempt": "Attempt",
  "jobs.enabled": "Enabled",
  "jobs.disabled": "Disabled",
  "jobs.none": "None",
  "jobs.timestampsLocal": "Timestamps (local time)",
  "jobs.statusUnavailable": "Status history unavailable for this Job.",
  "jobs.showDiagnostics": "Show approved diagnostic context",
  "jobs.errorCode": "Error code",
  "jobs.select": "Select a Job",
  "jobs.selectDetail":
    "Choose a Job from history to inspect its configuration, output, and diagnostics.",
  "jobs.termMap.follow": "Follow directory default",
  "jobs.termMap.none": "Explicitly disabled",
  "jobs.termMap.selected": "Explicit Term map",
  "termMaps.title": "Term maps",
  "termMaps.detail":
    "Keep reusable terminology precise and available across translations.",
  "termMaps.saved": "Saved Term maps",
  "termMaps.upload": "Upload Term map",
  "termMaps.newResource": "New resource",
  "termMaps.name": "Name",
  "termMaps.importJson": "Import JSON file",
  "termMaps.jsonHelp": "Use a .json file as one supported input path.",
  "termMaps.selectJson": "Select JSON file",
  "termMaps.jsonFile": "JSON file",
  "termMaps.jsonContent": "JSON content",
  "termMaps.invalidFile": "Choose a .json file containing a Term map.",
  "termMaps.readError": "The selected JSON file could not be read.",
  "termMaps.loaded": "Loaded {name}",
  "termMaps.pasteJson": "Paste JSON directly",
  "termMaps.pasteHelp":
    "Or paste a non-empty object of Source-to-Target strings, up to 1 MiB.",
  "termMaps.readingJson": "Reading JSON file...",
  "termMaps.previewHelp":
    "Add JSON using the file import or paste path to preview its mappings.",
  "termMaps.valid": "Valid Term map: {count} {unit}.",
  "termMaps.mapping": "mapping",
  "termMaps.mappings": "mappings",
  "termMaps.uploading": "Uploading Term map",
  "termMaps.uploadingButton": "Uploading...",
  "termMaps.library": "Library",
  "termMaps.retry": "Try again",
  "termMaps.emptyDetail":
    "Upload a JSON Term map to make consistent terminology reusable.",
  "termMaps.entry": "entry",
  "termMaps.entries": "entries",
  "termMaps.back": "Back to Term maps",
  "termMaps.details": "Term map details",
  "termMaps.updated": "Updated",
  "termMaps.newName": "New Term map name",
  "termMaps.saveName": "Save name",
  "termMaps.loadingDetails": "Loading details",
  "termMaps.search": "Search Source or Target",
  "termMaps.filter": "Type to filter",
  "termMaps.source": "Source",
  "termMaps.target": "Target",
  "termMaps.noMatchingTerms": "No matching terms.",
  "termMaps.replaceJson": "Replace all JSON content",
  "termMaps.replaceHelp":
    "This removes all current mappings and fully replaces them with the JSON below; it does not merge with existing content. Jobs already created keep the Term map content captured when they were queued.",
  "termMaps.replacementJson": "Replacement JSON content",
  "termMaps.replacing": "Replacing...",
  "termMaps.replaceContent": "Replace content",
  "termMaps.deleteTitle": "Delete Term map",
  "termMaps.deleteHelp":
    'This permanently deletes the Term map and clears Directory defaults that use it. Media, Jobs, and published subtitles are not deleted. Enter "{name}" to confirm.',
  "termMaps.confirmName": "Confirm Term map name",
  "termMaps.deleting": "Deleting...",
  "termMaps.deleteMap": "Delete Term map",
  "termMapValidation.enterObject":
    'Enter a Term map JSON object, such as {"Source":"Target"}.',
  "termMapValidation.validJson": 'Enter valid JSON, such as {"Source":"Target"}.',
  "termMapValidation.object": "Term map JSON must be a non-empty object.",
  "termMapValidation.mapping": "Term map JSON must contain at least one mapping.",
  "termMapValidation.source": "Source keys must be non-empty strings.",
  "termMapValidation.unicode": "Term map must contain valid Unicode strings.",
  "termMapValidation.unique":
    "Source keys must be unique regardless of case; remove the duplicate mapping.",
  "termMapValidation.target": "Target values must be non-empty strings.",
  "termMapValidation.size": "Term map must be at most 1 MiB.",
  "termMapValidation.invalid": "Term map content is invalid",
  "translate.suffixRequired": "Subtitle suffix must be non-empty.",
  "translate.suffixSegmentRequired": "Subtitle suffix segments cannot be empty.",
  "translate.suffixTrailingSpace": "Subtitle suffix segments cannot end in a space.",
  "translate.suffixReserved": "Subtitle suffix contains a reserved filename segment.",
  "translate.suffixUnsafe": "Subtitle suffix contains an unsafe character.",
  "termMaps.namePlaceholder": "Name it by media, season, language pair, and version.",
  "termMaps.noMaps": "No Term maps yet",
  "termMaps.noMapsDetail": "Upload a JSON Term map to reuse terminology across Jobs.",
  "termMaps.deleteConfirmation":
    "Delete this Term map? Jobs that already reference it will keep their recorded configuration.",
  "errors.statusUnavailable": "CueWeaver status is unavailable.",
  "errors.mediaDirectory": "This Media directory could not be loaded.",
  "errors.subtitleDiscovery": "Subtitles could not be discovered.",
  "errors.jobs": "Jobs could not be loaded.",
  "errors.invalidJobsResponse": "Jobs response has an invalid shape.",
  "errors.jobDetails": "Job details could not be loaded.",
  "errors.queue": "Translation could not be queued.",
  "errors.batchQueue": "Translations could not be queued.",
  "errors.invalidBatchResponse": "Batch response has an invalid shape.",
  "errors.retry": "Job could not be retried.",
  "errors.cancel": "Job could not be cancelled.",
  "errors.delete": "Job could not be deleted.",
  "errors.termMapOperation": "Term map operation failed",
  "errors.clearCompleted": "Completed Jobs could not be cleared.",
  "errors.unknown": "An unexpected error occurred.",
  "time.notRecorded": "Not recorded",
  "time.timestampUnavailable": "Timestamp unavailable",
  "time.unavailable": "Time unavailable",
  "time.utc": "UTC",
} as const;

export type TranslationKey = keyof typeof ENGLISH;
type TranslationTable = Record<TranslationKey, string>;

export class LocalizedError extends Error {
  constructor(
    readonly translationKey: TranslationKey,
    readonly detail?: string,
  ) {
    super(detail ?? translationKey);
    this.name = "LocalizedError";
  }
}

export function localizedError(key: TranslationKey, detail?: string): LocalizedError {
  return new LocalizedError(key, detail);
}

export function formatError(
  error: unknown,
  t: (key: TranslationKey) => string = translate,
): string {
  if (error instanceof LocalizedError) return error.detail ?? t(error.translationKey);
  if (error instanceof Error) return error.message;
  return t("errors.unknown");
}

const TRANSLATIONS: Record<Locale, TranslationTable> = {
  en: ENGLISH,
  "zh-CN": zhCN,
  "zh-TW": zhTW,
  ja,
  ko,
  es,
  fr,
  de,
  "pt-BR": ptBR,
};

let activeLocale: Locale = "en";

function readStoredLocale(): string | null {
  try {
    return typeof window === "undefined"
      ? null
      : window.localStorage.getItem(UI_LOCALE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(UI_LOCALE_STORAGE_KEY, locale);
  } catch {
    // The UI remains usable when browser storage is unavailable.
  }
}

export function resolveLocale(value: string | null | undefined): Locale {
  if (!value) return "en";
  const normalized = value.trim().toLowerCase().replaceAll("_", "-");
  const exact = SUPPORTED_LOCALES.find((locale) => locale.toLowerCase() === normalized);
  if (exact) return exact;
  if (normalized === "zh" || normalized.startsWith("zh-")) {
    const parts = normalized.split("-");
    if (
      parts.includes("hant") ||
      ["tw", "hk", "mo"].some((part) => parts.includes(part))
    )
      return "zh-TW";
    return "zh-CN";
  }
  const base = normalized.split("-")[0];
  return SUPPORTED_LOCALES.find((locale) => locale.toLowerCase() === base) ?? "en";
}

function isSupportedLocaleValue(value: string): boolean {
  const normalized = value.trim().toLowerCase().replaceAll("_", "-");
  const locale = resolveLocale(value);
  return locale !== "en" || normalized === "en" || normalized.startsWith("en-");
}

export function detectLocale(
  storedValue: string | null = readStoredLocale(),
  browserValues: readonly string[] = typeof navigator === "undefined"
    ? []
    : navigator.languages,
): Locale {
  if (storedValue && isSupportedLocaleValue(storedValue)) {
    return resolveLocale(storedValue);
  }
  for (const value of browserValues) {
    if (isSupportedLocaleValue(value)) return resolveLocale(value);
  }
  return "en";
}

export function setActiveLocale(locale: Locale): void {
  activeLocale = locale;
  if (typeof document !== "undefined") document.documentElement.lang = locale;
}

export function getActiveLocale(): Locale {
  return activeLocale;
}

export function translate(
  key: TranslationKey,
  values: Record<string, string | number> = {},
  locale: Locale = activeLocale,
): string {
  const template = TRANSLATIONS[locale][key] ?? ENGLISH[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name: string) =>
    String(values[name] ?? `{${name}}`),
  );
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: TranslationKey, values?: Record<string, string | number>) => string;
  localeOptions: ReadonlyArray<{ code: Locale; label: string }>;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const detected = detectLocale();
    setActiveLocale(detected);
    return detected;
  });

  const setLocale = (nextLocale: Locale) => {
    setLocaleState(nextLocale);
    setActiveLocale(nextLocale);
    storeLocale(nextLocale);
  };

  useEffect(() => {
    setActiveLocale(locale);
  }, [locale]);

  const value = useMemo(
    () => ({
      locale,
      setLocale,
      t: (key: TranslationKey, values?: Record<string, string | number>) =>
        translate(key, values, locale),
      localeOptions: LOCALE_OPTIONS,
    }),
    [locale],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside I18nProvider");
  return value;
}

export function getTranslationKeys(): readonly TranslationKey[] {
  return Object.keys(ENGLISH) as TranslationKey[];
}

export function getLocaleTable(locale: Locale): TranslationTable {
  return TRANSLATIONS[locale];
}

export function getUntranslatedKeys(locale: Locale): readonly TranslationKey[] {
  const table = getLocaleTable(locale);
  return getTranslationKeys().filter((key) => table[key] === ENGLISH[key]);
}
