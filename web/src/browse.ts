import {
  type QueryClient,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { TranslationKey } from "./i18n";
import { localizedError } from "./i18n/errors";

export interface MediaDirectoryEntry {
  name: string;
  path: string;
  kind: "directory" | "media";
  title?: string;
  year?: number;
  season?: number;
  episode?: number;
}

export interface MediaDirectory {
  path: string;
  entries: MediaDirectoryEntry[];
}

async function fetchDirectory(path: string): Promise<MediaDirectory> {
  const response = await fetch("/api/media/browse", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    await throwResponseError(response, "errors.mediaDirectory");
  }
  return response.json() as Promise<MediaDirectory>;
}

export function useMediaDirectory(path: string) {
  return useQuery({
    queryKey: ["media-directory", path],
    queryFn: () => fetchDirectory(path),
  });
}

export interface SubtitleCandidate {
  kind: "external" | "embedded";
  path?: string;
  stream_index?: number;
  format?: string;
  tags?: { language?: string; title?: string };
  dispositions?: string[];
}

export interface UnsupportedSubtitleCandidate {
  kind: "external" | "embedded";
  path?: string;
  stream_index?: number;
  reason: string;
}

export interface MediaDiscovery {
  path: string;
  directory?: string;
  candidates: SubtitleCandidate[];
  unsupported_candidates: UnsupportedSubtitleCandidate[];
}

async function fetchDiscovery(
  path: string,
  signal: AbortSignal,
): Promise<MediaDiscovery> {
  const response = await fetch("/api/media/discover", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ path }),
    signal,
  });
  if (!response.ok) {
    await throwResponseError(response, "errors.subtitleDiscovery");
  }
  return response.json() as Promise<MediaDiscovery>;
}

async function throwResponseError(
  response: Response,
  fallback: TranslationKey,
): Promise<never> {
  const body = (await response.json().catch(() => null)) as {
    message?: unknown;
  } | null;
  throw localizedError(
    fallback,
    typeof body?.message === "string" ? body.message : undefined,
  );
}

export function useMediaDiscovery(path: string | null) {
  return useQuery({
    queryKey: ["media-discovery", path],
    queryFn: ({ signal }) => fetchDiscovery(path!, signal),
    enabled: path !== null,
    retry: false,
    gcTime: 0,
  });
}

function createBatchDiscoveryQueue() {
  let active = 0;
  const waiting = new Set<() => void>();
  const drain = () => {
    while (active < 4 && waiting.size > 0) {
      const start = waiting.values().next().value!;
      waiting.delete(start);
      start();
    }
  };
  return (path: string, signal: AbortSignal): Promise<MediaDiscovery> =>
    new Promise((resolve, reject) => {
      if (signal.aborted) {
        reject(signal.reason);
        return;
      }
      const cancel = () => {
        waiting.delete(start);
        reject(signal.reason);
      };
      const start = () => {
        signal.removeEventListener("abort", cancel);
        active += 1;
        void fetchDiscovery(path, signal)
          .then(resolve, reject)
          .finally(() => {
            active -= 1;
            drain();
          });
      };
      signal.addEventListener("abort", cancel, { once: true });
      waiting.add(start);
      drain();
    });
}

// Share the limit across observers without sharing queues between QueryClients.
const batchDiscoveryQueues = new WeakMap<
  QueryClient,
  ReturnType<typeof createBatchDiscoveryQueue>
>();

export function useMediaDiscoveries(paths: string[]) {
  const client = useQueryClient();
  let discover = batchDiscoveryQueues.get(client);
  if (!discover) {
    discover = createBatchDiscoveryQueue();
    batchDiscoveryQueues.set(client, discover);
  }
  return useQueries({
    queries: paths.map((path) => ({
      queryKey: ["media-discovery", path],
      queryFn: ({ signal }: { signal: AbortSignal }) => discover(path, signal),
      retry: false,
      staleTime: 30_000,
      gcTime: 5 * 60_000,
    })),
  });
}
