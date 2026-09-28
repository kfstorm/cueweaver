import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { I18nProvider } from "../src/i18n";
import { ModelProfilesPage } from "../src/model-profiles-page";
import type { ModelProfile } from "../src/model-profiles";
import { ThemeProvider } from "../src/theme-provider";

const profile: ModelProfile = {
  id: "profile-1",
  name: "Synthetic profile",
  provider: "OpenAI",
  deletable: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  settings: [{ key: "model", value: "synthetic-model" }],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("lists standalone profiles without inheritance actions or status wording", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ model_profiles: [profile] }),
    })),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ThemeProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={["/settings/model-profiles"]}>
            <Routes>
              <Route path="/settings/model-profiles" element={<ModelProfilesPage />} />
            </Routes>
          </MemoryRouter>
        </I18nProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Synthetic profile")).toBeInTheDocument();
  expect(screen.getByText("Provider: OpenAI")).toBeInTheDocument();
  expect(
    screen.queryByText(/inherit|derived|selectable|base profile/i),
  ).not.toBeInTheDocument();
});
