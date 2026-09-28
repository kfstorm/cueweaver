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

function baseWithSelectableChild(): ModelProfile[] {
  const base = profile(
    "base",
    null,
    [{ key: "provider", kind: "literal", value: "OpenAI" }],
    [inheritedOpenAiProvider],
  );
  const child = {
    ...profile("child", "base", [], [inheritedOpenAiProvider]),
    selectable: true,
  };
  return [base, child];
}

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
  const keyInput = await screen.findByRole("combobox", { name: "Search settings" });
  fireEvent.change(keyInput, { target: { value: key } });
  fireEvent.click(await screen.findByRole("option", { name: new RegExp(key) }));
  fireEvent.click(screen.getByRole("button", { name: "Add setting" }));
  return keyInput;
}

async function addProvider(name: string) {
  fireEvent.change(await screen.findByRole("combobox", { name: "Provider" }), {
    target: { value: name },
  });
}

async function renderNamedOpenAiProfile() {
  const { saves } = renderEditor("/model-profiles/new");
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Synthetic profile" },
  });
  await addProvider("OpenAI");
  return saves;
}

async function settingRow(key: string): Promise<HTMLElement> {
  await screen.findByRole("combobox", { name: "Search settings" });
  const row = [...document.querySelectorAll<HTMLElement>(".profile-setting")].find(
    (item) => item.textContent?.includes(key),
  );
  if (!row) throw new Error(`Setting ${key} was not rendered`);
  return row;
}

function expectNoPendingReasoningEffort() {
  expect(document.querySelectorAll(".profile-setting")).toHaveLength(0);
  expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
  const search = screen.getByRole("combobox", { name: "Search settings" });
  fireEvent.change(search, { target: { value: "reasoning_effort" } });
  expect(screen.queryByRole("option", { name: /reasoning_effort/ })).toBeNull();
  expect(screen.getByRole("button", { name: "Add setting" })).toBeDisabled();
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
  it("selects a provider directly and adds a known setting with the existing schema", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    const keyInput = await screen.findByRole("combobox", { name: "Search settings" });
    expect(keyInput).toBeDisabled();

    await addProvider("OpenAI");
    expect(screen.getByRole("combobox", { name: "Provider" })).toHaveValue("OpenAI");
    expect(document.querySelectorAll(".profile-setting")).toHaveLength(0);
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

    const keyInput = await screen.findByRole("combobox", { name: "Search settings" });
    expect(
      within(document.querySelector(".profile-provider") as HTMLElement).getByText(
        "Inherited from base",
      ),
    ).toBeInTheDocument();
    fireEvent.focus(keyInput);
    expect(
      await screen.findByRole("option", { name: /reasoning_effort/ }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /model ·/ })).not.toBeInTheDocument();

    fireEvent.click(
      within(document.querySelector(".profile-provider") as HTMLElement).getByRole(
        "button",
        { name: "Override" },
      ),
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Provider" }), {
      target: { value: "DeepSeek" },
    });
    fireEvent.focus(keyInput);
    expect(screen.getByRole("option", { name: /deepseek_limit/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /reasoning_effort/ }),
    ).not.toBeInTheDocument();
    expect(keyInput).toHaveValue("");
  });

  it("discards unfinished additions and their parse errors when the provider changes", async () => {
    renderEditor("/model-profiles/new");
    await addProvider("OpenAI");
    await addSetting("reasoning_effort");
    expect(await settingRow("reasoning_effort")).toBeInTheDocument();

    await addSetting("temperature");
    const pendingTemperature = await settingRow("temperature");
    fireEvent.change(within(pendingTemperature).getByLabelText("Value"), {
      target: { value: "invalid" },
    });
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeDisabled();

    await addProvider("DeepSeek");
    expectNoPendingReasoningEffort();
  });

  it("removes a completed session addition unsupported by the new provider", async () => {
    const saves = await renderNamedOpenAiProfile();
    await addSetting("reasoning_effort");
    fireEvent.change(
      within(await settingRow("reasoning_effort")).getByRole("textbox"),
      {
        target: { value: "high" },
      },
    );
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    await addProvider("DeepSeek");
    expect(document.querySelectorAll(".profile-setting")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].body.settings).toEqual([
      { key: "provider", kind: "literal", value: "DeepSeek" },
    ]);
  });

  it("retains a completed session addition supported by the new provider", async () => {
    const saves = await renderNamedOpenAiProfile();
    await addSetting("temperature");
    fireEvent.change(within(await settingRow("temperature")).getByLabelText("Value"), {
      target: { value: "0.5" },
    });

    await addProvider("DeepSeek");
    expect(await settingRow("temperature")).toHaveTextContent("0.5");
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "temperature", 0.5);
  });

  it("discards unfinished additions when a new parent changes the effective provider", async () => {
    const openAiBase = profile(
      "openai-base",
      null,
      [{ key: "provider", kind: "literal", value: "OpenAI" }],
      [
        {
          key: "provider",
          value: "OpenAI",
          source: { id: "openai-base", name: "openai-base" },
        },
      ],
    );
    const deepSeekBase = profile(
      "deepseek-base",
      null,
      [{ key: "provider", kind: "literal", value: "DeepSeek" }],
      [
        {
          key: "provider",
          value: "DeepSeek",
          source: { id: "deepseek-base", name: "deepseek-base" },
        },
      ],
    );
    renderEditor("/model-profiles/new?parent=openai-base", [openAiBase, deepSeekBase]);
    await addSetting("reasoning_effort");
    expect(await settingRow("reasoning_effort")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Parent Model Profile" }), {
      target: { value: "deepseek-base" },
    });
    expectNoPendingReasoningEffort();

    await addSetting("deepseek_limit");
    fireEvent.change(
      within(await settingRow("deepseek_limit")).getByLabelText("Value"),
      { target: { value: "12" } },
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Parent Model Profile" }), {
      target: { value: "openai-base" },
    });
    expect(document.querySelectorAll(".profile-setting")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
  });

  it.each(["Unset", "Remove local"])(
    "discards unfinished additions when %s changes the effective provider",
    async (action) => {
      const parent = profile(
        "parent",
        null,
        [{ key: "provider", kind: "literal", value: "DeepSeek" }],
        [
          {
            key: "provider",
            value: "DeepSeek",
            source: { id: "parent", name: "parent" },
          },
        ],
      );
      const child = profile(
        "child",
        "parent",
        [{ key: "provider", kind: "literal", value: "OpenAI" }],
        [
          {
            key: "provider",
            value: "OpenAI",
            source: { id: "child", name: "child" },
          },
        ],
      );
      renderEditor("/model-profiles/child", [parent, child]);
      await addSetting("reasoning_effort");
      const providerArea = document.querySelector(".profile-provider") as HTMLElement;
      fireEvent.click(within(providerArea).getByRole("button", { name: action }));
      expect(document.querySelectorAll(".profile-setting")).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
    },
  );

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
    const saves = await renderNamedOpenAiProfile();
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
    const source = { id: "existing", name: "existing" };
    renderEditor("/model-profiles/existing", [
      profile(
        "existing",
        null,
        [
          { key: "provider", kind: "literal", value: "OpenAI" },
          { key: "deepseek_limit", kind: "literal", value: "manual value" },
        ],
        [
          { key: "provider", value: "OpenAI", source },
          { key: "deepseek_limit", value: "manual value", source },
        ],
      ),
    ]);
    const row = await settingRow("deepseek_limit");

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
    const source = { id: "existing", name: "existing" };
    renderEditor("/model-profiles/existing", [
      profile(
        "existing",
        null,
        [
          { key: "provider", kind: "literal", value: "OpenAI" },
          { key: "foo", kind: "literal", value: "seed" },
        ],
        [
          { key: "provider", value: "OpenAI", source },
          { key: "foo", value: "seed", source },
        ],
      ),
    ]);
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

  it("preserves a legacy non-string provider without exposing type editing", async () => {
    const current = existingIntegerProviderProfile();
    const { saves } = renderEditor("/model-profiles/base", [current]);
    const provider = await screen.findByRole("combobox", { name: "Provider" });
    expect(await screen.findByText(/Existing provider: 123/)).toBeInTheDocument();
    expect(provider).toHaveValue("");
    expect(screen.queryByLabelText("Value type")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "provider", 123);
  });

  it("offers a registry-only provider dropdown directly in the form", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    const provider = await screen.findByRole("combobox", { name: "Provider" });
    const save = screen.getByRole("button", { name: "Save Model Profile" });
    expect(provider).toHaveValue("");
    expect(
      [...provider.querySelectorAll("option")].map((option) => option.value),
    ).toEqual(["", "DeepSeek", "OpenAI"]);
    expect(provider.querySelector('option[value=""]')).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Base profile/));
    expect(save).toBeDisabled();

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
    expect(row).toHaveTextContent("Value type: string");
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

  it("disables provider and setting selection while the reference loads", async () => {
    let resolveReference!: (value: ModelProfileReference) => void;
    const referenceResponse = new Promise<ModelProfileReference>((resolve) => {
      resolveReference = resolve;
    });
    renderEditor("/model-profiles/new", [], referenceResponse);

    const provider = await screen.findByRole("combobox", { name: "Provider" });
    expect(provider).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Search settings" })).toBeDisabled();
    expect(screen.getByRole("status", { name: "" })).toBeInTheDocument();

    resolveReference(REFERENCE);
    await waitFor(() => expect(provider).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "Search settings" })).toBeDisabled();
  });

  it("allows an untouched legacy provider string outside the current registry", async () => {
    const current = existingCustomStringProviderProfile();
    const { saves } = renderEditor("/model-profiles/base", [current]);
    const provider = await screen.findByRole("combobox", { name: "Provider" });
    expect(
      await screen.findByText(/Existing provider: OldCustomProvider/),
    ).toBeInTheDocument();
    expect(provider).toHaveValue("");
    expect(
      [...provider.querySelectorAll("option")].map((item) => item.value),
    ).not.toContain("OldCustomProvider");
    expect(screen.getByRole("combobox", { name: "Search settings" })).toBeDisabled();
    expect(
      screen.getByText("Choose or override a provider to add settings."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "provider", "OldCustomProvider");
  });

  it("requires an explicit boolean value for a referenced setting", async () => {
    const { saves } = renderEditor("/model-profiles/new");
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Synthetic profile" },
    });
    await addProvider("OpenAI");

    await addSetting("free_plan");
    const freePlanRow = [...document.querySelectorAll(".profile-setting")].find((row) =>
      row.textContent?.includes("free_plan"),
    )!;
    expect(within(freePlanRow).getByLabelText("Value type")).toBeEnabled();
    expect(within(freePlanRow).getByLabelText("Value type")).toHaveValue("boolean");
    const save = screen.getByRole("button", { name: "Save Model Profile" });
    const value = within(freePlanRow).getByRole("combobox", { name: "Value" });
    expect(value).toHaveValue("");
    expect(save).toBeDisabled();

    fireEvent.change(value, { target: { value: "false" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await expectSavedLiteralSetting(saves, "free_plan", false);
  });

  it("keeps referenced numeric settings incomplete until a value is entered", async () => {
    const saves = await renderNamedOpenAiProfile();

    await addSetting("temperature");
    let rows = [...document.querySelectorAll(".profile-setting")];
    const temperatureRow = rows.find((row) =>
      row.textContent?.includes("temperature"),
    )!;
    expect(within(temperatureRow).getByLabelText("Value type")).toHaveValue("number");
    expect(within(temperatureRow).getByLabelText("Value type")).toBeEnabled();
    const temperatureValue = within(temperatureRow).getByLabelText("Value");
    expect(temperatureValue.tagName).toBe("INPUT");
    expect(temperatureValue).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeDisabled();
    expect(saves).toHaveLength(0);
    expect(temperatureRow).toHaveTextContent(
      translate("modelProfiles.pysubtransType", { type: "number" }),
    );

    await addSetting("model");
    rows = [...document.querySelectorAll(".profile-setting")];
    const modelRow = rows.find((row) => row.textContent?.includes("model"))!;
    expect(within(modelRow).getByLabelText("Value").tagName).toBe("INPUT");
    fireEvent.change(temperatureValue, { target: { value: "0.75" } });
    fireEvent.change(within(modelRow).getByLabelText("Value"), {
      target: { value: "synthetic-model" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "temperature", 0.75);
    expect(saves[0].body.settings).toContainEqual({
      key: "model",
      kind: "literal",
      value: "synthetic-model",
    });
  });

  it("reports reference failures and retries without offering free-text input", async () => {
    const { fetchMock } = renderEditor(
      "/model-profiles/new",
      [],
      Promise.reject(new Error("unavailable")),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
    expect(screen.getByRole("combobox", { name: "Provider" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Search settings" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => url === "/api/model-profile-reference"),
      ).toHaveLength(2),
    );
  });

  it("reports an empty provider registry instead of allowing provider entry", async () => {
    renderEditor(
      "/model-profiles/new",
      [],
      Promise.resolve({ ...REFERENCE, providers: {} }),
    );
    expect(
      await screen.findByText(/No registered providers were found/),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Provider" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("requires a selected known candidate and searches descriptions", async () => {
    renderEditor("/model-profiles/new");
    const search = await screen.findByRole("combobox", { name: "Search settings" });
    expect(search).toBeDisabled();
    expect(
      screen.getByText("Choose or override a provider to add settings."),
    ).toBeInTheDocument();
    await addProvider("OpenAI");
    fireEvent.change(search, { target: { value: "reasoning description" } });
    const option = screen.getByRole("option", { name: /reasoning_effort/ });
    expect(option).toHaveTextContent("Synthetic reasoning description");
    expect(screen.getByRole("button", { name: "Add setting" })).toBeDisabled();
    fireEvent.click(option);
    expect(screen.getByRole("button", { name: "Add setting" })).toBeEnabled();
    fireEvent.change(search, { target: { value: "unknown_key" } });
    expect(screen.getByText("No matching settings")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add setting" })).toBeDisabled();
    fireEvent.keyDown(search, { key: "Enter" });
    expect(document.querySelectorAll(".profile-setting")).toHaveLength(0);
  });

  it("supports keyboard selection in the setting combobox", async () => {
    renderEditor("/model-profiles/new");
    await addProvider("OpenAI");
    const search = screen.getByRole("combobox", { name: "Search settings" });
    fireEvent.change(search, { target: { value: "temperature" } });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(search).toHaveValue("temperature");
    expect(screen.getByRole("button", { name: "Add setting" })).toBeEnabled();
    fireEvent.keyDown(search, { key: "Enter" });
    const row = await settingRow("temperature");
    expect(row).toHaveTextContent("Synthetic temperature description");
  });

  it("preserves unknown local settings across provider changes and edits", async () => {
    const source = { id: "existing", name: "existing" };
    const current = profile(
      "existing",
      null,
      [
        { key: "provider", kind: "literal", value: "OpenAI" },
        { key: "custom_option", kind: "literal", value: "old" },
      ],
      [
        { key: "provider", value: "OpenAI", source },
        { key: "custom_option", value: "old", source },
      ],
    );
    const { saves } = renderEditor("/model-profiles/existing", [current]);
    const row = await settingRow("custom_option");
    fireEvent.change(within(row).getByLabelText("Value"), {
      target: { value: "updated" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Provider" }), {
      target: { value: "DeepSeek" },
    });
    expect(within(row).getByLabelText("Value")).toHaveValue("updated");
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "custom_option", "updated");
    expect(saves[0].body.settings).toContainEqual({
      key: "provider",
      kind: "literal",
      value: "DeepSeek",
    });
  });

  it("links to upstream provider configuration accessibly", async () => {
    renderEditor("/model-profiles/new");
    const link = await screen.findByRole("link", {
      name: /Provider configuration guide.*opens in a new window/,
    });
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/machinewrapped/llm-subtrans#translation-providers",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows a selectable provider problem before Save and honors inherited unset/remove", async () => {
    const { saves } = renderEditor("/model-profiles/child", baseWithSelectableChild());
    await screen.findByRole("combobox", { name: "Provider" });
    const providerArea = document.querySelector(".profile-provider") as HTMLElement;
    fireEvent.click(within(providerArea).getByRole("button", { name: "Unset" }));
    expect(within(providerArea).getByRole("alert")).toHaveTextContent(
      "needs an available provider",
    );
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeDisabled();
    fireEvent.click(within(providerArea).getByRole("button", { name: "Remove local" }));
    expect(screen.getByRole("button", { name: "Save Model Profile" })).toBeEnabled();
    fireEvent.click(within(providerArea).getByRole("button", { name: "Override" }));
    fireEvent.change(within(providerArea).getByRole("combobox", { name: "Provider" }), {
      target: { value: "DeepSeek" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await expectSavedLiteralSetting(saves, "provider", "DeepSeek");
  });

  it.each(["loading", "error"])(
    "blocks saving an unset base provider with a selectable descendant while reference is %s",
    async (state) => {
      const referenceResponse =
        state === "error"
          ? Promise.reject(new Error("unavailable"))
          : new Promise<ModelProfileReference>(() => {});
      const { saves } = renderEditor(
        "/model-profiles/base",
        baseWithSelectableChild(),
        referenceResponse,
      );
      if (state === "error") {
        await screen.findByRole("alert");
      } else {
        await screen.findByRole("combobox", { name: "Provider" });
      }
      const providerArea = document.querySelector(".profile-provider") as HTMLElement;
      fireEvent.click(within(providerArea).getByRole("button", { name: "Unset" }));
      const save = screen.getByRole("button", { name: "Save Model Profile" });
      expect(save).toBeDisabled();
      fireEvent.click(save);
      expect(saves).toHaveLength(0);
    },
  );

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
