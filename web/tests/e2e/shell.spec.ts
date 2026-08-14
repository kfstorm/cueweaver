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

async function stubProductStatus(page: Page, providerReady = true) {
  await page.route("/api/status", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        api: { ready: true },
        roots: { ready: true },
        translation_provider: providerReady
          ? { ready: true }
          : {
              ready: false,
              message:
                "Configure a provider in PySubtrans service settings, then restart CueWeaver.",
            },
        worker: { ready: true, mode: "single" },
      }),
    }),
  );
}

async function readJobs(page: Page) {
  const response = await page.request.get("/api/jobs");
  return (await response.json()).jobs as Array<{
    request: {
      target_language_code: string;
      stream_index?: number;
      source_format?: string;
    };
    status: string;
    queue_position?: number | null;
  }>;
}

async function startRealTranslation(
  page: Page,
  viewport: { name: string; width: number; height: number },
  subtitleName: RegExp,
  targetLanguage: string,
  beforeSubmit?: (page: Page) => Promise<void> | void,
) {
  await page.setViewportSize(viewport);
  await page.goto("/translate");
  await page.getByRole("button", { name: "Select Example movie" }).click();
  await page.getByRole("button", { name: subtitleName }).click();
  await page.getByLabel("Target language code").fill(targetLanguage);
  await beforeSubmit?.(page);

  const requestPromise = page.waitForRequest(
    (request) => request.url().endsWith("/api/jobs") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "Start translation" }).click();
  return requestPromise;
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
  await stubProductStatus(page, false);
  await page.goto("/translate");

  await expect(
    page.getByRole("status").filter({ hasText: "Configure a provider" }),
  ).toContainText("Configure a provider in PySubtrans service settings");
  await expect(page.getByRole("button", { name: "Start translation" })).toBeDisabled();
});

test("Term maps management works with keyboard and search on desktop and mobile", async ({
  page,
}) => {
  await stubProductStatus(page);
  await page.route("/api/term-maps", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        term_maps: [
          {
            id: "map-1",
            name: "Characters",
            entry_count: 2,
            updated_at: "2026-08-13T12:00:00Z",
          },
        ],
      }),
    }),
  );
  await page.route("/api/term-maps/map-1", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: "map-1",
        name: "Characters",
        entry_count: 2,
        updated_at: "2026-08-13T12:00:00Z",
        content: { Captain: "队长", Ship: "舰船" },
      }),
    }),
  );

  for (const viewport of [
    { width: 390, height: 844 },
    { width: 1280, height: 800 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/term-maps");
    await page.getByRole("button", { name: /Characters/ }).press("Enter");
    await expect(page.getByRole("heading", { name: "Characters" })).toBeVisible();
    await page.getByLabel("Search Source or Target").fill("ship");
    await expect(page.getByRole("cell", { name: "Ship" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Captain" })).toBeHidden();
  }
});

test("Term map mutations update the browser state", async ({ page }) => {
  let summary = {
    id: "map-1",
    name: "Characters",
    entry_count: 2,
    updated_at: "2026-08-13T12:00:00Z",
  };
  let content: Record<string, string> = { Captain: "队长", Ship: "舰船" };
  let deleted = false;
  await stubProductStatus(page);
  await page.route("/api/term-maps", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ term_maps: deleted ? [] : [summary] }),
    }),
  );
  await page.route("/api/term-maps/map-1", async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      summary = { ...summary, name: (await request.postDataJSON()).name };
    } else if (request.method() === "PUT") {
      content = (await request.postDataJSON()).content;
      summary = { ...summary, entry_count: Object.keys(content).length };
    } else if (request.method() === "DELETE") {
      deleted = true;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...summary, content }),
    });
  });

  await page.goto("/term-maps");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: /Characters/ }).click();
  expect(
    (await page.getByLabel("New Term map name").boundingBox())?.height,
  ).toBeGreaterThanOrEqual(44);
  expect(
    (await page.getByRole("button", { name: "Save name" }).boundingBox())?.height,
  ).toBeGreaterThanOrEqual(44);
  await expect(page.getByText(/Updated 2026-08-13T12:00:00Z/)).toBeVisible();
  await page.getByLabel("New Term map name").fill("People");
  await page.getByRole("button", { name: "Save name" }).click();
  await expect(page.getByRole("heading", { name: "People" })).toBeVisible();

  await page.getByLabel("Replacement JSON content").fill('{"Captain":"队长"}');
  await page.getByRole("button", { name: "Replace content" }).click();
  await expect(page.getByText(/1 entries/)).toBeVisible();
  await expect(page.getByRole("cell", { name: "Captain" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Ship" })).toBeHidden();

  await page.getByLabel("Confirm Term map name").fill("People");
  expect(
    (await page.getByLabel("Confirm Term map name").boundingBox())?.height,
  ).toBeGreaterThanOrEqual(44);
  expect(
    (await page.getByRole("button", { name: "Delete Term map" }).boundingBox())?.height,
  ).toBeGreaterThanOrEqual(44);
  await page.getByRole("button", { name: "Delete Term map" }).click();
  await expect(page.getByRole("heading", { name: "No Term maps yet" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "People" })).toBeHidden();
});

test("Term maps API validates and persists a real browser-created resource", async ({
  page,
}) => {
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
  const duplicate = await page.request.fetch("/api/term-maps", {
    method: "POST",
    headers: { "content-type": "application/json" },
    data: '{"name":"Duplicate","content":{"Source":"one","Source":"two"}}',
  });
  expect(duplicate.status()).toBe(400);
  expect((await duplicate.json()).error_code).toBe("invalid_term_map");
  const trailingComma = await page.request.fetch("/api/term-maps", {
    method: "POST",
    headers: { "content-type": "application/json" },
    data: '{"name":"Trailing","content":{"a":"b"},}',
  });
  expect(trailingComma.status()).toBe(400);
  expect((await trailingComma.json()).error_code).toBe("invalid_term_map");

  const created = await page.request.post("/api/term-maps", {
    data: { name, content: { Captain: "队长", Ship: "舰船" } },
  });
  expect(created.ok()).toBeTruthy();
  const summary = await created.json();
  const duplicateName = await page.request.post("/api/term-maps", {
    data: { name: name.toUpperCase(), content: { Other: "其他" } },
  });
  expect(duplicateName.status()).toBe(400);
  expect((await duplicateName.json()).error_code).toBe("duplicate_term_map_name");

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

const submissionSources = [
  {
    label: "External subtitle",
    subtitleName: /Select external subtitle en \(Example\.en\.srt\)/,
    candidate: {
      kind: "external",
      path: "Example.en.srt",
      format: "srt",
      tags: { language: "en", title: "" },
    },
    request: { media_path: "Example.mkv", subtitle_path: "Example.en.srt" },
  },
  {
    label: "Embedded subtitle",
    subtitleName: /Select embedded subtitle zhs \/ Chinese/,
    candidate: {
      kind: "embedded",
      stream_index: 3,
      format: "srt",
      tags: { language: "zhs", title: "Chinese" },
    },
    request: { media_path: "Example.mkv", stream_index: 3, source_format: "srt" },
  },
] as const;

test.describe("subtitle submission", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    for (const source of submissionSources) {
      test(`${viewport.name} Translate submits an ${source.label} Job`, async ({
        page,
      }) => {
        await page.setViewportSize(viewport);
        await stubProductStatus(page);
        await page.route("**/api/media/browse", (route) =>
          route.fulfill({
            contentType: "application/json",
            body: JSON.stringify({
              path: "",
              entries: [{ kind: "media", name: "Example.mkv", path: "Example.mkv" }],
            }),
          }),
        );
        await page.route("**/api/media/discover", (route) =>
          route.fulfill({
            contentType: "application/json",
            body: JSON.stringify({
              path: "Example.mkv",
              candidates: [source.candidate],
              unsupported_candidates: [],
            }),
          }),
        );
        const jobRequest = page.waitForRequest(
          (request) =>
            request.url().endsWith("/api/jobs") && request.method() === "POST",
        );
        await page.route("**/api/jobs", async (route) => {
          if (route.request().method() === "POST") {
            await route.fulfill({
              contentType: "application/json",
              body: JSON.stringify({
                id: "job-1",
                status: "Queued",
                request: { ...source.request, target_language_code: "zh-Hans" },
              }),
            });
            return;
          }
          await route.fulfill({ contentType: "application/json", body: '{"jobs":[]}' });
        });

        await page.goto("/translate");
        await page.getByRole("button", { name: "Select Example.mkv" }).click();
        await page.getByRole("button", { name: source.subtitleName }).click();
        await expect(page.locator("#target-languages option")).toHaveCount(15);
        await page.getByLabel("Target language code").fill("zh-Hans");
        const outputGroup = page.getByRole("group", { name: "Output filename" });
        await expect(outputGroup).toBeVisible();
        await expect(page.getByLabel("Media stem")).toHaveValue("Example.");
        await expect(page.getByLabel("Media stem")).toHaveAttribute("readonly");
        await expect(page.getByLabel("Subtitle suffix")).toHaveValue("zh-Hans");
        await expect(page.getByLabel("Source format extension")).toHaveValue(".srt");
        await expect(page.getByText("Example.zh-Hans.srt")).toBeVisible();
        await expect(page.getByLabel("Append a number (recommended)")).toBeChecked();
        await expect(page.getByLabel("Overwrite existing output")).not.toBeChecked();
        const outputFits = await outputGroup.evaluate(
          (element) => element.scrollWidth <= element.clientWidth,
        );
        expect(outputFits).toBe(true);
        if (viewport.name === "mobile") {
          const suffixBox = await page.getByLabel("Subtitle suffix").boundingBox();
          expect(suffixBox?.height).toBeGreaterThanOrEqual(44);
          for (const label of [
            "Append a number (recommended)",
            "Overwrite existing output",
          ]) {
            const box = await page
              .locator(".output-conflict-policy label")
              .filter({ hasText: label })
              .boundingBox();
            expect(box?.height).toBeGreaterThanOrEqual(44);
          }
        }
        await page.getByRole("button", { name: "Start translation" }).click();

        const request = await jobRequest;
        expect(await request.postDataJSON()).toEqual({
          ...source.request,
          target_language_code: "zh-Hans",
          output_suffix: "zh-Hans",
          output_conflict_policy: "append-number",
          term_map_id: null,
          dynamic_terminology_enabled: true,
          subtitle_terminology_filter_enabled: true,
        });
      });
    }
  }
});

test.describe("real translation workflow", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} remembers configuration and resets the source`, async ({
      page,
    }) => {
      const targetLanguage = `x-custom-${viewport.name}`;
      const requestPromise = await startRealTranslation(
        page,
        viewport,
        /Select external subtitle en/,
        targetLanguage,
        async (currentPage) => {
          await currentPage.getByText("Advanced settings").click();
          await currentPage.getByLabel("Dynamic terminology").uncheck();
          await currentPage.getByLabel("Subtitle terminology filtering").uncheck();
        },
      );

      const request = await requestPromise;
      expect(await request.postDataJSON()).toMatchObject({
        target_language_code: targetLanguage,
        output_suffix: targetLanguage,
        output_conflict_policy: "append-number",
        dynamic_terminology_enabled: false,
        subtitle_terminology_filter_enabled: false,
      });
      await expect(
        page.getByRole("button", { name: "Select Example movie" }),
      ).toBeVisible();
      await expect(page.getByLabel("Target language code")).toHaveValue(targetLanguage);
      await expect
        .poll(() =>
          page.evaluate(() => localStorage.getItem("cueweaver.target-language")),
        )
        .toBe(targetLanguage);
      await expect
        .poll(async () => {
          const jobs = await readJobs(page);
          return jobs.find((job) => job.request.target_language_code === targetLanguage)
            ?.status;
        })
        .toBe("Completed");

      await page.reload();
      await expect(page.getByLabel("Target language code")).toHaveValue(targetLanguage);
    });

    test(`${viewport.name} translates an Embedded subtitle through Extraction`, async ({
      page,
    }) => {
      const targetLanguage = `x-embedded-${viewport.name}`;
      const requestPromise = startRealTranslation(
        page,
        viewport,
        /Select embedded subtitle/,
        targetLanguage,
      );

      const request = await requestPromise;
      expect(await request.postDataJSON()).toMatchObject({
        media_path: "Example.mkv",
        stream_index: expect.any(Number),
        source_format: "srt",
        target_language_code: targetLanguage,
      });
      await expect(
        page.getByRole("button", { name: "Select Example movie" }),
      ).toBeVisible();
      await expect
        .poll(async () => {
          const jobs = await readJobs(page);
          return jobs.find((job) => job.request.target_language_code === targetLanguage)
            ?.status;
        })
        .toBe("Completed");
      const job = (await readJobs(page)).find(
        (candidate) => candidate.request.target_language_code === targetLanguage,
      );
      expect(job?.request.source_format).toBe("srt");
      expect(job?.request.stream_index).toEqual(expect.any(Number));
    });
  }

  test("does not remember a language when Job creation fails", async ({ page }) => {
    await page.route("**/api/jobs", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({
            error_code: "translation_failed",
            message: "Translation could not be queued.",
          }),
        });
        return;
      }
      await route.continue();
    });
    await page.goto("/translate");

    await page.getByRole("button", { name: "Select Example movie" }).click();
    await page.getByRole("button", { name: /Select external subtitle en/ }).click();
    await page.getByLabel("Target language code").fill("x-failed");
    await page.getByRole("button", { name: "Start translation" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "Translation could not be queued.",
    );
    await expect
      .poll(() =>
        page.evaluate(() => localStorage.getItem("cueweaver.target-language")),
      )
      .toBeNull();
  });

  test("serializes Jobs and keeps the API responsive while translating", async ({
    page,
  }) => {
    const create = (target_language_code: string) =>
      page.request.post("/api/jobs", {
        data: {
          media_path: "Example.mkv",
          subtitle_path: "Example.en.srt",
          target_language_code,
        },
      });

    const first = await create("queue-one");
    await expect
      .poll(async () => {
        const jobs = await readJobs(page);
        return jobs.find((job) => job.request.target_language_code === "queue-one")
          ?.status;
      })
      .toBe("Translating");
    const second = await create("queue-two");
    const third = await create("queue-three");
    expect(first.ok()).toBeTruthy();
    expect(second.ok()).toBeTruthy();
    expect(third.ok()).toBeTruthy();
    expect((await second.json()).queue_position).toBe(1);
    expect((await third.json()).queue_position).toBe(2);

    expect((await page.request.get("/api/status")).ok()).toBeTruthy();
    expect(
      (await page.request.post("/api/media/browse", { data: { path: "" } })).ok(),
    ).toBeTruthy();
    expect(
      (
        await page.request.post("/api/media/discover", {
          data: { path: "Example.mkv" },
        })
      ).ok(),
    ).toBeTruthy();
    expect((await page.request.get("/api/jobs")).ok()).toBeTruthy();

    await expect
      .poll(
        async () => {
          const jobs = await readJobs(page);
          return jobs
            .filter((job) => job.request.target_language_code.startsWith("queue-"))
            .map((job) => job.status);
        },
        { timeout: 15_000 },
      )
      .toEqual(["Completed", "Completed", "Completed"]);
  });
});
