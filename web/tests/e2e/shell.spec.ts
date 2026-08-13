import { expect, test, type Page } from "@playwright/test";

const routes = [
  ["/translate", "Translate"],
  ["/jobs", "Jobs"],
  ["/term-maps", "Term maps"],
] as const;

async function expectResponsiveShell(page: Page, mobile: boolean) {
  for (const [path, title] of routes) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    const desktop = page.getByRole("navigation", { name: "Primary navigation" });
    const bottom = page.getByRole("navigation", { name: "Mobile navigation" });
    await expect(desktop)[mobile ? "toBeHidden" : "toBeVisible"]();
    await expect(bottom)[mobile ? "toBeVisible" : "toBeHidden"]();
  }
}

test("desktop shell renders every product route", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await expectResponsiveShell(page, false);
});

test("mobile shell renders every product route", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expectResponsiveShell(page, true);
});

test("mobile primary actions meet the touch target", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/translate");

  const button = page.getByRole("button", { name: "Start translation" });
  const box = await button.boundingBox();

  expect(box?.height).toBeGreaterThanOrEqual(44);
});

test("unavailable provider is actionable and cannot submit", async ({ page }) => {
  await page.goto("/translate");

  await expect(page.getByRole("status")).toContainText(
    "Configure a provider in PySubtrans service settings",
  );
  await expect(page.getByRole("button", { name: "Start translation" })).toBeDisabled();
});

test("Term maps management works with keyboard and search on desktop and mobile", async ({ page }) => {
  await page.route("/api/status", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ api: { ready: true }, roots: { ready: true }, translation_provider: { ready: true }, worker: { ready: true, mode: "single" } }),
    }),
  );
  await page.route("/api/term-maps", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ term_maps: [{ id: "map-1", name: "Characters", entry_count: 2, updated_at: "2026-08-13T12:00:00Z" }] }),
    }),
  );
  await page.route("/api/term-maps/map-1", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ id: "map-1", name: "Characters", entry_count: 2, updated_at: "2026-08-13T12:00:00Z", content: { Captain: "队长", Ship: "舰船" } }),
    }),
  );

  for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/term-maps");
    await page.getByRole("button", { name: /Characters/ }).press("Enter");
    await expect(page.getByRole("heading", { name: "Characters" })).toBeVisible();
    await page.getByLabel("Search Source or Target").fill("ship");
    await expect(page.getByRole("cell", { name: "Ship" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Captain" })).toBeHidden();
  }
});

test("Term maps API validates and persists a real browser-created resource", async ({ page }) => {
  const name = `Browser terms ${Date.now()}`;
  const invalidCases = [
    { name: "Empty", content: {} },
    { name: "Blank source", content: { "": "target" } },
    { name: "Blank target", content: { source: "" } },
    { name: "Folded", content: { Source: "one", source: "two" } },
  ];

  for (const body of invalidCases) {
    const response = await page.request.post("/api/term-maps", { data: body });
    expect(response.status()).toBe(400);
    expect((await response.json()).error_code).toBe("invalid_term_map");
  }

  const created = await page.request.post("/api/term-maps", {
    data: { name, content: { Captain: "队长", Ship: "舰船" } },
  });
  expect(created.ok()).toBeTruthy();
  const summary = await created.json();
  const duplicate = await page.request.post("/api/term-maps", {
    data: { name: name.toUpperCase(), content: { Other: "其他" } },
  });
  expect(duplicate.status()).toBe(400);
  expect((await duplicate.json()).error_code).toBe("duplicate_term_map_name");

  const detail = await page.request.get(`/api/term-maps/${summary.id}`);
  expect((await detail.json()).content).toEqual({ Captain: "队长", Ship: "舰船" });

  await page.goto("/term-maps");
  await page.getByRole("button", { name: new RegExp(name) }).press("Enter");
  await page.getByLabel("Search Source or Target").fill("captain");
  await expect(page.getByRole("cell", { name: "Captain" })).toBeVisible();
});

test("mobile Translate can select a labelled Media", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/translate");

  const media = page.getByRole("button", { name: /Select Example movie/ });
  await expect(media).toBeVisible();
  await media.click();
  await expect(media).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Selected")).toBeVisible();
});

test.describe("explicit subtitle selection", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} Translate shows and selects discovered subtitles`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await page.goto("/translate");

      await page.getByRole("button", { name: /Select Example movie/ }).click();
      const external = page.getByRole("button", {
        name: /Select external subtitle en \(Example\.en\.srt\)/,
      });
      const embedded = page.getByRole("button", {
        name: /Select embedded subtitle/,
      });
      await expect(external).toBeVisible();
      await expect(external).toHaveAttribute("aria-pressed", "false");
      await expect(embedded).toBeVisible();
      await expect(embedded).toHaveAttribute("aria-pressed", "false");

      await external.click();
      await expect(external).toHaveAttribute("aria-pressed", "true");
    });
  }
});
