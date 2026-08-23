import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

import { jobRecord } from "./fixtures";

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

async function expectAccessibleProductRoutes(page: Page, dark = false) {
  if (dark)
    await page.addInitScript(() => localStorage.setItem("cueweaver.theme", "dark"));
  for (const [path, title] of routes) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
    if (dark) await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations,
      `${path}${dark ? " dark theme" : ""} accessibility violations`,
    ).toEqual([]);
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
                "Set PROVIDER and the matching provider environment variables, then restart CueWeaver.",
            },
        worker: { ready: true, mode: "single" },
      }),
    }),
  );
}

async function stubTermMapRoutes(
  page: Page,
  termMaps: unknown[],
  directoryState: Record<string, unknown>,
) {
  await page.route("/api/term-maps", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ term_maps: termMaps }),
    }),
  );
  await page.route("**/api/term-maps/directory**", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(directoryState),
    }),
  );
}

async function stubJobCreation(page: Page): Promise<Array<Record<string, unknown>>> {
  const submissions: Array<Record<string, unknown>> = [];
  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() === "POST") {
      submissions.push(JSON.parse(route.request().postData() ?? "{}"));
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(jobRecord(`job-term-map-${submissions.length}`, "Queued")),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ active_jobs: [], history_jobs: [], next_cursor: null }),
    });
  });
  return submissions;
}

async function stubBatchTranslate(page: Page) {
  await page.route("/api/media/browse", async (route) => {
    const path = postedPath(route);
    await fulfillJson(route, {
      path,
      entries:
        path === ""
          ? [
              { kind: "media", name: "First.mkv", path: "First.mkv" },
              { kind: "media", name: "Second.mkv", path: "Second.mkv" },
            ]
          : [],
    });
  });
  await page.route("/api/media/discover", async (route) => {
    const path = postedPath(route);
    await fulfillJson(route, {
      path,
      candidates: [
        {
          kind: "external",
          path: path.replace(".mkv", ".en.srt"),
          format: "srt",
          tags: { language: "en", title: "English" },
        },
        ...(path === "First.mkv"
          ? [
              {
                kind: "embedded",
                stream_index: 3,
                format: "ass",
                tags: { language: "zhs", title: "Chinese" },
              },
            ]
          : []),
      ],
      unsupported_candidates: [],
    });
  });
  await page.route("/api/term-maps", (route) => fulfillJson(route, { term_maps: [] }));
  await page.route("**/api/term-maps/directory**", (route) =>
    fulfillJson(route, {
      directory: "",
      local: null,
      effective: null,
      source_directory: null,
    }),
  );
}

function postedPath(route: Route): string {
  return (JSON.parse(route.request().postData() ?? "{}").path ?? "") as string;
}

function fulfillJson(route: Route, body: unknown) {
  return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
}

async function stubBatchJobs(page: Page) {
  await page.route("/api/jobs", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ active_jobs: [], history_jobs: [], next_cursor: null }),
    }),
  );
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
  await page.route("**/api/jobs**", async (route) => {
    if (!isJobsCollectionRequest(route.request())) {
      await route.continue();
      return;
    }
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await fulfillJobList(route, getJobs());
  });
}

function isJobsCollectionRequest(request: { url(): string }): boolean {
  return new URL(request.url()).pathname === "/api/jobs";
}

async function fulfillJobList(
  route: Parameters<Parameters<Page["route"]>[1]>[0],
  jobs: Array<ReturnType<typeof jobRecord>>,
) {
  const activeStatuses = new Set(["Queued", "Extracting", "Translating"]);
  await route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      active_jobs: jobs.filter((job) => activeStatuses.has(job.status)),
      history_jobs: jobs.filter((job) => !activeStatuses.has(job.status)),
      next_cursor: null,
      matching_count: jobs.length,
      completed_count: jobs.filter((job) => job.status === "Completed").length,
    }),
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

type E2EJobSummary = {
  id: string;
  attempt: number;
  started_at: string | null;
  finished_at: string | null;
  request: {
    target_language_code: string;
    stream_index?: number;
    source_format?: string;
    output_path?: string;
    term_map?: { id: string; name: string } | null;
  };
  status: string;
  queue_position?: number | null;
  error?: { code: string; message: string } | null;
};

type E2EJobDetail = E2EJobSummary & {
  extraction?: {
    status: string;
    path: string;
    format: string;
    content_digest: string;
  } | null;
};

async function readJobs(page: Page): Promise<E2EJobSummary[]> {
  const response = await page.request.get("/api/jobs");
  const body = await response.json();
  return [...body.active_jobs, ...body.history_jobs] as E2EJobSummary[];
}

async function readJobDetail(page: Page, jobId: string): Promise<E2EJobDetail> {
  const response = await page.request.get(`/api/jobs/${jobId}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as E2EJobDetail;
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
  await fillCustomTargetLanguage(page, targetLanguage);
  await beforeSubmit?.(page);

  const requestPromise = page.waitForRequest(
    (request) => request.url().endsWith("/api/jobs") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "Start translation" }).click();
  return requestPromise;
}

async function fillCustomTargetLanguage(page: Page, language: string) {
  await page.getByLabel("Common target language").selectOption("custom");
  await page.getByLabel("Target language code").fill(language);
}

test("desktop shell renders every product route", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await expectResponsiveShell(page, false);
});

test("mobile shell renders every product route", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expectResponsiveShell(page, true);
});

test("theme switching stays separate from mobile navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/translate");

  await expect(page.locator(".sidebar-theme-toggle")).toBeVisible();
  await expect(page.locator(".page-theme-toggle")).toBeHidden();
  await expect(
    page.getByRole("navigation", { name: "Mobile navigation" }),
  ).toBeHidden();
  await page.locator(".sidebar-theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".sidebar-theme-toggle")).toBeHidden();
  await expect(page.locator(".page-theme-toggle")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Mobile navigation" }).getByRole("link"),
  ).toHaveCount(3);
  await page.locator(".page-theme-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test.describe("accessibility regressions", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} product routes have no axe violations`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await expectAccessibleProductRoutes(page);
    });

    test(`${viewport.name} dark product routes have no axe violations`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await expectAccessibleProductRoutes(page, true);
    });

    test(`${viewport.name} active translation configuration has no axe violations`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await page.goto("/translate");

      await page.getByRole("button", { name: "Select Example movie" }).click();
      await page
        .getByRole("button", {
          name: /Select external subtitle en \(Example\.en\.srt\)/,
        })
        .click();
      await expect(
        page.getByRole("combobox", { name: "Common target language" }),
      ).toBeEnabled();

      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations, "active translation configuration violations").toEqual(
        [],
      );
    });
  }
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

test.describe("responsive Media and Discovery layout", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} keeps selected Media with its Discovery`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await stubProductStatus(page);
      await stubBatchTranslate(page);
      await stubBatchJobs(page);
      await page.goto("/translate");

      const media = page.getByRole("button", { name: "Select First.mkv" });
      await media.click();
      await expect(
        page.getByRole("region", { name: "Subtitle selection for First.mkv" }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Choose another Media" }),
      ).toBeVisible();

      if (viewport.name === "desktop") {
        const browserBox = await page
          .getByRole("region", { name: "Media browser" })
          .boundingBox();
        const discoveryBox = await page
          .getByRole("region", { name: "Subtitle selection for First.mkv" })
          .boundingBox();
        expect(browserBox?.x).toBeLessThan(discoveryBox?.x ?? 0);
        expect((browserBox?.x ?? 0) + (browserBox?.width ?? 0)).toBeLessThan(
          discoveryBox?.x ?? 0,
        );
      } else {
        await expect(
          page.getByRole("button", { name: "Select Second.mkv" }),
        ).toBeHidden();
      }

      await page.reload();
      await page.getByLabel("Batch mode").check();
      const firstMedia = page.getByRole("button", { name: "Select First.mkv" });
      const secondMedia = page.getByRole("button", { name: "Select Second.mkv" });
      await firstMedia.click();
      if (viewport.name === "mobile") {
        await expect(secondMedia).toBeHidden();
        await expect(firstMedia).toBeVisible();
        await expect(
          page.getByRole("button", { name: "Select another Media" }),
        ).toBeVisible();
      } else {
        await expect(secondMedia).toBeVisible();
        await expect(
          page.getByRole("button", { name: "Select another Media" }),
        ).toBeHidden();
      }
    });
  }
});

test.describe("batch Translate workflow", () => {
  for (const viewport of [
    { name: "desktop", width: 1280, height: 800 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    test(`${viewport.name} supports keyboard selection, submission, and mixed results`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await stubProductStatus(page);
      await stubBatchTranslate(page);
      await stubBatchJobs(page);

      const submissions: Array<Record<string, unknown>> = [];
      await page.route("/api/jobs/batch", async (route) => {
        submissions.push(
          (await route.request().postDataJSON()) as Record<string, unknown>,
        );
        await route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            results: [
              { id: "batch-job-1" },
              {
                error_code: "term_map_not_found",
                message: "Term map does not exist",
                field: "term_map_id",
              },
            ],
          }),
        });
      });

      await page.goto("/translate");
      await page.getByLabel("Batch mode").press("Space");

      const firstMedia = page.getByRole("button", { name: "Select First.mkv" });
      const secondMedia = page.getByRole("button", { name: "Select Second.mkv" });
      await firstMedia.press("Enter");
      if (viewport.name === "mobile") {
        await page.getByRole("button", { name: "Select another Media" }).click();
      }
      await secondMedia.press("Enter");
      await expect(firstMedia).toHaveAttribute("aria-pressed", "true");
      await expect(secondMedia).toHaveAttribute("aria-pressed", "true");

      const discoveries = page.getByRole("region", {
        name: /Subtitle selection for/,
      });
      await expect(discoveries).toHaveCount(2);
      await page.getByRole("button", { name: "Resolve candidates" }).click();
      const firstExternalCandidate = page.getByRole("button", {
        name: "Select external subtitle en / English (First.en.srt)",
      });
      const secondExternalCandidate = page.getByRole("button", {
        name: "Select external subtitle en / English (Second.en.srt)",
      });
      await expect(firstExternalCandidate).toBeVisible();
      await expect(secondExternalCandidate).toBeVisible();
      const selectionResults = await new AxeBuilder({ page }).analyze();
      expect(
        selectionResults.violations,
        `${viewport.name} batch selection violations`,
      ).toEqual([]);

      const candidateSearch = page.getByRole("searchbox", {
        name: "Search subtitle candidates",
      });
      const uniqueButton = page.getByRole("button", { name: "Select unique" });
      const batchModeLabel = page.locator("label.checkbox-field").filter({
        hasText: "Batch mode",
      });
      const conflictPolicyLabels = page.locator(".output-conflict-policy label");
      if (viewport.name === "mobile") {
        for (const control of [
          batchModeLabel,
          firstMedia,
          secondMedia,
          firstExternalCandidate,
          secondExternalCandidate,
          uniqueButton,
          candidateSearch,
          conflictPolicyLabels.nth(0),
          conflictPolicyLabels.nth(1),
        ]) {
          expect((await control.boundingBox())?.height).toBeGreaterThanOrEqual(44);
        }
      }
      await candidateSearch.fill("English");
      await firstExternalCandidate.press("Enter");
      await expect(firstExternalCandidate).toHaveAttribute("aria-pressed", "true");
      const searchResults = await new AxeBuilder({ page }).analyze();
      expect(
        searchResults.violations,
        `${viewport.name} candidate search violations`,
      ).toEqual([]);

      await fillCustomTargetLanguage(page, "zh-Hans");
      const submitResults = await new AxeBuilder({ page }).analyze();
      expect(
        submitResults.violations,
        `${viewport.name} batch submit violations`,
      ).toEqual([]);
      const queueButton = page.getByRole("button", {
        name: "Queue selected translations",
      });
      if (viewport.name === "mobile") {
        expect((await queueButton.boundingBox())?.height).toBeGreaterThanOrEqual(44);
      }
      await queueButton.click();

      await expect.poll(() => submissions).toHaveLength(1);
      expect(submissions[0]).toMatchObject({
        items: [
          { media_path: "First.mkv", subtitle_path: "First.en.srt" },
          { media_path: "Second.mkv", subtitle_path: "Second.en.srt" },
        ],
        target_language_code: "zh-Hans",
      });
      await expect(page.getByRole("heading", { name: "Batch results" })).toBeVisible();
      await expect(page.getByRole("status")).toContainText("1 Job queued · 1 error.");
      const resultList = page.getByLabel("Batch submission results");
      await expect(
        resultList.getByRole("group", { name: "First.mkv batch result" }),
      ).toContainText("Queued");
      const failedResult = resultList.getByRole("group", {
        name: "Second.mkv batch result",
      });
      await expect(failedResult).toContainText("Term map does not exist");
      await failedResult.getByText("Show error details").click();
      await expect(failedResult).toContainText("term_map_not_found");
      await expect(resultList.getByRole("button", { name: "View Job" })).toBeVisible();
      if (viewport.name === "mobile") {
        expect(
          (await resultList.getByRole("button", { name: "View Job" }).boundingBox())
            ?.height,
        ).toBeGreaterThanOrEqual(44);
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= window.innerWidth,
          ),
        ).toBe(true);
      }
      const resultChecks = await new AxeBuilder({ page }).analyze();
      expect(
        resultChecks.violations,
        `${viewport.name} batch result violations`,
      ).toEqual([]);
    });
  }
});

test("batch Translate creates independent Jobs in request order through the real queue", async ({
  page,
}) => {
  const targetLanguage = "e2e-batch";

  await page.goto("/translate");
  await page.getByLabel("Batch mode").check();
  await page.getByRole("button", { name: "Select Example movie" }).click();
  await page.getByRole("button", { name: "Select Second.mkv" }).click();
  const discoveries = page.getByRole("region", {
    name: /Subtitle selection for/,
  });
  await expect(discoveries).toHaveCount(2);
  await discoveries.nth(0).getByRole("button", { name: "Resolve candidates" }).click();
  await discoveries.nth(1).getByRole("button", { name: "Resolve candidates" }).click();
  await discoveries
    .nth(0)
    .getByRole("button", { name: /Select external subtitle/ })
    .click();
  await discoveries
    .nth(1)
    .getByRole("button", { name: /Select external subtitle/ })
    .click();
  await fillCustomTargetLanguage(page, targetLanguage);

  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/jobs/batch") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Queue selected translations" }).click();

  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const results = (await response.json()).results as Array<{
    id: string;
    request: { media_path: string };
  }>;
  expect(results.map((result) => result.request.media_path)).toEqual([
    "Example.mkv",
    "Second.mkv",
  ]);
  expect(results.map((result) => result.id)).toHaveLength(2);
  expect(new Set(results.map((result) => result.id)).size).toBe(2);

  const jobs = await Promise.all(
    results.map(async (result) => {
      await expect
        .poll(async () => (await readJobDetail(page, result.id)).status, {
          timeout: 15_000,
        })
        .toBe("Completed");
      return readJobDetail(page, result.id);
    }),
  );
  expect(jobs.map((job) => job.request.media_path)).toEqual([
    "Example.mkv",
    "Second.mkv",
  ]);
  expect(jobs.every((job) => job.error === null)).toBe(true);
  expect(new Date(jobs[0].finished_at!).getTime()).toBeLessThanOrEqual(
    new Date(jobs[1].started_at!).getTime(),
  );
});

test("Translate manages the current Directory default binding", async ({ page }) => {
  const termMap = {
    id: "map-directory",
    name: "Series terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  const childTermMap = {
    id: "map-directory-child",
    name: "Season terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  const replacementTermMap = {
    id: "map-directory-replacement",
    name: "Replacement terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  const directoryStates = new Map<string, { local: typeof termMap | null }>();
  const canonicalDirectory = (path: string) =>
    path === "alias" ? "Series" : path === "alias/Season 1" ? "Series/Season 1" : path;
  const readDirectoryState = (directory: string) => {
    const local = directoryStates.get(directory)?.local ?? null;
    if (local !== null) {
      return {
        directory,
        local,
        effective: local,
        source_directory: directory,
      };
    }
    const parent = directory.includes("/")
      ? directory.slice(0, directory.lastIndexOf("/"))
      : null;
    const inherited = parent === null ? null : readDirectoryState(parent);
    return {
      directory,
      local: null,
      effective: inherited?.effective ?? null,
      source_directory: inherited?.source_directory ?? null,
    };
  };
  await page.route("**/api/media/browse", async (route) => {
    const path = (JSON.parse(route.request().postData() ?? "{}").path ?? "") as string;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        path,
        entries:
          path === ""
            ? [{ kind: "directory", name: "Series", path: "alias" }]
            : path === "alias"
              ? [{ kind: "directory", name: "Season 1", path: "alias/Season 1" }]
              : [],
      }),
    });
  });
  await page.route("/api/term-maps", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ term_maps: [termMap, childTermMap, replacementTermMap] }),
    }),
  );
  await page.route("**/api/term-maps/directory**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).searchParams.get("path") ?? "";
    if (request.method() === "GET") {
      const directory = canonicalDirectory(path);
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(readDirectoryState(directory)),
      });
      return;
    }
    if (request.method() === "PUT") {
      const body = JSON.parse(request.postData() ?? "{}") as {
        path: string;
        term_map_id: string;
      };
      const selected =
        body.term_map_id === childTermMap.id
          ? childTermMap
          : body.term_map_id === replacementTermMap.id
            ? replacementTermMap
            : termMap;
      directoryStates.set(canonicalDirectory(body.path), { local: selected });
    } else if (request.method() === "DELETE") {
      const body = JSON.parse(request.postData() ?? "{}") as { path: string };
      directoryStates.delete(canonicalDirectory(body.path));
    }
    const body = JSON.parse(request.postData() ?? "{}") as { path: string };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(readDirectoryState(canonicalDirectory(body.path))),
    });
  });
  const expectEffectiveTermMap = async (name: string) => {
    await expect(
      page
        .getByRole("region", { name: "Directory default" })
        .locator(".directory-term-map-state dd")
        .filter({ hasText: name })
        .first(),
    ).toBeVisible();
  };

  await page.goto("/translate");
  await page.getByRole("button", { name: "Open Series" }).click();
  await expect(page.getByText("Effective Term map")).toBeVisible();
  await page
    .getByRole("combobox", { name: "Directory default" })
    .selectOption(termMap.id);
  await page.getByRole("button", { name: "Bind Term map" }).click();
  await expectEffectiveTermMap("Series terms");
  await expect(
    page.getByRole("button", { name: "Remove local binding" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open Season 1" }).click();
  await expectEffectiveTermMap("Series terms");
  await expect(page.getByText("Inherited from Series")).toBeVisible();
  await page
    .getByRole("combobox", { name: "Directory default" })
    .selectOption(childTermMap.id);
  await page.getByRole("button", { name: "Bind Term map" }).click();
  await expectEffectiveTermMap("Season terms");
  await page.getByRole("button", { name: "alias", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Directory default" })
    .selectOption(replacementTermMap.id);
  await page.getByRole("button", { name: "Replace local binding" }).click();
  await expectEffectiveTermMap("Replacement terms");
  await page.getByRole("button", { name: "Open Season 1" }).click();
  await expectEffectiveTermMap("Season terms");
  await page.getByRole("button", { name: "alias", exact: true }).click();
  await page.getByRole("button", { name: "Remove local binding" }).click();
  await expect(page.getByText("No default")).toBeVisible();
});

test("Translate Term map controls are keyboard-operable", async ({ page }) => {
  const termMap = {
    id: "map-keyboard",
    name: "Keyboard terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  await stubTermMapRoutes(page, [termMap], {
    directory: "",
    local: null,
    effective: null,
    source_directory: null,
  });

  await page.goto("/translate");
  const directoryDefault = page.getByRole("combobox", { name: "Directory default" });
  await expect(directoryDefault).toBeEnabled();
  await directoryDefault.focus();
  await directoryDefault.press("ArrowDown");
  await directoryDefault.press("Enter");
  await expect(directoryDefault).toHaveValue(termMap.id);

  await page.getByRole("button", { name: "Select Example movie" }).click();
  await page
    .getByRole("button", {
      name: /Select external subtitle en \(Example\.en\.srt\)/,
    })
    .click();
  const jobTermMap = page.getByRole("combobox", {
    name: "Term map for this translation",
  });
  await jobTermMap.focus();
  await jobTermMap.press("ArrowDown");
  await jobTermMap.press("ArrowDown");
  await jobTermMap.press("Enter");
  await expect(jobTermMap).toHaveValue(termMap.id);
});

test("Translate submits the server-authoritative directory default", async ({
  page,
}) => {
  await stubProductStatus(page);
  const defaultTermMap = {
    id: "map-follow",
    name: "Series terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  await stubTermMapRoutes(page, [defaultTermMap], {
    directory: "",
    local: null,
    effective: defaultTermMap,
    source_directory: "",
  });
  const submissions = await stubJobCreation(page);

  await page.goto("/translate");
  await page.getByRole("button", { name: "Select Example movie" }).click();
  await page
    .getByRole("button", {
      name: /Select external subtitle en \(Example\.en\.srt\)/,
    })
    .click();
  await fillCustomTargetLanguage(page, "zh-Hans");
  await expect(page.locator("#term-map-select")).toHaveValue("__directory_default__");
  await expect(page.locator("#term-map-select")).toContainText("Series terms");
  await page.getByRole("button", { name: "Start translation" }).click();

  await expect.poll(() => submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({
    term_map_mode: "follow",
    term_map_id: null,
  });
});

test("Translate keeps one-off Term map choices scoped to each submission", async ({
  page,
}) => {
  await stubProductStatus(page);
  const termMap = {
    id: "map-one-off",
    name: "One-off terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  await stubTermMapRoutes(page, [termMap], {
    directory: "",
    local: null,
    effective: termMap,
    source_directory: "",
  });
  const submissions = await stubJobCreation(page);

  const submit = async (termMapValue: string) => {
    await page.getByRole("button", { name: "Select Example movie" }).click();
    await page
      .getByRole("button", {
        name: /Select external subtitle en \(Example\.en\.srt\)/,
      })
      .click();
    await fillCustomTargetLanguage(page, "zh-Hans");
    await page.locator("#term-map-select").selectOption(termMapValue);
    const expectedSubmissionCount = submissions.length + 1;
    await page.getByRole("button", { name: "Start translation" }).click();
    await expect.poll(() => submissions.length).toBe(expectedSubmissionCount);
  };

  await page.goto("/translate");
  await submit(termMap.id);
  await expect(page.getByText("Translation queued")).toBeVisible();
  await page.getByRole("button", { name: "Translate another" }).click();
  await expect(page.locator("#term-map-select")).toHaveValue("__directory_default__");
  await submit("");

  expect(submissions).toHaveLength(2);
  expect(submissions[0]).toMatchObject({
    term_map_mode: "selected",
    term_map_id: termMap.id,
  });
  expect(submissions[1]).toMatchObject({ term_map_mode: "none", term_map_id: null });
});

test("Translate clears a one-off Term map choice when changing directories", async ({
  page,
}) => {
  await stubProductStatus(page);
  const termMap = {
    id: "map-directory-default",
    name: "Directory terms",
    entry_count: 1,
    updated_at: "2026-08-13T12:00:00Z",
  };
  await page.route("**/api/media/browse", async (route) => {
    const path = (JSON.parse(route.request().postData() ?? "{}").path ?? "") as string;
    const entries =
      path === ""
        ? [
            { kind: "media", name: "Example.mkv", path: "Example.mkv" },
            { kind: "directory", name: "Other", path: "Other" },
          ]
        : [{ kind: "media", name: "Other.mkv", path: "Other/Other.mkv" }];
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ path, entries }),
    });
  });
  await page.route("**/api/media/discover", async (route) => {
    const path = (JSON.parse(route.request().postData() ?? "{}").path ?? "") as string;
    const subtitlePath = path === "Example.mkv" ? "Example.en.srt" : "Other.en.srt";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        path,
        candidates: [
          {
            kind: "external",
            path: subtitlePath,
            format: "srt",
            tags: { language: "en", title: "" },
          },
        ],
        unsupported_candidates: [],
      }),
    });
  });
  await page.route("/api/term-maps", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ term_maps: [termMap] }),
    }),
  );
  await page.route("**/api/term-maps/directory**", async (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        directory: path,
        local: null,
        effective: path === "" ? termMap : null,
        source_directory: path === "" ? "" : null,
      }),
    });
  });
  const submissions = await stubJobCreation(page);

  await page.goto("/translate");
  await page.getByRole("button", { name: "Select Example.mkv" }).click();
  await page
    .getByRole("button", { name: "Select external subtitle en (Example.en.srt)" })
    .click();
  await fillCustomTargetLanguage(page, "zh-Hans");
  await page.locator("#term-map-select").selectOption(termMap.id);
  await expect(page.locator("#term-map-select")).toHaveValue(termMap.id);

  await page.getByRole("button", { name: "Media", exact: true }).click();
  await page.getByRole("button", { name: "Open Other" }).click();
  await expect(page.locator("#term-map-select")).toHaveValue("__directory_default__");

  await page.getByRole("button", { name: "Select Other.mkv" }).click();
  await page
    .getByRole("button", { name: "Select external subtitle en (Other.en.srt)" })
    .click();
  await fillCustomTargetLanguage(page, "zh-Hans");
  await page.getByRole("button", { name: "Start translation" }).click();
  await expect.poll(() => submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({
    media_path: "Other/Other.mkv",
    term_map_mode: "follow",
    term_map_id: null,
  });
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
        page.getByRole("button", { name: "Clear completed history (1)" }),
      ).toBeEnabled();
      await expect(page.getByText("2 matching")).toBeVisible();
      await page.getByRole("button", { name: "Clear completed history (1)" }).click();
      await expect(page.getByText("1 matching")).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Clear completed history (0)" }),
      ).toBeDisabled();
      await expect(page.getByRole("button", { name: /Example\.mkv/ })).toBeVisible();

      await page.getByRole("button", { name: /Example\.mkv/ }).click();
      await page.getByRole("button", { name: "Delete Job" }).click();
      await expect(page.getByRole("heading", { name: "No Jobs yet" })).toBeVisible();
      await expect(page).toHaveURL(/\/jobs$/);
      await expect(page.getByRole("heading", { name: "Job history" })).toBeFocused();
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
    page.getByRole("status").filter({ hasText: "Set PROVIDER" }),
  ).toContainText("Set PROVIDER and the matching provider environment variables");
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
  let releaseDetails: (() => void) | undefined;
  await page.route("/api/term-maps/map-1", async (route) => {
    await new Promise<void>((resolve) => {
      releaseDetails = resolve;
    });
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: "map-1",
        name: "Characters",
        entry_count: 2,
        updated_at: "2026-08-13T12:00:00Z",
        content: { Captain: "队长", Ship: "舰船" },
      }),
    });
  });

  for (const viewport of [
    { width: 390, height: 844 },
    { width: 1280, height: 800 },
  ]) {
    releaseDetails = undefined;
    await page.setViewportSize(viewport);
    await page.goto("/term-maps");
    await page.getByRole("button", { name: /Characters/ }).press("Enter");
    const loadingHeading = page.getByRole("heading", { name: "Term map details" });
    await expect(loadingHeading).toBeVisible();
    await expect(loadingHeading).not.toBeFocused();
    await expect.poll(() => releaseDetails).toBeDefined();
    const releaseCurrentDetails = releaseDetails;
    releaseDetails = undefined;
    releaseCurrentDetails?.();
    const detailHeading = page.getByRole("heading", { name: "Characters" });
    await expect(detailHeading).toBeVisible();
    await expect(detailHeading).toBeFocused();
    await expect
      .poll(async () => {
        const box = await detailHeading.boundingBox();
        return box !== null && box.y >= 0 && box.y + box.height <= viewport.height;
      })
      .toBe(true);
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
  const updated = page.locator(
    '.term-map-detail time[datetime="2026-08-13T12:00:00Z"]',
  );
  await expect(updated).toBeVisible();
  await expect(updated).toContainText("2026");
  await expect(updated).not.toHaveText("2026-08-13T12:00:00Z");
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
    request: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      term_map_mode: "follow",
      term_map_id: null,
    },
  },
  {
    label: "Embedded subtitle",
    subtitleName: /Select embedded subtitle stream 3 zhs \/ Chinese/,
    candidate: {
      kind: "embedded",
      stream_index: 3,
      format: "srt",
      tags: { language: "zhs", title: "Chinese" },
    },
    request: {
      media_path: "Example.mkv",
      stream_index: 3,
      source_format: "srt",
      term_map_mode: "follow",
      term_map_id: null,
    },
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
        await page.route("**/api/jobs**", async (route) => {
          if (!isJobsCollectionRequest(route.request())) {
            await route.continue();
            return;
          }
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
          await route.fulfill({
            contentType: "application/json",
            body: JSON.stringify({
              active_jobs: [],
              history_jobs: [],
              next_cursor: null,
            }),
          });
        });

        await page.goto("/translate");
        await page.getByRole("button", { name: "Select Example.mkv" }).click();
        await page.getByRole("button", { name: source.subtitleName }).click();
        await expect(page.locator("#common-target-language option")).toHaveCount(31);
        await fillCustomTargetLanguage(page, "zh-Hans");
        await expect(page.getByLabel("Subtitle suffix")).toHaveValue("zh-Hans");
        await expect(page.locator("#output-suffix-help")).toHaveText(
          "Output filename: Example.zh-Hans.srt",
        );
        await expect(page.getByLabel(/Skip existing output/u)).toBeChecked();
        await expect(page.getByLabel("Overwrite existing output")).not.toBeChecked();
        if (viewport.name === "mobile") {
          const suffixBox = await page.getByLabel("Subtitle suffix").boundingBox();
          expect(suffixBox?.height).toBeGreaterThanOrEqual(44);
          for (const label of ["Skip existing output", "Overwrite existing output"]) {
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
          output_conflict_policy: "skip",
          term_map_mode: "follow",
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
        output_conflict_policy: "skip",
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
    await fillCustomTargetLanguage(page, "x-failed");
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
          term_map_mode: "follow",
          term_map_id: null,
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

  const snapshotBlocker = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "e2e-snapshot-blocker",
      term_map_mode: "follow",
      term_map_id: null,
    },
  });
  expect(snapshotBlocker.ok()).toBeTruthy();
  await waitForJob(page, "e2e-snapshot-blocker", "Translating");

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
      term_map_mode: "selected",
      term_map_id: termMap.id,
    },
  });
  expect(external.ok()).toBeTruthy();
  const updatedTermMap = await page.request.put(`/api/term-maps/${termMap.id}`, {
    data: { content: { Captain: "舰长", Ship: "舰船" } },
  });
  expect(updatedTermMap.ok()).toBeTruthy();
  const updatedTermMapDetail = await page.request.get(`/api/term-maps/${termMap.id}`);
  expect((await updatedTermMapDetail.json()).content).toEqual({
    Captain: "舰长",
    Ship: "舰船",
  });
  const queuedExternal = await waitForJob(page, "e2e-term-map", "Queued");
  expect(queuedExternal.request.term_map).toEqual({
    id: termMap.id,
    name: "Release matrix terms",
  });
  expect(queuedExternal.request.term_map).not.toHaveProperty("content");
  const completedBlocker = await waitForJob(page, "e2e-snapshot-blocker", "Completed");
  expect(completedBlocker.status).toBe("Completed");
  const completedExternal = await waitForJob(page, "e2e-term-map", "Completed");
  const completedExternalDetail = await readJobDetail(page, completedExternal.id);
  expect(completedExternalDetail.request.term_map).toEqual({
    id: termMap.id,
    name: "Release matrix terms",
  });
  expect(completedExternalDetail.request.term_map).not.toHaveProperty("content");

  const embedded = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      stream_index: 1,
      source_format: "srt",
      target_language_code: "e2e-embedded",
      term_map_mode: "follow",
      term_map_id: null,
    },
  });
  expect(embedded.ok()).toBeTruthy();
  const completedEmbedded = await waitForJob(page, "e2e-embedded", "Completed");
  const completedEmbeddedDetail = await readJobDetail(page, completedEmbedded.id);
  expect(completedEmbedded.request.stream_index).toBe(1);
  expect(completedEmbedded.request.source_format).toBe("srt");
  expect(completedEmbeddedDetail.extraction).toMatchObject({
    status: "Completed",
    path: "source.srt",
    format: "srt",
  });
  expect(completedEmbeddedDetail.extraction?.content_digest).toMatch(/^[0-9a-f]{64}$/);

  for (const targetLanguage of ["e2e-retry-external", "e2e-retry-embedded"]) {
    const request =
      targetLanguage === "e2e-retry-external"
        ? {
            media_path: "Example.mkv",
            subtitle_path: "Example.en.srt",
            target_language_code: targetLanguage,
            term_map_mode: "follow",
            term_map_id: null,
          }
        : {
            media_path: "Example.mkv",
            stream_index: 1,
            source_format: "srt",
            target_language_code: targetLanguage,
            term_map_mode: "follow",
            term_map_id: null,
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
    output_conflict_policy: "append-number",
    term_map_mode: "follow",
    term_map_id: null,
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
    term_map_mode: "follow",
    term_map_id: null,
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
      term_map_mode: "follow",
      term_map_id: null,
    },
  });
  expect(permanentFailure.ok()).toBeTruthy();
  const failedJob = await waitForJob(page, "e2e-fail-permanent", "Failed");
  const deleted = await page.request.delete(`/api/jobs/${failedJob.id}`);
  expect(deleted.ok()).toBeTruthy();
  expect((await readJobs(page)).some((job) => job.id === failedJob.id)).toBe(false);

  const restartJob = await page.request.post("/api/jobs", {
    data: {
      media_path: "Example.mkv",
      subtitle_path: "Example.en.srt",
      target_language_code: "e2e-interrupted-retry",
      term_map_mode: "follow",
      term_map_id: null,
    },
  });
  expect(restartJob.ok()).toBeTruthy();
  await waitForJob(page, "e2e-interrupted-retry", "Translating");
});

test("production restart recovers and retries an Interrupted Job", async ({ page }) => {
  test.skip(process.env.CUEWEAVER_E2E_PHASE !== "restart");

  const interrupted = await waitForJob(page, "e2e-interrupted-retry", "Interrupted");
  expect(interrupted.error?.code).toBe("job_interrupted");
  const retry = await page.request.post(`/api/jobs/${interrupted.id}/retry`);
  expect(retry.ok()).toBeTruthy();
  const completed = await waitForJob(page, "e2e-interrupted-retry", "Completed");
  expect(completed.id).toBe(interrupted.id);
  expect(completed.attempt).toBe(2);

  const historical = (await readJobs(page)).find(
    (job) => job.request.target_language_code === "e2e-term-map",
  );
  expect(historical?.status).toBe("Completed");
  expect(historical?.request.term_map).toMatchObject({
    name: "Release matrix terms",
  });
  expect(historical?.request.term_map?.id).toEqual(expect.any(String));
  expect(historical?.request.term_map).not.toHaveProperty("content");
});
