import { useQuery } from "@tanstack/react-query";

import { localizedError } from "./i18n";

export interface ProductStatus {
  api: { ready: boolean };
  roots: { ready: boolean };
  translation_provider: { ready: boolean; message?: string };
  worker: { ready: boolean; mode: "single" };
  job_records?: {
    corrupt: { count: number; location: string };
    unsupported: { count: number; location: string };
  };
}

export function jobRecordAttention(status: ProductStatus | undefined): boolean {
  const records = status?.job_records;
  return (records?.corrupt.count ?? 0) + (records?.unsupported.count ?? 0) > 0;
}

async function fetchStatus(): Promise<ProductStatus> {
  const response = await fetch("/api/status");
  if (!response.ok) {
    throw localizedError("errors.statusUnavailable");
  }
  return response.json() as Promise<ProductStatus>;
}

export function useProductStatus() {
  return useQuery({
    queryKey: ["product-status"],
    queryFn: fetchStatus,
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
  });
}
