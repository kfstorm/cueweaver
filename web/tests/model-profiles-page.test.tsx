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

import { I18nProvider } from "../src/i18n";
import { ModelProfileEditor } from "../src/model-profiles-page";
import type { ModelProfile, ModelProfileOptions } from "../src/model-profiles";
import { ThemeProvider } from "../src/theme-provider";

const existing: ModelProfile = {
  id: "profile-1",
  name: "Synthetic profile",
  provider: "OpenAI",
  deletable: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  settings: [{ key: "temperature", value: 0.4 }],
};

const options = (provider = "OpenAI"): ModelProfileOptions => ({
  provider,
  options: [
    {
      key: "model",
      type: "string",
      description: "Model name",
      choices: null,
      value: "default-model",
    },
    { key: "retries", type: "integer", description: null, choices: null, value: 2 },
    {
      key: "temperature",
      type: "number",
      description: null,
      choices: null,
      value: 0.7,
    },
    { key: "stream", type: "boolean", description: null, choices: null, value: true },
    {
      key: "effort",
      type: "choice",
      description: null,
      choices: ["low", "high"],
      value: "low",
    },
    {
      key: "prompt_template",
      type: "multiline",
      description: null,
      choices: null,
      value: "{prompt}\n{context}",
    },
  ],
  refresh_when_changed: ["model", "stream", "effort"],
  setting_updates: {},
});

function response(body: unknown, ok = true) {
  return { ok, json: async () => body };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function optionsWithPendingSecondRequest(
  pending: ReturnType<typeof deferred<ReturnType<typeof response>>>,
) {
  let calls = 0;
  return async () => {
    calls += 1;
    return calls === 2 ? pending.promise : response(options());
  };
}

function renderEditor({
  path = "/model-profiles/new",
  profiles = [],
  referenceHandler,
  optionsHandler,
}: {
  path?: string;
  profiles?: ModelProfile[];
  referenceHandler?: () => Promise<ReturnType<typeof response>>;
  optionsHandler?: (init: RequestInit) => Promise<ReturnType<typeof response>>;
} = {}) {
  const saves: Record<string, unknown>[] = [];
  const optionRequests: Record<string, unknown>[] = [];
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    if (input === "/api/model-profiles" && !init?.method)
      return response({ model_profiles: profiles });
    if (input === "/api/model-profile-reference")
      return referenceHandler
        ? referenceHandler()
        : response({ pysubtrans_version: "2.0", providers: ["OpenAI", "DeepSeek"] });
    if (input === "/api/model-profile-options") {
      optionRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return optionsHandler ? optionsHandler(init!) : response(options());
    }
    if (input.startsWith("/api/model-profiles") && init?.method) {
      saves.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return response(existing);
    }
    throw new Error(`Unexpected request: ${input}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <ThemeProvider>
        <I18nProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route
                path="/model-profiles/:profileId"
                element={<ModelProfileEditor />}
              />
              <Route path="/settings/model-profiles" element={<div />} />
            </Routes>
          </MemoryRouter>
        </I18nProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { fetchMock, optionRequests, saves };
}

async function selectProvider(provider = "OpenAI") {
  fireEvent.change(await screen.findByRole("combobox", { name: /Provider/ }), {
    target: { value: provider },
  });
  await screen.findByLabelText("model");
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("standalone Model Profile editor", () => {
  it("shows provider registry loading and retries an error", async () => {
    const first = deferred<ReturnType<typeof response>>();
    let calls = 0;
    renderEditor({
      referenceHandler: async () => {
        calls += 1;
        return calls === 1
          ? first.promise
          : response({ pysubtrans_version: "2.0", providers: ["OpenAI"] });
      },
    });

    expect(await screen.findByText("Loading provider registry…")).toBeInTheDocument();
    first.resolve(response({ message: "Registry unavailable" }, false));
    expect(await screen.findByRole("alert")).toHaveTextContent("Registry unavailable");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(calls).toBe(2));
    expect(await screen.findByRole("combobox", { name: /Provider/ })).toBeEnabled();
  });

  it("uses the provider registry and renders each runtime option type", async () => {
    renderEditor();
    const provider = await screen.findByRole("combobox", { name: /Provider/ });
    expect(
      within(provider)
        .getAllByRole("option")
        .map((item) => item.textContent),
    ).toEqual(["Choose a provider", "OpenAI", "DeepSeek"]);
    fireEvent.change(provider, { target: { value: "OpenAI" } });

    expect(await screen.findByLabelText("model")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("retries")).toHaveAttribute("type", "number");
    expect(screen.getByLabelText("temperature")).toHaveAttribute("step", "any");
    expect(screen.getByLabelText("stream")).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText("effort").tagName).toBe("SELECT");
    expect(screen.getByLabelText("prompt_template").tagName).toBe("TEXTAREA");
    expect(screen.getByRole("link", { name: "Provider guide" })).toHaveAttribute(
      "href",
      expect.stringContaining("#translation-providers"),
    );
    expect(
      screen.queryByText(/inherit|unset|custom key|value type/i),
    ).not.toBeInTheDocument();
  });

  it("keeps runtime defaults sparse and creates or removes explicit settings", async () => {
    const { saves } = renderEditor();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "New profile" },
    });
    await selectProvider();
    expect(screen.getByLabelText("model")).toHaveValue("default-model");
    expect(
      screen.queryByRole("button", { name: "Use default" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("model"), {
      target: { value: "custom-model" },
    });
    expect(screen.getByRole("button", { name: "Use default" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("model"), { target: { value: "" } });
    fireEvent.click(screen.getByLabelText("stream"));
    const streamRow = screen.getByLabelText("stream").closest(".profile-option");
    fireEvent.click(
      within(streamRow as HTMLElement).getByRole("button", { name: "Use default" }),
    );
    fireEvent.change(screen.getByLabelText("temperature"), {
      target: { value: "0.25" },
    });
    fireEvent.change(screen.getByLabelText("retries"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("retries"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toEqual({
      name: "New profile",
      provider: "OpenAI",
      settings: [{ key: "temperature", value: 0.25 }],
    });
  });

  it("refreshes text on blur or Enter and boolean or choice immediately", async () => {
    const { optionRequests } = renderEditor();
    await selectProvider();
    expect(optionRequests).toHaveLength(1);

    fireEvent.change(screen.getByLabelText("model"), {
      target: { value: "typed-model" },
    });
    expect(optionRequests).toHaveLength(1);
    fireEvent.blur(screen.getByLabelText("model"));
    await waitFor(() => expect(optionRequests).toHaveLength(2));
    expect(optionRequests[1]).toEqual({
      provider: "OpenAI",
      settings: { model: "typed-model" },
    });

    fireEvent.change(screen.getByLabelText("model"), {
      target: { value: "entered-model" },
    });
    fireEvent.keyDown(screen.getByLabelText("model"), { key: "Enter" });
    await waitFor(() => expect(optionRequests).toHaveLength(3));
    expect(optionRequests[2]).toEqual({
      provider: "OpenAI",
      settings: { model: "entered-model" },
    });

    fireEvent.click(screen.getByLabelText("stream"));
    await waitFor(() => expect(optionRequests).toHaveLength(4));
    expect(optionRequests[3]).toMatchObject({
      settings: { model: "entered-model", stream: false },
    });
    fireEvent.change(screen.getByLabelText("effort"), { target: { value: "high" } });
    await waitFor(() => expect(optionRequests).toHaveLength(5));
    expect(optionRequests[4]).toMatchObject({
      settings: { model: "entered-model", stream: false, effort: "high" },
    });
  });

  it("ignores a stale options response after the provider changes", async () => {
    const first = deferred<ReturnType<typeof response>>();
    let calls = 0;
    renderEditor({
      optionsHandler: async () => {
        calls += 1;
        if (calls === 1) return first.promise;
        return response({
          provider: "DeepSeek",
          options: [
            {
              key: "deepseek_model",
              type: "string",
              description: null,
              choices: null,
              value: "deepseek-chat",
            },
          ],
          refresh_when_changed: [],
          setting_updates: {},
        });
      },
    });
    const provider = await screen.findByRole("combobox", { name: /Provider/ });
    fireEvent.change(provider, { target: { value: "OpenAI" } });
    expect(await screen.findByText("Refreshing provider options…")).toBeInTheDocument();
    fireEvent.change(provider, { target: { value: "DeepSeek" } });
    expect(await screen.findByLabelText("deepseek_model")).toBeInTheDocument();

    first.resolve(response(options("OpenAI")));
    await waitFor(() =>
      expect(screen.queryByLabelText("model")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("deepseek_model")).toHaveValue("deepseek-chat");
  });

  it("confirms provider changes and clears explicit settings only when accepted", async () => {
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    const { optionRequests, saves } = renderEditor({
      path: "/model-profiles/profile-1",
      profiles: [existing],
      optionsHandler: async (init) => {
        const body = JSON.parse(String(init.body)) as { provider: string };
        return response(options(body.provider));
      },
    });
    const provider = await screen.findByRole("combobox", { name: /Provider/ });
    await screen.findByLabelText("model");
    fireEvent.change(provider, { target: { value: "DeepSeek" } });
    expect(provider).toHaveValue("OpenAI");
    expect(screen.getByLabelText("temperature")).toHaveValue(0.4);

    fireEvent.change(provider, { target: { value: "DeepSeek" } });
    await waitFor(() => expect(provider).toHaveValue("DeepSeek"));
    await waitFor(() =>
      expect(optionRequests.at(-1)).toEqual({ provider: "DeepSeek", settings: {} }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() =>
      expect(saves.at(-1)).toMatchObject({ provider: "DeepSeek", settings: [] }),
    );
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("keeps the draft and current options when refresh fails, then retries", async () => {
    let calls = 0;
    const { optionRequests } = renderEditor({
      optionsHandler: async () => {
        calls += 1;
        if (calls === 2) return response({ message: "Synthetic failure" }, false);
        return response(options());
      },
    });
    await selectProvider();
    fireEvent.change(screen.getByLabelText("model"), { target: { value: "keep-me" } });
    fireEvent.blur(screen.getByLabelText("model"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Synthetic failure");
    expect(screen.getByLabelText("model")).toHaveValue("keep-me");
    expect(screen.getByLabelText("temperature")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry options" }));
    await waitFor(() => expect(optionRequests).toHaveLength(3));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByLabelText("model")).toHaveValue("keep-me");
  });

  it("drops explicit settings hidden by a successful dynamic refresh", async () => {
    const { saves } = renderEditor({
      path: "/model-profiles/profile-1",
      profiles: [
        {
          ...existing,
          settings: [
            { key: "stream", value: true },
            { key: "temperature", value: 0.4 },
          ],
        },
      ],
      optionsHandler: async (init) => {
        const { settings } = JSON.parse(String(init.body)) as {
          settings: Record<string, unknown>;
        };
        const result = options();
        if (settings.stream === false)
          result.options = result.options.filter(
            (option) => option.key !== "temperature",
          );
        return response(result);
      },
    });
    await screen.findByLabelText("temperature");
    fireEvent.click(screen.getByLabelText("stream"));
    await waitFor(() =>
      expect(screen.queryByLabelText("temperature")).not.toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].settings).toEqual([{ key: "stream", value: false }]);
  });

  it("persists only an auto-selected model while other defaults stay sparse", async () => {
    const { saves, optionRequests } = renderEditor({
      optionsHandler: async () =>
        response({
          ...options(),
          options: [
            {
              ...options().options[0],
              type: "choice",
              choices: ["model-a", "model-b"],
              value: "model-a",
            },
            ...options().options.slice(1),
            {
              key: "model_a_detail",
              type: "string",
              description: null,
              choices: null,
              value: "visible after selection",
            },
          ],
          setting_updates: { model: "model-a" },
        }),
    });
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Chosen model" },
    });
    await selectProvider();
    await waitFor(() => expect(screen.getByLabelText("model")).toHaveValue("model-a"));
    expect(screen.getByLabelText("model_a_detail")).toHaveValue(
      "visible after selection",
    );
    expect(optionRequests).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].settings).toEqual([{ key: "model", value: "model-a" }]);
  });

  it("applies the selected model after changing providers", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { saves } = renderEditor({
      path: "/model-profiles/profile-1",
      profiles: [existing],
      optionsHandler: async (init) => {
        const { provider } = JSON.parse(String(init.body)) as { provider: string };
        return response({
          ...options(provider),
          setting_updates: provider === "DeepSeek" ? { model: "model-a" } : {},
        });
      },
    });
    await screen.findByLabelText("model");
    fireEvent.change(screen.getByRole("combobox", { name: /Provider/ }), {
      target: { value: "DeepSeek" },
    });
    await waitFor(() => expect(screen.getByLabelText("model")).toHaveValue("model-a"));
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toMatchObject({
      provider: "DeepSeek",
      settings: [{ key: "model", value: "model-a" }],
    });
  });

  it("ignores stale setting updates after a newer edit and refresh", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    let calls = 0;
    const { saves } = renderEditor({
      optionsHandler: async () => {
        calls += 1;
        return calls === 2 ? pending.promise : response(options());
      },
    });
    await selectProvider();
    fireEvent.change(screen.getByLabelText("model"), { target: { value: "first" } });
    fireEvent.blur(screen.getByLabelText("model"));
    await waitFor(() => expect(calls).toBe(2));
    fireEvent.change(screen.getByLabelText("model"), { target: { value: "second" } });
    fireEvent.blur(screen.getByLabelText("model"));
    await waitFor(() => expect(calls).toBe(3));
    pending.resolve(response({ ...options(), setting_updates: { model: "old" } }));
    expect(screen.getByLabelText("model")).toHaveValue("second");
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Current" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].settings).toEqual([{ key: "model", value: "second" }]);
  });

  it("applies a model refresh after a non-refresh setting changes", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const { saves } = renderEditor({
      optionsHandler: optionsWithPendingSecondRequest(pending),
    });
    await selectProvider();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Updated profile" },
    });

    fireEvent.change(screen.getByLabelText("model"), {
      target: { value: "model-b" },
    });
    fireEvent.blur(screen.getByLabelText("model"));
    await waitFor(() =>
      expect(screen.getByText("Refreshing provider options…")).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText("temperature"), {
      target: { value: "0.25" },
    });

    const modelBOptions = options();
    modelBOptions.options = modelBOptions.options.filter(
      (option) => option.key !== "effort",
    );
    modelBOptions.options.push({
      key: "model_b_detail",
      type: "string",
      description: null,
      choices: null,
      value: "visible for model B",
    });
    pending.resolve(response(modelBOptions));

    expect(await screen.findByLabelText("model_b_detail")).toBeInTheDocument();
    expect(screen.queryByLabelText("effort")).not.toBeInTheDocument();
    expect(screen.getByLabelText("temperature")).toHaveValue(0.25);
    fireEvent.click(screen.getByRole("button", { name: "Save Model Profile" }));
    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0].settings).toEqual([
      { key: "model", value: "model-b" },
      { key: "temperature", value: 0.25 },
    ]);
  });
});
