import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { useMediaDiscoveries, useMediaDiscovery } from "../src/browse";

const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function discoveryRequests() {
  const active = new Map<
    string,
    { signal: AbortSignal; finish: (ok?: boolean) => void }
  >();
  const requested: string[] = [];
  let peak = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: string, init: RequestInit) => {
      const { path } = JSON.parse(String(init.body)) as { path: string };
      const signal = init.signal!;
      requested.push(path);
      return new Promise<Response>((resolve, reject) => {
        const abort = () => {
          active.delete(path);
          reject(signal.reason);
        };
        signal.addEventListener("abort", abort, { once: true });
        active.set(path, {
          signal,
          finish: (ok = true) => {
            signal.removeEventListener("abort", abort);
            active.delete(path);
            resolve(
              new Response(
                JSON.stringify({
                  path,
                  candidates: [
                    { kind: "external", path: `${path}.srt`, format: "srt" },
                  ],
                  unsupported_candidates: [],
                }),
                { status: ok ? 200 : 500 },
              ),
            );
          },
        });
        peak = Math.max(peak, active.size);
      });
    }),
  );
  return { active, requested, peak: () => peak };
}

function queryWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

it("limits a large batch to four requests and associates out-of-order results with paths", async () => {
  const requests = discoveryRequests();
  const paths = Array.from({ length: 100 }, (_, index) => `Episode-${index}.mkv`);
  const { result } = renderHook(({ paths }) => useMediaDiscoveries(paths), {
    ...queryWrapper(),
    initialProps: { paths },
  });

  await waitFor(() => expect(requests.active.size).toBe(4));
  expect(requests.requested).toHaveLength(4);
  while (requests.requested.length < paths.length || requests.active.size > 0) {
    await act(async () => {
      [...requests.active.values()].reverse().forEach((request) => request.finish());
    });
    await waitFor(() => {
      expect(result.current.filter((query) => query.isSuccess).length).toBe(
        requests.requested.length - requests.active.size,
      );
    });
  }
  await waitFor(() =>
    expect(result.current.every((query) => query.isSuccess)).toBe(true),
  );
  expect(requests.peak()).toBe(4);
  expect(result.current.map((query) => query.data?.path)).toEqual(paths);
  expect(result.current.map((query) => query.data?.candidates[0].path)).toEqual(
    paths.map((path) => `${path}.srt`),
  );
});

it("drops deselected queued Media, aborts active requests, and preserves reordered associations", async () => {
  const requests = discoveryRequests();
  const paths = Array.from({ length: 100 }, (_, index) => `Episode-${index}.mkv`);
  const { result, rerender, unmount } = renderHook(
    ({ paths }) => useMediaDiscoveries(paths),
    {
      ...queryWrapper(),
      initialProps: { paths },
    },
  );
  await waitFor(() => expect(requests.active.size).toBe(4));
  const initialRequests = [...requests.active.values()];
  rerender({ paths: [paths[8], paths[3]] });
  await waitFor(() =>
    expect([...requests.active.keys()]).toEqual([paths[3], paths[8]]),
  );
  expect(initialRequests.slice(0, 3).every((request) => request.signal.aborted)).toBe(
    true,
  );
  expect(requests.requested).toEqual([...paths.slice(0, 4), paths[8]]);
  await act(async () => requests.active.get(paths[8])!.finish());
  await waitFor(() => expect(result.current[0].data?.path).toBe(paths[8]));
  expect(result.current[1].isPending).toBe(true);
  const lastActive = requests.active.get(paths[3])!;
  unmount();
  expect(lastActive.signal.aborted).toBe(true);
  expect(requests.active.size).toBe(0);
  expect(requests.requested).toHaveLength(5);
});

it("reuses fresh batch results after deselection and refetches stale results", async () => {
  const requests = discoveryRequests();
  const { client, wrapper } = queryWrapper();
  const path = "Episode.mkv";
  const { result, rerender } = renderHook(({ paths }) => useMediaDiscoveries(paths), {
    wrapper,
    initialProps: { paths: [path] },
  });
  await waitFor(() => expect(requests.active.size).toBe(1));
  await act(async () => requests.active.get(path)!.finish());
  await waitFor(() => expect(result.current[0].isSuccess).toBe(true));
  rerender({ paths: [] });
  rerender({ paths: [path] });
  expect(result.current[0].data?.path).toBe(path);
  expect(result.current[0].isFetching).toBe(false);
  expect(requests.requested).toEqual([path]);

  const cached = result.current[0].data;
  rerender({ paths: [] });
  act(() => {
    client.setQueryData(["media-discovery", path], cached, {
      updatedAt: Date.now() - 31_000,
    });
  });
  rerender({ paths: [path] });
  await waitFor(() => expect(requests.requested).toEqual([path, path]));
  await act(async () => requests.active.get(path)!.finish());
  await waitFor(() => expect(result.current[0].isFetching).toBe(false));
});

it("shares batch slots and in-flight queries across observers and releases slots on errors", async () => {
  const requests = discoveryRequests();
  const { wrapper } = queryWrapper();
  const first = renderHook(
    () => useMediaDiscoveries(["A.mkv", "B.mkv", "C.mkv", "D.mkv"]),
    { wrapper },
  );
  const second = renderHook(() => useMediaDiscoveries(["A.mkv", "E.mkv"]), { wrapper });
  await waitFor(() => expect(requests.active.size).toBe(4));
  expect(requests.requested).toHaveLength(4);
  await act(async () => requests.active.get("C.mkv")!.finish(false));
  await waitFor(() => expect(first.result.current[2].isError).toBe(true));
  await waitFor(() => expect(requests.active.has("E.mkv")).toBe(true));
  const shared = requests.active.get("A.mkv")!;
  first.unmount();
  expect(shared.signal.aborted).toBe(false);
  await act(async () => shared.finish());
  await waitFor(() => expect(second.result.current[0].data?.path).toBe("A.mkv"));
  expect(requests.peak()).toBe(4);
  expect(requests.requested.filter((path) => path === "A.mkv")).toHaveLength(1);
});

it("keeps single-Media discovery independent of the batch queue and aborts it on removal", async () => {
  const requests = discoveryRequests();
  const { wrapper } = queryWrapper();
  renderHook(() => useMediaDiscoveries(["A.mkv", "B.mkv", "C.mkv", "D.mkv"]), {
    wrapper,
  });
  const single = renderHook(() => useMediaDiscovery("Single.mkv"), { wrapper });
  await waitFor(() => expect(requests.active.size).toBe(5));
  const request = requests.active.get("Single.mkv")!;
  single.unmount();
  expect(request.signal.aborted).toBe(true);
});
