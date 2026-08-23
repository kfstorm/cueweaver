import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { validateTermMapContent } from "./term-map-validation";
import { localizedError } from "./i18n";

export {
  MAX_TERM_MAP_BYTES,
  MAX_TERM_MAP_UPLOAD_BYTES,
  validateTermMapContent,
} from "./term-map-validation";
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

export interface DirectoryTermMapState {
  directory: string;
  local: TermMapSummary | null;
  effective: TermMapSummary | null;
  source_directory: string | null;
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
  void queryClient.invalidateQueries({ queryKey: ["directory-term-map"] });
}

async function readResponse<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & ApiError;
  if (!response.ok) {
    throw localizedError("errors.termMapOperation", body.message);
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

export function useDirectoryTermMap(path: string) {
  return useQuery({
    queryKey: ["directory-term-map", path],
    queryFn: async () =>
      readResponse<DirectoryTermMapState>(
        await fetch(`/api/term-maps/directory?path=${encodeURIComponent(path)}`),
      ),
  });
}

export function useBindDirectoryTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ path, termMapId }: { path: string; termMapId: string }) =>
      readResponse<DirectoryTermMapState>(
        await fetch("/api/term-maps/directory", {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ path, term_map_id: termMapId }),
        }),
      ),
    onSuccess: (state, variables) => {
      queryClient.setQueryData(["directory-term-map", state.directory], state);
      queryClient.setQueryData(["directory-term-map", variables.path], state);
      void queryClient.invalidateQueries({ queryKey: ["directory-term-map"] });
    },
  });
}

export function useRemoveDirectoryTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (path: string) =>
      readResponse<DirectoryTermMapState>(
        await fetch("/api/term-maps/directory", {
          method: "DELETE",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ path }),
        }),
      ),
    onSuccess: (state, path) => {
      queryClient.setQueryData(["directory-term-map", state.directory], state);
      queryClient.setQueryData(["directory-term-map", path], state);
      void queryClient.invalidateQueries({ queryKey: ["directory-term-map"] });
    },
  });
}

export function useCreateTermMap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, content }: { name: string; content: string }) => {
      const validation = validateTermMapContent(content);
      if (validation.error || validation.content === null) {
        throw localizedError(validation.errorKey ?? "termMapValidation.invalid");
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
        throw localizedError(validation.errorKey ?? "termMapValidation.invalid");
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
      void queryClient.invalidateQueries({ queryKey: ["directory-term-map"] });
      queryClient.removeQueries({ queryKey: ["term-maps", variables.id] });
    },
  });
}
