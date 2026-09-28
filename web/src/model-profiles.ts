import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type ProfileSetting = { key: string; value: unknown };
export type ModelProfile = {
  id: string;
  name: string;
  provider: string;
  deletable: boolean;
  created_at: string;
  updated_at: string;
  settings: ProfileSetting[];
};
export type ProfileInput = Pick<ModelProfile, "name" | "provider" | "settings">;
export type ModelProfileOption = {
  key: string;
  type: "string" | "multiline" | "integer" | "number" | "boolean" | "choice";
  description: string | null;
  choices: string[] | null;
  value: unknown;
};
export type ModelProfileReference = {
  pysubtrans_version: string;
  providers: string[];
};
export type ModelProfileOptions = {
  provider: string;
  options: ModelProfileOption[];
  refresh_when_changed: string[];
  setting_updates: Record<string, string>;
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = (await response.json()) as T & { message?: string };
  if (!response.ok) throw new Error(body.message ?? "Model Profile request failed");
  return body;
}

export function useModelProfiles() {
  return useQuery({
    queryKey: ["model-profiles"],
    staleTime: 60_000,
    queryFn: async () =>
      (await request<{ model_profiles: ModelProfile[] }>("/api/model-profiles"))
        .model_profiles,
  });
}

export function useModelProfileReference() {
  return useQuery({
    queryKey: ["model-profile-reference"],
    staleTime: 60_000,
    queryFn: () => request<ModelProfileReference>("/api/model-profile-reference"),
  });
}

export function requestModelProfileOptions(
  provider: string,
  settings: Record<string, unknown>,
  signal: AbortSignal,
) {
  return request<ModelProfileOptions>("/api/model-profile-options", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ provider, settings }),
    signal,
  });
}

export function useSaveModelProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: ProfileInput }) =>
      request<ModelProfile>(
        id ? `/api/model-profiles/${encodeURIComponent(id)}` : "/api/model-profiles",
        {
          method: id ? "PUT" : "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(input),
        },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["model-profiles"] });
    },
  });
}

export function useDeleteModelProfile() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      request(`/api/model-profiles/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: { "content-type": "application/json" },
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["model-profiles"] });
    },
  });
}
