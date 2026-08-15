import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { validateTermMapContent } from "./term-map-validation";

export { MAX_TERM_MAP_BYTES, validateTermMapContent } from "./term-map-validation";
export type { TermMapContentValidation } from "./term-map-validation";

export interface TermMapSummary {
  id: string;
  name: string;
  entry_count: number;
  updated_at: string;
}

export interface TermMapDetail extends TermMapSummary {
  content: Record<string, string>;
}

interface TermMapListResponse {
  term_maps: TermMapSummary[];
}

interface ApiError {
  message?: string;
}

async function requestTermMap(
  id: string,
  method: "PATCH" | "PUT" | "DELETE",
  body: string,
): Promise<TermMapSummary> {
  return readResponse<TermMapSummary>(
    await fetch(`/api/term-maps/${id}`, {
      method,
      headers: { "content-type": "application/json" },
      body,
    }),
  );
}

function refreshTermMapQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  id: string,
) {
  void queryClient.invalidateQueries({ queryKey: ["term-maps"] });
  void queryClient.invalidateQueries({ queryKey: ["term-maps", id] });
}

async function readResponse<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & ApiError;
  if (!response.ok) {
    throw new Error(body.message ?? "Term map operation failed");
  }
  return body;
}

export function useTermMaps() {
  return useQuery({
    queryKey: ["term-maps"],
    queryFn: async () =>
      readResponse<TermMapListResponse>(await fetch("/api/term-maps")),
  });
}

export function useTermMap(id: string | null) {
  return useQuery({
    queryKey: ["term-maps", id],
    enabled: id !== null,
    queryFn: async () =>
      readResponse<TermMapDetail>(await fetch(`/api/term-maps/${id}`)),
  });
}

export function useCreateTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) => {
      const validation = validateTermMapContent(content);
      if (validation.error || validation.content === null) {
        throw new Error(validation.error ?? "Term map content is invalid");
      }
      return readResponse<TermMapSummary>(
        await fetch("/api/term-maps", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name, content: validation.content }),
        }),
      );
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["term-maps"] }),
  });
}

export function useRenameTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) =>
      requestTermMap(id, "PATCH", JSON.stringify({ name })),
    onSuccess: (_, variables) => {
      refreshTermMapQueries(queryClient, variables.id);
    },
  });
}

export function useReplaceTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, content }: { id: string; content: string }) => {
      const validation = validateTermMapContent(content);
      if (validation.error || validation.content === null) {
        throw new Error(validation.error ?? "Term map content is invalid");
      }
      return requestTermMap(id, "PUT", JSON.stringify({ content: validation.content }));
    },
    onSuccess: (_, variables) => {
      refreshTermMapQueries(queryClient, variables.id);
    },
  });
}

export function useDeleteTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) =>
      requestTermMap(id, "DELETE", JSON.stringify({ name })),
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["term-maps"] });
      queryClient.removeQueries({ queryKey: ["term-maps", variables.id] });
    },
  });
}
