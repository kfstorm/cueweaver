import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { ModelProfileEditor, ModelProfilesPage } from "../src/model-profiles-page";
import type { ModelProfile } from "../src/model-profiles";
import { ThemeProvider } from "../src/theme-provider";
import { I18nProvider } from "../src/i18n";

const parent: ModelProfile = {
  id: "profile-parent",
  name: "Shared",
  parent_id: null,
  selectable: false,
  deletable: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  settings: [{ key: "provider", kind: "literal", value: "OpenAI" }],
  effective_settings: [
    {
      key: "provider",
      value: "OpenAI",
      source: { id: "profile-parent", name: "Shared" },
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

it("creates a derived Model Profile without copying inherited settings", async () => {
  const fetchMock = vi.fn(async (input: string, options?: RequestInit) => {
    if (input === "/api/model-profiles" && options?.method === "POST") {
      return {
        ok: true,
        json: async () => ({ ...parent, id: "profile-child", name: "Derived" }),
      };
    }
    return { ok: true, json: async () => ({ model_profiles: [parent] }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <I18nProvider>
      <ThemeProvider>
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <MemoryRouter initialEntries={["/model-profiles"]}>
            <Routes>
              <Route path="/model-profiles" element={<ModelProfilesPage />} />
              <Route
                path="/model-profiles/:profileId"
                element={<ModelProfileEditor />}
              />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </ThemeProvider>
    </I18nProvider>,
  );

  fireEvent.click(await screen.findByRole("link", { name: "Create derived" }));
  expect(screen.getByRole("combobox", { name: "Parent Model Profile" })).toHaveValue(
    parent.id,
  );
  expect(screen.getByText(/Inherited from Shared/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Derived" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /Base profile/ }));
  fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/model-profiles",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Derived",
          parent_id: parent.id,
          selectable: true,
          settings: [],
        }),
      }),
    ),
  );
});
