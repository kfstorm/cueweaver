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

type E2EJobStatus = "Queued" | "Completed" | "Failed" | "Interrupted";

function jobRecord(id: string, status: E2EJobStatus = "Completed") {
  return {
    id,
    attempt: 1,
    status,
    created_at: "2026-08-13T12:00:00Z",
    started_at: "2026-08-13T12:00:01Z",
    finished_at: status === "Queued" ? null : "2026-08-13T12:00:02Z",
    queue_position: status === "Queued" ? 1 : null,
    request: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "zh-Hans",
      term_map: null,
      dynamic_terminology_enabled: true,
      subtitle_terminology_filter_enabled: true,
      output_suffix: "zh-Hans",
      output_conflict_policy: "append-number",
      output_path: "Example.zh-Hans.srt",
      source_format: "srt",
    },
    error:
      status === "Failed"
        ? { code: "translation_failed", message: "Translation failed" }
        : status === "Interrupted"
          ? { code: "job_interrupted", message: "Job was interrupted" }
          : null,
  };
}

async function stubJobs(page: Page, jobs: Array<ReturnType<typeof jobRecord>>) {
  await registerJobListRoute(page, () => jobs);
  await page.route("**/api/jobs/*", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await fulfillJobDetail(route, jobs);
  });
}

async function registerJobListRoute(
  page: Page,
  getJobs: () => Array<ReturnType<typeof jobRecord>>,
) {
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await fulfillJobList(route, getJobs());
  });
}

async function fulfillJobList(
  route: Parameters<Parameters<Page["route"]>[1]>[0],
  jobs: Array<ReturnType<typeof jobRecord>>,
) {
  await route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ jobs }),
  });
}

async function fulfillJobDetail(
  route: Parameters<Parameters<Page["route"]>[1]>[0],
  jobs: Array<ReturnType<typeof jobRecord>>,
) {
  const id = new URL(route.request().url()).pathname.split("/").pop();
  const job = jobs.find((candidate) => candidate.id === id);
  if (job === undefined) {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ message: "Job does not exist" }),
    });
    return;
  }
  await route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(job),
  });
}

async function stubMutableJobs(
  page: Page,
  initialJobs: Array<ReturnType<typeof jobRecord>>,
) {
  let jobs = [...initialJobs];
  await registerJobListRoute(page, () => jobs);
  await page.route("**/api/jobs/*", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "DELETE" && path === "/api/jobs/completed") {
      const deleted = jobs
        .filter((job) => job.status === "Completed")
        .map((job) => job.id);
      jobs = jobs.filter((job) => job.status !== "Completed");
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ deleted, failed: [] }),
      });
      return;
    }
    if (request.method() === "DELETE") {
      const id = path.split("/").pop();
      jobs = jobs.filter((job) => job.id !== id);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ id, deleted: true }),
      });
      return;
    }
    if (request.method() !== "GET") {
      await route.continue();
      return;
    }
    await fulfillJobDetail(route, jobs);
  });
}

async function readJobs(page: Page) {
  const response = await page.request.get("/api/jobs");
  return (await response.json()).jobs as Array<{
    id: string;
    request: {
      target_language_code: string;
      stream_index?: number;
      source_format?: string;
      output_path?: string;
      term_map?: { name: string; content: Record<string, string> } | null;
    };
    status: string;
    queue_position?: number | null;
    error?: { code: string; message: string } | null;
    extraction?: {
      status: string;
      path: string;
      format: string;
      content_digest: string;
    } | null;
  }>;
}

async function waitForJob(
  page: Page,
  targetLanguage: string,
  status: string,
  timeout = 15_000,
) {
  await expect
    .poll(
      async () =>
        (await readJobs(page)).find(
          (job) => job.request.target_language_code === targetLanguage,
        )?.status,
      { timeout },
    )
    .toBe(status);
  return (await readJobs(page)).find(
    (job) => job.request.target_language_code === targetLanguage,
  )!;
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

test("Translate source and subtitle selection work with the keyboard", async ({
  page,
}) => {
  await page.goto("/translate");

  const media = page.getByRole("button", { name: "Select Example movie" });
  await media.focus();
  await media.press("Enter");
  await expect(media).toHaveAttribute("aria-pressed", "true");

  const subtitle = page.getByRole("button", {
    name: /Select external subtitle en \(Example\.en\.srt\)/,
  });
  await subtitle.focus();
  await subtitle.press("Enter");
  await expect(subtitle).toHaveAttribute("aria-pressed", "true");
});

test.describe("Job history layouts", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} supports list to full detail navigation`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await stubProductStatus(page);
      await stubJobs(page, [jobRecord("job-e2e")]);
      await page.goto("/jobs");

      await page.getByRole("button", { name: /Example\.mkv/ }).click();
      await expect(page).toHaveURL(/\/jobs\/job-e2e$/);
      await expect(
        page.getByRole("heading", { name: "Request summary" }),
      ).toBeVisible();
      await expect(page.getByRole("heading", { name: "Example.mkv" })).toBeFocused();
      await expect(page.getByRole("button", { name: "Back to Jobs" })).toBeVisible();

      const list = page.getByRole("list", { name: "Translation Jobs" });
      await expect(list)[viewport.name === "mobile" ? "toBeHidden" : "toBeVisible"]();
      if (viewport.name === "mobile") {
        expect(
          (await page.getByRole("button", { name: "Back to Jobs" }).boundingBox())
            ?.height,
        ).toBeGreaterThanOrEqual(44);
      }

      await page.getByRole("button", { name: "Back to Jobs" }).click();
      await expect(page).toHaveURL(/\/jobs$/);
      if (viewport.name === "mobile") {
        await expect(page.getByRole("button", { name: /Example\.mkv/ })).toBeVisible();
      } else {
        await expect(page.getByRole("heading", { name: "Select a Job" })).toBeVisible();
      }
    });
  }
});

test.describe("Job history mutations", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} clears Completed and deletes a terminal Job`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await stubProductStatus(page);
      await stubMutableJobs(page, [
        jobRecord("job-completed"),
        jobRecord("job-failed", "Failed"),
      ]);
      page.on("dialog", (dialog) => dialog.accept());
      await page.goto("/jobs");

      await expect(
        page.getByRole("button", { name: "Clear Completed (1)" }),
      ).toBeEnabled();
      await expect(page.getByText("2 total")).toBeVisible();
      await page.getByRole("button", { name: "Clear Completed (1)" }).click();
      await expect(
        page.getByRole("button", { name: "Clear Completed (0)" }),
      ).toBeDisabled();
      await expect(page.getByText("1 total")).toBeVisible();
      await expect(page.getByRole("button", { name: /Example\.mkv/ })).toBeVisible();

      await page.getByRole("button", { name: /Example\.mkv/ }).click();
      await page.getByRole("button", { name: "Delete Job" }).click();
      await expect(page.getByRole("heading", { name: "No Jobs yet" })).toBeVisible();
      await expect(page).toHaveURL(/\/jobs$/);
      await expect(page.getByRole("heading", { name: "All Jobs" })).toBeFocused();
      if (viewport.name === "mobile") {
        await expect(page.getByRole("heading", { name: "No Jobs yet" })).toBeVisible();
      }
    });
  }
});

test("desktop Job history keeps a long list scrollable", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await stubProductStatus(page);
  await stubJobs(
    page,
    Array.from({ length: 30 }, (_, index) => jobRecord(`job-${index}`)),
  );
  await page.goto("/jobs");

  await expect(page.getByRole("listitem")).toHaveCount(30);
  expect(
    await page
      .locator(".job-list")
      .evaluate((element) => element.scrollHeight > element.clientHeight),
  ).toBe(true);
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

test("production release matrix covers durable Job behavior", async ({ page }) => {
  test.skip(process.env.CUEWEAVER_E2E_PHASE === "restart");

  const termMapResponse = await page.request.post("/api/term-maps", {
    data: {
      name: "Release matrix terms",
      content: { Captain: "队长", Ship: "舰船" },
    },
  });
  expect(termMapResponse.ok()).toBeTruthy();
  const termMap = await termMapResponse.json();

  const external = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "e2e-term-map",
      term_map_id: termMap.id,
    },
  });
  expect(external.ok()).toBeTruthy();
  const completedExternal = await waitForJob(page, "e2e-term-map", "Completed");
  expect(completedExternal.request.term_map).toMatchObject({
    name: "Release matrix terms",
    content: { Captain: "队长", Ship: "舰船" },
  });

  const embedded = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      stream_index: 1,
      source_format: "srt",
      target_language_code: "e2e-embedded",
    },
  });
  expect(embedded.ok()).toBeTruthy();
  const completedEmbedded = await waitForJob(page, "e2e-embedded", "Completed");
  expect(completedEmbedded.request.stream_index).toBe(1);
  expect(completedEmbedded.request.source_format).toBe("srt");
  expect(completedEmbedded.extraction).toMatchObject({
    status: "Completed",
    path: "source.srt",
    format: "srt",
  });
  expect(completedEmbedded.extraction?.content_digest).toMatch(/^[0-9a-f]{64}$/);

  for (const targetLanguage of ["e2e-retry-external", "e2e-retry-embedded"]) {
    const request =
      targetLanguage === "e2e-retry-external"
        ? {
            media_path: "Example.mkv",
            subtitle_path: "Example.en.srt",
            target_language_code: targetLanguage,
          }
        : {
            media_path: "Example.mkv",
            stream_index: 1,
            source_format: "srt",
            target_language_code: targetLanguage,
          };
    const created = await page.request.post("/api/jobs", { data: request });
    expect(created.ok()).toBeTruthy();
    const failed = await waitForJob(page, targetLanguage, "Failed");
    expect(failed.error?.code).toBe("translation_failed");
    const retried = await page.request.post(`/api/jobs/${failed.id}/retry`);
    expect(retried.ok()).toBeTruthy();
    const retriedJob = await waitForJob(page, targetLanguage, "Completed");
    expect(retriedJob.id).toBe(failed.id);
  }

  const numberedRequest = {
    media_path: "Example.mkv",
    subtitle_path: "Example.en.srt",
    target_language_code: "e2e-number-one",
    output_suffix: "release-number",
  };
  const firstNumbered = await page.request.post("/api/jobs", {
    data: numberedRequest,
  });
  expect(firstNumbered.ok()).toBeTruthy();
  const firstNumberedJob = await waitForJob(page, "e2e-number-one", "Completed");
  expect(firstNumberedJob.request.output_path).toBe("Example.release-number.srt");
  const secondNumbered = await page.request.post("/api/jobs", {
    data: { ...numberedRequest, target_language_code: "e2e-number-two" },
  });
  expect(secondNumbered.ok()).toBeTruthy();
  const secondNumberedJob = await waitForJob(page, "e2e-number-two", "Completed");
  expect(secondNumberedJob.request.output_path).toBe("Example.release-number.2.srt");

  const overwriteRequest = {
    media_path: "Example.mkv",
    subtitle_path: "Example.en.srt",
    output_suffix: "release-overwrite",
    output_conflict_policy: "overwrite",
  };
  const overwriteFirst = await page.request.post("/api/jobs", {
    data: { ...overwriteRequest, target_language_code: "e2e-overwrite-one" },
  });
  expect(overwriteFirst.ok()).toBeTruthy();
  await waitForJob(page, "e2e-overwrite-one", "Completed");
  const overwriteSecond = await page.request.post("/api/jobs", {
    data: { ...overwriteRequest, target_language_code: "e2e-overwrite-two" },
  });
  expect(overwriteSecond.ok()).toBeTruthy();
  const overwriteSecondJob = await waitForJob(page, "e2e-overwrite-two", "Completed");
  expect(overwriteSecondJob.request.output_path).toBe("Example.release-overwrite.srt");

  const permanentFailure = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "e2e-fail-permanent",
    },
  });
  expect(permanentFailure.ok()).toBeTruthy();
  const failedJob = await waitForJob(page, "e2e-fail-permanent", "Failed");
  const deleted = await page.request.delete(`/api/jobs/${failedJob.id}`);
  expect(deleted.ok()).toBeTruthy();
  expect((await readJobs(page)).some((job) => job.id === failedJob.id)).toBe(false);

  const restartMarker = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "e2e-restart-marker",
    },
  });
  expect(restartMarker.ok()).toBeTruthy();
  await waitForJob(page, "e2e-restart-marker", "Completed");
});

test("production restart preserves terminal Job history", async ({ page }) => {
  test.skip(process.env.CUEWEAVER_E2E_PHASE !== "restart");

  const marker = await waitForJob(page, "e2e-restart-marker", "Completed");
  expect(marker.request.media_path).toBe("Example.mkv");
});
