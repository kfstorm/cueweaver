/* eslint-disable react-refresh/only-export-components -- The provider and hook share an in-memory draft. */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { OutputConflictPolicy, TermMapMode } from "./jobs";

interface TranslationDraft {
  profile: string;
  directory: string;
  filter: string;
  media: string | null;
  batch: boolean;
  batchMedia: Set<string>;
  batchSubtitles: Map<string, string>;
  expandedMedia: Set<string>;
  browserExpanded: boolean;
  subtitleFilter: string;
  subtitle: string | null;
  language: string;
  languageChoice: string;
  suffix: string;
  conflictPolicy: OutputConflictPolicy;
  suffixEdited: boolean;
  termMapMode: TermMapMode;
  termMapId: string | null;
  dynamicTerminology: boolean;
  subtitleTerminology: boolean;
}

interface DraftStore {
  read<K extends keyof TranslationDraft>(key: K): TranslationDraft[K] | undefined;
  write<K extends keyof TranslationDraft>(key: K, value: TranslationDraft[K]): void;
}

const DraftContext = createContext<DraftStore | null>(null);

export function TranslationDraftProvider({ children }: { children: ReactNode }) {
  const [draft] = useState<DraftStore>(() => {
    const values: Partial<TranslationDraft> = {};
    return {
      read: (key) => values[key],
      write: (key, value) => {
        values[key] = value;
      },
    };
  });
  return <DraftContext.Provider value={draft}>{children}</DraftContext.Provider>;
}

// Keep the form across route changes without persisting media paths to browser storage.
export function useTranslationDraft<K extends keyof TranslationDraft>(
  key: K,
  initial: TranslationDraft[K] | (() => TranslationDraft[K]),
) {
  const draft = useContext(DraftContext);
  if (!draft) throw new Error("TranslationDraftProvider is required");
  const state = useState<TranslationDraft[K]>(() => {
    const stored = draft.read(key);
    return stored !== undefined
      ? stored
      : typeof initial === "function"
        ? (initial as () => TranslationDraft[K])()
        : initial;
  });
  const [value] = state;
  useEffect(() => {
    draft.write(key, value);
  }, [draft, key, value]);
  return state;
}
