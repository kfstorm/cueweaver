import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ChatGPTSettingsPage } from "../src/chatgpt-auth";
import { I18nProvider, translate } from "../src/i18n";
import { SettingsNavigation } from "../src/settings-page";

function renderSettings() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <I18nProvider>
        <MemoryRouter>
          <SettingsNavigation />
          <ChatGPTSettingsPage />
        </MemoryRouter>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each(["credentials_present", "not_configured", "unavailable"] as const)(
  "reports %s as local presence, with discoverable setup and recovery",
  async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status }) }),
    );
    renderSettings();
    expect(await screen.findByText(translate(`chatgpt.${status}`))).toBeInTheDocument();
    expect(screen.getByText(translate("chatgpt.disclaimer"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ChatGPT" })).toHaveAttribute(
      "href",
      "/settings/chatgpt",
    );
    expect(
      screen.getByRole("heading", { name: translate("chatgpt.localTitle") }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: translate("chatgpt.remoteTitle") }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: translate("chatgpt.recoveryTitle") }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: translate("chatgpt.guide") }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("README.md#translate-with-a-chatgpt-subscription"),
    );
    expect(
      screen.queryByRole("button", { name: /sign in|sign out/i }),
    ).not.toBeInTheDocument();
  },
);

it("shows loading and manually refreshes without reloading the page", async () => {
  let resolve!: (response: unknown) => void;
  const pending = new Promise((done) => {
    resolve = done;
  });
  const fetch = vi
    .fn()
    .mockReturnValueOnce(pending)
    .mockResolvedValue({
      ok: true,
      json: async () => ({ status: "credentials_present" }),
    });
  vi.stubGlobal("fetch", fetch);
  renderSettings();
  expect(screen.getByRole("status")).toHaveTextContent(translate("chatgpt.loading"));
  expect(
    screen.getByRole("button", { name: translate("chatgpt.refresh") }),
  ).toBeDisabled();
  resolve({ ok: true, json: async () => ({ status: "not_configured" }) });
  await screen.findByText(translate("chatgpt.not_configured"));
  fireEvent.click(screen.getByRole("button", { name: translate("chatgpt.refresh") }));
  await screen.findByText(translate("chatgpt.credentials_present"));
  expect(fetch).toHaveBeenCalledTimes(2);
});

it.each(["http", "network", "invalid"])(
  "handles %s failures without rendering response details",
  async (failure) => {
    const fetch = vi.fn();
    if (failure === "network")
      fetch.mockRejectedValueOnce(new Error("synthetic private details"));
    else
      fetch.mockResolvedValueOnce({
        ok: failure !== "http",
        json: async () => ({ status: "synthetic private details" }),
      });
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ status: "not_configured" }),
    });
    vi.stubGlobal("fetch", fetch);
    renderSettings();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      translate("chatgpt.error"),
    );
    expect(screen.queryByText(/synthetic private details/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: translate("chatgpt.refresh") }));
    await screen.findByText(translate("chatgpt.not_configured"));
  },
);

it("copies the local CLI command and supports manual copying when unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "not_configured" }),
    }),
  );
  const writeText = vi
    .fn()
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new Error("clipboard unavailable"));
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  renderSettings();
  fireEvent.click(screen.getByRole("button", { name: translate("chatgpt.copy") }));
  await screen.findByText(translate("chatgpt.copied"));
  expect(writeText).toHaveBeenCalledWith(
    "uv run cueweaver-chatgpt --work-root /absolute/work-root sign-in",
  );
  fireEvent.click(screen.getByRole("button", { name: translate("chatgpt.copy") }));
  await waitFor(() =>
    expect(screen.getByText(translate("chatgpt.copyFailed"))).toBeInTheDocument(),
  );
});
