import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { I18nProvider, setActiveLocale } from "../src/i18n";
import { ModelProfileEditor } from "../src/model-profiles-page";
import type { ModelProfile, ModelProfileReference } from "../src/model-profiles";
import { ThemeProvider } from "../src/theme-provider";

const REFERENCE: ModelProfileReference = {
  pysubtrans_version: "1.6.0",
  providers: {
    DeepSeek: [
      {
        key: "api_key",
        type: "string",
        description: "Synthetic DeepSeek key description",
        choices: null,
      },
      {
        key: "temperature",
        type: "number",
        description: "Synthetic temperature description",
        choices: null,
      },
      { key: "model", type: "string", description: "Model description", choices: null },
    ],
    OpenAI: [
      {
        key: "api_key",
        type: "string",
        description: "Synthetic OpenAI key description",
        choices: null,
      },
      {
        key: "model",
        type: "string",
        description: "Model description",
        choices: null,
      },
      {
        key: "temperature",
        type: "number",
        description: "Synthetic temperature description",
        choices: null,
      },
      {
        key: "reasoning_effort",
        type: "string",
        description: "Synthetic reasoning description",
        choices: ["none", "low", "high"],
      },
    ],
  },
};

function profile(
  id: string,
  parentId: string | null,
  settings: ModelProfile["settings"],
  effectiveSettings: ModelProfile["effective_settings"],
): ModelProfile {
  return {
    id,
    name: id,
    parent_id: parentId,
    selectable: false,
    deletable: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    settings,
    effective_settings: effectiveSettings,
  };
}

const inheritedOpenAiProvider = {
  key: "provider",
  value: "OpenAI",
  source: { id: "base", name: "base" },
};

const inheritedModel = {
  key: "model",
  value: "synthetic-model",
  source: { id: "base", name: "base" },
};

function renderEditor(path: string, profiles: ModelProfile[] = []) {
  const saves: Array<{ method: string; body: Record<string, unknown> }> = [];
  const fetchMock = vi
    .fn()
    .mockImplementation(async (input: string, init?: RequestInit) => {
      if (input === "/api/model-profiles" && !init?.method) {
        return jsonResponse({ model_profiles: profiles });
      }
      if (input === "/api/model-profile-reference") {
        return jsonResponse(REFERENCE);
      }
      if (input.startsWith("/api/model-profiles") && init?.method) {
        saves.push({
          method: init.method,
          body: JSON.parse(String(init.body)) as Record<string, unknown>,
        });
        return jsonResponse({ id: "saved-profile" });
      }
      return jsonResponse({});
    });
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route
                path="/model-profiles/:profileId"
                element={<ModelProfileEditor />}
              />
              <Route path="/model-profiles" element={<div />} />
            </Routes>
          </MemoryRouter>
        </I18nProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { fetchMock, saves };
}

function jsonResponse(body: unknown, ok = true) {
  return { ok, json: async () => body };
}

async function addSetting(key: string) {
  const keyInput = await screen.findByLabelText(/New setting key/);
  fireEvent.change(keyInput, { target: { value: key } });
  fireEvent.click(screen.getByRole("button", { name: "Add setting" }));
  return keyInput;
}

afterEach(() => {
  cleanup();
  setActiveLocale("en");
  vi.unstubAllGlobals();
});

describe("Model Profile setting references", () => {
  it("adds provider, reference and custom settings, then saves the existing schema", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    const keyInput = await screen.findByLabelText(/New setting key/);
    const suggestions = document.getElementById("model-profile-setting-reference");
    expect(suggestions?.querySelector('option[value="provider"]')).toBeInTheDocument();

    await addSetting("provider");
    const providerRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("provider"),
    )!;
    expect(within(providerRow).getByLabelText("Value type")).toHaveValue("string");
    expect(within(providerRow).getByLabelText("Value type")).toBeDisabled();
    fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
      target: { value: "OpenAI" },
    });
    expect(keyInput).toHaveValue("");

    await addSetting("api_key");
    expect(
      await screen.findByText("Synthetic OpenAI key description"),
    ).toBeInTheDocument();
    const settingRows = document.querySelectorAll(".profile-setting");
    const apiKeyRow = [...settingRows].find((row) =>
      row.textContent?.includes("api_key"),
    );
    expect(apiKeyRow).toBeTruthy();
    expect(within(apiKeyRow as HTMLElement).getByLabelText("Value type")).toHaveValue(
      "string",
    );
    fireEvent.change(within(apiKeyRow as HTMLElement).getByLabelText("Value"), {
      target: { value: "synthetic-key" },
    });

    await addSetting("custom_flag");
    const customRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("custom_flag"),
    );
    expect(customRow).toBeTruthy();
    fireEvent.change(within(customRow as HTMLElement).getByLabelText("Value type"), {
      target: { value: "boolean" },
    });

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].method).toBe("POST");
    expect(saves[0].body).toMatchObject({
      name: "Synthetic profile",
      settings: expect.arrayContaining([
        { key: "provider", kind: "literal", value: "OpenAI" },
        { key: "api_key", kind: "literal", value: "synthetic-key" },
        { key: "custom_flag", kind: "literal", value: false },
      ]),
    });
  });

  it("uses inherited provider suggestions and updates them after provider override", async () => {
    const base = profile("base", null, [], [inheritedOpenAiProvider, inheritedModel]);
    const child = profile(
      "child",
      "base",
      [],
      [inheritedOpenAiProvider, inheritedModel],
    );
    renderEditor("/model-profiles/child", [base, child]);

    const keyInput = await screen.findByLabelText(/New setting key/);
    const datalist = document.getElementById("model-profile-setting-reference")!;
    await waitFor(() => {
      expect(
        [...datalist.querySelectorAll("option")].map((option) => option.value),
      ).toContain("reasoning_effort");
    });
    expect(
      [...datalist.querySelectorAll("option")].map((option) => option.value),
    ).not.toContain("model");

    const providerRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("provider"),
    )!;
    fireEvent.click(within(providerRow).getByRole("button", { name: "Override" }));
    fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
      target: { value: "DeepSeek" },
    });
    await waitFor(() => {
      expect(
        [...datalist.querySelectorAll("option")].map((option) => option.value),
      ).toContain("temperature");
    });
    expect(
      [...datalist.querySelectorAll("option")].map((option) => option.value),
    ).not.toContain("reasoning_effort");
    expect(keyInput).toHaveValue("");
  });

  it("uses numeric types and static choices while leaving dynamic model values editable", async () => {
    renderEditor("/model-profiles/new");
    await addSetting("provider");
    fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
      target: { value: "OpenAI" },
    });

    await addSetting("temperature");
    let rows = [...document.querySelectorAll(".profile-setting")];
    const temperatureRow = rows.find((row) =>
      row.textContent?.includes("temperature"),
    )!;
    expect(within(temperatureRow).getByLabelText("Value type")).toHaveValue("number");
    expect(within(temperatureRow).getByLabelText("Value").tagName).toBe("INPUT");

    await addSetting("reasoning_effort");
    rows = [...document.querySelectorAll(".profile-setting")];
    const reasoningRow = rows.find((row) =>
      row.textContent?.includes("reasoning_effort"),
    )!;
    const choices = within(reasoningRow).getByLabelText("Value");
    expect(choices.tagName).toBe("SELECT");
    expect(
      within(choices)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["none", "low", "high"]);
    expect(
      within(reasoningRow).getByText("Synthetic reasoning description"),
    ).toBeInTheDocument();

    await addSetting("model");
    rows = [...document.querySelectorAll(".profile-setting")];
    const modelRow = rows.find((row) => row.textContent?.includes("model"))!;
    expect(within(modelRow).getByLabelText("Value").tagName).toBe("INPUT");
  });

  it("keeps inherited override, unset and remove-local controls available", async () => {
    const inheritedTemperature = {
      key: "temperature",
      value: 0.25,
      source: { id: "base", name: "base" },
    };
    const base = profile(
      "base",
      null,
      [],
      [inheritedOpenAiProvider, inheritedTemperature],
    );
    const child = profile(
      "child",
      "base",
      [],
      [inheritedOpenAiProvider, inheritedTemperature],
    );
    renderEditor("/model-profiles/child", [base, child]);

    await screen.findByLabelText(/New setting key/);
    let row = [...document.querySelectorAll(".profile-setting")].find((item) =>
      item.textContent?.includes("temperature"),
    )!;
    fireEvent.click(within(row).getByRole("button", { name: "Override" }));
    row = [...document.querySelectorAll(".profile-setting")].find((item) =>
      item.textContent?.includes("temperature"),
    )!;
    fireEvent.click(within(row).getByRole("button", { name: "Unset" }));
    expect(row).toHaveTextContent("Unset locally");
    fireEvent.click(within(row).getByRole("button", { name: "Remove local" }));
    expect(row).toHaveTextContent("Inherited from base");
    expect(within(row).getByText("0.25")).toBeInTheDocument();
  });
});
