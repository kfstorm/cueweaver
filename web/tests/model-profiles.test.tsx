import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { ModelProfileEditor, ModelProfilesPage } from "../src/model-profiles-page";
import type { ModelProfile } from "../src/model-profiles";
import { ThemeProvider } from "../src/theme-provider";
import { I18nProvider } from "../src/i18n";

const reference = { pysubtrans_version: "synthetic", providers: { OpenAI: [] } };

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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderProfiles(initialEntry = "/settings/model-profiles") {
  render(
    <I18nProvider>
      <ThemeProvider>
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <MemoryRouter initialEntries={[initialEntry]}>
            <Routes>
              <Route path="/settings/model-profiles" element={<ModelProfilesPage />} />
              <Route
                path="/settings/model-profiles/:profileId"
                element={<ModelProfileEditor />}
              />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </ThemeProvider>
    </I18nProvider>,
  );
}

it("creates a derived Model Profile without copying inherited settings", async () => {
  const fetchMock = vi.fn(async (input: string, options?: RequestInit) => {
    if (input === "/api/model-profile-reference")
      return { ok: true, json: async () => reference };
    if (input === "/api/model-profiles" && options?.method === "POST") {
      return {
        ok: true,
        json: async () => ({ ...parent, id: "profile-child", name: "Derived" }),
      };
    }
    return { ok: true, json: async () => ({ model_profiles: [parent] }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  renderProfiles();

  fireEvent.click(await screen.findByRole("link", { name: "Create derived" }));
  expect(screen.getByRole("combobox", { name: "Parent Model Profile" })).toHaveValue(
    parent.id,
  );
  expect(screen.getByText(/Inherited from Shared/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "Derived" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /Base profile/ }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Provider" })).toBeEnabled(),
  );
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

const editable: ModelProfile = {
  ...parent,
  id: "profile-editable",
  settings: [
    { key: "alpha", kind: "literal", value: 1 },
    { key: "beta", kind: "literal", value: 2 },
  ],
  effective_settings: [],
};

async function openEditableProfile() {
  const fetchMock = vi.fn(async (input: string, options?: RequestInit) => ({
    ok: true,
    json: async () =>
      input === "/api/model-profile-reference"
        ? reference
        : input === `/api/model-profiles/${editable.id}` && options?.method === "PUT"
          ? editable
          : { model_profiles: [editable] },
  }));
  vi.stubGlobal("fetch", fetchMock);
  renderProfiles(`/settings/model-profiles/${editable.id}`);
  const values = await screen.findAllByRole("textbox", { name: "Value" });
  return {
    fetchMock,
    values,
    save: screen.getByRole("button", { name: "Save Model Profile" }),
  };
}

it("keeps Save disabled until every setting has a valid value", async () => {
  const { fetchMock, values, save } = await openEditableProfile();
  fireEvent.change(values[0], { target: { value: "invalid" } });
  fireEvent.change(values[1], { target: { value: "4" } });
  expect(save).toBeDisabled();

  fireEvent.change(values[0], { target: { value: "3" } });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/model-profiles/${editable.id}`,
      expect.objectContaining({ method: "PUT" }),
    ),
  );
  const request = fetchMock.mock.calls.find(([, options]) => options?.method === "PUT");
  const payload = JSON.parse(String(request?.[1]?.body));
  expect(payload).toMatchObject({
    name: editable.name,
    parent_id: null,
    selectable: false,
  });
  expect(payload.settings).toHaveLength(2);
  expect(payload.settings).toEqual(
    expect.arrayContaining([
      { key: "alpha", kind: "literal", value: 3 },
      { key: "beta", kind: "literal", value: 4 },
    ]),
  );
});

it.each(["Unset", "Remove local"])(
  "clears a setting's parse error when choosing %s",
  async (action) => {
    const { values, save } = await openEditableProfile();
    fireEvent.change(values[0], { target: { value: "invalid" } });
    expect(save).toBeDisabled();

    fireEvent.click(screen.getAllByRole("button", { name: action })[0]);
    expect(save).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  },
);
