import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type ProfileSetting = { key: string; kind: "literal" | "unset"; value: unknown };
export type EffectiveSetting = {
  key: string;
  value: unknown;
  source: { id: string; name: string };
};
export type ModelProfile = {
  id: string;
  name: string;
  parent_id: string | null;
  selectable: boolean;
  deletable: boolean;
  created_at: string;
  updated_at: string;
  settings: ProfileSetting[];
  effective_settings: EffectiveSetting[];
};
export type ProfileInput = Pick<
  ModelProfile,
  "name" | "parent_id" | "selectable" | "settings"
>;
export type SettingReference = {
  key: string;
  type: string | null;
  description: string | null;
  choices: string[] | null;
};
export type ModelProfileReference = {
  pysubtrans_version: string;
  providers: Record<string, SettingReference[]>;
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
