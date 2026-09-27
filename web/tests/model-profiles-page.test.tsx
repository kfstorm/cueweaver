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

import { I18nProvider, setActiveLocale, translate } from "../src/i18n";
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
      {
        key: "deepseek_limit",
        type: "number",
        description: "Synthetic DeepSeek-only setting",
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
      {
        key: "free_plan",
        type: "boolean",
        description: null,
        choices: null,
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

function existingStringTemperatureProfile(): ModelProfile {
  const source = { id: "existing", name: "existing" };
  return profile(
    "existing",
    null,
    [
      { key: "provider", kind: "literal", value: "OpenAI" },
      { key: "temperature", kind: "literal", value: "0.5" },
    ],
    [
      { key: "provider", value: "OpenAI", source },
      { key: "temperature", value: "0.5", source },
    ],
  );
}

function existingIntegerProviderProfile(): ModelProfile {
  const source = { id: "base", name: "base" };
  return profile(
    "base",
    null,
    [{ key: "provider", kind: "literal", value: 123 }],
    [{ key: "provider", value: 123, source }],
  );
}

function existingCustomStringProviderProfile(): ModelProfile {
  const source = { id: "base", name: "base" };
  return profile(
    "base",
    null,
    [{ key: "provider", kind: "literal", value: "OldCustomProvider" }],
    [{ key: "provider", value: "OldCustomProvider", source }],
  );
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

function renderEditor(
  path: string,
  profiles: ModelProfile[] = [],
  referenceResponse: Promise<ModelProfileReference> = Promise.resolve(REFERENCE),
) {
  const saves: Array<{ method: string; body: Record<string, unknown> }> = [];
  const fetchMock = vi
    .fn()
    .mockImplementation(async (input: string, init?: RequestInit) => {
      if (input === "/api/model-profiles" && !init?.method) {
        return jsonResponse({ model_profiles: profiles });
      }
      if (input === "/api/model-profile-reference") {
        return jsonResponse(await referenceResponse);
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
              <Route path="/settings/model-profiles" element={<div />} />
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

async function addProvider(name: string) {
  await addSetting("provider");
  fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
    target: { value: name },
  });
}

async function settingRow(key: string): Promise<HTMLElement> {
  await screen.findByLabelText(/New setting key/);
  const row = [...document.querySelectorAll<HTMLElement>(".profile-setting")].find(
    (item) => item.textContent?.includes(key),
  );
  if (!row) throw new Error(`Setting ${key} was not rendered`);
  return row;
}

async function expectSavedLiteralSetting(
  saves: Array<{ method: string; body: Record<string, unknown> }>,
  key: string,
  value: unknown,
) {
  await waitFor(() => expect(saves).toHaveLength(1));
  expect(saves[0].body).toMatchObject({
    settings: expect.arrayContaining([{ key, kind: "literal", value }]),
  });
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

    await addProvider("OpenAI");
    const providerRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("provider"),
    )!;
    expect(within(providerRow).getByLabelText("Value type")).toHaveValue("string");
    expect(within(providerRow).getByLabelText("Value type")).toBeEnabled();
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

  it("preserves an existing value whose type differs from the provider reference", async () => {
    const current = existingStringTemperatureProfile();
    const { saves } = renderEditor("/model-profiles/existing", [current]);

    const row = await settingRow("temperature");
    const type = within(row).getByLabelText("Value type");
    expect(type).toHaveValue("string");
    expect(type).toBeEnabled();
    expect(row).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Existing profile" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "temperature", "0.5");
  });

  it("converts a mismatched existing setting only after the user changes its type", async () => {
    const current = existingStringTemperatureProfile();
    const { saves } = renderEditor("/model-profiles/existing", [current]);

    const row = await settingRow("temperature");
    fireEvent.change(within(row).getByLabelText("Value type"), {
      target: { value: "number" },
    });
    expect(within(row).getByLabelText("Value type")).toHaveValue("number");
    expect(row).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );
    fireEvent.change(within(row).getByLabelText("Value"), {
      target: { value: "0.5" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "temperature", 0.5);
  });

  it("allows custom values for settings with static choice suggestions", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    await addProvider("OpenAI");
    await addSetting("reasoning_effort");

    const row = await settingRow("reasoning_effort");
    expect(within(row).getByLabelText("Value type")).toBeEnabled();
    const value = within(row).getByRole("textbox");
    expect(value.tagName).toBe("INPUT");
    expect(row).toHaveTextContent(/none.*low.*high/);
    expect(row.querySelector(".profile-editor .field-help")).toBeInTheDocument();
    fireEvent.change(value, { target: { value: "custom-effort" } });
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "reasoning_effort", "custom-effort");
  });

  it("updates reference hints without changing an existing setting", async () => {
    renderEditor("/model-profiles/new");
    await addProvider("OpenAI");
    await addSetting("deepseek_limit");
    const row = await settingRow("deepseek_limit");
    fireEvent.change(within(row).getByLabelText("Value"), {
      target: { value: "manual value" },
    });

    fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
      target: { value: "DeepSeek" },
    });

    expect(within(row).getByLabelText("Value type")).toHaveValue("string");
    expect(within(row).getByLabelText("Value type")).toBeEnabled();
    expect(row).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
  });

  it("keeps custom editor input and parse state when the provider changes", async () => {
    renderEditor("/model-profiles/new");
    await addProvider("OpenAI");
    await addSetting("foo");
    const row = await settingRow("foo");
    fireEvent.change(within(row).getByLabelText("Value type"), {
      target: { value: "number" },
    });
    const value = within(row).getByLabelText("Value");
    fireEvent.change(value, { target: { value: "0" } });
    fireEvent.change(value, { target: { value: "abc" } });
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeDisabled();

    fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
      target: { value: "DeepSeek" },
    });

    expect(within(row).getByLabelText("Value")).toHaveValue("abc");
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeDisabled();
    fireEvent.change(within(row).getByLabelText("Value"), {
      target: { value: "0" },
    });
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
  });

  it("preserves a legacy non-string provider until the user changes its type", async () => {
    const current = existingIntegerProviderProfile();
    const { saves } = renderEditor("/model-profiles/base", [current]);
    const row = await settingRow("provider");
    expect(row).toHaveTextContent("· integer");
    expect(row).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "string" }),
    );
    expect(within(row).getByLabelText("Value type")).toHaveValue("integer");
    expect(within(row).getByLabelText("Value type")).toBeEnabled();
    expect(within(row).getByLabelText("Value")).toHaveValue("123");
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "provider", 123);
  });

  it("offers a provider dropdown when adding a provider setting", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    await addSetting("provider");

    const row = await settingRow("provider");
    const provider = within(row).getByRole("combobox", { name: "Provider" });
    const save = screen.getByRole("button", { name: "Save Model Profile" });
    expect(provider).toHaveValue("");
    expect(
      [...provider.querySelectorAll("option")].map((option) => option.value),
    ).toEqual(["", "DeepSeek", "OpenAI"]);
    expect(provider.querySelector('option[value=""]')).toBeDisabled();
    expect(save).toBeEnabled();

    fireEvent.change(provider, { target: { value: "OpenAI" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await expectSavedLiteralSetting(saves, "provider", "OpenAI");
  });

  it("preserves the inherited type and value when a setting is overridden", async () => {
    const inheritedTemperature = {
      key: "temperature",
      value: "0.5",
      source: { id: "base", name: "base" },
    };
    const base = profile(
      "base",
      null,
      [
        { key: "provider", kind: "literal", value: "OpenAI" },
        { key: "temperature", kind: "literal", value: "0.5" },
      ],
      [inheritedOpenAiProvider, inheritedTemperature],
    );
    const child = profile(
      "child",
      "base",
      [],
      [inheritedOpenAiProvider, inheritedTemperature],
    );
    const { saves } = renderEditor("/model-profiles/child", [base, child]);

    let row = await settingRow("temperature");
    expect(row).toHaveTextContent("· string");
    expect(row).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );

    fireEvent.click(within(row).getByRole("button", { name: "Override" }));
    row = await settingRow("temperature");
    expect(within(row).getByLabelText("Value type")).toHaveValue("string");
    expect(within(row).getByLabelText("Value type")).toBeEnabled();
    expect(within(row).getByLabelText("Value")).toHaveValue("0.5");

    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "temperature", "0.5");
  });

  it("keeps the generic type for a key added before reference metadata arrives", async () => {
    let resolveReference!: (value: ModelProfileReference) => void;
    const referenceResponse = new Promise<ModelProfileReference>((resolve) => {
      resolveReference = resolve;
    });
    renderEditor("/model-profiles/new", [], referenceResponse);

    await addSetting("provider");
    const providerRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("provider"),
    )!;
    fireEvent.change(within(providerRow).getByLabelText("Value"), {
      target: { value: "OpenAI" },
    });
    await addSetting("temperature");
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    resolveReference(REFERENCE);

    const temperatureRow = await screen.findByText("Synthetic temperature description");
    const row = temperatureRow.closest(".profile-setting")!;
    expect(within(row).getByLabelText("Value type")).toHaveValue("string");
    expect(within(row).getByLabelText("Value")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
    expect(row).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );
  });

  it("allows an untouched legacy provider string outside the current registry", async () => {
    const current = existingCustomStringProviderProfile();
    const { saves } = renderEditor("/model-profiles/base", [current]);
    const row = await settingRow("provider");

    expect(within(row).getByLabelText("Value type")).toHaveValue("string");
    expect(within(row).getByRole("combobox", { name: "Provider" })).toHaveValue(
      "OldCustomProvider",
    );
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "provider", "OldCustomProvider");
  });

  it("keeps a setting editable after switching away from its reference provider", async () => {
    renderEditor("/model-profiles/new");
    await addProvider("OpenAI");

    await addSetting("free_plan");
    const providerValue = screen.getByRole("combobox", { name: "Provider" });
    fireEvent.change(providerValue, { target: { value: "DeepSeek" } });

    const freePlanRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("free_plan"),
    )!;
    expect(within(freePlanRow).getByLabelText("Value type")).toBeEnabled();
    expect(within(freePlanRow).getByLabelText("Value type")).toHaveValue("boolean");
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
  });

  it("initializes known numeric settings and leaves model values editable", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    await addProvider("OpenAI");

    await addSetting("temperature");
    let rows = [...document.querySelectorAll(".profile-setting")];
    const temperatureRow = rows.find((row) =>
      row.textContent?.includes("temperature"),
    )!;
    expect(within(temperatureRow).getByLabelText("Value type")).toHaveValue("number");
    expect(within(temperatureRow).getByLabelText("Value type")).toBeEnabled();
    const temperatureValue = within(temperatureRow).getByLabelText("Value");
    expect(temperatureValue.tagName).toBe("INPUT");
    expect(temperatureValue).toHaveValue("0.5");
    expect(temperatureRow).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );

    await addSetting("model");
    rows = [...document.querySelectorAll(".profile-setting")];
    const modelRow = rows.find((row) => row.textContent?.includes("model"))!;
    expect(within(modelRow).getByLabelText("Value").tagName).toBe("INPUT");
    fireEvent.change(temperatureValue, { target: { value: "0.75" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "temperature", 0.75);
  });

  it("uses generic provider editing when provider reference data is unavailable", async () => {
    const { saves } = renderEditor(
      "/model-profiles/new",
      [],
      Promise.reject(new Error("unavailable")),
    );
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    await addSetting("provider");

    const row = await settingRow("provider");
    expect(within(row).getByLabelText("Value type")).toHaveValue("string");
    const provider = within(row).getByLabelText("Value");
    expect(provider.tagName).toBe("INPUT");
    fireEvent.change(provider, { target: { value: "UnlistedProvider" } });
    await addSetting("custom_option");
    const customRow = await settingRow("custom_option");
    fireEvent.change(within(customRow).getByLabelText("Value type"), {
      target: { value: "number" },
    });
    fireEvent.change(within(customRow).getByLabelText("Value"), {
      target: { value: "2.5" },
    });
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "provider", "UnlistedProvider");
    expect(saves[0].body.settings).toContainEqual({
      key: "custom_option",
      kind: "literal",
      value: 2.5,
    });
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

    let row = await settingRow("temperature");
    fireEvent.click(within(row).getByRole("button", { name: "Override" }));
    row = await settingRow("temperature");
    fireEvent.click(within(row).getByRole("button", { name: "Unset" }));
    expect(row.querySelector(".profile-value")).not.toHaveTextContent("0.25");
    expect(within(row).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(within(row).getByRole("button", { name: "Remove local" }));
    expect(row.querySelector(".profile-value")).toHaveTextContent("0.25");
    expect(within(row).getAllByRole("button")).toHaveLength(2);
  });
});
