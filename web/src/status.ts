import { useQuery } from "@tanstack/react-query";

export interface ProductStatus {
  api: { ready: boolean };
  roots: { ready: boolean };
  translation_provider: { ready: boolean; message?: string };
  worker: { ready: boolean; mode: "single" };
}

async function fetchStatus(): Promise<ProductStatus> {
  const response = await fetch("/api/status");
  if (!response.ok) {
    throw new Error("CueWeaver status is unavailable.");
  }
  return response.json() as Promise<ProductStatus>;
}

export function useProductStatus() {
  return useQuery({ queryKey: ["product-status"], queryFn: fetchStatus });
}
