import { expect, test, type Page } from "@playwright/test";

import { jobRecord } from "./fixtures";

const viewports = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
] as const;

async function stubStatus(page: Page) {
  await page.route("**/api/status", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        api: { ready: true },
        roots: { ready: true },
        translation_provider: { ready: true },
        worker: { ready: true, mode: "single" },
      }),
    }),
  );
}

async function stubMedia(page: Page) {
  await page.route("**/api/media/browse", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ path: "", entries: [] }),
    }),
  );
}

async function stubJobs(page: Page, historyJobs = [jobRecord("density-job")]) {
  const job = jobRecord("density-job");
  await page.route("**/api/jobs**", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        active_jobs: [],
        history_jobs: historyJobs,
        next_cursor: null,
        completed_count: historyJobs.filter((job) => job.status === "Completed").length,
      }),
    }),
  );
  await page.route("**/api/jobs/density-job*", (route) =>
    route.fulfill({ contentType: "application/json", body: JSON.stringify(job) }),
  );
}

async function stubTermMaps(
  page: Page,
  termMaps = [
    {
      id: "density-map",
      name: "Characters",
      entry_count: 2,
      updated_at: "2026-08-13T12:00:00Z",
    },
  ],
) {
  await page.route("**/api/term-maps**", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ term_maps: termMaps }),
    }),
  );
}

for (const viewport of viewports) {
  test(`${viewport.name} Translate controls use the density contract`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await stubStatus(page);
    await stubMedia(page);
    await page.goto("/translate");
    await expect(
      page.getByRole("heading", { name: "Translate", exact: true }),
    ).toBeVisible();

    const buttonSizes = await page
      .locator("main button:visible")
      .evaluateAll((buttons) =>
        buttons.map((button) => getComputedStyle(button).fontSize),
      );
    expect(buttonSizes).toEqual(buttonSizes.map(() => "14px"));

    const controlSizes = await page
      .locator("main .form-control:visible, main .select-control:visible")
      .evaluateAll((controls) =>
        controls.map((control) => ({
          fontSize: getComputedStyle(control).fontSize,
          minHeight: getComputedStyle(control).minHeight,
        })),
      );
    expect(controlSizes.length).toBeGreaterThan(0);
    expect(controlSizes).toEqual(
      controlSizes.map(() => ({
        fontSize: viewport.name === "mobile" ? "16px" : "14px",
        minHeight: viewport.name === "mobile" ? "44px" : "36px",
      })),
    );

    const filterStyles = await page
      .locator(".media-filter .form-control")
      .evaluate((input) => {
        const styles = getComputedStyle(input);
        return { fontSize: styles.fontSize, minHeight: styles.minHeight };
      });
    expect(filterStyles).toEqual({
      fontSize: viewport.name === "mobile" ? "16px" : "14px",
      minHeight: viewport.name === "mobile" ? "44px" : "36px",
    });

    const helpStyles = await page
      .locator(".field-help:visible")
      .evaluateAll((elements) =>
        elements.map((element) => {
          const styles = getComputedStyle(element);
          return { fontSize: styles.fontSize, marginTop: styles.marginTop };
        }),
      );
    expect(helpStyles.length).toBeGreaterThan(0);
    expect(
      helpStyles.every(
        ({ fontSize, marginTop }) => fontSize === "12px" && marginTop === "6px",
      ),
    ).toBe(true);

    const visibleTextSizes = await page
      .locator("body *:visible")
      .evaluateAll((elements) =>
        elements
          .filter(
            (element) =>
              element.children.length === 0 &&
              element.textContent?.trim() !== "" &&
              !element.closest(".brand-mark"),
          )
          .map((element) => getComputedStyle(element).fontSize),
      );
    expect(
      visibleTextSizes.every((fontSize) => Number.parseFloat(fontSize) >= 11),
    ).toBe(true);

    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  });

  test(`${viewport.name} Jobs and Term maps avoid hidden-state gaps`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await stubStatus(page);
    await stubJobs(page);
    await page.goto("/jobs");
    await expect(page.locator("#history-jobs-title")).toBeVisible();

    const jobsGap = await page.locator("#history-jobs-title").evaluate((heading) => {
      const headingBox = heading.getBoundingClientRect();
      const controls = document
        .querySelector(".job-list-heading")!
        .getBoundingClientRect();
      return headingBox.top - controls.bottom;
    });
    expect(jobsGap).toBeLessThanOrEqual(64);
    expect(
      await page
        .locator(".job-list-state")
        .evaluate((state) => getComputedStyle(state).minHeight),
    ).toBe("0px");

    await stubTermMaps(page);
    await page.goto("/term-maps");
    await expect(page.locator(".term-map-item")).toBeVisible();

    const termMapGap = await page.locator(".term-map-item").evaluate((item) => {
      const itemBox = item.getBoundingClientRect();
      const headingBox = document.querySelector("#maps-title")!.getBoundingClientRect();
      return itemBox.top - headingBox.bottom;
    });
    expect(termMapGap).toBeLessThanOrEqual(64);
    expect(
      await page
        .locator(".term-map-list-state")
        .evaluate((state) => getComputedStyle(state).minHeight),
    ).toBe("0px");
  });

  test(`${viewport.name} Job details keep identity controls readable and keyboard reachable`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await stubStatus(page);
    await stubJobs(page);
    await page.goto("/jobs");
    const clearHistory = page.getByRole("button", { name: /Clear completed history/ });
    await clearHistory.focus();
    await expect(clearHistory).toBeFocused();
    await page.getByRole("button", { name: "Example.mkv" }).click();
    await expect(page.getByRole("heading", { name: "Example.mkv" })).toBeVisible();

    const identityBoxes = await page
      .locator(".job-detail-context > *")
      .evaluateAll((elements) =>
        elements.map((element) => {
          const box = element.getBoundingClientRect();
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
        }),
      );
    expect(
      identityBoxes.every(({ left, right }) => left >= 0 && right <= viewport.width),
    ).toBe(true);
    const jobId = page.locator(".job-id-control code");
    expect(
      await jobId.evaluate((element) => getComputedStyle(element).overflowWrap),
    ).toBe("anywhere");
    await page.getByRole("button", { name: "Copy Job ID" }).focus();
    expect(await page.evaluate(() => document.activeElement?.textContent)).toContain(
      "Copy Job ID",
    );
    for (const name of ["Back to Jobs", "Copy Job ID", "Delete Job"]) {
      const control = page.getByRole("button", { name });
      await control.focus();
      await expect(control).toBeFocused();
    }
  });

  test(`${viewport.name} Jobs and Term maps retain visible empty-state footprints`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    await stubStatus(page);
    await stubJobs(page, []);
    await page.goto("/jobs");
    await expect(
      page.getByRole("heading", { name: "No Jobs yet", exact: true }),
    ).toBeVisible();
    expect(
      await page
        .locator(".job-list-state")
        .evaluate((state) => getComputedStyle(state).minHeight),
    ).toBe("88px");

    await stubTermMaps(page, []);
    await page.goto("/term-maps");
    await expect(
      page.getByRole("heading", { name: "No Term maps yet", exact: true }),
    ).toBeVisible();
    expect(
      await page
        .locator(".term-map-list-state")
        .evaluate((state) => getComputedStyle(state).minHeight),
    ).toBe("190px");
  });
}
