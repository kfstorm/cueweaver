import { useQuery } from "@tanstack/react-query";

export interface MediaDirectoryEntry {
  name: string;
  path: string;
  kind: "directory" | "media";
  title?: string;
  year?: number;
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
    const body = (await response.json().catch(() => null)) as {
      message?: unknown;
    } | null;
    throw new Error(
      typeof body?.message === "string"
        ? body.message
        : "This Media directory could not be loaded.",
    );
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
}

export interface UnsupportedSubtitleCandidate {
  kind: "external" | "embedded";
  path?: string;
  stream_index?: number;
  reason: string;
}

export interface MediaDiscovery {
  path: string;
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
    const body = (await response.json().catch(() => null)) as {
      message?: unknown;
    } | null;
    throw new Error(
      typeof body?.message === "string"
        ? body.message
        : "Subtitles could not be discovered.",
    );
  }
  return response.json() as Promise<MediaDiscovery>;
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
